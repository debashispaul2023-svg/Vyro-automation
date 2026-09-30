"""
AI Channel Manager for Vyro-automation (dry-run by default).

Separate from Daily video generation. Once a day it:

  1. Lists recent uploads on the connected YouTube channel
  2. Optionally reads Analytics if the token has yt-analytics.readonly
  3. Asks Gemini whether any title/description should be rewritten
  4. Logs proposals to channel_manager_actions.json

Nothing is edited on YouTube unless CHANNEL_MANAGER_APPLY_CHANGES=true.

Uses the same token.json Daily already restores from YOUTUBE_TOKEN_JSON.
Current upload token may only have youtube.upload — list + stats usually
work; live title edits need a wider YouTube scope. Dry-run still works.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone

from youtube_uploader import UploadError, _load_credentials

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


def _youtube():
    from googleapiclient.discovery import build

    creds = _load_credentials("token.json")
    return build("youtube", "v3", credentials=creds), creds


def _analytics(creds):
    try:
        from googleapiclient.discovery import build

        return build("youtubeAnalytics", "v2", credentials=creds)
    except Exception:
        return None


def _list_recent_videos(youtube, limit: int = 20) -> list[dict]:
    ch = youtube.channels().list(part="contentDetails,statistics", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        print("[cm] no channel found for this token")
        return []
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    print(
        f"[cm] channel videos={items[0].get('statistics', {}).get('videoCount')} "
        f"views={items[0].get('statistics', {}).get('viewCount')}"
    )
    pl = youtube.playlistItems().list(
        part="contentDetails,snippet",
        playlistId=uploads,
        maxResults=min(limit, 50),
    ).execute()
    ids = [it["contentDetails"]["videoId"] for it in pl.get("items") or []]
    if not ids:
        return []
    det = youtube.videos().list(
        part="snippet,status,statistics,contentDetails",
        id=",".join(ids),
    ).execute()
    out = []
    for it in det.get("items") or []:
        sn, st, stats = it.get("snippet") or {}, it.get("status") or {}, it.get("statistics") or {}
        out.append(
            {
                "video_id": it["id"],
                "title": sn.get("title") or "",
                "description": (sn.get("description") or "")[:280],
                "privacy": st.get("privacyStatus"),
                "published_at": sn.get("publishedAt"),
                "views": int(stats.get("viewCount") or 0),
                "likes": int(stats.get("likeCount") or 0),
                "comments": int(stats.get("commentCount") or 0),
                "duration": (it.get("contentDetails") or {}).get("duration"),
            }
        )
    return out


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
    if not os.path.isfile("token.json"):
        print("[cm] token.json missing — skip (Daily still works without this job)")
        return 0
    try:
        youtube, creds = _youtube()
    except UploadError as exc:
        print(f"[cm] auth failed: {exc}")
        return 0
    except Exception as exc:
        print(f"[cm] auth failed: {exc}")
        traceback.print_exc()
        return 0

    try:
        snapshot = _list_recent_videos(youtube)
    except Exception as exc:
        print(f"[cm] list videos failed: {exc}")
        traceback.print_exc()
        return 0

    if not snapshot:
        print("[cm] no videos to review")
        return 0

    for row in snapshot[:8]:
        print(
            f"[cm] {row['privacy']:8} {row['views']:5}v  {row['title'][:70]}"
        )

    titles = [r["title"] for r in snapshot]
    dupes = {t for t in titles if titles.count(t) > 1}
    if dupes:
        print(f"[cm] duplicate titles: {len(dupes)}")

    analytics = _analytics(creds)
    if analytics:
        print("[cm] analytics client ready (readonly if scope exists)")
    else:
        print("[cm] analytics client not built")

    proposed = _gemini_actions(snapshot)
    if not proposed:
        print("[cm] no actions proposed")
        _append_log({"type": "noop", "count": len(snapshot), "duplicates": list(dupes)[:5]})
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
