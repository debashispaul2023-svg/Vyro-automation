"""
clip_picker.py

Picks the best source clip from a pile of Drive footage so Part 4 does
not render a junk/black/too-short file.

Two-pass:
  1. Rank every candidate using Drive metadata only (size, duration,
     resolution) — no download.
  2. Download the top shortlist, grab a few frames, ask Gemini whether
     the footage is usable FIFA/World-Cup-style action (not blank, logo
     cards, or unrelated junk). If Gemini is unavailable, pass 1 wins.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Optional

import requests

from google_doc_reader import (
    DriveClip,
    collect_video_clips_in_drive_folder,
    extract_drive_file_id,
    find_candidate_source_clips,
    is_drive_folder_url,
)

MIN_SIZE_BYTES = 8 * 1024 * 1024
MIN_DURATION_S = 12.0
IDEAL_MAX_DURATION_S = 90.0
MIN_SHORT_SIDE = 720
SHORTLIST_N = 3
GEMINI_MODEL = os.environ.get("GEMINI_MODEL_NAME", "gemini-3.6-flash")


class ClipPickError(Exception):
    """Raised when no usable clip can be chosen."""


@dataclass
class ScoredClip:
    clip: DriveClip
    meta_score: float
    ai_score: float = 0.0
    ai_reason: str = ""
    local_path: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.meta_score + self.ai_score


def collect_clips_from_doc_text(doc_text: str) -> list[DriveClip]:
    raw = find_candidate_source_clips(doc_text)
    clips: list[DriveClip] = []
    seen: set[str] = set()
    for url in raw:
        if is_drive_folder_url(url):
            for clip in collect_video_clips_in_drive_folder(url):
                if clip.file_id not in seen:
                    seen.add(clip.file_id)
                    clips.append(clip)
            continue
        fid = extract_drive_file_id(url)
        if fid and fid not in seen:
            seen.add(fid)
            clips.append(
                DriveClip(
                    file_id=fid,
                    name=fid,
                    mime_type="",
                    size_bytes=0,
                    url=url,
                )
            )
    return clips


def _duration_s(clip: DriveClip) -> float:
    return (clip.duration_ms or 0) / 1000.0


def score_metadata(clip: DriveClip, min_seconds: float = MIN_DURATION_S) -> tuple[float, list[str]]:
    """Heuristic score from Drive metadata. Higher is better."""
    score = 0.0
    notes: list[str] = []
    size = clip.size_bytes
    dur = _duration_s(clip)
    short_side = min(clip.width, clip.height) if clip.width and clip.height else 0
    long_side = max(clip.width, clip.height)

    if size and size < MIN_SIZE_BYTES:
        score -= 4
        notes.append(f"tiny file ({size} bytes)")
    elif size >= 20 * 1024 * 1024:
        score += 2
        notes.append("size looks like real footage")
    elif size >= MIN_SIZE_BYTES:
        score += 1

    if dur:
        if dur < min_seconds:
            score -= 6
            notes.append(f"too short ({dur:.1f}s < {min_seconds}s)")
        elif min_seconds <= dur <= IDEAL_MAX_DURATION_S:
            score += 3
            notes.append(f"usable duration ({dur:.1f}s)")
        elif dur <= 180:
            score += 1
            notes.append(f"long source ({dur:.1f}s) — can trim")
        else:
            score -= 1
            notes.append(f"very long ({dur:.1f}s)")
    else:
        notes.append("no duration metadata")

    if short_side and short_side < MIN_SHORT_SIDE and long_side < 1280:
        score -= 3
        notes.append(f"low-res ({clip.width}x{clip.height})")
    elif long_side >= 1920 or short_side >= 1080:
        score += 3
        notes.append(f"HD ({clip.width}x{clip.height})")
    elif long_side >= 1280:
        score += 2
        notes.append(f"decent res ({clip.width}x{clip.height})")

    name = (clip.name or "").lower()
    if name.startswith("comp "):
        score += 1
        notes.append("looks like a finished edit (Comp)")
    if clip.mime_type == "application/vnd.google-apps.vid":
        score -= 5
        notes.append("Google-native vid — skip, not a real file")

    return score, notes


def shortlist_clips(
    clips: list[DriveClip],
    *,
    min_seconds: float = MIN_DURATION_S,
    n: int = SHORTLIST_N,
) -> list[ScoredClip]:
    usable: list[ScoredClip] = []
    rejected = 0
    for clip in clips:
        if clip.mime_type == "application/vnd.google-apps.vid":
            rejected += 1
            continue
        meta, notes = score_metadata(clip, min_seconds=min_seconds)
        if _duration_s(clip) and _duration_s(clip) < min_seconds:
            rejected += 1
            continue
        if clip.size_bytes and clip.size_bytes < MIN_SIZE_BYTES:
            rejected += 1
            continue
        usable.append(ScoredClip(clip=clip, meta_score=meta, notes=notes))

    usable.sort(key=lambda s: s.meta_score, reverse=True)
    print(f"[picker] {len(clips)} clips in → {len(usable)} passed metadata, {rejected} rejected")
    for i, item in enumerate(usable[: n * 2], 1):
        c = item.clip
        print(
            f"[picker]   meta#{i} {item.meta_score:+.1f}  "
            f"{c.name}  {c.size_bytes}B  {c.width}x{c.height}  "
            f"{_duration_s(c):.1f}s  {'; '.join(item.notes)}"
        )
    if not usable:
        raise ClipPickError("Every clip failed the metadata filter (too small/short/low-res).")
    return usable[:n]


def download_drive_file(file_id: str, dest_path: str, api_key: str) -> str:
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    resp = requests.get(
        url,
        params={"alt": "media", "key": api_key},
        stream=True,
        timeout=180,
    )
    if resp.status_code != 200:
        raise ClipPickError(
            f"Drive download failed for {file_id} (status {resp.status_code}): "
            f"{resp.text[:300]}"
        )
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            if chunk:
                f.write(chunk)
    if not os.path.isfile(dest_path) or os.path.getsize(dest_path) < 1000:
        raise ClipPickError(f"Downloaded file is empty/tiny: {dest_path}")
    print(f"[picker] downloaded {file_id} → {dest_path} ({os.path.getsize(dest_path)} bytes)")
    return dest_path


def _extract_frames(video_path: str, dest_dir: str) -> list[str]:
    os.makedirs(dest_dir, exist_ok=True)
    frames: list[str] = []
    for i, stamp in enumerate(("00:00:02", "00:00:06", "00:00:10"), start=1):
        out = os.path.join(dest_dir, f"frame_{i}.jpg")
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-ss", stamp, "-i", video_path,
                "-frames:v", "1", "-q:v", "3", out,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and os.path.isfile(out):
            frames.append(out)
    return frames


def _gemini_score_frames(frames: list[str], campaign_notes: str) -> tuple[float, str]:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key or not frames:
        return 0.0, "Gemini skipped (no key or no frames)"

    try:
        import google.generativeai as genai
        from PIL import Image
    except ImportError:
        return 0.0, "Gemini skipped (package missing)"

    prompt = f"""You are picking source footage for a FIFA / World Cup vertical edit.
