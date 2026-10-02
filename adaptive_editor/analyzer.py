"""Probe clips and sample a few frames. Do not send every frame."""
from __future__ import annotations

import os
import subprocess


def probe(path: str) -> dict:
    meta = {"path": path, "duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "audio": False}
    if not path or not os.path.isfile(path):
        return meta
    try:
        p = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height,r_frame_rate",
                "-of", "json", path,
            ],
            capture_output=True, text=True, timeout=20,
        )
        import json
        data = json.loads(p.stdout or "{}")
        meta["duration"] = float((data.get("format") or {}).get("duration") or 0)
        for stream in data.get("streams") or []:
            if stream.get("codec_type") == "video" and not meta["width"]:
                meta["width"] = int(stream.get("width") or 0)
                meta["height"] = int(stream.get("height") or 0)
                rate = str(stream.get("r_frame_rate") or "0/1")
                if "/" in rate:
                    a, b = rate.split("/", 1)
                    meta["fps"] = round(float(a) / max(float(b), 1), 2)
            if stream.get("codec_type") == "audio":
                meta["audio"] = True
    except Exception as exc:
        print(f"[adaptive] probe failed {path}: {exc}")
    return meta


def sample_times(duration: float) -> list[float]:
    if duration <= 0.4:
        return []
    times = [0.2, duration * 0.5, max(0.2, duration - 0.3)]
    if duration > 4:
        times.append(duration * 0.25)
    out = []
    for t in times:
        t = round(min(max(0.1, t), duration - 0.05), 2)
        if t not in out:
            out.append(t)
    return out[:5]


def grab(path: str, t: float, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1", dest],
            check=True, capture_output=True, timeout=20,
        )
        return os.path.isfile(dest) and os.path.getsize(dest) > 200
    except Exception:
        return False


_SIGNAL_CACHE: dict[str, dict] = {}


def frame_signal(path: str, duration: float = 0.0) -> dict:
    """Sample two frames. Motion is the only footage signal. No event is inferred here."""
    if path in _SIGNAL_CACHE:
        return _SIGNAL_CACHE[path]
    signal = {"motion": 0.0, "scene_change": False, "static": True, "sampled": False}
    if not path or not os.path.isfile(path):
        _SIGNAL_CACHE[path] = signal
        return signal
    if duration <= 0:
        duration = probe(path)["duration"]
    times = sample_times(duration) or [0.2]
    frames = []
    for t in times[:2]:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
             "-vf", "scale=32:18,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=20,
        )
        if raw.returncode == 0 and raw.stdout:
            frames.append(raw.stdout[: 32 * 18])
    if len(frames) >= 2 and len(frames[0]) == len(frames[1]) and frames[0]:
        diff = sum(abs(a - b) for a, b in zip(frames[0], frames[1])) / len(frames[0])
        signal["motion"] = round(diff / 255.0, 4)
        signal["scene_change"] = signal["motion"] >= 0.08
        signal["static"] = signal["motion"] < 0.02
        signal["sampled"] = True
    elif frames:
        signal["sampled"] = True
        signal["static"] = True
    _SIGNAL_CACHE[path] = signal
    return signal
