"""Captions only repeat supported events. No fixed timestamps."""
from __future__ import annotations


def _game() -> str:
    try:
        from game_context import current_game
        return current_game()
    except Exception:
        return "this game"


def captions(steps: list) -> list:
    rows = []
    t = 0.0
    for step in steps:
        dur = max(0.4, float(step["end"]) - float(step["start"]))
        text = step.get("reason") or step.get("role") or ""
        rows.append({
            "start": round(t, 2),
            "end": round(t + min(dur, 1.6), 2),
            "text": text.replace("_", " "),
        })
        t = rows[-1]["end"]
    if rows:
        rows.append({
            "start": round(t, 2),
            "end": round(t + 0.8, 2),
            "text": f"Game is called {_game()}",
        })
    return rows
