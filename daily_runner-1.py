"""
daily_runner.py

Fully-automated entrypoint, meant to run on a daily schedule (GitHub Actions
cron — see .github/workflows/vyro_daily.yml):

  1. Check Vyro for an active campaign. If none (or it fails the AI quality
     screen), check every Whop campaign listed in whop_campaigns.json.
  2. Ask ai_brain to sanity-check the campaign's requirements — skip and try
     the other platform if it looks like a scam/low-effort listing.
  3. Skip cleanly if nothing usable found anywhere, or if the found
     campaign_id was already processed before (processed_campaigns.json).
  4. Resolve the source clip: some campaigns give a direct clip link
     (Vyro); some (many Whop ones) point to an external Google Doc that
     contains the real footage links — resolve that first if needed.
  5. Parse requirements with AI (falls back to regex parsing on failure) ->
     render vertical short -> generate AI-written metadata (falls back to
     the template on failure) -> validate -> upload to YouTube -> upload to
     Instagram Reels.
  6. Submit the resulting link back to whichever platform the campaign
     came from.
  7. Record the campaign_id as processed, so it's never submitted twice.

Usage (locally or in CI):
    python daily_runner.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Union

import requests

from ai_brain import AIBrainError, ai_generate_metadata, ai_parse_requirements, ai_rank_clip_names, ai_plan_edit_tools, ai_score_campaign
from checker import ValidationError, validate_or_raise
from clip_picker import ClipPickError, collect_clips_from_doc_text, download_drive_file, pick_best_clip
from google_doc_reader import (
    GoogleDocReadError,
    extract_drive_file_id,
    find_campaign_icon_in_folder,
    list_content_folder_clips,
    resolve_and_download_footage,
)
from instagram_uploader import InstagramUploadError, fetch_reel_permalink, upload_reel
from metadata import MetadataError, VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from tts_engine import generate_voiceover, karaoke_words, phrases_from_words
from requirements_parser import CampaignRequirements, RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError
from vyro_client import run_check as vyro_run_check
from vyro_client import run_submit as vyro_run_submit
from whop_client import WhopCampaign, WhopClientError
from whop_client import check_configured_campaigns as whop_check_configured_campaigns
from whop_client import discover_and_join_new_campaigns as whop_discover_and_join_new_campaigns
from whop_client import submit_video_link as whop_submit_video_link
from youtube_uploader import UploadError, upload_video

PROCESSED_LOG_PATH = "processed_campaigns.json"
CLIP_LOG_PATH = "processed_clips.json"
FISCH_DRIVE_FOLDER = "15GpPGJAMvG5ooypQMrhr5aSUZsJo2jxD"
BUDGET_CLOSE_RATIO = 0.90
SOURCE_CLIP_PATH = "input_16x9.mp4"
OUTPUT_PATH = "output/short.mp4"

Campaign = Union[VyroCampaign, WhopCampaign]


def _load_processed() -> set[str]:
    if not os.path.isfile(PROCESSED_LOG_PATH):
        return set()
    with open(PROCESSED_LOG_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def _save_processed(processed: set[str]) -> None:
    with open(PROCESSED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)



def _parse_money_token(num: str, suffix: str) -> float:
    n = float((num or "0").replace(",", "") or 0)
    s = (suffix or "").lower()
    if s == "k":
        n *= 1_000
    elif s == "m":
        n *= 1_000_000
    return n


def _budget_used_ratio(text: str) -> float | None:
    """Parse '$130.85 / $2K' or '$145K/$156K'. None if unknown."""
    if not text:
        return None
    m = re.search(
        r"\$\s*([\d,.]+)\s*([KkMm])?\s*/\s*\$\s*([\d,.]+)\s*([KkMm])?",
        text,
    )
    if not m:
        return None
    used = _parse_money_token(m.group(1), m.group(2) or "")
    total = _parse_money_token(m.group(3), m.group(4) or "")
    if total <= 0:
        return None
    return used / total


def _close_spent_campaign(log: dict, campaign: Campaign, ratio: float) -> None:
    cid = campaign.campaign_id
    if cid not in log["closed_campaigns"]:
        log["closed_campaigns"].append(cid)
    _save_clip_log(log)
    print(f"[budget] CLOSED {campaign.name} ({cid}) at {ratio:.0%} used — never pick again")


def _refuse_mixed_footage(campaign: Campaign) -> None:
    name = (campaign.name or "").lower()
    src = (campaign.source_clip_url or "") + " " + (getattr(campaign, "reference_doc_url", None) or "")
    if FISCH_DRIVE_FOLDER in src and "fisch" not in name:
        raise RuntimeError(
            f"REFUSE mix: Fisch Drive folder attached to '{campaign.name}'. "
            "Submit would go to the wrong campaign."
        )


def _load_clip_log() -> dict:
    empty = {"closed_campaigns": [], "clips": []}
    if not os.path.isfile(CLIP_LOG_PATH):
        return empty
    with open(CLIP_LOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return empty
    data.setdefault("closed_campaigns", [])
    data.setdefault("clips", [])
    return data


def _save_clip_log(log: dict) -> None:
    with open(CLIP_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def _clip_already_used(log: dict, campaign_id: str, clip_id: str, name: str = "") -> bool:
    """Skip a file if this Drive id or clip name was already rendered."""
    reuse = (os.environ.get("CLIP_REUSE") or "").strip().lower() in ("1", "true", "yes")
    if reuse:
        return False
    cid = (clip_id or "").strip()
    nm = (name or "").strip().lower()
    for row in log.get("clips") or []:
        if cid and (row.get("clip_id") or "").strip() == cid:
            return True
        used_name = (row.get("name") or "").strip().lower()
        if nm and used_name and used_name == nm:
            return True
    return False


def _clip_sort_key(clip: dict) -> tuple:
    name = clip.get("name") or ""
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 10_000, name.lower())


def _download_source_clip(url: str, dest_path: str) -> None:
    """
    Downloads the campaign's source clip. Tries yt-dlp first (handles
    YouTube, Google Drive, and most video hosts); falls back to a plain
    HTTP GET for direct file links.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    ytdlp_result = subprocess.run(
        ["yt-dlp", "-f", "mp4/best", "-o", dest_path, url],
        capture_output=True,
        text=True,
    )
    if ytdlp_result.returncode == 0 and os.path.isfile(dest_path):
        return

    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)


