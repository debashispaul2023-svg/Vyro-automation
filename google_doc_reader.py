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




def _unwrap_google_redirect(url: str) -> str:
    """docs.google.com wraps real hrefs as https://www.google.com/url?q=..."""
    from urllib.parse import parse_qs, unquote, urlparse

    if "google.com/url" not in url or "q=" not in url:
        return url
    qs = parse_qs(urlparse(url).query)
    real = (qs.get("q") or [""])[0]
    return unquote(real) if real else url


def _html_export_links(doc_id: str) -> list[str]:
    """TXT export drops hyperlinks. HTML export keeps the real hrefs."""
    from html import unescape

    resp = requests.get(
        f"https://docs.google.com/document/d/{doc_id}/export?format=html",
        timeout=30,
    )
    if resp.status_code != 200:
        return []
    links: list[str] = []
    for raw in re.findall(r'href="([^"]+)"', resp.text):
        url = _unwrap_google_redirect(unescape(raw)).rstrip(".,;:)")
        if url.startswith("http"):
            links.append(url)
    return links


def fetch_google_doc_text(doc_url: str) -> str:
    """Fetches a public Google Doc as text, plus hidden hyperlink URLs."""
    doc_id = _extract_doc_id(doc_url)
    resp = requests.get(
        f"https://docs.google.com/document/d/{doc_id}/export?format=txt",
        timeout=30,
    )
    if resp.status_code != 200:
        raise GoogleDocReadError(
            f"Could not fetch Google Doc (status {resp.status_code}). "
            "It may not be shared as 'Anyone with the link can view'."
        )
    text = resp.text
    extra = _html_export_links(doc_id)
    if extra:
        print(f"[doc] HTML export found {len(extra)} hyperlink(s): {extra[:5]}")
        text = text + "\n" + "\n".join(extra)
    return text


def extract_urls(text: str) -> list[str]:
    """Pulls out every URL mentioned in a block of text, in order."""
    return [_unwrap_google_redirect(u) for u in _URL_PATTERN.findall(text)]


