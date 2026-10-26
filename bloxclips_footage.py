"""BloxClips footage hub: campaign link in, clips + examples + rules hints out.

The hub (https://footage.bloxclips.com/<campaign>) is a client-side page. Its data is one public
manifest (https://files.bloxclips.com/manifest.json): folders, files, and finished example videos.
Files are served from files.bloxclips.com (R2), not Google Drive.

A campaign link alone is enough. resolve_campaign() accepts any of:
  * a footage hub URL            https://footage.bloxclips.com/scrub-or-die-campaign#/footage/Clips
  * a Content Rewards / Whop URL (the page title gives the game name)
  * a plain campaign name        "Scrub or Die"
and returns the matching top-level folder, its clips, example videos and music/SFX.
Nothing from another campaign is ever returned (the match is on the top-level folder).
"""
from __future__ import annotations

import html
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

MANIFEST = "https://files.bloxclips.com/manifest.json"
MIRROR = "https://github.com/debashispaul2023-svg/Vyro-automation/releases/download/bloxclips-mirror"
HUB = "https://footage.bloxclips.com"
SKIP_FOLDERS = {"music", "sound effects", "sfx"}
EXAMPLES_FOLDER_ID = "refs"          # "Finished videos"
_NOISE = ("campaign", "horror game", "roblox", "game", "official", "clips")
_cache: dict = {}


# ------------------------------------------------------------------ small helpers

def is_bloxclips_url(url: str) -> bool:
    low = (url or "").lower()
    return "footage.bloxclips.com" in low or "files.bloxclips.com" in low


def _slug(text: str) -> str:
    return re.sub(r"^-|-$", "", re.sub(r"[^a-z0-9]+", "-", (text or "").lower()))


def norm_name(text: str) -> str:
    """'Scrub or Die [Horror Game]' / 'Scrub or Die Campaign' -> 'scrub or die'."""
    t = html.unescape(text or "").lower()
    t = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", t)          # [Horror Game], (Roblox)
    t = re.sub(r"\s[|\-–—]\s.*$", "", t)        # " | Content Rewards" suffixes
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    words = [w for w in t.split() if w not in _NOISE]
    return " ".join(words).strip()


def _file_url(key: str) -> str:
    return FILES + "/" + "/".join(quote(part) for part in (key or "").split("/"))


# ------------------------------------------------------------------ manifest

