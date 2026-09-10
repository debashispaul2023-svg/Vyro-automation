"""
test_instagram_upload.py

STEP 6 ISOLATED TEST — render + Instagram Reels upload.

Does NOT touch Whop submit or YouTube.
Uses the same known-good Part 4 clip (Comp 1_43) so footage is not the variable.

Required secrets:
  GOOGLE_DRIVE_API_KEY
  IG_ACCESS_TOKEN
  IG_BUSINESS_ACCOUNT_ID
  ASSET_HOST_REPO     (public repo owner/name)
  ASSET_HOST_TOKEN    (PAT with repo scope on that public repo)
"""

from __future__ import annotations

import os
import sys
import time

from clip_picker import ClipPickError, download_drive_file
from instagram_uploader import InstagramUploadError, upload_reel
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements

DEFAULT_CLIP_FILE_ID = "1lYi7WDssaaRpZTgZEyJjs1l_YyGsuEhE"
SOURCE_PATH = "work/part6/source.mp4"
OUTPUT_PATH = "output/short.mp4"


def main() -> int:
    print("=" * 70)
    print("STEP 6 TEST: render + Instagram Reels upload")
    print("=" * 70)

    needed = [
        "GOOGLE_DRIVE_API_KEY",
        "IG_ACCESS_TOKEN",
        "IG_BUSINESS_ACCOUNT_ID",
        "ASSET_HOST_REPO",
        "ASSET_HOST_TOKEN",
    ]
    missing = [n for n in needed if not (os.environ.get(n) or "").strip()]
    for n in needed:
        print(f"{n} present: {bool((os.environ.get(n) or '').strip())}")
    if missing:
        print("❌ Missing secrets:", ", ".join(missing))
        print("Add them in GitHub → Settings → Secrets → Actions, then re-run.")
        return 1

    file_id = (os.environ.get("PART6_CLIP_FILE_ID") or "").strip() or DEFAULT_CLIP_FILE_ID
    print(f"clip file_id: {file_id}")

    try:
        download_drive_file(file_id, SOURCE_PATH, os.environ["GOOGLE_DRIVE_API_KEY"].strip())
    except ClipPickError as exc:
        print(f"❌ Drive download failed: {exc}")
        return 1

    req = CampaignRequirements(
        campaign_id="part6-test",
        min_seconds=12.0,
        max_seconds=35.0,
        mandatory_hashtags=["#shorts"],
    )
    try:
        render_short(
            source_path=SOURCE_PATH,
            output_path=OUTPUT_PATH,
            req=req,
            fallback_caption_text="",
        )
    except RenderError as exc:
        print(f"❌ Render failed: {exc}")
        return 1
    print(f"Rendered {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")

    caption = (
        "Pipeline test reel — will delete after checking.\n"
        "#Ad\n"
        "#shorts"
    )
    tag = f"part6-{int(time.time())}"
    try:
        media_id = upload_reel(OUTPUT_PATH, caption, tag)
    except InstagramUploadError as exc:
        print(f"❌ Instagram upload failed: {exc}")
        return 1

    print(f"✅ Published Instagram Reel media_id: {media_id}")
    print("✅ Part 6 complete. Do NOT submit this to Whop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
