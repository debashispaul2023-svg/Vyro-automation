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
    reenc = os.path.join(folder, "a_reenc.mp4")
    subprocess.run(["ffmpeg", "-y", "-i", a, "-c:v", "libx264", "-crf", "30", reenc], check=True, capture_output=True)
    ha, hb = fu.file_hash(a), fu.file_hash(b)
    assert ha == fu.file_hash(copy) and ha != hb
    fa, fb, fr = fu.fingerprint(a), fu.fingerprint(b), fu.fingerprint(reenc)
    fu.record({"source_file_id": "A", "source_hash": ha, "fingerprint": fa, "start": 0, "end": 2, "video_id": "v1"})
    assert fu.reject({"clip_id": "A", "source_hash": ha, "start": 0, "end": 2}) == "CONTENT_DUPLICATE"
    assert fu.reject({"clip_id": "A", "start": 0, "end": 2}) == "COOLDOWN"
    assert fu.reject({"clip_id": "copy", "source_hash": ha, "start": 0.2, "end": 1.8, "title": "new title"}) == "CONTENT_DUPLICATE"
    assert fu.reject({"clip_id": "re", "fingerprint": fr, "start": 0, "end": 2}) == "VISUAL_DUPLICATE"
    assert fu.reject({"source_hash": ha, "start": 1.0, "end": 2.0}) == "CONTENT_DUPLICATE"
    assert fu.reject({"clip_id": "B", "source_hash": hb, "fingerprint": fb, "start": 0, "end": 2}) == ""
    assert fu.similar(fa, fb) < 0.98
    assert not fu.allow_reuse()
    for i in range(12):
        fu.record({"source_file_id": f"other{i}", "source_hash": f"other{i}", "start": 0, "end": 1})
    assert fu.reject({"clip_id": "A-again", "source_hash": ha, "title": "new title #shorts #roblox", "start": 0, "end": 2}) == "CONTENT_DUPLICATE"
    tags = "#shorts #roblox #rollanimegirls"
    assert "#shorts" in tags and "#roblox" in tags
    print("footage usage tests PASS")


if __name__ == "__main__":
    test_all()
