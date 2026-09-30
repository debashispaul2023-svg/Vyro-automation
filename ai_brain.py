"""Gemini brain with short per-key timeout so Daily cannot hang."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

MODEL_CANDIDATES = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
)
MODEL_NAME = MODEL_CANDIDATES[0]
GEMINI_TIMEOUT_SEC = 20


class AIBrainError(Exception):
    """Raised when a Gemini call fails and there is no safe fallback."""


def _gemini_keys() -> list[str]:
    blob = (os.environ.get("GEMINI_API_KEYS") or "").strip()
    keys = [k.strip() for k in blob.split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    return keys


def _model(api_key: str, model_name: str | None = None) -> "genai.GenerativeModel":
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name or MODEL_NAME)


def _extract_json_blob(text: str) -> str:
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    start = raw.find("{")
    if start < 0:
        return raw
    depth = 0
    in_str = False
    esc = False
    for i, ch in enumerate(raw[start:], start=start):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]


def _repair_json(blob: str) -> str:
    s = (blob or "").strip()
    if not s:
        return "{}"
    s = re.sub(r",(\s*[}\]])", r"\1", s)
    in_str = False
    esc = False
    for ch in s:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
    if in_str:
        s += '"'
    opens = s.count("[") - s.count("]")
    if opens > 0:
        s += "]" * opens
    opens = s.count("{") - s.count("}")
    if opens > 0:
        s += "}" * opens
    return s


def _salvage_fields(blob: str) -> dict:
    out: dict = {}
    for key in ("mandatory_hashtags", "required_links", "hashtags", "ranked_names"):
        m = re.search(rf'"{key}"\s*:\s*\[(.*?)\]', blob, re.S)
        if not m:
            continue
        items = re.findall(r'"([^"\\]*)"', m.group(1))
        out[key] = items
    for key in ("title", "description", "instagram_caption", "notes", "reason",
                "speak_text", "cta_text", "end_title", "referral_code", "watermark_text"):
        m = re.search(rf'"{key}"\s*:\s*"([^"]*)"', blob)
        if m:
            out[key] = m.group(1)
        elif re.search(rf'"{key}"\s*:\s*null', blob):
            out[key] = None
    for key in ("min_seconds", "max_seconds"):
        m = re.search(rf'"{key}"\s*:\s*([0-9.]+)', blob)
        if m:
            out[key] = float(m.group(1))
    for key in ("is_good", "need_captions", "need_spoken_voice", "need_end_icon",
                "need_quality_boost", "ready_to_upload"):
        m = re.search(rf'"{key}"\s*:\s*(true|false)', blob, re.I)
        if m:
            out[key] = m.group(1).lower() == "true"
    return out


def _loads_json(text: str) -> dict:
    blob = _extract_json_blob(text)
    for candidate in (blob, _repair_json(blob)):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            continue
    salvaged = _salvage_fields(blob)
    if salvaged:
        print("[gemini] repaired truncated JSON")
        return salvaged
    raise json.JSONDecodeError("unrepairable", blob[:80], 0)


def _call_json(prompt: str, max_output_tokens: int = 2048) -> dict:
    keys = _gemini_keys()
    if not keys:
        raise AIBrainError("GEMINI_API_KEY / GEMINI_API_KEYS not set.")
    last_exc: Exception | None = None
    text = ""
    try:
        for i, key in enumerate(keys, start=1):
            model_name = MODEL_CANDIDATES[(i - 1) % len(MODEL_CANDIDATES)]
            try:
                model = _model(key, model_name)
                response = model.generate_content(
                    prompt,
                    generation_config=genai.types.GenerationConfig(
                        response_mime_type="application/json",
                        max_output_tokens=max_output_tokens,
                        temperature=0.2,
                    ),
                    request_options={"timeout": GEMINI_TIMEOUT_SEC},
                )
                text = response.text
                print(f"[gemini] ok key #{i} model={model_name}")
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                print(f"[gemini] key #{i}/{len(keys)} {model_name} failed: {str(exc)[:160]}")
                if i < len(keys):
                    continue
        else:
            raise AIBrainError(f"Gemini API call failed: {last_exc}")
        if not text and last_exc:
            raise AIBrainError(f"Gemini API call failed: {last_exc}") from last_exc
    except AIBrainError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIBrainError(f"Gemini API call failed: {exc}") from exc

    if not text:
        raise AIBrainError("Gemini returned an empty response.")
    try:
        return _loads_json(text)
    except json.JSONDecodeError as exc:
        raise AIBrainError(f"Could not parse Gemini's JSON response: {exc}\nRaw: {text[:400]}") from exc


@dataclass
class ParsedRequirements:
    mandatory_hashtags: list[str] = field(default_factory=list)
    required_links: list[str] = field(default_factory=list)
    min_seconds: float = 15.0
    max_seconds: float = 60.0
    referral_code: Optional[str] = None
    watermark_text: Optional[str] = None
    notes: str = ""


_REQUIREMENTS_PROMPT = """\\
Extract campaign rules. JSON only:\n{{\n  \"mandatory_hashtags\": [\"#example\"],\n  \"required_links\": [\"https://...\"],\n  \"min_seconds\": 15,\n  \"max_seconds\": 30,\n  \"referral_code\": null,\n  \"watermark_text\": null,\n  \"notes\": \"\"\n}}\nKeep notes under 12 words. Close every array.\n\nRequirements text:\n\"\"\"\n{text}\n\"\"\"\n"""
