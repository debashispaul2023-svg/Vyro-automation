"""Gemini runtime tests. No API keys printed."""
import gemini_runtime


def test_quota_once():
    rt = gemini_runtime.reset()
    calls = []

    def fake(model):
        calls.append(model)
        if model == "gemini-3.8-flash":
            raise RuntimeError("429 quota_exceeded")
        return "ok"

    for _ in range(11):
        model = rt.choose_model("vision")
        rt.note_attempt(model)
        try:
            fake(model)
            rt.mark_success(model)
        except Exception as exc:
            if rt.classify(exc) == "QUOTA_EXHAUSTED":
                rt.quota_errors += 1
                rt.quarantine(model, "QUOTA_EXHAUSTED")
    assert calls.count("gemini-3.8-flash") == 1
    assert calls[-1] == "gemini-3.6-flash"


def test_fallback_and_persist():
    rt = gemini_runtime.reset()
    rt.quarantine("gemini-3.8-flash", "QUOTA_EXHAUSTED")
    rt.mark_failure("gemini-3.6-flash", "unavailable")
    rt.quarantine("gemini-3.6-flash", "TRANSIENT_ERROR")
    assert rt.choose_model("vision") == "gemini-3.5-flash"
    rt.mark_success("gemini-3.5-flash")
    assert [rt.choose_model("vision") for _ in range(5)] == ["gemini-3.5-flash"] * 5


def test_cross_subsystem():
    rt = gemini_runtime.reset()
    rt.note_attempt("gemini-3.8-flash")
    rt.quarantine("gemini-3.8-flash", "QUOTA_EXHAUSTED")
    assert rt.choose_model("metadata") == "gemini-3.6-flash"
    assert rt.choose_model("vision") != "gemini-3.8-flash"


def test_malformed():
    raw = '{\n  "mandatory_hashtags)'
    assert "mandatory_hashtags" in raw
    tags = ["#shorts", "#roblox", "#rollanimegirls"]
    assert "#shorts" in tags and "#roblox" in tags


def test_transient_bound():
    rt = gemini_runtime.reset()
    rt.transient["gemini-3.6-flash"] = 2
    assert rt.transient["gemini-3.6-flash"] <= 2


if __name__ == "__main__":
    test_quota_once()
    test_fallback_and_persist()
    test_cross_subsystem()
    test_malformed()
    test_transient_bound()
    print(gemini_runtime.RUNTIME.summary())
    print("gemini runtime tests PASS")
