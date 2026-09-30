"""Roll Anime Girls pack: gameplay-first, CTA only in the last 1.5-2s."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

ANIME_SCRIPTS = [
    "This plot just started printing money. I still had spins left. So I used them. You roll. You drop them on your plot. They keep earning. Even if you walk away.",
    "Look at the money on this plot. I dumped the rest of my spins. Roll. Place them. They start earning.",
    "I left the plot running. Came back to this. So I rolled again and dropped the next one on the pad. They print money while you play.",
]
FULL_CTA = "Try Roll Anime Girls on Roblox."
HOOK_TEXT = "PRINTING MONEY"
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


def _punch_open(ns: dict, video_path: str) -> None:
    probe = ns["_probe_dur"]
    dur = probe(video_path)
    if dur < 8:
        return
    print("[open] punch skipped if already story-cut")


def install(ns: dict) -> None:
    host_apply = ns.get("_apply_campaign_pack")
    if not callable(host_apply):
        print("[voice] host pack missing — override skipped")
        return

    def _fit_video_to_voice(video_path: str, body_wav: str, extra: float = 2.2) -> float:
        probe = ns["_probe_dur"]
        body_d = probe(body_wav) if body_wav and os.path.isfile(body_wav) else 0.0
        dur = probe(video_path)
        if body_d < 2 or dur <= 0:
            return dur
        target = min(22.0, max(body_d + extra, 15.0))
        if dur <= target + 0.2:
            return max(dur, target)
        work = video_path + ".voicefit.mp4"
        try:
            subprocess.run(["ffmpeg", "-y", "-i", video_path, "-t", f"{target:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", work], check=True, capture_output=True, timeout=90)
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
                if body:
                    spoken = body
                    print("[script] using footage-first story script")
                if extra_cta:
                    globals()["FULL_CTA"] = extra_cta
            except Exception as exc:
                print(f"[script] story script skipped: {exc}")
            print(f"[voice] ANIME gameplay-first variant {used_n % len(ANIME_SCRIPTS) + 1}")
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
        print("[pack] voice failed — keep existing render")
        return
    body_d = probe(body_wav) if os.path.isfile(body_wav) else 0.0
    dur = fit(video_path, body_wav, extra=CTA_HOLD + 0.35)
    cta_a = max(0.0, dur - CTA_HOLD)
    tts_ok = layout_voice(body_wav, "", tts, dur)
    if not tts_ok and os.path.isfile(body_wav):
        shutil.copy(body_wav, tts)
        tts_ok = True
    caption_vf = (
        f"drawtext={font}text='{_safe_draw(HOOK_TEXT, 22)}':fontcolor=white:fontsize=60:"
        f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.16:enable='between(t,0,0.90)',"
        f"drawtext={font}text='{_safe_draw(CTA_TEXT, 40)}':fontcolor=white:fontsize=44:"
        f"borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.74:enable='between(t,{cta_a:.2f},{dur:.2f})'"
    )
    print(f"[pack] voice_end={body_d:.1f}s cta_from={cta_a:.1f}s video={dur:.1f}s")
    cmd = ["ffmpeg", "-y", "-i", video_path, "-i", tts, "-filter_complex",
           f"[0:v]{caption_vf}[v];[1:a]volume=2.1,aresample=44100,aformat=channel_layouts=stereo[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
           "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.2f}", work]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[pack] captions + voice burned (no game-audio mix)")
            return
    except Exception as exc:
        print(f"[pack] mix failed ({exc}); keep rendered short.mp4")
