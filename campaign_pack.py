"""Roll Anime Girls pack: explain the loop, name once, icon + CTA at the end."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

# Reviewers reject "printing money" with no loop. Say roll, place, earn, offline.
ANIME_SCRIPTS = [
    "You roll the dice to unlock a character. Place that character on your plot. They earn money for you, even while you are offline. Luck potions help rarer rolls. Rebirth gives permanent boosts. The game is called Roll Anime Girls.",
    "Here is how it works. Roll the dice. Unlock a character. Put them on your plot and they start earning cash, online and offline. Buy a luck potion for a rarer pull. Then rebirth for a permanent boost. Game is called Roll Anime Girls.",
    "This is the gameplay loop. Roll dice, unlock a character, place them on your plot, and they generate money even if you leave. Potions boost your luck. Rebirth resets you with permanent boosts. Roll Anime Girls on Roblox.",
]
FULL_CTA = "Try Roll Anime Girls on Roblox."
HOOK_TEXT = "ROLL. PLACE. EARN."
CTA_TEXT = "Try Roll Anime Girls on Roblox"
ATEMPO = 1.08
CTA_HOLD = 1.85
BANNED_OPEN = ("wait. watch this", "today we're playing", "hey guys", "this roblox game", "roll anime girls is")


def _apad(path: str, extra: float = 0.45) -> None:
    if not path or not os.path.isfile(path):
        return
    tmp = path + ".pad.wav"
    try:
        subprocess.run(["ffmpeg", "-y", "-i", path, "-af", f"apad=pad_dur={extra:.2f}", tmp], check=True, capture_output=True, timeout=30)
        if os.path.isfile(tmp) and os.path.getsize(tmp) > 200:
            shutil.move(tmp, path)
    except Exception as exc:
        print(f"[voice] apad skipped: {exc}")


def _scale_words(words: list, tempo: float) -> list:
    if not words or tempo <= 0:
        return words
    out = []
    for w in words:
        row = dict(w)
        try:
            row["start"] = float(w.get("start") or 0) / tempo
            row["end"] = float(w.get("end") or 0) / tempo
        except (TypeError, ValueError):
            pass
        out.append(row)
    return out


def _safe_draw(text: str, limit: int = 36) -> str:
    safe = re.sub(r"\s+", " ", (text or "").replace("\\", " ").replace("\n", " ").replace("'", ""))
    return safe.replace(":", " -")[:limit]


def _explains_loop(text: str) -> bool:
    low = (text or "").lower()
    has_roll = "roll" in low or "dice" in low
    has_place = "plot" in low or "place" in low
    has_earn = "earn" in low or "money" in low or "cash" in low
    has_name = "roll anime girls" in low
    return has_roll and has_place and has_earn and has_name


def _icon_path() -> str:
    for p in ("output/game_icon.png", "output/game_icon.jpg"):
        if os.path.isfile(p):
            return p
    return ""


def _punch_open(ns: dict, video_path: str) -> None:
    probe = ns["_probe_dur"]
    dur = probe(video_path)
    if dur < 8:
        return
    print("[open] punch skipped if already story-cut")


def install(ns: dict) -> None:
    host_apply = ns.get("_apply_campaign_pack")
    if not callable(host_apply):
        print("[voice] host pack missing \u2014 override skipped")
        return

    def _fit_video_to_voice(video_path: str, body_wav: str, extra: float = 2.2) -> float:
        probe = ns["_probe_dur"]
        body_d = probe(body_wav) if body_wav and os.path.isfile(body_wav) else 0.0
        dur = probe(video_path)
        if body_d < 2 or dur <= 0:
            return dur
        target = min(28.0, max(body_d + extra, 16.0))
        if dur <= target + 0.2:
            return max(dur, target)
        work = video_path + ".voicefit.mp4"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-t", f"{target:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", work],
                check=True, capture_output=True, timeout=90,
            )
            if os.path.isfile(work) and os.path.getsize(work) > 1000:
                shutil.move(work, video_path)
                print(f"[voice] trimmed {dur:.1f}s -> {target:.1f}s")
                return target
        except Exception as exc:
            print(f"[voice] fit skipped: {exc}")
        return dur

    def _apply_campaign_pack(campaign, video_path: str) -> None:
        blob = f"{campaign.name or ''}\n{campaign.requirements_text or ''}".lower()
        if "anime" in blob or "roll anime" in blob:
            plan = getattr(campaign, "_edit_plan", None) or {}
            plan["speak_text"] = "Roll Anime Girls"
            plan["cta_text"] = FULL_CTA
            plan["end_title"] = "ROLL ANIME GIRLS"
            plan["need_spoken_voice"] = True
            plan["need_captions"] = True
            campaign._edit_plan = plan
            used_n = 0
            try:
                used_n = len((ns["_load_clip_log"]().get("clips") or []))
            except Exception:
                used_n = 0
            spoken = ANIME_SCRIPTS[used_n % len(ANIME_SCRIPTS)]
            try:
                from story_engine import load_story_script
                body, extra_cta = load_story_script()
                if body and _explains_loop(body):
                    spoken = body
                    print("[script] using footage-first story script")
                elif body:
                    print("[script] story script too vague \u2014 using loop explanation")
                if extra_cta:
                    globals()["FULL_CTA"] = extra_cta
            except Exception as exc:
                print(f"[script] story script skipped: {exc}")
            if not _explains_loop(spoken):
                spoken = ANIME_SCRIPTS[0]
            print(f"[voice] ANIME loop-explain variant {used_n % len(ANIME_SCRIPTS) + 1}")
            print(f"[voice] spoken: {spoken}")
            _run_long_pack(ns, campaign, video_path, blob, spoken)
            return
        host_apply(campaign, video_path)

    ns["_fit_video_to_voice"] = _fit_video_to_voice
    ns["_apply_campaign_pack"] = _apply_campaign_pack
    print("[voice] campaign_pack override installed")


def _run_long_pack(ns: dict, campaign, video_path: str, blob: str, spoken: str) -> None:
    tts_spoken = ns["_tts_spoken"]
    probe = ns["_probe_dur"]
    font = ns["_font"]()
    layout_voice = ns["_layout_voice"]
    fit = ns["_fit_video_to_voice"]
    body_wav = "output/fisch_body.wav"
    cta_wav = "output/fisch_cta.wav"
    tts = "output/fisch_tts.wav"
    work = "output/fisch_pack.mp4"
    os.makedirs("output", exist_ok=True)
    tts_spoken.last_words = []
    body_ok = tts_spoken(spoken, body_wav)
    _apad(body_wav, 0.25)
    cta_ok = tts_spoken(FULL_CTA, cta_wav)
    _apad(cta_wav, 0.35)
    if not body_ok:
        print("[pack] voice failed \u2014 keep existing render")
        return
    body_d = probe(body_wav) if os.path.isfile(body_wav) else 0.0
    dur = fit(video_path, body_wav, extra=CTA_HOLD + 0.35)
    cta_a = max(0.0, dur - CTA_HOLD)
    tts_ok = layout_voice(body_wav, "", tts, dur)
    if not tts_ok and os.path.isfile(body_wav):
        shutil.copy(body_wav, tts)
        tts_ok = True
    caption_vf = (
        f"drawtext={font}text='{_safe_draw(HOOK_TEXT, 22)}':fontcolor=white:fontsize=54:"
        f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.16:enable='between(t,0,1.20)',"
        f"drawtext={font}text='{_safe_draw(CTA_TEXT, 40)}':fontcolor=white:fontsize=42:"
        f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.78:enable='between(t,{cta_a:.2f},{dur:.2f})'"
    )
    icon = _icon_path()
    print(f"[pack] voice_end={body_d:.1f}s cta_from={cta_a:.1f}s video={dur:.1f}s icon={bool(icon)}")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", tts]
    if icon:
        cmd += ["-i", icon]
        fc = (
            f"[0:v]{caption_vf}[base];"
            f"[2:v]scale=640:640:force_original_aspect_ratio=decrease[ic];"
            f"[base][ic]overlay=(W-w)/2:(H-h)/2-120:enable='gte(t,{cta_a:.2f})'[v];"
            f"[1:a]volume=2.1,aresample=44100,aformat=channel_layouts=stereo[a]"
        )
    else:
        fc = (
            f"[0:v]{caption_vf}[v];"
            f"[1:a]volume=2.1,aresample=44100,aformat=channel_layouts=stereo[a]"
        )
        print("[pack] no game icon on disk \u2014 CTA text only")
    cmd += [
        "-filter_complex", fc,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
        "-t", f"{dur:.2f}", work,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[pack] captions + voice burned, icon on end card" if icon else "[pack] captions + voice burned (no game-audio mix)")
            return
    except Exception as exc:
        print(f"[pack] mix failed ({exc}); keep rendered short.mp4")