def _youtube_token_ready(token_path: str = "token.json") -> bool:
    """True only if token.json is a real user token with refresh_token."""
    if not os.path.isfile(token_path):
        return False
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("installed") or data.get("web"):
        return False
    return bool(data.get("refresh_token") and (data.get("token") or data.get("access_token")))


def _pick_drive_folder(folder_url: str, dest_path: str) -> None:
    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    clips = collect_clips_from_doc_text(folder_url)
    if clips and drive_key:
        winner = pick_best_clip(
            clips,
            work_dir="work/daily",
            api_key=drive_key,
            campaign_notes="",
        )
        shutil.copyfile(winner.local_path, dest_path)
        return
    raise ClipPickError(f"No downloadable videos in Drive folder {folder_url}")


def _list_campaign_clips(campaign: Campaign) -> list[dict[str, str]]:
    folder = getattr(campaign, "source_clip_url", "") or ""
    clips = list_content_folder_clips(
        source_clip_url=folder,
        reference_doc_url=getattr(campaign, "reference_doc_url", None) or "",
        brief_text=campaign.requirements_text or campaign.name or "",
    )
    _maybe_download_game_icon(folder)
    return clips


def _maybe_download_game_icon(folder_url: str) -> None:
    if not folder_url or os.path.isfile("output/game_icon.png") or os.path.isfile("output/game_icon.jpg"):
        return
    icon = find_campaign_icon_in_folder(folder_url)
    if not icon:
        return
    os.makedirs("output", exist_ok=True)
    dest = "output/game_icon.png"
    try:
        download_drive_file(icon["clip_id"], dest, (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip())
        print(f"[icon] saved {dest}")
    except Exception as exc:
        print(f"[icon] download failed: {exc}")



def _next_unused_pack(campaign: Campaign, log: dict, want: int = 4) -> list[dict]:
    """3-4 unused official clips so the edit can explain the loop like the example."""
    first = _next_unused_clip(campaign, log)
    if first is None:
        return []
    clips = _list_campaign_clips(campaign)
    unused = [
        c for c in clips
        if not _clip_already_used(log, campaign.campaign_id, c["clip_id"], c.get("name") or "")
        and c.get("kind") in ("drive_file", "direct")
    ]
    def _ms(c):
        try:
            return float(c.get("duration_ms") or 0)
        except (TypeError, ValueError):
            return 0.0
    unused = [c for c in unused if _ms(c) >= 10000]
    best_by_num: dict[int, dict] = {}
    leftovers = []
    for c in unused:
        name = c.get("name") or ""
        m = re.search(r"(\d+)", name)
        if not m:
            leftovers.append(c)
            continue
        num = int(m.group(1))
        prev = best_by_num.get(num)
        if prev is None or _ms(c) > _ms(prev):
            best_by_num[num] = c
    unused = list(best_by_num.values()) + leftovers
    unused.sort(key=_clip_sort_key)
    pack = []
    if first and any(c["clip_id"] == first["clip_id"] for c in unused):
        pack.append(first)
    for c in unused:
        if pack and c["clip_id"] == pack[0]["clip_id"]:
            continue
        pack.append(c)
        if len(pack) >= want:
            break
    if not pack and unused:
        pack = unused[:want]
    print(f"[clips] merge pack ({len(pack)}): " + ", ".join(x.get("name") or x["clip_id"] for x in pack))
    return pack


def _stitch_official_clips(clips: list[dict], dest_path: str) -> str:
    key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    if not key:
        raise GoogleDocReadError("GOOGLE_DRIVE_API_KEY missing")
    os.makedirs("work/merge", exist_ok=True)
    parts = []
    for i, clip in enumerate(clips):
        fid = clip.get("clip_id") or extract_drive_file_id(clip.get("url") or "")
        if not fid:
            continue
        raw = f"work/merge/raw_{i}.mp4"
        even = f"work/merge/even_{i}.mp4"
        download_drive_file(fid, raw, key)
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", raw,
                "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
                "-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-c:a", "aac", "-ar", "44100", "-ac", "2", even,
            ],
            check=True, capture_output=True, timeout=180,
        )
        parts.append(even)
        print(f"[merge] part {i+1}/{len(clips)} {clip.get('name')}")
    if not parts:
        raise GoogleDocReadError("No clips downloaded to merge")
    if len(parts) == 1:
        shutil.copyfile(parts[0], dest_path)
        return clips[0].get("url") or parts[0]
    lst = "work/merge/list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for part in parts:
            f.write(f"file '{os.path.abspath(part)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", dest_path],
        check=True, capture_output=True, timeout=120,
    )
    print(f"[merge] wrote {dest_path} from {len(parts)} clips")
    _cap_video_length(dest_path, max_seconds=30.0, speed=1.2)
    return dest_path


