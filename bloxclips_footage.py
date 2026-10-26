"""List campaign clips from the public BloxClips footage hub.

The hub is not Google Drive. It serves a manifest and the files live on
files.bloxclips.com. Music and sound-effect folders are skipped.
"""
from __future__ import annotations

import re
from urllib.parse import quote, urlparse

import requests

MANIFEST = "https://files.bloxclips.com/manifest.json"
FILES = "https://files.bloxclips.com"
SKIP_FOLDERS = {"music", "sound effects"}


def is_bloxclips_url(url: str) -> bool:
    low = (url or "").lower()
    return "footage.bloxclips.com" in low or "files.bloxclips.com" in low


def _slug(text: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", (text or "").lower()))


def _file_url(key: str) -> str:
    return FILES + "/" + "/".join(quote(part) for part in (key or "").split("/"))


def list_clips(url: str) -> list[dict[str, str]]:
    """Video clips for one campaign hub page. Empty if the slug is unknown."""
    slug = _slug(urlparse(url).path.strip("/").split("/")[0])
    if not slug:
        return []
    resp = requests.get(MANIFEST, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    folders = {f["id"]: f for f in data.get("folders") or []}
    root = next(
        (f for f in folders.values() if f.get("parent") == "root" and _slug(f.get("name")) == slug),
        None,
    )
    if root is None:
        print(f"[bloxclips] no campaign folder for slug {slug}")
        return []
    kids: dict[str, list[str]] = {}
    for folder in folders.values():
        kids.setdefault(folder.get("parent"), []).append(folder["id"])
    inside: set[str] = set()
    stack = [root["id"]]
    while stack:
        item = stack.pop()
        if item in inside:
            continue
        inside.add(item)
        stack.extend(kids.get(item, []))
    out: list[dict[str, str]] = []
    for file in data.get("files") or []:
        if file.get("kind") != "video" or file.get("folder") not in inside:
            continue
        folder_name = (folders.get(file["folder"]) or {}).get("name") or ""
        if folder_name.lower() in SKIP_FOLDERS:
            continue
        key = file.get("url") or ""
        if not key:
            continue
        out.append({
            "clip_id": file["id"],
            "name": file.get("name") or file["id"],
            "url": _file_url(key),
            "kind": "direct",
            "duration_ms": str(int(float(file.get("dur") or 0) * 1000)),
            "folder": folder_name,
        })
    print(f"[bloxclips] {slug}: {len(out)} clip(s)")
    return out
