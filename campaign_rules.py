"""Requirements for ANY campaign, built from what the link/config gives, never from a game name.

Order of sources (all merged, nothing hardcoded per game):
  1. entry["requirements"]            list of rule lines saved in whop_campaigns.json
  2. Content Rewards page (logged-in) when a CR session is configured (contentrewards_client)
  3. generic brief: name spoken + CTA "Game is called <name> on Roblox." + Roblox/English-only

Content Rewards locks its requirements until the account has joined and is signed in
("Join to unlock N requirements"). Without CR_STORAGE_STATE the bot says so loudly and uses 1 + 3.
"""
from __future__ import annotations

import os
import re


def game_name(entry: dict, fallback: str = "") -> str:
    raw = (entry.get("name_hint") or fallback or "").strip()
    try:
        from bloxclips_footage import norm_name
        # keep original capitalisation: strip only bracket tags and the word "Campaign"
        raw = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", raw)
        raw = re.sub(r"\bcampaign\b", "", raw, flags=re.I).strip()
    except Exception:
        pass
    return re.sub(r"\s+", " ", raw).strip() or fallback or "this game"


def fetched_rules(entry: dict) -> list[str]:
    """Rules read live from Content Rewards. [] when locked / no session / not a CR campaign."""
    if (entry.get("platform") or "").lower() != "contentrewards":
        return []
    url = entry.get("url") or ""
    try:
        import contentrewards_client as cr
        if not cr.session_available():
            print("[rules] Content Rewards requirements are LOCKED (no CR_STORAGE_STATE session); "
                  "using saved/generic rules. Join + sign in once and save the session to read them.")
            return []
        text = cr.fetch_requirements(url)
    except Exception as exc:
        print(f"[rules] Content Rewards read failed: {exc}")
        return []
    lines = [ln.strip(" -•\t") for ln in (text or "").splitlines() if len(ln.strip()) > 8]
    print(f"[rules] read {len(lines)} requirement line(s) from Content Rewards")
    return lines


def rules_text(entry: dict, name: str = "") -> str:
    game = game_name(entry, name)
    saved = [str(x).strip() for x in (entry.get("requirements") or []) if str(x).strip()]
    live = fetched_rules(entry)
    generic = [
        f"The name {game} must be spoken somewhere in the video.",
        f"A clear CTA must be included. Example: Game is called {game} on Roblox.",
        "Only Roblox accounts. English-based.",
    ]
    lines: list[str] = []
    for src in (live, saved, generic):
        for ln in src:
            if ln.lower() not in {x.lower() for x in lines}:
                lines.append(ln)
    print(f"[rules] {game}: live={len(live)} saved={len(saved)} generic={len(generic)} -> {len(lines)} line(s)")
    return game + "\n" + "\n".join(lines)
