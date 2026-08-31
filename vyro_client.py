"""
vyro_client.py

Browser-automation client for Vyro (app.vyro.com). Vyro has no public API,
so this module drives a real (headless) Chromium browser with Playwright to:

  1. Log in to Vyro with your account credentials
  2. Check the "Add Clips" / campaigns page for an active campaign
  3. Scrape that campaign's requirements text + the ~1 minute source clip
     Vyro gives you to download and edit
  4. After the video is rendered and uploaded to YouTube, log back in and
     submit the resulting YouTube link into the campaign's submission form

================================================================================
⚠️  READ THIS BEFORE RUNNING  ⚠️
================================================================================
Every selector marked "TODO" below is a PLACEHOLDER. I do not have access to
your logged-in Vyro dashboard, so I cannot know the exact HTML structure of
app.vyro.com's login form, campaign cards, or submission form. You must fill
these in yourself, once, using your browser's "Inspect Element" tool — the
step-by-step guide is in README.md under "ধাপ ০: Vyro সিলেক্টর বের করা".

Until you do this, the script will fail loudly with a clear error telling you
exactly which selector didn't match — it will NOT silently do the wrong thing.

Also note: automating login/form-submission on a third-party platform may
violate that platform's Terms of Service. This automates your own account
for your own campaigns — the ToS risk is yours to accept, not something this
script can decide for you. Check Vyro's ToS before relying on this for real
payouts.
================================================================================
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

# TODO: confirm these two URLs against your actual browser address bar
VYRO_LOGIN_URL = "https://app.vyro.com/login"
VYRO_CAMPAIGNS_URL = "https://app.vyro.com/add-clips"

DEFAULT_TIMEOUT_MS = 20000


class VyroClientError(Exception):
    """Raised on any Vyro browser-automation failure."""


@dataclass
class VyroCampaign:
    campaign_id: str
    name: str
    requirements_text: str  # raw scraped requirement text -> requirements_parser.parse_campaign()
    source_clip_url: str    # the ~1 min clip Vyro gives you to download & edit
    submit_page_url: str    # URL of this campaign's submission page


def _fill_first_match(page: Page, candidates: list, value: str, what: str) -> None:
    """Try a list of (locator-building lambda) candidates in order; use the
    first one that actually appears on the page. This avoids needing the
    exact CSS selector/class name — placeholder text and input type are
    usually stable even when Vyro's internal class names change."""
    last_exc: Exception | None = None
    for build_locator in candidates:
        try:
            locator = build_locator(page)
            locator.wait_for(state="visible", timeout=4000)
            locator.fill(value)
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    raise VyroClientError(
        f"Could not find the {what} field — none of the known patterns "
        f"matched. Last error: {last_exc}"
    )


def _click_first_match(page: Page, candidates: list, what: str) -> None:
    last_exc: Exception | None = None
    for build_locator in candidates:
        try:
            locator = build_locator(page)
            locator.wait_for(state="visible", timeout=4000)
            locator.click()
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
    raise VyroClientError(
        f"Could not find the {what} button — none of the known patterns "
        f"matched. Last error: {last_exc}"
    )


def _login(page: Page, email: str, password: str) -> None:
    page.goto(VYRO_LOGIN_URL, timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

    try:
        # Tries several common patterns in order — placeholder text, input
        # type, and name attribute — so this keeps working even without
        # knowing Vyro's exact internal CSS class names.
        _fill_first_match(
            page,
            [
                lambda p: p.get_by_placeholder("Email", exact=False),
                lambda p: p.locator('input[type="email"]'),
                lambda p: p.locator('input[name="email"]'),
            ],
            email,
            "email",
        )
        _fill_first_match(
            page,
            [
                lambda p: p.get_by_placeholder("Password", exact=False),
                lambda p: p.locator('input[type="password"]'),
                lambda p: p.locator('input[name="password"]'),
            ],
            password,
            "password",
        )
        _click_first_match(
            page,
            [
                lambda p: p.get_by_role("button", name=re.compile(r"log\s*in|sign\s*in", re.I)),
                lambda p: p.locator('button[type="submit"]'),
                lambda p: p.get_by_text(re.compile(r"log\s*in|sign\s*in", re.I)),
            ],
            "log in / sign in",
        )
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except VyroClientError:
        raise
    except PlaywrightTimeoutError as exc:
        raise VyroClientError(f"Login failed — timed out. Details: {exc}") from exc

    if "login" in page.url:
        raise VyroClientError(
            "Still on the login page after submitting — check VYRO_EMAIL / "
            "VYRO_PASSWORD secrets, or the login selectors."
        )


def check_for_campaign(page: Page) -> Optional[VyroCampaign]:
    """
    Visits the "Add Clips" / campaigns page and returns a VyroCampaign if an
    active (non-completed) campaign is available, otherwise None.
    """
    page.goto(VYRO_CAMPAIGNS_URL, timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

    # TODO: replace with the real selector for an ACTIVE campaign card.
    # Tip: right-click an active campaign card -> Inspect -> right-click the
    # highlighted <div> in DevTools -> Copy -> Copy selector
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
    page.goto(campaign.submit_page_url, timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    try:
        # TODO: replace with the real submission-form selectors
        page.fill('input[name="video_url"]', video_url, timeout=DEFAULT_TIMEOUT_MS)
        page.click('button:has-text("Submit")', timeout=DEFAULT_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise VyroClientError(
            "Submission failed — update the submit-form selectors in "
            f"submit_video_link(). Details: {exc}"
        ) from exc


def run_check() -> Optional[VyroCampaign]:
    """One-shot: log in, check for a campaign, return it. Browser closes after."""
    email = os.environ["VYRO_EMAIL"]
    password = os.environ["VYRO_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page, email, password)
            return check_for_campaign(page)
        finally:
            browser.close()


def run_submit(campaign: VyroCampaign, video_url: str) -> None:
    """One-shot: log in and submit a video link to a specific campaign."""
    email = os.environ["VYRO_EMAIL"]
    password = os.environ["VYRO_PASSWORD"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            _login(page, email, password)
            submit_video_link(page, campaign, video_url)
        finally:
            browser.close()


if __name__ == "__main__":
    # Quick manual test: python vyro_client.py
    # (needs VYRO_EMAIL / VYRO_PASSWORD set in your environment)
    found = run_check()
    if found is None:
        print("No active campaign found.")
    else:
        print(f"Campaign: {found.campaign_id} — {found.name}")
        print(f"Requirements: {found.requirements_text}")
        print(f"Source clip: {found.source_clip_url}")
