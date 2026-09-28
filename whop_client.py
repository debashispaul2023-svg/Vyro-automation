"""
whop_client.py

Browser-automation client for Whop (whop.com) "Content Rewards" clipping
campaigns.

================================================================================
HOW THIS DIFFERS FROM vyro_client.py
================================================================================
Whop has no single "my campaigns" dashboard page to scrape — every joined
campaign lives at its own URL under the creator's Whop
(whop.com/<creator>/<exp-id>/app/campaigns/<campaign-id>/), and there's no
in-app list that shows them all in one place. So instead of scraping a
"campaigns page", this module reads a small config file
(whop_campaigns.json) listing the campaign URLs you've already joined, and
checks each one directly. When you join a new campaign on Whop, add its URL
to that file.

Auth also works differently: you log in to Whop via Google (unscriptable),
so this replays your full captured cookie jar (see module docstring below
for how to capture it) instead of performing a login.

================================================================================
HOW AUTH WORKS
================================================================================
  1. Log in to https://whop.com normally in your phone browser via Google
  2. Open DevTools -> Network tab, reload the page
  3. Tap any request going to whop.com -> Headers tab -> find the
     "Cookie:" line under Request Headers
  4. Copy the ENTIRE value of that line
  5. Set it as the GitHub secret WHOP_COOKIE_HEADER

⚠️ Known risk: one of these cookies, `cf_clearance`, is Cloudflare's
bot-check pass-token and may be tied to the IP/browser fingerprint that
earned it — it may not transfer cleanly to a GitHub Actions runner. If
check_configured_campaigns() fails with a Cloudflare challenge-page error
(not a login redirect), that's this issue — it needs a different fix
(residential proxy etc), not just a fresh cookie.
================================================================================
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

CAMPAIGNS_CONFIG_PATH = "whop_campaigns.json"
WHOP_DISCOVER_URL = "https://whop.com/discover"
BLOXCLIPS_HOME = "https://whop.com/bloxclips/"
BLOXCLIPS_APP = "https://whop.com/bloxclips/exp_EfN9ClEYDL8Bh9/app/"
WHOP_DISCOVER_URLS = (
    BLOXCLIPS_APP,
)
DEFAULT_TIMEOUT_MS = 45000
COOKIE_DOMAIN = ".whop.com"


class WhopClientError(Exception):
    """Raised on any Whop browser-automation failure."""


class WhopSessionExpired(WhopClientError):
    """Raised specifically when the saved cookie jar no longer works."""


@dataclass
class WhopCampaign:
    campaign_id: str
    name: str
    requirements_text: str
    source_clip_url: str
    submit_page_url: str
    reference_doc_url: Optional[str] = None
    platforms: list[str] = field(default_factory=list)
