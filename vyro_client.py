"""
vyro_client.py

Browser-automation client for Vyro (app.vyro.com). Vyro has no public API,
so this module drives a real (headless) Chromium browser with Playwright to:

  1. Reuse a saved login SESSION (cookie) to open Vyro already logged in
  2. Check the "Add Clips" / campaigns page for an active campaign
  3. Scrape that campaign's requirements text + the ~1 minute source clip
     Vyro gives you to download and edit
  4. After the video is rendered and uploaded to YouTube, reuse the same
     session to submit the resulting YouTube link into the campaign's form

================================================================================
WHY SESSION-COOKIE AUTH INSTEAD OF EMAIL/PASSWORD
================================================================================
Vyro's login is passwordless — it emails you a one-time code (OTP), so there
is no password to automate. Instead, this script reuses your browser's
LOGIN SESSION COOKIE (a cookie named `vyro_sid`), captured once manually.
That cookie proves you're logged in, the same way it does in your own
browser, without ever handling an OTP.

How to (re-)capture it, whenever the session expires:
  1. Log in to https://app.vyro.com in your phone browser (Kiwi Browser or
     any Chromium browser with DevTools) using the normal OTP flow.
  2. Open DevTools -> Application tab -> Storage -> Cookies -> app.vyro.com
  3. Tap the `vyro_sid` row and copy its FULL value (long-press -> Copy
     value; the table view truncates it).
  4. Put that value in the GitHub secret VYRO_SESSION_COOKIE (Settings ->
     Secrets and variables -> Actions -> update the secret).

This cookie is normally valid for weeks/months. When it finally expires,
check_for_campaign()/submit_video_link() will get redirected back to the
login page and raise a clear VyroSessionExpired error telling you to repeat
the steps above — you do NOT need to touch the code, just refresh the secret.

Also note: automating actions on a third-party platform may violate that
platform's Terms of Service. This automates your own account for your own
campaigns — the ToS risk is yours to accept, not something this script can
decide for you.
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

VYRO_HOME_URL = "https://app.vyro.com/"
VYRO_LOGIN_URL = "https://app.vyro.com/login"
# TODO: confirm this against the real "Add Clips" / campaigns page URL once
# you're looking at it logged in (it showed "Campaigns" and "Add Clips" tabs)
VYRO_CAMPAIGNS_URL = "https://app.vyro.com/add-clips"

DEFAULT_TIMEOUT_MS = 20000
SESSION_COOKIE_NAME = "vyro_sid"
SESSION_COOKIE_DOMAIN = os.environ.get("VYRO_SESSION_COOKIE_DOMAIN", ".vyro.com")


class VyroClientError(Exception):
    """Raised on any Vyro browser-automation failure."""


class VyroSessionExpired(VyroClientError):
    """Raised specifically when the saved session cookie no longer works."""


@dataclass
class VyroCampaign:
    campaign_id: str
    name: str
    requirements_text: str  # raw scraped requirement text -> requirements_parser.parse_campaign()
    source_clip_url: str    # the ~1 min clip Vyro gives you to download & edit
    submit_page_url: str    # URL of this campaign's submission page


def _fill_first_match(page: Page, candidates: list, value: str, what: str, timeout_ms: int = 4000) -> None:
    """Try a list of (locator-building lambda) candidates in order; use the
    first one that actually appears on the page. This avoids needing the
    exact CSS selector/class name — placeholder text and input type are
    usually stable even when Vyro's internal class names change."""
    last_exc: Exception | None = None
    for build_locator in candidates:
        try:
            locator = build_locator(page)
            locator.wait_for(state="visible", timeout=timeout_ms)
            locator.fill(value)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    raise VyroClientError(
        f"Could not find the {what} field — none of the known patterns "
        f"matched. Last error: {last_exc}"
    )


def _click_first_match(page: Page, candidates: list, what: str, optional: bool = False) -> bool:
    """Same as above but for buttons/links. If optional=True, returns False
    instead of raising when nothing matches."""
    last_exc: Exception | None = None
    for build_locator in candidates:
        try:
            locator = build_locator(page)
            locator.wait_for(state="visible", timeout=3000)
            locator.click()
            return True
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    if optional:
        return False
    raise VyroClientError(
        f"Could not find the {what} button — none of the known patterns "
        f"matched. Last error: {last_exc}"
    )


