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


def _check_duration(duration: float, req: CampaignRequirements) -> Optional[str]:
    if duration < req.min_seconds - DURATION_TOLERANCE_SECONDS:
        return (
            f"Video duration {duration:.2f}s is below campaign minimum "
            f"{req.min_seconds}s."
        )
    if duration > req.max_seconds + DURATION_TOLERANCE_SECONDS:
        return (
            f"Video duration {duration:.2f}s exceeds campaign maximum "
            f"{req.max_seconds}s."
        )
    return None


def _check_resolution(width: int, height: int) -> Optional[str]:
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
    """
    Runs all compliance checks and returns a CheckResult. Does NOT raise —
    use `validate_or_raise` if you want the halt-on-failure behavior.
    """
    checks: dict[str, bool] = {}
    failures: list[str] = []

    def _run(name: str, fn) -> None:  # noqa: ANN001
        error = fn()
        checks[name] = error is None
        if error:
            failures.append(error)

    _run("hashtags_present", lambda: _check_hashtags(title, description, req))
    _run("shorts_spacing", lambda: _check_shorts_spacing(title))
    _run("campaign_links_present", lambda: _check_links(description, req))

    if VideoFileClip is None:
        error = "moviepy is not installed; cannot verify video duration and resolution."
        checks["duration_within_range"] = False
        checks["resolution_9x16"] = False
        failures.append(error)
    elif not os.path.isfile(video_path):
        error = f"Video file not found for checks: {video_path}"
        checks["duration_within_range"] = False
        checks["resolution_9x16"] = False
        failures.append(error)
    else:
        clip = None
        try:
            # OPTIMIZATION: Open the video file exactly once to extract all required
            # properties (duration, width, height). Instantiating VideoFileClip triggers
            # expensive ffprobe subprocess calls. Caching the properties here avoids
            # redundantly re-opening the file in multiple separate check functions.
            clip = VideoFileClip(video_path)
            duration = clip.duration
            width = clip.w
            height = clip.h
        except Exception as exc:  # noqa: BLE001
            error = f"Failed to read video properties: {exc}"
            checks["duration_within_range"] = False
            checks["resolution_9x16"] = False
            failures.append(error)
            duration, width, height = None, None, None
        finally:
            if clip is not None:
                clip.close()

        if duration is not None and width is not None and height is not None:
            _run("duration_within_range", lambda: _check_duration(duration, req))
            _run("resolution_9x16", lambda: _check_resolution(width, height))

    return CheckResult(passed=not failures, checks=checks, failures=failures)


def validate_or_raise(
    video_path: str,
    title: str,
    description: str,
    req: CampaignRequirements,
) -> CheckResult:
    """
    Same as run_pre_upload_checks, but raises ValidationError (halting the
    upload) if any check failed. Use this directly in front of the YouTube
    upload API call.
    """
    result = run_pre_upload_checks(video_path, title, description, req)
    if not result.passed:
        raise ValidationError(result.failures)
    return result


if __name__ == "__main__":
    from requirements_parser import parse_campaign

    demo_req = parse_campaign(
        {
            "campaign_id": "demo",
            "mandatory_hashtags": ["#mrbeast"],
            "required_links": ["https://vyro.ai/c/demo"],
            "min_seconds": 20,
            "max_seconds": 35,
        }
    )
    try:
        validate_or_raise(
            video_path="output/short_demo.mp4",
            title="He gave away $100,000 #mrbeast #shorts",
            description="Full video linked below.\nhttps://vyro.ai/c/demo\n#mrbeast #shorts",
            req=demo_req,
        )
        print("All checks passed. Safe to upload.")
    except ValidationError as e:
        print(e)
