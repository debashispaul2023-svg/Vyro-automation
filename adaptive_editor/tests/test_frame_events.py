"""Frame evidence tests. Filename cannot create an event."""
from adaptive_editor.event_detector import event_from_footage
from adaptive_editor.memory.retrieval import advise
from adaptive_editor.roadmap import build
from adaptive_editor.schemas import Shot
from adaptive_editor.hook_engine import score


def test_static_roll_name_is_not_roll():
    event, ok = event_from_footage("roll.mp4", {"sampled": True, "static": True, "visual_signal": "static", "motion": 0.0})
    assert event != "ROLL" and ok is False


def test_motion_only_roll_name_is_not_roll():
    event, ok = event_from_footage("roll.mp4", {
        "sampled": True, "static": False, "visual_signal": "motion_only", "motion": 0.2, "scene_change_score": 0.05,
    })
    assert event == "UNKNOWN" and ok is False


def test_scene_change_is_recorded_not_guessed():
    event, ok = event_from_footage("clip.mp4", {
        "sampled": True, "static": False, "visual_signal": "scene_change", "scene_change_score": 0.22, "frame_samples": [0.2, 1.0],
    })
    assert event == "UNKNOWN" and ok is False


def test_reveal_pattern_rejected_without_evidence():
    shots = [score(Shot("a.mp4", 0, 1, "UNKNOWN", reason="UNKNOWN", supported=False))]
    advice = advise([s for s in shots if s.supported])
    assert advice.get("selected", "") != "tease_roll_reveal"
    story = build([score(Shot("a.mp4", 0, 1, "ROLL", reason="ROLL", supported=True))], pattern_id="tease_roll_reveal")
    assert story.structure != "tease_roll_reveal"
