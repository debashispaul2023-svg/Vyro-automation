"""
whop_client.py

Browser-automation client for Whop (whop.com), for the "Content Rewards"
clipping campaigns — same idea as vyro_client.py but for a second platform.

================================================================================
HOW AUTH WORKS HERE
================================================================================
You log in to Whop with Google (social login), which cannot be scripted —
Google actively blocks automated sign-ins. So instead of logging in, this
module replays your ENTIRE captured cookie jar from a real logged-in browser
session into a fresh Playwright context.

How to (re-)capture it, whenever the session expires:
  1. Log in to https://whop.com normally in your phone browser via Google
  2. Open DevTools -> Network tab, reload the page
  3. Tap any request going to whop.com -> Headers tab -> find the
     "Cookie:" line under Request Headers
  4. Select and copy the ENTIRE value of that line
  5. Set that whole string as the GitHub secret WHOP_COOKIE_HEADER
================================================================================
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)
from gmail_otp import wait_for_vyro_otp

WHOP_HOME_URL = "https://whop.com/"
WHOP_CAMPAIGNS_URL = "https://whop.com/discover"

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
    requirements_text: str
    source_clip_url: str
    submit_page_url: str


def _parse_cookie_header(raw: str) -> list[dict]:
    """Turns a raw 'name1=value1; name2=value2; ...' header string into the
    list-of-dicts format Playwright's context.add_cookies() expects."""
    cookies = []
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies.append(
            {
                "name": name.strip(),
                "value": value.strip(),
                "domain": COOKIE_DOMAIN,
                "path": "/",
            }
        )
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
            "didn't transfer to this server). This needs a different fix, not just a "
            "cookie refresh."
        )

    if "/login" in page.url or "/start" in page.url:
        raise WhopSessionExpired(
            "Whop session expired (redirected to login/start). Re-capture "
            "your cookies and update the WHOP_COOKIE_HEADER GitHub secret."
        )


def check_for_campaign(page: Page) -> Optional[WhopCampaign]:
    """
    Visits the Whop Discover page, clicks the first available campaign,
    handles auto-join & OTP if prompted, and scrapes requirements.
    """
    _ensure_logged_in(page, WHOP_CAMPAIGNS_URL)

    # Find and click any campaign card showing a budget or views metric
    cards = page.get_by_text(re.compile(r"per 1k views|budget", re.I))
    if cards.count() == 0:
        return None
        
    cards.first.click()
    _settle(page)

    # Auto-Join Flow
    join_btn = page.get_by_role("button", name=re.compile(r"join campaign", re.I))
    if join_btn.is_visible():
        join_btn.click()
        _settle(page)
        
        # Handle new Whop Email OTP prompt
        send_code_btn = page.get_by_role("button", name=re.compile(r"send code", re.I))
        if send_code_btn.is_visible(timeout=3000):
            login_ts = time.time()
            send_code_btn.click()
            
            print("Requested OTP from Whop. Waiting for email...")
            # Fetch OTP from Gmail (overriding hint to look for Whop emails)
            otp_code = wait_for_vyro_otp(after_epoch_seconds=login_ts, sender_hint="whop")
            print(f"OTP received! Entering code: {otp_code}")
            
            page.keyboard.type(otp_code)
            verify_btn = page.get_by_role("button", name=re.compile(r"verify|sign in", re.I))
            if verify_btn.is_visible():
                verify_btn.click()
            _settle(page)

    # Scrape Campaign Details
    name = page.locator("h1").first.text_content() or "Unknown Whop Campaign"
    
    req_locator = page.locator("p").filter(has_text=re.compile(r"download clips|focus on|dos|refer to", re.I))
    requirements_text = req_locator.text_content() if req_locator.count() > 0 else "Unknown requirements"
    
    # Extract Google Drive / External reference link
    source_clip_url = page.get_by_role("link", name=re.compile(r"drive\.google|docs\.google|external", re.I)).get_attribute("href")
    
    campaign_id = name.replace(" ", "_").lower()

    return WhopCampaign(
        campaign_id=campaign_id,
        name=name,
        requirements_text=requirements_text,
        source_clip_url=source_clip_url or "",
        submit_page_url=page.url,
    )


def submit_video_link(page: Page, campaign: WhopCampaign, video_url: str) -> None:
    """
    Handles the final submission modal for the Whop campaign.
    """
    _ensure_logged_in(page, campaign.submit_page_url)
    
    # Open submission modal
    submit_init_btn = page.get_by_role("button", name=re.compile(r"submit clip", re.I))
    submit_init_btn.click()
    _settle(page)

    # Paste YouTube video link
    url_input = page.get_by_placeholder(re.compile(r"tiktok\.com|youtube\.com|instagram\.com", re.I))
    url_input.fill(video_url)
    
    # Accept requirements checkbox
    checkbox = page.get_by_role("checkbox")
    if checkbox.is_visible():
        checkbox.check()
        
    # Final confirm button
    final_submit = page.get_by_role("button", name=re.compile(r"submit clip", re.I)).last
    final_submit.click()
    _settle(page)


