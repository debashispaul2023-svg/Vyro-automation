"""
daily_runner.py

Fully-automated entrypoint, meant to run on a daily schedule (GitHub Actions
cron — see .github/workflows/vyro_daily.yml):

  1. Log in to Vyro and check for an active campaign (vyro_client.run_check)
  2. Skip cleanly if there's no campaign today, or if this campaign_id was
     already processed before (tracked in processed_campaigns.json)
  3. Download the campaign's ~1 minute source clip
  4. Parse requirements -> render vertical short -> generate metadata ->
     validate -> upload to YouTube
  5. Log back into Vyro and submit the resulting YouTube link
  6. Record the campaign_id as processed, so it's never submitted twice

Usage (locally or in CI):
    python daily_runner.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import requests

from checker import ValidationError, validate_or_raise
from metadata import MetadataError, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError, run_check, run_submit
from youtube_uploader import UploadError, upload_video

PROCESSED_LOG_PATH = "processed_campaigns.json"
SOURCE_CLIP_PATH = "input_16x9.mp4"
OUTPUT_PATH = "output/short.mp4"


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


def process_campaign(campaign: VyroCampaign) -> int:
    try:
        req = parse_campaign(campaign.requirements_text or campaign.name)
        req.campaign_id = campaign.campaign_id
    except RequirementsParseError as exc:
        print(f"Requirements parsing failed: {exc}", file=sys.stderr)
        return 1

    try:
        _download_source_clip(campaign.source_clip_url, SOURCE_CLIP_PATH)
        print(f"[1/4] Downloaded source clip -> {SOURCE_CLIP_PATH}")
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
        print(f"[2/4] Rendered vertical short -> {OUTPUT_PATH}")

        meta = generate_metadata(
            hook=hook,
            summary=(campaign.requirements_text or "")[:200],
            req=req,
        )
        print(f"[3/4] Generated metadata. Title: {meta.title}")

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
        print(f"[4/4] Uploaded to YouTube: {result.video_url}")
    except RenderError as exc:
        print(f"Rendering failed: {exc}", file=sys.stderr)
        return 1
    except MetadataError as exc:
        print(f"Metadata generation failed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except UploadError as exc:
        print(f"YouTube upload failed: {exc}", file=sys.stderr)
        return 1

    try:
        run_submit(campaign, result.video_url)
        print(f"Submitted {result.video_url} to Vyro campaign '{campaign.campaign_id}'.")
    except VyroClientError as exc:
        print(
            f"Upload succeeded but Vyro submission failed: {exc}\n"
            f"SUBMIT THIS URL MANUALLY: {result.video_url}",
            file=sys.stderr,
        )
        return 1

    return 0


def main() -> int:
    processed = _load_processed()

    try:
        campaign = run_check()
    except VyroClientError as exc:
        print(f"Vyro check failed: {exc}", file=sys.stderr)
        return 1

    if campaign is None:
        print("No active campaign today. Exiting.")
        return 0

    if campaign.campaign_id in processed:
        print(f"Campaign '{campaign.campaign_id}' already processed. Skipping.")
        return 0

    print(f"Found new campaign: {campaign.campaign_id} — {campaign.name}")

    status = process_campaign(campaign)
    if status == 0:
        processed.add(campaign.campaign_id)
        _save_processed(processed)
    return status


if __name__ == "__main__":
    sys.exit(main())
