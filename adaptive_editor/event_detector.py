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


def event_from_footage(name: str, signal: dict | None) -> tuple[str, bool]:
    """Frame evidence decides. Filename is logged and cannot create an event."""
    hint = event_from_name(name)
    sig = signal or {}
    evidence = {
        "motion_score": sig.get("motion", 0.0),
        "scene_change_score": sig.get("scene_change_score", 0.0),
        "frame_samples": sig.get("frame_samples") or [],
        "visual_signal": sig.get("visual_signal") or "unsampled",
        "filename_hint": hint,
    }
    if not sig.get("sampled") or sig.get("static") or evidence["visual_signal"] in ("static", "single_frame", "unsampled", "motion_only"):
        print(f"[adaptive] filename hint {hint} ignored — visual_signal={evidence['visual_signal']}")
        return "UNKNOWN", False
    if evidence["visual_signal"] == "reveal-like transition, not identity":
        print(f"[adaptive] reveal-like transition evidence={evidence}")
        return "CHARACTER_REVEAL", True
    if evidence["visual_signal"] == "scene_change":
        print(f"[adaptive] scene change recorded, event not classified — {evidence}")
        return "UNKNOWN", False
    print(f"[adaptive] filename hint {hint} ignored — no classified visual event")
    return "UNKNOWN", False
