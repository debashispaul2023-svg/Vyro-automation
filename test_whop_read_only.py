"""
test_whop_read_only.py

STEP 2 ISOLATED TEST — Whop session login + reading ONE campaign's real
content (through its iframe, if it's embedded as one).

This does NOT download anything, generate metadata, upload anywhere, or
submit anything. It only:
  1. Logs in to Whop using WHOP_COOKIE_HEADER
  2. Opens the first campaign URL listed in whop_campaigns.json
  3. Prints out everything check_configured_campaigns() managed to read:
     campaign name, how much text was captured (and from where — main
     page or an iframe), the reference doc URL if found, and which
     platforms were detected.

Run this on its own (locally or as a one-off GitHub Actions step) to
confirm Part 2 works before moving on to Part 3 (Google Doc reading).

Usage:
    WHOP_COOKIE_HEADER=... python test_whop_read_only.py
"""

from __future__ import annotations

import sys

from whop_client import WhopClientError, check_configured_campaigns


def main() -> int:
    print("=" * 70)
    print("STEP 2 TEST: Whop login + single campaign read")
    print("=" * 70)

    try:
        campaign = check_configured_campaigns()
    except WhopClientError as exc:
        print(f"\n❌ FAILED — Whop client raised an error:\n{exc}")
        return 1

    if campaign is None:
        print(
            "\n⚠️  No usable campaign found. This could mean:\n"
            "   - whop_campaigns.json is empty or missing\n"
            "   - the campaign is region-locked or budget-exhausted\n"
            "   - the session cookie expired (check for a 'session expired' message above)\n"
            "Check the [whop_client debug] lines above this for more detail."
        )
        return 0

    print("\n✅ SUCCESS — Campaign read:")
    print(f"   campaign_id:       {campaign.campaign_id}")
    print(f"   name:              {campaign.name}")
    print(f"   platforms:         {campaign.platforms}")
    print(f"   reference_doc_url: {campaign.reference_doc_url}")
    print(f"   source_clip_url:   {campaign.source_clip_url or '(empty — expected, resolved in Part 3)'}")
    print(f"   requirements_text length: {len(campaign.requirements_text)} chars")
    print(f"   requirements_text preview:\n{'-' * 70}")
    print(campaign.requirements_text[:800])
    print("-" * 70)

    # Sanity check: did we actually get real content, or just nav junk?
    if len(campaign.requirements_text) < 150:
        print(
            "\n⚠️  WARNING: requirements_text is suspiciously short — this "
            "might still be nav-shell text rather than real campaign "
            "content. Check the [whop_client debug] lines above to see "
            "which frame was used."
        )
        return 1

    print("\n✅ Part 2 looks good. Ready to move on to Part 3 (Google Doc reading).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
