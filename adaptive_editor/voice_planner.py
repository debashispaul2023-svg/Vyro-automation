"""Script only claims events present in the selected roadmap."""
from __future__ import annotations

LINES = {
    "ROLL": "The dice roll.",
    "SPIN": "The wheel spins.",
    "CHARACTER_REVEAL": "A character is revealed.",
    "RARE_REVEAL": "A rare result shows on screen.",
    "REWARD": "The plot shows money.",
    "FAIL": "That roll does not hit.",
    "LUCKY_RESULT": "That result lands.",
    "FAST_ACTION": "The character moves.",
}


def script_for(steps: list) -> tuple[str, list]:
    events = []
    for step in steps:
        ev = step.get("reason") or ""
        if ev and ev not in events:
            events.append(ev)
    lines = [LINES[ev] for ev in events if ev in LINES]
    if not lines:
        lines = ["This is the gameplay that is actually on screen."]
    lines.append("The game is called Roll Anime Girls.")
    return " ".join(lines), []