def run_check() -> Optional[WhopCampaign]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            return check_for_campaign(page)
        finally:
            browser.close()


def run_submit(campaign: WhopCampaign, video_url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            submit_video_link(page, campaign, video_url)
        finally:
            browser.close()


if __name__ == "__main__":
    found = run_check()
    if found is None:
        print("No active campaign found.")
    else:
        print(f"Campaign: {found.campaign_id} — {found.name}")
        print(f"Requirements: {found.requirements_text}")
        print(f"Source clip: {found.source_clip_url}")
                "domain": COOKIE_DOMAIN,
                "path": "/",
            }
        )
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
    idle, so a networkidle timeout here is NOT treated as an error — we
    just proceed with whatever's on the page already."""
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

    if "/login" in page.url or "/start" in page.url:
        raise WhopSessionExpired(
            "Whop session expired (redirected to login/start). Re-capture "
            "your cookies (see module docstring) and update the "
            "WHOP_COOKIE_HEADER GitHub secret."
        )


def check_for_campaign(page: Page) -> Optional[WhopCampaign]:
    """
    Visits the Whop campaigns/content-rewards page and returns a
    WhopCampaign if an active one is available, otherwise None.
    """
    _ensure_logged_in(page, WHOP_CAMPAIGNS_URL)

    # TODO: placeholder — fill in once an actual campaign card is visible to
    # inspect. Same pattern as vyro_client.py's check_for_campaign().
    campaign_card = page.query_selector('[data-testid="content-rewards-campaign-card"]')
    if campaign_card is None:
        return None

    campaign_card.click()
    _settle(page)

    try:
        name = (page.text_content('[data-testid="campaign-name"]') or "").strip() or "unknown"
        requirements_text = (page.text_content('[data-testid="campaign-requirements"]') or "").strip()
        source_clip_url = page.get_attribute('[data-testid="campaign-source-clip"] a', "href") or ""
        campaign_id = page.get_attribute('[data-testid="campaign-root"]', "data-campaign-id") or name
    except PlaywrightTimeoutError as exc:
        raise WhopClientError(
            "Found a campaign but couldn't read its details — update the "
            f"detail-page selectors in check_for_campaign(). Details: {exc}"
        ) from exc

    if not source_clip_url:
        raise WhopClientError(
            "Campaign found but no source-clip URL was scraped. Fix the "
            "'campaign-source-clip' selector in check_for_campaign()."
        )

    return WhopCampaign(
        campaign_id=str(campaign_id),
        name=name,
        requirements_text=requirements_text,
        source_clip_url=source_clip_url,
        submit_page_url=page.url,
    )


def submit_video_link(page: Page, campaign: WhopCampaign, video_url: str) -> None:
    _ensure_logged_in(page, campaign.submit_page_url)

    try:
        filled = False
        for build_locator in [
            lambda p: p.get_by_placeholder(re.compile(r"link|url", re.I)),
            lambda p: p.locator('input[name="video_url"]'),
            lambda p: p.locator('input[type="url"]'),
        ]:
            try:
                locator = build_locator(page)
                locator.wait_for(state="visible", timeout=4000)
                locator.fill(video_url)
                filled = True
                break
            except Exception:  # noqa: BLE001
                continue
        if not filled:
            raise WhopClientError("Could not find the video-link input field on the submission form.")

        clicked = False
        for build_locator in [
            lambda p: p.get_by_role("button", name=re.compile(r"submit", re.I)),
            lambda p: p.locator('button[type="submit"]'),
        ]:
            try:
                locator = build_locator(page)
                locator.wait_for(state="visible", timeout=3000)
                locator.click()
                clicked = True
                break
            except Exception:  # noqa: BLE001
                continue
        if not clicked:
            raise WhopClientError("Could not find the submit button on the submission form.")

        _settle(page)
    except WhopClientError:
        raise
    except PlaywrightTimeoutError as exc:
        raise WhopClientError(f"Submission failed. Details: {exc}") from exc


def run_check() -> Optional[WhopCampaign]:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            return check_for_campaign(page)
        finally:
            browser.close()


def run_submit(campaign: WhopCampaign, video_url: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            submit_video_link(page, campaign, video_url)
        finally:
            browser.close()


if __name__ == "__main__":
    found = run_check()
    if found is None:
        print("No active campaign found.")
    else:
        print(f"Campaign: {found.campaign_id} — {found.name}")
        print(f"Requirements: {found.requirements_text}")
        print(f"Source clip: {found.source_clip_url}")
