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
session into a fresh Playwright context (Whop's session isn't one single
cookie — it's several: whop-core.access-token, whop-core.user-id,
whop-core.refresh-token, cf_clearance, and others, all working together).

How to (re-)capture it, whenever the session expires:
  1. Log in to https://whop.com normally in your phone browser via Google
  2. Open DevTools -> Network tab, reload the page
  3. Tap any request going to whop.com -> Headers tab -> find the
     "Cookie:" line under Request Headers
  4. Select and copy the ENTIRE value of that line (it's long — all your
     Whop cookies semicolon-separated in one string)
  5. Set that whole string as the GitHub secret WHOP_COOKIE_HEADER

⚠️ Known risk: one of these cookies, `cf_clearance`, is Cloudflare's
bot-check pass-token and is often tied to the IP/browser fingerprint that
earned it. It may NOT work when replayed from a GitHub Actions runner (a
different IP). If check_for_campaign()/submit routines fail specifically at
the first page load — a Cloudflare "Just a moment..." challenge page,
not a redirect to login — that's what's happening. There's no simple fix
from inside this script for that case; it would need a residential proxy
or a different automation strategy. Try it first; this note just explains
that failure mode in advance so it's not a mystery if it happens.

Whop's dashboard structure for campaigns is not yet mapped out (no active
campaign was available to inspect while building this), so
check_for_campaign()/submit_video_link() below have TODO placeholder
selectors, same as vyro_client.py did initially. Send screenshots of the
"Content Rewards" / campaigns dashboard once something is visible there,
and these get filled in.
================================================================================
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

WHOP_HOME_URL = "https://whop.com/"
# TODO: confirm the real URL once you have an active campaign to look at.
# Likely candidates based on Whop's site structure: whop.com/hub,
# whop.com/discover?category=content-rewards, or a specific joined-whop's
# own "content rewards" tab. Update once known.
WHOP_CAMPAIGNS_URL = "https://whop.com/hub"

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


def _ensure_logged_in(page: Page, target_url: str) -> None:
    page.goto(target_url, timeout=DEFAULT_TIMEOUT_MS)
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

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
    page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)

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

        page.wait_for_load_state("networkidle", timeout=DEFAULT_TIMEOUT_MS)
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
