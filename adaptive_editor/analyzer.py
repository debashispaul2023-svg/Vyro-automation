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
