"""
test_google_doc_read.py

STEP 3 ISOLATED TEST — Google Doc reading + candidate source-clip URL
extraction.

This does NOT download any video, render anything, or touch Whop/Vyro.
It only:
  1. Gets the reference Google Doc URL — either from the GOOGLE_DOC_URL
     env var (if set), or by re-running check_configured_campaigns()
     against the live Whop campaign (same as Part 2) and using its
     reference_doc_url
  2. Fetches the doc's plain text via fetch_google_doc_text()
  3. Extracts candidate source-clip URLs via find_candidate_source_clips()
  4. Prints everything for manual inspection

Run this as a one-off GitHub Actions step to confirm Part 3 works before
moving on to Part 4 (video download + render).

Usage:
    python test_google_doc_read.py
    # or, to test a specific doc without touching Whop:
    GOOGLE_DOC_URL="https://docs.google.com/document/d/XXXX/edit" python test_google_doc_read.py
"""

from __future__ import annotations

import os
import sys

from google_doc_reader import GoogleDocReadError, fetch_google_doc_text, find_candidate_source_clips


def _get_doc_url() -> str | None:
    env_url = os.environ.get("GOOGLE_DOC_URL")
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

    print(f"Got reference_doc_url from Whop campaign '{campaign.name}': {campaign.reference_doc_url}")
    return campaign.reference_doc_url


def main() -> int:
    print("=" * 70)
    print("STEP 3 TEST: Google Doc read + candidate source-clip extraction")
    print("=" * 70)

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

    candidates = find_candidate_source_clips(text)
    print(f"\nCandidate source-clip URLs found: {len(candidates)}")
    for i, url in enumerate(candidates, 1):
        print(f"   {i}. {url}")

    if not candidates:
        print(
            "\n⚠️  No candidate URLs matched (drive.google.com / .mp4 / dropbox.com / "
            "wetransfer.com / youtube.com / youtu.be). Either the doc has no direct "
            "footage link (may need to look for a different pattern), or the doc "
            "content itself isn't publicly reachable — check the text preview above."
        )
        return 1

    print("\n✅ Part 3 looks good. Ready to move on to Part 4 (video download + render).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
