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
# TODO: confirm this exact URL if it doesn't match — best guess based on
# the page heading "Discover Content Rewards" seen in screenshots.
WHOP_DISCOVER_URL = "https://whop.com/discover"
WHOP_DISCOVER_URLS = (
    "https://whop.com/discover",
    "https://whop.com/discover/content-rewards/",
)
DEFAULT_TIMEOUT_MS = 20000
COOKIE_DOMAIN = ".whop.com"


class WhopClientError(Exception):
    """Raised on any Whop browser-automation failure."""


class WhopSessionExpired(WhopClientError):
    """Raised specifically when the saved cookie jar no longer works."""


@dataclass
class WhopCampaign:
    campaign_id: str
    name: str
    requirements_text: str          # the page's visible description/rules text
    source_clip_url: str            # filled in later once resolved (may start empty)
    submit_page_url: str            # same as the campaign URL for Whop
    reference_doc_url: Optional[str] = None  # external Google Doc link, if any
    platforms: list[str] = field(default_factory=list)  # e.g. ["tiktok", "youtube", "instagram"]


def _load_configured_campaigns() -> list[dict]:
    """Loads whop_campaigns.json. Supports both the current format
    ({"joined_campaigns": [{"url": ..., "name_hint": ...}]}) and the older
    format ({"joined_campaign_urls": [...]}) for backwards compatibility —
    entries from the old format get an empty name_hint."""
    if not os.path.isfile(CAMPAIGNS_CONFIG_PATH):
        return []
    with open(CAMPAIGNS_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "joined_campaigns" in data:
        return list(data["joined_campaigns"])

    # Legacy format fallback.
    return [{"url": u, "name_hint": ""} for u in data.get("joined_campaign_urls", [])]


def _parse_cookie_header(raw: str) -> list[dict]:
    cookies = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append({"name": name.strip(), "value": value.strip(), "domain": COOKIE_DOMAIN, "path": "/"})
    return cookies


def _new_context_with_session(browser: Browser) -> BrowserContext:
    raw_cookie_header = os.environ.get("WHOP_COOKIE_HEADER")
    if not raw_cookie_header:
        raise WhopClientError(
            "WHOP_COOKIE_HEADER is not set. Capture your Whop cookies via "
            "DevTools -> Network -> any request -> Headers -> Cookie, and "
            "set the full value as the WHOP_COOKIE_HEADER GitHub secret."
        )
    context = browser.new_context()
    context.add_cookies(_parse_cookie_header(raw_cookie_header))
    return context


def _settle(page: Page) -> None:
    """Waits for the page's initial HTML to load (required), then makes a
    best-effort attempt to wait for network activity to quiet down. Modern
    dashboards often have background polling/analytics that never truly go
    idle, so a networkidle timeout here is NOT treated as an error."""
    page.wait_for_load_state("domcontentloaded", timeout=DEFAULT_TIMEOUT_MS)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PlaywrightTimeoutError:
        pass


def _ensure_logged_in(page: Page, target_url: str) -> None:
    page.goto(target_url, timeout=DEFAULT_TIMEOUT_MS)
    _settle(page)

    title = (page.title() or "").lower()
    if "just a moment" in title or "attention required" in title:
        raise WhopClientError(
            "Whop's Cloudflare bot-check blocked this request (cf_clearance "
            "didn't transfer to this server). See the module docstring's "
            "'Known risk' section — this needs a different fix, not just a "
            "cookie refresh."
        )

    if "/login" in page.url or re.search(r"whop\.com/start(\?|$)", page.url):
        raise WhopSessionExpired(
            "Whop session expired (redirected to login/start). Re-capture "
            "your cookies (see module docstring) and update the "
            "WHOP_COOKIE_HEADER GitHub secret."
        )


def _get_content_frame(page: Page):
    """Pick the campaign iframe. Never prefer the in-app Discover feed."""
    CONTENT_MARKERS = ("submit clip", "budget", "requirement", "campaign", "views", "dos")
    SCRIPT_LOOKING_PREFIXES = ("(function", "!function", "window.", "var ", "const ", "let ")

    try:
        page.wait_for_timeout(1500)
        ranked = []
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                text = frame.inner_text("body", timeout=3000)
            except Exception:  # noqa: BLE001
                continue
            stripped = text.strip()
            if not stripped or stripped.startswith(SCRIPT_LOOKING_PREFIXES):
                continue
            lower = stripped.lower()
            url = (frame.url or "").lower()
            marker_hits = sum(1 for marker in CONTENT_MARKERS if marker in lower)
            score = marker_hits * 10 + min(len(stripped), 5000) // 200
            if "submit clip" in lower:
                score += 25
            if "/campaigns/" in url:
                score += 40
            if "/discover" in url and "/campaigns/" not in url:
                score -= 80
            print(
                f"[whop_client debug] Frame {frame.url} markers={marker_hits} "
                f"chars={len(stripped)} score={score}"
            )
            ranked.append((score, frame))
        ranked.sort(key=lambda row: row[0], reverse=True)
        if ranked and ranked[0][0] > 0:
            print(f"[whop_client debug] Using frame {ranked[0][1].url}")
            return ranked[0][1]
    except Exception as exc:  # noqa: BLE001
        print(f"[whop_client debug] Iframe detection failed: {exc}")

    print("[whop_client debug] No substantial iframe found; using main page directly.")
    return page


def _extract_campaign_details(page: Page, campaign_url: str, name_hint: str = "") -> Optional[WhopCampaign]:
    """Reads a single joined-campaign page. Returns None if the campaign
    looks unavailable (region-locked, budget fully used, etc).

    name_hint (e.g. "RICOCHET") is an optional keyword from the campaign's
    name, stored in whop_campaigns.json, used to find the right card when
    the app lands on a scrollable "Your Campaigns" list instead of the
    specific campaign directly."""
    # Whop's campaign page is a heavy single-page app — the real content
    # (budget, description, submit button) loads via background API calls
    # AFTER the initial HTML, and often lives inside an embedded app
    # iframe rather than the main page body (see _get_content_frame).
    try:
        page.get_by_text(re.compile(r"submit clip|budget|views", re.I)).first.wait_for(
            state="visible", timeout=12000
        )
    except PlaywrightTimeoutError:
        print(f"[whop_client debug] Campaign content never appeared on the main page for {campaign_url} (checking iframes next).")

    frame = _get_content_frame(page)
    uuid_match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        campaign_url,
        re.I,
    )
    if uuid_match and "/discover" in (frame.url or "").lower():
        cid = uuid_match.group(1)
        base = (frame.url or "").split("/discover")[0]
        target = f"{base}/campaigns/{cid}"
        print(f"[whop_client debug] Discover iframe hijacked the page — opening {target}")
        try:
            frame.goto(target, timeout=20000)
            page.wait_for_timeout(2500)
            frame = _get_content_frame(page)
        except Exception as exc:  # noqa: BLE001
            print(f"[whop_client debug] Iframe goto failed: {exc}")
    body_text = frame.inner_text("body")
    print(f"[whop_client debug] Captured {len(body_text)} chars. First 300: {body_text[:300]!r}")

    # The app iframe has its own internal nav (Home/Campaigns/Discover/...)
    # and can land on "Discover" (browsable campaigns from this creator)
    # instead of "Campaigns" (the ones you've actually joined) even when we
    # navigated to a specific campaign's URL. If we don't see "Submit clip"
    # yet, try clicking the "Campaigns" nav item and re-reading.
    if "submit clip" not in body_text.lower():
        try:
            print("[whop_client debug] 'Submit clip' not found yet — trying the app's 'Campaigns' nav tab.")
            frame.get_by_text(re.compile(r"^Campaigns$", re.I)).first.click(timeout=5000)
            page.wait_for_timeout(2000)
            body_text = frame.inner_text("body")
            print(f"[whop_client debug] After clicking 'Campaigns': {len(body_text)} chars. First 300: {body_text[:300]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[whop_client debug] Could not click 'Campaigns' nav tab: {exc}")

    # IMPORTANT: "Submit clip" appears as an inline button on EVERY card in
    # a list view too, not just on a single campaign's detail page — so
    # its mere presence doesn't mean we're on the right page yet. Count
    # occurrences: more than one means we're still looking at a LIST of
    # campaigns and need to drill into the specific one via name_hint.
    submit_clip_count = len(re.findall(r"submit clip", body_text, re.I))
    looks_like_list = submit_clip_count > 1
    print(f"[whop_client debug] 'Submit clip' appears {submit_clip_count} time(s) — {'looks like a LIST view' if looks_like_list else 'looks like a single detail page'}.")

    if looks_like_list and name_hint:
        print(f"[whop_client debug] On a campaigns list — looking for '{name_hint}' to click into.")
        found_hint = name_hint.lower() in body_text.lower()
        for scroll_attempt in range(8):
            if found_hint:
                break
            try:
                frame.locator("body").evaluate("el => el.scrollBy(0, 600)")
            except Exception:  # noqa: BLE001
                break
            page.wait_for_timeout(600)
            body_text = frame.inner_text("body")
            if name_hint.lower() in body_text.lower():
                found_hint = True
                print(f"[whop_client debug] Found '{name_hint}' after {scroll_attempt + 1} scroll(s).")

        if not found_hint:
            print(
                f"[whop_client debug] Scrolled 8 times but never found '{name_hint}' in the "
                "campaigns list. It may not be joined under this app, or the name_hint is wrong."
            )
        else:
            try:
                card_title = frame.get_by_text(re.compile(re.escape(name_hint), re.I)).first
                card_title.click(timeout=5000, force=True)
                page.wait_for_timeout(2000)
                body_text = frame.inner_text("body")
                submit_clip_count = len(re.findall(r"submit clip", body_text, re.I))
                looks_like_list = submit_clip_count > 1
                print(
                    f"[whop_client debug] After clicking '{name_hint}' card: {len(body_text)} chars, "
                    f"'submit clip' x{submit_clip_count}. First 300: {body_text[:300]!r}"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[whop_client debug] Could not click the '{name_hint}' card: {exc}")

    # Still on a list (no name_hint given, or it didn't work)? Fall back to
    # clicking the first campaign-looking card/link as a last resort —
    # excluding accessibility helper links like "Skip to content" which
    # would otherwise match too (they're links with visible-ish text too).
    if looks_like_list or "submit clip" not in body_text.lower():
        try:
            print("[whop_client debug] Still no 'Submit clip' — trying to click the first campaign card in the list.")
            candidate = frame.get_by_role("link").filter(
                has_text=re.compile(r".{5,}")
            ).filter(
                has_not_text=re.compile(r"skip to content|help & support", re.I)
            ).first
            candidate.click(timeout=5000, force=True)
            page.wait_for_timeout(2000)
            body_text = frame.inner_text("body")
            print(f"[whop_client debug] After clicking first campaign card: {len(body_text)} chars. First 300: {body_text[:300]!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"[whop_client debug] Could not click into a campaign card: {exc}")

    if "not available in your region" in body_text.lower():
        return None

    # "$9.7K / $25K" style budget-used line — if it looks like \~100% used,
    # skip (no point submitting to an exhausted campaign).
    budget_match = re.search(r"\$([\d.]+)K?\s*/\s*\$([\d.]+)K", body_text)
    if budget_match:
        used = float(budget_match.group(1))
        total = float(budget_match.group(2))
        if total > 0 and used / total >= 0.999:
            return None

    # Campaign name: try the largest heading-like text near the top —
    # resilient fallback is just the page title.
    name = (page.title() or "").split("|")[0].strip() or "Whop campaign"
    try:
        heading = frame.get_by_role("heading").first.inner_text(timeout=2000)
        if heading and len(heading.strip()) > 3:
            name = heading.strip()
    except Exception:  # noqa: BLE001
        pass

    # Reference material link (e.g.
