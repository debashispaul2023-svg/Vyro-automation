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
    "gemini-3.6-flash",
    "gemini-2.5-flash",
)
MODEL_NAME = MODEL_CANDIDATES[0]
GEMINI_TIMEOUT_SEC = 20
