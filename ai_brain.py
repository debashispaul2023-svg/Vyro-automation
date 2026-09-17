"""
ai_brain.py

The "AI brain" for this automation — wraps the Google Gemini API (model:
gemini-3.6-flash, which is on the free tier as of writing — light text
work like this doesn't need a paid/Pro model) for three jobs:

  1. ai_parse_requirements() — reads a campaign's requirement text in ANY
     language or format and extracts structured rules: hashtags, links,
     duration range, referral code, watermark text.

  2. ai_generate_metadata() — writes an actual catchy title/description/
     caption/hashtag set tailored to the clip's content, instead of the
     fixed template in metadata.py. Falls back to the template version if
     the API call fails.

  3. ai_score_campaign() — sanity-checks whether a campaign's requirements
     look legitimate vs. low-quality/scam-like, so daily_runner.py can
     skip bad campaigns and try another platform instead.

Required environment variable: GEMINI_API_KEY
(Get one free at https://aistudio.google.com/apikey)

Note: a paid "Google AI Pro" app subscription (the consumer Gemini chat
app) does NOT include API access or credits — the API is billed
separately. This module intentionally sticks to a free-tier Flash model so
no billing setup is needed at all. Free tier has modest rate limits (a
handful of requests per minute, up to ~1000/day) — comfortably enough for
this pipeline's once-a-day usage, but don't call these functions in a
tight loop. If you ever do set up separate API billing and want more
reasoning power, change MODEL_NAME below to a Pro model — no other code
changes needed.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

MODEL_NAME = "gemini-3.6-flash"


class AIBrainError(Exception):
    """Raised when a Gemini call fails and there is no safe fallback."""


def _model() -> "genai.GenerativeModel":
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise AIBrainError("GEMINI_API_KEY environment variable is not set.")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(MODEL_NAME)


def _call_json(prompt: str, max_output_tokens: int = 1024) -> dict:
    """Sends a prompt to Gemini requesting a strict JSON response, and
    parses it. Raises AIBrainError on any failure (network, bad JSON,
    empty response, etc) so callers can fall back to non-AI logic."""
    try:
        model = _model()
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                response_mime_type="application/json",
                max_output_tokens=max_output_tokens,
                temperature=0.4,
            ),
        )
        text = response.text
    except AIBrainError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AIBrainError(f"Gemini API call failed: {exc}") from exc

    if not text:
        raise AIBrainError("Gemini returned an empty response.")

    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise AIBrainError(f"Could not parse Gemini's JSON response: {exc}\nRaw: {text}") from exc


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
possibly in Bengali, English, a mix, or broken grammar, and possibly noisy \
(pulled from a full webpage, so it may include unrelated navigation text — \
ignore that and find the actual campaign rules). Extract the actual rules \
a video clipper must follow.

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

    data = _call_json(_REQUIREMENTS_PROMPT.format(text=raw_text[:6000]))

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


# ---------------------------------------------------------------------------
# 3. Campaign quality scoring
# ---------------------------------------------------------------------------

@dataclass
class CampaignScore:
    is_good: bool
    reason: str


_SCORE_PROMPT = """\
You are screening a video-clipping campaign brief for a creator whose goal
is genuine virality on Instagram/YouTube Shorts/TikTok — content people
share because it's funny, surprising, satisfying, or dramatic, not
because of a licensed song or a sports league's official highlights.

