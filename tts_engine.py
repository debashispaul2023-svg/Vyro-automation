"""ElevenLabs only. Rotate keys. No espeak / Edge fallback.

Free ElevenLabs plans cannot use *library* voices through the API (HTTP 402 paid_plan_required).
When that happens the engine stops hammering keys/models for that voice and falls back to a
voice the account can use (a premade voice found via GET /v1/voices, or ELEVENLABS_FALLBACK_VOICE_ID).
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import requests

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
# "Loyal - Viral Shortform Content": the voice used for the Scrub or Die / Dead Age Shorts.
# NOTE: a GitHub secret ELEVENLABS_VOICE_ID still overrides this. Delete or update that secret.
DEFAULT_VOICE = "mI8xLTBNjMXAf31I4xlB"
DEFAULT_MODEL = "eleven_v4"
FALLBACK_MODEL = "eleven_multilingual_v2"
LAST_RESORT_MODEL = "eleven_turbo_v2_5"
# eleven_v4 is generated with the voice's own defaults (that is how the Shorts were made);
# the tuned settings below only apply to the older fallback models.
MODELS_WITHOUT_SETTINGS = {"eleven_v4"}


def _keys() -> list[str]:
    blob = (os.environ.get("ELEVENLABS_API_KEYS") or "").strip()
    keys = [k.strip() for k in blob.split(",") if k.strip()]
    single = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    if single and single not in keys:
        keys.append(single)
    return keys


def _voice() -> str:
    return (os.environ.get("ELEVENLABS_VOICE_ID") or DEFAULT_VOICE).strip()


def _words_from_alignment(text: str, alignment: dict | None) -> list[dict]:
    if not alignment:
        return []
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars or len(chars) != len(starts) or len(chars) != len(ends):
        return []
    words = text.split()
    out = []
    pos = 0
    try:
        for word in words:
            while pos < len(chars) and chars[pos].strip() == "":
                pos += 1
            if pos >= len(chars):
                return []
            start_i = pos
            matched = 0
            while pos < len(chars) and matched < len(word) and chars[pos] == word[matched]:
                pos += 1
                matched += 1
            if matched != len(word):
                return []
            out.append({"text": word, "start": float(starts[start_i]), "end": float(ends[pos - 1])})
        return out
    except Exception:
        return []


def karaoke_words(words: list[dict], delay: float = 0.0) -> list[tuple[float, float, str]]:
    """One on-screen word at a time, aligned to spoken timing."""
    out = []
    for w in words or []:
        txt = re.sub(r"[^\w+#']+", "", str(w.get("text") or ""))
        if not txt:
            continue
        a = max(0.0, float(w.get("start") or 0) + delay)
        b = max(a + 0.12, float(w.get("end") or 0) + delay + 0.04)
        out.append((a, b, txt))
    return out


def phrases_from_words(words: list[dict], script: str) -> list[tuple[float, float, str]]:
    """Group timed words into the script sentences."""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    if not sentences:
        sentences = [script.strip()]
    if not words:
        # even split so on-screen text still matches the script
        total = max(len(script.split()), 1)
        t = 0.0
        out = []
        for sent in sentences:
            n = max(len(sent.split()), 1)
            dur = max(2.2, n * 0.38)
            out.append((t, t + dur, sent))
            t += dur
        return out
    used = 0
    out = []
    for sent in sentences:
        n = max(len(sent.split()), 1)
        chunk = words[used:used + n]
        used += n
        if not chunk:
            break
        out.append((float(chunk[0]["start"]), float(chunk[-1]["end"]) + 0.12, sent))
    return out


def _humanize_script(text: str) -> str:
    """Short punchy lines. TTS reads this more energetically."""
    text = re.sub(r"\s+", " ", (text or "").strip())
    text = text.replace(" — ", ". ")
    text = text.replace("+1", "Plus One")
    text = re.sub(r"\.\s+", ". ", text)
    return text


def _is_plan_block(resp) -> bool:
    try:
        return resp.status_code == 402 or "paid_plan_required" in resp.text
    except Exception:
        return False


def _fallback_voices(key: str, tried: set[str]) -> list[str]:
    """Voices this account can use on a free plan: explicit env id, then premade voices."""
    out: list[str] = []
    env = (os.environ.get("ELEVENLABS_FALLBACK_VOICE_ID") or "").strip()
    if env and env not in tried:
        out.append(env)
    try:
        r = requests.get(f"{ELEVENLABS_BASE}/voices", headers={"xi-api-key": key}, timeout=30)
        if r.status_code == 200:
            voices = r.json().get("voices", [])
            # own/cloned/designed voices first, then premade; skip anything already tried
            order = {"generated": 0, "cloned": 0, "premade": 1, "professional": 2}
            voices = sorted(voices, key=lambda v: order.get(v.get("category"), 3))
            for v in voices:
                vid = v.get("voice_id")
                if vid and vid not in tried and vid not in out and v.get("category") in order:
                    out.append(vid)
        else:
            print(f"[voice] could not list voices: HTTP {r.status_code}")
    except Exception as exc:
        print(f"[voice] could not list voices: {exc}")
    return out[:4]


def _try_voice(voice: str, keys: list[str], spoken: str, settings: dict, dest_mp3: str):
    """Returns (words, blocked). words is None on failure; blocked=True means a plan block."""
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice}/with-timestamps"
    for i, key in enumerate(keys, start=1):
        masked = f"...{key[-4:]}" if len(key) > 4 else "****"
        try:
            print(f"[voice] ElevenLabs key #{i}/{len(keys)} ({masked}) voice={voice[-6:]}")
            resp = None
            for model in (DEFAULT_MODEL, FALLBACK_MODEL, LAST_RESORT_MODEL):
                payload = {"text": spoken, "model_id": model, "apply_text_normalization": "auto"}
                if model not in MODELS_WITHOUT_SETTINGS:
                    payload["voice_settings"] = dict(settings)
                resp = requests.post(url, headers={"xi-api-key": key, "Content-Type": "application/json"},
                                     json=payload, params={"output_format": "mp3_44100_128"}, timeout=60)
                if resp.status_code == 400 and "speed" in (payload.get("voice_settings") or {}):
                    payload["voice_settings"].pop("speed", None)
                    resp = requests.post(url, headers={"xi-api-key": key, "Content-Type": "application/json"},
                                         json=payload, params={"output_format": "mp3_44100_128"}, timeout=60)
                if resp.status_code == 200:
                    print(f"[voice] model {model}")
                    break
                print(f"[voice] model {model} HTTP {resp.status_code}: {resp.text[:120]}")
                if _is_plan_block(resp):
                    # Same answer for every model and every free key: stop, try another voice.
                    print(f"[voice] voice {voice[-6:]} needs a paid plan on the API; skipping it")
                    return None, True
            if resp is None or resp.status_code != 200:
                continue
            data = resp.json()
            audio_b64 = data.get("audio_base64") or ""
            if not audio_b64:
                print(f"[voice] key #{i} no audio_base64")
                continue
            Path(dest_mp3).write_bytes(base64.b64decode(audio_b64))
            words = _words_from_alignment(spoken, data.get("alignment"))
            Path(dest_mp3).with_suffix(".words.json").write_text(json.dumps(words), encoding="utf-8")
            return words, False
        except Exception as exc:
            print(f"[voice] key #{i} failed: {exc}")
    return None, False


def generate_voiceover(text: str, dest_mp3: str) -> tuple[str | None, list[dict]]:
    """
    Returns (mp3_path_or_None, word_timings).
    No voice file if every voice/key fails; the caller must skip TTS.
    """
    text = (text or "").strip()
    if not text:
        return None, []
    keys = _keys()
    if not keys:
        print("[voice] no ELEVENLABS_API_KEY / ELEVENLABS_API_KEYS; skipping voice")
        return None, []

    spoken = _humanize_script(text)
    try:
        import usage_guard
        allowed, why = usage_guard.check_tts(len(spoken))
        print(f"[credits] ElevenLabs {why}")
        if not allowed:
            print("[credits] voice skipped to protect credits")
            return None, []
    except ImportError:
        usage_guard = None
    settings = {"stability": 0.18, "similarity_boost": 0.62, "style": 0.82,
                "use_speaker_boost": True, "speed": 1.08}
    Path(dest_mp3).parent.mkdir(parents=True, exist_ok=True)

    tried: set[str] = set()
    voice = _voice()
    queue = [voice]
    fallbacks_loaded = False
    while queue:
        voice = queue.pop(0)
        if voice in tried:
            continue
        tried.add(voice)
        words, blocked = _try_voice(voice, keys, spoken, settings, dest_mp3)
        if words is not None:
            if usage_guard:
                usage_guard.record_tts(len(spoken))
            print(f"[voice] ElevenLabs ok ({len(words)} word timings) voice={voice[-6:]}")
            return dest_mp3, words
        if blocked and not fallbacks_loaded:
            fallbacks_loaded = True
            queue.extend(_fallback_voices(keys[0], tried))
            print(f"[voice] fallback voices to try: {[v[-6:] for v in queue]}")

    print("[voice] every ElevenLabs voice/key failed; no voiceover")
    return None, []
