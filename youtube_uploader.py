"""
youtube_uploader.py

Uploads a rendered short to YouTube using the YouTube Data API v3.
Authentication uses a pre-generated OAuth token (see README for the
one-time local setup step) — this module itself never opens a browser,
so it works both locally and inside GitHub Actions (CI).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = "token.json"


class UploadError(Exception):
    """Raised when authentication or upload to YouTube fails."""


@dataclass
class UploadResult:
    video_id: str
    video_url: str


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
        # Persist the refreshed token so the next run doesn't need to refresh again.
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise UploadError(
            "OAuth token is invalid or missing a refresh token. Re-run the "
            "one-time local setup to regenerate it."
        )

    return creds


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = "22",  # "People & Blogs" — change if a better fit exists
    privacy_status: str = "public",  # "public", "unlisted", or "private"
    token_path: str = DEFAULT_TOKEN_PATH,
) -> UploadResult:
    """
    Uploads video_path to YouTube and returns the resulting video ID + URL.
    Raises UploadError on any authentication or API failure.
    """
    if not os.path.isfile(video_path):
        raise UploadError(f"Video file not found: {video_path}")

    creds = _load_credentials(token_path)

    try:
        youtube = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags,
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
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

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        return UploadResult(video_id=video_id, video_url=video_url)

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
        privacy_status="private",
    )
    print(f"Uploaded: {result.video_url}")
