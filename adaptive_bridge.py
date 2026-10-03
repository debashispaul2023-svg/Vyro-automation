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
    story = result.get("story") or {}
    events = [step.get("event") for step in (story.get("roadmap") or []) if step.get("event") != "UNKNOWN"]
    story["script"] = script_from_events(events)
    plan = result.get("edit_plan") or {}
    if not plan.get("clips"):
        plan = {
            "signature": "visual_only_selected_clips",
            "clips": [
                {"clip": path, "start": 0.4, "end": 2.2, "role": "VISUAL", "reason": "campaign selected"}
                for path in clips[:3]
                if os.path.isfile(path)
            ],
        }
        print("[adaptive] no verified event — visual plan from selected campaign clips")
    plan["voice"] = [{"text": story["script"]}]
    rendered = render_plan(plan, dest, work="output/adaptive/production/parts")
    if not rendered.get("ok"):
        print("[adaptive] render failed — existing renderer fallback")
        return {"ok": False, "reason": rendered.get("reason")}
    print(f"[adaptive] using adaptive output {dest}")
    return {"ok": True, "story": story, "path": dest, "events": events}
