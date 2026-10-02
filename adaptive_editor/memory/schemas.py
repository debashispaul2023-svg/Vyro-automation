from __future__ import annotations

SCHEMA_VERSION = 1

RESULTS = ("successful", "weak", "failed", "unknown")


def memory_record(
    memory_id: str,
    source: str,
    events: list,
    shots: list,
    hook: dict,
    structure: str,
    duration: float,
    qc_passed: bool,
    file_hash: str = "",
    game: str = "Roll Anime Girls",
    pacing: str = "adaptive",
    result: str = "unknown",
    performance: dict | None = None,
) -> dict:
    perf = {
        "views": None,
        "likes": None,
        "comments": None,
        "retention": None,
        "average_percentage_viewed": None,
        "swiped_away": None,
        "engagement_rate": None,
    }
    if performance:
        for key in perf:
            if key in performance:
                perf[key] = performance[key]
    if result not in RESULTS:
        result = "unknown"
    return {
        "schema_version": SCHEMA_VERSION,
        "memory_id": memory_id,
        "source": source,
        "game": game,
        "file_hash": file_hash,
        "events": events,
        "shots": shots,
        "hook": hook,
        "structure": structure,
        "pacing": pacing,
        "shot_pattern": [s.get("event") for s in shots],
        "duration": duration,
        "qc": {"passed": bool(qc_passed)},
        "performance": perf,
        "result": result,
    }
