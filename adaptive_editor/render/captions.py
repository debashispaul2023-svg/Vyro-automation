"""Caption timing and safe SRT text. No AI."""
from __future__ import annotations


def segments(plan: dict) -> list:
    rows = []
    for row in plan.get("captions") or []:
        text = " ".join(str(row.get("text") or "").split())
        if not text:
            continue
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        if end <= start:
            continue
        rows.append({"start": round(start, 2), "end": round(end, 2), "text": text[:80]})
    return rows


def to_srt(rows: list) -> str:
    blocks = []
    for i, row in enumerate(rows, 1):
        blocks.append(f"{i}\n{_ts(row['start'])} --> {_ts(row['end'])}\n{_safe(row['text'])}\n")
    return "\n".join(blocks)


def _safe(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("\n", " ")
        .replace("'", "")
        .replace('"', "")
        .replace(":", " ")
    )


def _ts(seconds: float) -> str:
    ms = int(round(max(0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
