"""Footage identity. Filename and title are not identity."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone

LOG = os.environ.get("FOOTAGE_USAGE_LOG", "footage_usage.jsonl")
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
    if not a or not b or a == b:
        return 1.0 if a and a == b else 0.0
    pa, pb = a.split("|"), b.split("|")
    same = 0
    total = 0
    for x, y in zip(pa, pb):
        xb, yb = bytes.fromhex(x), bytes.fromhex(y)
        total += len(xb)
        same += sum(1 for i, j in zip(xb, yb) if abs(i - j) <= 8)
    return same / max(1, total)


def overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    span = max(0.0, a1 - a0)
    if span <= 0:
        return 0.0
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / span


def load() -> list:
    if not os.path.isfile(LOG):
        return []
    rows = []
    for line in open(LOG, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print("[footage] skipped corrupt usage line")
    return rows


def record(row: dict) -> None:
    row = dict(row)
    row.setdefault("used_at", datetime.now(timezone.utc).isoformat())
    row.setdefault("usage_count", 1)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def reject(candidate: dict, rows: list | None = None) -> str:
    rows = rows if rows is not None else load()
    cid = (candidate.get("clip_id") or candidate.get("source_file_id") or "").strip()
    digest = candidate.get("source_hash") or ""
    finger = candidate.get("fingerprint") or ""
    start = float(candidate.get("start") or 0)
    end = float(candidate.get("end") or 0)
    recent = rows[-COOLDOWN:]
    for row in recent:
        if cid and cid == (row.get("source_file_id") or row.get("clip_id") or ""):
            return "RECENT_FOOTAGE_COOLDOWN"
        if digest and digest == row.get("source_hash"):
            ratio = overlap_ratio(start, end or start + 1, float(row.get("start") or 0), float(row.get("end") or 0))
            if end <= start or ratio >= OVERLAP_THRESHOLD or float(row.get("end") or 0) <= float(row.get("start") or 0):
                return "CONTENT_HASH_DUPLICATE"
        if finger and similar(finger, row.get("fingerprint") or "") >= 0.92:
            return "VISUAL_FINGERPRINT_DUPLICATE"
        if digest and digest == row.get("source_hash") and end > start:
            if overlap_ratio(start, end, float(row.get("start") or 0), float(row.get("end") or 0)) >= OVERLAP_THRESHOLD:
                return "RECENT_FOOTAGE_OVERLAP"
    return ""
