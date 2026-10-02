"""Caption timeline tests."""
from adaptive_editor.caption_planner import captions
from adaptive_editor.render.captions import segments


def _overlap(rows):
    return any(rows[i]["start"] < rows[i - 1]["end"] for i in range(1, len(rows)))


def test_sequential_no_overlap():
    two = captions([{"start": 0, "end": 1.6, "reason": "ROLL"}, {"start": 0, "end": 1.6, "reason": "REWARD"}])
    assert not _overlap(two)
    assert two[1]["start"] >= two[0]["end"]
    three = captions([{"start": 0, "end": 1, "reason": "A"}, {"start": 0, "end": 1, "reason": "B"}, {"start": 0, "end": 1, "reason": "C"}])
    assert not _overlap(three)


def test_end_clamp_and_reject():
    rows = segments({"duration_target": 1.6, "captions": [{"start": 1.2, "end": 3.0, "text": "END"}]})
    assert rows[0]["end"] <= 1.6
    assert segments({"captions": [{"start": 1, "end": 1, "text": "bad"}, {"start": -1, "end": 0.2, "text": "neg"}]}) == []


def test_simultaneous_only_when_marked():
    rows = segments({"captions": [
        {"start": 0.0, "end": 1.0, "text": "A"},
        {"start": 0.2, "end": 1.2, "text": "B", "simultaneous": True},
    ]})
    assert rows[1]["start"] < rows[0]["end"]
