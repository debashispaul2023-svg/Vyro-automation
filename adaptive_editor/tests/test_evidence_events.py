"""Evidence-based event tests. Filename cannot create an event."""
from adaptive_editor.event_detector import classify_events, event_from_footage
from adaptive_editor.roadmap import build
from adaptive_editor.schemas import Shot
from adaptive_editor.hook_engine import score


def test_filename_and_motion_are_not_enough():
    event, ok = event_from_footage("roll.mp4", {"sampled": True, "static": False, "visual_signal": "motion_only", "motion": 0.3})
    assert event == "UNKNOWN" and ok is False
    event, ok = event_from_footage("roll.mp4", {"sampled": True, "visual_signal": "scene_change", "scene_change_score": 0.4})
    assert event == "UNKNOWN" and ok is False


def test_vision_confirms_events():
    vision = {"events": [
        {"type": "ROLL", "start": 1, "end": 2, "confidence": 0.9, "evidence": ["rolling state visible"]},
        {"type": "REWARD", "start": 3, "end": 4, "confidence": 0.86, "evidence": ["reward presented"]},
    ]}
    rows = classify_events({"motion": 0.2}, vision, "clip.mp4")
    assert [r["type"] for r in rows] == ["ROLL", "REWARD"]
    weak = classify_events({}, {"events": [{"type": "CHARACTER_REVEAL", "confidence": 0.4, "evidence": ["guess"]}]})
    assert weak == []


def test_memory_cannot_insert_missing_event():
    shots = [score(Shot("a.mp4", 0, 1, "ROLL", reason="ROLL", supported=True))]
    story = build(shots, pattern_id="tease_roll_reveal")
    assert all(step["reason"] != "CHARACTER_REVEAL" for step in story.roadmap)


def test_bad_vision_falls_back():
    assert classify_events({"sampled": True, "static": False}, {"events": "bad"}) == []
