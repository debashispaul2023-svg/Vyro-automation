"""Stops ElevenLabs credits from being burned by retries or repeated runs.
Limits (env, with defaults):  ELEVENLABS_MAX_CALLS_PER_RUN=4   ELEVENLABS_DAILY_CHAR_LIMIT=2500   ELEVENLABS_MAX_CHARS_PER_CALL=700
Counts only SUCCESSFUL generations (failed HTTP calls are not billed). State: usage_log.json (committed by the workflow)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

LOG = Path(os.environ.get("VYRO_USAGE_LOG", "usage_log.json"))
_run_calls = 0


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _load() -> dict:
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def check_tts(chars: int) -> tuple[bool, str]:
    if chars > _int("ELEVENLABS_MAX_CHARS_PER_CALL", 700):
        return False, f"{chars} chars > per-call limit {_int('ELEVENLABS_MAX_CHARS_PER_CALL', 700)}"
    if _run_calls >= _int("ELEVENLABS_MAX_CALLS_PER_RUN", 4):
        return False, f"per-run limit reached ({_run_calls} generations)"
    used = int((_load().get(_today()) or {}).get("elevenlabs_chars", 0))
    limit = _int("ELEVENLABS_DAILY_CHAR_LIMIT", 2500)
    if used + chars > limit:
        return False, f"daily limit: used {used} + {chars} > {limit} chars"
    return True, f"ok: this call ~{chars} chars, today {used}/{limit}"


def record_tts(chars: int) -> None:
    global _run_calls
    _run_calls += 1
    data = _load()
    day = data.setdefault(_today(), {})
    day["elevenlabs_chars"] = int(day.get("elevenlabs_chars", 0)) + chars
    day["elevenlabs_calls"] = int(day.get("elevenlabs_calls", 0)) + 1
    for k in sorted(data)[:-14]:      # keep 14 days
        data.pop(k, None)
    try:
        LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def max_videos_per_day() -> int:
    return _int("VYRO_MAX_VIDEOS_PER_DAY", 1)


def published_today() -> int:
    return int((_load().get(_today()) or {}).get("videos_published", 0))


def record_publish() -> None:
    data = _load()
    day = data.setdefault(_today(), {})
    day["videos_published"] = int(day.get("videos_published", 0)) + 1
    try:
        LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
