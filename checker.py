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


def _check_video_properties(video_path: str, req: CampaignRequirements) -> tuple[Optional[str], Optional[str]]:
    """
    ⚡ Bolt: Batch video property checks (duration and resolution) into a single function.
    Opening a VideoFileClip is expensive because it spawns an ffmpeg subprocess to parse
    the file header. By passing audio=False and opening the clip only once, we halve the
    I/O overhead and avoid redundant ffmpeg calls during validation.
    """
    if VideoFileClip is None:
        return (
            "moviepy is not installed; cannot verify video duration.",
            "moviepy is not installed; cannot verify video resolution."
        )
    if not os.path.isfile(video_path):
        return (
            f"Video file not found for duration check: {video_path}",
            f"Video file not found for resolution check: {video_path}"
        )

    clip = None
    try:
        clip = VideoFileClip(video_path, audio=False)
        duration = clip.duration
        width, height = clip.w, clip.h
    except Exception as exc:  # noqa: BLE001
        return (f"Failed to read video properties: {exc}", f"Failed to read video properties: {exc}")
    finally:
        if clip is not None:
            clip.close()

    dur_error = None
    if duration < req.min_seconds - DURATION_TOLERANCE_SECONDS:
        dur_error = (
            f"Video duration {duration:.2f}s is below campaign minimum "
            f"{req.min_seconds}s."
        )
    elif duration > req.max_seconds + DURATION_TOLERANCE_SECONDS:
        dur_error = (
            f"Video duration {duration:.2f}s exceeds campaign maximum "
            f"{req.max_seconds}s."
        )

    res_error = None
    if (width, height) != (TARGET_WIDTH, TARGET_HEIGHT):
        res_error = (
            f"Video resolution {width}x{height} is not the required "
            f"9:16 vertical format ({TARGET_WIDTH}x{TARGET_HEIGHT})."
        )

    return dur_error, res_error


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

    dur_err, res_err = _check_video_properties(video_path, req)
    checks["duration_within_range"] = dur_err is None
    if dur_err:
        failures.append(dur_err)
    checks["resolution_9x16"] = res_err is None
    if res_err:
        failures.append(res_err)

    _run("campaign_links_present", lambda: _check_links(description, req))

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
