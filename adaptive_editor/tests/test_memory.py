"""Memory tests. Temp dir only. No upload."""
import os

from adaptive_editor.memory.feedback import record_feedback
from adaptive_editor.memory.footage_memory import already_indexed, save_experience
from adaptive_editor.memory.retrieval import advise
from adaptive_editor.memory.schemas import memory_record
from adaptive_editor.memory.scorer import score_pattern
from adaptive_editor.memory.storage import append_jsonl
from adaptive_editor.schemas import Shot
from adaptive_editor.hook_engine import score


def _shots():
    return [
        score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL")),
        score(Shot("reveal.mp4", 0, 2, "CHARACTER_REVEAL", reason="CHARACTER_REVEAL")),
    ]


def test_store_and_skip_duplicate(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    row = memory_record("a", "old", ["ROLL"], [], {}, "generic_roll", 3, True, file_hash="abc", result="successful")
    weak = memory_record("b", "old", ["ROLL"], [], {}, "generic_roll", 3, True, file_hash="def", result="weak")
    assert append_jsonl("footage_memory.jsonl", row)
    assert append_jsonl("footage_memory.jsonl", weak)
    assert already_indexed("abc")
    assert not already_indexed("new")


def test_compatible_and_reject():
    events = {"ROLL", "CHARACTER_REVEAL"}
    good, reason = score_pattern(events, {"pattern_id": "tease_roll_reveal", "required_events": ["ROLL", "CHARACTER_REVEAL"], "success_rate": 0.75}, [])
    bad, why = score_pattern(events, {"pattern_id": "fail_attempt", "required_events": ["FAIL"], "success_rate": 0.5}, [])
    assert good > 0 and "compatible" in reason
    assert bad < 0 and "FAIL" in why


def test_overused_penalty():
    recent = ["tease_roll_reveal"] * 5
    score, reason = score_pattern(
        {"ROLL", "CHARACTER_REVEAL"},
        {"pattern_id": "tease_roll_reveal", "required_events": ["ROLL", "CHARACTER_REVEAL"], "success_rate": 0.75},
        recent,
    )
    assert "penalty 0.10" in reason
    assert score < 0.9


def test_feedback_nulls(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    row = record_feedback("v1", "tease_roll_reveal", "unknown", {"views": 10})
    assert row["performance"]["views"] == 10
    assert row["performance"]["retention"] is None


def test_corrupt_does_not_crash(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    os.makedirs(tmp, exist_ok=True)
    open(os.path.join(tmp, "footage_memory.jsonl"), "w").write("{bad\n")
    advice = advise(_shots())
    assert "selected" in advice


def test_save_experience(tmp):
    os.environ["ADAPTIVE_MEMORY_DIR"] = tmp
    class Story:
        hook = {"reason": "ROLL"}
        structure = "generic_roll"
        estimated_duration = 4
    assert save_experience(_shots(), Story(), {"ok": True})
