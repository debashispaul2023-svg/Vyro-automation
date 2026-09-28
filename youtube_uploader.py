"""
youtube_uploader.py

Uploads a rendered short to YouTube using the YouTube Data API v3.
Videos are Gaming category, marked as AI/synthetic media, uploaded private,
and scheduled public via YouTube's native publishAt (one video every 10 hours).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = "token.json"
GAMING_CATEGORY_ID = "20"
SCHEDULE_HOURS = 10
SCHEDULE_PATH = "youtube_schedule.json"


class UploadError(Exception):
    """Raised when authentication or upload to YouTube fails."""


@dataclass
class UploadResult:
    video_id: str
    video_url: str
    publish_at: str = ""


def _load_credentials(token_path: str = DEFAULT_TOKEN_PATH) -> Credentials:
    if not os.path.isfile(token_path):
        raise UploadError(
            f"OAuth token file not found at '{token_path}'. Run the one-time "
            f"local setup (see README) to generate it before uploading."
        )

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"Failed to refresh expired OAuth token: {exc}") from exc
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise UploadError(
            "OAuth token is invalid or missing a refresh token. Re-run the "
            "one-time local setup to regenerate it."
        )

    return creds


def _parse_iso(value: str) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _next_publish_slot() -> datetime:
    """Queue slots 10 hours apart. Never schedule in the past."""
    now = datetime.now(timezone.utc)
    floor = now + timedelta(minutes=20)
    data: dict = {}
    if os.path.isfile(SCHEDULE_PATH):
        try:
            with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
    queued = _parse_iso(str(data.get("next_publish_at") or ""))
    if queued and queued > floor:
        return queued
    last = _parse_iso(str(data.get("last_publish_at") or ""))
    if last:
        nxt = last + timedelta(hours=SCHEDULE_HOURS)
        if nxt > floor:
            return nxt
    return floor + timedelta(hours=SCHEDULE_HOURS)


def _remember_slot(slot: datetime, video_id: str) -> None:
    nxt = slot + timedelta(hours=SCHEDULE_HOURS)
    payload = {
        "last_publish_at": slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "next_publish_at": nxt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_video_id": video_id,
        "interval_hours": SCHEDULE_HOURS,
    }
    with open(SCHEDULE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = GAMING_CATEGORY_ID,
    privacy_status: str = "private",
    token_path: str = DEFAULT_TOKEN_PATH,
) -> UploadResult:
    """
    Upload as private + schedule public with YouTube publishAt.
    Unlisted is upgraded to scheduled-private because publishAt only works on private.
    """
    if not os.path.isfile(video_path):
        raise UploadError(f"Video file not found: {video_path}")

    creds = _load_credentials(token_path)
    slot = _next_publish_slot()
    publish_at = slot.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(
        f"[yt] category=Gaming({GAMING_CATEGORY_ID}) AI=yes "
        f"privacy=private schedule={publish_at} (YouTube publishAt, +{SCHEDULE_HOURS}h queue)"
    )
    if privacy_status == "unlisted":
        print("[yt] unlisted requested — using private+schedule so Studio can auto-public")

    try:
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id or GAMING_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": "private",
                "publishAt": publish_at,
                "selfDeclaredMadeForKids": False,
                "containsSyntheticMedia": True,
            },
        }
        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                print(f"Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        if not video_id:
            raise UploadError(f"Upload succeeded but no video ID returned: {response}")

        _remember_slot(slot, video_id)
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[yt] scheduled public at {publish_at}: {video_url}")
        return UploadResult(video_id=video_id, video_url=video_url, publish_at=publish_at)

    except HttpError as exc:
        raise UploadError(f"YouTube API error during upload: {exc}") from exc
    except UploadError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise UploadError(f"Unexpected error during YouTube upload: {exc}") from exc


if __name__ == "__main__":
    result = upload_video(
        video_path="output/short.mp4",
        title="Demo upload #shorts",
        description="Test upload from youtube_uploader.py",
        tags=["shorts", "demo"],
    )
    print(f"Uploaded: {result.video_url} schedule={result.publish_at}")
