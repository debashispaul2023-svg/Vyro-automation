"""Upload a short to YouTube. Next uploads stay unlisted until YOUTUBE_PRIVACY=public."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = "token.json"
GAMING_CATEGORY_ID = "20"
SHARED_TAGS = ["Roll Anime Girls", "Roblox", "Roblox RNG", "shorts", "tycoon", "offline money"]
PUBLISHED_LOG = "published_log.json"

# Official campaign lines — never paraphrase these.
CAMPAIGN_LOCK = {
    "roll anime girls": {
        "name": "Roll Anime Girls",
        "cta": "Game is called Roll Anime Girls on Roblox.",
        "link": "https://www.roblox.com/games/92289737492030/Roll-Anime-Girls",
    },
    "how to fisch": {
        "name": "How to Fisch",
        "cta": "Game is called How to Fisch on Roblox.",
        "link": "https://www.roblox.com/games/119870009085173/How-to-Fisch",
    },
    "tongue escape": {
        "name": "+1 Tongue Escape",
        "cta": "Game is called +1 Tongue Escape on Roblox.",
        "link": "",
    },
    "steal a seed": {
        "name": "Steal A Seed",
        "cta": "Game is called Steal A Seed on Roblox.",
        "link": "https://www.roblox.com/games/122216176958450/Steal-A-Seed",
    },
}


class UploadError(Exception):
    pass


@dataclass
class UploadResult:
    video_id: str
    video_url: str
    publish_at: str = ""


def record_published_video(video_id: str, title: str = "", video_url: str = "") -> None:
    vid = (video_id or "").strip()
    if not vid:
        return
    rows: list = []
    if os.path.isfile(PUBLISHED_LOG):
        try:
            rows = json.loads(open(PUBLISHED_LOG, encoding="utf-8").read())
        except Exception:
            rows = []
    if not isinstance(rows, list):
        rows = []
    if any((r or {}).get("video_id") == vid for r in rows if isinstance(r, dict)):
        return
    rows.append(
        {
            "video_id": vid,
            "title": title or "",
            "video_url": video_url or f"https://www.youtube.com/watch?v={vid}",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    try:
        with open(PUBLISHED_LOG, "w", encoding="utf-8") as f:
            json.dump(rows[-400:], f, indent=2)
        print(f"[yt] logged {vid} in {PUBLISHED_LOG}")
    except Exception as exc:
        print(f"[yt] published_log write skipped: {exc}")


def _match_key(text: str) -> str:
    low = (text or "").lower()
    for key in CAMPAIGN_LOCK:
        if key in low:
            return key
    return ""


def _lock_title(title: str) -> str:
    """Keep the campaign title. Only add the official game name and #shorts if missing."""
    raw = re.sub(r"\s+", " ", title or "").strip()
    key = _match_key(raw)
    lock = CAMPAIGN_LOCK.get(key) or {}
    name = lock.get("name") or ""
    if name and name.lower() not in raw.lower():
        raw = f"{name} {raw}".strip()
        print(f"[yt] inserted official name into title: {name}")
    if raw and "#shorts" not in raw.lower():
        raw = raw[:88].rstrip() + " #shorts"
    raw = raw[:100].rstrip()
    print(f"[yt] requirement title kept: {raw!r}")
    return raw or "Roblox short #shorts"


def _lock_description(title: str, description: str) -> str:
    """Keep campaign description. Append official CTA + game link word-for-word if absent."""
    body = (description or "").strip()
    key = _match_key(title + " " + body)
    lock = CAMPAIGN_LOCK.get(key) or {}
    cta = (lock.get("cta") or "").strip()
    link = (lock.get("link") or "").strip()
    if cta and cta.lower() not in body.lower():
        body = f"{body}\n{cta}".strip() if body else cta
        print(f"[yt] appended official CTA word-for-word")
    if link and link not in body:
        body = f"{body}\n{link}".strip()
        print(f"[yt] appended official game link")
    return body


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


def _privacy(requested: str) -> str:
    raw = (os.environ.get("YOUTUBE_PRIVACY") or requested or "unlisted").strip().lower()
    if raw not in ("public", "unlisted", "private"):
        raw = "unlisted"
    return raw


def upload_video(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str = GAMING_CATEGORY_ID,
    privacy_status: str = "unlisted",
    token_path: str = DEFAULT_TOKEN_PATH,
) -> UploadResult:
    """Upload unlisted unless YOUTUBE_PRIVACY=public."""
    if not os.path.isfile(video_path):
        raise UploadError(f"Video file not found: {video_path}")

    title = _lock_title(title)
    description = _lock_description(title, description)
    creds = _load_credentials(token_path)
    extra = []
    if "roll anime" in title.lower():
        extra = ["RollAnimeGirls", "RobloxRNG", "Roblox"]
    elif "fisch" in title.lower():
        extra = ["HowToFisch", "Fisch", "Roblox"]
    tag_list = _clean_tags(list(tags or []) + extra)
    desc = _with_hashtags(description, tag_list)
    privacy = _privacy(privacy_status)
    print(
        f"[yt] MAIN category=Gaming({GAMING_CATEGORY_ID}) AI=yes "
        f"privacy={privacy} title={title!r} tags={tag_list}"
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
                "privacyStatus": privacy,
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
        print(f"[yt] {privacy} now: {video_url}")
        record_published_video(video_id, title, video_url)
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
