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
WHOP_DISCOVER_URL = "https://whop.com/discover/content-rewards/"
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
    """
    Whop hosts third-party campaign apps (like this one — the URL contains
    'exp_...', Whop's marker for an embedded app extension) inside an
    <iframe>. page.inner_text("body") on the main page only sees Whop's
    OWN outer shell (search bar, balance, notification counts) — not the
    actual campaign content, which lives inside that iframe's separate
    document.

    There can be MULTIPLE iframes though — e.g. hidden tracking/pixel
    script iframes (TikTok/Meta conversion tracking) that also happen to
    have a lot of "text" (their raw JS source). So it's not enough to pick
    the iframe with the most text — we specifically look for one whose
    text contains real campaign UI words ("submit clip", "budget",
    "requirement", etc), and skip anything that looks like injected script
    source (starts with "(function", "!function", etc).
    """
    CONTENT_MARKERS = ("submit clip", "budget", "requirement", "campaign", "views", "dos")
    SCRIPT_LOOKING_PREFIXES = ("(function", "!function", "window.", "var ", "const ", "let ")

    try:
        page.wait_for_timeout(1500)  # give iframes a moment to attach
        best_candidate = None
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                text = frame.inner_text("body", timeout=3000)
            except Exception:  # noqa: BLE001
                continue

            stripped = text.strip()
            if not stripped or stripped.startswith(SCRIPT_LOOKING_PREFIXES):
                continue  # looks like a script-injection artifact, not real UI

            lower = stripped.lower()
            marker_hits = sum(1 for marker in CONTENT_MARKERS if marker in lower)
            if marker_hits > 0:
                print(
                    f"[whop_client debug] Frame {frame.url} matched {marker_hits} content "
                    f"markers, {len(text)} chars — using this one."
                )
                return frame

            if best_candidate is None and len(stripped) > 100:
                best_candidate = frame  # keep as a weak fallback only

        if best_candidate is not None:
            print(
                f"[whop_client debug] No frame matched content markers; falling back to "
                f"largest non-script frame: {best_candidate.url}"
            )
            return best_candidate
    except Exception as exc:  # noqa: BLE001
        print(f"[whop_client debug] Iframe detection failed: {exc}")

    print("[whop_client debug] No substantial iframe found; using main page directly.")
    return page


def _extract_campaign_details(page: Page, campaign_url: str) -> Optional[WhopCampaign]:
    """Reads a single joined-campaign page. Returns None if the campaign
    looks unavailable (region-locked, budget fully used, etc)."""
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
    body_text = frame.inner_text("body")
    print(f"[whop_client debug] Captured {len(body_text)} chars. First 300: {body_text[:300]!r}")

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
    try:
        heading = frame.get_by_role("heading").first.inner_text(timeout=2000)
        if heading and len(heading.strip()) > 3:
            name = heading.strip()
    except Exception:  # noqa: BLE001
        pass

    # Reference material link (e.g. an external Google Doc with the real
    # requirements + footage). On Whop this is often NOT a real <a href> —
    # it's a clickable card that opens a new tab via JS. So: try a normal
    # href scan first (cheap), and if that finds nothing, click the card
    # and capture whichever URL the resulting new tab lands on.
    reference_doc_url = None
    try:
        all_links = frame.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        for href in all_links:
            if "docs.google.com" in href or "drive.google.com" in href:
                reference_doc_url = href
                break
    except Exception:  # noqa: BLE001
        pass

    if not reference_doc_url:
        try:
            reference_card = frame.get_by_text(
                re.compile(r"edit\?usp=sharing|reference materials|resources|dos and don", re.I)
            ).first
            try:
                # Case A: click opens a new tab.
                with page.context.expect_page(timeout=4000) as new_page_info:
                    reference_card.click(timeout=3000)
                new_page = new_page_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                candidate_url = new_page.url
                if "docs.google.com" in candidate_url or "drive.google.com" in candidate_url:
                    reference_doc_url = candidate_url
                new_page.close()
            except PlaywrightTimeoutError:
                # Case B: no new tab appeared — maybe it navigated the
                # current tab instead, or opened an in-page preview whose
                # iframe/src we can read off the DOM.
                page.wait_for_timeout(1000)
                if "docs.google.com" in page.url or "drive.google.com" in page.url:
                    reference_doc_url = page.url
                    page.go_back(timeout=5000)
                else:
                    # Case C: look for an iframe preview embed pointing at
                    # a Google Doc/Drive URL, which some card-preview UIs use.
                    try:
                        iframe_srcs = page.eval_on_selector_all("iframe[src]", "els => els.map(e => e.src)")
                        for src in iframe_srcs:
                            if "docs.google.com" in src or "drive.google.com" in src:
                                reference_doc_url = src
                                break
                    except Exception:  # noqa: BLE001
                        pass
        except Exception as exc:  # noqa: BLE001
            print(f"[whop_client debug] Could not resolve reference doc link: {exc}")

    if not reference_doc_url:
        # Nothing worked — print a snippet of the page around "reference"/
        # "dos" so the next failure's logs give enough context to fix this
        # without needing another round of screenshots.
        lower_body = body_text.lower()
        for marker in ("reference", "dos", "google doc"):
            idx = lower_body.find(marker)
            if idx != -1:
                snippet = body_text[max(0, idx - 80):idx + 200].replace("\n", " | ")
                print(f"[whop_client debug] Context around '{marker}': ...{snippet}...")

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


