"""Gemini brain with short per-key timeout so Daily cannot hang."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

MODEL_CANDIDATES = (
    "gemini-2.0-flash",
    "gemini-2.5-flash",
    "gemini-3.6-flash",
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


_REQUIREMENTS_PROMPT = """\
Extract campaign rules. JSON only:
{{
  "mandatory_hashtags": ["#example"],
  "required_links": ["https://..."],
  "min_seconds": 15,
  "max_seconds": 30,
  "referral_code": null,
  "watermark_text": null,
  "notes": ""
}}
Keep notes under 12 words. Close every array.

Requirements text:
\"\"\"
{text}
\"\"\"
"""


def _regex_requirements(raw_text: str) -> ParsedRequirements:
    text = raw_text or ""
    tags = re.findall(r"#\w+", text)
    links = re.findall(r"https?://[^\s)\]>\"']+", text)
    code = None
    m = re.search(r"(?:code|use code)\s*[:\-]?\s*([A-Z0-9]{3,20})", text, re.I)
    if m:
        code = m.group(1)
    return ParsedRequirements(
        mandatory_hashtags=list(dict.fromkeys(tags)),
        required_links=list(dict.fromkeys(links))[:8],
        min_seconds=15.0,
        max_seconds=30.0,
        referral_code=code,
        watermark_text=None,
        notes="regex fallback",
    )


def ai_parse_requirements(raw_text: str) -> ParsedRequirements:
    if not raw_text or not raw_text.strip():
        raise AIBrainError("Empty requirements text.")
    try:
        data = _call_json(_REQUIREMENTS_PROMPT.format(text=raw_text[:3500]), max_output_tokens=512)
    except AIBrainError as exc:
        print(f"[gemini] parse fallback ({exc})")
        return _regex_requirements(raw_text)
    tags = data.get("mandatory_hashtags") or []
    if not isinstance(tags, list):
        tags = []
    links = data.get("required_links") or []
    if not isinstance(links, list):
        links = []
    return ParsedRequirements(
        mandatory_hashtags=[h if str(h).startswith("#") else f"#{h}" for h in tags if h],
        required_links=[str(x) for x in links if x],
        min_seconds=float(data.get("min_seconds", 15.0) or 15.0),
        max_seconds=float(data.get("max_seconds", 30.0) or 30.0),
        referral_code=data.get("referral_code") or None,
        watermark_text=data.get("watermark_text") or None,
        notes=(data.get("notes") or "")[:240],
    )


@dataclass
class AIMetadata:
    title: str
    description: str
    hashtags: list[str]
    instagram_caption: str


_METADATA_PROMPT = """\
Write Shorts + Reels metadata. JSON only:
{{
  "title": "title under 100 chars ending with mandatory hashtags then #shorts",
  "description": "2-4 short lines including mandatory links",
  "hashtags": ["#tag1"],
  "instagram_caption": "short caption with mandatory hashtags"
}}
Hook: {hook}
Summary: {summary}
Hashtags: {hashtags}
Links: {links}
Code: {referral_code}
"""


def ai_generate_metadata(
    hook: str,
    summary: str,
    mandatory_hashtags: list[str],
    required_links: list[str],
    referral_code: Optional[str],
) -> AIMetadata:
    prompt = _METADATA_PROMPT.format(
        hook=hook,
        summary=summary or "(no summary provided)",
        hashtags=", ".join(mandatory_hashtags) or "(none)",
        links=", ".join(required_links) or "(none)",
        referral_code=referral_code or "(none)",
    )
    data = _call_json(prompt)
    description = data.get("description", "") or ""
    for link in required_links:
        if link not in description:
            description += f"\n{link}"
    title = data.get("title", "") or hook
    for tag in mandatory_hashtags:
        if tag.lower() not in title.lower():
            title += f" {tag}"
    return AIMetadata(
        title=title.strip(),
        description=description.strip(),
        hashtags=list(data.get("hashtags", mandatory_hashtags)),
        instagram_caption=(data.get("instagram_caption") or description).strip(),
    )


@dataclass
class CampaignScore:
    is_good: bool
    reason: str


def ai_score_campaign(raw_text: str) -> CampaignScore:
    if not raw_text or not raw_text.strip():
        return CampaignScore(is_good=False, reason="Empty requirements text.")
    try:
        data = _call_json(
            'Score this campaign. JSON only: {{"is_good": true, "reason": "one line"}}\n' + raw_text[:4000],
            max_output_tokens=256,
        )
    except AIBrainError as exc:
        return CampaignScore(is_good=True, reason=f"Scoring unavailable ({exc}), proceeding by default.")
    return CampaignScore(is_good=bool(data.get("is_good", True)), reason=data.get("reason", ""))


def ai_rank_clip_names(clip_names: list[str], requirements: str) -> list[str]:
    names = [n for n in clip_names if n]
    if not names:
        return []
    try:
        data = _call_json(
            "Pick 8 best clip filenames. Skip icon/logo/banner. JSON only: "
            '{"ranked_names": ["file1.mp4"]}\nRules:\n'
            + (requirements or "")[:2500]
            + "\nFiles:\n"
            + "\n".join(names[:80])
        )
        ranked = [str(x) for x in (data.get("ranked_names") or []) if x]
        print(f"[ai] ranked {len(ranked)} clip name(s)")
        return ranked
    except Exception as exc:
        print(f"[ai] clip rank skipped: {exc}")
        return []


def ai_plan_edit_tools(requirements: str) -> dict:
    text = (requirements or "").strip()
    low = text.lower()
    ready = any(x in low for x in ("ready to upload", "ready-made", "just upload", "no edit", "do not edit"))
    must_speak = any(x in low for x in ("must be spoken", "spoken somewhere", "voiceover", "voice over", "say the name"))
    must_icon = any(x in low for x in ("game icon", "icon must", "logo at the end", "shown at the end"))
    must_cta = any(x in low for x in ("cta", "call to action", "game is called"))
    must_cap = any(x in low for x in ("on-screen caption", "burned caption", "subtitle"))
    reject_lq = any(x in low for x in ("low-quality", "low quality", "poorly presented"))
    anime = "anime" in low
    fisch = "fisch" in low
    fallback = {
        "speak_text": (
            "You roll dice to unlock anime girls, place them on your plot, and make money. Game is called Roll Anime Girls on Roblox."
            if must_speak and anime
            else (
                "In this Roblox game you catch strange fish, upgrade your gear, and fight. The game is called How to Fisch."
                if must_speak and fisch
                else ""
            )
        ),
        "cta_text": (
            "Game is called Roll Anime Girls on Roblox"
            if must_cta and anime
            else ("Game is called How to Fisch on Roblox" if must_cta and fisch else "")
        ),
        "end_title": (
            "ROLL ANIME GIRLS"
            if must_icon and anime
            else ("HOW TO FISCH" if must_icon and fisch else "")
        ),
        "need_captions": must_cap or must_cta,
        "need_spoken_voice": must_speak,
        "need_end_icon": must_icon,
        "need_quality_boost": reject_lq and not ready,
        "ready_to_upload": ready,
        "notes": "heuristic plan",
    }
    if not text:
        return fallback
    try:
        data = _call_json(
            "Decide edit tools. JSON only with speak_text,cta_text,end_title,"
            "need_captions,need_spoken_voice,need_end_icon,need_quality_boost,notes.\n"
            + text[:3500]
        )
        plan = dict(fallback)
        plan.update({k: data.get(k, plan[k]) for k in plan})
        if plan.get("ready_to_upload"):
            plan["need_spoken_voice"] = False
            plan["need_captions"] = False
            plan["need_end_icon"] = False
            plan["need_quality_boost"] = False
            plan["speak_text"] = ""
        print(f"[ai] edit plan: {plan}")
        return plan
    except Exception as exc:
        print(f"[ai] edit plan fallback: {exc}")
        return fallback
