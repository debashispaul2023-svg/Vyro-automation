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
    """Footage signal is authoritative. Filename is a weak hint and is rejected if unsupported."""
    hint = event_from_name(name)
    sig = signal or {}
    if not sig.get("sampled") or sig.get("static"):
        print(f"[adaptive] filename hint {hint} not trusted — footage has no supporting motion")
        return "UNKNOWN", False
    action = {"ROLL", "SPIN", "FAST_ACTION", "FAIL"}
    reveal = {"CHARACTER_REVEAL", "RARE_REVEAL", "LUCKY_RESULT"}
    if hint in action and sig.get("motion", 0) >= 0.02:
        return hint, True
    if hint in reveal and sig.get("scene_change"):
        return hint, True
    if hint == "REWARD" and sig.get("motion", 0) >= 0.02:
        return hint, True
    print(f"[adaptive] filename hint {hint} not trusted — footage signal does not support it")
    return "UNKNOWN", False
