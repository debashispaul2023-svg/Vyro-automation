"""Production adapter. Flag stays off unless ADAPTIVE_EDITOR_ENABLED=1."""
from __future__ import annotations

import os
import shutil

from adaptive_editor.engine import run_adaptive
from adaptive_editor.render.renderer import render_plan


def enabled() -> bool:
    return (os.environ.get("ADAPTIVE_EDITOR_ENABLED") or "0").strip() == "1"


def script_from_events(events: list[str]) -> str:
    allowed = [e for e in events if e and e != "UNKNOWN"]
    if not allowed:
        return "Gameplay is visible. No verified event."
    return "Visible events: " + ", ".join(dict.fromkeys(allowed)) + "."


def try_render(clips: list[str], dest: str) -> dict:
    if not enabled():
        print("[adaptive] ADAPTIVE_EDITOR_ENABLED=0 — using existing renderer")
        return {"ok": False, "reason": "flag off"}
    result = run_adaptive(clips, out_dir="output/adaptive/production", local_test=False)
    if not result.get("ok"):
        print("[adaptive] failed — existing renderer fallback")
        return result
    story = result.get("story") or {}
    events = [step.get("event") for step in (story.get("roadmap") or [])]
    story["script"] = script_from_events(events)
    plan = result.get("edit_plan") or {}
    plan["voice"] = [{"text": story["script"]}]
    rendered = render_plan(plan, dest, work="output/adaptive/production/parts")
    if not rendered.get("ok"):
        print("[adaptive] render failed — existing renderer fallback")
        return {"ok": False, "reason": rendered.get("reason")}
    print(f"[adaptive] using adaptive output {dest}")
    return {"ok": True, "story": story, "path": dest, "events": events}
