"""Run: python3 -m pytest tests/test_studio_editor.py   (or: python3 tests/test_studio_editor.py)"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import studio_editor as S  # noqa: E402


def _synthetic(path, seconds=20, with_audio=True):
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=size=1280x720:rate=30:duration={seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"] + (["-c:a", "aac"] if with_audio else []) + [path]
    subprocess.run(cmd, check=True)


def _fake_voice(seconds):
    def provider(text, dest):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=200:duration={seconds}", dest], check=True)
        return dest, []
    return provider


def test_segments_fill_target_exactly():
    for src, target in [(64, 10.3), (20, 12.0), (12, 9.0), (30, 15.0)]:
        segs = S.plan_segments(src, target, [1.0] * int(src))
        assert segs, (src, target)
        assert abs(S.segs_output_duration(segs) - target) < 0.05
        assert all(0 <= a < b <= src for a, b, _ in segs)
        assert all(segs[i][0] >= segs[i - 1][0] for i in range(1, len(segs)))


def test_source_too_short_is_refused_not_frozen():
    assert S.plan_segments(2.0, 12.0) == []


def test_atempo_chain():
    assert S.atempo_chain(1.0) == ""
    assert S.atempo_chain(3.0).count("atempo") == 2


def test_profiles_and_keywords():
    name, prof = S.find_profile("Scrub or Die")
    assert name == "Scrub or Die"
    lines = S.pick_script(prof, name, 0)
    assert "Game is called Scrub or Die." in S.strip_markup(lines)
    assert "roll anime" not in S.strip_markup(lines).lower()
    gname, gprof = S.find_profile("Some New Game")
    glines = S.pick_script(gprof, gname, 0)
    assert "Some New Game" in S.strip_markup(glines) and "Anime" not in S.strip_markup(glines)


def test_ass_one_word_per_line_red_keywords():
    words = [{"text": t, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, t in enumerate("Scrub or Die is out now".split())]
    p = os.path.join(tempfile.mkdtemp(), "c.ass")
    n = S.write_ass(p, words, {"scrub", "die"}, 1.0)
    txt = open(p).read()
    assert n == 6 and txt.count("Dialogue:") == 6
    assert txt.count("&H004B4BFF&") == 2


def test_full_render_voice_covers_video():
    d = tempfile.mkdtemp()
    for with_audio in (True, False):
        src = os.path.join(d, f"src{with_audio}.mp4")
        _synthetic(src, 20, with_audio)
        out = os.path.join(d, f"out{with_audio}.mp4")
        r = S.render_short(src, out, "Scrub or Die", "", 0, voice_provider=_fake_voice(9.0))
        assert r["ok"], r
        info = S.probe(out)
        assert (info["width"], info["height"]) == (1080, 1920) and info["has_audio"]
        assert abs(info["duration"] - (9.0 / S.VO_TEMPO + S.TAIL)) < 0.6
        assert r["voice_coverage"] >= 0.9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