Reject junk: black/blank screens, static title cards, memes with no football,
ultra-blurry clips, slideshows, or unrelated talking-head footage.
Prefer real football action, crowd, players, stadium, ball — sharp HD.

Campaign notes:
\"\"\"{campaign_notes[:1500]}\"\"\"

Look at the frames. Respond with ONLY JSON:
{{"score": 0-10, "usable": true or false, "reason": "one short sentence"}}
"""
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(GEMINI_MODEL)
        parts: list = [prompt]
        for path in frames[:3]:
            parts.append(Image.open(path))
        response = model.generate_content(
            parts,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=256,
                temperature=0.2,
            ),
        )
        text = (response.text or "").strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
        data = json.loads(cleaned)
        raw_score = float(data.get("score", 0))
        usable = bool(data.get("usable", raw_score >= 6))
        reason = str(data.get("reason") or "")
        if not usable:
            raw_score = min(raw_score, 3)
        return raw_score, reason
    except Exception as exc:
        return 0.0, f"Gemini failed ({exc})"


def pick_best_clip(
    clips: list[DriveClip],
    *,
    work_dir: str,
    api_key: str,
    campaign_notes: str = "",
    min_seconds: float = MIN_DURATION_S,
) -> ScoredClip:
    shortlisted = shortlist_clips(clips, min_seconds=min_seconds, n=SHORTLIST_N)
    best: Optional[ScoredClip] = None

    for i, item in enumerate(shortlisted, start=1):
        ext = ".mp4"
        name = (item.clip.name or "").lower()
        if name.endswith(".mov"):
            ext = ".mov"
        dest = os.path.join(work_dir, f"candidate_{i}{ext}")
        try:
            download_drive_file(item.clip.file_id, dest, api_key)
        except ClipPickError as exc:
            print(f"[picker] candidate {i} download failed: {exc}")
            continue
        item.local_path = dest
        frames = _extract_frames(dest, os.path.join(work_dir, f"frames_{i}"))
        print(f"[picker] candidate {i}: {len(frames)} frame(s)")
        ai_score, reason = _gemini_score_frames(frames, campaign_notes)
        item.ai_score = ai_score
        item.ai_reason = reason
        print(
            f"[picker] candidate {i} total={item.total:.1f} "
            f"(meta {item.meta_score:+.1f} + ai {ai_score:.1f}) {reason}"
        )
        if best is None or item.total > best.total:
            best = item

    if best is None or not best.local_path:
        raise ClipPickError("Could not download any shortlisted clip.")

    print(
        f"[picker] WINNER: {best.clip.name}  total={best.total:.1f}  "
        f"{best.ai_reason or '; '.join(best.notes)}"
    )
    return best
