"""Footage-first story layer with deterministic fallback."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass

STORY_JSON = "output/story.json"
WORK = "work/story"
VISION_MODELS = ("gemini-3.8-flash", "gemini-3.6-flash", "gemini-2.5-flash")

EVENT_WORDS = {
    "rolling": ("spin", "roll", "wheel", "dice"),
    "character_reveal": ("character", "girl", "unlock", "reveal", "summon"),
    "money_reward": ("money", "cash", "$", "earn", "income", "qa/s", "t/s"),
    "plot_place": ("plot", "place", "pad", "base", "platform"),
    "luck": ("luck", "potion"),
    "rebirth": ("rebirth", "prestige"),
    "ui_result": ("reward", "result", "popup"),
    "menu": ("shop", "inventory", "settings", "menu"),
    "running": ("run", "walk", "obby"),
}
COMPAT = {
    "rolling": {"character_reveal", "ui_result", "plot_place", "money_reward"},
    "character_reveal": {"plot_place", "money_reward", "luck", "rolling"},
    "plot_place": {"money_reward", "luck", "rebirth", "character_reveal"},
    "money_reward": {"plot_place", "luck", "rebirth", "rolling"},
    "luck": {"rolling", "character_reveal", "money_reward"},
    "rebirth": {"money_reward", "plot_place"},
    "ui_result": {"character_reveal", "money_reward", "plot_place"},
}


@dataclass
class Shot:
    clip_id: str
    path: str = ""
    name: str = ""
    start: float = 0.0
    end: float = 0.0
    duration: float = 0.0
    description: str = ""
    gameplay_event: str = "unknown"
    characters_visible: bool = False
    ui_visible: bool = False
    action_score: float = 0.0
    hook_score: float = 0.0
    story_score: float = 0.0
    payoff_score: float = 0.0
    visual_quality_score: float = 0.0
    repetition_score: float = 0.0
    analyzed: bool = False
    role: str = ""


def _probe(path: str) -> float:
    try:
        p = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
        )
        return float((p.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _event_from_text(text: str) -> str:
    low = (text or "").lower()
    best, hits = "unknown", 0
    for ev, words in EVENT_WORDS.items():
        n = sum(1 for w in words if w in low)
        if n > hits:
            hits, best = n, ev
    return best


def _scores(event: str, desc: str, duration: float) -> dict:
    hook = story = payoff = action = 3.0
    if event in ("money_reward", "character_reveal", "ui_result"):
        hook += 4; payoff += 3
    if event == "rolling":
        action += 3; story += 2
    if event == "plot_place":
        story += 3; payoff += 2
    if event in ("luck", "rebirth"):
        story += 1; action += 1
    if event == "menu":
        hook -= 2; story -= 1
    if duration < 0.6:
        hook -= 2
    if not desc or "unknown" in desc.lower() or "unanalyzed" in desc.lower():
        hook -= 1
    return {
        "action_score": max(0.0, action), "hook_score": max(0.0, hook),
        "story_score": max(0.0, story), "payoff_score": max(0.0, payoff),
        "visual_quality_score": 5.0 if duration >= 1.2 else 3.0,
    }


def _grab_frame(path: str, t: float, dest: str) -> bool:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", path, "-frames:v", "1", dest],
            check=True, capture_output=True, timeout=20,
        )
        return os.path.isfile(dest) and os.path.getsize(dest) > 400
    except Exception:
        return False


def _describe_frame(frame_path: str) -> str:
    if not os.path.isfile(frame_path):
        return ""
    keys = [k.strip() for k in (os.environ.get("GEMINI_API_KEYS") or "").split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    if not keys:
        return ""
    try:
        import google.generativeai as genai
    except Exception:
        return ""
    raw = open(frame_path, "rb").read()
    prompt = "Describe this Roblox gameplay frame in one factual sentence. Only visible events. No rarity guesses."
    for key in keys[:3]:
        for model_name in VISION_MODELS:
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(model_name)
                resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": raw}])
                text = (getattr(resp, "text", None) or "").strip()
                if text:
                    print(f"[story] vision ok model={model_name}")
                    return text[:240]
            except Exception as exc:
                print(f"[story] vision {model_name} failed: {str(exc)[:120]}")
    return ""
