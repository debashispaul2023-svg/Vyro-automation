"""Optional vidIQ metadata. No invented MCP calls. Fallback is mandatory."""
from __future__ import annotations

import os


def from_story(story: dict, campaign_tags: list[str]) -> dict:
    events = [e for e in (story.get("events") or []) if e and e != "UNKNOWN"]
    payload = {
        "platform": "YouTube Shorts",
        "game": story.get("game") or "Roll Anime Girls",
        "topic": "Roblox",
        "events": events,
        "story_structure": story.get("structure") or "",
        "hook": story.get("hook") or "",
        "duration": story.get("duration") or 0,
    }
    if not (os.environ.get("VIDIQ_MCP_URL") or "").strip():
        print("[vidiq] unavailable")
        print("[vidiq] fallback -> gemini")
        return {"ok": False, "reason": "vidiq mcp not configured", "payload": payload, "tags": list(campaign_tags)}
    print("[vidiq] endpoint configured but no verified MCP schema in this runtime")
    print("[vidiq] fallback -> gemini")
    return {"ok": False, "reason": "schema unavailable", "payload": payload, "tags": list(campaign_tags)}


def deterministic(hook: str, tags: list[str]) -> dict:
    locked = []
    for tag in ["#shorts", "#roblox", "#rollanimegirls"] + list(tags or []):
        item = tag if str(tag).startswith("#") else f"#{tag}"
        if item.lower() not in [t.lower() for t in locked]:
            locked.append(item)
    title = (hook or "Roll Anime Girls Roblox")[:70]
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"[:100]
    return {
        "title": title,
        "description": "Roll Anime Girls is a Roblox RNG tycoon. " + " ".join(locked),
        "tags": locked,
    }
