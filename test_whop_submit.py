"""
test_whop_submit.py

STEP 7 ISOLATED TEST — open joined Whop campaign and fill Submit clip.

Does NOT download footage or render.
Default is DRY RUN (does not click the final Submit).
Set CONFIRM_SUBMIT=yes to actually submit.

Required:
  WHOP_COOKIE_HEADER
  VIDEO_URL  — public Instagram / YouTube / TikTok link

Do NOT submit the U2/FIFA test video (youtu.be/SlakivTUMj8 or Comp 1_43)
to the Zodiac CoD campaign. That will be rejected.
"""

from __future__ import annotations

import os
import sys

from whop_client import WhopClientError, check_configured_campaigns, submit_video_link

_BLOCKED_URL_BITS = (
    "slakivtumj8",
    "1lyi7wdssaarpztgzeyjjs1l_yygsuehe",
    "street of dreams",
)


def main() -> int:
    print("=" * 70)
    print("STEP 7 TEST: Whop Submit clip")
    print("=" * 70)

    if not (os.environ.get("WHOP_COOKIE_HEADER") or "").strip():
        print("❌ WHOP_COOKIE_HEADER missing.")
        return 1

    video_url = (os.environ.get("VIDEO_URL") or "").strip()
    confirm = (os.environ.get("CONFIRM_SUBMIT") or "").strip().lower() in ("yes", "true", "1")
    print(f"VIDEO_URL: {video_url or '(empty)'}")
    print(f"CONFIRM_SUBMIT: {confirm}")

    if not video_url:
        print("❌ Put the public Reel/YouTube/TikTok URL in the workflow VIDEO_URL box.")
        return 1
    if not video_url.startswith("http"):
        print("❌ VIDEO_URL must be a full https:// link.")
        return 1

    blocked = [b for b in _BLOCKED_URL_BITS if b in video_url.lower()]
    if blocked:
        print("❌ This looks like the U2/FIFA Part 5 test clip. Do not submit it to Zodiac.")
        return 1

    try:
        campaign = check_configured_campaigns()
    except WhopClientError as exc:
        print(f"❌ Whop campaign open failed: {exc}")
        return 1

    if campaign is None:
        print("❌ No usable campaign in whop_campaigns.json.")
        return 1

    print(f"Campaign: {campaign.campaign_id} — {campaign.name}")
    print(f"Submit page: {campaign.submit_page_url}")

    try:
        submit_video_link(campaign, video_url, dry_run=not confirm)
    except WhopClientError as exc:
        print(f"❌ Submit flow failed: {exc}")
        return 1

    if confirm:
        print("✅ Part 7 submitted. Check Whop → Submissions.")
    else:
        print("✅ Part 7 dry-run complete (form opened + URL filled).")
        print("Re-run with confirm_submit=yes only if this is a REAL Zodiac/IG clip URL.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
  