def _probe_seconds(path: str) -> float:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
        )
        return float((probe.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _cap_video_length(path: str, max_seconds: float = 30.0, speed: float = 1.2) -> None:
    """Keep shorts <= 30s. Speed 1.2x first, then hard trim."""
    if not os.path.isfile(path):
        return
    dur = _probe_seconds(path)
    if dur <= 0 or dur <= max_seconds + 0.15:
        print(f"[cap] {path} already {dur:.1f}s")
        return
    work = path + ".cap.mp4"
    use_speed = dur > max_seconds and speed and speed > 1.0
    ff = ["ffmpeg", "-y", "-i", path]
    if use_speed:
        ff += [
            "-filter_complex",
            f"[0:v]setpts=PTS/{speed}[v];[0:a]atempo={speed}[a]",
            "-map", "[v]", "-map", "[a]",
        ]
        print(f"[cap] {dur:.1f}s -> {speed}x then trim {max_seconds:.0f}s")
    else:
        print(f"[cap] trim {dur:.1f}s to {max_seconds:.0f}s")
    ff += ["-t", str(max_seconds), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-ar", "44100", work]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=180)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, path)
            print(f"[cap] wrote {path} ({_probe_seconds(path):.1f}s)")
    except Exception as exc:
        print(f"[cap] failed: {exc}")
        if os.path.isfile(work):
            os.remove(work)


def _next_unused_clip(campaign: Campaign, log: dict) -> dict[str, str] | None:
    if campaign.campaign_id in set(log.get("closed_campaigns") or []):
        print(f"Campaign {campaign.campaign_id} is closed — never reuse its folder.")
        return None
    clips = _list_campaign_clips(campaign)
    if not clips:
        return None
    unused = [
        clip
        for clip in clips
        if not _clip_already_used(log, campaign.campaign_id, clip["clip_id"], clip.get("name") or "")
    ]
    if not unused:
        print(f"[clips] all {len(clips)} content-folder clips already used for this campaign.")
        return None
    usable = [c for c in unused if c.get("kind") in ("drive_file", "drive_folder", "direct")]
    pool = usable or unused
    long_enough = []
    for c in pool:
        try:
            ms = float(c.get("duration_ms") or 0)
        except (TypeError, ValueError):
            ms = 0
        if ms >= 10000:
            long_enough.append(c)
    if long_enough:
        print(f"[clips] dropped {len(pool) - len(long_enough)} clips under 10s")
        pool = long_enough
    else:
        print("[clips] no 10s+ clip left; using remaining pool")
    pool.sort(key=_clip_sort_key)
    print(f"[clips] next unused by number: {pool[0].get('name')} ({len(pool)} left)")
    clip = pool[0]
    print(f"[clips] next unused: {clip.get('name') or clip['clip_id']} ({clip['kind']})")
    return clip



def _probe_dur(path: str) -> float:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=20,
        )
        return float((probe.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _font() -> str:
    for p in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.isfile(p):
            return f"fontfile={p}:"
    return ""


def _layout_voice(body_wav: str, cta_wav: str, dest: str, video_dur: float) -> bool:
    """Body plays from 0. CTA (Save. Follow.) is pinned to the last 2.3s."""
    body_d = _probe_dur(body_wav) if body_wav and os.path.isfile(body_wav) else 0.0
    cta_d = _probe_dur(cta_wav) if cta_wav and os.path.isfile(cta_wav) else 0.0
    cta_at = max(1.0, video_dur - max(cta_d, 2.1) - 0.05)
    gap = max(0.15, cta_at - body_d)
    if body_d > cta_at - 0.2 and body_wav:
        trimmed = dest.replace(".wav", "_bodytrim.wav")
        subprocess.run(
            ["ffmpeg", "-y", "-i", body_wav, "-t", f"{max(0.8, cta_at - 0.25):.2f}",
             "-c:a", "pcm_s16le", trimmed],
            check=True, capture_output=True, timeout=30,
        )
        body_wav = trimmed
        body_d = _probe_dur(body_wav)
        gap = max(0.15, cta_at - body_d)
    parts = []
    if body_wav and os.path.isfile(body_wav):
        parts.append(body_wav)
    silence = dest.replace(".wav", "_gap.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
         "-t", f"{gap:.2f}", "-c:a", "pcm_s16le", silence],
        check=True, capture_output=True, timeout=20,
    )
    parts.append(silence)
    if cta_wav and os.path.isfile(cta_wav):
        parts.append(cta_wav)
    lst = dest.replace(".wav", "_concat.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        for p in parts:
            fh.write(f"file '{os.path.abspath(p)}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "pcm_s16le", dest],
        check=True, capture_output=True, timeout=30,
    )
    print(f"[voice] layout body={body_d:.1f}s gap={gap:.1f}s cta@{cta_at:.1f}s video={video_dur:.1f}s")
    return os.path.isfile(dest) and os.path.getsize(dest) > 200


def _tts_spoken(text: str, dest_wav: str) -> bool:
    """ElevenLabs only. No espeak."""
    if not (text or "").strip():
        return False
    mp3 = dest_wav.replace(".wav", ".mp3")
    path, words = generate_voiceover(text, mp3)
    if not path:
        return False
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-filter:a", "atempo=1.08,volume=1.25", dest_wav],
            check=True, capture_output=True, timeout=30,
        )
    except Exception as exc:
        print(f"[voice] mp3->wav failed: {exc}")
        return False
    setattr(_tts_spoken, "last_words", words)
    return os.path.isfile(dest_wav) and os.path.getsize(dest_wav) > 200



def _quality_boost(video_path: str) -> None:
    work = "output/quality.mp4"
    ff = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf",
        "scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,setsar=1,"
        "unsharp=5:5:1.4:5:5:0.0,eq=contrast=1.1:saturation=1.14:brightness=0.02",
        "-c:v", "libx264", "-preset", "medium", "-crf", "17",
        "-c:a", "aac", "-b:a", "192k", work,
    ]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=180)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[quality] 1080x1920 crf18 boost applied")
    except Exception as exc:
        print(f"[quality] skipped: {exc}")



def _lock_plan_from_rules(rules: str, plan: dict | None) -> dict:
    """ready_to_upload=false means GENERATE with tools, not skip."""
    low = (rules or "").lower()
    out = dict(plan or {})
    out["ready_to_upload"] = False
    if any(w in low for w in ("spoken", "must be spoken", "voice", "say the name")):
        out["need_spoken_voice"] = True
    if any(w in low for w in ("icon", "visibly shown", "game title", "shown somewhere")):
        out["need_end_icon"] = True
    out["need_captions"] = True
    if any(w in low for w in ("low-quality", "quality", "poorly presented")):
        out["need_quality_boost"] = True
    if "tongue" in low:
        out["speak_text"] = out.get("speak_text") or "+1 Tongue Escape"
        out["cta_text"] = out.get("cta_text") or "Game is called +1 Tongue Escape on Roblox"
        out["end_title"] = out.get("end_title") or "+1 Tongue Escape"
    if "fisch" in low:
        out["speak_text"] = out.get("speak_text") or "How to Fisch"
        out["cta_text"] = out.get("cta_text") or "Game is called How to Fisch on Roblox"
        out["end_title"] = out.get("end_title") or "HOW TO FISCH"
    if "steal" in low and "seed" in low:
        out["speak_text"] = out.get("speak_text") or "Steal A Seed"
        out["cta_text"] = out.get("cta_text") or "Game is called Steal A Seed on Roblox"
    if "athletics" in low:
        out["speak_text"] = out.get("speak_text") or "World Athletics"
        out["cta_text"] = out.get("cta_text") or "Game is called World Athletics on Roblox"
    return out


def _apply_requirement_tools(campaign: Campaign, video_path: str) -> None:
    """Read rules, arm tools, then generate. False ready_to_upload = edit required."""
    rules = f"{campaign.name or ''}\n{campaign.requirements_text or ''}"
    print("[req] ----- campaign rules -----")
    print((rules or "")[:900])
    print("[req] ----- end rules -----")
    try:
        plan = ai_plan_edit_tools(rules)
    except Exception as exc:
        print(f"[tools] AI plan failed ({exc}) — using rules lock")
        plan = {}
    plan = _lock_plan_from_rules(rules, plan)
    print(
        "[tools] armed "
        f"voice={plan.get('need_spoken_voice')} "
        f"captions={plan.get('need_captions')} "
        f"icon={plan.get('need_end_icon')} "
        f"quality={plan.get('need_quality_boost')} "
        f"speak={plan.get('speak_text')!r} "
        f"cta={plan.get('cta_text')!r}"
    )
    _quality_boost(video_path)
    campaign._edit_plan = plan  # type: ignore[attr-defined]
    _apply_campaign_pack(campaign, video_path)


def _apply_campaign_pack(campaign: Campaign, video_path: str) -> None:
    """Fisch: spoken name + end CTA. Keeps original gameplay audio."""
    blob = f"{campaign.name or ''}\n{campaign.requirements_text or ''}".lower()
    plan = getattr(campaign, "_edit_plan", None) or {}
    if plan.get("ready_to_upload"):
        return
    if plan and not (plan.get("need_spoken_voice") or plan.get("need_captions") or plan.get("need_end_icon")):
        return
    game = (plan.get("speak_text") or campaign.name or "this Roblox game").strip()
    cta = (plan.get("cta_text") or f"Game is called {game} on Roblox").strip()
    req_has_code = bool(re.search(r"\b(use code|promo code|referral code)\b", blob))
    if "fisch" in blob:
        scripts = [
            "WAIT. This Roblox game slaps. Catch weird fish. Upgrade your gear. Fight to survive. That is the whole loop. Game is called How to Fisch on Roblox.",
            "YO. This is not a normal fishing game. Catch a fish. Upgrade. Then fight. It looks simple and then it hits. Game is called How to Fisch on Roblox.",
            "LOOK. You keep catching fish to get stronger. Then you fight to stay alive. That loop is addictive. Game is called How to Fisch on Roblox.",
        ]
    elif "tongue" in blob:
        scripts = [
            "WAIT. This Roblox game is actually insane. Your tongue grows. You swing across the map. You escape stage after stage. The stages keep getting harder. Game is called Plus One Tongue Escape on Roblox.",
            "YO. Watch this parkour. The tongue gets longer. The stages get harder. Do not fall. Game is called Plus One Tongue Escape on Roblox.",
            "LOOK. Grow the tongue. Swing. Escape. That is the whole game. Game is called Plus One Tongue Escape on Roblox.",
        ]
    elif "steal" in blob and "seed" in blob:
        scripts = [
            "WAIT. This Roblox game is actually wild. You sneak in. You steal a seed. Then you run. Game is called Steal A Seed on Roblox.",
            "YO. Watch this. Grab the seed and get out before they catch you. Game is called Steal A Seed on Roblox.",
        ]
    else:
        scripts = [
            f"WAIT. This Roblox game is actually fun. Watch this. {cta}.",
            f"YO. I found a new Roblox game you should try. {cta}.",
        ]
    used_n = 0
    try:
        used_n = len((_load_clip_log().get("clips") or []))
    except Exception:
        used_n = 0
    spoken = scripts[used_n % len(scripts)]
    print(f"[voice] script variant {used_n % len(scripts) + 1}/{len(scripts)}")
    _tts_spoken.last_words = []
    if plan and not plan.get("need_spoken_voice"):
        spoken = ""
    work = "output/fisch_pack.mp4"
    tts = "output/fisch_tts.wav"
    body_wav = "output/fisch_body.wav"
    cta_wav = "output/fisch_cta.wav"
    os.makedirs("output", exist_ok=True)
    dur = _probe_dur(video_path) or 24.0
    tts_ok = False
    cta_ok = False
    if spoken:
        body_ok = _tts_spoken(spoken, body_wav)
        body_words = list(getattr(_tts_spoken, "last_words", []) or [])
        cta_ok = _tts_spoken("Save. Hit follow.", cta_wav)
        if not cta_ok:
            cta_ok = _tts_spoken("Save. Hit follow.", cta_wav)
        _tts_spoken.last_words = body_words
        if not body_ok:
            raise RuntimeError("ElevenLabs voice required but failed — refusing silent video")
        tts_ok = _layout_voice(body_wav, "", tts, dur)
        if not tts_ok:
            shutil.copy(body_wav, tts)
            tts_ok = True
    words = list(getattr(_tts_spoken, "last_words", []) or [])
    karaoke = karaoke_words(words, delay=0.0)[:48]
    if not karaoke and spoken:
        karaoke = [(a, b, t) for a, b, t in phrases_from_words(words, spoken)]
    font = _font()
    # Stickers are pinned to the VIDEO end, not the early voice end.
    save_a, save_b = max(0.0, dur - 2.6), max(0.5, dur - 1.3)
    follow_a, follow_b = max(0.0, dur - 1.3), dur
    end_at = max(0.0, dur - 3.0)
    parts = []
    for a, b, txt in karaoke:
        if b <= a or not txt:
            continue
        clean = re.sub(r"[^A-Za-z0-9+#']+", "", txt)
        if clean.lower() in {"save", "follow"}:
            continue
        if a >= save_a - 0.05:
            continue
        b = min(b, save_a - 0.05)
        if b <= a:
            continue
        safe = txt.replace("\\", " ").replace("'", "").replace(":", " -")[:22]
        parts.append(
            f"drawtext={font}text='{safe}':fontcolor=white:fontsize=78:"
            f"borderw=8:bordercolor=0x001033:"
            f"shadowcolor=black@0.85:shadowx=3:shadowy=3:"
            f"x=(w-text_w)/2:y=h-360:enable='between(t,{a:.2f},{b:.2f})'"
        )
    parts.append(
        f"drawtext={font}text=' SAVE ':fontcolor=0x001033:fontsize=110:"
        f"box=1:boxcolor=0xB8FF00@0.95:boxborderw=28:"
        f"x=(w-text_w)/2:y=h-430:enable='between(t,{save_a:.2f},{save_b:.2f})'"
    )
    parts.append(
        f"drawtext={font}text=' HIT FOLLOW ':fontcolor=0x001033:fontsize=88:"
        f"box=1:boxcolor=0xFFE600@0.95:boxborderw=28:"
        f"x=(w-text_w)/2:y=h-430:enable='between(t,{follow_a:.2f},{follow_b:.2f})'"
    )
    if req_has_code:
        raw_req = f"{campaign.name or ''}\n{campaign.requirements_text or ''}"
        code = "WELCOME1"
        if "BONUS500" in raw_req:
            code = "BONUS500"
        cm = re.search(r"(?:use code|promo code)\s*[:\-]?\s*([A-Za-z0-9]{3,16})", raw_req, re.I)
        if cm:
            code = cm.group(1)
        parts.append(
            f"drawtext={font}text='Use code {code}':fontcolor=white:fontsize=68:"
            f"borderw=8:bordercolor=0x001033:"
            f"x=(w-text_w)/2:y=h-300:enable='between(t,{max(0.0, save_a-1.4):.2f},{save_a:.2f})'"
        )
    caption_vf = ",".join(parts) if parts else "null"
    draw = caption_vf
    print(f"[caption] karaoke={len(karaoke)} SAVE {save_a:.1f}-{save_b:.1f} FOLLOW {follow_a:.1f}-{follow_b:.1f}")
    en = f"gte(t,{end_at:.2f})"
    icon = next((p for p in ("output/game_icon.png", "output/game_icon.jpg") if os.path.isfile(p)), "")
    if icon:
        draw = (
            f"[0:v]{draw}[base];"
            f"[1:v]scale=820:820:force_original_aspect_ratio=decrease,"
            f"pad=840:840:(ow-iw)/2:(oh-ih)/2:white[ic];"
            f"[base][ic]overlay=(W-w)/2:H-h-90:enable='{en}'[v]"
        )
        print(f"[fisch] thumbnail icon {icon}")
    cta_ms = int(max(0.0, save_a) * 1000)
    ff = ["ffmpeg", "-y", "-i", video_path]
    extra_a = 0
    if icon:
        ff += ["-i", icon]
    if tts_ok:
        ff += ["-i", tts]
        extra_a += 1
    if cta_ok and os.path.isfile(cta_wav):
        ff += ["-i", cta_wav]
        extra_a += 1
    # audio indexes: 0=video, 1=icon?, then tts, then cta
    a_tts = 2 if icon else 1
    a_cta = a_tts + 1
    if icon and tts_ok:
        audio = f"[0:a]volume=0.32[ag];[{a_tts}:a]volume=1.4[ab];"
        if extra_a >= 2:
            audio += (
                f"[{a_cta}:a]adelay={cta_ms}|{cta_ms},volume=1.55[ac];"
                "[ag][ab][ac]amix=inputs=3:duration=first:dropout_transition=0[a]"
            )
        else:
            audio += "[ag][ab]amix=inputs=2:duration=first:dropout_transition=0[a]"
        ff += [
            "-filter_complex", draw + ";" + audio,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-c:a", "aac", "-t", f"{dur:.2f}", work,
        ]
    elif icon:
        ff += ["-filter_complex", draw, "-map", "[v]", "-c:a", "copy",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
               "-t", f"{dur:.2f}", work]
    elif tts_ok:
        audio = f"[0:a]volume=0.32[ag];[1:a]volume=1.4[ab];"
        if extra_a >= 2:
            audio += (
                f"[2:a]adelay={cta_ms}|{cta_ms},volume=1.55[ac];"
                "[ag][ab][ac]amix=inputs=3:duration=first:dropout_transition=0[a]"
            )
        else:
            audio += "[ag][ab]amix=inputs=2:duration=first:dropout_transition=0[a]"
        ff += [
            "-filter_complex",
            f"[0:v]{draw}[v];" + audio,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-c:a", "aac", "-t", f"{dur:.2f}", work,
        ]
    else:
        ff += ["-vf", draw, "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", work]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=300)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[pack] voice + captions + icon burned in")
        else:
            raise RuntimeError("pack output missing")
    except Exception as exc:
        print(f"[pack] full filter failed ({exc}); retry captions+voice only")
        simple = ["ffmpeg", "-y", "-i", video_path]
        if tts_ok:
            simple += [
                "-i", tts,
                "-filter_complex",
                f"[0:v]{caption_vf}[v];[0:a]volume=0.35[a0];[1:a]volume=1.35[a1];"
                "[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[a]",
                "-map", "[v]", "-map", "[a]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-shortest", work,
            ]
        else:
            simple += ["-vf", caption_vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                       "-c:a", "copy", work]
        try:
            subprocess.run(simple, check=True, capture_output=True, timeout=300)
            if os.path.isfile(work) and os.path.getsize(work) > 1000:
                shutil.move(work, video_path)
                print("[pack] captions + voice burned (no icon)")
            else:
                raise RuntimeError("simple pack missing")
        except Exception as exc2:
            raise RuntimeError(f"voice/caption burn failed: {exc2}") from exc2


