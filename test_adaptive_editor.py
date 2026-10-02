"""Planning test. Never uploads, never touches production logs."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def _make_clips(folder: str) -> list[str]:
    os.makedirs(folder, exist_ok=True)
    specs = [
        ("roll_dice.mp4", "blue"),
        ("character_reveal.mp4", "yellow"),
        ("money_reward.mp4", "green"),
    ]
    paths = []
    for name, color in specs:
        dest = os.path.join(folder, name)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=24", "-t", "3", dest],
            check=True, capture_output=True, timeout=30,
        )
        paths.append(dest)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-render", action="store_true")
    parser.add_argument("--clips", nargs="*", default=[])
    args = parser.parse_args()
    out = os.environ.get("ADAPTIVE_OUTPUT_DIR") or "output/adaptive_test"
    clips = args.clips or _make_clips(os.path.join(out, "clips"))
    from adaptive_editor.engine import run_adaptive
    result = run_adaptive(clips, out_dir=out, local_test=True)
    if args.no_render:
        print("[adaptive] rendering skipped")
    else:
        print("[adaptive] rendering: planning-only in v1 test (no upload)")
    print("[adaptive] fallback:" , "not needed" if result.get("ok") else result.get("reason"))
    print("[adaptive] upload: never")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
