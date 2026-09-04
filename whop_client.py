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


def _load_campaign_urls() -> list[str]:
    if not os.path.isfile(CAMPAIGNS_CONFIG_PATH):
        return []
    with open(CAMPAIGNS_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return list(data.get("joined_campaign_urls", []))


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
        page.wait_for_load_state("networkidle", timeout=4000)
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


def _extract_campaign_details(page: Page, campaign_url: str) -> Optional[WhopCampaign]:
    """Reads a single joined-campaign page. Returns None if the campaign
    looks unavailable (region-locked, budget fully used, etc)."""
    body_text = page.inner_text("body")

    if "not available in your region" in body_text.lower():
        return None

    # "$9.7K / $25K" style budget-used line — if it looks like ~100% used,
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

    # Reference material link (e.g. an external Google Doc with the real
    # requirements + footage). Look for a link near the words "Reference"
    # or "Dos" — resilient text-based search rather than a guessed CSS class.
    reference_doc_url = None
    try:
        link_locator = page.get_by_role("link").filter(has_text=re.compile(r"edit\?usp=sharing|docs\.google|drive\.google", re.I))
        if link_locator.count() > 0:
            reference_doc_url = link_locator.first.get_attribute("href")
    except Exception:  # noqa: BLE001
        pass

    if not reference_doc_url:
        # Fall back to scanning all links on the page for a Google Docs/Drive URL.
        try:
            all_links = page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
            for href in all_links:
                if "docs.google.com" in href or "drive.google.com" in href:
                    reference_doc_url = href
                    break
        except Exception:  # noqa: BLE001
            pass

    # Which platforms this campaign accepts (icons shown near the submit
    # button / in the submit form: TikTok, YouTube, Instagram).
    platforms = []
    for platform in ("tiktok", "youtube", "instagram"):
        if platform in body_text.lower():
            platforms.append(platform)

    campaign_id = campaign_url.rstrip("/").split("/")[-1]

    return WhopCampaign(
        campaign_id=campaign_id,
        name=name,
        requirements_text=body_text[:4000],  # cap length; AI parser handles noisy text fine
        source_clip_url="",  # resolved later via reference_doc_url if empty
        submit_page_url=campaign_url,
        reference_doc_url=reference_doc_url,
        platforms=platforms or ["youtube"],
    )


def check_configured_campaigns() -> Optional[WhopCampaign]:
    """Checks every campaign URL listed in whop_campaigns.json and returns
    the first usable one found (not region-locked, not budget-exhausted).
    Returns None if none are usable right now."""
    urls = _load_campaign_urls()
    if not urls:
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            for url in urls:
                _ensure_logged_in(page, url)
                campaign = _extract_campaign_details(page, url)
                if campaign is not None:
                    return campaign
            return None
        finally:
            browser.close()


def _select_platform_icon(page: Page, video_url: str) -> None:
    """The submit form has TikTok/YouTube/Instagram icon buttons near the
    top — click whichever matches the video URL's domain."""
    lower = video_url.lower()
    if "tiktok.com" in lower:
        platform_name = "tiktok"
    elif "youtube.com" in lower or "youtu.be" in lower:
        platform_name = "youtube"
    elif "instagram.com" in lower:
        platform_name = "instagram"
    else:
        return  # unknown platform, let Whop auto-detect if it can

    try:
        page.get_by_role("button", name=re.compile(platform_name, re.I)).click(timeout=3000)
    except Exception:  # noqa: BLE001
        pass  # not critical — Whop may auto-detect the platform from the URL


def submit_video_link(campaign: WhopCampaign, video_url: str) -> None:
    """Opens the campaign page, opens the 'Submit clip' form, fills in the
    video link, checks the required confirmation checkbox, and submits."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            _ensure_logged_in(page, campaign.submit_page_url)

            # Open the "Submit clip" modal.
            page.get_by_role("button", name=re.compile("submit clip", re.I)).first.click(timeout=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(1000)  # let the modal animate in

            _select_platform_icon(page, video_url)

            # The URL input field (placeholder mentions tiktok.com/youtube/instagram).
            url_input = page.get_by_placeholder(re.compile(r"tiktok\.com|youtube\.com|instagram\.com|video", re.I))
            url_input.first.fill(video_url, timeout=DEFAULT_TIMEOUT_MS)

            # Required "I've read the requirements..." checkbox.
            try:
                page.get_by_role("checkbox").first.check(timeout=3000)
            except Exception:  # noqa: BLE001
                # fall back to clicking the text label if the checkbox role isn't picked up
                page.get_by_text(re.compile("read the requirements", re.I)).click(timeout=3000)

            # Final submit button inside the modal (same label, second instance).
            page.get_by_role("button", name=re.compile("submit clip", re.I)).last.click(timeout=DEFAULT_TIMEOUT_MS)
            _settle(page)
        except PlaywrightTimeoutError as exc:
            raise WhopClientError(f"Submission failed — a step timed out. Details: {exc}") from exc
        finally:
            browser.close()


if __name__ == "__main__":
    found = check_configured_campaigns()
    if found is None:
        print("No usable configured campaign found.")
    else:
        print(f"Campaign: {found.campaign_id} — {found.name}")
        print(f"Platforms: {found.platforms}")
        print(f"Reference doc: {found.reference_doc_url}")
