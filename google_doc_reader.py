"""
google_doc_reader.py

Some Whop campaigns don't put source-footage on the campaign page —
they link a Google Doc that contains either a direct video URL or
(more often) a Google Drive FOLDER of footage.

This module:
  1. Fetches a publicly viewable Google Doc as plain text
     (Anyone-with-the-link — no login).
  2. Pulls candidate footage URLs out of that text.
  3. If a candidate is a Drive FOLDER, lists the files inside it
     via the Drive API (GOOGLE_DRIVE_API_KEY) and returns the
     video files as downloadable Drive file URLs.

Limitations:
  - Docs and folders must be shared "Anyone with the link can view".
  - A restricted / private folder needs a service account later.
  - Nested folders are walked up to 2 levels deep.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

import requests

_DOC_ID_PATTERN = re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
_FOLDER_ID_PATTERN = re.compile(
    r"drive\.google\.com/(?:drive/)?(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)"
)
_DRIVE_FILE_ID_PATTERN = re.compile(
    r"drive\.google\.com/(?:file/d/|open\?id=)([a-zA-Z0-9_-]+)"
)
_URL_PATTERN = re.compile(r"https?://[^\s\)\]\"'<>]+")

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi")
_FOLDER_MIME = "application/vnd.google-apps.folder"
_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
_DRIVE_LIST_URL = "https://www.googleapis.com/drive/v3/files"
_MAX_FOLDER_DEPTH = 2


class GoogleDocReadError(Exception):
    """Raised when a Google Doc or Drive folder can't be fetched or parsed."""


def _extract_doc_id(doc_url: str) -> str:
    match = _DOC_ID_PATTERN.search(doc_url)
    if not match:
        raise GoogleDocReadError(f"Not a recognizable Google Docs URL: {doc_url}")
    return match.group(1)


def extract_drive_folder_id(url: str) -> str | None:
    match = _FOLDER_ID_PATTERN.search(unquote(url))
    return match.group(1) if match else None


def is_drive_folder_url(url: str) -> bool:
    return extract_drive_folder_id(url) is not None


def drive_file_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/view"


@dataclass
class DriveClip:
    """One video file found inside a Drive folder (or a direct Drive file)."""

    file_id: str
    name: str
    mime_type: str
    size_bytes: int
    url: str
    width: int = 0
    height: int = 0
    duration_ms: int = 0


def extract_drive_file_id(url: str) -> str | None:
    match = _DRIVE_FILE_ID_PATTERN.search(unquote(url))
    return match.group(1) if match else None


def _clip_from_drive_item(item: dict[str, Any], *, file_id: str | None = None) -> DriveClip:
    fid = file_id or item.get("id") or ""
    meta = item.get("videoMediaMetadata") or {}
    try:
        size_bytes = int(item.get("size") or 0)
    except (TypeError, ValueError):
        size_bytes = 0
    try:
        width = int(meta.get("width") or 0)
    except (TypeError, ValueError):
        width = 0
    try:
        height = int(meta.get("height") or 0)
    except (TypeError, ValueError):
        height = 0
    try:
        duration_ms = int(meta.get("durationMillis") or 0)
    except (TypeError, ValueError):
        duration_ms = 0
    return DriveClip(
        file_id=fid,
        name=item.get("name") or fid,
        mime_type=item.get("mimeType") or "",
        size_bytes=size_bytes,
        url=drive_file_url(fid),
        width=width,
        height=height,
        duration_ms=duration_ms,
    )


def fetch_google_doc_text(doc_url: str) -> str:
    """Fetches the plain-text content of a publicly viewable Google Doc."""
    doc_id = _extract_doc_id(doc_url)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"

    resp = requests.get(export_url, timeout=30)
    if resp.status_code != 200:
        raise GoogleDocReadError(
            f"Could not fetch Google Doc (status {resp.status_code}). "
            "It may not be shared as 'Anyone with the link can view'."
        )
    return resp.text


def extract_urls(text: str) -> list[str]:
    """Pulls out every URL mentioned in a block of text, in order."""
    return _URL_PATTERN.findall(text)


def find_candidate_source_clips(doc_text: str) -> list[str]:
    """Raw URL candidates from doc text (may still include Drive folders)."""
    urls = extract_urls(doc_text)
    candidates = []
    seen: set[str] = set()
    for url in urls:
        cleaned = url.rstrip(".,;:)")
        lower = cleaned.lower()
        if any(
            marker in lower
            for marker in (
                "drive.google.com",
                ".mp4",
                "dropbox.com",
                "wetransfer.com",
                "we.tl/",
                "youtube.com",
                "youtu.be",
                "vimeo.com",
            )
        ):
            if cleaned not in seen:
                seen.add(cleaned)
                candidates.append(cleaned)
    return candidates


def _looks_like_video(name: str, mime_type: str) -> bool:
    mime = (mime_type or "").lower()
    if mime.startswith("video/"):
        return True
    lower_name = (name or "").lower()
    return any(lower_name.endswith(ext) for ext in _VIDEO_EXTENSIONS)