Mark it LOW QUALITY (reject) if ANY of these apply:
  - It centers on sports footage tied to official/licensed music or a
    league's branded audio (e.g. "FIFA + World Cup Edits", official
    anthem/song requirements) — these have low organic virality and high
    copyright-strike risk regardless of payout.
  - It's a one-off test/leftover listing rather than a real live
    campaign (e.g. anything that reads like "U2", "Geezerbomb",
    "Rockbottom", "world cup edits" — these are known dead test
    campaigns, always reject them outright if mentioned).
  - It's romance/dating/adult-themed content of any kind (not
    appropriate to pursue).
  - It shows scam signs: vague/contradictory rules, asks for sensitive
    personal/financial info beyond a normal payout method, unrealistic
    payout promises, no real content rules, or reads as spam/gibberish/
    nav-junk with no real campaign content.
  - Its budget is already fully used up (e.g. "100% used", "$0
    remaining").

Mark it GOOD if it's a real, live, clearly-ruled campaign about content
with genuine share-appeal: comedy, gaming, satisfying/oddly-satisfying
clips, surprising reveals, clean general entertainment, relatable
everyday moments, or similar — where the creator's own edit/hook does the
work, not a licensed song or sports-league branding.

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

    try:
        data = _call_json(_SCORE_PROMPT.format(text=raw_text[:6000]), max_output_tokens=256)
    except AIBrainError as exc:
        # If scoring itself fails, don't block the pipeline over it.
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


# ---------------------------------------------------------------------------
# 4. Rank official folder clips against campaign rules
# ---------------------------------------------------------------------------

_RANK_PROMPT = """\
You pick official gameplay clips for a paid campaign.

Campaign rules:
{rules}

Clip filenames in the folder:
{names}

Pick the 8 best filenames that likely show the core mechanic (for How to Fisch:
catching fish, fighting, upgrading gear, not a still logo). Skip names like
Game_Image, icon, logo, banner, thumbnail.

Respond with ONLY JSON:
{{
  "ranked_names": ["file1.mp4", "file2.mp4"]
}}
"""


def ai_rank_clip_names(clip_names: list[str], requirements: str) -> list[str]:
    """Return preferred clip filenames. Empty list on failure."""
    names = [n for n in clip_names if n]
    if not names:
        return []
    try:
        data = _call_json(
            _RANK_PROMPT.format(
                rules=(requirements or "")[:3500],
                names="\n".join(names[:80]),
            )
        )
        ranked = [str(x) for x in (data.get("ranked_names") or []) if x]
        print(f"[ai] ranked {len(ranked)} clip name(s)")
        return ranked
    except Exception as exc:
        print(f"[ai] clip rank skipped: {exc}")
        return []


# ---------------------------------------------------------------------------
# 5. Turn campaign rules into which edit tools to run
# ---------------------------------------------------------------------------

_PLAN_PROMPT = """\
Read these campaign rules and decide which edit tools the renderer must run.

Rules:
{rules}

Respond with ONLY JSON:
{{
  "speak_text": "exact words that must be spoken, or empty",
  "cta_text": "on-screen CTA, or empty",
  "end_title": "end-card title like HOW TO FISCH, or empty",
  "need_captions": true,
  "need_spoken_voice": true,
  "need_end_icon": true,
  "need_quality_boost": true,
  "notes": "one line"
}}
If the rules say the name must be spoken, set need_spoken_voice true and speak_text.
If they say show the game icon at the end, set need_end_icon true.
If they reject low quality, set need_quality_boost true.
"""


def ai_plan_edit_tools(requirements: str) -> dict:
    text = (requirements or "").strip()
    low = text.lower()
    ready = any(x in low for x in ("ready to upload", "ready-made", "just upload", "no edit", "do not edit"))
    must_speak = any(x in low for x in ("must be spoken", "spoken somewhere", "voiceover", "voice over", "say the name"))
    must_icon = any(x in low for x in ("game icon", "icon must", "logo at the end", "shown at the end"))
    must_cta = any(x in low for x in ("cta", "call to action", "game is called"))
    must_cap = any(x in low for x in ("on-screen caption", "burned caption", "subtitle"))
    reject_lq = any(x in low for x in ("low-quality", "low quality", "poorly presented"))
    fallback = {
        "speak_text": ("In this Roblox game you catch strange fish, upgrade your gear, and fight. The game is called How to Fisch." if must_speak and "fisch" in low else ""),
        "cta_text": "Game is called How to Fisch on Roblox" if must_cta and "fisch" in low else "",
        "end_title": "HOW TO FISCH" if must_icon and "fisch" in low else "",
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
        data = _call_json(_PLAN_PROMPT.format(rules=text[:4000]))
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