def download_mediasilo_review(review_url: str, dest_path: str) -> str:
    """Open a public MediaSilo review link and save the first real video file.

    MediaSilo is a JS app — there is no Drive-style file list. We intercept
    mp4/m3u8 network responses after the gallery loads.
    """
    from playwright.sync_api import sync_playwright

    hits: list[str] = []

    def _on_response(resp) -> None:
        url = resp.url or ""
        low = url.lower()
        ctype = (resp.headers or {}).get("content-type", "").lower()
        if resp.status != 200:
            return
        if (
            ".mp4" in low
            or "video/" in ctype
            or "application/vnd.apple.mpegurl" in ctype
            or ".m3u8" in low
        ):
            if url not in hits:
                hits.append(url)

    print(f"[mediasilo] opening {review_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", _on_response)
        try:
            page.goto(review_url, timeout=60000, wait_until="domcontentloaded")
            page.wait_for_timeout(6000)
            for sel in ("video", "[class*='thumb']", "[class*='asset']", "img"):
                try:
                    page.locator(sel).first.click(timeout=2500)
                    page.wait_for_timeout(4000)
                    break
                except Exception:
                    continue
            page.wait_for_timeout(4000)
        finally:
            browser.close()

    print(f"[mediasilo] intercepted {len(hits)} media URL(s)")
    if not hits:
        raise GoogleDocReadError(
            f"Opened MediaSilo review but no downloadable video appeared. "
            f"The share may have downloads disabled: {review_url}"
        )

    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    last_err = None
    for media_url in hits:
        if ".m3u8" in media_url.lower():
            result = __import__("subprocess").run(
                ["yt-dlp", "-f", "mp4/best", "-o", dest_path, media_url],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0 and os.path.isfile(dest_path):
                return media_url
            last_err = result.stderr[-300:] if result.stderr else "yt-dlp failed"
            continue
        try:
            r = requests.get(media_url, stream=True, timeout=120)
            r.raise_for_status()
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
            if os.path.isfile(dest_path) and os.path.getsize(dest_path) > 10000:
                return media_url
        except Exception as exc:
            last_err = str(exc)
            continue
    raise GoogleDocReadError(f"MediaSilo video download failed: {last_err}")


def find_candidate_source_clips(doc_text: str) -> list[str]:

    """Raw URL candidates from doc text (Drive, MediaSilo, YouTube, etc)."""
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
                "mediasilo.com",
                "frame.io",
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
        # de-dupe by file_id, keep first
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


def classify_footage_url(url: str) -> str:
    """Which adapter should handle this URL."""
    low = (url or "").lower()
    if is_drive_folder_url(url):
        return "drive_folder"
    if extract_drive_file_id(url):
        return "drive_file"
    if "mediasilo.com" in low:
        return "mediasilo"
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if "vimeo.com" in low:
        return "vimeo"
    if "frame.io" in low:
        return "frameio"
    if "dropbox.com" in low or "wetransfer.com" in low or "we.tl/" in low:
        return "file_host"
    path = low.split("?", 1)[0]
    if any(path.endswith(ext) for ext in _VIDEO_EXTENSIONS):
        return "direct"
    return "unknown"


def gather_brief_text(source: str) -> str:
    """Campaign URL / Google Doc / raw brief → text that includes hidden hrefs."""
    if source and "docs.google.com/document" in source:
        return fetch_google_doc_text(source)
    return source or ""


def resolve_and_download_footage(
    *,
    dest_path: str,
    source_clip_url: str = "",
    reference_doc_url: str = "",
    brief_text: str = "",
    download_url_fn=None,
    download_drive_fn=None,
    pick_drive_folder_fn=None,
) -> str:
    """One entry point: read the brief, pick an adapter, download a clip.

    Returns the source URL that was downloaded into dest_path.
    Unknown hosts raise GoogleDocReadError so the daily run can skip
    the campaign instead of crashing.
    """
    blob = "\n".join(x for x in (source_clip_url, reference_doc_url, brief_text) if x)
    if reference_doc_url and "docs.google.com/document" in reference_doc_url:
        try:
            blob = blob + "\n" + fetch_google_doc_text(reference_doc_url)
        except GoogleDocReadError as exc:
            print(f"[resolver] doc fetch failed ({exc}); using page text only")
    elif source_clip_url and "docs.google.com/document" in source_clip_url:
        blob = blob + "\n" + fetch_google_doc_text(source_clip_url)

    candidates = find_candidate_source_clips(blob)
    if source_clip_url and classify_footage_url(source_clip_url) != "unknown":
        if source_clip_url not in candidates:
            candidates.insert(0, source_clip_url)

    print(f"[resolver] {len(candidates)} candidate URL(s)")
    for url in candidates:
        print(f"[resolver]   {classify_footage_url(url)}  {url}")

    if not candidates:
        raise GoogleDocReadError(
            "No footage URL in the brief (Drive / YouTube / MediaSilo / mp4). "
            "Skipping this campaign."
        )

    last_err = "no adapter succeeded"
    for url in candidates:
        kind = classify_footage_url(url)
        print(f"[resolver] trying {kind}: {url}")
        try:
            if kind == "unknown":
                print(f"[resolver] skip unknown host: {url}")
                continue
            if kind == "drive_folder":
                if pick_drive_folder_fn is None:
                    files = find_videos_in_drive_folder(url)
                    if not files:
                        raise GoogleDocReadError(f"Drive folder has no videos: {url}")
                    url = files[0]
                    kind = "drive_file"
                else:
                    pick_drive_folder_fn(url, dest_path)
                    return url
            if kind == "drive_file" and download_drive_fn is not None:
                fid = extract_drive_file_id(url)
                if not fid:
                    raise GoogleDocReadError(f"Bad Drive file URL: {url}")
                download_drive_fn(fid, dest_path)
                return url
            if kind == "mediasilo":
                download_mediasilo_review(url, dest_path)
                return url
            if download_url_fn is not None:
                download_url_fn(url, dest_path)
                return url
            os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
            resp = requests.get(url, stream=True, timeout=120)
            resp.raise_for_status()
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    fh.write(chunk)
            return url
        except Exception as exc:
            last_err = f"{kind} {url}: {exc}"
            print(f"[resolver] {last_err}")
            continue

    raise GoogleDocReadError(
        f"Footage resolver could not download any candidate. Last error: {last_err}"
    )



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
