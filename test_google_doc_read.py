"""
test_google_doc_read.py

STEP 3 ISOLATED TEST — Google Doc reading + Drive-folder expansion +
candidate source-clip URL extraction.

Does NOT download video, render, upload, or submit.

Flow:
  1. Get the reference Google Doc URL from GOOGLE_DOC_URL, or by
     re-running the live Whop campaign lookup (Part 2).
  2. Fetch the doc text.
  3. Extract raw candidate URLs.
  4. If any candidate is a Drive folder, list video files inside it
     with GOOGLE_DRIVE_API_KEY.
  5. Print everything for manual inspection.

Usage:
    python test_google_doc_read.py
    GOOGLE_DOC_URL="https://docs.google.com/document/d/XXXX/edit" python test_google_doc_read.py
"""

from __future__ import annotations

import os
import sys

from google_doc_reader import (
    GoogleDocReadError,
    fetch_google_doc_text,
    find_candidate_source_clips,
    is_drive_folder_url,
    resolve_candidate_source_clips,
)


def _get_doc_url() -> str | None:
    env_url = (os.environ.get("GOOGLE_DOC_URL") or "").strip()
    if env_url:
        print(f"Using GOOGLE_DOC_URL from environment: {env_url}")
        return env_url

    print("No GOOGLE_DOC_URL set — falling back to live Whop campaign lookup (Part 2)...")
    from whop_client import WhopClientError, check_configured_campaigns

    try:
        campaign = check_configured_campaigns()
    except WhopClientError as exc:
        print(f"\n❌ FAILED — Whop client raised an error:\n{exc}")
        return None

    if campaign is None:
        print("\n⚠️  No usable Whop campaign found — can't get a reference_doc_url this way.")
        return None

    if not campaign.reference_doc_url:
        print(f"\n⚠️  Campaign '{campaign.name}' has no reference_doc_url.")
        return None

    print(
        f"Got reference_doc_url from Whop campaign '{campaign.name}': "
        f"{campaign.reference_doc_url}"
    )
    return campaign.reference_doc_url


def main() -> int:
    print("=" * 70)
    print("STEP 3 TEST: Google Doc + Drive folder → source-clip URLs")
    print("=" * 70)

    has_drive_key = bool((os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip())
    print(f"GOOGLE_DRIVE_API_KEY present: {has_drive_key}")

    doc_url = _get_doc_url()
    if not doc_url:
        return 1

    try:
        text = fetch_google_doc_text(doc_url)
    except GoogleDocReadError as exc:
        print(f"\n❌ FAILED — could not fetch Google Doc:\n{exc}")
        return 1

    print(f"\n✅ Fetched doc text: {len(text)} chars")
    print("-" * 70)
    print(text[:1500])
    print("-" * 70)

    raw = find_candidate_source_clips(text)
    print(f"\nRaw candidate URLs (before folder expand): {len(raw)}")
    for i, url in enumerate(raw, 1):
        kind = "DRIVE FOLDER" if is_drive_folder_url(url) else "link"
        print(f"   {i}. [{kind}] {url}")

    if not raw:
        print(
            "\n⚠️  Doc readable, but no Drive/YouTube/Dropbox/.mp4-style link found."
        )
        return 1

    try:
        resolved = resolve_candidate_source_clips(text)
    except GoogleDocReadError as exc:
        print(f"\n❌ FAILED — could not resolve footage URLs:\n{exc}")
        return 1

    print(f"\nResolved source-clip URLs: {len(resolved)}")
    for i, url in enumerate(resolved, 1):
        print(f"   {i}. {url}")

    if not resolved:
        print(
            "\n⚠️  Folder listed (or links found) but no video file matched. "
            "Check the [drive] lines above — if the folder is empty / private "
            "/ only has images, Part 3 cannot pick a clip."
        )
        return 1

    print("\n✅ Part 3 complete. Ready for Part 4 (video download + render).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
