"""Index old campaign footage into the experience bank. Never uploads."""
from __future__ import annotations

import argparse
import os
import sys

from ..analyzer import probe
from ..event_detector import event_from_name
from ..hook_engine import score
from ..roadmap import build
from ..schemas import Shot
from ..shot_detector import detect_shots
from .footage_memory import already_indexed, file_hash
from .schemas import memory_record
from .storage import append_jsonl

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv"}


def import_path(path: str) -> dict:
    stats = {"indexed": 0, "skipped": 0, "failed": 0}
    if os.path.isdir(path):
        for root, _dirs, files in os.walk(path):
            for name in files:
                if os.path.splitext(name)[1].lower() in VIDEO_EXT:
                    _one(os.path.join(root, name), stats)
    elif os.path.isfile(path):
        _one(path, stats)
    else:
        print(f"[adaptive-memory] path missing: {path}")
        stats["failed"] += 1
    print(f"[adaptive-memory] indexed={stats['indexed']} skipped={stats['skipped']} failed={stats['failed']}")
    return stats


def _one(path: str, stats: dict) -> None:
    try:
        digest = file_hash(path)
        if already_indexed(digest):
            print(f"[adaptive-memory] already indexed: {digest[:12]}")
            stats["skipped"] += 1
            return
        meta = probe(path)
        windows = detect_shots(path) or ([(0.0, min(meta["duration"], 2.5))] if meta["duration"] > 0.4 else [])
        if not windows:
            print(f"[adaptive-memory] no shots: {path}")
            stats["failed"] += 1
            return
        shots = []
        for start, end in windows:
            event = event_from_name(os.path.basename(path))
            shots.append(score(Shot(path, start, end, event, reason=event)))
        story = build(shots)
        row = memory_record(
            memory_id=digest[:12] or os.path.basename(path),
            source="old_campaign",
            events=sorted({s.event for s in shots}),
            shots=[{"start": s.start, "end": s.end, "event": s.event, "hook_score": s.hook_score} for s in shots],
            hook=story.hook or {},
            structure=story.structure,
            duration=meta["duration"],
            qc_passed=True,
            file_hash=digest,
            result="unknown",
        )
        if append_jsonl("footage_memory.jsonl", row):
            print(f"[adaptive-memory] indexed {os.path.basename(path)} structure={story.structure}")
            stats["indexed"] += 1
        else:
            stats["failed"] += 1
    except Exception as exc:
        print(f"[adaptive-memory] import failed {path}: {exc}")
        stats["failed"] += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?")
    parser.add_argument("--directory")
    args = parser.parse_args()
    target = args.directory or args.path
    if not target:
        print("usage: python -m adaptive_editor.memory.importer --directory ./old_campaigns")
        return 2
    import_path(target)
    print("[adaptive-memory] upload: never")
    return 0


if __name__ == "__main__":
    sys.exit(main())
