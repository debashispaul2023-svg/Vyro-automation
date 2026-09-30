"""Roll Anime Girls pack: gameplay-first, CTA only in the last 1.5–2s."""
from __future__ import annotations

import os
import re
import shutil
import subprocess

ANIME_SCRIPTS = [
    (
        "This plot just started printing money. "
        "I still had spins left. So I used them. "
        "You roll. You drop them on your plot. "
        "They keep earning. Even if you walk away."
    ),
    (
        "Look at the money on this plot. "
        "I dumped the rest of my spins. "
        "Roll. Place them. They start earning."
    ),
    (
        "I left the plot running. Came back to this. "
        "So I rolled again and dropped the next one on the pad. "
        "They print money while you play."
    ),
]
FULL_CTA = "Try Roll Anime Girls on Roblox."
HOOK_TEXT = "PRINTING MONEY"
CTA_TEXT = "Try Roll Anime Girls on Roblox"
ATEMPO = 1.08
CTA_HOLD = 1.85
BANNED_OPEN = (
    "wait. watch this",
    "today we're playing",
    "hey guys",
    "this roblox game",
    "roll anime girls is",
)


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


def _safe_draw(text: str, limit: int = 36) -> str:
    safe = re.sub(r"\s+", " ", (text or "").replace("\\", " ").replace("\n", " ").replace("'", ""))
    return safe.replace(":", " -")[:limit]


