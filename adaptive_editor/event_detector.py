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
    low = (name or "").lower()
    if "rare" in low:
        return "RARE_REVEAL"
    best, hits = "LOW_INFORMATION", 0
    for event, words in WORDS.items():
        n = sum(1 for w in words if w in low)
        if n > hits:
            hits, best = n, event
    return best
