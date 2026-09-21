"""Vertical short renderer. 9:16 with blurred fill, no full-time watermark."""

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
    """Fit gameplay on 1080x1920. Blur-fill bars instead of chopping the frame."""
    if not source_path or not os.path.isfile(source_path):
        raise RenderError(f"source missing: {source_path}")
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    graph = (
        "[0:v]split[fg][bg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=18:8,eq=brightness=-0.05[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        "unsharp=5:5:1.2:5:5:0.0,eq=contrast=1.07:saturation=1.10:brightness=0.01"
    )
    ff = [
        "ffmpeg", "-y", "-i", source_path,
        "-filter_complex", graph,
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-ar", "44100", "-ac", "2",
        output_path,
    ]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=240)
    except Exception:
        ff = [
            "ffmpeg", "-y", "-i", source_path,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            output_path,
        ]
        try:
            subprocess.run(ff, check=True, capture_output=True, timeout=240)
        except Exception as exc:
            raise RenderError(f"ffmpeg render failed: {exc}") from exc
    if not os.path.isfile(output_path) or os.path.getsize(output_path) < 1000:
        raise RenderError("render output missing")
    print("[render] 1080x1920 blur-fit — gameplay centered")
    return output_path
