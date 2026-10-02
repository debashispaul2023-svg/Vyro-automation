"""Pick a structure that the shots can actually support."""
from __future__ import annotations

STRUCTURES = (
    "tease_roll_reveal",
    "attempt_fail_payoff",
    "hook_setup_payoff",
    "action_result",
    "strongest_visual",
)


def choose(shots: list) -> str:
    events = {s.event for s in shots}
    if "RARE_REVEAL" in events or "CHARACTER_REVEAL" in events:
        if "ROLL" in events or "SPIN" in events:
            return "tease_roll_reveal"
        return "hook_setup_payoff"
    if "FAIL" in events and ("REWARD" in events or "LUCKY_RESULT" in events):
        return "attempt_fail_payoff"
    if "FAST_ACTION" in events and "REWARD" in events:
        return "action_result"
    return "strongest_visual"


def recent_too_similar(signature: dict, history: list) -> bool:
    if not history:
        return False
    last = history[-1]
    return (
        last.get("structure") == signature.get("structure")
        and last.get("hook_type") == signature.get("hook_type")
    )
