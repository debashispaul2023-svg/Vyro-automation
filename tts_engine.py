"""ElevenLabs only. Rotate keys. No espeak / Edge fallback."""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import requests

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"  # Adam
DEFAULT_MODEL = "eleven_multilingual_v2"


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


def generate_voiceover(text: str, dest_mp3: str) -> tuple[str | None, list[dict]]:
    """
    Returns (mp3_path_or_None, word_timings).
    No voice file if every key fails — caller must skip TTS.
    """
    text = (text or "").strip()
    if not text:
        return None, []
    keys = _keys()
    if not keys:
        print("[voice] no ELEVENLABS_API_KEY / ELEVENLABS_API_KEYS — skipping voice")
        return None, []

    voice = _voice()
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice}/with-timestamps"
    payload = {
        "text": text,
        "model_id": DEFAULT_MODEL,
        "voice_settings": {
            "stability": 0.32,
            "similarity_boost": 0.75,
            "style": 0.45,
            "use_speaker_boost": True,
        },
    }
    Path(dest_mp3).parent.mkdir(parents=True, exist_ok=True)

    for i, key in enumerate(keys, start=1):
        masked = f"...{key[-4:]}" if len(key) > 4 else "****"
        try:
            print(f"[voice] ElevenLabs key #{i}/{len(keys)} ({masked})")
            resp = requests.post(
                url,
                headers={"xi-api-key": key, "Content-Type": "application/json"},
                json=payload,
                params={"output_format": "mp3_44100_128"},
                timeout=60,
            )
            if resp.status_code != 200:
                print(f"[voice] key #{i} HTTP {resp.status_code}: {resp.text[:160]}")
                continue
            data = resp.json()
            audio_b64 = data.get("audio_base64") or ""
            if not audio_b64:
                print(f"[voice] key #{i} no audio_base64")
                continue
            Path(dest_mp3).write_bytes(base64.b64decode(audio_b64))
            words = _words_from_alignment(text, data.get("alignment"))
            side = Path(dest_mp3).with_suffix(".words.json")
            side.write_text(json.dumps(words), encoding="utf-8")
            print(f"[voice] ElevenLabs ok ({len(words)} word timings)")
            return dest_mp3, words
        except Exception as exc:
            print(f"[voice] key #{i} failed: {exc}")
            continue

    print("[voice] every ElevenLabs key failed — no voiceover")
    return None, []
