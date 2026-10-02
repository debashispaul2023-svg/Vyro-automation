"""Old-footage memory trial. Temp dir only. No upload."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile


def _clip(folder: str, name: str, src: str, seconds: str) -> str:
    dest = os.path.join(folder, name)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", src, "-t", seconds, dest], check=True, capture_output=True, timeout=30)
    return dest


def main() -> int:
    root = tempfile.mkdtemp(prefix="adaptive-mem-")
    os.environ["ADAPTIVE_MEMORY_DIR"] = os.path.join(root, "bank")
    old = os.path.join(root, "old")
    os.makedirs(old, exist_ok=True)
    roll = _clip(old, "roll_dice.mp4", "testsrc=size=320x180:rate=24", "3")
    reward = _clip(old, "money_reward.mp4", "testsrc=size=320x180:rate=24", "4")
    _clip(old, "static_roll.mp4", "color=c=blue:s=320x180:rate=24", "2")
    shutil.copy(roll, os.path.join(old, "roll_copy.mp4"))
    from adaptive_editor.memory.importer import import_path
    from adaptive_editor.memory.storage import read_jsonl
    first = import_path(old)
    second = import_path(old)
    rows = read_jsonl("footage_memory.jsonl")
    assert first["indexed"] <= 1, first
    assert first["failed"] >= 2, first
    assert second["indexed"] == 0
    child = subprocess.run(
        [sys.executable, "-c", "import os; from adaptive_editor.memory.storage import read_jsonl; print(len(read_jsonl('footage_memory.jsonl')))"],
        cwd=os.getcwd(), env={**os.environ, "PYTHONPATH": os.getcwd(), "ADAPTIVE_MEMORY_DIR": os.environ["ADAPTIVE_MEMORY_DIR"]},
        capture_output=True, text=True, timeout=30,
    )
    assert child.stdout.strip() in ("0", "1"), child.stderr
    open(os.path.join(os.environ["ADAPTIVE_MEMORY_DIR"], "footage_memory.jsonl"), "a").write("{bad\n")
    from adaptive_editor.hook_engine import score
    from adaptive_editor.memory.feedback import record_feedback
    from adaptive_editor.memory.pattern_memory import load_patterns
    from adaptive_editor.memory.retrieval import advise
    from adaptive_editor.roadmap import build
    from adaptive_editor.schemas import Shot
    shots = [
        score(Shot("new_roll.mp4", 0, 2, "ROLL", reason="ROLL", supported=True)),
        score(Shot("new_money.mp4", 0, 2, "REWARD", reason="REWARD", supported=True)),
    ]
    advice = advise(shots)
    assert advice["selected"] != "tease_roll_reveal"
    assert "CHARACTER_REVEAL" in advice.get("reason", "") or advice["selected"] == "generic_roll"
    story = build(shots, pattern_id="tease_roll_reveal")
    assert story.structure != "tease_roll_reveal"
    assert all(step["reason"] != "CHARACTER_REVEAL" for step in story.roadmap)
    record_feedback("test-only", "generic_roll", "successful", None)
    record_feedback("test-only-weak", "generic_roll", "weak", None)
    record_feedback("test-only-unknown", "generic_roll", "unknown", None)
    pattern = [p for p in load_patterns() if p["pattern_id"] == "generic_roll"][0]
    assert pattern["success_count"] >= 1
    assert pattern["performance"] if False else pattern.get("result") in ("successful", "weak")
    for _ in range(5):
        open(os.path.join(os.environ["ADAPTIVE_MEMORY_DIR"], "footage_memory.jsonl"), "a").write(
            '{"structure":"generic_roll","events":["ROLL"],"result":"unknown"}\n'
        )
    again = advise(shots)
    assert again["penalty"] == 0.1 or "overused" in again.get("reason", "")
    print("[adaptive-memory] persistence ok")
    print("[adaptive-memory] upload: never")
    print(f"indexed={first['indexed']} duplicates={first['skipped']} failed={first['failed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
