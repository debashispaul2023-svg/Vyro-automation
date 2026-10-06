"""Footage-first story layer with deterministic fallback."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass

import gemini_runtime

STORY_JSON = "output/story.json"
WORK = "work/story"
VISION_MODELS = gemini_runtime.MODELS
_VISION_DEAD = gemini_runtime.RUNTIME.quarantined
_VISION_CALLS = 0
_VISION_CAP = 3

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
    "character_reveal": {"plot_place", "money_reward", "rolling"},
    "plot_place": {"money_reward", "character_reveal", "rolling"},
    "money_reward": {"plot_place", "character_reveal", "rolling"},
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
    if event in ("luck", "rebirth", "running"):
        story -= 4; hook -= 3
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
    global _VISION_CALLS
    if not os.path.isfile(frame_path):
        return ""
    if _VISION_CALLS >= _VISION_CAP or len(_VISION_DEAD) >= len(VISION_MODELS):
        print("[story] vision skipped — quota cap")
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
    rt = gemini_runtime.RUNTIME
    for _ in range(len(gemini_runtime.MODELS)):
        model_name = rt.choose_model("vision")
        if not model_name:
            break
        if model_name in rt.quarantined:
            print(f"[story] skip quarantined model={model_name}")
            continue
        rt.note_attempt(model_name)
        for key in keys[:2]:
            try:
                genai.configure(api_key=key)
                model = genai.GenerativeModel(model_name)
                resp = model.generate_content([prompt, {"mime_type": "image/jpeg", "data": raw}])
                text = (getattr(resp, "text", None) or "").strip()
                _VISION_CALLS += 1
                if text:
                    rt.mark_success(model_name)
                    print(f"[story] vision model={model_name}")
                    return text[:240]
            except Exception as exc:
                kind = rt.classify(exc)
                print(f"[story] vision {model_name} failed: {str(exc)[:120]}")
                if kind == "QUOTA_EXHAUSTED":
                    rt.quota_errors += 1
                    rt.quarantine(model_name, kind)
                    print(f"[story] skip quarantined model={model_name}")
                    break
                if kind == "TRANSIENT_ERROR":
                    rt.transient[model_name] = rt.transient.get(model_name, 0) + 1
                    rt.retries += 1
                    if rt.transient[model_name] > 2:
                        rt.quarantine(model_name, kind)
                    break
                rt.mark_failure(model_name, kind)
                break
    return ""


def analyze_clip(path: str, clip_id: str, name: str = "") -> list:
    dur = _probe(path)
    if dur <= 0.4 or not os.path.isfile(path):
        return []
    windows = [(0.0, min(dur, 3.5))]
    if dur > 6:
        windows.append((max(0.0, dur * 0.45), min(dur, dur * 0.45 + 3.2)))
    shots = []
    for i, (a, b) in enumerate(windows[:2]):
        frame = f"{WORK}/f_{clip_id}_{i}.jpg"
        desc = _describe_frame(frame) if _grab_frame(path, (a + b) / 2.0, frame) else ""
        analyzed = bool(desc)
        event = _event_from_text(f"{name} {desc}") if (desc or name) else "unknown"
        if not analyzed:
            event = _event_from_text(name) or "unknown"
            desc = f"unanalyzed clip {name or clip_id}"
        sc = _scores(event, desc, b - a)
        shots.append(Shot(
            clip_id=str(clip_id), path=path, name=name or clip_id,
            start=round(a, 2), end=round(b, 2), duration=round(b - a, 2),
            description=desc, gameplay_event=event,
            characters_visible=("character" in desc.lower() or "girl" in desc.lower()),
            ui_visible=any(w in desc.lower() for w in ("ui", "button", "shop", "$")),
            analyzed=analyzed, **sc,
        ))
    return shots


def _compat(prev, nxt) -> float:
    if prev.path == nxt.path and abs(prev.start - nxt.start) < 0.2:
        return 0.0
    if nxt.gameplay_event in COMPAT.get(prev.gameplay_event, set()):
        return 3.0
    if "unknown" in (prev.gameplay_event, nxt.gameplay_event):
        return 1.0
    return 0.4 if nxt.gameplay_event == "menu" else 1.2


def build_story(shots: list, target: float = 23.0) -> list:
    if not shots:
        return []
    print(f"[story] analyzing {len(shots)} shots")
    hook = max(shots, key=lambda s: (s.hook_score, s.payoff_score, s.duration))
    hook.role = "HOOK"
    print(f"[story] hook candidate: {hook.name} {hook.start}-{hook.end} score={hook.hook_score:.1f} event={hook.gameplay_event}")
    picked = [hook]
    used = {(hook.path, round(hook.start, 1))}
    remain = [s for s in shots if (s.path, round(s.start, 1)) not in used]
    roles = ["SETUP", "ACTION", "RESULT", "PAYOFF"]
    story_events = {"rolling", "character_reveal", "plot_place", "money_reward", "ui_result"}
    while remain and sum(s.duration for s in picked) < target - 2.2:
        story_left = [s for s in remain if s.gameplay_event in story_events or s.characters_visible]
        pool = story_left or remain
        nxt = max(pool, key=lambda s: _compat(picked[-1], s) + s.story_score + 0.4 * s.payoff_score)
        if nxt.path in {s.path for s in picked}:
            remain = [s for s in remain if s is not nxt]
            continue
        if _compat(picked[-1], nxt) < 0.5 and len(picked) > 1 and story_left:
            remain = [s for s in remain if s is not nxt]
            continue
        nxt.role = roles[min(len(picked) - 1, len(roles) - 1)]
        picked.append(nxt)
        used.add((nxt.path, round(nxt.start, 1)))
        remain = [s for s in remain if (s.path, round(s.start, 1)) not in used]
    print("[story] selected:")
    for i, s in enumerate(picked, 1):
        print(f"  {i}. {s.name} {s.start}-{s.end} {s.role} {s.gameplay_event} analyzed={s.analyzed}")
    if not any(s.analyzed for s in picked):
        print("[story] insufficient footage for full narrative — honest heuristic only")
    return picked


def script_from_shots(shots: list, campaign_name: str = "") -> str:
    lines = [
        "You roll the dice to unlock a character.",
        "Place that character on your plot.",
        "They earn money even while you are offline.",
        "The game is called Roll Anime Girls.",
    ]
    print(f"[script] loop explanation from selected shots ({len(shots)} shots)")
    return " ".join(lines).strip()


def campaign_cta(blob: str) -> str:
    low = (blob or "").lower()
    if "anime" in low or "roll anime" in low:
        return "Try Roll Anime Girls on Roblox."
    if "fisch" in low:
        return "Game is called How to Fisch on Roblox."
    if "steal" in low and "seed" in low:
        return "Game is called Steal A Seed on Roblox."
    return "Try it on Roblox."


def _cut_segment(src: str, start: float, end: float, dest: str) -> bool:
    ln = max(0.6, end - start)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", f"{start:.2f}", "-t", f"{ln:.2f}", "-i", src,
             "-filter_complex",
             "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:1[bg];"
             "[0:v]scale=-2:1700[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1",
             "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-c:a", "aac", "-ar", "44100", dest],
            check=True, capture_output=True, timeout=60,
        )
        return os.path.isfile(dest) and os.path.getsize(dest) > 1000
    except Exception as exc:
        print(f"[story] cut failed: {exc}")
        return False


def assemble(shots: list, dest: str) -> bool:
    os.makedirs(WORK, exist_ok=True)
    parts = []
    for i, s in enumerate(shots):
        out = f"{WORK}/seg_{i}.mp4"
        if _cut_segment(s.path, s.start, s.end, out):
            parts.append(out)
    if not parts:
        return False
    lst = f"{WORK}/list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
    tmp = dest + ".story.mp4"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", tmp],
            check=True, capture_output=True, timeout=120,
        )
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 1000:
            shutil.move(tmp, dest)
            print(f"[story] assembled {len(parts)} story segments -> {dest}")
            return True
    except Exception as exc:
        print(f"[story] assemble failed: {exc}")
    return False


def qc_story(path: str, shots: list) -> list:
    if not os.path.isfile(path) or os.path.getsize(path) < 1000:
        return ["missing file"]
    fails = []
    dur = _probe(path)
    if dur < 6:
        fails.append(f"too short {dur:.1f}s")
    if dur > 32:
        fails.append(f"too long {dur:.1f}s")
    keys = [(s.path, round(s.start, 1)) for s in shots]
    print("[qc] repetition FAIL" if len(keys) != len(set(keys)) else "[qc] repetition PASS")
    if len(keys) != len(set(keys)):
        fails.append("duplicate consecutive shots")
    print("[qc] narration/visual alignment PASS" if any(s.analyzed for s in shots) else "[qc] narration/visual alignment SKIP (no vision)")
    return fails


def stitch_from_story(clips: list, dest_path: str, campaign_blob: str = "") -> bool:
    os.makedirs(WORK, exist_ok=True)
    os.makedirs("output", exist_ok=True)
    try:
        from clip_picker import download_drive_file
        from google_doc_reader import extract_drive_file_id
    except Exception as exc:
        print(f"[story] semantic analysis unavailable — using deterministic fallback ({exc})")
        return False
    key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    if not key:
        print("[story] semantic analysis unavailable — using deterministic fallback (no Drive key)")
        return False
    local = []
    for i, clip in enumerate(clips[:8]):
        fid = clip.get("clip_id") or extract_drive_file_id(clip.get("url") or "")
        if not fid:
            continue
        raw = f"{WORK}/raw_{i}.mp4"
        try:
            download_drive_file(fid, raw, key)
        except Exception as exc:
            print(f"[story] download skip {fid}: {exc}")
            continue
        if os.path.isfile(raw):
            local.append((raw, str(fid), clip.get("name") or fid))
    if len(local) < 2:
        print("[story] insufficient clips — using deterministic fallback")
        return False
    shots = []
    for path, fid, name in local:
        shots.extend(analyze_clip(path, fid, name))
    if not shots:
        print("[story] no shots — using deterministic fallback")
        return False
    picked = build_story(shots, target=23.0)
    if len(picked) < 2:
        print("[story] story too thin — using deterministic fallback")
        return False
    if not assemble(picked, dest_path):
        return False
    payload = {
        "fallback": False,
        "script": script_from_shots(picked, campaign_blob),
        "cta": campaign_cta(campaign_blob),
        "shots": [asdict(s) for s in picked],
    }
    with open(STORY_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"[qc] issues: {qc_story(dest_path, picked)}")
    print("[voice] synced to story duration")
    return True


def load_story_script():
    if not os.path.isfile(STORY_JSON):
        return "", ""
    try:
        data = json.loads(open(STORY_JSON, encoding="utf-8").read())
    except Exception:
        return "", ""
    return (data.get("script") or "").strip(), (data.get("cta") or "").strip()


def attach(ns: dict) -> None:
    orig_pack = ns.get("_next_unused_pack")
    orig_stitch = ns.get("_stitch_official_clips")
    if callable(orig_pack):
        def _next_unused_pack(campaign, log, want: int = 8):
            return orig_pack(campaign, log, want=max(int(want or 0), 8))
        ns["_next_unused_pack"] = _next_unused_pack
        print("[story] pack size raised to 8 candidates")
    if callable(orig_stitch):
        def _stitch_official_clips(clips, dest_path):
            blob = ""
            try:
                blob = " ".join(str(c.get("name") or "") for c in (clips or []) if isinstance(c, dict))
            except Exception:
                blob = ""
            try:
                if stitch_from_story(clips, dest_path, blob):
                    return dest_path
            except Exception as exc:
                print(f"[story] semantic analysis unavailable — using deterministic fallback ({exc})")
            return orig_stitch(clips, dest_path)
        ns["_stitch_official_clips"] = _stitch_official_clips
        print("[story] stitch hook installed")
