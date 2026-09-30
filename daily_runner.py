"""Restore wrapper. Full runner lives in daily_runner-2.py after a bad overwrite."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_IMPL = Path(__file__).with_name("daily_runner-2.py")
_spec = importlib.util.spec_from_file_location("daily_runner_impl", _IMPL)
if _spec is None or _spec.loader is None:
    raise ImportError(f"cannot load {_IMPL}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["daily_runner_impl"] = _mod
_spec.loader.exec_module(_mod)
globals().update(vars(_mod))

if __name__ == "__main__":
    raise SystemExit(int(_mod.main() or 0))