def _drive_api_key() -> str:
    key = os.environ.get("GOOGLE_DRIVE_API_KEY", "").strip()
    if not key:
        raise GoogleDocReadError(
            "GOOGLE_DRIVE_API_KEY is missing. Add it as a GitHub secret "
            "and pass it into the workflow env."
        )
    return key


def list_drive_folder_files(folder_id: str, api_key: str) -> list[dict[str, Any]]:
    """Lists non-trashed children of a publicly shared Drive folder."""
    files: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        params: dict[str, Any] = {
            "q": f"'{folder_id}' in parents and trashed = false",
            "fields": (
                "nextPageToken,"
                "files(id,name,mimeType,size,videoMediaMetadata,shortcutDetails)"
            ),
            "pageSize": 100,
            "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
            "key": api_key,
        }
        if page_token:
            params["pageToken"] = page_token

        resp = requests.get(_DRIVE_LIST_URL, params=params, timeout=30)
        if resp.status_code != 200:
            body = resp.text[:400]
            raise GoogleDocReadError(
                f"Drive API could not list folder {folder_id} "
                f"(status {resp.status_code}). {body} "
                "Usual causes: folder is not 'Anyone with the link', "
                "Drive API is not enabled on the key's Cloud project, "
                "or the API key is restricted to HTTP referrers "
                "(must be unrestricted / IP-none for GitHub Actions)."
            )

        payload = resp.json()
        files.extend(payload.get("files") or [])
        page_token = payload.get("nextPageToken")
        if not page_token:
            break

    return files


def collect_video_clips_in_drive_folder(
    folder_url_or_id: str,
    *,
    api_key: str | None = None,
    depth: int = 0,
) -> list[DriveClip]:
    """Walk a Drive folder (2 levels) and return video files with metadata."""
    folder_id = extract_drive_folder_id(folder_url_or_id) or folder_url_or_id
    if not folder_id or "/" in folder_id:
        raise GoogleDocReadError(f"Not a recognizable Drive folder: {folder_url_or_id}")

    key = api_key or _drive_api_key()
    entries = list_drive_folder_files(folder_id, key)
    print(f"[drive] folder {folder_id}: {len(entries)} item(s) at depth {depth}")

    clips: list[DriveClip] = []
    other_files: list[DriveClip] = []

    for item in entries:
        mime = item.get("mimeType") or ""
        name = item.get("name") or ""
        item_id = item.get("id") or ""
        if not item_id:
            continue

        if mime == _SHORTCUT_MIME:
            details = item.get("shortcutDetails") or {}
            target_id = details.get("targetId")
            target_mime = details.get("targetMimeType") or ""
            if target_id and _looks_like_video(name, target_mime):
                clips.append(_clip_from_drive_item(item, file_id=target_id))
            elif target_id and target_mime == _FOLDER_MIME and depth < _MAX_FOLDER_DEPTH:
                clips.extend(
                    collect_video_clips_in_drive_folder(target_id, api_key=key, depth=depth + 1)
                )
            continue

        if mime == _FOLDER_MIME:
            if depth < _MAX_FOLDER_DEPTH:
                clips.extend(
                    collect_video_clips_in_drive_folder(item_id, api_key=key, depth=depth + 1)
                )
            continue

        clip = _clip_from_drive_item(item)
        size = clip.size_bytes or "?"
        print(
            f"[drive]   - {name} ({mime or 'unknown'}, size={size}, "
            f"{clip.width}x{clip.height}, {clip.duration_ms}ms)"
        )
        if _looks_like_video(name, mime):
            clips.append(clip)
        else:
            other_files.append(clip)

    if clips:
        seen: set[str] = set()
        unique: list[DriveClip] = []
        for clip in clips:
            if clip.file_id in seen:
                continue
            seen.add(clip.file_id)
            unique.append(clip)
        return unique

    if len(other_files) == 1:
        print("[drive] no video mime/extension match; using the only file in the folder")
        return other_files

    return []


def find_videos_in_drive_folder(
    folder_url_or_id: str,
    *,
    api_key: str | None = None,
    depth: int = 0,
) -> list[str]:
    """Returns Drive file view-URLs for videos inside a folder."""
    return [
        clip.url
        for clip in collect_video_clips_in_drive_folder(
            folder_url_or_id, api_key=api_key, depth=depth
        )
    ]


def resolve_candidate_source_clips(
    doc_text: str,
    *,
    api_key: str | None = None,
) -> list[str]:
    """Doc text → footage URLs, expanding any Drive folders along the way."""
    raw = find_candidate_source_clips(doc_text)
    resolved: list[str] = []
    seen: set[str] = set()

    for url in raw:
        if is_drive_folder_url(url):
            print(f"[drive] expanding folder: {url}")
            expanded = find_videos_in_drive_folder(url, api_key=api_key)
            for item in expanded:
                if item not in seen:
                    seen.add(item)
                    resolved.append(item)
            continue
        if url not in seen:
            seen.add(url)
            resolved.append(url)

    return resolved


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python google_doc_reader.py <google_doc_url>")
        sys.exit(1)

    text = fetch_google_doc_text(sys.argv[1])
    print("--- Doc text ---")
    print(text[:2000])
    print("--- Resolved source clips ---")
    for url in resolve_candidate_source_clips(text):
        print(url)
