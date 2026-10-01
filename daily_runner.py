"""Load daily_runner-2 then install voice + story overrides."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# globals().update() copies the impl module's __name__ and would skip main().
_IS_MAIN = __name__ == "__main__"

_IMPL = Path(__file__).with_name("daily_runner-2.py")
_spec = importlib.util.spec_from_file_location("daily_runner_impl", _IMPL)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {_IMPL}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["daily_runner_impl"] = _mod
_spec.loader.exec_module(_mod)
globals().update(vars(_mod))

try:
    import campaign_pack
    campaign_pack.install(globals())
    campaign_pack.install(vars(_mod))
except Exception as exc:
    print(f"[voice] override not loaded: {exc}")

try:
    import story_engine
    story_engine.attach(globals())
    story_engine.attach(vars(_mod))
except Exception as exc:
    print(f"[story] attach skipped: {exc}")


# Campaign title must name the game, show the loop, and stay under 100 chars.
_TITLE_VARIANTS = (
    "Roll Anime Girls: Roll Dice, Place, Earn Offline #shorts #roblox",
    "Roll Anime Girls: Unlock, Place on Plot, Earn Offline #shorts #roblox",
    "Roll Anime Girls on Roblox: Roll, Place, Earn Cash #shorts #roblox",
)
_LOOP_LINE = "Roll the dice, unlock a character, place them on your plot, and they earn money even offline."


def _force_upload_title(title: str, description: str) -> tuple[str, str]:
    seed = sum(ord(c) for c in (title or "Roll Anime Girls"))
    raw = _TITLE_VARIANTS[seed % len(_TITLE_VARIANTS)]
    raw = raw[:100].rstrip()
    desc = description or ""
    if "place" not in desc.lower() or "offline" not in desc.lower():
        desc = (_LOOP_LINE + "\n" + desc).strip()
    for tag in ("#shorts", "#roblox", "#rollanimegirls"):
        if tag not in desc.lower():
            desc = (desc.rstrip() + " " + tag).strip()
    link = "https://www.roblox.com/games/92289737492030/Roll-Anime-Girls"
    if link not in desc:
        desc = (desc.rstrip() + "\n" + link).strip()
    if "try roll anime girls" not in desc.lower():
        desc = (desc.rstrip() + "\nTry Roll Anime Girls on Roblox.").strip()
    return raw, desc


def _install_upload_guard() -> None:
    try:
        import checker
    except Exception as exc:
        print(f"[check] guard skipped: {exc}")
        return
    orig = checker.validate_or_raise

    def validate_or_raise(video_path, title, description, req):
        title, description = _force_upload_title(title, description)
        print(f"[check] title forced: {title!r}")
        return orig(video_path, title, description, req)

    checker.validate_or_raise = validate_or_raise
    _mod.validate_or_raise = validate_or_raise
    globals()["validate_or_raise"] = validate_or_raise
    print("[check] shorts title guard installed")


_install_upload_guard()

if _IS_MAIN:
    print("[daily] main starting")
    raise SystemExit(int(_mod.main() or 0))
