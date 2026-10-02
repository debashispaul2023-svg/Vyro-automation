"""Real TTS check. Skips when no key. Never uploads."""
from __future__ import annotations

import os
import subprocess


def check_real_tts(dest: str) -> dict:
    if not (os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVENLABS_API_KEYS")):
        print("REAL TTS: SKIPPED — NO API KEY")
        return {"ok": False, "status": "SKIPPED"}
    from tts_engine import generate_voiceover
    path, _words = generate_voiceover("The dice roll.", dest)
    if not path or not os.path.isfile(path):
        return {"ok": False, "status": "FAIL"}
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path],
        capture_output=True, text=True, timeout=20,
    )
    print(probe.stdout)
    return {"ok": True, "status": "PASS", "path": path}