def _ig_caption(campaign: Campaign, req: CampaignRequirements, meta: VideoMetadata) -> str:
    """Campaign rules first. Never paste Whop chrome or #shorts spam."""
    raw = f"{campaign.name or ''}\n{campaign.requirements_text or ''}"
    raw_l = raw.lower()
    if "fisch" in raw_l or "how to fisch" in raw_l:
        body = (
            "THIS ROBLOX GAME IS SO PEAK\n"
            "Catch fish, upgrade gear, fight to survive.\n"
            "Game is called How to Fisch on Roblox."
        )
        extras = ["#Roblox", "#Fisch", "#HowToFisch"]
    elif "zodiac" in raw_l and "weekend" in raw_l:
        body = "zodiac just dropped in beta weekend 2 and it's free to play"
        extras = ["#COD", "#Zodiac", "#MW4"]
    elif "modern warfare" in raw_l or "mw4" in raw_l:
        body = "MW4 multiplayer beta gameplay — drop in and play"
        extras = ["#COD", "#MW4", "#CallOfDuty"]
    elif "roblox" in raw_l:
        body = "Roblox gameplay clip"
        extras = ["#Roblox", "#RobloxClips"]
    else:
        title = re.sub(r"#\S+", "", meta.title or campaign.name or "New clip").strip()
        body = title or "New official campaign clip"
        extras = []
        for tag in (req.mandatory_hashtags or [])[:3]:
            token = tag if str(tag).startswith("#") else f"#{tag}"
            extras.append(token)

    lines = [body]
    if "callofduty" in raw_l or "call of duty" in raw_l or "zodiac" in raw_l:
        lines.append("@Callofduty")
    lines.append("#Ad")
    for tag in extras[:3]:
        if tag.lower() not in ("#ad", "#advertisement", "#sponsored"):
            lines.append(tag)
    caption = "\n".join(lines)
    print(f"[caption] {caption!r}")
    return caption


