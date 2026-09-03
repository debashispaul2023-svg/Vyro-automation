"""
ai_brain.py

The "AI brain" for this automation — wraps the Anthropic Claude API
(model: claude-haiku-4-5, the cheapest current tier — this is light text
work, not heavy reasoning, so Haiku is the right choice both for cost and
speed) for three jobs:

  1. ai_parse_requirements() — reads a campaign's requirement text in ANY
     language or format (a sentence, a bullet list, broken English/Bengali
     mixed, whatever) and extracts structured rules: hashtags, links,
     duration range, referral code, watermark text. This replaces/backs up
     requirements_parser.py's regex-based parse_from_text_prompt(), which
     only handles fairly clean English phrasing.

  2. ai_generate_metadata() — writes an actual catchy title/description/
     caption/hashtag set tailored to the clip's content, instead of the
     fixed template in metadata.py. Falls back to the template version if
     the API call fails, so a bad API day never blocks the whole pipeline.

  3. ai_score_campaign() — before spending time downloading/rendering/
     uploading, asks Claude to sanity-check whether a campaign's
     requirements look legitimate (clear payout terms, reasonable content
     rules) vs. low-quality or scam-like (vague/contradictory rules, asks
     for personal/financial info it shouldn't, unrealistic promises). Used
     by daily_runner.py to skip bad campaigns and try the other platform
     instead of wasting a full render+upload cycle on a dead end.

Required environment variable: ANTHROPIC_API_KEY
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import anthropic

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1024


class AIBrainError(Exception):
    """Raised when a Claude call fails and there is no safe fallback."""


def _client() -> anthropic.Anthropic:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIBrainError("ANTHROPIC_API_KEY environment variable is not set.")
    return anthropic.Anthropic(api_key=api_key)


def _extract_json(text: str) -> dict:
    """Claude sometimes wraps JSON in ```json fences despite instructions —
    strip those before parsing."""
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
    notes: str = ""  # anything unusual the AI flagged for a human to see


_REQUIREMENTS_PROMPT = """\
You will be given the raw requirements text for a video-clipping campaign, \
possibly in Bengali, English, a mix, or broken grammar. Extract the actual \
rules a video clipper must follow.

Respond with ONLY a JSON object (no markdown fences, no preamble), matching \
exactly this shape:
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

    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": _REQUIREMENTS_PROMPT.format(text=raw_text)}],
    )
    text = response.content[0].text
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError) as exc:
        raise AIBrainError(f"Could not parse Claude's JSON response: {exc}\nRaw: {text}") from exc

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

Respond with ONLY a JSON object (no markdown fences, no preamble):
{{
  "title": "YouTube title, under 100 characters, MUST end with the mandatory hashtags then #shorts",
  "description": "YouTube description, 2-4 short lines, MUST include every mandatory link verbatim and the referral code if given",
  "hashtags": ["#tag1", "#tag2"],
  "instagram_caption": "A separate short Instagram caption, can be more casual/emoji-friendly, MUST also include the mandatory hashtags"
}}
"""


def ai_generate_metadata(
    hook: str,
    summary: str,
    mandatory_hashtags: list[str],
    required_links: list[str],
    referral_code: Optional[str],
) -> AIMetadata:
    client = _client()
    prompt = _METADATA_PROMPT.format(
        hook=hook,
        summary=summary or "(no summary provided)",
        hashtags=", ".join(mandatory_hashtags) or "(none)",
        links=", ".join(required_links) or "(none)",
        referral_code=referral_code or "(none)",
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError) as exc:
        raise AIBrainError(f"Could not parse Claude's JSON response: {exc}\nRaw: {text}") from exc

    # Safety net: verify mandatory links/hashtags actually made it in. If
    # Claude dropped one, append it rather than silently violating the
    # campaign's rules (which could get the submission rejected).
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
not worth the effort: vague or contradictory rules, asks for sensitive \
personal/financial information beyond a normal payout method, promises \
unrealistic payouts, has no clear content/duration rules at all, or reads \
as spam/gibberish. Otherwise mark it GOOD — normal campaigns with clear \
(even strict) rules are fine, being strict is not the same as being a scam.

Campaign requirements text:
\"\"\"
{text}
\"\"\"

Respond with ONLY a JSON object (no markdown fences, no preamble):
{{"is_good": true or false, "reason": "one short sentence explaining why"}}
"""


def ai_score_campaign(raw_text: str) -> CampaignScore:
    if not raw_text or not raw_text.strip():
        return CampaignScore(is_good=False, reason="Empty requirements text.")

    client = _client()
    response = client.messages.create(
        model=MODEL,
        max_tokens=256,
        messages=[{"role": "user", "content": _SCORE_PROMPT.format(text=raw_text)}],
    )
    text = response.content[0].text
    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, IndexError) as exc:
        # If scoring itself fails, don't block the pipeline over it — treat
        # as "good" and let the normal requirements parser/validator catch
        # any real problems downstream.
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
    else:
        print("Unknown mode. Use 'parse' or 'score'.")
