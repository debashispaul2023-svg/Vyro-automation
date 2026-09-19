"""Vertical short renderer. No full-time watermark or code overlay."""

from __future__ import annotations

import os
import subprocess


class RenderError(Exception):
    pass


def render_short(
    source_path: str,
    output_path: str,
    req=None,
    fallback_caption_text: str | None = None,
) -> str:
    """Scale/pad to 1080x1920. Captions and codes are burned later by daily_runner."""
    if not source_path or not os.path.isfile(source_path):
        raise RenderError(f"source missing: {source_path}")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    ff = [
        "ffmpeg", "-y", "-i", source_path,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,"
        "unsharp=5:5:1.3:5:5:0.0,eq=contrast=1.08:saturation=1.12:brightness=0.02",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        output_path,
    ]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=240)
    except Exception as exc:
        raise RenderError(f"ffmpeg render failed: {exc}") from exc
    if not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
        raise RenderError("render output missing")
    print("[render] vertical 1080x1920 — no full-time text overlay")
    return output_path