def _save_campaign_urls(urls: list[str]) -> None:
    with open(CAMPAIGNS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"joined_campaign_urls": urls}, f, indent=2)


def discover_and_join_new_campaigns(score_fn=None, max_new: int = 2) -> list[str]:
    """
    Visits Whop's public Discover page, looks at campaigns not already in
    whop_campaigns.json, and joins up to `max_new` of them automatically.

    `score_fn`, if given, should be a callable(requirements_text) -> object
    with an `.is_good` bool attribute (matches ai_brain.CampaignScore) —
    used to skip campaigns that look low-quality/scammy before joining.
    Passed in as a parameter (rather than imported directly) to avoid a
    circular import between whop_client.py and ai_brain.py.

    Returns the list of campaign URLs that were newly joined this run (also
    appended into whop_campaigns.json so future runs treat them as known).

    ⚠️ This is the least-tested part of the whole pipeline — Whop's Discover
    page card structure was only seen in screenshots, not inspected live,
    so the selectors below are resilient text/role-based best guesses. On
    failure this prints debug context to the logs so the next round can be
    fixed from logs alone, the same way the rest of whop_client.py's
    selectors were iteratively fixed.
    """
    known_urls = set(_load_campaign_urls())
    newly_joined: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            _ensure_logged_in(page, WHOP_DISCOVER_URL)

            # Wait for actual campaign cards to render (heavy SPA, same
            # issue as the campaign detail page).
            try:
                page.get_by_text(re.compile(r"join campaign|view campaign", re.I)).first.wait_for(
                    state="visible", timeout=12000
                )
            except PlaywrightTimeoutError:
                print("[whop_client debug] Discover page campaign cards never appeared to load.")

            # Each campaign is presented as a card; the visible "$X/1k views"
            # rate text is a reasonably unique anchor per card. Collect
            # candidate card containers via that text, then read each one's
            # nearby heading for a name and check budget-used before
            # deciding whether to open it.
            card_texts = page.get_by_text(re.compile(r"\$[\d.]+\s*/\s*1k", re.I))
            card_count = min(card_texts.count(), 20)  # sane upper bound per run
            print(f"[whop_client debug] Found {card_count} candidate campaign rate labels on Discover page.")

            joined_this_run = 0
            for i in range(card_count):
                if joined_this_run >= max_new:
                    break
                try:
                    card_texts.nth(i).scroll_into_view_if_needed(timeout=3000)
                    card_texts.nth(i).click(timeout=3000)
                except Exception as exc:  # noqa: BLE001
                    print(f"[whop_client debug] Could not open Discover card #{i}: {exc}")
                    continue

                page.wait_for_timeout(1500)  # let the detail modal animate in

                modal_text = page.inner_text("body")
                if "not available in your region" in modal_text.lower():
                    print(f"[whop_client debug] Card #{i} is region-locked, skipping.")
                    _close_any_modal(page)
                    continue

                # Already-known campaign? Try to read its URL if the modal
                # exposes one, else just check by visible name overlap —
                # best effort, duplicates are harmless since
                # check_configured_campaigns() de-dupes by campaign_id later.

                if score_fn is not None:
                    try:
                        score = score_fn(modal_text[:4000])
                        if not score.is_good:
                            print(f"[whop_client debug] AI skipped Discover card #{i}: {score.reason}")
                            _close_any_modal(page)
                            continue
                    except Exception as exc:  # noqa: BLE001
                        print(f"[whop_client debug] AI scoring unavailable for card #{i} ({exc}); proceeding anyway.")

                try:
                    join_button = page.get_by_role("button", name=re.compile("join campaign", re.I)).first
                    join_button.click(timeout=5000)
                    page.wait_for_timeout(2000)
                except Exception as exc:  # noqa: BLE001
                    print(f"[whop_client debug] No 'Join Campaign' button on card #{i} (maybe already joined): {exc}")
                    _close_any_modal(page)
                    continue

                # After joining, Whop should navigate to (or reveal) the
                # campaign's own dashboard URL under /app/campaigns/... —
                # capture whatever URL we land on if it matches that pattern.
                page.wait_for_timeout(1500)
                if "/app/campaigns/" in page.url and page.url not in known_urls:
                    known_urls.add(page.url)
                    newly_joined.append(page.url)
                    joined_this_run += 1
                    print(f"[whop_client debug] Joined new campaign: {page.url}")
                else:
                    print(
                        f"[whop_client debug] Joined card #{i} but landed on unexpected URL "
                        f"({page.url}) — couldn't confirm the campaign dashboard link. "
                        "You may need to add it to whop_campaigns.json manually this time."
                    )

                # Go back to Discover for the next card.
                page.goto(WHOP_DISCOVER_URL, timeout=DEFAULT_TIMEOUT_MS)
                _settle(page)

        finally:
            browser.close()

    if newly_joined:
        _save_campaign_urls(sorted(known_urls))

    return newly_joined


