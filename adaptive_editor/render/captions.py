"""Caption timing and safe SRT text. No AI."""
from __future__ import annotations


def segments(plan: dict, duration: float | None = None) -> list:
    limit = duration if duration is not None else float(plan.get("duration_target") or 0)
    rows = []
    last_end = 0.0
    for row in plan.get("captions") or []:
        text = wrap(" ".join(str(row.get("text") or "").replace("\n", " ").split()))
        if not text:
            continue
        start = float(row.get("start") or 0)
        end = float(row.get("end") or 0)
        if end <= start or start < 0:
            continue
        if not row.get("simultaneous"):
            start = max(start, last_end)
        if limit > 0:
            end = min(end, limit)
        if end - start < 0.3:
            continue
        rows.append({"start": round(start, 2), "end": round(end, 2), "text": text})
        last_end = max(last_end, end)
    return rows


def wrap(text: str, width: int = 32) -> str:
    words = text.split()
    lines, cur = [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if len(trial) > width and cur:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return "\n".join(lines[:3])


def to_srt(rows: list) -> str:
    blocks = []
    for i, row in enumerate(rows, 1):
        blocks.append(f"{i}\n{_ts(row['start'])} --> {_ts(row['end'])}\n{_safe(row['text'])}\n")
    return "\n".join(blocks)


def _safe(text: str) -> str:
    return (
        text.replace("\\", "")
        .replace("'", "")
        .replace('"', "")
        .replace("&", " and ")
    )


def _ts(seconds: float) -> str:
    ms = int(round(max(0, seconds) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
