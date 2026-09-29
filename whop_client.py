"""Load the working Whop client implementation and inject current BLOX campaign IDs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_impl_path = Path(__file__).with_name("whop_client-1.py")
_spec = importlib.util.spec_from_file_location("_whop_client_impl", _impl_path)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
sys.modules["_whop_client_impl"] = _mod
_spec.loader.exec_module(_mod)

_mod.BLOX_CAMPAIGN_IDS = {
    "Roll Anime Girls": "117ddb85-e38d-47e2-abc1-9bbaf7ec8d1c",
    "Steal A Seed": "ad06c6d9-d46f-4b03-9bf8-58bf8a09cf5b",
    "Tongue Escape": "ce2f887e-f54d-43b0-a2b9-e8da505f7b7a",
    "How to Fisch": "b59bb70c-58bf-44c1-9e44-0b54c59d90f4",
}


def _skipped(campaign, skip_ids) -> bool:
    skip = {str(x).strip().lower() for x in (skip_ids or set()) if str(x).strip()}
    if not skip:
        return False
    cid = str(getattr(campaign, "campaign_id", "") or "").strip().lower()
    name = str(getattr(campaign, "name", "") or "").strip().lower()
    if cid and cid in skip:
        return True
    if name and name in skip:
        return True
    return False


def check_configured_campaigns(skip_ids=None):
    """daily_runner passes skip_ids; older impl may not accept it."""
    fn = _mod.check_configured_campaigns
    try:
        campaign = fn(skip_ids=skip_ids)
    except TypeError:
        campaign = fn()
    if campaign is None:
        return None
    if _skipped(campaign, skip_ids):
        print(f"[whop] wrapper skip exact-match '{campaign.name}' id={campaign.campaign_id}")
        return None
    return campaign


WhopCampaign = _mod.WhopCampaign
WhopClientError = _mod.WhopClientError
WhopSessionExpired = getattr(_mod, "WhopSessionExpired", _mod.WhopClientError)
discover_and_join_new_campaigns = _mod.discover_and_join_new_campaigns
submit_video_link = _mod.submit_video_link
BLOX_CAMPAIGN_IDS = _mod.BLOX_CAMPAIGN_IDS

__all__ = [
    "WhopCampaign",
    "WhopClientError",
    "WhopSessionExpired",
    "check_configured_campaigns",
    "discover_and_join_new_campaigns",
    "submit_video_link",
    "BLOX_CAMPAIGN_IDS",
]
