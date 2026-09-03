"""
ai_brain.py

The "AI brain" for this automation — wraps the Google Gemini API
(model: gemini-2.5-flash) for three jobs:

  1. ai_parse_requirements() — extracts structured rules from text.
  2. ai_generate_metadata() — writes title/description/caption tailored to clip.
  3. ai_score_campaign() — sanity-checks if a campaign is legitimate.

Required environment variable: GEMINI_API_KEY
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

MODEL_NAME = "gemini-2.5-flash"


class AIBrainError(Exception):
    """Raised when a Gemini call fails and there is no safe fallback."""


def _get_model() -> genai.GenerativeModel:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AIBrainError("GEMINI_API_KEY environment variable is not set.")
    
    genai.configure(api_key=api_key)
    # Using JSON response mime type to guarantee structured output
    return genai.GenerativeModel(
        model_name=MODEL_NAME,
        generation_config={"response_mime_type": "application/json"}
    )


def _extract_json(text: str) -> dict:
    """Fallback cleaner in case markdown fences are still returned."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# 1. Requirement parsing
# ---------------------------------------------------------------------------

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
You will be given the raw requirements text for a video-clipping campaign, \
possibly in Bengali, English, a mix, or broken grammar. Extract the actual \
rules a video clipper must follow.

Respond with ONLY a JSON object matching exactly this shape:
{{
  "mandatory_hashtags": ["#example"],
  "required_links": ["https://..."],
  "min_seconds": 15,
  "max_seconds": 60,
  "referral_code": "CODE or null",
  "watermark_text": "text to overlay on video, or null",
  "notes": "anything unusual/unclear worth flagging, or empty string"
}}

If a field isn't mentioned, use a sensible default (empty list, 15/60 for \
duration, null for optional strings).

Requirements text:
\"\"\"
{text}
\"\"\"
"""


def ai_parse_requirements(raw_text: str) -> ParsedRequirements:
    if not raw_text or not raw_text.strip():
        raise AIBrainError("Empty requirements text.")

    model = _get_model()
    try:
        response = model.generate_content(_REQUIREMENTS_PROMPT.format(text=raw_text))
        data = _extract_json(response.text)
    except Exception as exc:
        raise AIBrainError(f"Could not parse Gemini's response: {exc}") from exc

    return ParsedRequirements(
        mandatory_hashtags=[h if h.startswith("#") else f"#{h}" for h in data.get("mandatory_hashtags", [])],
        required_links=list(data.get("required_links", [])),
        min_seconds=float(data.get("min_seconds", 15.0) or 15.0),
        max_seconds=float(data.get("max_seconds", 60.0) or 60.0),
        referral_code=data.get("referral_code") or None,
        watermark_text=data.get("watermark_text") or None,
        notes=data.get("notes", "") or "",
    )


# ---------------------------------------------------------------------------
# 2. Metadata generation
# ---------------------------------------------------------------------------

@dataclass
class AIMetadata:
    title: str
    description: str
    hashtags: list[str]
    instagram_caption: str


_METADATA_PROMPT = """\
You are writing YouTube Shorts + Instagram Reels metadata for a viral clip \
from this campaign. Write in a punchy, high-CTR style — the kind of hook \
that gets clicks, not a dry description.

Clip context / hook: {hook}
Campaign summary: {summary}
Mandatory hashtags (MUST all appear, do not change or drop any): {hashtags}
Mandatory links (MUST appear in the description, verbatim): {links}
Referral code (if any, mention it naturally): {referral_code}

Respond with ONLY a JSON object:
{{
  "title": "YouTube title, under 100 characters, MUST end with the mandatory hashtags then #shorts",
  "description": "YouTube description, 2-4 short lines, MUST include every mandatory link verbatim and the referral code if given",
  "hashtags": ["#tag1", "#tag2"],
  "instagram_caption": "A separate short Instagram caption, MUST also include the mandatory hashtags"
}}
"""


def ai_generate_metadata(
    hook: str,
    summary: str,
    mandatory_hashtags: list[str],
    required_links: list[str],
    referral_code: Optional[str],
) -> AIMetadata:
    model = _get_model()
    prompt = _METADATA_PROMPT.format(
        hook=hook,
        summary=summary or "(no summary provided)",
        hashtags=", ".join(mandatory_hashtags) or "(none)",
        links=", ".join(required_links) or "(none)",
        referral_code=referral_code or "(none)",
    )
    
    try:
        response = model.generate_content(prompt)
        data = _extract_json(response.text)
    except Exception as exc:
        raise AIBrainError(f"Could not parse Gemini's response: {exc}") from exc

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


# ---------------------------------------------------------------------------
# 3. Campaign quality scoring
# ---------------------------------------------------------------------------

@dataclass
class CampaignScore:
    is_good: bool
    reason: str


_SCORE_PROMPT = """\
You are screening a video-clipping campaign brief before a creator commits \
time to it. Flag it as LOW QUALITY if it shows signs of being a scam or \
not worth the effort: vague rules, asks for sensitive info, promises \
unrealistic payouts, or reads as gibberish. Otherwise mark it GOOD.

Campaign requirements text:
\"\"\"
{text}
\"\"\"

Respond with ONLY a JSON object:
{{"is_good": true or false, "reason": "one short sentence explaining why"}}
"""


def ai_score_campaign(raw_text: str) -> CampaignScore:
    if not raw_text or not raw_text.strip():
        return CampaignScore(is_good=False, reason="Empty requirements text.")

    model = _get_model()
    try:
        response = model.generate_content(_SCORE_PROMPT.format(text=raw_text))
        data = _extract_json(response.text)
    except Exception as exc:
        return CampaignScore(is_good=True, reason=f"Scoring unavailable ({exc}), proceeding by default.")

    return CampaignScore(is_good=bool(data.get("is_good", True)), reason=data.get("reason", ""))


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python ai_brain.py <parse|score> <text>")
        sys.exit(1)

    mode = sys.argv[1]
    sample_text = sys.argv[2] if len(sys.argv) > 2 else "Use hashtags #mrbeast #shorts, 20-35 seconds, code MRBEAST"

    if mode == "parse":
        print(ai_parse_requirements(sample_text))
    elif mode == "score":
        print(ai_score_campaign(sample_text))
