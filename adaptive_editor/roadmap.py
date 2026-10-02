"""Hold reveals. Cut static. Never invent a missing role."""
from __future__ import annotations

from .pacing import STRUCTURES, choose, recent_too_similar
from .schemas import StoryDoc


def build(shots: list, history: list | None = None, pattern_id: str | None = None) -> StoryDoc:
    history = history or []
    usable = [s for s in shots if s.event not in ("UNKNOWN", "LOW_INFORMATION") and s.supported]
    if not usable:
        return StoryDoc("", "none", {}, [], 0, 0, "none", "", ["no supported footage events"])
    structure = choose(usable)
    if pattern_id and _can(pattern_id, usable):
        structure = pattern_id
        print(f"[adaptive] roadmap built from memory pattern {pattern_id}")
    elif pattern_id:
        print(f"[adaptive] memory pattern {pattern_id} rejected — footage missing required events")
    hook = max(usable, key=lambda s: (s.hook_score - s.static_penalty, s.payoff_score))
    signature = {"hook_type": hook.event.lower(), "structure": structure, "pacing": "adaptive"}
    if recent_too_similar(signature, history):
        alts = [s for s in STRUCTURES if s != structure]
        for alt in alts:
            if _can(alt, usable):
                structure = alt
                break
    steps = _steps(structure, usable, hook)
    dur = round(sum(s["end"] - s["start"] for s in steps), 2)
    concept = hook.event.replace("_", " ").lower()
    return StoryDoc(
        source=hook.clip,
        concept=concept,
        hook={"source_clip": hook.clip, "start": hook.start, "end": hook.end, "reason": hook.reason or hook.event},
        roadmap=steps,
        estimated_duration=dur,
        confidence=round(min(0.92, 0.45 + hook.hook_score / 2), 2),
        structure=structure,
        script="",
    )


def _can(structure: str, shots: list) -> bool:
    events = {s.event for s in shots}
    if structure in ("tease_roll_reveal",):
        return bool(events & {"ROLL", "SPIN"}) and bool(events & {"RARE_REVEAL", "CHARACTER_REVEAL"})
    if structure in ("attempt_fail_payoff", "fail_attempt"):
        return "FAIL" in events
    if structure == "generic_roll":
        return "ROLL" in events
    return True


def _steps(structure: str, shots: list, hook) -> list:
    used = []

    def take(role: str, prefer: set, fallback):
        pool = [s for s in shots if s.event in prefer and s not in used]
        if not pool:
            print(f"[adaptive] skip {role} — no footage event in {sorted(prefer)}")
            return
        pick = fallback if fallback in pool else pool[0]
        used.append(pick)
        end = pick.end
        if pick.event in ("STATIC", "MENU", "LOW_INFORMATION"):
            end = min(pick.end, pick.start + 0.8)
        if pick.event in ("RARE_REVEAL", "CHARACTER_REVEAL"):
            end = min(pick.end, pick.start + 2.2)
        used[-1] = pick
        return {
            "role": role,
            "clip": pick.clip,
            "start": pick.start,
            "end": round(end, 2),
            "reason": pick.event,
        }

    rows = []
    tease = take("HOOK", {"RARE_REVEAL", "CHARACTER_REVEAL", "LUCKY_RESULT", "FAST_ACTION"}, hook)
    if tease:
        rows.append(tease)
    if structure == "tease_roll_reveal":
        row = take("SETUP", {"ROLL", "SPIN"}, None)
        if row:
            rows.append(row)
        row = take("PAYOFF", {"RARE_REVEAL", "CHARACTER_REVEAL", "REWARD"}, None)
        if row:
            rows.append(row)
    elif structure == "attempt_fail_payoff":
        row = take("ESCALATION", {"FAIL", "ROLL"}, None)
        if row:
            rows.append(row)
        row = take("PAYOFF", {"REWARD", "LUCKY_RESULT"}, None)
        if row:
            rows.append(row)
    else:
        row = take("SETUP", {"ROLL", "SPIN", "FAST_ACTION"}, None)
        if row:
            rows.append(row)
        row = take("PAYOFF", {"REWARD", "CHARACTER_REVEAL", "RARE_REVEAL"}, None)
        if row:
            rows.append(row)
    return rows
