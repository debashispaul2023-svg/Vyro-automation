"""Gemini brain with short per-key timeout so Daily cannot hang."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

MODEL_CANDIDATES = (
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-3.6-flash",
)
MODEL_NAME = MODEL_CANDIDATES[0]
GEMINI_TIMEOUT_SEC = 20


class AIBrainError(Exception):
    """Raised when a Gemini call fails and there is no safe fallback."""


def _gemini_keys() -> list[str]:
    blob = (os.environ.get("GEMINI_API_KEYS") or "").strip()
    keys = [k.strip() for k in blob.split(",") if k.strip()]
    one = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if one and one not in keys:
        keys.insert(0, one)
    return keys


def _model(api_key: str, model_name: str | None = None):
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name or MODEL_NAME)
