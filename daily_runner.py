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
import shutil
import subprocess
import sys
from typing import Union

import requests

from ai_brain import AIBrainError, ai_generate_metadata, ai_parse_requirements, ai_score_campaign
from checker import ValidationError, validate_or_raise
from clip_picker import ClipPickError, collect_clips_from_doc_text, download_drive_file, pick_best_clip
from google_doc_reader import (
    GoogleDocReadError,
    extract_drive_file_id,
    fetch_google_doc_text,
    find_candidate_source_clips,
    is_drive_folder_url,
)
from instagram_uploader import InstagramUploadError, upload_reel
from metadata import MetadataError, VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements, RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError
from vyro_client import run_check as vyro_run_check
from vyro_client import run_submit as vyro_run_submit
from whop_client import WhopCampaign, WhopClientError
from whop_client import check_configured_campaigns as whop_check_configured_campaigns
from whop_client import discover_and_join_new_campaigns as whop_discover_and_join_new_campaigns
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


def _youtube_token_ready(token_path: str = "token.json") -> bool:
    """True only if token.json is a real user token with refresh_token."""
    if not os.path.isfile(token_path):
        return False
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("installed") or data.get("web"):
        return False
    return bool(data.get("refresh_token") and (data.get("token") or data.get("access_token")))


def _resolve_source_clip(campaign: Campaign) -> str:
    """Download the best source clip (Parts 3+4) into SOURCE_CLIP_PATH.

    Vyro usually has campaign.source_clip_url. Whop usually has a Google Doc
    that points at a Drive folder — list videos, score them, download winner.
    """
    os.makedirs(os.path.dirname(SOURCE_CLIP_PATH) or ".", exist_ok=True)
    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()

    if campaign.source_clip_url and not is_drive_folder_url(campaign.source_clip_url):
        fid = extract_drive_file_id(campaign.source_clip_url)
        if fid and drive_key:
            download_drive_file(fid, SOURCE_CLIP_PATH, drive_key)
            return campaign.source_clip_url
        _download_source_clip(campaign.source_clip_url, SOURCE_CLIP_PATH)
        return campaign.source_clip_url

    reference_doc_url = getattr(campaign, "reference_doc_url", None)
    folder_or_doc = reference_doc_url or campaign.source_clip_url
    if not folder_or_doc:
        raise RuntimeError(
            "No source clip URL and no reference document to resolve one "
            "from. This campaign may require footage you have to record/"
            "provide yourself, which this pipeline doesn't handle yet."
        )

    doc_text = ""
    if "docs.google.com/document" in folder_or_doc:
        try:
            doc_text = fetch_google_doc_text(folder_or_doc)
        except GoogleDocReadError as exc:
            raise RuntimeError(f"Could not read the campaign's reference document: {exc}") from exc
    else:
        doc_text = folder_or_doc

    clips = collect_clips_from_doc_text(doc_text)
    if clips and drive_key:
        try:
            winner = pick_best_clip(
                clips,
                work_dir="work/daily",
                api_key=drive_key,
                campaign_notes=(campaign.requirements_text or campaign.name or "")[:2000],
            )
            shutil.copyfile(winner.local_path, SOURCE_CLIP_PATH)
            return winner.clip.url
        except ClipPickError as exc:
            print(f"Best-clip picker failed ({exc}); falling back to first downloadable URL.", file=sys.stderr)

    candidates = find_candidate_source_clips(doc_text) if doc_text else [folder_or_doc]
    if not candidates:
        raise RuntimeError(
            "The campaign's reference document didn't contain any "
            "recognizable footage link (Drive/YouTube/Dropbox/etc)."
        )
    for candidate_url in candidates:
        if is_drive_folder_url(candidate_url):
            continue
        try:
            fid = extract_drive_file_id(candidate_url)
            if fid and drive_key:
                download_drive_file(fid, SOURCE_CLIP_PATH, drive_key)
                return candidate_url
            _download_source_clip(candidate_url, SOURCE_CLIP_PATH)
            return candidate_url
        except Exception as exc:  # noqa: BLE001
            print(f"Download failed for {candidate_url}: {exc}", file=sys.stderr)
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
        newly_joined = whop_discover_and_join_new_campaigns(score_fn=ai_score_campaign, max_new=2)
        if newly_joined:
            print(f"Auto-joined {len(newly_joined)} new Whop campaign(s): {newly_joined}")
    except WhopClientError as exc:
        print(f"Whop auto-discovery/join failed: {exc}", file=sys.stderr)

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

        if not _youtube_token_ready():
            print(
                "[4/5] YouTube token.json is missing or is still client_secrets. "
                "Stopping after render (Parts 1-4 done). "
                "Finish YouTube Phone Login, then re-run."
            )
            return 2
        result = upload_video(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            tags=meta.tags,
            privacy_status="unlisted",
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
        return 0
    if status == 2:
        print("Parts 1-4 done. Campaign NOT marked processed until YouTube token works.")
        return 0
    return status


if __name__ == "__main__":
    sys.exit(main())
