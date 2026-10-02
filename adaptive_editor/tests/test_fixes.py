"""Fix tests. No upload."""
import os
import subprocess
import tempfile

from adaptive_editor.engine import run_adaptive
from adaptive_editor.event_detector import event_from_footage
from adaptive_editor.hook_engine import score
from adaptive_editor.memory.feedback import record_feedback
from adaptive_editor.memory.footage_memory import save_experience
from adaptive_editor.memory.retrieval import advise
from adaptive_editor.memory.scorer import score_pattern
from adaptive_editor.providers.chatgpt_review import accept_review
from adaptive_editor.roadmap import build
from adaptive_editor.schemas import Shot


def test_filename_not_trusted():
    folder = tempfile.mkdtemp()
    dest = os.path.join(folder, "roll_dice.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2", dest],
        check=True, capture_output=True, timeout=30,
    )
    event, supported = event_from_footage("roll_dice.mp4", {"sampled": True, "static": True, "motion": 0.0, "scene_change": False})
    assert event == "UNKNOWN" and supported is False
    gated = run_adaptive([dest], out_dir=os.path.join(folder, "out"))
    assert gated.get("reason") == "production gate closed"


def test_memory_changes_steps():
    roll = score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL", supported=True))
    reveal = score(Shot("reveal.mp4", 1, 3, "CHARACTER_REVEAL", reason="CHARACTER_REVEAL", supported=True))
    story = build([roll, reveal], pattern_id="tease_roll_reveal")
    reasons = [step["reason"] for step in story.roadmap]
    assert story.structure == "tease_roll_reveal"
    assert reasons[0] == "CHARACTER_REVEAL"
    assert "ROLL" in reasons


def test_incompatible_and_no_substitute():
    roll = score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL", supported=True))
    story = build([roll], pattern_id="fail_attempt")
    assert story.structure != "fail_attempt"
    assert all(step["reason"] != "CHARACTER_REVEAL" for step in story.roadmap)


def test_hash_and_duplicate(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    os.makedirs(tmp, exist_ok=True)
    dest = os.path.join(tmp, "roll.mp4")
    open(dest, "wb").write(b"footage-bytes")
    shot = score(Shot(dest, 0, 2, "ROLL", reason="ROLL", supported=True))

    class Story:
        hook = {"reason": "ROLL"}
        structure = "generic_roll"
        estimated_duration = 2

    assert save_experience([shot], Story(), {"ok": True})
    assert save_experience([shot], Story(), {"ok": True}) is False


def test_seed_unknown_and_feedback(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    unknown, _ = score_pattern({"ROLL"}, {"pattern_id": "generic_roll", "required_events": ["ROLL"], "success_rate": 0.9, "result": "unknown"}, [])
    real, _ = score_pattern({"ROLL"}, {"pattern_id": "generic_roll", "required_events": ["ROLL"], "success_rate": 0.9, "result": "successful"}, [])
    assert unknown < real
    record_feedback("v", "generic_roll", "weak", {})
    weak, reason = score_pattern({"ROLL"}, {"pattern_id": "generic_roll", "required_events": ["ROLL"], "success_rate": 0.2, "result": "weak"}, [])
    assert "weak" in reason
    assert weak < real


def test_similar_memory_and_repetition(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    from adaptive_editor.memory.storage import append_jsonl
    append_jsonl("footage_memory.jsonl", {"structure": "generic_roll", "events": ["ROLL"], "result": "successful"})
    shots = [score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL", supported=True))]
    advice = advise(shots)
    assert advice["selected"] == "generic_roll"
    for _ in range(5):
        append_jsonl("footage_memory.jsonl", {"structure": "generic_roll", "events": ["ROLL"], "result": "unknown"})
    again = advise(shots)
    assert again["penalty"] == 0.1 or again["selected"] != "generic_roll" or "overused" in again.get("reason", "")


def test_chatgpt_and_corrupt(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    bad = accept_review({"title": "ChatGPT"})
    assert bad["ok"] is False
    good = accept_review({"hook": "reveal", "structure": "generic_roll", "pacing": "fast", "issues": [], "suggestions": []})
    assert good["structured"] is True
    os.makedirs(tmp, exist_ok=True)
    open(os.path.join(tmp, "footage_memory.jsonl"), "w").write("{bad\n")
    advice = advise([score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL", supported=True))])
    assert "selected" in advice
