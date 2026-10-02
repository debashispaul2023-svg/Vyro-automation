"""Unit checks. No network. No upload."""
from adaptive_editor.event_detector import event_from_name
from adaptive_editor.roadmap import build
from adaptive_editor.schemas import Shot
from adaptive_editor.hook_engine import score
from adaptive_editor.voice_planner import script_for


def test_event_from_name():
    assert event_from_name("rare_character_reveal.mp4") == "RARE_REVEAL"
    assert event_from_name("roll_dice.mp4") == "ROLL"


def test_script_does_not_invent_rare():
    steps = [{"reason": "ROLL"}, {"reason": "REWARD"}]
    text, blocked = script_for(steps)
    assert "rare" not in text.lower()
    assert blocked == []


def test_roadmap_changes_with_footage():
    roll = score(Shot("roll.mp4", 0, 2, "ROLL", reason="ROLL"))
    reveal = score(Shot("rare_reveal.mp4", 1, 3, "RARE_REVEAL", reason="RARE_REVEAL"))
    money = score(Shot("money.mp4", 0, 2, "REWARD", reason="REWARD"))
    a = build([roll, reveal])
    b = build([roll, money])
    assert a.structure != b.structure or a.concept != b.concept
