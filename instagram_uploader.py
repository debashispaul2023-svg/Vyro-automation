"""Instagram uploader disabled. Account is shadowbanned; YouTube is the only publish target."""

from __future__ import annotations


class InstagramUploadError(Exception):
    pass


def host_video_publicly(video_path: str, release_tag: str) -> str:
    raise InstagramUploadError("Instagram disabled")


def upload_reel(video_path: str, caption: str, release_tag: str) -> str:
    print("[ig] skipped — Instagram disabled (shadowban). YouTube only.")
    raise InstagramUploadError("Instagram disabled (shadowban)")


def fetch_reel_permalink(media_id: str) -> str:
    return ""
