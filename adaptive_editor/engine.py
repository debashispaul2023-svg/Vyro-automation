"""Orchestrator. Does not import daily_runner or upload anything."""
from __future__ import annotations

import json
import os

from . import config
from .analyzer import probe, sample_times
from .edit_planner import plan as make_plan
from .event_detector import event_from_name
from .fallback import fallback
from .hook_engine import score
from .providers.chatgpt_review import ChatGPTReviewProvider
from .providers.gemini_analyzer import GeminiReviewProvider
from .providers.local_analyzer import LocalReviewProvider
from .qc import qc
from .roadmap import build
from .schemas import Shot
from .shot_detector import detect_shots


def run_adaptive(clips: list[str], out_dir: str | None = None, history: list | None = None) -> dict:
    out = out_dir or config.output_dir()
    os.makedirs(out, exist_ok=True)
    print("[adaptive] candidates:", clips)
    if not clips:
        return fallback("no clips")
    shots = []
    print("[adaptive] analyzing:")
    for path in clips:
        meta = probe(path)
        print(f"  {os.path.basename(path)} {meta['duration']:.1f}s {meta['width']}x{meta['height']} audio={meta['audio']}")
        windows = detect_shots(path)
        if not windows and meta["duration"] > 0.5:
            windows = [(0.0, min(meta["duration"], 2.5))]
        for start, end in windows:
            shot = Shot(
                clip=path,
                start=start,
                end=end,
                event=event_from_name(os.path.basename(path)),
                reason=event_from_name(os.path.basename(path)),
            )
            shots.append(score(shot))
        print(f"  sample times {sample_times(meta['duration'])}")
    if not shots:
        return fallback("analysis failed")
    print("[adaptive] detected events:")
    for s in shots:
        print(f"  {os.path.basename(s.clip)} {s.start}-{s.end} {s.event} hook={s.hook_score:.2f}")
    review = _review(shots)
    advice = _memory(shots)
    story = build(shots, history or [])
    if advice.get("selected") and _compatible(advice["selected"], shots):
        story.structure = advice["selected"]
        print(f"[adaptive-memory] final roadmap: {story.structure}")
    else:
        print(f"[adaptive-memory] final roadmap: {story.structure}")
    print(f"[adaptive] strongest hook: {story.hook}")
    print(f"[adaptive] story structure: {story.structure}")
    print(f"[adaptive] roadmap: {story.roadmap}")
    edit = make_plan(story, review)
    print(f"[adaptive] script: {story.script}")
    print(f"[adaptive] edit plan: {edit['signature']}")
    report = qc(edit)
    _write(out, "story.json", story.to_dict())
    _write(out, "edit_plan.json", edit)
    _write(out, "qc_report.json", report)
    _save(shots, story, report)
    print("[adaptive] script generated from actual footage")
    return {"ok": report["ok"], "story": story.to_dict(), "edit_plan": edit, "qc": report, "out": out, "memory": advice}


def _memory(shots: list) -> dict:
    try:
        from .memory.retrieval import advise
        return advise(shots)
    except Exception as exc:
        print(f"[adaptive-memory] retrieval failed: {exc}")
        print("[adaptive-memory] continuing with fresh footage analysis")
        return {"ok": False, "selected": ""}


def _compatible(pattern_id: str, shots: list) -> bool:
    events = {s.event for s in shots}
    if pattern_id == "tease_roll_reveal":
        return "ROLL" in events and ("CHARACTER_REVEAL" in events or "RARE_REVEAL" in events)
    if pattern_id == "fail_attempt":
        return "FAIL" in events
    if pattern_id == "generic_roll":
        return "ROLL" in events
    return False


def _save(shots: list, story, report: dict) -> None:
    try:
        from .memory.footage_memory import save_experience
        save_experience(shots, story, report)
    except Exception as exc:
        print(f"[adaptive-memory] save failed: {exc}")


def _review(shots: list) -> dict:
    local = LocalReviewProvider().analyze(shots)
    print(f"[adaptive] local review confidence={local.get('confidence')}")
    gem = GeminiReviewProvider().analyze(shots)
    if not gem.get("ok"):
        print("[adaptive] Gemini unavailable")
    chat = ChatGPTReviewProvider().analyze(shots)
    if not chat.get("ok"):
        print("[adaptive] ChatGPT review skipped")
    return {"local": local, "gemini": gem, "chatgpt": chat}


def _write(out: str, name: str, payload: dict) -> None:
    path = os.path.join(out, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[adaptive] wrote {path}")
