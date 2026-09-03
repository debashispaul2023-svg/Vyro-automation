"""
daily_runner.py

Fully-automated entrypoint, meant to run on a daily schedule (GitHub Actions
cron — see .github/workflows/vyro_daily.yml):

  1. Check Vyro for an active campaign. If none (or it's low-quality per the
     AI screen), check Whop too.
  2. Ask ai_brain to sanity-check the campaign's requirements — skip and try
     the other platform if it looks like a scam/low-effort listing.
  3. Skip cleanly if nothing found anywhere, or if the found campaign_id was
     already processed before (tracked in processed_campaigns.json)
  4. Download the campaign's source clip
  5. Parse requirements with AI (falls back to regex parsing if the API
     call fails) -> render vertical short -> generate AI-written metadata
     (falls back to the template version on API failure) -> validate ->
     upload to YouTube -> upload to Instagram Reels
  6. Submit the resulting YouTube link back to whichever platform (Vyro or
     Whop) the campaign came from
  7. Record the campaign_id as processed, so it's never submitted twice

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
from instagram_uploader import InstagramUploadError, upload_reel
from metadata import MetadataError, VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements, RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError
from vyro_client import run_check as vyro_run_check
from vyro_client import run_submit as vyro_run_submit
from whop_client import WhopCampaign, WhopClientError
from whop_client import run_check as whop_run_check
from whop_client import run_submit as whop_run_submit
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
    YouTube and most video hosts); falls back to a plain HTTP GET for
    direct file links (Google Drive direct-download, S3, CDN links, etc).
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
    """Checks Vyro first, then Whop. Skips any campaign that fails the AI
    quality screen and tries the other platform instead. Returns
    (platform_name, campaign) or (None, None) if nothing usable exists
    anywhere right now."""
    try:
        vyro_campaign = vyro_run_check()
    except VyroClientError as exc:
        print(f"Vyro check failed: {exc}", file=sys.stderr)
        vyro_campaign = None

    if vyro_campaign is not None and _screen_campaign("vyro", vyro_campaign):
        return "vyro", vyro_campaign

    try:
        whop_campaign = whop_run_check()
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
        whop_run_submit(campaign, video_url)
    else:
        raise ValueError(f"Unknown platform: {platform}")


def _parse_requirements(campaign: Campaign) -> CampaignRequirements:
    """Tries the AI parser first (handles any language/format); falls back
    to the regex-based parser in requirements_parser.py if the API call
    fails, so a bad API day never blocks the pipeline."""
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
    """Tries the AI metadata writer first; falls back to the template
    version in metadata.py if the API call fails."""
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
        _download_source_clip(campaign.source_clip_url, SOURCE_CLIP_PATH)
        print(f"[1/5] Downloaded source clip -> {SOURCE_CLIP_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to download source clip: {exc}", file=sys.stderr)
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
        # Instagram is a bonus channel — don't fail the whole run if only
        # this step breaks, since YouTube already succeeded.
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
