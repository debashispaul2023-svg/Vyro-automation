"""Production adapter. Flag stays off unless ADAPTIVE_EDITOR_ENABLED=1."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess


def enabled() -> bool:
    return (os.environ.get("ADAPTIVE_EDITOR_ENABLED") or "0").strip() == "1"


def _probe(path: str) -> float:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True, timeout=20,
        )
        return float((p.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def adaptive_input(clips) -> dict:
    rows = []
    for item in clips or []:
        if isinstance(item, str):
            item = {"path": item}
        path = item.get("path") or item.get("source_path") or ""
        if not path or not os.path.isfile(path):
            continue
        if os.path.basename(path) == "input_16x9.mp4":
            print("[adaptive] rejected stitched input_16x9.mp4")
            continue
        dur = _probe(path)
        rows.append({
            "source_file_id": item.get("source_file_id") or item.get("clip_id") or "",
            "filename": item.get("filename") or os.path.basename(path),
            "source_path": path,
            "duration": round(dur, 2),
            "start": 0.2,
            "end": round(min(4.6, max(0.8, dur - 0.1)), 2),
            "sha256": _hash(path),
        })
    return {"selected_clips": rows, "campaign_context": "Roll Anime Girls"}


def _vision(path: str) -> dict:
    try:
        import gemini_runtime
        import google.generativeai as genai
    except Exception as exc:
        print(f"[vision] unavailable {exc}")
        return {"event": "UNKNOWN", "confidence": 0, "status": "GEMINI_VISION_NOT_VERIFIED"}
    keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    if not keys:
        print("[vision] no key")
        return {"event": "UNKNOWN", "confidence": 0, "status": "GEMINI_VISION_NOT_VERIFIED"}
    frame = path + ".jpg"
    subprocess.run(["ffmpeg", "-y", "-ss", "1", "-i", path, "-frames:v", "1", frame], capture_output=True)
    if not os.path.isfile(frame):
        return {"event": "UNKNOWN", "confidence": 0, "status": "GEMINI_VISION_NOT_VERIFIED"}
    rt = gemini_runtime.RUNTIME
    model = rt.choose_model("vision")
    if not model:
        return {"event": "UNKNOWN", "confidence": 0, "status": "GEMINI_VISION_NOT_VERIFIED"}
    rt.note_attempt(model)
    try:
        genai.configure(api_key=keys[0])
        resp = genai.GenerativeModel(model).generate_content([
            "JSON only with event, confidence, reason. event is ROLL, CHARACTER_REVEAL, REWARD, or UNKNOWN. UNKNOWN unless clearly visible.",
            {"mime_type": "image/jpeg", "data": open(frame, "rb").read()},
        ])
        text = (getattr(resp, "text", None) or "")[:180]
        print(f"[vision] clip={os.path.basename(path)} model={model} frames=1 {text}")
        rt.mark_success(model)
        return {"event": "UNKNOWN", "confidence": 0, "status": "called", "model": model, "raw": text}
    except Exception as exc:
        if rt.classify(exc) == "QUOTA_EXHAUSTED":
            rt.quarantine(model, "QUOTA_EXHAUSTED")
        print(f"[vision] failed {os.path.basename(path)} {str(exc)[:120]}")
        return {"event": "UNKNOWN", "confidence": 0, "status": "GEMINI_VISION_NOT_VERIFIED"}


def try_render(clips, dest: str) -> dict:
    if not enabled():
        print("[adaptive] ADAPTIVE_EDITOR_ENABLED=0 — using existing renderer")
        return {"ok": False, "reason": "flag off"}
    from adaptive_editor.render.renderer import render_plan

    pack = adaptive_input(clips)
    rows = pack["selected_clips"]
    print(f"[adaptive] individual clips={len(rows)}")
    if not rows:
        print("[adaptive] INSUFFICIENT_ADAPTIVE_FOOTAGE")
        return {"ok": False, "reason": "INSUFFICIENT_ADAPTIVE_FOOTAGE"}
    events = []
    for row in rows:
        print(f"[adaptive] source id={row['source_file_id']} file={row['filename']}")
        seen = _vision(row["source_path"])
        row["vision"] = seen
        if seen.get("event") not in ("", "UNKNOWN", None):
            events.append(seen["event"])
    hook = max(rows, key=lambda r: r["duration"])
    lines = [
        "Can you roll your favorite character?",
        "Roll the dice to unlock one.",
        "Place it on your plot.",
        "It earns money while you are offline.",
        "The game is Roll Anime Girls.",
    ]
    script = " ".join(lines)
    plan = {
        "source_clips": [r["source_path"] for r in rows],
        "story_structure": "HOOK-SETUP-ACTION-PAYOFF" if len(rows) >= 3 else "limited",
        "hook": {"clip": hook["filename"], "reason": "longest selected campaign clip"},
        "render_mode": "adaptive",
        "cta": "Try Roll Anime Girls on Roblox.",
        "clips": [],
        "voice": [{"text": script}],
        "captions": [],
    }
    t = 0.0
    roles = ["HOOK", "SETUP", "ACTION", "PAYOFF", "CTA"]
    for i, row in enumerate(rows):
        dur = max(0.4, row["end"] - row["start"])
        plan["clips"].append({
            "clip": row["source_path"],
            "start": row["start"],
            "end": row["end"],
            "role": roles[min(i, len(roles) - 1)],
            "reason": row["filename"],
        })
        plan["captions"].append({"start": round(t, 2), "end": round(t + dur, 2), "text": lines[min(i, len(lines) - 1)]})
        t += dur
    plan["duration_target"] = round(t, 2)
    plan["target_duration"] = plan["duration_target"]
    if t < 15:
        print("[adaptive] INSUFFICIENT_ADAPTIVE_FOOTAGE")
        return {"ok": False, "reason": "INSUFFICIENT_ADAPTIVE_FOOTAGE", "duration": t}
    if t > 24.5:
        plan["clips"] = plan["clips"][:5]
    os.makedirs("output/adaptive/test", exist_ok=True)
    story = {
        "selected_clips": rows,
        "detected_events": events,
        "hook": plan["hook"],
        "roadmap": [c["role"] for c in plan["clips"]],
        "story_structure": plan["story_structure"],
        "script": script,
        "captions": plan["captions"],
        "final_edit_plan": {"render_mode": "adaptive", "target_duration": plan["duration_target"]},
    }
    json.dump(story, open("output/adaptive/test/story.json", "w", encoding="utf-8"), indent=2)
    print(f"[adaptive] story.json output/adaptive/test/story.json duration={plan['duration_target']}")
    rendered = render_plan(plan, dest, work="output/adaptive/production/parts")
    if not rendered.get("ok") or not rendered.get("captions_burned"):
        print("[adaptive] render failed — existing renderer fallback")
        return {"ok": False, "reason": rendered.get("reason") or "captions missing", "render": rendered}
    print(f"[adaptive] using adaptive output {dest}")
    return {"ok": True, "story": story, "path": dest, "events": events, "render": rendered}
