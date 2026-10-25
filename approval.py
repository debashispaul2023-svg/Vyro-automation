"""Human approval gate. Nothing is posted publicly (YouTube / Instagram) or submitted to Whop
until YOU approve the rendered Short.

Flow
  1. Daily run renders + validates one Short, saves it to review/ and STOPS (no upload, no submit, clips NOT marked used).
  2. You watch the video (GitHub Actions artifact `review-short`, or review/pending_short.mp4 in the repo).
  3. Run the workflow manually with publish_pending = approve  -> upload + submit.   reject -> discard, clip is reused later.
While something is pending, the daily run does nothing (so it cannot burn voice credits or stack up videos).
Turn the gate off with VYRO_REQUIRE_APPROVAL=0.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import time
from pathlib import Path

REVIEW_DIR = Path(os.environ.get("VYRO_REVIEW_DIR", "review"))
PENDING_JSON = REVIEW_DIR / "pending.json"
PENDING_VIDEO = REVIEW_DIR / "pending_short.mp4"
EXPIRE_HOURS = float(os.environ.get("VYRO_PENDING_EXPIRE_HOURS", "72"))


def required() -> bool:
    return (os.environ.get("VYRO_REQUIRE_APPROVAL") or "1").strip().lower() not in ("0", "false", "no")


def command() -> str:
    """'approve' | 'reject' | ''  (from workflow input VYRO_PUBLISH_PENDING)."""
    v = (os.environ.get("VYRO_PUBLISH_PENDING") or "").strip().lower()
    return v if v in ("approve", "reject") else ""


def has_pending() -> bool:
    return PENDING_JSON.is_file() and PENDING_VIDEO.is_file()


def pending_age_hours() -> float:
    try:
        data = json.loads(PENDING_JSON.read_text(encoding="utf-8"))
        return (time.time() - float(data.get("created_at") or 0)) / 3600.0
    except Exception:  # noqa: BLE001
        return 0.0


def clear_pending() -> None:
    for p in (PENDING_JSON, PENDING_VIDEO):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def save_pending(platform: str, campaign, video_path: str, meta, pack: list, req_notes: str = "") -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(video_path, PENDING_VIDEO)
    try:
        camp = dataclasses.asdict(campaign)
    except TypeError:
        camp = dict(getattr(campaign, "__dict__", {}))
    data = {
        "created_at": time.time(),
        "platform": platform,
        "campaign_class": type(campaign).__name__,
        "campaign": {k: v for k, v in camp.items() if isinstance(v, (str, int, float, bool, type(None), list, dict))},
        "pack": pack,
        "title": getattr(meta, "title", ""),
        "description": getattr(meta, "description", ""),
        "tags": list(getattr(meta, "tags", []) or []),
        "notes": req_notes,
    }
    PENDING_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return PENDING_JSON


def load_pending() -> dict:
    return json.loads(PENDING_JSON.read_text(encoding="utf-8"))


def summary(data: dict) -> str:
    c = data.get("campaign") or {}
    return (f"campaign={c.get('name')!r} id={c.get('campaign_id')} platform={data.get('platform')}\n"
            f"title: {data.get('title')}\n--- description ---\n{data.get('description')}")
