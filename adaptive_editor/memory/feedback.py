"""Optional performance update. Missing metrics stay null. No inferred numbers."""
from __future__ import annotations

from .pattern_memory import load_patterns, save_patterns
from .storage import append_jsonl

FIELDS = (
    "views",
    "likes",
    "comments",
    "retention",
    "average_percentage_viewed",
    "swiped_away",
    "engagement_rate",
)


def record_feedback(video_id: str, structure: str, result: str, performance: dict | None = None) -> dict:
    perf = {key: None for key in FIELDS}
    for key, value in (performance or {}).items():
        if key in perf:
            perf[key] = value
    if result not in ("successful", "weak", "failed", "unknown"):
        result = "unknown"
    row = {
        "schema_version": 1,
        "video_id": video_id,
        "structure": structure,
        "performance": perf,
        "result": result,
    }
    append_jsonl("performance_memory.jsonl", row)
    if result in ("successful", "failed"):
        _bump(structure, result)
    print(f"[adaptive-memory] feedback {video_id} result={result}")
    return row


def _bump(structure: str, result: str) -> None:
    patterns = load_patterns()
    for pattern in patterns:
        if pattern.get("pattern_id") != structure:
            continue
        pattern["usage_count"] = int(pattern.get("usage_count") or 0) + 1
        if result == "successful":
            pattern["success_count"] = int(pattern.get("success_count") or 0) + 1
        else:
            pattern["failure_count"] = int(pattern.get("failure_count") or 0) + 1
        usage = max(1, int(pattern["usage_count"]))
        pattern["success_rate"] = round(int(pattern.get("success_count") or 0) / usage, 2)
    save_patterns(patterns)
