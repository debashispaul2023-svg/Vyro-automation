"""TTS fixture and caption tests. No paid API."""
import os
import subprocess
import tempfile

from adaptive_editor.render.captions import segments, to_srt
from adaptive_editor.render.renderer import render_plan
from adaptive_editor.render.tts import fixture_tts, narrate


def _clip(folder, name):
    dest = os.path.join(folder, name)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-t", "3", dest],
        check=True, capture_output=True, timeout=30,
    )
    return dest


def _fail_tts(text, dest):
    raise RuntimeError("tts down")


def test_tts_and_captions():
    folder = tempfile.mkdtemp()
    spoken = narrate("The dice roll.", os.path.join(folder, "v.m4a"), 2, provider=fixture_tts)
    assert spoken["ok"] and spoken["fallback"] is False
    failed = narrate("The dice roll.", os.path.join(folder, "f.m4a"), 2, provider=_fail_tts)
    assert failed["fallback"] is True and failed["ok"]
    rows = segments({"captions": [{"start": 0, "end": 1.2, "text": "ROLL's \"win\"\nnext"}]})
    assert rows[0]["end"] == 1.2
    srt = to_srt(rows)
    assert "-->" in srt and "'" not in srt and '"' not in srt
    assert segments({"captions": []}) == []
    roll = _clip(folder, "roll.mp4")
    plan = {
        "duration_target": 2,
        "clips": [{"role": "SETUP", "clip": roll, "start": 0.2, "end": 1.6, "reason": "ROLL"}],
        "voice": [{"text": "The dice roll.", "start": 0.2}],
        "captions": [{"start": 0.2, "end": 1.2, "text": "ROLL's win"}],
    }
    dest = os.path.join(folder, "out.mp4")
    result = render_plan(plan, dest, os.path.join(folder, "parts"))
    assert result["ok"], result
    assert result["probe"]["video"] and result["probe"]["audio"]
    assert result["upload"] is False
