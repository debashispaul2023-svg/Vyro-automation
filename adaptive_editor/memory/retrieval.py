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


def _advise(shots: list) -> dict:
    memories = read_jsonl("footage_memory.jsonl")
    print(f"[adaptive-memory] loaded {len(memories)} memories")
    events = sorted({s.event for s in shots if s.event})
    print("[adaptive-memory] new footage events:")
    for ev in events:
        print(f"  {ev}")
    event_set = set(events)
    ranked = []
    print("[adaptive-memory] candidate patterns:")
    for pattern in load_patterns():
        score, reason = score_pattern(event_set, pattern, recent_structures())
        pid = pattern.get("pattern_id")
        if score < 0:
            print(f"  {pid} {reason}")
            continue
        ranked.append((score, pattern, reason))
        print(f"  {pid} score={score:.2f}")
    if not ranked:
        print("[adaptive-memory] matching patterns: 0")
        return {"ok": False, "selected": "", "penalty": 0.0}
    ranked.sort(key=lambda row: row[0], reverse=True)
    score, pattern, reason = ranked[0]
    recent = recent_structures()
    penalty = 0.1 if recent[-5:].count(pattern["pattern_id"]) >= 4 else 0.0
    if penalty and len(ranked) > 1:
        print("[adaptive-memory] structure overused")
        score, pattern, reason = ranked[1]
        reason = "overused top pattern, next compatible selected"
    print(f"[adaptive-memory] matching patterns: {len(ranked)}")
    print(f"[adaptive-memory] selected pattern: {pattern['pattern_id']}")
    print(f"[adaptive-memory] pattern success rate: {float(pattern.get('success_rate') or 0):.2f}")
    print(f"[adaptive-memory] repetition penalty: {penalty:.2f}")
    print(f"[adaptive-memory] reason: {reason}")
    return {
        "ok": True,
        "selected": pattern["pattern_id"],
        "success_rate": pattern.get("success_rate"),
        "penalty": penalty,
        "reason": reason,
    }
