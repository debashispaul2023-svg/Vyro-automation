"""
requirements_parser.py

Parses Vyro campaign configuration (JSON dict, JSON string, or free-text prompt)
and extracts a normalized, strongly-typed CampaignRequirements object that the
rest of the pipeline (renderer, metadata, checker) can rely on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional


class RequirementsParseError(Exception):
    """Raised when campaign requirements cannot be parsed or are invalid."""


@dataclass
class CampaignRequirements:
    campaign_id: str = "unknown"
    mandatory_hashtags: list[str] = field(default_factory=list)
    required_links: list[str] = field(default_factory=list)
    target_audio_id: Optional[str] = None
    target_audio_name: Optional[str] = None
    overlay_text: Optional[str] = None
    watermark_text: Optional[str] = None
    min_seconds: float = 15.0
    max_seconds: float = 60.0
    referral_code: Optional[str] = None
    extra_tags: list[str] = field(default_factory=list)
    raw_source: Any = None

    def validate(self) -> None:
        if self.min_seconds < 0:
            raise RequirementsParseError("min_seconds cannot be negative.")
        if self.max_seconds <= 0:
            raise RequirementsParseError("max_seconds must be positive.")
        if self.min_seconds > self.max_seconds:
            raise RequirementsParseError(
                f"min_seconds ({self.min_seconds}) cannot exceed "
                f"max_seconds ({self.max_seconds})."
            )
        for tag in self.mandatory_hashtags:
            if not tag.startswith("#"):
                raise RequirementsParseError(
                    f"Hashtag '{tag}' must start with '#'."
                )


_HASHTAG_RE = re.compile(r"#\w+")
_URL_RE = re.compile(r"https?://\S+")
_DURATION_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?\s*(?:-|to|–)\s*"
    r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs|seconds)?",
    re.IGNORECASE,
)
_CODE_RE = re.compile(r"code\s*['\"]?([A-Za-z0-9_\-]+)['\"]?", re.IGNORECASE)


def _normalize_hashtag(tag: str) -> str:
    tag = tag.strip()
    if not tag:
        return tag
    if not tag.startswith("#"):
        tag = f"#{tag}"
    return tag


def parse_from_dict(data: dict) -> CampaignRequirements:
    """Parse a Vyro campaign JSON object (already decoded) into requirements."""
    try:
        hashtags = [
            _normalize_hashtag(t)
            for t in data.get("mandatory_hashtags", data.get("hashtags", []))
        ]
        links = list(data.get("required_links", data.get("links", [])))

        audio = data.get("audio", {}) if isinstance(data.get("audio"), dict) else {}
        target_audio_id = data.get("target_audio_id") or audio.get("id")
        target_audio_name = data.get("target_audio_name") or audio.get("name")

        duration = data.get("duration", {}) if isinstance(data.get("duration"), dict) else {}
        min_seconds = float(
            data.get("min_seconds", duration.get("min_seconds", 15.0))
        )
        max_seconds = float(
            data.get("max_seconds", duration.get("max_seconds", 60.0))
        )

        req = CampaignRequirements(
            campaign_id=str(data.get("campaign_id", "unknown")),
            mandatory_hashtags=hashtags,
            required_links=links,
            target_audio_id=target_audio_id,
            target_audio_name=target_audio_name,
            overlay_text=data.get("overlay_text"),
            watermark_text=data.get("watermark_text") or data.get("watermark"),
            min_seconds=min_seconds,
            max_seconds=max_seconds,
            referral_code=data.get("referral_code"),
            extra_tags=list(data.get("extra_tags", [])),
            raw_source=data,
        )
        req.validate()
        return req
    except RequirementsParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RequirementsParseError(f"Failed to parse campaign dict: {exc}") from exc


def parse_from_json_string(json_str: str) -> CampaignRequirements:
    """Parse a raw JSON string (e.g. from Vyro API response body)."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise RequirementsParseError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RequirementsParseError("Top-level JSON must be an object.")
    return parse_from_dict(data)


def parse_from_text_prompt(prompt: str) -> CampaignRequirements:
    """
    Best-effort extraction of campaign requirements from a free-text brief,
    e.g. "Use hashtags #mrbeast #shorts, must be 20-35s, use code 'MRBEAST',
    link https://vyro.ai/campaign/123".
    """
    if not prompt or not prompt.strip():
        raise RequirementsParseError("Empty text prompt provided.")

    hashtags = [_normalize_hashtag(t) for t in _HASHTAG_RE.findall(prompt)]
    links = _URL_RE.findall(prompt)

    min_seconds, max_seconds = 15.0, 60.0
    range_match = _DURATION_RANGE_RE.search(prompt)
    if range_match:
        min_seconds = float(range_match.group(1))
        max_seconds = float(range_match.group(2))

    code_match = _CODE_RE.search(prompt)
    referral_code = code_match.group(1) if code_match else None

    watermark_text = f"Use code '{referral_code}'" if referral_code else None

    req = CampaignRequirements(
        campaign_id="text-prompt",
        mandatory_hashtags=hashtags,
        required_links=links,
        min_seconds=min_seconds,
        max_seconds=max_seconds,
        referral_code=referral_code,
        watermark_text=watermark_text,
        raw_source=prompt,
    )
    req.validate()
    return req


def parse_campaign(source: Any) -> CampaignRequirements:
    """
    Universal entry point. Accepts:
      - dict (already-decoded Vyro JSON/API response)
      - str that looks like JSON (starts with '{' or '[')
      - str that is a free-text prompt/brief
    """
    if isinstance(source, dict):
        return parse_from_dict(source)
    if isinstance(source, str):
        stripped = source.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return parse_from_json_string(stripped)
        return parse_from_text_prompt(stripped)
    raise RequirementsParseError(
        f"Unsupported source type for campaign parsing: {type(source)}"
    )


if __name__ == "__main__":
    example = {
        "campaign_id": "vyro_001",
        "mandatory_hashtags": ["mrbeast", "#shorts"],
        "required_links": ["https://vyro.ai/c/vyro_001"],
        "target_audio_id": "sound_998877",
        "watermark_text": "Use code 'MRBEAST'",
        "min_seconds": 20,
        "max_seconds": 35,
        "referral_code": "MRBEAST",
    }
    parsed = parse_campaign(example)
    print(parsed)
