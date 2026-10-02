from __future__ import annotations

import os


def enabled() -> bool:
    return (os.environ.get("ADAPTIVE_EDITOR_ENABLED") or "0").strip() == "1"


def chatgpt_enabled() -> bool:
    return (os.environ.get("CHATGPT_REVIEW") or "0").strip() == "1"


def output_dir() -> str:
    return os.environ.get("ADAPTIVE_OUTPUT_DIR") or "output/adaptive"
