"""
daily_runner.py

Fully-automated entrypoint, meant to run on a daily schedule (GitHub Actions
cron — see .github/workflows/vyro_daily.yml):

  1. Check Vyro for an active campaign. If none (or it fails the AI quality
     screen), check every Whop campaign listed in whop_campaigns.json.
  2. Ask ai_brain to sanity-check the campaign's requirements — skip and try
     the other platform if it looks like a scam/low-effort listing.
  3. Skip cleanly if nothing usable found anywhere, or if the found
     campaign_id was already processed before (processed_campaigns.json).
  4. Resolve the source clip: some campaigns give a direct clip link
     (Vyro); some (many Whop ones) point to an external Google Doc that
     contains the real footage links — resolve that first if needed.
  5. Parse requirements with AI (falls back to regex parsing on failure) ->
     render vertical short -> generate AI-written metadata (falls back to
     the template on failure) -> validate -> upload to YouTube -> upload to
     Instagram Reels.
  6. Submit the resulting link back to whichever platform the campaign
     came from.
  7. Record the campaign_id as processed, so it's never submitted twice.

Usage (locally or in CI):
    python daily_runner.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Union

import requests

from ai_brain import AIBrainError, ai_generate_metadata, ai_parse_requirements, ai_score_campaign
from checker import ValidationError, validate_or_raise
from clip_picker import ClipPickError, collect_clips_from_doc_text, download_drive_file, pick_best_clip
from google_doc_reader import (
    GoogleDocReadError,
    extract_drive_file_id,
    list_content_folder_clips,
    resolve_and_download_footage,
)
from instagram_uploader import InstagramUploadError, upload_reel
from metadata import MetadataError, VideoMetadata, generate_metadata
from renderer import RenderError, render_short
from requirements_parser import CampaignRequirements, RequirementsParseError, parse_campaign
from vyro_client import VyroCampaign, VyroClientError
from vyro_client import run_check as vyro_run_check
from vyro_client import run_submit as vyro_run_submit
from whop_client import WhopCampaign, WhopClientError
from whop_client import check_configured_campaigns as whop_check_configured_campaigns
from whop_client import discover_and_join_new_campaigns as whop_discover_and_join_new_campaigns
from whop_client import submit_video_link as whop_submit_video_link
from youtube_uploader import UploadError, upload_video

PROCESSED_LOG_PATH = "processed_campaigns.json"
CLIP_LOG_PATH = "processed_clips.json"
SOURCE_CLIP_PATH = "input_16x9.mp4"
OUTPUT_PATH = "output/short.mp4"

Campaign = Union[VyroCampaign, WhopCampaign]


def _load_processed() -> set[str]:
    if not os.path.isfile(PROCESSED_LOG_PATH):
        return set()
    with open(PROCESSED_LOG_PATH, "r", encoding="utf-8") as f:
        return set(json.load(f))


def _save_processed(processed: set[str]) -> None:
    with open(PROCESSED_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(processed), f, indent=2)


def _load_clip_log() -> dict:
    empty = {"closed_campaigns": [], "clips": []}
    if not os.path.isfile(CLIP_LOG_PATH):
        return empty
    with open(CLIP_LOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return empty
    data.setdefault("closed_campaigns", [])
    data.setdefault("clips", [])
    return data


def _save_clip_log(log: dict) -> None:
    with open(CLIP_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def _clip_already_used(log: dict, campaign_id: str, clip_id: str) -> bool:
    """Same file is allowed again only under a different (new) campaign_id."""
    for row in log.get("clips") or []:
        if row.get("campaign_id") == campaign_id and row.get("clip_id") == clip_id:
            return True
    return False


def _download_source_clip(url: str, dest_path: str) -> None:
    """
    Downloads the campaign's source clip. Tries yt-dlp first (handles
    YouTube, Google Drive, and most video hosts); falls back to a plain
    HTTP GET for direct file links.
    """
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)

    ytdlp_result = subprocess.run(
        ["yt-dlp", "-f", "mp4/best", "-o", dest_path, url],
        capture_output=True,
        text=True,
    )
    if ytdlp_result.returncode == 0 and os.path.isfile(dest_path):
        return

    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            f.write(chunk)


def _youtube_token_ready(token_path: str = "token.json") -> bool:
    """True only if token.json is a real user token with refresh_token."""
    if not os.path.isfile(token_path):
        return False
    try:
        with open(token_path, encoding="utf-8") as f:
            data = json.loads(f.read())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("installed") or data.get("web"):
        return False
    return bool(data.get("refresh_token") and (data.get("token") or data.get("access_token")))


def _pick_drive_folder(folder_url: str, dest_path: str) -> None:
    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
    clips = collect_clips_from_doc_text(folder_url)
    if clips and drive_key:
        winner = pick_best_clip(
            clips,
            work_dir="work/daily",
            api_key=drive_key,
            campaign_notes="",
        )
        shutil.copyfile(winner.local_path, dest_path)
        return
    raise ClipPickError(f"No downloadable videos in Drive folder {folder_url}")


def _list_campaign_clips(campaign: Campaign) -> list[dict[str, str]]:
    return list_content_folder_clips(
        source_clip_url=getattr(campaign, "source_clip_url", "") or "",
        reference_doc_url=getattr(campaign, "reference_doc_url", None) or "",
        brief_text=campaign.requirements_text or campaign.name or "",
    )


def _next_unused_clip(campaign: Campaign, log: dict) -> dict[str, str] | None:
    if campaign.campaign_id in set(log.get("closed_campaigns") or []):
        print(f"Campaign {campaign.campaign_id} is closed — never reuse its folder.")
        return None
    clips = _list_campaign_clips(campaign)
    if not clips:
        return None
    for clip in clips:
        if not _clip_already_used(log, campaign.campaign_id, clip["clip_id"]):
            print(f"[clips] next unused: {clip.get('name') or clip['clip_id']} ({clip['kind']})")
            return clip
    print(f"[clips] all {len(clips)} content-folder clips already used for this campaign.")
    return None


def _ig_caption(campaign: Campaign, req: CampaignRequirements, meta: VideoMetadata) -> str:
    """Rules first. Missing required lines get appended so IG does not ship a naked caption."""
    raw = f"{campaign.name or ''}\n{campaign.requirements_text or ''}\n{meta.title}\n{meta.description}"
    desc = (meta.description or "").strip()
    if "skip to content" in desc.lower() or "link your discord" in desc.lower():
        desc = ""
    lines = [meta.title.strip(), desc]
    low = "\n".join(lines).lower()
    raw_l = raw.lower()
    if "zodiac" in raw_l and "zodiac" not in low:
        lines.append("Zodiac Beta Weekend 2 — play free this weekend.")
    if "call of duty" in raw_l or "callofduty" in raw_l:
        if "@callofduty" not in low:
            lines.append("@Callofduty")
    if "#ad" not in low:
        lines.append("#Ad")
    for tag in req.mandatory_hashtags or []:
        token = tag if str(tag).startswith("#") else f"#{tag}"
        if token.lower() not in low:
            lines.append(token)
    for link in req.required_links or []:
        if link and link not in "\n".join(lines):
            lines.append(link)
    caption = "\n".join(x for x in lines if x).strip()
    print(f"[caption] {caption[:240]!r}")
    return caption


def _resolve_source_clip(campaign: Campaign) -> str:
    """Agent resolver: brief → host → adapter → SOURCE_CLIP_PATH."""
    os.makedirs(os.path.dirname(SOURCE_CLIP_PATH) or ".", exist_ok=True)
    drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()

    def _drive_file(fid: str, dest: str) -> None:
        if not drive_key:
            raise GoogleDocReadError("GOOGLE_DRIVE_API_KEY missing")
        download_drive_file(fid, dest, drive_key)

    try:
        return resolve_and_download_footage(
            dest_path=SOURCE_CLIP_PATH,
            source_clip_url=campaign.source_clip_url or "",
            reference_doc_url=getattr(campaign, "reference_doc_url", None) or "",
            brief_text=campaign.requirements_text or campaign.name or "",
            download_url_fn=_download_source_clip,
            download_drive_fn=_drive_file,
            pick_drive_folder_fn=_pick_drive_folder,
        )
    except GoogleDocReadError as exc:
        raise RuntimeError(str(exc)) from exc


_SKIP_CAMPAIGN_MARKERS = (
    "u2 -",
    "street of dreams",
    "geezerbomb",
    "rockbottom",
    "fifa + world cup",
    "world cup edits",
)


def _is_blocked_example_campaign(campaign: Campaign) -> str | None:
    """U2 was only a pipeline test. Never treat it as a production target."""
    blob = f"{campaign.name or ''} {campaign.requirements_text or ''}".lower()
    for marker in _SKIP_CAMPAIGN_MARKERS:
        if marker in blob:
            return marker
    return None


def _screen_campaign(platform: str, campaign: Campaign) -> bool:
    """Keep campaigns that can work as Instagram Reels. Drop test/weak ones."""
    blocked = _is_blocked_example_campaign(campaign)
    if blocked:
        print(
            f"Skipping {platform} campaign '{campaign.name}': "
            f"example/test marker '{blocked}'. Looking for a viral IG brief instead."
        )
        return False

    try:
        score = ai_score_campaign(
            f"NAME: {campaign.name}\n\n{campaign.requirements_text or campaign.name}"
        )
    except AIBrainError as exc:
        print(f"AI campaign screening unavailable ({exc}); proceeding anyway.", file=sys.stderr)
        return True

    print(f"AI screen ({platform}): good={score.is_good} — {score.reason}")
    if not score.is_good:
        print(f"Skipping {platform} campaign '{campaign.campaign_id}': {score.reason}")
        return False
    return True


def _find_campaign() -> tuple[str, Campaign] | tuple[None, None]:
    """Checks Vyro first, then every configured Whop campaign. Skips any
    campaign that fails the AI quality screen and tries the next source."""
    try:
        vyro_campaign = vyro_run_check()
    except VyroClientError as exc:
        print(f"Vyro check failed: {exc}", file=sys.stderr)
        vyro_campaign = None

    if vyro_campaign is not None and _screen_campaign("vyro", vyro_campaign):
        return "vyro", vyro_campaign

    try:
        newly_joined = whop_discover_and_join_new_campaigns(score_fn=ai_score_campaign, max_new=2)
        if newly_joined:
            print(f"Auto-joined {len(newly_joined)} new Whop campaign(s): {newly_joined}")
    except Exception as exc:
        print(f"Whop auto-discovery/join failed: {exc}", file=sys.stderr)

    try:
        whop_campaign = whop_check_configured_campaigns()
    except WhopClientError as exc:
        print(f"Whop check failed: {exc}", file=sys.stderr)
        whop_campaign = None

    if whop_campaign is not None and _screen_campaign("whop", whop_campaign):
        return "whop", whop_campaign

    return None, None


def _instagram_permalink(media_id: str) -> str:
    """Turn Graph media id into a public Reel URL for Whop submit."""
    token = (os.environ.get("IG_ACCESS_TOKEN") or "").strip()
    if not media_id or not token:
        return ""
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{media_id}",
            params={"fields": "permalink", "access_token": token},
            timeout=30,
        )
        data = resp.json() if resp.content else {}
        link = (data.get("permalink") or "").strip()
        if link.startswith("http"):
            print(f"[ig] permalink {link}")
            return link
        print(f"[ig] no permalink in Graph response: {data}")
    except Exception as exc:
        print(f"[ig] permalink lookup failed: {exc}")
    return ""


def _submit_back(platform: str, campaign: Campaign, video_url: str) -> None:
    if platform == "vyro":
        vyro_run_submit(campaign, video_url)
    elif platform == "whop":
        whop_submit_video_link(campaign, video_url)
    else:
        raise ValueError(f"Unknown platform: {platform}")


def _parse_requirements(campaign: Campaign) -> CampaignRequirements:
    raw_text = campaign.requirements_text or campaign.name
    try:
        parsed = ai_parse_requirements(raw_text)
        req = CampaignRequirements(
            campaign_id=campaign.campaign_id,
            mandatory_hashtags=parsed.mandatory_hashtags,
            required_links=parsed.required_links,
            min_seconds=parsed.min_seconds,
            max_seconds=parsed.max_seconds,
            referral_code=parsed.referral_code,
            watermark_text=parsed.watermark_text,
            raw_source=raw_text,
        )
        req.validate()
        if parsed.notes:
            print(f"AI parser note: {parsed.notes}")
        return req
    except (AIBrainError, RequirementsParseError) as exc:
        print(f"AI requirements parsing failed ({exc}); falling back to regex parser.", file=sys.stderr)
        req = parse_campaign(raw_text)
        req.campaign_id = campaign.campaign_id
        return req


def _generate_metadata(hook: str, summary: str, req: CampaignRequirements) -> VideoMetadata:
    try:
        ai_meta = ai_generate_metadata(
            hook=hook,
            summary=summary,
            mandatory_hashtags=req.mandatory_hashtags,
            required_links=req.required_links,
            referral_code=req.referral_code,
        )
        return VideoMetadata(title=ai_meta.title, description=ai_meta.description, tags=ai_meta.hashtags)
    except (AIBrainError, MetadataError) as exc:
        print(f"AI metadata generation failed ({exc}); falling back to template.", file=sys.stderr)
        return generate_metadata(hook=hook, summary=summary, req=req)



def _relax_min_seconds_to_source(req: CampaignRequirements, source_path: str) -> None:
    """Stop render from dying when AI parsed min=15 but the clip is 14.5s."""
    try:
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", source_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        source_dur = float((probe.stdout or "0").strip() or 0)
    except Exception as exc:  # noqa: BLE001
        print(f"Could not probe source duration ({exc}); render will use parsed min.")
        return
    if not source_dur:
        return
    if source_dur < req.min_seconds:
        new_min = max(1.0, source_dur - 0.05)
        print(
            f"Source is {source_dur:.2f}s < parsed min {req.min_seconds}s "
            f"— lowering min to {new_min:.2f}s"
        )
        req.min_seconds = new_min


def process_campaign(platform: str, campaign: Campaign, preferred_clip: dict | None = None) -> int:
    req = _parse_requirements(campaign)

    try:
        if preferred_clip and preferred_clip.get("kind") == "drive_file":
            fid = preferred_clip.get("clip_id") or extract_drive_file_id(preferred_clip.get("url") or "")
            drive_key = (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
            if not fid or not drive_key:
                raise GoogleDocReadError("Drive clip selected but file id / API key missing")
            download_drive_file(fid, SOURCE_CLIP_PATH, drive_key)
            resolved_source = preferred_clip.get("url") or fid
        elif preferred_clip and preferred_clip.get("url"):
            resolved_source = resolve_and_download_footage(
                dest_path=SOURCE_CLIP_PATH,
                source_clip_url=preferred_clip["url"],
                reference_doc_url="",
                brief_text="",
                download_url_fn=_download_source_clip,
                download_drive_fn=lambda i, d: download_drive_file(
                    i, d, (os.environ.get("GOOGLE_DRIVE_API_KEY") or "").strip()
                ),
                pick_drive_folder_fn=_pick_drive_folder,
            )
        else:
            resolved_source = _resolve_source_clip(campaign)
        print(f"[1/5] Resolved + downloaded source clip from {resolved_source} -> {SOURCE_CLIP_PATH}")
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to resolve/download source clip: {exc}", file=sys.stderr)
        return 1

    hook = campaign.name or "New campaign clip"
    youtube_url = None

    try:
        _relax_min_seconds_to_source(req, SOURCE_CLIP_PATH)
        render_short(
            source_path=SOURCE_CLIP_PATH,
            output_path=OUTPUT_PATH,
            req=req,
            fallback_caption_text=hook,
        )
        print(f"[2/5] Rendered vertical short -> {OUTPUT_PATH}")

        meta = _generate_metadata(hook=hook, summary=(campaign.requirements_text or "")[:200], req=req)
        print(f"[3/5] Generated metadata. Title: {meta.title}")

        validate_or_raise(
            video_path=OUTPUT_PATH,
            title=meta.title,
            description=meta.description,
            req=req,
        )

        youtube_url = None
        if _youtube_token_ready():
            try:
                yt = upload_video(
                    video_path=OUTPUT_PATH,
                    title=meta.title,
                    description=meta.description,
                    tags=meta.tags,
                    privacy_status="unlisted",
                    token_path="token.json",
                )
                youtube_url = yt.video_url
                print(f"[4/5] Uploaded to YouTube (backup, unlisted): {youtube_url}")
            except UploadError as exc:
                print(f"[4/5] YouTube upload failed (IG is primary, continuing): {exc}", file=sys.stderr)
        else:
            print("[4/5] No usable YouTube token — skipping YT. Instagram is the main target.")
    except RenderError as exc:
        print(f"Rendering failed: {exc}", file=sys.stderr)
        return 1
    except ValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    instagram_media_id = None
    try:
        instagram_media_id = upload_reel(
            video_path=OUTPUT_PATH,
            caption=_ig_caption(campaign, req, meta),
            release_tag=f"clip-{platform}-{campaign.campaign_id}-{int(__import__('time').time())}",
        )
        print(f"[5/5] Uploaded to Instagram Reels (MAIN): {instagram_media_id}")
    except InstagramUploadError as exc:
        print(f"Instagram upload failed: {exc}", file=sys.stderr)
        print(
            "IG needs IG_ACCESS_TOKEN + IG_BUSINESS_ACCOUNT_ID + "
            "ASSET_HOST_REPO + ASSET_HOST_TOKEN (public GitHub release host).",
            file=sys.stderr,
        )

    ig_url = _instagram_permalink(str(instagram_media_id or ""))
    submit_url = ig_url or (youtube_url or "").strip()
    if submit_url and "youtube.com/watch" in submit_url and "v=" not in submit_url:
        print(f"[submit] refusing broken YouTube URL: {submit_url}")
        submit_url = ""
    if ig_url:
        print(f"[6/5] Auto-submitting Instagram FIRST: {ig_url}")
    elif submit_url:
        print(f"[6/5] No IG permalink — falling back to YouTube: {submit_url}")
    if submit_url:
        try:
            _submit_back(platform, campaign, submit_url)
            print(f"Submitted {submit_url} to {platform} campaign '{campaign.campaign_id}'.")
        except (VyroClientError, WhopClientError) as exc:
            print(
                f"Upload succeeded but {platform} submission failed: {exc}\n"
                f"SUBMIT THIS URL MANUALLY: {submit_url}",
                file=sys.stderr,
            )
    else:
        print("[6/5] Nothing public to auto-submit.")

    if instagram_media_id or youtube_url:
        return 0
    print("Rendered OK but neither Instagram nor YouTube published. Not marking processed.")
    return 2


def main() -> int:
    clip_log = _load_clip_log()

    platform, campaign = _find_campaign()

    if campaign is None:
        print("No usable active campaign today on Vyro or Whop. Exiting.")
        return 0

    if campaign.campaign_id in set(clip_log.get("closed_campaigns") or []):
        print(f"Campaign '{campaign.campaign_id}' is closed. Ignoring its folder forever.")
        return 0

    print(f"Found active campaign on {platform}: {campaign.campaign_id} — {campaign.name}")

    nxt = _next_unused_clip(campaign, clip_log)
    if nxt is None:
        print("No unused Content Folder clip left on this campaign (or folder unreadable).")
        return 0

    status = process_campaign(platform, campaign, preferred_clip=nxt)
    if status == 0:
        clip_log["clips"].append(
            {
                "campaign_id": campaign.campaign_id,
                "clip_id": nxt["clip_id"],
                "url": nxt.get("url", ""),
                "kind": nxt.get("kind", ""),
            }
        )
        _save_clip_log(clip_log)
        print(f"Recorded clip {nxt['clip_id']} for campaign {campaign.campaign_id}.")
        return 0
    if status == 2:
        print("Rendered but no IG/YouTube publish. Clip NOT marked used.")
        return 0
    return status


if __name__ == "__main__":
    sys.exit(main())
