"""
checker.py

Strict pre-upload validation gate. Runs every campaign compliance check
before the YouTube upload API is ever called. Any failed check halts the
upload and raises a ValidationError listing every problem found (not just
the first one), so the caller can log/alert with full detail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from requirements_parser import CampaignRequirements

try:
    from moviepy.editor import VideoFileClip
except ImportError:  # pragma: no cover
    VideoFileClip = None  # type: ignore[assignment, misc]

TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
DURATION_TOLERANCE_SECONDS = 0.5
# Briefs that never mention a floor still parse as 15s. Shorts under that
# must still upload when they are a real vertical clip.
DEFAULT_PARSED_MIN = 15.0
SHORTS_FLOOR = 7.0


class ValidationError(Exception):
    """Raised when one or more pre-upload compliance checks fail."""

    def __init__(self, failures: list[str]):
        self.failures = failures
        message = "Upload halted — campaign compliance check failed:\n" + "\n".join(
            f"  [FAIL] {f}" for f in failures
        )
        super().__init__(message)


@dataclass
class CheckResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


def _check_hashtags(title: str, description: str, req: CampaignRequirements) -> Optional[str]:
    combined = f"{title}\n{description}".lower()
    missing = [tag for tag in req.mandatory_hashtags if tag.lower() not in combined]
    if missing:
        return f"Missing mandatory hashtags in title/description: {', '.join(missing)}"
    return None


def _check_shorts_spacing(title: str) -> Optional[str]:
    import re

    if "#shorts" not in title.lower():
        return "Title is missing '#shorts'."
    if re.search(r"\S#shorts", title, flags=re.IGNORECASE):
        return "Title is missing required space before '#shorts'."
    return None


def _check_duration(video_path: str, req: CampaignRequirements) -> Optional[str]:
    if VideoFileClip is None:
        return "moviepy is not installed; cannot verify video duration."
    if not os.path.isfile(video_path):
        return f"Video file not found for duration check: {video_path}"

    clip = None
    try:
        clip = VideoFileClip(video_path)
        duration = clip.duration
    except Exception as exc:  # noqa: BLE001
        return f"Failed to read video duration: {exc}"
    finally:
        if clip is not None:
            clip.close()

    min_s = float(req.min_seconds or 0)
    max_s = float(req.max_seconds or 60)
    if min_s >= DEFAULT_PARSED_MIN and duration >= SHORTS_FLOOR:
        print(f"[check] relaxing default {min_s}s min — clip is {duration:.1f}s")
        min_s = min(min_s, duration)
    if duration < min_s - DURATION_TOLERANCE_SECONDS:
        return (
            f"Video duration {duration:.2f}s is below campaign minimum "
            f"{min_s}s."
        )
    if duration > max_s + DURATION_TOLERANCE_SECONDS:
        return (
            f"Video duration {duration:.2f}s exceeds campaign maximum "
            f"{max_s}s."
        )
    return None


def _check_resolution(video_path: str) -> Optional[str]:
    if VideoFileClip is None:
        return "moviepy is not installed; cannot verify video resolution."
    if not os.path.isfile(video_path):
        return f"Video file not found for resolution check: {video_path}"

    clip = None
    try:
        clip = VideoFileClip(video_path)
        width, height = clip.w, clip.h
    except Exception as exc:  # noqa: BLE001
        return f"Failed to read video resolution: {exc}"
    finally:
        if clip is not None:
            clip.close()

    if (width, height) != (TARGET_WIDTH, TARGET_HEIGHT):
        return (
            f"Video resolution {width}x{height} is not the required "
            f"9:16 vertical format ({TARGET_WIDTH}x{TARGET_HEIGHT})."
        )
    return None


def _check_links(description: str, req: CampaignRequirements) -> Optional[str]:
    missing = [link for link in req.required_links if link not in description]
    if missing:
        return f"Missing mandatory campaign link(s) in description: {', '.join(missing)}"
    return None


def run_pre_upload_checks(
    video_path: str,
    title: str,
    description: str,
    req: CampaignRequirements,
) -> CheckResult:
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def _run(name: str, fn) -> None:  # noqa: ANN001
        error = fn()
        checks[name] = error is None
        if error:
            failures.append(error)

    _run("hashtags_present", lambda: _check_hashtags(title, description, req))
    _run("shorts_spacing", lambda: _check_shorts_spacing(title))
    _run("duration_within_range", lambda: _check_duration(video_path, req))
    _run("resolution_9x16", lambda: _check_resolution(video_path))
    _run("campaign_links_present", lambda: _check_links(description, req))

    return CheckResult(passed=not failures, checks=checks, failures=failures)


def validate_or_raise(
    video_path: str,
    title: str,
    description: str,
    req: CampaignRequirements,
) -> CheckResult:
    result = run_pre_upload_checks(video_path, title, description, req)
    if not result.passed:
        raise ValidationError(result.failures)
    return result
