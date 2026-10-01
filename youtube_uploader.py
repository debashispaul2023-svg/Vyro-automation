"""Upload a short to YouTube. Next uploads stay unlisted until YOUTUBE_PRIVACY=public."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
DEFAULT_TOKEN_PATH = "token.json"
GAMING_CATEGORY_ID = "20"
SHARED_TAGS = ["Roll Anime Girls", "Roblox", "Roblox RNG", "shorts", "tycoon", "offline money"]
PUBLISHED_LOG = "published_log.json"

CAMPAIGN_LOCK = {
    "roll anime girls": {
        "name": "Roll Anime Girls",
        "cta": "Game is called Roll Anime Girls on Roblox.",
        "link": "https://www.roblox.com/games/92289737492030/Roll-Anime-Girls",
    },
    "how to fisch": {
        "name": "How to Fisch",
        "cta": "Game is called How to Fisch on Roblox.",
        "link": "https://www.roblox.com/games/119870009085173/How-to-Fisch",
    },
    "tongue escape": {
        "name": "+1 Tongue Escape",
        "cta": "Game is called +1 Tongue Escape on Roblox.",
        "link": "",
    },
    "steal a seed": {
        "name": "Steal A Seed",
        "cta": "Game is called Steal A Seed on Roblox.",
        "link": "https://www.roblox.com/games/122216176958450/Steal-A-Seed",
    },
}
