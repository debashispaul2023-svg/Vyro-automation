"""
daily_runner.py

Fully-automated entrypoint, meant to run on a daily schedule (GitHub Actions
cron — see .github/workflows/vyro_daily.yml):

  1. Check Vyro for an active campaign. If none (or it fails the AI quality
     screen), check every Whop campaign listed in whop_campaigns.json.
  2. Ask ai_brain to sanity-check the campaign's requirements — skip and try
     the other platform if it looks like a scam/low-effort listing.
  3. Skip cleanly if nothing usable found anywhere, or if the found
     campaign_id was already processed before (processed_campaigns.json).
  4. Resolve the source clip: some campaigns give a direct clip link
     (Vyro); some (many Whop ones) point to an external Google Doc that
     contains the real footage links — resolve that first if needed.
  5. Parse requirements with AI (falls back to regex parsing on failure) ->
     render vertical short -> generate AI-written metadata (falls back to
     the template on failure) -> validate -> upload to YouTube -> upload to
     Instagram Reels.
  6. Submit the resulting link back to whichever platform the campaign
     came from.
  7. Record the campaign_id as processed, so it's never submitted twice.

Usage (locally or in CI):
    python daily_runner.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Union

import requests

from ai_brain import AIBrainError, ai_generate_metadata, ai_parse_requirements, ai_score_campaign
from checker import ValidationError, validate_or_raise
from google_doc_reader import GoogleDocReadError, fetch_google_doc_text, find_candidate_source_clips
from instagram_uploader import InstagramUploadError, upload_reel
from metadata import MetadataError, VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements, RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError
from vyro_client import run_check as vyro_run_check
from vyro_client import run_submit as vyro_run_submit
from whop_client import WhopCampaign, WhopClientError
from whop_client import check_configured_campaigns as whop_check_configured_campaigns
from whop_client import submit_video_link as whop_submit_video_link
from youtube_uploader import UploadError, upload_video

PROCESSED_LOG_PATH = "processed_campaigns.json"
SOURCE_CLIP_PATH = "input_16x9.mp4"
OUTPUT_PATH = "output/short.mp4"

Campaign = Union[VyroCampaign, WhopCampaign]


def _load_processed() -> set[str]:
    if not os.path.isfile(PROCESSED_LOG_PATH):
        return set()
    with open(PROCESSED_LOG_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def _save_processed(processed: set[str]) -> None:
    with open(PROCESSED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)


def _download_source_clip(url: str, dest_path: str) -> None:
    """
    Downloads the campaign's source clip. Tries yt-dlp first (handles
    YouTube, Google Drive, and most video hosts); falls back to a plain
    HTTP GET for direct file links.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    ytdlp_result = subprocess.run(
        ["yt-dlp", "-f", "mp4/best", "-o", dest_path, url],
        capture_output=True,
        text=True,
    )
    if ytdlp_result.returncode == 0 and os.path.isfile(dest_path):
        return

    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)


def _resolve_source_clip(campaign: Campaign) -> str:
    """Returns a downloadable URL for the campaign's source footage. If the
    campaign already has one (Vyro's usual case), use it directly. If it's
    empty but the campaign links an external Google Doc (common on Whop),
    fetch that doc and pull the first candidate video link out of it."""
    if campaign.source_clip_url:
        return campaign.source_clip_url

    reference_doc_url = getattr(campaign, "reference_doc_url", None)
    if not reference_doc_url:
        raise RuntimeError(
            "No source clip URL and no reference document to resolve one "
            "from. This campaign may require footage you have to record/"
            "provide yourself, which this pipeline doesn't handle yet."
        )

    try:
        doc_text = fetch_google_doc_text(reference_doc_url)
    except GoogleDocReadError as exc:
        raise RuntimeError(f"Could not read the campaign's reference document: {exc}") from exc

    candidates = find_candidate_source_clips(doc_text)
    if not candidates:
        raise RuntimeError(
            "The campaign's reference document didn't contain any "
            "recognizable footage link (Drive/YouTube/Dropbox/etc)."
        )

    # Try each candidate until one actually downloads successfully.
    for candidate_url in candidates:
        try:
            _download_source_clip(candidate_url, SOURCE_CLIP_PATH)
            return candidate_url
        except Exception:  # noqa: BLE001
            continue

    raise RuntimeError("None of the candidate footage links in the reference document could be downloaded.")


def _screen_campaign(platform: str, campaign: Campaign) -> bool:
    """Returns True if the campaign passes the AI quality screen (or the
    screen itself is unavailable, in which case we don't block on it)."""
    try:
        score = ai_score_campaign(campaign.requirements_text or campaign.name)
    except AIBrainError as exc:
        print(f"AI campaign screening unavailable ({exc}); proceeding anyway.", file=sys.stderr)
        return True

    if not score.is_good:
        print(f"Skipping {platform} campaign '{campaign.campaign_id}': {score.reason}")
        return False
    return True


