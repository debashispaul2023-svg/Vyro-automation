"""studio_editor.py - the Short editor (one-word captions, blur-fill 9:16, SFX + music bed).

Key rule that fixes "voice stops early": the VIDEO LENGTH IS DERIVED FROM THE VOICE.
  video_length = voice_end / VO_TEMPO + TAIL
The source is cut into speed-ramped windows (busiest parts, by motion) that fill exactly that length.
No freeze-frame, no minimum padding, so the voice always covers the whole video.

Entry point: render_campaign_short(source, dest, campaign=..., req=...) -> dict(ok=...).
Returns ok=False (never raises) so the caller can fall back to the old renderer.
"""
from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SFX_DIR = HERE / "assets" / "sfx"
FONT_DIR = HERE / "assets" / "fonts"
PROFILES_PATH = HERE / "game_profiles.json"

VO_TEMPO = float(os.environ.get("STUDIO_VO_TEMPO", "1.08"))
TAIL = 0.35            # seconds of picture after the last spoken word
MAX_SPEED_TOTAL = 2.6  # never compress the source more than this on average
SPEED_PATTERN = [1.0, 1.7, 1.25, 1.9, 1.4]
SFX_GAIN = {"intro": 0.55, "riser": 0.5, "pop": 0.6, "flash": 0.5, "boom": 0.75, "bang": 0.55}
MUSIC_RMS_DB = -28.0   # music bed level (RMS), ducked further under the voice
RED = r"{\c&H004B4BFF&}"
WHITE = r"{\c&HFFFFFF&}"


def run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(path: str) -> dict:
    p = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
             "-of", "json", path], 30)
    data = json.loads(p.stdout or "{}")
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


# ---------------------------------------------------------------- script / profile

def load_profiles() -> dict:
    try:
        return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[studio] profiles unreadable ({exc}) - generic only")
        return {"_generic": {"hashtags": ["#roblox"], "scripts": [["[{name}] is out now on Roblox.", "Game is called [{name}.]"]]}}


def find_profile(game_name: str, brief: str = "") -> tuple[str, dict]:
    profiles = load_profiles()
    blob = f"{game_name}\n{brief[:400]}".lower()
    for key, prof in profiles.items():
        if key.startswith("_"):
            continue
        if key in blob:
            return prof.get("name") or game_name, prof
    gen = profiles.get("_generic") or {}
    return (game_name or "this game").strip(), gen


def pick_script(profile: dict, name: str, index: int) -> list[str]:
    scripts = profile.get("scripts") or [["[{name}] is out now on Roblox.", "Game is called [{name}.]"]]
    lines = scripts[index % len(scripts)]
    return [ln.replace("{name}", name) for ln in lines]


def strip_markup(lines: list[str]) -> str:
    return " ".join(re.sub(r"[\[\]]", "", ln).strip() for ln in lines).strip()


def keyword_set(lines: list[str]) -> set[str]:
    kws: set[str] = set()
    for ln in lines:
        for chunk in re.findall(r"\[([^\]]+)\]", ln):
            for w in chunk.split():
                kws.add(re.sub(r"[^a-z0-9]", "", w.lower()))
    kws.discard("")
    return kws


# ---------------------------------------------------------------- timeline planning

def atempo_chain(speed: float) -> str:
    if abs(speed - 1.0) < 1e-3:
        return ""
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.4f}")
    return "," + ",".join(parts)


def motion_profile(src: str, duration: float) -> list[float]:
    """Per-second motion score (mean luma difference). Falls back to flat."""
    n = max(1, int(math.ceil(duration)))
    scores = [0.0] * n
    counts = [0] * n
    try:
        p = run(["ffmpeg", "-v", "error", "-i", src, "-an", "-vf",
                 "fps=4,scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-",
                 "-f", "null", "-"], 180)
        t = None
        for line in (p.stdout or "").splitlines():
            m = re.search(r"pts_time:([0-9.]+)", line)
            if m:
                t = float(m.group(1))
                continue
            m = re.search(r"YDIF=([0-9.]+)", line)
            if m and t is not None:
                i = min(n - 1, int(t))
                scores[i] += float(m.group(1))
                counts[i] += 1
        scores = [s / c if c else 0.0 for s, c in zip(scores, counts)]
    except Exception as exc:  # noqa: BLE001
        print(f"[studio] motion scan skipped: {exc}")
    return scores


