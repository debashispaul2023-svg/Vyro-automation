"""Narration before mix. Uses project TTS only when a key exists. Tests use a fixture."""
from __future__ import annotations

import os

from .ffmpeg_adapter import tone


def narrate(text: str, dest: str, seconds: float, provider=None) -> dict:
    text = " ".join((text or "").split())
    if not text:
        return {"ok": False, "path": "", "provider": "none", "fallback": False}
    fn = provider or _project_tts
    try:
        path = fn(text, dest)
        if path and os.path.isfile(path) and os.path.getsize(path) > 200:
            print(f"[adaptive-tts] audio {path}")
            return {"ok": True, "path": path, "provider": getattr(fn, "__name__", "custom"), "fallback": False}
    except Exception as exc:
        print(f"[adaptive-tts] failed: {exc}")
    bed = dest if dest.endswith(".m4a") else dest + ".m4a"
    if tone(bed, seconds):
        print("[adaptive-tts] fallback silent bed")
        return {"ok": True, "path": bed, "provider": "silent-bed", "fallback": True}
    return {"ok": False, "path": "", "provider": "none", "fallback": True}


def _project_tts(text: str, dest: str) -> str:
    if not (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEYS")):
        raise RuntimeError("no TTS key")
    from tts_engine import generate_voiceover
    path, _words = generate_voiceover(text, dest)
    if not path:
        raise RuntimeError("TTS returned no file")
    return path


def fixture_tts(text: str, dest: str) -> str:
    """Deterministic local audio. Not a real voice provider."""
    from .ffmpeg_adapter import tone
    if not tone(dest, max(1.0, len(text) / 12)):
        raise RuntimeError("fixture audio failed")
    return dest