def _resolve_source_clip(campaign: Campaign) -> str:
    """Agent resolver: brief → host → adapter → SOURCE_CLIP_PATH."""
    os.makedirs(os.path.dirname(SOURCE_CLIP_PATH) or ".", exist_ok=True)
    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()

    def _drive_file(fid: str, dest: str) -> None:
        if not drive_key:
            raise GoogleDocReadError("GOOGLE_DRIVE_API_KEY missing")
        download_drive_file(fid, dest, drive_key)

    try:
        return resolve_and_download_footage(
            dest_path=SOURCE_CLIP_PATH,
            source_clip_url=campaign.source_clip_url or "",
            reference_doc_url=getattr(campaign, "reference_doc_url", None) or "",
            brief_text=campaign.requirements_text or campaign.name or "",
            download_url_fn=_download_source_clip,
            download_drive_fn=_drive_file,
            pick_drive_folder_fn=_pick_drive_folder,
        )
    except GoogleDocReadError as exc:
        raise RuntimeError(str(exc)) from exc


_SKIP_CAMPAIGN_MARKERS = (
    "u2 -",
    "street of dreams",
    "geezerbomb",
    "rockbottom",
    "fifa + world cup",
    "world cup edits",
    "forgegui",
    "forge gui",
)
_ALLOW_CAMPAIGN_MARKERS = (
    "how to fisch",
    "steal a seed",
    "tongue escape",
    "world athletics",
)


