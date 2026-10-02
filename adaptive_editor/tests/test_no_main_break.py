"""Regression: production modules are not imported by the adaptive package."""


def test_no_main_break():
    import adaptive_editor.engine as engine
    src = open(engine.__file__, encoding="utf-8").read()
    assert "import daily_runner" not in src
    assert "upload_video" not in src
    assert "submit_video_link" not in src
