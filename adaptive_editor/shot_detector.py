"""Split a clip into short shots. Scene detect if ffmpeg can, else even windows."""
from __future__ import annotations

import subprocess

from .analyzer import probe


def detect_shots(path: str) -> list[tuple[float, float]]:
    meta = probe(path)
    dur = meta["duration"]
    if dur < 0.6:
        return []
    cuts = _scene_cuts(path, dur)
    if len(cuts) < 2:
        step = 2.4 if dur > 6 else max(1.2, dur / 2)
        cuts = []
        t = 0.0
        while t < dur - 0.4:
            cuts.append((round(t, 2), round(min(dur, t + step), 2)))
            t += step
    return cuts[:6]


def _scene_cuts(path: str, dur: float) -> list[tuple[float, float]]:
    try:
        p = subprocess.run(
            [
                "ffmpeg", "-i", path, "-filter:v", "select='gt(scene,0.35)',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return []
    times = [0.0]
    for line in (p.stderr or "").splitlines():
        if "pts_time:" not in line:
            continue
        try:
            raw = line.split("pts_time:")[1].split()[0]
            times.append(float(raw))
        except (IndexError, ValueError):
            continue
    times.append(dur)
    times = sorted(set(round(t, 2) for t in times if 0 <= t <= dur))
    shots = []
    for a, b in zip(times, times[1:]):
        if b - a >= 0.6:
            shots.append((a, b))
    return shots
