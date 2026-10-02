"""Fallback hierarchy. Never silent."""
from __future__ import annotations


def fallback(reason: str) -> dict:
    print(f"[adaptive] {reason}")
    print("[adaptive] falling back to story_engine")
    print("[adaptive] story_engine not called from this layer — production path stays unchanged")
    print("[adaptive] falling back to campaign_pack")
    return {"ok": False, "fallback": "campaign_pack", "reason": reason}
