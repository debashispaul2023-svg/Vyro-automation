"""Optional vidIQ metadata. No invented MCP calls. Fallback is mandatory."""
from __future__ import annotations

import os


def from_story(story: dict, campaign_tags: list[str]) -> dict:
    events = [e for e in (story.get("events") or []) if e and e != "UNKNOWN"]
    payload = {
        "platform": "YouTube Shorts",
        "game": story.get("game") or "",
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


def _game_tag(game: str | None) -> str:
    import re
    g = re.sub(r"[^a-z0-9]", "", (game or "").lower())
    return f"#{g}" if g else ""


def deterministic(hook: str, tags: list[str], game: str | None = None) -> dict:
    """Fallback metadata. Uses ONLY the current campaign's game (never another game's name)."""
    locked = []
    for tag in ["#shorts", "#roblox", _game_tag(game)] + list(tags or []):
        if not tag:
            continue
        item = tag if str(tag).startswith("#") else f"#{tag}"
        if item.lower() not in [t.lower() for t in locked]:
            locked.append(item)
    name = (game or "").strip()
    title = (hook or (f"{name} Roblox" if name else "Roblox clip"))[:70]
    if "#shorts" not in title.lower():
        title = f"{title} #shorts"[:100]
    body = f"{name} is out now on Roblox. " if name else ""
    return {"title": title, "description": body + " ".join(locked), "tags": locked}
