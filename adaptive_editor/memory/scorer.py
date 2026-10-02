"""Deterministic similarity. Swap later for embeddings without changing retrieval."""
from __future__ import annotations


def event_overlap(have: set, need: set) -> float:
    if not need:
        return 0.0
    return len(have & need) / len(need)


def score_pattern(events: set, pattern: dict, recent: list) -> tuple[float, str]:
    need = {e.upper() for e in pattern.get("required_events") or []}
    if need and not need <= events:
        missing = sorted(need - events)
        return -1.0, f"rejected: missing {', '.join(missing)}"
    optional = {e.upper() for e in pattern.get("optional_events") or []}
    overlap = event_overlap(events, need | optional or need)
    success = float(pattern.get("success_rate") or 0)
    score = 0.55 * overlap + 0.45 * success
    pid = pattern.get("pattern_id")
    penalty = 0.0
    if recent and recent[-5:].count(pid) >= 4:
        penalty = 0.1
        score -= penalty
    return round(score, 2), f"compatible events + historical prior {success:.2f} penalty {penalty:.2f}"
