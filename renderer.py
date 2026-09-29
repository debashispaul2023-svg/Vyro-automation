"""Vertical short renderer. 9:16 blur-fit + pulsing Vyro corner logo."""

from __future__ import annotations

import os
import subprocess

MIN_SECONDS = 16.0
MAX_SECONDS = 30.0


class RenderError(Exception):
    pass


def _find_logo() -> str:
    for name in ("logo.png", "logo.jpg", "logo-hud.jpg", "vyro-logo-canva-2.png"):
        if os.path.isfile(name):
            return name
    return ""


def _probe_dur(path: str) -> float:
    try:
        p = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=20,
        )
        return float((p.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _loop_to_min(path: str, min_s: float = MIN_SECONDS) -> str:
    d = _probe_dur(path)
    if d <= 0 or d >= min_s:
        return path
    loops = max(1, int(min_s / max(d, 0.1)) + 1)
    os.makedirs("work", exist_ok=True)
    out = "work/looped_src.mp4"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-stream_loop", str(loops), "-i", path,
                "-t", f"{min_s:.2f}",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", out,
            ],
            check=True, capture_output=True, timeout=120,
        )
    except Exception as exc:
        print(f"[render] loop failed ({exc}) — using original {d:.1f}s")
        return path
    if os.path.isfile(out) and os.path.getsize(out) > 1000:
        print(f"[render] looped {d:.1f}s -> {min_s:.0f}s so Short stays over 15s")
        return out
    return path


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
    source_path = _loop_to_min(source_path, MIN_SECONDS)
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
        "crop=1080:1920,boxblur=20:8,eq=brightness=-0.06[bg];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,"
        "unsharp=5:5:1.35:5:5:0.0,eq=contrast=1.09:saturation=1.12:brightness=0.015[base]"
    )
    extra_in: list[str] = []
    if logo:
        extra_in = ["-i", logo]
        graph += (
            ";[1:v]format=rgba,scale=176:176:force_original_aspect_ratio=decrease,"
            "zoompan=z='1.04+0.04*sin(2*PI*on/45)':d=1:s=176x176:fps=30,"
            "fade=t=in:st=0.12:d=0.35:alpha=1[lg];"
            "[base][lg]overlay=W-w-22:28[vout]"
        )
        print(f"[render] pulsing logo overlay from {logo}")
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
            "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,"
            "unsharp=5:5:1.2:5:5:0.0",
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
    out_d = _probe_dur(output_path)
    print(f"[render] 1080x1920 blur-fit — {out_d:.1f}s")
    if 0 < out_d < MIN_SECONDS:
        output_path = _loop_to_min(output_path, MIN_SECONDS)
    return output_path
