"""python3 tests/test_approval_usage.py"""
import os, sys, tempfile, importlib, types, dataclasses
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh(tmp):
    os.environ["VYRO_REVIEW_DIR"] = os.path.join(tmp, "review")
    os.environ["VYRO_USAGE_LOG"] = os.path.join(tmp, "usage_log.json")
    for m in ("approval", "usage_guard"):
        sys.modules.pop(m, None)
    return importlib.import_module("approval"), importlib.import_module("usage_guard")


@dataclasses.dataclass
class C:
    campaign_id: str = "c1"
    name: str = "Scrub or Die"
    requirements_text: str = "r"


def test_approval_roundtrip_and_expiry():
    tmp = tempfile.mkdtemp()
    ap, _ = _fresh(tmp)
    v = os.path.join(tmp, "s.mp4"); open(v, "wb").write(b"x" * 100)
    meta = types.SimpleNamespace(title="T", description="D", tags=["a"])
    assert not ap.has_pending()
    ap.save_pending("whop", C(), v, meta, [{"clip_id": "1"}])
    assert ap.has_pending() and ap.load_pending()["campaign"]["name"] == "Scrub or Die"
    assert ap.pending_age_hours() < 0.1
    ap.clear_pending()
    assert not ap.has_pending()
    os.environ["VYRO_PUBLISH_PENDING"] = "approve"; assert ap.command() == "approve"
    os.environ["VYRO_PUBLISH_PENDING"] = "none"; assert ap.command() == ""
    os.environ["VYRO_REQUIRE_APPROVAL"] = "0"; assert not ap.required()
    os.environ["VYRO_REQUIRE_APPROVAL"] = "1"; assert ap.required()


def test_usage_guard_limits():
    tmp = tempfile.mkdtemp()
    _, ug = _fresh(tmp)
    os.environ["ELEVENLABS_DAILY_CHAR_LIMIT"] = "300"; os.environ["ELEVENLABS_MAX_CALLS_PER_RUN"] = "2"
    assert ug.check_tts(100)[0]
    ug.record_tts(100)
    assert ug.check_tts(150)[0]
    ug.record_tts(150)
    assert not ug.check_tts(10)[0]            # per-run cap (2 calls)
    _, ug = _fresh(tmp)                       # new run, same day: daily chars 250/300
    assert not ug.check_tts(100)[0]
    assert ug.check_tts(40)[0]
    os.environ["ELEVENLABS_MAX_CHARS_PER_CALL"] = "50"
    assert not ug.check_tts(60)[0]





def test_one_video_per_day():
    tmp = tempfile.mkdtemp()
    _, ug = _fresh(tmp)
    os.environ.pop("VYRO_MAX_VIDEOS_PER_DAY", None)
    assert ug.max_videos_per_day() == 1 and ug.published_today() == 0
    ug.record_publish()
    assert ug.published_today() >= ug.max_videos_per_day()


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f(); print("PASS", n)