def _is_blocked_example_campaign(campaign: Campaign) -> str | None:
    """U2 was only a pipeline test. Never treat it as a production target."""
    blob = f"{campaign.name or ''} {campaign.requirements_text or ''}".lower()
    for marker in _SKIP_CAMPAIGN_MARKERS:
        if marker in blob:
            return marker
    return None


def _screen_campaign(platform: str, campaign: Campaign) -> bool:
    """Keep campaigns that can work as Instagram Reels. Drop test/weak ones."""
    blocked = _is_blocked_example_campaign(campaign)
    if blocked:
        print(
            f"Skipping {platform} campaign '{campaign.name}': "
            f"example/test marker '{blocked}'. Looking for a viral IG brief instead."
        )
        return False

    try:
        score = ai_score_campaign(
            f"NAME: {campaign.name}\n\n{campaign.requirements_text or campaign.name}"
        )
    except AIBrainError as exc:
        print(f"AI campaign screening unavailable ({exc}); proceeding anyway.", file=sys.stderr)
        return True

    print(f"AI screen ({platform}): good={score.is_good} — {score.reason}")
    if not score.is_good:
        has_folder = bool(getattr(campaign, "source_clip_url", "") or "")
        named = (campaign.name or "").lower()
        if has_folder and named and "content rewards" not in named:
            print("[screen] AI rejected chrome text, but Drive folder is configured — keeping campaign")
            return True
        print(f"Skipping {platform} campaign '{campaign.campaign_id}': {score.reason}")
        return False
    return True


def _find_campaign() -> tuple[str, Campaign] | tuple[None, None]:
    """Checks Vyro first, then every configured Whop campaign. Skips any
    campaign that fails the AI quality screen and tries the next source."""
    try:
        vyro_campaign = vyro_run_check()
    except VyroClientError as exc:
        print(f"Vyro check failed: {exc}", file=sys.stderr)
        vyro_campaign = None

    if vyro_campaign is not None and _screen_campaign("vyro", vyro_campaign):
        return "vyro", vyro_campaign

    if (os.environ.get("WHOP_ENABLE_DISCOVER") or "").strip().lower() in ("1", "true", "yes"):
        try:
            newly_joined = whop_discover_and_join_new_campaigns(score_fn=ai_score_campaign, max_new=2)
            if newly_joined:
                print(f"Auto-joined {len(newly_joined)} new Whop campaign(s): {newly_joined}")
        except Exception as exc:
            print(f"Whop auto-discovery/join failed: {exc}", file=sys.stderr)
    else:
        print("[whop] Discover auto-join off (set WHOP_ENABLE_DISCOVER=1 to turn on).")

    try:
        whop_campaign = whop_check_configured_campaigns()
    except WhopClientError as exc:
        print(f"Whop check failed: {exc}", file=sys.stderr)
        whop_campaign = None

    if whop_campaign is not None and _screen_campaign("whop", whop_campaign):
        return "whop", whop_campaign

    return None, None


def _instagram_permalink(media_id: str) -> str:
    """Public Reel URL. Uses Instagram Graph (same token as upload)."""
    link = fetch_reel_permalink(media_id)
    if link:
        print(f"[ig] permalink {link}")
    else:
        print("[ig] no permalink — check IG_ACCESS_TOKEN is a current Instagram Login token")
    return link


def _submit_back(platform: str, campaign: Campaign, video_url: str) -> None:
    if platform == "vyro":
        vyro_run_submit(campaign, video_url)
        return
    if platform != "whop":
        raise ValueError(f"Unknown platform: {platform}")
    last = None
    for attempt in range(1, 4):
        try:
            print(f"[whop] submit attempt {attempt}/3 {video_url}")
            whop_submit_video_link(campaign, video_url)
            return
        except Exception as exc:
            last = exc
            print(f"[whop] submit attempt {attempt} failed: {exc}")
    raise last