def _find_campaign() -> tuple[str, Campaign] | tuple[None, None]:
    """Checks Vyro first, then every configured Whop campaign. Skips any
    campaign that fails the AI quality screen and tries the next source."""
    try:
        vyro_campaign = vyro_run_check()
    except VyroClientError as exc:
        print(f"Vyro check failed: {exc}", file=sys.stderr)
        vyro_campaign = None

    if vyro_campaign is not None and _screen_campaign("vyro", vyro_campaign):
        return "vyro", vyro_campaign

    try:
        whop_campaign = whop_check_configured_campaigns()
    except WhopClientError as exc:
        print(f"Whop check failed: {exc}", file=sys.stderr)
        whop_campaign = None

    if whop_campaign is not None and _screen_campaign("whop", whop_campaign):
        return "whop", whop_campaign

    return None, None


def _submit_back(platform: str, campaign: Campaign, video_url: str) -> None:
    if platform == "vyro":
        vyro_run_submit(campaign, video_url)
    elif platform == "whop":
        whop_submit_video_link(campaign, video_url)
    else:
        raise ValueError(f"Unknown platform: {platform}")


def _parse_requirements(campaign: Campaign) -> CampaignRequirements:
    raw_text = campaign.requirements_text or campaign.name
    try:
        parsed = ai_parse_requirements(raw_text)
        req = CampaignRequirements(
            campaign_id=campaign.campaign_id,
            mandatory_hashtags=parsed.mandatory_hashtags,
            required_links=parsed.required_links,
            min_seconds=parsed.min_seconds,
            max_seconds=parsed.max_seconds,
            referral_code=parsed.referral_code,
            watermark_text=parsed.watermark_text,
            raw_source=raw_text,
        )
        req.validate()
        if parsed.notes:
            print(f"AI parser note: {parsed.notes}")
        return req
    except (AIBrainError, RequirementsParseError) as exc:
        print(f"AI requirements parsing failed ({exc}); falling back to regex parser.", file=sys.stderr)
        req = parse_campaign(raw_text)
        req.campaign_id = campaign.campaign_id
        return req


def _generate_metadata(hook: str, summary: str, req: CampaignRequirements) -> VideoMetadata:
    try:
        ai_meta = ai_generate_metadata(
            hook=hook,
            summary=summary,
            mandatory_hashtags=req.mandatory_hashtags,
            required_links=req.required_links,
            referral_code=req.referral_code,
        )
        return VideoMetadata(title=ai_meta.title, description=ai_meta.description, tags=ai_meta.hashtags)
    except (AIBrainError, MetadataError) as exc:
        print(f"AI metadata generation failed ({exc}); falling back to template.", file=sys.stderr)
        return generate_metadata(hook=hook, summary=summary, req=req)


def process_campaign(platform: str, campaign: Campaign) -> int:
    req = _parse_requirements(campaign)

    try:
        resolved_source = _resolve_source_clip(campaign)
        print(f"[1/5] Resolved + downloaded source clip from {resolved_source} -> {SOURCE_CLIP_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to resolve/download source clip: {exc}", file=sys.stderr)
        return 1

    hook = campaign.name or "New campaign clip"

    try:
        render_short(
            source_path=SOURCE_CLIP_PATH,
            output_path=OUTPUT_PATH,
            req=req,
            fallback_caption_text=hook,
        )
        print(f"[2/5] Rendered vertical short -> {OUTPUT_PATH}")

        meta = _generate_metadata(hook=hook, summary=(campaign.requirements_text or "")[:200], req=req)
        print(f"[3/5] Generated metadata. Title: {meta.title}")

        validate_or_raise(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            req=req,
        )

        result = upload_video(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            privacy_status="public",
            token_path="token.json",
        )
        print(f"[4/5] Uploaded to YouTube: {result.video_url}")
    except RenderError as exc:
        print(f"Rendering failed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except UploadError as exc:
        print(f"YouTube upload failed: {exc}", file=sys.stderr)
        return 1

    try:
        instagram_media_id = upload_reel(
            video_path=OUTPUT_PATH,
            caption=f"{meta.title}\n\n{meta.description}",
            release_tag=f"clip-{platform}-{campaign.campaign_id}",
        )
        print(f"[5/5] Uploaded to Instagram Reels: {instagram_media_id}")
    except InstagramUploadError as exc:
        print(f"Instagram upload failed (continuing anyway): {exc}", file=sys.stderr)

    try:
        _submit_back(platform, campaign, result.video_url)
        print(f"Submitted {result.video_url} to {platform} campaign '{campaign.campaign_id}'.")
    except (VyroClientError, WhopClientError) as exc:
        print(
            f"Upload succeeded but {platform} submission failed: {exc}\n"
            f"SUBMIT THIS URL MANUALLY: {result.video_url}",
            file=sys.stderr,
        )
        return 1

    return 0


def main() -> int:
    processed = _load_processed()

    platform, campaign = _find_campaign()

    if campaign is None:
        print("No usable active campaign today on Vyro or Whop. Exiting.")
        return 0

    if campaign.campaign_id in processed:
        print(f"Campaign '{campaign.campaign_id}' ({platform}) already processed. Skipping.")
        return 0

    print(f"Found new campaign on {platform}: {campaign.campaign_id} — {campaign.name}")

    status = process_campaign(platform, campaign)
    if status == 0:
        processed.add(campaign.campaign_id)
        _save_processed(processed)
    return status


if __name__ == "__main__":
    sys.exit(main())
