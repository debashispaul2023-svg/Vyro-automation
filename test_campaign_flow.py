#!/usr/bin/env python3
"""Standalone test. Does not replace daily_runner.py or vyro_daily.yml.

Join/read campaign → open footage folder → pick clips → render.
Blocks YouTube, Instagram, and Whop submit at runtime.
Does not mark clips used.
"""
from __future__ import annotations

import os
import sys

import daily_runner as dr


def _block_youtube(*_a, **_k):
    print("[test] YouTube upload blocked")

    class _Dummy:
        video_url = ""

    return _Dummy()


def _block_instagram(*_a, **_k):
    print("[test] Instagram upload blocked")
    raise dr.InstagramUploadError("test mode — upload skipped")


def _block_submit(*_a, **_k):
    print("[test] Whop/Vyro submit blocked")


def main() -> int:
    print("=" * 70)
    print("TEST FILE: test_campaign_flow.py")
    print("Does NOT merge into Daily workflow.")
    print("Uploads OFF. Submit OFF.")
    print("=" * 70)

    dr.upload_video = _block_youtube
    dr.upload_reel = _block_instagram
    dr._submit_back = _block_submit
    os.environ["VYRO_SKIP_UPLOAD"] = "1"
    os.environ["CLIP_REUSE"] = "1"
    os.environ["WHOP_ENABLE_DISCOVER"] = "0"
    print("[test] CLIP_REUSE=1 — old clips allowed (no upload)")
    print("[test] Discover join off — use already-joined Tongue/Fisch")

    print(f"GOOGLE_DRIVE_API_KEY present: {bool((os.environ.get('GOOGLE_DRIVE_API_KEY') or '').strip())}")
    print(f"WHOP_COOKIE_HEADER present: {bool((os.environ.get('WHOP_COOKIE_HEADER') or '').strip())}")

    platform, campaign = dr._find_campaign()
    if campaign is None:
        print("FAIL: no campaign found (join/read step).")
        return 1

    print(f"[ok] campaign platform={platform}")
    print(f"[ok] name={campaign.name!r} id={campaign.campaign_id}")
    print(f"[ok] footage={campaign.source_clip_url or '(none)'}")
    print(f"[ok] doc={getattr(campaign, 'reference_doc_url', None) or '(none)'}")
    rules = (campaign.requirements_text or "")[:400].replace("\n", " | ")
    print(f"[ok] requirements snippet: {rules!r}")

    refuse = getattr(dr, "_refuse_mixed_footage", None)
    if callable(refuse):
        try:
            refuse(campaign)
            print("[ok] footage folder matches this campaign")
        except Exception as exc:
            print(f"FAIL mix-guard: {exc}")
            return 1

    log = dr._load_clip_log()
    pack = dr._next_unused_pack(campaign, log, want=4)
    if not pack:
        print("FAIL: folder opened but no unused clip could be selected.")
        return 1
    campaign._merge_pack = pack  # type: ignore[attr-defined]
    print("[ok] selected pack:")
    for i, clip in enumerate(pack, 1):
        print(f"     {i}. {clip.get('name')} ({clip.get('kind')}) {clip.get('clip_id')}")

    status = dr.process_campaign(platform, campaign, preferred_clip=pack[0])
    out = dr.OUTPUT_PATH
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        print(f"[ok] rendered {out} ({os.path.getsize(out)} bytes)")
        print("PASS: join/read/select/generate works. Nothing uploaded.")
        return 0
    print(f"FAIL: render missing (process status={status})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