def plan_segments(source_dur: float, target: float, motion: list[float] | None = None) -> list[tuple[float, float, float]]:
    """Return [(src_start, src_end, speed)] whose output durations sum to exactly `target`."""
    lo = 0.3
    usable = max(0.0, source_dur - lo - 0.2)
    if usable < 1.0:
        return []
    k = max(3, int(round(target / 2.2)))
    out_d = target / k
    mults = [SPEED_PATTERN[i % len(SPEED_PATTERN)] for i in range(k)]
    mean_m = sum(mults) / k
    budget = min(usable, target * MAX_SPEED_TOTAL) * 0.98
    base = budget / (target * mean_m)
    if base * min(mults) < 0.55:
        return []                       # source far too short for this voice
    speeds = [max(0.7, base * m) for m in mults]
    src_len = [out_d * s for s in speeds]
    total_src = sum(src_len)
    segs: list[tuple[float, float, float]] = []
    if total_src >= usable * 0.85:
        t0 = lo                       # nearly the whole clip: keep it continuous
        for ln, sp in zip(src_len, speeds):
            end = min(t0 + ln, source_dur - 0.1)
            segs.append((t0, end, (end - t0) / out_d))
            t0 = end
        return segs
    slot = usable / k
    prev_end = lo
    for i, (ln, sp) in enumerate(zip(src_len, speeds)):
        s_lo, s_hi = lo + i * slot, lo + (i + 1) * slot
        best_start, best_score = None, -1.0
        for c in range(5):
            start = s_lo + (max(0.0, slot - ln) * c / 4 if slot > ln else (slot - ln) / 2)
            start = max(prev_end - 0.0, min(start, source_dur - 0.1 - ln))
            if motion:
                score = sum(motion[min(len(motion) - 1, int(x))] for x in
                            [start + ln * j / 4 for j in range(5)])
            else:
                score = 0.0
            if score > best_score:
                best_score, best_start = score, start
        start = max(lo, best_start)
        end = min(start + ln, source_dur - 0.1)
        segs.append((start, end, (end - start) / out_d))
        prev_end = end
    return segs


def segs_output_duration(segs: list[tuple[float, float, float]]) -> float:
    return sum((b - a) / sp for a, b, sp in segs)


# ---------------------------------------------------------------- captions

def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def words_for_captions(text: str, words: list[dict], voice_end: float) -> list[dict]:
    """Word timings from ElevenLabs alignment; proportional fallback if missing."""
    toks = text.split()
    if words and len(words) >= len(toks) * 0.8:
        return [{"text": w["text"], "start": float(w["start"]), "end": float(w["end"])} for w in words]
    total = sum(len(t) + 2 for t in toks) or 1
    t0, out = 0.1, []
    span = max(0.5, voice_end - 0.1)
    for tok in toks:
        d = span * (len(tok) + 2) / total
        out.append({"text": tok, "start": t0, "end": t0 + d})
        t0 += d
    return out


CAPTION_FONT = int(os.environ.get("STUDIO_CAPTION_SIZE") or 66)     # small, not a giant overlay
CAPTION_Y = int(os.environ.get("STUDIO_CAPTION_Y") or 1420)         # bottom third, under the picture
FG_CROP = min(1.0, max(0.6, float(os.environ.get("STUDIO_FG_CROP") or 0.75)))   # share of the frame width kept sharp
MAX_SHORT_SECONDS = float(os.environ.get("STUDIO_MAX_SECONDS") or 24.0)         # campaigns want under 25s


