"""Save an experience after QC. Does not invent views."""
from __future__ import annotations

import hashlib
import os

from .schemas import memory_record
from .storage import append_jsonl, read_jsonl


def file_hash(path: str) -> str:
    if not path or not os.path.isfile(path):
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def already_indexed(digest: str) -> bool:
    if not digest:
        return False
    return any((row.get("file_hash") == digest) for row in read_jsonl("footage_memory.jsonl"))


def recent_structures(limit: int = 5) -> list:
    rows = read_jsonl("footage_memory.jsonl")
    return [r.get("structure") for r in rows[-limit:] if r.get("structure")]


def save_experience(shots: list, story, qc_report: dict, source: str = "adaptive_test") -> bool:
    try:
        events = sorted({s.event for s in shots})
        shot_rows = [
            {
                "start": s.start,
                "end": s.end,
                "event": s.event,
                "visual_interest": s.visual_interest,
                "hook_score": s.hook_score,
                "payoff_score": s.payoff_score,
            }
            for s in shots[:8]
        ]
        digest = file_hash(shots[0].clip) if shots else ""
        if digest and already_indexed(digest):
            print(f"[adaptive-memory] already indexed: {digest[:12]}")
            return False
        row = memory_record(
            memory_id=f"{source}_{len(read_jsonl('footage_memory.jsonl')) + 1}",
            source=source,
            events=events,
            shots=shot_rows,
            hook=story.hook or {},
            structure=story.structure,
            duration=story.estimated_duration,
            qc_passed=bool((qc_report or {}).get("ok")),
            file_hash=digest,
            result="unknown",
        )
        ok = append_jsonl("footage_memory.jsonl", row)
        if ok:
            print(f"[adaptive-memory] saved experience {row['memory_id']} result=unknown")
        return ok
    except Exception as exc:
        print(f"[adaptive-memory] save failed: {exc}")
        return False