def load_manifest(path: str | None = None, retries: int = 3) -> dict:
    """Manifest dict. `path` (or BLOXCLIPS_MANIFEST_FILE) loads a local copy, for tests/offline."""
    path = path or os.environ.get("BLOXCLIPS_MANIFEST_FILE")
    if path:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    if _cache.get("m") and time.time() - _cache.get("t", 0) < 600:
        return _cache["m"]
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(
                MANIFEST,
                timeout=30,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://footage.bloxclips.com/",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            _cache.update(m=data, t=time.time())
            return data
        except Exception as exc:  # network, JSON, 5xx
            last = exc
            time.sleep(1.5 * attempt)
    snap = Path(__file__).with_name("data") / "bloxclips_manifest.json"
    if snap.is_file():
        print(f"[bloxclips] live manifest blocked ({last}); using snapshot {snap.name}")
        with open(snap, "r", encoding="utf-8") as fh:
            return json.load(fh)
    raise RuntimeError(f"[bloxclips] manifest unavailable: {last}")


def _roots(manifest: dict) -> list[dict]:
    return [f for f in manifest.get("folders") or []
            if f.get("parent") == "root" and f.get("id") != EXAMPLES_FOLDER_ID]


def find_campaign_root(manifest: dict, *keys: str) -> dict | None:
    """Top-level campaign folder whose normalised name matches any key. Exact first, then containment."""
    roots = _roots(manifest)
    want = [norm_name(k) for k in keys if k]
    want = [w for w in want if w]
    for w in want:
        for r in roots:
            if norm_name(r.get("name")) == w:
                return r
    for w in want:
        for r in roots:
            n = norm_name(r.get("name"))
            if n and (n in w or w in n) and min(len(n), len(w)) >= 5:
                return r
    return None


def _descendants(manifest: dict, root_id: str) -> set[str]:
    kids: dict = {}
    for f in manifest.get("folders") or []:
        kids.setdefault(f.get("parent"), []).append(f["id"])
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(kids.get(cur, []))
    return seen


# ------------------------------------------------------------------ event hints (no Gemini needed)

_HINTS = (
    ("jumpscare", ("jumpscare", "jump scare")),
    ("monster_chase", ("chase", "run", "escape")),
    ("monster_attack", ("attack", "stun", "monster")),
    ("cleaning", ("clean", "slime", "trash", "mirror", "kitchen", "shelf", "couch", "mop")),
    ("loot", ("loot", "banking", "collect")),
    ("truck", ("truck", "escape", "drive")),
    ("menu", ("shop", "lobby", "daily", "reward", "equipment", "chapter", "index", "group")),
)


def event_hint(name: str) -> str:
    """Gameplay event guessed from a hub clip name, e.g. 'Cleaning slime + jumpscare' -> jumpscare."""
    low = (name or "").lower()
    for event, words in _HINTS:
        if any(w in low for w in words):
            return event
    return "unknown"


# ------------------------------------------------------------------ result type

@dataclass
class Hub:
    name: str
    slug: str
    footage_url: str
    clips: list = field(default_factory=list)
    examples: list = field(default_factory=list)
    music: list = field(default_factory=list)
    sfx: list = field(default_factory=list)
    matched_by: str = ""

    def report(self) -> str:
        folders = sorted({c["folder"] for c in self.clips})
        return (f"campaign={self.name!r} footage={self.footage_url} clips={len(self.clips)} "
                f"folders={folders} examples={len(self.examples)} music={len(self.music)} "
                f"sfx={len(self.sfx)} matched_by={self.matched_by}")


def _entry(file: dict, folder_name: str) -> dict:
    key = file.get("url") or ""
    dur = float(file.get("dur") or 0)
    return {
        "clip_id": file["id"],
        "name": file.get("name") or file["id"],
        "url": _file_url(key),
        "kind": "direct",
        "duration_ms": str(int(dur * 1000)),
        "folder": folder_name,
        "width": str(file.get("w") or ""),
        "height": str(file.get("h") or ""),
        "size": str(file.get("size") or ""),
        "event": event_hint(file.get("name") or ""),
    }


_GENERIC_TITLES = ("content rewards", "whop", "just a moment", "sign in", "login", "")


def _useful(title: str) -> bool:
    return norm_name(title) not in _GENERIC_TITLES and len(norm_name(title)) >= 3


def page_title(url: str, timeout: int = 20) -> str:
    """Game name from a campaign page. Plain HTML first (og:title / <title>); these sites are
    client-rendered, so when that gives only the site name, render the page once with Playwright."""
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code < 400:
            m = (re.search(r'property=["\']og:title["\'][^>]*content=["\']([^"\']+)', resp.text)
                 or re.search(r'content=["\']([^"\']+)["\'][^>]*property=["\']og:title', resp.text)
                 or re.search(r"<title[^>]*>([^<]+)</title>", resp.text, re.I))
            title = html.unescape(m.group(1)).strip() if m else ""
            if _useful(title):
                return title
    except Exception:
        pass
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                page.wait_for_timeout(3500)
                for cand in (page.title(), (page.locator("h1").first.inner_text() if page.locator("h1").count() else "")):
                    if _useful(cand or ""):
                        return cand.strip()
            finally:
                browser.close()
    except Exception as exc:
        print(f"[bloxclips] rendered title lookup failed: {exc}")
    return ""


def resolve_campaign(link: str = "", name: str = "", manifest: dict | None = None,
                     title_fn=page_title) -> Hub | None:
    """Campaign link and/or name -> Hub, or None when the hub has no folder for it."""
    manifest = manifest or load_manifest()
    keys: list[tuple[str, str]] = []
    if link and is_bloxclips_url(link):
        seg = urlparse(link).path.strip("/").split("/")[0]
        if seg:
            keys.append(("footage-slug", seg.replace("-", " ")))
    if name:
        keys.append(("name", name))
    if link and not is_bloxclips_url(link):
        title = title_fn(link)
        if title:
            keys.append(("page-title", title))
    root = None
    matched = ""
    for how, key in keys:
        root = find_campaign_root(manifest, key)
        if root:
            matched = f"{how}:{key}"
            break
    if root is None:
        print(f"[bloxclips] no hub folder for link={link!r} name={name!r} tried={[k for _, k in keys]}")
        return None
    folders = {f["id"]: f for f in manifest.get("folders") or []}
    inside = _descendants(manifest, root["id"])
    hub = Hub(name=root["name"], slug=_slug(root["name"]), footage_url=f"{HUB}/{_slug(root['name'])}", matched_by=matched)
    base = norm_name(root["name"])
    for f in manifest.get("files") or []:
        folder = folders.get(f.get("folder")) or {}
        fname = folder.get("name") or ""
        if f.get("folder") == EXAMPLES_FOLDER_ID:
            if f.get("kind") == "video" and norm_name(os.path.splitext(f.get("name") or "")[0]).startswith(base):
                hub.examples.append(_entry(f, "Finished videos"))
            continue
        if f.get("folder") not in inside or not f.get("url"):
            continue
        if f.get("kind") == "audio":
            (hub.sfx if "sound" in fname.lower() or "sfx" in fname.lower() else hub.music).append(_entry(f, fname))
            continue
        if f.get("kind") != "video" or fname.lower() in SKIP_FOLDERS:
            continue
        hub.clips.append(_entry(f, fname))
    hub.clips.sort(key=lambda c: (c["folder"], _num(c["name"]), c["name"]))
    print(f"[bloxclips] {hub.report()}")
    return hub


def _num(name: str) -> int:
    m = re.search(r"(\d+)", name or "")
    return int(m.group(1)) if m else 10_000


def list_clips(url: str, name: str = "") -> list[dict]:
    """Compatible entry point used by google_doc_reader. Empty when the hub has no such campaign."""
    hub = resolve_campaign(link=url, name=name)
    return list(hub.clips) if hub else []


def download(url: str, dest: str, expected_size: int = 0, retries: int = 3) -> str:
    """Stream a hub file to dest. Verifies the size when the manifest gave one."""
    import subprocess
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://footage.bloxclips.com/",
    }
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=90, headers=headers) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        fh.write(chunk)
            got = os.path.getsize(dest)
            if expected_size and abs(got - expected_size) > 0:
                raise IOError(f"size {got} != manifest {expected_size}")
            return dest
        except Exception as exc:
            last = exc
            try:
                subprocess.run(
                    ["curl", "-fsSL", "-A", "Mozilla/5.0", "-e", "https://footage.bloxclips.com/", "-o", dest, url],
                    check=True, timeout=120,
                )
                if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
                    return dest
            except Exception as curl_exc:
                last = curl_exc
            clip_id = url.split("/o/")[-1].split("/")[0] if "/o/" in url else ""
            if clip_id:
                mirror = f"{MIRROR}/{clip_id}.mp4"
                try:
                    subprocess.run(["curl", "-fsSL", "-L", "-o", dest, mirror], check=True, timeout=180)
                    if os.path.isfile(dest) and os.path.getsize(dest) > 1000:
                        print(f"[bloxclips] downloaded mirror {clip_id}")
                        return dest
                except Exception as mirror_exc:
                    last = mirror_exc
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"[bloxclips] download failed {url}: {last}")


if __name__ == "__main__":
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    h = resolve_campaign(link=target if target.startswith("http") else "", name="" if target.startswith("http") else target)
    if not h:
        sys.exit(2)
    print(h.report())
    for c in h.clips[:10]:
        print(" ", c["folder"], "|", c["name"], c["duration_ms"], c["width"] + "x" + c["height"], c["event"])