def write_ass(path: str, words: list[dict], keywords: set[str], tempo: float, ypos: int = 0) -> int:
    ypos = ypos or CAPTION_Y
    hdr = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
           "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
           "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
           "Style: Word,Inter SemiBold,{FONT},&H00FFFFFF,&H00FFFFFF,&H00101010,&H90000000,0,0,0,0,100,100,0,0,1,5,3,5,70,70,0,1\n\n"
           "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    hdr = hdr.replace("{FONT}", str(CAPTION_FONT))
    pop = r"\fscx85\fscy85\t(0,90,\fscx100\fscy100)"
    rows = [hdr]
    n = 0
    for i, w in enumerate(words):
        txt = re.sub(r"[^\w+#'?!.,$%-]", "", str(w["text"]))
        if not txt:
            continue
        a = w["start"] / tempo
        nxt = words[i + 1]["start"] / tempo if i + 1 < len(words) else (w["end"] / tempo + 0.3)
        b = max(a + 0.12, min(nxt, w["end"] / tempo + 0.25))
        key = re.sub(r"[^a-z0-9]", "", txt.lower()) in keywords
        rows.append(f"Dialogue: 1,{_ts(a)},{_ts(b)},Word,,0,0,0,,{{\\an5\\pos(540,{ypos}){pop}}}{RED if key else WHITE}{txt}\n")
        n += 1
    Path(path).write_text("".join(rows), encoding="utf-8")
    return n


# ---------------------------------------------------------------- video render

def render_video(src: str, info: dict, segs: list, ass_path: str, out: str) -> bool:
    fc = []
    has_a = info["has_audio"]
    for i, (a, b, sp) in enumerate(segs):
        fc.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=(PTS-STARTPTS)/{sp:.4f},fps=30,format=yuv420p,setsar=1[v{i}]")
        if has_a:
            fc.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS{atempo_chain(sp)},"
                      f"aformat=sample_rates=44100:channel_layouts=stereo[a{i}]")
        else:
            fc.append(f"anullsrc=r=44100:cl=stereo,atrim=duration={(b - a) / sp:.3f},asetpts=PTS-STARTPTS[a{i}]")
    cat = "".join(f"[v{i}][a{i}]" for i in range(len(segs)))
    fc.append(f"{cat}concat=n={len(segs)}:v=1:a=1[vc][ac]")
    portrait = info["height"] > info["width"]
    bg = ("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=35:6,"
          "eq=brightness=0.07:saturation=1.35:contrast=1.05")
    if portrait:
        fg = "scale=1080:-2:flags=lanczos"
        ov = "overlay=0:(H-h)/2"
    else:
        # Whole frame, full width, no crop (no zoom); blurred copy fills the rest of the 9:16 canvas.
        fg = f"crop=iw*{FG_CROP}:ih:(iw-iw*{FG_CROP})/2:0,scale=1080:-2:flags=lanczos,unsharp=5:5:0.3"
        ov = "overlay=0:(H-h)/2-60"
    fontdir = str(FONT_DIR).replace("\\", "/").replace(":", r"\:")
    ass_arg = str(ass_path).replace("\\", "/").replace(":", r"\:")
    fc.append(f"[vc]split[b0][f0];[b0]{bg}[bg];[f0]{fg}[fg];[bg][fg]{ov},ass='{ass_arg}':fontsdir='{fontdir}',format=yuv420p[v]")
    script = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    script.write(";\n".join(fc))
    script.close()
    p = run(["ffmpeg", "-y", "-v", "error", "-i", src, "-filter_complex_script", script.name, "-map", "[v]", "-map", "[ac]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-profile:v", "high", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out], 600)
    os.unlink(script.name)
    if p.returncode != 0:
        print(f"[studio] video render failed: {p.stderr[-400:]}")
        return False
    return os.path.isfile(out) and os.path.getsize(out) > 1000


# ---------------------------------------------------------------- audio mix

def _peak_db(path: str, ss: float = 0.0, t: float | None = None) -> float:
    cmd = ["ffmpeg", "-hide_banner", "-ss", str(ss)]
    if t:
        cmd += ["-t", str(t)]
    cmd += ["-i", path, "-af", "volumedetect", "-f", "null", "-"]
    p = run(cmd, 60)
    m = re.search(r"max_volume:\s*(-?[0-9.]+) dB", p.stderr or "")
    return float(m.group(1)) if m else -3.0


