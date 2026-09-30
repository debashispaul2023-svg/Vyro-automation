"""
AI Channel Manager for Vyro-automation (dry-run by default).

The Daily upload token only has youtube.upload, so channels.list(mine=True)
returns 403. This manager therefore:

  1. Tries OAuth mine=True (works after a wider-scope re-auth)
  2. Falls back to public videos.list via API key + published_log.json
  3. Optional: YOUTUBE_CHANNEL_HANDLE or YOUTUBE_CHANNEL_ID + API key

Nothing is edited on YouTube unless CHANNEL_MANAGER_APPLY_CHANGES=true.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from youtube_uploader import PUBLISHED_LOG, UploadError, _load_credentials

ACTIONS_LOG = "channel_manager_actions.json"
MAX_ACTIONS = 3
APPLY_CHANGES = os.getenv("CHANNEL_MANAGER_APPLY_CHANGES", "false").lower() == "true"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(entry: dict) -> None:
    rows = []
    if os.path.isfile(ACTIONS_LOG):
        try:
            rows = json.loads(open(ACTIONS_LOG, encoding="utf-8").read())
        except Exception:
            rows = []
    if not isinstance(rows, list):
        rows = []
    entry["timestamp"] = _now()
    entry["applied"] = APPLY_CHANGES
    rows.append(entry)
    with open(ACTIONS_LOG, "w", encoding="utf-8") as f:
        json.dump(rows[-300:], f, indent=2)


def _api_key() -> str:
    return (os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_DRIVE_API_KEY") or "").strip()


def _oauth_client():
    creds = _load_credentials("token.json")
    return build("youtube", "v3", credentials=creds), creds


def _key_client():
    key = _api_key()
    if not key:
        return None
    return build("youtube", "v3", developerKey=key)


def _hydrate(items: list) -> list[dict]:
    out = []
    for it in items or []:
        sn, st, stats = it.get("snippet") or {}, it.get("status") or {}, it.get("statistics") or {}
        out.append(
            {
                "video_id": it.get("id") or "",
                "title": sn.get("title") or "",
                "description": (sn.get("description") or "")[:280],
                "privacy": st.get("privacyStatus") or "public",
                "published_at": sn.get("publishedAt"),
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
                "duration": (it.get("contentDetails") or {}).get("duration"),
            }
        )
    return [r for r in out if r.get("video_id")]


def _videos_by_ids(client, ids: list[str]) -> list[dict]:
    clean = []
    seen = set()
    for raw in ids:
        vid = (raw or "").strip()
        if vid and vid not in seen:
            seen.add(vid)
            clean.append(vid)
    out = []
    for i in range(0, len(clean), 50):
        chunk = clean[i : i + 50]
        det = client.videos().list(
            part="snippet,status,statistics,contentDetails",
            id=",".join(chunk),
        ).execute()
        out.extend(_hydrate(det.get("items") or []))
    return out


def _ids_from_published_log() -> list[str]:
    if not os.path.isfile(PUBLISHED_LOG):
        return []
    try:
        rows = json.loads(open(PUBLISHED_LOG, encoding="utf-8").read())
    except Exception:
        return []
    if not isinstance(rows, list):
        return []
    ids = []
    for row in rows:
        if isinstance(row, dict) and row.get("video_id"):
            ids.append(str(row["video_id"]))
    return ids


def _list_via_oauth(youtube) -> list[dict]:
    ch = youtube.channels().list(part="contentDetails,statistics", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        return []
    print(
        f"[cm] oauth channel videos={items[0].get('statistics', {}).get('videoCount')} "
        f"views={items[0].get('statistics', {}).get('viewCount')}"
    )
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads,
        maxResults=20,
    ).execute()
    ids = [it["contentDetails"]["videoId"] for it in pl.get("items") or []]
    return _videos_by_ids(youtube, ids)


def _list_via_handle(client) -> list[dict]:
    handle = (os.getenv("YOUTUBE_CHANNEL_HANDLE") or "").strip().lstrip("@")
    cid = (os.getenv("YOUTUBE_CHANNEL_ID") or "").strip()
    items = []
    if cid.startswith("UC"):
        resp = client.channels().list(part="contentDetails,statistics", id=cid).execute()
        items = resp.get("items") or []
    elif handle:
        resp = client.channels().list(part="contentDetails,statistics", forHandle=handle).execute()
        items = resp.get("items") or []
    if not items:
        return []
    print(
        f"[cm] public channel videos={items[0].get('statistics', {}).get('videoCount')} "
        f"views={items[0].get('statistics', {}).get('viewCount')}"
    )
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = client.playlistItems().list(
        part="contentDetails",
        playlistId=uploads,
        maxResults=20,
    ).execute()
    ids = [it["contentDetails"]["videoId"] for it in pl.get("items") or []]
    return _videos_by_ids(client, ids)


def _collect_snapshot() -> tuple[list[dict], object | None]:
    youtube = None
    creds = None
    if os.path.isfile("token.json"):
        try:
            youtube, creds = _oauth_client()
            snap = _list_via_oauth(youtube)
            if snap:
                print(f"[cm] listed {len(snap)} video(s) via OAuth")
                return snap, youtube
        except HttpError as exc:
            if getattr(exc, "resp", None) is not None and exc.resp.status == 403:
                print("[cm] OAuth token is youtube.upload only — skipping mine=True list")
            else:
                print(f"[cm] OAuth list skipped: {exc}")
        except UploadError as exc:
            print(f"[cm] OAuth skipped: {exc}")
        except Exception as exc:
            print(f"[cm] OAuth skipped: {exc}")

    key_yt = _key_client()
    if key_yt is None:
        print("[cm] no YOUTUBE_API_KEY / GOOGLE_DRIVE_API_KEY for public list")
    else:
        try:
            snap = _list_via_handle(key_yt)
            if snap:
                print(f"[cm] listed {len(snap)} video(s) via channel handle/id")
                return snap, youtube or key_yt
        except Exception as exc:
            print(f"[cm] handle list skipped: {exc}")
        ids = _ids_from_published_log()
        if ids:
            try:
                snap = _videos_by_ids(key_yt, ids)
                if snap:
                    print(f"[cm] listed {len(snap)} video(s) via published_log.json + API key")
                    return snap, youtube or key_yt
            except Exception as exc:
                print(f"[cm] published_log list failed: {exc}")

    ids = _ids_from_published_log()
    if youtube and ids:
        try:
            snap = _videos_by_ids(youtube, ids)
            if snap:
                print(f"[cm] listed {len(snap)} video(s) via published_log.json + OAuth")
                return snap, youtube
        except Exception as exc:
            print(f"[cm] oauth-by-id skipped: {exc}")

    print("[cm] no video snapshot available")
    return [], youtube


def _gemini_actions(snapshot: list[dict]) -> list[dict]:
    if not snapshot:
        return []
    try:
        from ai_brain import _call_json
    except Exception as exc:
        print(f"[cm] ai_brain import failed: {exc}")
        return []
    views = [int(v.get("views") or 0) for v in snapshot]
    median = sorted(views)[len(views) // 2] if views else 0
    prompt = (
        "You manage a family-friendly Roblox gameplay Shorts channel. "
        "YouTube is the main platform. Propose at most 3 actions, or [].\n"
        "Allowed types:\n"
        '1. {"type":"rewrite_metadata","video_id":"...","new_title":"...","new_description":"...","reasoning":"..."}\n'
        '2. {"type":"flag_duplicate_title","video_id":"...","reasoning":"..."}\n'
        "Rules:\n"
        "- Only rewrite if views are clearly below the channel median AND the title is weak or duplicated.\n"
        "- Titles must put the official game name first, stay under 100 chars, end with #shorts.\n"
        "- No clickbait lies. No adult wording. Keep Roblox game names exact.\n"
        "- Duplicate titles across videos should be flagged.\n"
        f"- Channel median views in this snapshot: {median}\n"
        f"SNAPSHOT:\n{json.dumps(snapshot, indent=2)[:8000]}\n"
        'Return JSON only: {"actions":[...]}'
    )
    try:
        data = _call_json(prompt, max_output_tokens=800)
    except Exception as exc:
        print(f"[cm] Gemini skipped: {exc}")
        return []
    actions = data.get("actions") if isinstance(data, dict) else data
    return actions if isinstance(actions, list) else []


def _apply_rewrite(youtube, action: dict) -> bool:
    vid = action.get("video_id")
    title = (action.get("new_title") or "")[:100]
    desc = action.get("new_description") or ""
    if not vid or not title:
        return False
    _append_log(
        {
            "type": "rewrite_metadata",
            "video_id": vid,
            "new_title": title,
            "reasoning": action.get("reasoning") or "",
        }
    )
    if not APPLY_CHANGES:
        print(f"[cm] dry-run rewrite {vid} -> {title!r}")
        return True
    if youtube is None:
        print("[cm] no OAuth client — cannot apply rewrite")
        return False
    try:
        cur = youtube.videos().list(part="snippet", id=vid).execute()
        items = cur.get("items") or []
        if not items:
            print(f"[cm] video {vid} not found")
            return False
        snippet = items[0]["snippet"]
        snippet["title"] = title
        if desc:
            snippet["description"] = desc
        snippet["categoryId"] = snippet.get("categoryId") or "20"
        youtube.videos().update(part="snippet", body={"id": vid, "snippet": snippet}).execute()
        print(f"[cm] applied rewrite {vid}")
        return True
    except Exception as exc:
        print(f"[cm] apply failed (need youtube scope, not only youtube.upload): {exc}")
        return False


def run_channel_manager() -> int:
    mode = "LIVE" if APPLY_CHANGES else "DRY RUN"
    print("=" * 60)
    print(f"AI CHANNEL MANAGER — {mode}")
    print("=" * 60)

    snapshot, youtube = _collect_snapshot()
    if not snapshot:
        print("[cm] nothing to review this run")
        _append_log({"type": "noop", "reason": "no snapshot"})
        print("=" * 60)
        return 0

    for row in snapshot[:8]:
        print(f"[cm] {row['privacy']:8} {row['views']:5}v  {row['title'][:70]}")

    titles = [r["title"] for r in snapshot]
    dupes = {t for t in titles if titles.count(t) > 1}
    if dupes:
        print(f"[cm] duplicate titles: {len(dupes)}")

    proposed = _gemini_actions(snapshot)
    if not proposed:
        print("[cm] no actions proposed")
        _append_log({"type": "noop", "count": len(snapshot), "duplicates": list(dupes)[:5]})
        print("=" * 60)
        return 0

    applied = 0
    for action in proposed:
        if applied >= MAX_ACTIONS:
            break
        kind = action.get("type")
        print(f"[cm] propose {kind} {action.get('video_id')} — {str(action.get('reasoning') or '')[:140]}")
        if kind == "rewrite_metadata":
            if _apply_rewrite(youtube, action):
                applied += 1
        else:
            _append_log(action)
            applied += 1
    print(f"[cm] logged {applied} action(s)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_channel_manager())
