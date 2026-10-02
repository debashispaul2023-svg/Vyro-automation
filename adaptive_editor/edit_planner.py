"""Planning only. Renderer executes this. Renderer does not invent the story."""
from __future__ import annotations

from .caption_planner import captions
from .voice_planner import script_for


def plan(story, review: dict | None = None) -> dict:
    steps = story.roadmap or []
    script, blocked = script_for(steps)
    story.script = script
    story.unsupported_claims = blocked
    clips = []
    t = 0.0
    for step in steps:
        dur = round(float(step["end"]) - float(step["start"]), 2)
        clips.append({**step, "timeline_start": round(t, 2), "timeline_end": round(t + dur, 2)})
        t += dur
    return {
        "duration_target": round(min(28.0, max(t, 8.0)), 2),
        "clips": clips,
        "cuts": [{"at": c["timeline_end"], "reason": c.get("reason")} for c in clips],
        "captions": captions(steps),
        "voice": [{"text": script, "start": 0.2}],
        "effects": [{"type": "hold", "when": "reveal"}] if any(c.get("reason") in ("RARE_REVEAL", "CHARACTER_REVEAL") for c in clips) else [],
        "hook": story.hook,
        "cta": {"text": "Try Roll Anime Girls on Roblox", "at": "end"},
        "review": review or {},
        "signature": {
            "hook_type": (story.hook or {}).get("reason", "").lower(),
            "structure": story.structure,
            "pacing": "adaptive",
            "caption_style": "minimal",
            "effect_profile": "light",
        },
    }
