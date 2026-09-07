"""
test_clip_pick_render.py

STEP 4 ISOLATED TEST — pick the best source clip, download it, render a
vertical short. Does NOT upload or submit.
"""

from __future__ import annotations

import os
import shutil
import sys

from clip_picker import ClipPickError, collect_clips_from_doc_text, pick_best_clip
from google_doc_reader import GoogleDocReadError, fetch_google_doc_text
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements


def _get_doc_url() -> str | None:
    env_url = (os.environ.get("GOOGLE_DOC_URL") or "").strip()
    if env_url:
        print(f"Using GOOGLE_DOC_URL from environment: {env_url}")
        return env_url

    print("No GOOGLE_DOC_URL set — falling back to live Whop campaign lookup...")
    from whop_client import WhopClientError, check_configured_campaigns

    try:
        campaign = check_configured_campaigns()
    except WhopClientError as exc:
        print(f"\n❌ FAILED — Whop client raised an error:\n{exc}")
        return None

    if campaign is None or not campaign.reference_doc_url:
        print("\n⚠️  No Whop campaign / reference_doc_url available.")
        return None

    print(f"Got reference_doc_url from '{campaign.name}': {campaign.reference_doc_url}")
    return campaign.reference_doc_url


def main() -> int:
    print("=" * 70)
    print("STEP 4 TEST: best-clip pick + download + render")
    print("=" * 70)

    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    print(f"GOOGLE_DRIVE_API_KEY present: {bool(drive_key)}")
    print(f"GEMINI_API_KEY present: {bool((os.environ.get('GEMINI_API_KEY') or '').strip())}")
    if not drive_key:
        print("❌ GOOGLE_DRIVE_API_KEY missing.")
        return 1

    doc_url = _get_doc_url()
    if not doc_url:
        return 1

    try:
        text = fetch_google_doc_text(doc_url)
    except GoogleDocReadError as exc:
        print(f"\n❌ FAILED — could not fetch Google Doc:\n{exc}")
        return 1

    print(f"Doc text: {len(text)} chars")
    clips = collect_clips_from_doc_text(text)
    print(f"Clips discovered: {len(clips)}")
    if not clips:
        print("❌ No video files found in the doc/folder.")
        return 1

    work_dir = "work/part4"
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)

    try:
        winner = pick_best_clip(
            clips,
            work_dir=work_dir,
            api_key=drive_key,
            campaign_notes=text[:2000],
            min_seconds=12.0,
        )
    except ClipPickError as exc:
        print(f"\n❌ FAILED — clip picker:\n{exc}")
        return 1

    print(f"\nWinner path: {winner.local_path}")
    print(f"Winner name: {winner.clip.name}")
    print(f"Winner url:  {winner.clip.url}")

    req = CampaignRequirements(
        campaign_id="part4-test",
        min_seconds=12.0,
        max_seconds=35.0,
        mandatory_hashtags=["#shorts"],
    )
    output_path = "output/short.mp4"
    try:
        render_short(
            source_path=winner.local_path,
            output_path=output_path,
            req=req,
            fallback_caption_text="",
        )
    except RenderError as exc:
        print(f"\n⚠️  Picker+download worked, but render failed:\n{exc}")
        print("Part 4 pick is usable; renderer needs a separate fix.")
        return 1

    size = os.path.getsize(output_path) if os.path.isfile(output_path) else 0
    print(f"\n✅ Rendered {output_path} ({size} bytes)")
    print("✅ Part 4 complete (best clip picked + vertical short rendered).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
