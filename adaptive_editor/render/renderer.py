"""Execute an edit plan. Does not pick clips and does not upload."""
from __future__ import annotations

import os

from .ffmpeg_adapter import concat, probe, tone, trim


def render_plan(plan: dict, dest: str, work: str | None = None) -> dict:
    work = work or (dest + ".parts")
    os.makedirs(work, exist_ok=True)
    clips = plan.get("clips") or []
    if not clips:
        return _fail("empty edit plan")
    parts = []
    used = []
    for i, clip in enumerate(clips):
        src = clip.get("clip") or ""
        if not src or not os.path.isfile(src):
            return _fail(f"missing source {src}")
        part = os.path.join(work, f"{i:02d}.mp4")
        if not trim(src, float(clip.get("start") or 0), float(clip.get("end") or 0), part):
            return _fail(f"trim failed {src}")
        parts.append(part)
        used.append({"role": clip.get("role"), "clip": src, "reason": clip.get("reason")})
    audio = ""
    if plan.get("voice"):
        audio = os.path.join(work, "voice.m4a")
        if not tone(audio, float(plan.get("duration_target") or 8)):
            return _fail("audio bed failed")
    if not concat(parts, dest, audio):
        return _fail("concat failed")
    info = probe(dest)
    expected = sum(float(c["end"]) - float(c["start"]) for c in clips)
    fails = []
    if not info.get("ok"):
        fails.append("ffprobe unreadable")
    if info.get("duration", 0) <= 0:
        fails.append("zero duration")
    if abs(info.get("duration", 0) - expected) > 1.5:
        fails.append(f"duration {info.get('duration')} vs {expected}")
    if plan.get("voice") and not info.get("audio"):
        fails.append("audio missing")
    print(f"[adaptive-render] order: {[u['reason'] for u in used]}")
    print(f"[adaptive-render] qc: {info} fails={fails}")
    return {
        "ok": not fails,
        "path": dest,
        "order": used,
        "probe": info,
        "fails": fails,
        "upload": False,
    }


def _fail(reason: str) -> dict:
    print(f"[adaptive-render] failed: {reason}")
    print("[adaptive-render] upload: never")
    return {"ok": False, "reason": reason, "upload": False, "order": []}