def _punch_open(ns: dict, video_path: str) -> None:
    """First 3s: at least 2 hard visual changes from different timestamps."""
    probe = ns["_probe_dur"]
    dur = probe(video_path)
    if dur < 8:
        return
    os.makedirs("work/open", exist_ok=True)
    slices = [
        (0.05, 0.95),
        (min(4.2, dur - 3.2), 0.95),
        (min(8.4, dur - 2.1), 0.95),
    ]
    parts = []
    for i, (ss, ln) in enumerate(slices):
        out = f"work/open/cut_{i}.mp4"
        z = 1.0 + (0.08 * i)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{ss:.2f}", "-t", f"{ln:.2f}", "-i", video_path,
                    "-vf", f"scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,zoompan=z='{z}':d=1:s=1080x1920",
                    "-r", "30", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", out,
                ],
                check=True, capture_output=True, timeout=40,
            )
            if os.path.isfile(out) and os.path.getsize(out) > 1000:
                parts.append(out)
        except Exception as exc:
            print(f"[open] slice {i} skipped: {exc}")
    if len(parts) < 2:
        print("[open] not enough punches — keep source")
        return
    rest = "work/open/rest.mp4"
    opened = "work/open/opened.mp4"
    lst = "work/open/list.txt"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "2.90", "-i", video_path, "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "20", "-c:a", "aac", rest],
            check=True, capture_output=True, timeout=90,
        )
        with open(lst, "w", encoding="utf-8") as f:
            for p in parts:
                f.write(f"file '{os.path.abspath(p)}'\n")
            if os.path.isfile(rest):
                f.write(f"file '{os.path.abspath(rest)}'\n")
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", opened],
            check=True, capture_output=True, timeout=90,
        )
        if os.path.isfile(opened) and os.path.getsize(opened) > 1000:
            shutil.move(opened, video_path)
            print(f"[open] {len(parts)} visual changes in first 3s")
    except Exception as exc:
        print(f"[open] rebuild skipped: {exc}")


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
        # Body + small gap + 1.85s CTA. Do not stretch dead air.
        target = min(22.0, max(body_d + extra, 15.0))
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
            if any(b in spoken.lower() for b in BANNED_OPEN):
                spoken = ANIME_SCRIPTS[0]
            print(f"[voice] ANIME gameplay-first variant {used_n % len(ANIME_SCRIPTS) + 1}")
            _punch_open(ns, video_path)
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
    _apad(body_wav, 0.25)
    cta_ok = tts_spoken(FULL_CTA, cta_wav)
    _apad(cta_wav, 0.35)
    tts_spoken.last_words = body_words
    if not body_ok:
        raise RuntimeError("ElevenLabs voice required but failed — refusing silent video")

    body_d = probe(body_wav) if os.path.isfile(body_wav) else 0.0
    cta_d = probe(cta_wav) if cta_ok and os.path.isfile(cta_wav) else 2.0
    dur = fit(video_path, body_wav, extra=CTA_HOLD + 0.35)
    # CTA window is ONLY the last 1.5–2s — never earlier.
    cta_a = max(0.0, dur - CTA_HOLD)
    if body_d > cta_a - 0.12 and os.path.isfile(body_wav):
        trimmed = body_wav + ".pre_cta.wav"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", body_wav, "-t", f"{max(2.0, cta_a - 0.12):.2f}", trimmed],
                check=True, capture_output=True, timeout=20,
            )
            if os.path.isfile(trimmed):
                shutil.move(trimmed, body_wav)
                body_d = probe(body_wav)
                print(f"[voice] body cut to {body_d:.1f}s so CTA owns the last {CTA_HOLD:.1f}s")
        except Exception as exc:
            print(f"[voice] body trim skipped: {exc}")
    tts_ok = layout_voice(body_wav, "", tts, dur)
    if not tts_ok:
        shutil.copy(body_wav, tts)
        tts_ok = True

    words = list(getattr(tts_spoken, "last_words", []) or [])
    groups = phrases_from_words(words, spoken) or caption_groups(words, spoken)
    label_map = [
        ("printing money", "PRINTING MONEY"),
        ("look at the money", "PRINTING MONEY"),
        ("spins left", "SPINS LEFT"),
        ("dumped the rest", "SPINS LEFT"),
        ("you roll", "ROLL"),
        ("roll. place", "ROLL"),
        ("rolled again", "ROLL"),
        ("drop them", "ON THE PLOT"),
        ("dropped the next", "ON THE PLOT"),
        ("keep earning", "THEY EARN"),
        ("start earning", "THEY EARN"),
        ("print money", "THEY EARN"),
    ]
    labeled = []
    seen = set()
    for a, b, line in groups:
        low = (line or "").lower()
        tag = ""
        for needle, cap in label_map:
            if needle in low:
                tag = cap
                break
        if not tag or tag in seen:
            continue
        seen.add(tag)
        labeled.append((a, min(b, a + 1.15), tag))
    groups = labeled

    print(f"[pack] voice_end={body_d:.1f}s cta_from={cta_a:.1f}s hold={CTA_HOLD:.1f}s video={dur:.1f}s")
    print(f"[pack] hook={HOOK_TEXT!r} cta={FULL_CTA!r}")

    parts = []
    hook_b = 0.90
    parts.append(
        f"drawtext={font}text='{_safe_draw(HOOK_TEXT, 22)}':fontcolor=white:fontsize=60:"
        f"borderw=5:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.16:enable='between(t,0,{hook_b:.2f})'"
    )
    for a, b, txt in groups:
        if b <= a or not txt:
            continue
        if a < hook_b:
            a = hook_b
        if a >= cta_a - 0.10:
            continue
        b = min(b, cta_a - 0.10, a + 1.2)
        if b <= a:
            continue
        parts.append(
            f"drawtext={font}text='{_safe_draw(txt, 18)}':fontcolor=white:fontsize=48:"
            f"borderw=4:bordercolor=black:"
            f"x=(w-text_w)/2:y=h*0.80:enable='between(t,{a:.2f},{b:.2f})'"
        )
    # CTA text ONLY in the final 1.5–2s — then gone with the video.
    parts.append(
        f"drawtext={font}text='{_safe_draw(CTA_TEXT, 40)}':fontcolor=white:fontsize=44:"
        f"borderw=5:bordercolor=black:"
        f"x=(w-text_w)/2:y=h*0.74:enable='between(t,{cta_a:.2f},{dur:.2f})'"
    )
    caption_vf = ",".join(parts) if parts else "null"
    draw = caption_vf
    en = f"gte(t,{cta_a:.2f})"
    icon = next((p for p in ("output/game_icon.png", "output/game_icon.jpg") if os.path.isfile(p)), "")
    if icon:
        draw = (
            f"[0:v]{draw}[base];"
            f"[1:v]scale=780:780:force_original_aspect_ratio=decrease,"
            f"pad=800:800:(ow-iw)/2:(oh-ih)/2:white[ic];"
            f"[base][ic]overlay=(W-w)/2:H-h-110:enable='{en}'[v]"
        )
        print(f"[pack] icon+CTA only {cta_a:.1f}s–{dur:.1f}s")
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
            print("[pack] gameplay-first + last-window CTA burned in")
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
