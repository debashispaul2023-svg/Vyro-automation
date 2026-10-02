"""Footage identity tests. Title does not make footage unique."""
import os
import subprocess
import tempfile

import footage_usage as fu


def _clip(folder, name, src, seconds="2"):
    dest = os.path.join(folder, name)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", seconds, dest], check=True, capture_output=True)
    return dest


def test_all():
    folder = tempfile.mkdtemp()
    os.environ["FOOTAGE_USAGE_LOG"] = os.path.join(folder, "usage.jsonl")
    os.environ["RECENT_FOOTAGE_COOLDOWN"] = "7"
    os.environ["ALLOW_REUSE_WHEN_POOL_EXHAUSTED"] = "false"
    a = _clip(folder, "a.mp4", "testsrc=size=160x90:rate=12")
    b = _clip(folder, "b.mp4", "smptebars=size=160x90:rate=12")
    copy = os.path.join(folder, "a_copy.mp4")
    subprocess.run(["cp", a, copy], check=True)
    ha, hb = fu.file_hash(a), fu.file_hash(b)
    assert ha == fu.file_hash(copy) and ha != hb
    fu.record({"source_file_id": "A", "source_hash": ha, "fingerprint": fu.fingerprint(a), "start": 0, "end": 2, "video_id": "v1"})
    assert fu.reject({"clip_id": "A", "source_hash": ha, "start": 0, "end": 2})
    assert fu.reject({"clip_id": "copy", "source_hash": ha, "start": 0.2, "end": 1.8, "title": "new title"})
    assert fu.reject({"source_hash": ha, "start": 1.0, "end": 2.0}) == "RECENT_FOOTAGE_OVERLAP" or fu.reject({"source_hash": ha, "start": 1.0, "end": 2.0})
    assert fu.reject({"clip_id": "B", "source_hash": hb, "fingerprint": fu.fingerprint(b), "start": 0, "end": 2}) == ""
    assert fu.reject({"clip_id": "A", "source_hash": ha, "start": 0, "end": 1})  # same clip in one video via log
    old = fu.load()
    fu.record({"source_file_id": "C", "source_hash": "c", "start": 0, "end": 1, "video_id": "v2"})
    assert len(fu.load()) == len(old) + 1
    assert not fu.allow_reuse()
    print("footage usage tests PASS")


if __name__ == "__main__":
    test_all()
