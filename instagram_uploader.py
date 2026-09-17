"""
instagram_uploader.py

Uploads a rendered video to Instagram as a Reel via the Instagram Graph API
(the "Instagram API with Instagram Login" flow — see vyro_client.py's
docstring for background, and README.md for how to (re)generate
IG_ACCESS_TOKEN roughly every 60 days when it expires).

Instagram's API does NOT accept a direct file upload — it fetches the video
FROM a public URL you give it. So this module first pushes the rendered
video to a small PUBLIC GitHub repo as a Release asset (kept separate from
this private automation repo) to get that public URL, then:

  1. POST /{ig-business-id}/media          -> creates a "container" (id)
  2. Poll GET /{container-id}?fields=status_code until FINISHED
  3. POST /{ig-business-id}/media_publish  -> actually publishes the Reel

Required environment variables:
  IG_ACCESS_TOKEN        - long-lived Instagram access token
  IG_BUSINESS_ACCOUNT_ID - the Instagram Business Account numeric id
  ASSET_HOST_REPO        - "owner/repo" of a PUBLIC repo used only to host
                            these temporary video files (create an empty
                            public repo just for this)
  ASSET_HOST_TOKEN       - a GitHub Personal Access Token with 'repo' scope
                            for that public repo (this automation repo's
                            own GITHUB_TOKEN can't create releases in a
                            DIFFERENT repo, so a separate PAT is needed)
"""

from __future__ import annotations

import os
import time

import requests

GRAPH_API_BASE = "https://graph.instagram.com"
API_VERSION = "v21.0"


class InstagramUploadError(Exception):
    """Raised on any Instagram Graph API / asset-hosting failure."""


def _get_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise InstagramUploadError(f"Missing required environment variable: {name}")
    return value


def host_video_publicly(video_path: str, release_tag: str) -> str:
    """
    Uploads video_path as a GitHub Release asset in a small PUBLIC repo and
    returns the public download URL Instagram's servers can fetch it from.
    """
    repo = _get_env("ASSET_HOST_REPO")
    token = _get_env("ASSET_HOST_TOKEN")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    create_resp = requests.post(
        f"https://api.github.com/repos/{repo}/releases",
        headers=headers,
        json={"tag_name": release_tag, "name": release_tag, "draft": False, "prerelease": False},
        timeout=30,
    )
    if create_resp.status_code >= 300:
        raise InstagramUploadError(f"Failed to create GitHub release: {create_resp.text}")
    upload_url_template = create_resp.json()["upload_url"]  # e.g. ".../assets{?name,label}"
    upload_url = upload_url_template.split("{")[0]

    filename = os.path.basename(video_path)
    with open(video_path, "rb") as f:
        asset_resp = requests.post(
            f"{upload_url}?name={filename}",
            headers={**headers, "Content-Type": "video/mp4"},
            data=f,
            timeout=180,
        )
    if asset_resp.status_code >= 300:
        raise InstagramUploadError(f"Failed to upload release asset: {asset_resp.text}")

    return asset_resp.json()["browser_download_url"]


def _create_container(ig_business_id: str, access_token: str, video_url: str, caption: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{API_VERSION}/{ig_business_id}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": access_token,
        },
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise InstagramUploadError(f"Failed to create media container: {data}")
    return data["id"]


def _wait_for_container(container_id: str, access_token: str, timeout_seconds: int = 300) -> None:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = requests.get(
            f"{GRAPH_API_BASE}/{API_VERSION}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
            timeout=30,
        )
        data = resp.json()
        status = data.get("status_code")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise InstagramUploadError(f"Instagram failed to process the video: {data}")
        time.sleep(10)
    raise InstagramUploadError("Timed out waiting for Instagram to process the video container.")


def _publish_container(ig_business_id: str, access_token: str, container_id: str) -> str:
    resp = requests.post(
        f"{GRAPH_API_BASE}/{API_VERSION}/{ig_business_id}/media_publish",
        data={"creation_id": container_id, "access_token": access_token},
        timeout=30,
    )
    data = resp.json()
    if "id" not in data:
        raise InstagramUploadError(f"Failed to publish Reel: {data}")
    return data["id"]


def upload_reel(video_path: str, caption: str, release_tag: str) -> str:
    """Full flow: host video publicly -> create container -> wait -> publish.
    Returns the published Instagram media id."""
    access_token = _get_env("IG_ACCESS_TOKEN")
    ig_business_id = _get_env("IG_BUSINESS_ACCOUNT_ID")

    video_url = host_video_publicly(video_path, release_tag)
    container_id = _create_container(ig_business_id, access_token, video_url, caption)
    _wait_for_container(container_id, access_token)
    return _publish_container(ig_business_id, access_token, container_id)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python instagram_uploader.py <video_path> <caption>")
        sys.exit(1)
    tag = f"clip-{int(time.time())}"
    media_id = upload_reel(sys.argv[1], sys.argv[2], tag)
    print(f"Published Instagram Reel: {media_id}")


def fetch_reel_permalink(media_id: str) -> str:
    """Instagram Login tokens work on graph.instagram.com, not graph.facebook.com."""
    token = os.environ.get("IG_ACCESS_TOKEN") or ""
    if not media_id or not token:
        return ""
    for base in (
        f"{GRAPH_API_BASE}/{API_VERSION}",
        "https://graph.instagram.com/v21.0",
        "https://graph.facebook.com/v21.0",
    ):
        try:
            resp = requests.get(
                f"{base}/{media_id}",
                params={"fields": "permalink,shortcode", "access_token": token},
                timeout=30,
            )
            data = resp.json() if resp.content else {}
            link = (data.get("permalink") or "").strip()
            if link.startswith("http"):
                return link
            code = (data.get("shortcode") or "").strip()
            if code:
                return f"https://www.instagram.com/reel/{code}/"
            print(f"[ig] permalink miss {base}: {data}")
        except Exception as exc:
            print(f"[ig] permalink {base} failed: {exc}")
    return ""
