"""
test_youtube_upload.py

STEP 5 ISOLATED TEST — Gemini metadata + YouTube upload.

Does NOT touch Whop submit or Instagram.
Does NOT re-run the 3-clip picker (Part 4 already picked a winner).

Flow:
  1. Fetch the campaign Google Doc (GOOGLE_DOC_URL or the known U2 doc).
  2. Download one known-good Drive file (PART5_CLIP_FILE_ID or Comp 1_43).
  3. Render a 1080x1920 short.
  4. Generate title/description with Gemini (template fallback).
  5. Upload to YouTube as UNLISTED and print the URL.

Usage:
    python test_youtube_upload.py
"""

from __future__ import annotations

import os
import sys

from ai_brain import AIBrainError, ai_generate_metadata
from clip_picker import ClipPickError, download_drive_file
from google_doc_reader import GoogleDocReadError, fetch_google_doc_text
from metadata import VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements
from youtube_uploader import UploadError, upload_video

DEFAULT_DOC_URL = (
    "https://docs.google.com/document/d/"
    "1zEtxP_3Qfw-bFY4awBFj8ALOnVNTzvleiifQeRn1oCo/edit?usp=sharing"
)
# Comp 1_43.mp4 — Part 4 winner
DEFAULT_CLIP_FILE_ID = "1lYi7WDssaaRpZTgZEyJjs1l_YyGsuEhE"
SOURCE_PATH = "work/part5/source.mp4"
OUTPUT_PATH = "output/short.mp4"


def _doc_url() -> str:
    return (os.environ.get("GOOGLE_DOC_URL") or "").strip() or DEFAULT_DOC_URL


def _clip_file_id() -> str:
    return (os.environ.get("PART5_CLIP_FILE_ID") or "").strip() or DEFAULT_CLIP_FILE_ID


def _build_metadata(hook: str, summary: str, req: CampaignRequirements) -> VideoMetadata:
    try:
        ai_meta = ai_generate_metadata(
            hook=hook,
            summary=summary,
            mandatory_hashtags=req.mandatory_hashtags,
            required_links=req.required_links,
            referral_code=req.referral_code,
        )
        print("[meta] Gemini metadata OK")
        return VideoMetadata(
            title=ai_meta.title[:100],
            description=ai_meta.description,
            tags=ai_meta.hashtags,
        )
    except AIBrainError as exc:
        print(f"[meta] Gemini failed ({exc}); using template fallback")
        return generate_metadata(hook=hook, summary=summary, req=req)


def main() -> int:
    print("=" * 70)
    print("STEP 5 TEST: Gemini metadata + YouTube upload")
    print("=" * 70)

    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    print(f"GOOGLE_DRIVE_API_KEY present: {bool(drive_key)}")
    print(f"GEMINI_API_KEY present: {bool((os.environ.get('GEMINI_API_KEY') or '').strip())}")
    print(f"token.json present: {os.path.isfile('token.json')}")
    if not drive_key:
        print("❌ GOOGLE_DRIVE_API_KEY missing.")
        return 1
    if not os.path.isfile("token.json"):
        print("❌ token.json missing. Workflow must write YOUTUBE_TOKEN_JSON to token.json.")
        return 1

    doc_url = _doc_url()
    file_id = _clip_file_id()
    print(f"doc_url: {doc_url}")
    print(f"clip file_id: {file_id}")

    try:
        doc_text = fetch_google_doc_text(doc_url)
    except GoogleDocReadError as exc:
        print(f"\n❌ FAILED — could not fetch Google Doc:\n{exc}")
        return 1
    print(f"Doc text: {len(doc_text)} chars")

    try:
        download_drive_file(file_id, SOURCE_PATH, drive_key)
    except ClipPickError as exc:
        print(f"\n❌ FAILED — Drive download:\n{exc}")
        return 1

    req = CampaignRequirements(
        campaign_id="part5-test",
        min_seconds=12.0,
        max_seconds=35.0,
        mandatory_hashtags=["#shorts", "#FIFA", "#WorldCup"],
    )
    hook = "U2 Street of Dreams | FIFA World Cup edit"

    try:
        render_short(
            source_path=SOURCE_PATH,
            output_path=OUTPUT_PATH,
            req=req,
            fallback_caption_text="",
        )
    except RenderError as exc:
        print(f"\n❌ FAILED — render:\n{exc}")
        return 1
    size = os.path.getsize(OUTPUT_PATH)
    print(f"Rendered {OUTPUT_PATH} ({size} bytes)")

    meta = _build_metadata(hook=hook, summary=doc_text[:1500], req=req)
    print(f"\n--- Title ---\n{meta.title}")
    print(f"--- Description ---\n{meta.description[:500]}")
    print(f"--- Tags ---\n{meta.tags}")

    try:
        result = upload_video(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            tags=[t.lstrip("#") for t in meta.tags][:15],
            privacy_status="unlisted",
            token_path="token.json",
        )
    except UploadError as exc:
        print(f"\n❌ FAILED — YouTube upload:\n{exc}")
        return 1

    print(f"\n✅ Uploaded (unlisted): {result.video_url}")
    print(f"   video_id: {result.video_id}")
    print("✅ Part 5 complete. Do NOT submit this to Whop yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