def _mean_db(path: str, ss: float, t: float) -> float:
    p = run(["ffmpeg", "-hide_banner", "-ss", str(ss), "-t", str(t), "-i", path, "-af", "volumedetect", "-f", "null", "-"], 60)
    m = re.search(r"mean_volume:\s*(-?[0-9.]+) dB", p.stderr or "")
    return float(m.group(1)) if m else -20.0


def sfx_events(words: list[dict], tempo: float, keywords: set[str], total: float) -> list[tuple[str, float]]:
    """intro at 0, pop at each sentence start (after the first), riser into the first keyword, bang on it."""
    ev: list[tuple[str, float]] = [("intro", 0.0)]
    starts = []
    for i, w in enumerate(words):
        t = w["start"] / tempo
        prev = str(words[i - 1]["text"]) if i else ""
        if i and re.search(r"[.?!]$", prev):
            starts.append(t)
    ev += [("pop", t) for t in starts[:4]]
    kw_t = next((w["start"] / tempo for w in words
                 if re.sub(r"[^a-z0-9]", "", str(w["text"]).lower()) in keywords and w["start"] / tempo > 2.0), None)
    if kw_t is not None:
        ev.append(("bang", kw_t))
        if kw_t - 2.6 > 0.4:
            ev.append(("riser", kw_t - 2.6))
    return [(k, t) for k, t in ev if 0 <= t < total - 0.2]


