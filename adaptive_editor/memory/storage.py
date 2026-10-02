"""JSONL storage. Corrupt lines are skipped. Backend can be swapped later."""
from __future__ import annotations

import json
import os

from .. import config


def data_dir() -> str:
    return os.environ.get("ADAPTIVE_MEMORY_DIR") or os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data"
    )


def _path(name: str) -> str:
    os.makedirs(data_dir(), exist_ok=True)
    return os.path.join(data_dir(), name)


def read_jsonl(name: str) -> list:
    path = _path(name)
    if not os.path.isfile(path):
        return []
    rows = []
    try:
        raw = open(path, encoding="utf-8").read().splitlines()
    except Exception as exc:
        print(f"[adaptive-memory] storage read failed: {exc}")
        return []
    for line in raw:
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            print("[adaptive-memory] skipped corrupt memory line")
    return rows


def append_jsonl(name: str, row: dict) -> bool:
    try:
        with open(_path(name), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return True
    except Exception as exc:
        print(f"[adaptive-memory] storage write failed: {exc}")
        return False


def read_json(name: str, default: dict) -> dict:
    path = _path(name)
    if not os.path.isfile(path):
        return default
    try:
        data = json.loads(open(path, encoding="utf-8").read())
        return data if isinstance(data, dict) else default
    except Exception as exc:
        print(f"[adaptive-memory] pattern file unreadable: {exc}")
        return default


def write_json(name: str, payload: dict) -> bool:
    try:
        with open(_path(name), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return True
    except Exception as exc:
        print(f"[adaptive-memory] pattern write failed: {exc}")
        return False
