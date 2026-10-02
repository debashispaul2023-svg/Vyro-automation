"""Find compatible past patterns. Never invent a missing event."""
from __future__ import annotations

from .footage_memory import recent_structures
from .pattern_memory import load_patterns
from .scorer import score_pattern
from .storage import read_jsonl


def advise(shots: list) -> dict:
    try:
        return _advise(shots)
    except Exception as exc:
        print(f"[adaptive-memory] retrieval failed: {exc}")
        print("[adaptive-memory] continuing with fresh footage analysis")
        return {"ok": False, "selected": "", "penalty": 0.0}


def _memory_boost(event_set: set, pattern_id: str, memories: list) -> float:
    boost = 0.0
    for row in memories:
        if row.get("result") not in ("successful", "weak", "failed"):
            continue
        if _norm(row.get("structure")) != pattern_id:
            continue
        have = {str(e).upper() for e in (row.get("events") or [])}
        if not have:
            continue
        overlap = len(event_set & have) / max(1, len(event_set | have))
        if row.get("result") == "successful":
            boost = max(boost, round(0.2 * overlap, 2))
        elif row.get("result") == "weak":
            boost = min(boost, -0.1)
    return boost


def _norm(structure: str | None) -> str:
    raw = (structure or "").lower()
    if raw in ("tease_roll_reveal", "strongest_visual"):
        return raw
    return raw


def _advise(shots: list) -> dict:
    memories = read_jsonl("footage_memory.jsonl")
    print(f"[adaptive-memory] loaded {len(memories)} memories")
    events = sorted({s.event for s in shots if s.event and s.event != "UNKNOWN"})
    print("[adaptive-memory] new footage events:")
    for ev in events:
        print(f"  {ev}")
    event_set = set(events)
    ranked = []
    print("[adaptive-memory] candidate patterns:")
    recent = [_norm(x) for x in recent_structures()]
    for pattern in load_patterns():
        boost = _memory_boost(event_set, pattern.get("pattern_id"), memories)
        score, reason = score_pattern(event_set, pattern, recent, boost)
        pid = pattern.get("pattern_id")
        if score < 0:
            print(f"  {pid} {reason}")
            continue
        ranked.append((score, pattern, reason))
        print(f"  {pid} score={score:.2f} memory_boost={boost:.2f}")
    if not ranked:
        print("[adaptive-memory] matching patterns: 0")
        return {"ok": False, "selected": "", "penalty": 0.0}
    ranked.sort(key=lambda row: row[0], reverse=True)
    score, pattern, reason = ranked[0]
    penalty = 0.1 if recent[-5:].count(pattern["pattern_id"]) >= 4 else 0.0
    if penalty and len(ranked) > 1:
        print("[adaptive-memory] structure overused")
        score, pattern, reason = ranked[1]
        reason = "overused top pattern, next compatible selected"
    elif penalty:
        print("[adaptive-memory] structure overused but no other compatible pattern — footage wins")
    print(f"[adaptive-memory] matching patterns: {len(ranked)}")
    print(f"[adaptive-memory] selected pattern: {pattern['pattern_id']}")
    rate = pattern.get("success_rate")
    print(f"[adaptive-memory] pattern success rate: {rate if rate is not None else 'unknown'}")
    print(f"[adaptive-memory] repetition penalty: {penalty:.2f}")
    print(f"[adaptive-memory] reason: {reason}")
    return {
        "ok": True,
        "selected": pattern["pattern_id"],
        "success_rate": rate,
        "penalty": penalty,
        "reason": reason,
    }
