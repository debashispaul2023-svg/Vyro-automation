"""Scores are internal. Static and menu shots lose hook value."""
from __future__ import annotations

from .schemas import Shot


def score(shot: Shot) -> Shot:
    base = 0.35
    if shot.event in ("RARE_REVEAL", "CHARACTER_REVEAL", "LUCKY_RESULT"):
        shot.hook_score, shot.payoff_score, shot.visual_interest = 0.92, 0.95, 0.88
        shot.story_value, shot.novelty_score = 0.9, 0.86
    elif shot.event in ("SPIN", "ROLL", "FAST_ACTION"):
        shot.hook_score, shot.motion_score, shot.story_value = 0.62, 0.8, 0.7
        shot.visual_interest = 0.66
    elif shot.event == "REWARD":
        shot.payoff_score, shot.story_value, shot.hook_score = 0.84, 0.74, 0.58
    elif shot.event == "FAIL":
        shot.story_value, shot.hook_score = 0.55, 0.4
    elif shot.event in ("MENU", "STATIC", "WAITING", "LOW_INFORMATION"):
        shot.hook_score, shot.static_penalty = 0.12, 0.7
        shot.visual_interest = 0.15
    else:
        shot.hook_score = base
    shot.clarity_score = 0.7 if shot.duration >= 0.8 else 0.4
    if shot.duration < 0.5:
        shot.hook_score = max(0.0, shot.hook_score - 0.2)
    return shot
