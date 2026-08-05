"""
metadata.py

Generates YouTube Shorts metadata (title + description) that complies with
Vyro campaign requirements: mandatory hashtags, referral links, and strict
formatting rules (e.g. a space must always precede '#shorts').
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from requirements_parser import CampaignRequirements

MAX_TITLE_LENGTH = 100  # YouTube hard limit
MAX_DESCRIPTION_LENGTH = 5000  # YouTube hard limit


class MetadataError(Exception):
    """Raised when metadata cannot be generated or fails formatting rules."""


@dataclass
class VideoMetadata:
    title: str
    description: str
    tags: list[str]


def _clean_hook(hook: str) -> str:
    hook = re.sub(r"\s+", " ", hook).strip()
    hook = hook.rstrip("#").strip()
    return hook


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _ensure_space_before_shorts(title: str) -> str:
    """
    Guarantees exactly one space precedes every '#shorts' occurrence, and
    collapses accidental double spaces / missing spaces (e.g. 'video#shorts'
    or 'video  #shorts' both become 'video #shorts').
    """
    # Insert a space before #shorts if immediately preceded by a non-space char.
    title = re.sub(r"(?<=\S)(#shorts)", r" \1", title, flags=re.IGNORECASE)
    # Collapse multiple spaces before #shorts down to exactly one.
    title = re.sub(r"\s{2,}(#shorts)", r" \1", title, flags=re.IGNORECASE)
    return title


def generate_title(
    hook: str,
    req: CampaignRequirements,
    extra_hashtags: list[str] | None = None,
) -> str:
    """
    Builds a viral, high-CTR title: "<hook> <mandatory hashtags> #shorts".
    Ensures '#shorts' is present exactly once with correct spacing, and that
    all mandatory campaign hashtags are included.
    """
    if not hook or not hook.strip():
        raise MetadataError("Hook/title text cannot be empty.")

    hook = _clean_hook(hook)

    hashtags = list(req.mandatory_hashtags) + list(extra_hashtags or [])
    hashtags = [h if h.startswith("#") else f"#{h}" for h in hashtags]
    hashtags = _dedupe_preserve_order(hashtags)

    # Remove any existing #shorts from the list; we append it explicitly last.
    hashtags = [h for h in hashtags if h.lower() != "#shorts"]

    title_parts = [hook] + hashtags + ["#shorts"]
    title = " ".join(part for part in title_parts if part)
    title = _ensure_space_before_shorts(title)
    title = re.sub(r"\s+", " ", title).strip()

    if len(title) > MAX_TITLE_LENGTH:
        # Trim the hook portion first, keep hashtags + #shorts intact.
        hashtag_suffix = " " + " ".join(hashtags + ["#shorts"])
        allowed_hook_len = MAX_TITLE_LENGTH - len(hashtag_suffix)
        if allowed_hook_len < 1:
            raise MetadataError(
                "Mandatory hashtags alone exceed the YouTube title length limit."
            )
        trimmed_hook = hook[:allowed_hook_len].rstrip()
        title = _ensure_space_before_shorts(trimmed_hook + hashtag_suffix)

    if not re.search(r"(?<!\S)#shorts\b", title, flags=re.IGNORECASE):
        raise MetadataError("Failed to enforce '#shorts' presence in title.")
    if re.search(r"\S#shorts", title, flags=re.IGNORECASE):
        raise MetadataError("Failed to enforce space before '#shorts' in title.")

    return title


def generate_description(
    summary: str,
    req: CampaignRequirements,
    extra_tags: list[str] | None = None,
) -> str:
    """
    Builds a description containing the mandatory campaign links, referral
    code, and hashtags/tags, appended after a short human-readable summary.
    """
    summary = (summary or "").strip()

    lines: list[str] = []
    if summary:
        lines.append(summary)
        lines.append("")

    if req.required_links:
        lines.append("🔗 Link:")
        for link in req.required_links:
            lines.append(link)
        lines.append("")

    if req.referral_code:
        lines.append(f"Use code '{req.referral_code}' for a special offer!")
        lines.append("")

    all_tags = _dedupe_preserve_order(
        [h if h.startswith("#") else f"#{h}" for h in req.mandatory_hashtags]
        + [t if t.startswith("#") else f"#{t}" for t in (req.extra_tags + (extra_tags or []))]
    )
    if "#shorts" not in [t.lower() for t in all_tags]:
        all_tags.append("#shorts")

    lines.append(" ".join(all_tags))

    description = "\n".join(lines).strip()

    if len(description) > MAX_DESCRIPTION_LENGTH:
        description = description[:MAX_DESCRIPTION_LENGTH].rstrip()

    for link in req.required_links:
        if link not in description:
            raise MetadataError(
                f"Mandatory campaign link missing from description: {link}"
            )

    return description


def generate_metadata(
    hook: str,
    summary: str,
    req: CampaignRequirements,
    extra_hashtags: list[str] | None = None,
) -> VideoMetadata:
    title = generate_title(hook, req, extra_hashtags)
    description = generate_description(summary, req, extra_hashtags)
    tags = _dedupe_preserve_order(
        [h.lstrip("#") for h in req.mandatory_hashtags]
        + [t.lstrip("#") for t in req.extra_tags]
        + (extra_hashtags or [])
        + ["shorts"]
    )
    return VideoMetadata(title=title, description=description, tags=tags)


if __name__ == "__main__":
    from requirements_parser import parse_campaign

    req = parse_campaign(
        {
            "campaign_id": "demo",
            "mandatory_hashtags": ["mrbeast"],
            "required_links": ["https://vyro.ai/c/demo"],
            "referral_code": "MRBEAST",
        }
    )
    meta = generate_metadata(
        hook="He gave away $100,000 in 60 seconds",
        summary="Clipped from the original livestream.",
        req=req,
    )
    print(meta.title)
    print("---")
    print(meta.description)