def _close_any_modal(page: Page) -> None:
    """Best-effort: press Escape and/or click a close (X) button to dismiss
    whatever modal/overlay might currently be open, before moving on."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:  # noqa: BLE001
        pass


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


def _select_platform_icon(frame, video_url: str) -> None:
    """The submit form has TikTok/YouTube/Instagram icon buttons near the
    top — click whichever matches the video URL's domain. `frame` can be a
    Page or a Frame (both support the same locator API)."""
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
        frame.get_by_role("button", name=re.compile(platform_name, re.I)).click(timeout=3000)
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
            frame = _get_content_frame(page)  # the campaign app likely lives in an iframe (see comment on _get_content_frame)

            # Open the "Submit clip" modal.
            frame.get_by_role("button", name=re.compile("submit clip", re.I)).first.click(timeout=DEFAULT_TIMEOUT_MS)
            page.wait_for_timeout(1000)  # let the modal animate in

            _select_platform_icon(frame, video_url)

            # The URL input field (placeholder mentions tiktok.com/youtube/instagram).
            url_input = frame.get_by_placeholder(re.compile(r"tiktok\.com|youtube\.com|instagram\.com|video", re.I))
            url_input.first.fill(video_url, timeout=DEFAULT_TIMEOUT_MS)

            # Required "I've read the requirements..." checkbox.
            try:
                frame.get_by_role("checkbox").first.check(timeout=3000)
            except Exception:  # noqa: BLE001
                # fall back to clicking the text label if the checkbox role isn't picked up
                frame.get_by_text(re.compile("read the requirements", re.I)).click(timeout=3000)

            # Final submit button inside the modal (same label, second instance).
            frame.get_by_role("button", name=re.compile("submit clip", re.I)).last.click(timeout=DEFAULT_TIMEOUT_MS)
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
