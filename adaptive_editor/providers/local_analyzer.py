"""Deterministic reviewer. Never invents events."""
from __future__ import annotations


class LocalReviewProvider:
    name = "local"

    def analyze(self, shots: list, frames: list | None = None) -> dict:
        if not shots:
            return {"ok": False, "reason": "no shots", "hook": None, "boring": [], "confidence": 0.0}
        ranked = sorted(shots, key=lambda s: (s.hook_score, s.payoff_score), reverse=True)
        best = ranked[0]
        boring = [s.clip for s in shots if s.event in ("STATIC", "MENU", "WAITING", "LOW_INFORMATION")]
        return {
            "ok": True,
            "provider": self.name,
            "strongest_hook": {"clip": best.clip, "start": best.start, "end": best.end, "event": best.event},
            "boring": boring,
            "confidence": round(min(0.95, 0.4 + best.hook_score), 2),
            "notes": "local scores only; no invented event",
        }
