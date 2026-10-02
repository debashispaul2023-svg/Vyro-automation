"""Reusable patterns. A pattern is usable only if required events exist in new footage."""
from __future__ import annotations

from .storage import read_json, write_json

SEED = {
    "schema_version": 1,
    "patterns": [
        {
            "pattern_id": "tease_roll_reveal",
            "required_events": ["ROLL", "CHARACTER_REVEAL"],
            "optional_events": ["RARE_REVEAL", "REWARD"],
            "sequence": ["hook_tease", "roll", "reveal"],
            "recommended_pacing": "fast",
            "usage_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "weak_count": 0,
            "success_rate": None,
            "result": "unknown",
            "notes": "seed structure only, no measured performance",
        },
        {
            "pattern_id": "generic_roll",
            "required_events": ["ROLL"],
            "optional_events": [],
            "sequence": ["roll"],
            "recommended_pacing": "medium",
            "usage_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "weak_count": 0,
            "success_rate": None,
            "result": "unknown",
            "notes": "seed structure only, no measured performance",
        },
        {
            "pattern_id": "fail_attempt",
            "required_events": ["FAIL"],
            "optional_events": ["ROLL"],
            "sequence": ["attempt", "fail"],
            "recommended_pacing": "medium",
            "usage_count": 0,
            "success_count": 0,
            "failure_count": 0,
            "weak_count": 0,
            "success_rate": None,
            "result": "unknown",
            "notes": "seed structure only, no measured performance",
        },
    ],
}


def load_patterns() -> list:
    data = read_json("pattern_memory.json", SEED)
    return list(data.get("patterns") or SEED["patterns"])


def save_patterns(patterns: list) -> bool:
    return write_json("pattern_memory.json", {"schema_version": 1, "patterns": patterns})
