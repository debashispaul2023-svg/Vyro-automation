"""Map filename and optional review notes to events. Never invent a rare pull."""
from __future__ import annotations

WORDS = {
    "RARE_REVEAL": ("rare", "legendary", "secret"),
    "CHARACTER_REVEAL": ("character", "unlock", "reveal", "summon"),
    "LUCKY_RESULT": ("lucky", "jackpot"),
    "FAIL": ("fail", "miss", "common"),
    "SPIN": ("spin", "wheel"),
    "ROLL": ("roll", "dice"),
    "REWARD": ("money", "cash", "reward", "earn"),
    "FAST_ACTION": ("run", "jump", "obby"),
    "MENU": ("menu", "shop", "inventory"),
    "STATIC": ("static", "idle", "afk"),
}


def event_from_name(name: str) -> str:
    """Filename hint only. Not footage truth."""
    low = (name or "").lower()
    if "rare" in low:
        return "RARE_REVEAL"
    best, hits = "LOW_INFORMATION", 0
    for event, words in WORDS.items():
        n = sum(1 for w in words if w in low)
        if n > hits:
            hits, best = n, event
    return best


ALLOWED = {
    "ROLL", "REWARD", "CHARACTER_REVEAL", "LUCKY_RESULT", "FAIL", "WIN", "LOSS",
    "SURPRISE", "FAST_ACTION", "PROGRESSION", "WAITING", "STATIC", "UNKNOWN",
}
MIN_CONFIDENCE = 0.75


def event_from_footage(name: str, signal: dict | None, vision: dict | None = None) -> tuple[str, bool]:
    """Local signals are evidence only. Filename cannot create an event."""
    hint = event_from_name(name)
    events = classify_events(signal or {}, vision or {}, hint)
    if not events:
        print(f"[adaptive] filename hint {hint} ignored — no sufficient visual evidence")
        return "UNKNOWN", False
    best = events[0]
    print(f"[adaptive] event {best['type']} confidence={best['confidence']} evidence={best['evidence']}")
    return best["type"], True


def classify_events(signal: dict, vision: dict | None = None, filename_hint: str = "") -> list:
    rows = []
    items = (vision or {}).get("events") if isinstance(vision, dict) else None
    for item in items or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "UNKNOWN")
        conf = float(item.get("confidence") or 0)
        evidence = item.get("evidence") or []
        if kind not in ALLOWED or kind == "UNKNOWN" or conf < MIN_CONFIDENCE or not evidence:
            continue
        rows.append({
            "type": kind,
            "start": float(item.get("start") or 0),
            "end": float(item.get("end") or 0),
            "confidence": conf,
            "evidence": evidence,
            "filename_hint": filename_hint,
            "local": {
                "motion": signal.get("motion"),
                "scene_change_score": signal.get("scene_change_score"),
                "visual_signal": signal.get("visual_signal"),
            },
        })
    if rows:
        return rows
    if signal.get("sampled") and signal.get("static"):
        return [{"type": "STATIC", "start": 0, "end": 0, "confidence": 0.8, "evidence": ["little visual change"], "filename_hint": filename_hint}]
    return []