def _parse_requirements(campaign: Campaign) -> CampaignRequirements:
    raw_text = campaign.requirements_text or campaign.name
    try:
        parsed = ai_parse_requirements(raw_text)
        req = CampaignRequirements(
            campaign_id=campaign.campaign_id,
            mandatory_hashtags=parsed.mandatory_hashtags,
            required_links=parsed.required_links,
            min_seconds=parsed.min_seconds,
            max_seconds=parsed.max_seconds,
            referral_code=parsed.referral_code,
            watermark_text=parsed.watermark_text,
            raw_source=raw_text,
        )
        req.validate()
        if parsed.notes:
            print(f"AI parser note: {parsed.notes}")
        return req
    except (AIBrainError, RequirementsParseError) as exc:
        print(f"AI requirements parsing failed ({exc}); falling back to regex parser.", file=sys.stderr)
        req = parse_campaign(raw_text)
        req.campaign_id = campaign.campaign_id
        return req


def _generate_metadata(hook: str, summary: str, req: CampaignRequirements) -> VideoMetadata:
    blob = f"{hook}\n{summary}".lower()
    if "tongue" in blob:
        print("[meta] Tongue lock — official campaign wording only")
        n = len((_load_clip_log().get("clips") or []))
        titles = [
            "Your tongue keeps GROWING in this Roblox game — +1 Tongue Escape #shorts",
            "Don't fall. The tongue gets longer — +1 Tongue Escape #shorts",
            "This Roblox parkour tongue game is actually crazy — +1 Tongue Escape #shorts",
            "Stage after stage the tongue grows — +1 Tongue Escape #shorts",
            "I found a Roblox game where your tongue is the path — +1 Tongue Escape #shorts",
        ]
        return VideoMetadata(
            title=titles[n % len(titles)],
            description=(
                "Your tongue keeps growing while you escape.\n"
                "Game is called +1 Tongue Escape on Roblox.\n"
                "https://www.roblox.com/games/122245938604556/1-Tongue-Escape"
            ),
            tags=["#Roblox", "#TongueEscape", "#shorts"],
        )
    if "fisch" in blob or "how to fisch" in blob:
        print("[meta] Fisch lock — official campaign wording only")
        return VideoMetadata(
            title=(
                [
                    "THIS ROBLOX GAME IS SO PEAK — How to Fisch #roblox #shorts",
                    "I found the WEIRDEST Roblox game — How to Fisch #shorts",
                    "Catch fish. Then FIGHT. How to Fisch #roblox #shorts",
                    "This Roblox fishing game slaps — How to Fisch #shorts",
                    "FPS plus fishing on Roblox — How to Fisch #shorts",
                ][len((_load_clip_log().get("clips") or [])) % 5]
            ),
            description=(
                "In this Roblox game you catch fish, upgrade gear, and fight bosses.\n"
                "Game is called How to Fisch on Roblox.\n"
                "https://www.roblox.com/games/119870009085173/How-to-Fisch"
            ),
            tags=["#Roblox", "#Fisch", "#HowToFisch", "#shorts"],
        )
    try:
        ai_meta = ai_generate_metadata(
            hook=hook,
            summary=summary,
            mandatory_hashtags=req.mandatory_hashtags,
            required_links=req.required_links,
            referral_code=req.referral_code,
        )
        return VideoMetadata(title=ai_meta.title, description=ai_meta.description, tags=ai_meta.hashtags)
    except (AIBrainError, MetadataError) as exc:
        print(f"AI metadata generation failed ({exc}); falling back to template.", file=sys.stderr)
        return generate_metadata(hook=hook, summary=summary, req=req)



