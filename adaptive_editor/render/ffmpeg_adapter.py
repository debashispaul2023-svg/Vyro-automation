"""Isolated ffmpeg calls. Does not import production renderer."""
from __future__ import annotations

import json
import os
import subprocess


def probe(path: str) -> dict:
    info = {"ok": False, "duration": 0.0, "video": False, "audio": False}
    if not path or not os.path.isfile(path):
        return info
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path],
            capture_output=True, text=True, timeout=20,
        )
        data = json.loads(p.stdout or "{}")
        info["duration"] = float((data.get("format") or {}).get("duration") or 0)
        kinds = {s.get("codec_type") for s in data.get("streams") or []}
        info["video"] = "video" in kinds
        info["audio"] = "audio" in kinds
        info["ok"] = info["video"] and info["duration"] > 0
    except Exception as exc:
        info["error"] = str(exc)[:160]
    return info


def trim(src: str, start: float, end: float, dest: str) -> bool:
    length = max(0.3, end - start)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
                "-r", "24", "-an", dest,
            ],
            check=True, capture_output=True, timeout=60,
        )
        return os.path.isfile(dest) and os.path.getsize(dest) > 500
    except Exception:
        return False


def concat(parts: list[str], dest: str, audio: str = "") -> bool:
    lst = dest + ".txt"
    with open(lst, "w", encoding="utf-8") as f:
        for part in parts:
            f.write(f"file '{os.path.abspath(part)}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst]
    if audio and os.path.isfile(audio):
        cmd += ["-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", dest]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=90)
        return os.path.isfile(dest) and os.path.getsize(dest) > 500
    except Exception:
        return False


def tone(dest: str, seconds: float) -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", f"{max(1.0, seconds):.2f}", dest],
            check=True, capture_output=True, timeout=30,
        )
        return os.path.isfile(dest)
    except Exception:
        return False
