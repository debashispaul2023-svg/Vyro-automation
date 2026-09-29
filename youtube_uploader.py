"""Upload a short to YouTube as public immediately. Gaming + AI disclosure."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = "token.json"
GAMING_CATEGORY_ID = "20"
SHARED_TAGS = ["contentcreator", "viral", "roblox", "shorts"]

SEO_TITLES = {
    "roll anime girls": "I rolled 200 anime girls in this Roblox RNG #shorts",
    "steal a seed": "I stole every seed in this Roblox game #shorts",
    "how to fisch": "This Roblox fishing FPS is actually insane #shorts",
    "tongue escape": "Your tongue keeps GROWING in this Roblox game #shorts",
}


class UploadError(Exception):
    pass


@dataclass
class UploadResult:
    video_id: str
    video_url: str
    publish_at: str = ""


def _seo_title(title: str) -> str:
    raw = re.sub(r"\s+", " ", title or "").strip()
    low = raw.lower().replace("#shorts", "").strip(" -|")
    for key, better in SEO_TITLES.items():
        if low == key or low.startswith(key):
            print(f"[yt] SEO title rewrite: {raw!r} -> {better!r}")
            return better
    if raw and "#shorts" not in raw.lower():
        raw = raw[:88].rstrip() + " #shorts"
    return raw or "Roblox short #shorts"


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
        except Exception as exc:
            raise UploadError(f"Failed to refresh expired OAuth token: {exc}") from exc
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    if not creds or not creds.valid:
        raise UploadError(
            "OAuth token is invalid or missing a refresh token. Re-run the "
            "one-time local setup to regenerate it."
        )
    return creds


def _clean_tags(tags: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in list(tags or []) + SHARED_TAGS:
        token = str(raw or "").strip().lstrip("#")
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(token[:30])
        if len(out) >= 12:
            break
    return out


def _with_hashtags(description: str, tags: list[str]) -> str:
    body = (description or "").rstrip()
    hashes = " ".join(f"#{t}" for t in tags if t)
    if hashes and hashes.lower() not in body.lower():
        body = f"{body}\n\n{hashes}".strip()
    return body[:4900]


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = GAMING_CATEGORY_ID,
    privacy_status: str = "public",
    token_path: str = DEFAULT_TOKEN_PATH,
) -> UploadResult:
    """Always publish public immediately. Same tags as Instagram caption."""
    if not os.path.isfile(video_path):
        raise UploadError(f"Video file not found: {video_path}")

    title = _seo_title(title)
    creds = _load_credentials(token_path)
    tag_list = _clean_tags(tags + ["Roblox", "RobloxRNG", "RollAnimeGirls"])
    desc = _with_hashtags(description, tag_list)
    print(
        f"[yt] category=Gaming({GAMING_CATEGORY_ID}) AI=yes "
        f"privacy=public title={title!r} tags={tag_list}"
    )

    try:
        youtube = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title,
                "description": desc,
                "tags": tag_list,
                "categoryId": category_id or GAMING_CATEGORY_ID,
            },
            "status": {
                "privacyStatus": "public",
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

        video_url = f"https://www.youtube.com/watch?v={video_id}"
        print(f"[yt] public now: {video_url}")
        return UploadResult(video_id=video_id, video_url=video_url, publish_at="")

    except HttpError as exc:
        raise UploadError(f"YouTube API error during upload: {exc}") from exc
    except UploadError:
        raise
    except Exception as exc:
        raise UploadError(f"Unexpected error during YouTube upload: {exc}") from exc


if __name__ == "__main__":
    result = upload_video(
        video_path="output/short.mp4",
        title="Demo upload #shorts",
        description="Test upload from youtube_uploader.py",
        tags=["shorts", "demo"],
    )
    print(f"Uploaded: {result.video_url}")
