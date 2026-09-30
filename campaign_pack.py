"""Voice/caption pack override. Long script, full final CTA, no mid-cut."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

# Body does NOT say the required last line. That line is spoken once, in full, as CTA.
ANIME_SCRIPTS = [
    (
        "WAIT. Watch this Roblox game. You spin the wheel. You unlock a character. "
        "You place them on your plot. They make money while you play. "
        "Buy potions for better luck. Then rebirth for a permanent boost. "
        "That is the whole loop. Keep rolling. Keep building."
    ),
    (
        "You roll. You unlock a character. You place them on your plot. "
        "They make money. Buy potions for better luck. Then rebirth for a permanent boost. "
        "Rare pulls change your whole base. Keep rolling. Keep building."
    ),
    (
        "This Roblox game is an RNG tycoon. Over two hundred characters to roll. "
        "Place them on your plot and they earn. Upgrade luck. Rebirth. Climb faster. "
        "Keep rolling. Keep building."
    ),
]
FULL_CTA = "Game is called Roll Anime Girls on Roblox."
ATEMPO = 1.08


def _apad(path: str, extra: float = 0.45) -> None:
    if not path or not os.path.isfile(path):
        return
    tmp = path + ".pad.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-af", f"apad=pad_dur={extra:.2f}", tmp],
            check=True, capture_output=True, timeout=30,
        )
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


def install(ns: dict) -> None:
    host_apply = ns.get("_apply_campaign_pack")
    if not callable(host_apply):
        print("[voice] host pack missing — override skipped")
        return

    def _fit_video_to_voice(video_path: str, body_wav: str, extra: float = 3.2) -> float:
        probe = ns["_probe_dur"]
        body_d = probe(body_wav) if body_wav and os.path.isfile(body_wav) else 0.0
        dur = probe(video_path)
        if body_d < 2 or dur <= 0:
            return dur
        target = min(30.0, max(body_d + extra, 14.0))
        if dur <= target + 0.2:
            print(f"[voice] keep {dur:.1f}s video; voice {body_d:.1f}s")
            return max(dur, target)
        work = video_path + ".voicefit.mp4"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", video_path, "-t", f"{target:.2f}",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", work],
                check=True, capture_output=True, timeout=90,
            )
            if os.path.isfile(work) and os.path.getsize(work) > 1000:
                shutil.move(work, video_path)
                print(f"[voice] trimmed {dur:.1f}s -> {target:.1f}s to fit full CTA")
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
            print(f"[voice] ANIME long script variant {used_n % len(ANIME_SCRIPTS) + 1}")
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
    hook_line = ns["_hook_line"]
    phrases_from_words = ns.get("phrases_from_words")
    caption_groups = ns["_caption_groups"]
    layout_voice = ns["_layout_voice"]
    fit = ns["_fit_video_to_voice"]
    assert_voice = ns.get("_assert_voice_audio")
    from tts_engine import phrases_from_words as _pfw
    if phrases_from_words is None:
        phrases_from_words = _pfw

    body_wav = "output/fisch_body.wav"
    cta_wav = "output/fisch_cta.wav"
    tts = "output/fisch_tts.wav"
    work = "output/fisch_pack.mp4"
    os.makedirs("output", exist_ok=True)

    tts_spoken.last_words = []
    body_ok = tts_spoken(spoken, body_wav)
    body_words = _scale_words(list(getattr(tts_spoken, "last_words", []) or []), ATEMPO)
    _apad(body_wav, 0.35)
    cta_ok = tts_spoken(FULL_CTA, cta_wav)
    _apad(cta_wav, 0.55)
    tts_spoken.last_words = body_words
    if not body_ok:
        raise RuntimeError("ElevenLabs voice required but failed — refusing silent video")

    body_d = probe(body_wav) if os.path.isfile(body_wav) else 0.0
    cta_d = probe(cta_wav) if cta_ok and os.path.isfile(cta_wav) else 2.6
    need = body_d + max(cta_d, 2.4) + 0.25
    dur = fit(video_path, body_wav, extra=max(3.2, cta_d + 0.4))
    if dur < need:
        dur = need
        print(f"[voice] extend timeline to {dur:.1f}s so CTA can finish")
    tts_ok = layout_voice(body_wav, "", tts, dur)
    if not tts_ok:
        shutil.copy(body_wav, tts)
        tts_ok = True

    words = list(getattr(tts_spoken, "last_words", []) or [])
    groups = phrases_from_words(words, spoken) or caption_groups(words, spoken)
    flat = []
    for a, b, line in groups:
        parts = [p for p in re.split(r"\s+", line) if p]
        if len(parts) <= 4:
            flat.append((a, b, " ".join(parts)))
            continue
        span = max(0.4, (b - a) / max(1, (len(parts) + 3) // 4))
        t = a
        for i in range(0, len(parts), 4):
            chunk = parts[i:i + 4]
            flat.append((t, min(b, t + span), " ".join(chunk)))
            t += span
    groups = flat

    voice_end = probe(tts) if os.path.isfile(tts) else body_d
    # CTA starts AFTER the body finishes — never overlap the last words.
    cta_a = max(voice_end + 0.12, 3.0)
    if cta_a + cta_d + 0.2 > dur:
        dur = min(30.0, cta_a + cta_d + 0.25)
    hook_b = min(1.15, max(0.9, cta_a - 0.2))
    print(f"[pack] voice_end={voice_end:.1f}s cta_from={cta_a:.1f}s cta_dur={cta_d:.1f}s video={dur:.1f}s")

    hook_txt = hook_line(blob)
    parts = []
    safe_hook = hook_txt.replace("\\", " ").replace("'", "").replace(":", " -")[:36]
    parts.append(
        f"drawtext={font}text='{safe_hook}':fontcolor=white:fontsize=72:"
        f"borderw=6:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.18:enable='between(t,0,{hook_b:.2f})'"
    )
    skip_words = {"yo", "yo.", "wait", "wait.", "look", "look.", "this"}
    for a, b, txt in groups:
        if b <= a or not txt:
            continue
        if a < hook_b:
            a = hook_b
        if a >= cta_a - 0.05:
            continue
        b = min(b, cta_a - 0.05)
        if b <= a:
            continue
        raw_txt = re.sub(r"[^a-zA-Z ]+", "", txt).strip().lower()
        if raw_txt in skip_words:
            continue
        safe = re.sub(r"\s+", " ", txt.replace("\\", " ").replace("\n", " ").replace("'", ""))
        safe = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", safe).replace(":", " -")[:42]
        parts.append(
            f"drawtext={font}text='{safe}':fontcolor=white:fontsize=56:"
            f"borderw=5:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.72:enable='between(t,{a:.2f},{b:.2f})'"
        )
    # Full required line on screen for the whole CTA window.
    parts.append(
        f"drawtext={font}text='Game is called Roll Anime Girls on Roblox':fontcolor=0x001033:fontsize=52:"
        f"box=1:boxcolor=0xB8FF00@0.95:boxborderw=18:"
        f"x=(w-text_w)/2:y=h*0.70:enable='between(t,{cta_a:.2f},{dur:.2f})'"
    )
    caption_vf = ",".join(parts) if parts else "null"
    draw = caption_vf
    end_at = max(cta_a, dur - 2.2)
    en = f"gte(t,{end_at:.2f})"
    icon = next((p for p in ("output/game_icon.png", "output/game_icon.jpg") if os.path.isfile(p)), "")
    if icon:
        draw = (
            f"[0:v]{draw}[base];"
            f"[1:v]scale=820:820:force_original_aspect_ratio=decrease,"
            f"pad=840:840:(ow-iw)/2:(oh-ih)/2:white[ic];"
            f"[base][ic]overlay=(W-w)/2:H-h-90:enable='{en}'[v]"
        )
    cta_ms = int(max(0.0, cta_a) * 1000)
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
    a_tts = 2 if icon else 1
    a_cta = a_tts + 1
    if icon and tts_ok:
        audio = f"[0:a]volume=0.12[ag];[{a_tts}:a]volume=2.1[ab];"
        if extra_a >= 2:
            audio += (
                f"[{a_cta}:a]adelay={cta_ms}|{cta_ms},volume=2.0[ac];"
                "[ag][ab][ac]amix=inputs=3:duration=longest:dropout_transition=0:normalize=0[a]"
            )
        else:
            audio += "[ag][ab]amix=inputs=2:duration=longest:dropout_transition=0[a]"
        ff += ["-filter_complex", draw + ";" + audio, "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
               "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.2f}", work]
    elif tts_ok:
        audio = "[0:a]volume=0.12[ag];[1:a]volume=2.1[ab];"
        if extra_a >= 2:
            audio += (
                f"[2:a]adelay={cta_ms}|{cta_ms},volume=2.0[ac];"
                "[ag][ab][ac]amix=inputs=3:duration=longest:dropout_transition=0[a]"
            )
        else:
            audio += "[ag][ab]amix=inputs=2:duration=longest:dropout_transition=0[a]"
        ff += ["-filter_complex", f"[0:v]{draw}[v];" + audio, "-map", "[v]", "-map", "[a]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
               "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", "-t", f"{dur:.2f}", work]
    else:
        ff += ["-vf", draw, "-c:a", "copy", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", work]
    try:
        subprocess.run(ff, check=True, capture_output=True, timeout=300)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[pack] long voice + full CTA burned in")
        else:
            raise RuntimeError("pack output missing")
    except Exception as exc:
        print(f"[pack] full filter failed ({exc}); retry captions+voice only")
        simple = ["ffmpeg", "-y", "-i", video_path]
        if tts_ok:
            simple += ["-i", tts, "-filter_complex",
                       f"[0:v]{caption_vf}[v];[0:a]volume=0.12[a0];[1:a]volume=2.1[a1];"
                       "[a0][a1]amix=inputs=2:duration=longest:dropout_transition=0[a]",
                       "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                       "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2", work]
        else:
            simple += ["-vf", caption_vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "copy", work]
        subprocess.run(simple, check=True, capture_output=True, timeout=300)
        if os.path.isfile(work) and os.path.getsize(work) > 1000:
            shutil.move(work, video_path)
            print("[pack] captions + voice burned (no icon)")
        else:
            raise RuntimeError("simple pack missing")
    if tts_ok and callable(assert_voice):
        assert_voice(video_path)
