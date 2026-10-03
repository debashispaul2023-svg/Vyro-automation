"""Production adapter. Flag stays off unless ADAPTIVE_EDITOR_ENABLED=1."""
from __future__ import annotations

import os
import subprocess
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


def _vision_frame(path: str) -> None:
    try:
        import gemini_runtime
        import google.generativeai as genai
    except Exception as exc:
        print(f"[vision] unavailable {exc}")
        return
    keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    if not keys:
        print("[vision] no key")
        return
    frame = path + ".jpg"
    subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", path, "-frames:v", "1", frame], capture_output=True)
    if not os.path.isfile(frame):
        print(f"[vision] no frame {os.path.basename(path)}")
        return
    rt = gemini_runtime.RUNTIME
    model = rt.choose_model("vision")
    if not model:
        return
    rt.note_attempt(model)
    try:
        genai.configure(api_key=keys[0])
        resp = genai.GenerativeModel(model).generate_content([
            "JSON only. event must be ROLL, REWARD, CHARACTER_REVEAL, or UNKNOWN. UNKNOWN unless the frame clearly shows it.",
            {"mime_type": "image/jpeg", "data": open(frame, "rb").read()},
        ])
        print(f"[vision] clip={os.path.basename(path)} model={model} {(getattr(resp, 'text', None) or '')[:160]}")
        rt.mark_success(model)
    except Exception as exc:
        if rt.classify(exc) == "QUOTA_EXHAUSTED":
            rt.quarantine(model, "QUOTA_EXHAUSTED")
        print(f"[vision] failed {os.path.basename(path)} {str(exc)[:120]}")


def try_render(clips: list[str], dest: str) -> dict:
    if not enabled():
        print("[adaptive] ADAPTIVE_EDITOR_ENABLED=0 — using existing renderer")
        return {"ok": False, "reason": "flag off"}
    clips = [path for path in clips if path and os.path.isfile(path)]
    print(f"[adaptive] individual clips={len(clips)}")
    for path in clips:
        print(f"[adaptive] source={os.path.basename(path)}")
        _vision_frame(path)
    result = run_adaptive(clips, out_dir="output/adaptive/production", local_test=False)
    story = result.get("story") or {}
    events = [step.get("event") for step in (story.get("roadmap") or []) if step.get("event") not in ("", "UNKNOWN")]
    story["script"] = script_from_events(events)
    plan = result.get("edit_plan") or {}
    if not plan.get("clips"):
        plan = {"signature": "visual_only_selected_clips", "clips": []}
        for path in clips[:5]:
            plan["clips"].append({
                "clip": path,
                "start": 0.3,
                "end": 3.4,
                "role": "VISUAL",
                "reason": "campaign selected individual clip",
            })
        print("[adaptive] no verified event — segments from selected clips only")
    plan["voice"] = [{"text": story["script"]}]
    plan["captions"] = []
    t = 0.0
    for clip in plan["clips"]:
        dur = max(0.4, float(clip["end"]) - float(clip["start"]))
        plan["captions"].append({"start": t, "end": t + dur, "text": story["script"][:42]})
        t += dur
    plan["duration_target"] = t
    rendered = render_plan(plan, dest, work="output/adaptive/production/parts")
    if not rendered.get("ok") or not rendered.get("captions_burned"):
        print("[adaptive] render failed — existing renderer fallback")
        return {"ok": False, "reason": rendered.get("reason") or "captions missing", "render": rendered}
    print(f"[adaptive] using adaptive output {dest}")
    return {"ok": True, "story": story, "path": dest, "events": events, "render": rendered}
    if not rendered.get("ok"):
        print("[adaptive] render failed — existing renderer fallback")
        return {"ok": False, "reason": rendered.get("reason")}
    print(f"[adaptive] using adaptive output {dest}")
    return {"ok": True, "story": story, "path": dest, "events": events}
