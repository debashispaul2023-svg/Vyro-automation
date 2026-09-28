"""Vertical short renderer. 9:16 with blurred fill + animated Vyro corner logo."""

from __future__ import annotations

import os
import subprocess


class RenderError(Exception):
    pass


def _find_logo() -> str:
    for name in ("logo.png", "logo.jpg", "vyro-logo-canva-2.png"):
        if os.path.isfile(name):
            return name
    return ""


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
    has_audio = False
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0", source_path,
            ],
            capture_output=True, text=True, timeout=20,
        )
        has_audio = "audio" in (probe.stdout or "").lower()
    except Exception:
        has_audio = False

    logo = _find_logo()
    graph = (
        "[0:v]split[fg][bg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=18:8,eq=brightness=-0.05[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        "unsharp=5:5:1.2:5:5:0.0,eq=contrast=1.07:saturation=1.10:brightness=0.01[base]"
    )
    extra_in: list[str] = []
    if logo:
        extra_in = ["-i", logo]
        graph += (
            ";[1:v]format=rgba,scale=128:128:force_original_aspect_ratio=decrease,"
            "fade=t=in:st=0.15:d=0.45:alpha=1[lg];"
            "[base][lg]overlay=W-w-28:36[vout]"
        )
        print(f"[render] animated logo overlay from {logo}")
    else:
        graph += "[vout]"
        print("[render] no logo.png in repo root — skip watermark")

    audio_in = [] if has_audio else ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
    audio_src = "0:a?" if has_audio else f"{2 if logo else 1}:a"
    audio_map = ["-map", "0:a?", "-shortest"] if has_audio else ["-map", audio_src, "-shortest"]
    ff = [
        "ffmpeg", "-y", "-i", source_path, *extra_in, *audio_in,
        "-filter_complex", graph,
        "-map", "[vout]", *audio_map,
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        output_path,
    ]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=240)
    except Exception:
        ff = [
            "ffmpeg", "-y", "-i", source_path, *audio_in,
            "-vf",
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
            "-map", "0:v:0", *(["-map", "0:a:0"] if has_audio else ["-map", "1:a", "-shortest"]),
            "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
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
