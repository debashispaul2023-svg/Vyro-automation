"""Footage identity. Filename and title are not identity."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

LOG = "footage_usage.jsonl"
OVERLAP_THRESHOLD = float(os.environ.get("FOOTAGE_OVERLAP_THRESHOLD", "0.50"))
COOLDOWN = int(os.environ.get("RECENT_FOOTAGE_COOLDOWN", "7"))


def allow_reuse() -> bool:
    return (os.environ.get("ALLOW_REUSE_WHEN_POOL_EXHAUSTED") or "").strip().lower() in ("1", "true", "yes")


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fingerprint(path: str) -> str:
    bits = []
    for t in (0.4, 1.5, 3.0):
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", "scale=8:8,format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=20,
        )
        if raw.returncode == 0 and raw.stdout:
            bits.append(raw.stdout[:64].hex())
    return "|".join(bits)


def similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    pa, pb = a.split("|"), b.split("|")
    same = 0
    total = 0
    for x, y in zip(pa, pb):
        xb, yb = bytes.fromhex(x), bytes.fromhex(y)
        total += len(xb)
        same += sum(1 for i, j in zip(xb, yb) if abs(i - j) <= 3)
    return same / max(1, total)


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    span = max(0.0, a1 - a0)
    if span <= 0:
        return 0.0
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / span


def _log() -> str:
    return os.environ.get("FOOTAGE_USAGE_LOG") or LOG


def load() -> list:
    path = _log()
    if not os.path.isfile(path):
        return []
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print("[footage] skipped corrupt usage line")
    return rows


def record(row: dict) -> bool:
    row = dict(row)
    key = row.get("source_file_id") or row.get("clip_id") or row.get("video_id") or row.get("source_hash")
    if key and any((r.get("source_file_id") or r.get("clip_id") or r.get("video_id") or r.get("source_hash")) == key for r in load()):
        return False
    row.setdefault("used_at", datetime.now(timezone.utc).isoformat())
    row.setdefault("usage_count", 1)
    with open(_log(), "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return True


def backfill(processed_path: str, published_path: str) -> dict:
    """Record known ids. Hash only when a local file is explicitly mapped."""
    added = unresolved = 0
    if os.path.isfile(processed_path):
        data = json.load(open(processed_path, encoding="utf-8"))
        for row in data.get("clips") or []:
            cid = row.get("clip_id") or ""
            if not cid:
                continue
            if record({"source_file_id": cid, "source_file": row.get("name") or "", "campaign": row.get("campaign_id") or "", "status": "LEGACY_UNRESOLVED", "start": 0, "end": 0}):
                unresolved += 1
                added += 1
    if os.path.isfile(published_path):
        for row in json.load(open(published_path, encoding="utf-8")):
            vid = row.get("video_id") or ""
            if not vid:
                continue
            if record({"video_id": vid, "source_file": row.get("title") or "", "status": "LEGACY_UNRESOLVED"}):
                unresolved += 1
                added += 1
    print(f"[footage] backfill added={added} unresolved={unresolved}")
    return {"added": added, "unresolved": unresolved}


def reject(candidate: dict, rows: list | None = None) -> str:
    rows = rows if rows is not None else load()
    cid = (candidate.get("clip_id") or candidate.get("source_file_id") or "").strip()
    digest = candidate.get("source_hash") or ""
    finger = candidate.get("fingerprint") or ""
    start = float(candidate.get("start") or 0)
    end = float(candidate.get("end") or 0)
    for row in rows:
        if digest and digest == row.get("source_hash"):
            ratio = overlap_ratio(start, end or start + 1, float(row.get("start") or 0), float(row.get("end") or 0))
            if end > start and 0 < ratio < 0.99 and ratio >= OVERLAP_THRESHOLD:
                print(f"[footage] rejected={cid or digest[:12]} reason=SEGMENT_OVERLAP")
                return "SEGMENT_OVERLAP"
            print(f"[footage] rejected={cid or digest[:12]} reason=CONTENT_DUPLICATE")
            return "CONTENT_DUPLICATE"
        if finger and similar(finger, row.get("fingerprint") or "") >= 0.98:
            print(f"[footage] rejected={cid or 'fingerprint'} reason=VISUAL_DUPLICATE")
            return "VISUAL_DUPLICATE"
    for row in rows[-COOLDOWN:]:
        if cid and cid == (row.get("source_file_id") or row.get("clip_id") or ""):
            print(f"[footage] rejected={cid} reason=COOLDOWN")
            return "COOLDOWN"
    print("[footage] selected=YES reason=UNIQUE_COMPATIBLE_FOOTAGE")
    return ""
