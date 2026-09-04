"""
google_doc_reader.py

Some Whop campaigns (and possibly other platforms) don't put their
requirements/source-footage directly on the campaign page — they link out
to a Google Doc ("Please refer to Google Doc for requirements") that
contains the actual rules and download links for the footage to clip.

This module fetches a PUBLICLY VIEWABLE Google Doc's plain text content
(no login/API key needed — this only works if the doc's sharing is set to
"Anyone with the link can view", which is standard for this kind of public
campaign brief) and pulls out any URLs mentioned in it, so the pipeline can
try each one as a possible source-clip download.

Limitations:
  - Only works for Google DOCS (docs.google.com/document/...), not Drive
    folders. If a campaign links a Drive FOLDER instead of a single file or
    a Doc, this can't list its contents — that would need the Google Drive
    API with credentials, which is a bigger separate setup. If that comes
    up, flag it and we'll add Drive folder support then.
  - If the doc is not publicly viewable, the fetch will fail — that's a
    campaign-side configuration issue, not something this script can work
    around.
"""

from __future__ import annotations

import re

import requests

_DOC_ID_PATTERN = re.compile(r"docs\.google\.com/document/d/([a-zA-Z0-9_-]+)")
_URL_PATTERN = re.compile(r"https?://[^\s\)\]\"'<>]+")


class GoogleDocReadError(Exception):
    """Raised when a Google Doc can't be fetched or parsed."""


def _extract_doc_id(doc_url: str) -> str:
    match = _DOC_ID_PATTERN.search(doc_url)
    if not match:
        raise GoogleDocReadError(f"Not a recognizable Google Docs URL: {doc_url}")
    return match.group(1)


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
    """Pulls out every URL mentioned in a block of text, in the order they
    appear. Used to find candidate footage-download links inside a doc."""
    return _URL_PATTERN.findall(text)


def find_candidate_source_clips(doc_text: str) -> list[str]:
    """Filters extracted URLs down to ones that look like they point at a
    downloadable video (Drive file links, direct .mp4 links, Dropbox,
    WeTransfer, etc) rather than random reference links. Returns them in
    priority order — caller should try each with yt-dlp/requests until one
    works, since we can't be 100% sure which one is the actual clip without
    downloading it."""
    urls = extract_urls(doc_text)
    candidates = []
    for url in urls:
        lower = url.lower()
        if any(
            marker in lower
            for marker in ("drive.google.com", ".mp4", "dropbox.com", "wetransfer.com", "youtube.com", "youtu.be")
        ):
            candidates.append(url)
    return candidates


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python google_doc_reader.py <google_doc_url>")
        sys.exit(1)

    text = fetch_google_doc_text(sys.argv[1])
    print("--- Doc text ---")
    print(text[:2000])
    print("--- Candidate source clips ---")
    for url in find_candidate_source_clips(text):
        print(url)