def mix_audio(video: str, vo: str, events: list[tuple[str, float]], total: float, out: str) -> bool:
    inputs = ["-i", video, "-i", vo]
    music = SFX_DIR / "music.mp3"
    use_music = music.is_file()
    music_gain = 0.0
    if use_music:
        inputs += ["-ss", "1.42", "-t", f"{total + 1:.2f}", "-i", str(music)]
        music_gain = MUSIC_RMS_DB - _mean_db(str(music), 1.42, min(total + 1, 20))
    fc = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[g]"]
    nvo = 3 if use_music else 2
    fc.append(f"[1:a]atempo={VO_TEMPO},highpass=f=70,aformat=sample_rates=44100:channel_layouts=stereo,adelay=40|40,"
              f"asplit={nvo}[vo]" + "".join(f"[vs{i}]" for i in range(nvo - 1)))
    fc.append("[g][vs0]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350:makeup=1[gd];[gd]volume=-6dB[gdv]")
    mix_in = ["[gdv]", "[vo]"]
    idx = 3 if use_music else 2
    if use_music:
        fc.append(f"[2:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={music_gain:.2f}dB,"
                  f"afade=t=in:d=0.5,afade=t=out:st={max(0.1, total - 1.2):.2f}:d=1.2[m0];"
                  f"[m0][vs1]sidechaincompress=threshold=0.02:ratio=8:attack=10:release=300:makeup=1[md]")
        mix_in.append("[md]")
    for n, (kind, t) in enumerate(events):
        f = SFX_DIR / f"{kind}.mp3"
        if not f.is_file():
            continue
        inputs += ["-i", str(f)]
        g = 20 * math.log10(SFX_GAIN.get(kind, 0.5)) - _peak_db(str(f))
        ms = int(t * 1000)
        fc.append(f"[{idx}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume={g:.2f}dB,adelay={ms}|{ms}[s{n}]")
        mix_in.append(f"[s{n}]")
        idx += 1
    fc.append("".join(mix_in) + f"amix=inputs={len(mix_in)}:normalize=0:duration=first,alimiter=limit=0.95,"
              f"loudnorm=I=-14:TP=-1.5:LRA=7[m]")
    script = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
    script.write(";\n".join(fc))
    script.close()
    p = run(["ffmpeg", "-y", "-v", "error"] + inputs + ["-filter_complex_script", script.name, "-map", "0:v", "-map", "[m]",
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.3f}", "-movflags", "+faststart", out], 300)
    os.unlink(script.name)
    if p.returncode != 0:
        print(f"[studio] audio mix failed: {p.stderr[-400:]}")
        return False
    return os.path.isfile(out) and os.path.getsize(out) > 1000


# ---------------------------------------------------------------- orchestration

def render_short(source: str, dest: str, game_name: str, brief: str = "", index: int = 0,
                 voice_provider=None, max_seconds: float = 30.0) -> dict:
    """voice_provider(text, mp3_path) -> (mp3_path|None, words). Default: tts_engine.generate_voiceover."""
    try:
        name, prof = find_profile(game_name, brief)
        lines = pick_script(prof, name, index)
        text = strip_markup(lines)
        kws = keyword_set(lines)
        kws |= {re.sub(r"[^a-z0-9]", "", w.lower()) for w in name.split()}
        if name.startswith("+1"):
            kws |= {"plus", "one", "1"}
        info = probe(source)                     # check the footage BEFORE spending voice credits
        if info["duration"] < 3 or not info["width"]:
            return {"ok": False, "reason": "source unreadable/short"}
        work = Path(tempfile.mkdtemp(prefix="studio_"))
        if voice_provider is None:
            from tts_engine import generate_voiceover as voice_provider  # type: ignore
        mp3, words = voice_provider(text, str(work / "vo.mp3"))
        if not mp3 or not os.path.isfile(mp3):
            return {"ok": False, "reason": "no voiceover"}
        vo_dur = probe(mp3)["duration"]
        voice_end = (max((w["end"] for w in words), default=vo_dur) if words else vo_dur)
        voice_end = max(voice_end, vo_dur - 0.15)
        total = voice_end / VO_TEMPO + TAIL
        total = min(total, max_seconds)
        motion = motion_profile(source, info["duration"])
        segs = plan_segments(info["duration"], total, motion)
        if not segs:
            return {"ok": False, "reason": f"source {info['duration']:.1f}s too short for {total:.1f}s voice"}
        total = segs_output_duration(segs)
        cw = words_for_captions(text, words, voice_end)
        ass_path = str(work / "caps.ass")
        write_ass(ass_path, cw, kws, VO_TEMPO)
        base = str(work / "base.mp4")
        if not render_video(source, info, segs, ass_path, base):
            return {"ok": False, "reason": "video render failed"}
        events = sfx_events(cw, VO_TEMPO, kws, total)
        os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
        if not mix_audio(base, mp3, events, total, dest):
            return {"ok": False, "reason": "audio mix failed"}
        final = probe(dest)["duration"]
        coverage = min(1.0, (voice_end / VO_TEMPO) / max(final, 0.1))
        print(f"[studio] ok game={name!r} voice_end={voice_end / VO_TEMPO:.1f}s video={final:.1f}s "
              f"voice_covers={coverage * 100:.0f}% segs={len(segs)} sfx={[k for k, _ in events]}")
        return {"ok": True, "path": dest, "duration": final, "voice_coverage": coverage,
                "script": text, "hashtags": prof.get("hashtags") or [], "game": name}
    except Exception as exc:  # noqa: BLE001
        print(f"[studio] failed: {exc}")
        return {"ok": False, "reason": str(exc)}


class StudioStrictError(BaseException):
    """Raised only when STUDIO_STRICT=1 and the studio edit failed. It is a BaseException on purpose:
    the caller's `except Exception` fallback must NOT swallow it, so test runs fail loudly with the reason
    instead of silently producing an old-style video."""


def render_campaign_short(source: str, dest: str, campaign=None, req=None, clip_index: int = 0) -> dict:
    name = (getattr(campaign, "name", "") or "").strip()
    brief = getattr(campaign, "requirements_text", "") or ""
    max_s = float(getattr(req, "max_seconds", 30.0) or 30.0)
    res = render_short(source, dest, name, brief, clip_index, max_seconds=min(MAX_SHORT_SECONDS, max_s))
    if not res.get("ok"):
        print(f"[studio] NOT USED, reason: {res.get('reason')}")
        if (os.environ.get("STUDIO_STRICT") or "0").strip() == "1":
            raise StudioStrictError(f"studio editor failed (STUDIO_STRICT=1): {res.get('reason')}")
        print("[studio] falling back to the OLD renderer")
    return res
