"""Render tests. Sample clips only. No upload."""
import os
import subprocess
import tempfile

from adaptive_editor.render.renderer import render_plan


def _clip(folder, name):
    dest = os.path.join(folder, name)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-t", "3", dest],
        check=True, capture_output=True, timeout=30,
    )
    return dest


def test_render_follows_plan():
    folder = tempfile.mkdtemp()
    roll = _clip(folder, "roll.mp4")
    reward = _clip(folder, "money.mp4")
    plan = {
        "duration_target": 3,
        "clips": [
            {"role": "SETUP", "clip": roll, "start": 0.2, "end": 1.4, "reason": "ROLL"},
            {"role": "PAYOFF", "clip": reward, "start": 0.2, "end": 1.4, "reason": "REWARD"},
        ],
        "voice": [{"text": "The dice roll.", "start": 0.2}],
    }
    dest = os.path.join(folder, "out.mp4")
    result = render_plan(plan, dest, os.path.join(folder, "parts"))
    assert result["ok"], result
    assert [row["reason"] for row in result["order"]] == ["ROLL", "REWARD"]
    assert result["probe"]["video"] and result["probe"]["audio"]
    assert result["upload"] is False


def test_missing_clip_does_not_substitute():
    folder = tempfile.mkdtemp()
    roll = _clip(folder, "roll.mp4")
    plan = {
        "clips": [
            {"role": "SETUP", "clip": roll, "start": 0, "end": 1, "reason": "ROLL"},
            {"role": "PAYOFF", "clip": os.path.join(folder, "missing.mp4"), "start": 0, "end": 1, "reason": "REWARD"},
        ]
    }
    result = render_plan(plan, os.path.join(folder, "out.mp4"))
    assert result["ok"] is False
    assert "missing" in result["reason"]
    assert result["order"] == []
    assert result["upload"] is False