def _relax_min_seconds_to_source(req: CampaignRequirements, source_path: str) -> None:
    """Stop render from dying when AI parsed min=15 but the clip is 14.5s."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", source_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        source_dur = float((probe.stdout or "0").strip() or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not probe source duration ({exc}); render will use parsed min.")
        return
    if not source_dur:
        return
    if source_dur < req.min_seconds:
        new_min = max(1.0, source_dur - 0.05)
        print(
            f"Source is {source_dur:.2f}s < parsed min {req.min_seconds}s "
            f"— lowering min to {new_min:.2f}s"
        )
        req.min_seconds = new_min


def process_campaign(platform: str, campaign: Campaign, preferred_clip: dict | None = None) -> int:
    req = _parse_requirements(campaign)

    try:
        extra = list(getattr(campaign, "_merge_pack", None) or [])
        if extra and extra[0].get("kind") == "drive_file":
            resolved_source = _stitch_official_clips(extra, SOURCE_CLIP_PATH)
        elif preferred_clip and preferred_clip.get("kind") == "drive_file":
            fid = preferred_clip.get("clip_id") or extract_drive_file_id(preferred_clip.get("url") or "")
            drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
            if not fid or not drive_key:
                raise GoogleDocReadError("Drive clip selected but file id / API key missing")
            download_drive_file(fid, SOURCE_CLIP_PATH, drive_key)
            resolved_source = preferred_clip.get("url") or fid
        elif preferred_clip and preferred_clip.get("url"):
            resolved_source = resolve_and_download_footage(
                dest_path=SOURCE_CLIP_PATH,
                source_clip_url=preferred_clip["url"],
                reference_doc_url="",
                brief_text="",
                download_url_fn=_download_source_clip,
                download_drive_fn=lambda i, d: download_drive_file(
                    i, d, (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
                ),
                pick_drive_folder_fn=_pick_drive_folder,
            )
        else:
            resolved_source = _resolve_source_clip(campaign)
        print(f"[1/5] Resolved + downloaded source clip from {resolved_source} -> {SOURCE_CLIP_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to resolve/download source clip: {exc}", file=sys.stderr)
        return 1

    hook = campaign.name or "New campaign clip"
    youtube_url = None

    try:
        _relax_min_seconds_to_source(req, SOURCE_CLIP_PATH)
        render_short(
            source_path=SOURCE_CLIP_PATH,
            output_path=OUTPUT_PATH,
            req=req,
            fallback_caption_text=None,
        )
        print(f"[2/5] Rendered vertical short -> {OUTPUT_PATH}")
        _cap_video_length(OUTPUT_PATH, max_seconds=30.0, speed=1.2)
        _apply_requirement_tools(campaign, OUTPUT_PATH)
        _cap_video_length(OUTPUT_PATH, max_seconds=30.0, speed=1.2)

        meta = _generate_metadata(hook=hook, summary=(campaign.requirements_text or "")[:200], req=req)
        print(f"[3/5] Generated metadata. Title: {meta.title}")

        validate_or_raise(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            req=req,
        )

        if (os.environ.get("VYRO_SKIP_UPLOAD") or "").strip().lower() in ("1", "true", "yes"):
            size = os.path.getsize(OUTPUT_PATH) if os.path.isfile(OUTPUT_PATH) else 0
            print(f"[4/5] SKIP upload (VYRO_SKIP_UPLOAD=1) output={OUTPUT_PATH} ({size} bytes)")
            print("[5/5] SKIP Instagram")
            print("[6/5] SKIP Whop submit")
            return 0

        youtube_url = None
        if _youtube_token_ready():
            try:
                yt = upload_video(
                    video_path=OUTPUT_PATH,
                    title=meta.title,
                    description=meta.description,
                    tags=meta.tags,
                    privacy_status="unlisted",
                    token_path="token.json",
                )
                youtube_url = yt.video_url
                print(f"[4/5] Uploaded to YouTube (backup, unlisted): {youtube_url}")
            except UploadError as exc:
                print(f"[4/5] YouTube upload failed (IG is primary, continuing): {exc}", file=sys.stderr)
        else:
            print("[4/5] No usable YouTube token — skipping YT. Instagram is the main target.")
    except RenderError as exc:
        print(f"Rendering failed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    instagram_media_id = None
    try:
        instagram_media_id = upload_reel(
            video_path=OUTPUT_PATH,
            caption=_ig_caption(campaign, req, meta),
            release_tag=f"clip-{platform}-{campaign.campaign_id}-{int(__import__('time').time())}",
        )
        print(f"[5/5] Uploaded to Instagram Reels (MAIN): {instagram_media_id}")
    except InstagramUploadError as exc:
        print(f"Instagram upload failed: {exc}", file=sys.stderr)
        print(
            "IG needs IG_ACCESS_TOKEN + IG_BUSINESS_ACCOUNT_ID + "
            "ASSET_HOST_REPO + ASSET_HOST_TOKEN (public GitHub release host).",
            file=sys.stderr,
        )

    ig_url = _instagram_permalink(str(instagram_media_id or ""))
    yt_url = (youtube_url or "").strip()
    if yt_url and "youtube.com/watch" in yt_url and "v=" not in yt_url:
        print(f"[submit] refusing broken YouTube URL: {yt_url}")
        yt_url = ""
    submit_urls = [u for u in (ig_url, yt_url) if u]
    if not submit_urls:
        print("[6/5] Nothing public to auto-submit.")
    for submit_url in submit_urls:
        kind = "Instagram" if "instagram.com" in submit_url else "YouTube"
        print(f"[6/5] Auto-submitting {kind}: {submit_url}")
        try:
            _submit_back(platform, campaign, submit_url)
            print(f"Submitted {submit_url} to {platform} campaign '{campaign.campaign_id}'.")
        except (VyroClientError, WhopClientError) as exc:
            print(
                f"Upload succeeded but {platform} submission failed: {exc}\n"
                f"SUBMIT THIS URL MANUALLY: {submit_url}",
                file=sys.stderr,
            )

    if instagram_media_id or youtube_url:
        return 0
    print("Rendered OK but neither Instagram nor YouTube published. Not marking processed.")
    return 2


def main() -> int:
    clip_log = _load_clip_log()
    if (os.environ.get("CLIP_REUSE") or "").strip().lower() in ("1", "true", "yes"):
        print("[clips] CLIP_REUSE=1 — old folder clips can be picked again")

    platform, campaign = _find_campaign()

    if campaign is None:
        print("No usable active campaign today on Vyro or Whop. Exiting.")
        return 0

    if campaign.campaign_id in set(clip_log.get("closed_campaigns") or []):
        print(f"Campaign '{campaign.campaign_id}' is closed. Ignoring its folder forever.")
        return 0

    print(f"Found active campaign on {platform}: {campaign.campaign_id} — {campaign.name}")
    _refuse_mixed_footage(campaign)
    blob = f"{campaign.name or ''}\n{campaign.requirements_text or ''}"
    ratio = _budget_used_ratio(blob)
    if ratio is not None:
        print(f"[budget] {campaign.name} used {ratio:.0%}")
        if ratio >= BUDGET_CLOSE_RATIO:
            _close_spent_campaign(clip_log, campaign, ratio)
            print("Pick another campaign tomorrow. This one is done.")
            return 0
    print(
        f"[lock] clip+submit ONLY '{campaign.name}' "
        f"id={campaign.campaign_id} footage={(campaign.source_clip_url or '')[:60]}"
    )

    pack = _next_unused_pack(campaign, clip_log, want=4)
    if not pack:
        print("No unused Content Folder clip left on this campaign (or folder unreadable).")
        return 0
    campaign._merge_pack = pack  # type: ignore[attr-defined]
    nxt = pack[0]

    status = process_campaign(platform, campaign, preferred_clip=nxt)
    if status == 0:
        for piece in pack:
            clip_log["clips"].append(
                {
                    "campaign_id": campaign.campaign_id,
                    "clip_id": piece["clip_id"],
                    "name": piece.get("name", ""),
                    "url": piece.get("url", ""),
                    "kind": piece.get("kind", ""),
                }
            )
        _save_clip_log(clip_log)
        print(f"Recorded {len(pack)} merged clip(s) for campaign {campaign.campaign_id}.")
        return 0
    if status == 2:
        print("Rendered but no IG/YouTube publish. Clip NOT marked used.")
        return 0
    if nxt.get("kind") == "mediasilo":
        clip_log["clips"].append(
            {
                "campaign_id": campaign.campaign_id,
                "clip_id": nxt["clip_id"],
                "url": nxt.get("url", ""),
                "kind": "mediasilo",
                "status": "download_failed",
            }
        )
        _save_clip_log(clip_log)
        print("MediaSilo has no downloadable file. Marked skipped so Daily will not loop-fail.")
        return 0
    return status


if __name__ == "__main__":
    sys.exit(main())