def _new_context_with_session(browser: Browser) -> BrowserContext:
    """Creates a browser context pre-loaded with the saved Vyro session cookie."""
    session_value = os.environ.get("VYRO_SESSION_COOKIE")
    if not session_value:
        raise VyroClientError(
            "VYRO_SESSION_COOKIE is not set. Log in to app.vyro.com manually, "
            f"copy the '{SESSION_COOKIE_NAME}' cookie's full value, and set it "
            "as the VYRO_SESSION_COOKIE GitHub secret."
        )

    context = browser.new_context()
    context.add_cookies(
        [
            {
                "name": SESSION_COOKIE_NAME,
                "value": session_value,
                "domain": SESSION_COOKIE_DOMAIN,
                "path": "/",
                "expires": time.time() + 60 * 60 * 24 * 180,  # far future; real expiry is server-side
                "httpOnly": True,
                "secure": True,
                "sameSite": "Lax",
            }
        ]
    )
    return context


def _ensure_logged_in(page: Page, target_url: str) -> None:
    """Navigates to target_url and confirms the session cookie actually
    logged us in (i.e. Vyro didn't bounce us back to /login)."""
    page.goto(target_url, timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

    if "/login" in page.url or "get-started" in page.url.lower():
        raise VyroSessionExpired(
            "Vyro session expired (got redirected to the login page). "
            "Log in manually in your browser again, copy the new "
            f"'{SESSION_COOKIE_NAME}' cookie value, and update the "
            "VYRO_SESSION_COOKIE GitHub secret."
        )


def check_for_campaign(page: Page) -> Optional[VyroCampaign]:
    """
    Visits the "Add Clips" / campaigns page and returns a VyroCampaign if an
    active (non-completed) campaign is available, otherwise None.
    """
    _ensure_logged_in(page, VYRO_CAMPAIGNS_URL)

    # TODO: replace with the real selector for an ACTIVE campaign card, once
    # a campaign actually appears on this page (nothing to inspect while
    # it's empty). Tip: right-click the card -> Inspect -> Copy -> Copy selector
    campaign_card = page.query_selector('[data-testid="active-campaign-card"]')
    if campaign_card is None:
        return None  # no active campaign right now — this is a NORMAL, expected result

    campaign_card.click()
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

    try:
        # TODO: replace with the real selectors on the campaign detail page
        name = (page.text_content('[data-testid="campaign-name"]') or "").strip() or "unknown"
        requirements_text = (page.text_content('[data-testid="campaign-requirements"]') or "").strip()
        source_clip_url = page.get_attribute('[data-testid="campaign-source-clip"] a', "href") or ""
        campaign_id = page.get_attribute('[data-testid="campaign-root"]', "data-campaign-id") or name
    except PlaywrightTimeoutError as exc:
        raise VyroClientError(
            "Found a campaign but couldn't read its details — update the "
            f"detail-page selectors in check_for_campaign(). Details: {exc}"
        ) from exc

    if not source_clip_url:
        raise VyroClientError(
            "Campaign found but no source-clip URL was scraped. Fix the "
            "'campaign-source-clip' selector in check_for_campaign()."
        )

    return VyroCampaign(
        campaign_id=str(campaign_id),
        name=name,
        requirements_text=requirements_text,
        source_clip_url=source_clip_url,
        submit_page_url=page.url,
    )


def submit_video_link(page: Page, campaign: VyroCampaign, video_url: str) -> None:
    """Navigates to the campaign's submission page and submits the YouTube link."""
    _ensure_logged_in(page, campaign.submit_page_url)

    try:
        _fill_first_match(
            page,
            [
                lambda p: p.get_by_placeholder(re.compile(r"link|url", re.I)),
                lambda p: p.locator('input[name="video_url"]'),
                lambda p: p.locator('input[type="url"]'),
            ],
            video_url,
            "video link",
        )
        _click_first_match(
            page,
            [
                lambda p: p.get_by_role("button", name=re.compile(r"submit", re.I)),
                lambda p: p.locator('button[type="submit"]'),
            ],
            "submit",
        )
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except VyroClientError:
        raise
    except PlaywrightTimeoutError as exc:
        raise VyroClientError(
            "Submission failed — update the submit-form selectors in "
            f"submit_video_link(). Details: {exc}"
        ) from exc


def run_check() -> Optional[VyroCampaign]:
    """One-shot: open Vyro with the saved session, check for a campaign, return it."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            return check_for_campaign(page)
        finally:
            browser.close()


def run_submit(campaign: VyroCampaign, video_url: str) -> None:
    """One-shot: open Vyro with the saved session and submit a video link."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            submit_video_link(page, campaign, video_url)
        finally:
            browser.close()


if __name__ == "__main__":
    # Quick manual test: VYRO_SESSION_COOKIE=... python vyro_client.py
    found = run_check()
    if found is None:
        print("No active campaign found.")
    else:
        print(f"Campaign: {found.campaign_id} — {found.name}")
        print(f"Requirements: {found.requirements_text}")
        print(f"Source clip: {found.source_clip_url}")
