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
    requirements_text: str
    source_clip_url: str
    submit_page_url: str
    reference_doc_url: Optional[str] = None
    platforms: list[str] = field(default_factory=list)


def _load_configured_campaigns() -> list[dict]:
    if not os.path.isfile(CAMPAIGNS_CONFIG_PATH):
        return []
    with open(CAMPAIGNS_CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if "joined_campaigns" in data:
        return list(data["joined_campaigns"])

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
            except Exception:
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
    except Exception as exc:
        print(f"[whop_client debug] Iframe detection failed: {exc}")

    print("[whop_client debug] No substantial iframe found; using main page directly.")
    return page


def _extract_campaign_details(page: Page, campaign_url: str, name_hint: str = "") -> Optional[WhopCampaign]:
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
    if uuid_match:
        cid = uuid_match.group(1)
        app_frame = None
        for f in page.frames:
            if "apps.whop.com" in (f.url or ""):
                app_frame = f
                break
        if app_frame is not None:
            origin = (app_frame.url or "").split("/discover")[0].split("/campaigns")[0]
            target = f"{origin}/campaigns/{cid}"
            print(f"[whop_client debug] Opening campaign inside app iframe: {target}")
            try:
                app_frame.goto(target, timeout=20000)
                page.wait_for_timeout(2500)
                frame = _get_content_frame(page)
            except Exception as exc:
                print(f"[whop_client debug] Iframe goto failed: {exc}")
    body_text = frame.inner_text("body")
    print(f"[whop_client debug] Captured {len(body_text)} chars. First 300: {body_text[:300]!r}")

    if "submit clip" not in body_text.lower():
        try:
            print("[whop_client debug] 'Submit clip' not found yet — trying the app's 'Campaigns' nav tab.")
            frame.get_by_text(re.compile(r"^Campaigns$", re.I)).first.click(timeout=5000)
            page.wait_for_timeout(2000)
            body_text = frame.inner_text("body")
            print(f"[whop_client debug] After clicking 'Campaigns': {len(body_text)} chars. First 300: {body_text[:300]!r}")
        except Exception as exc:
            print(f"[whop_client debug] Could not click 'Campaigns' nav tab: {exc}")

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
            except Exception:
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
            except Exception as exc:
                print(f"[whop_client debug] Could not click the '{name_hint}' card: {exc}")

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
        except Exception as exc:
            print(f"[whop_client debug] Could not click into a campaign card: {exc}")

    if "not available in your region" in body_text.lower():
        return None

    budget_match = re.search(r"\$([\d.]+)K?\s*/\s*\$([\d.]+)K", body_text)
    if budget_match:
        used = float(budget_match.group(1))
        total = float(budget_match.group(2))
        if total > 0 and used / total >= 0.999:
            return None

    name = (page.title() or "").split("|")[0].strip() or "Whop campaign"
    try:
        heading = frame.get_by_role("heading").first.inner_text(timeout=2000)
        if heading and len(heading.strip()) > 3:
            name = heading.strip()
    except Exception:
        pass

    reference_doc_url = None
    try:
        all_links = frame.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        for href in all_links:
            if "docs.google.com" in href or "drive.google.com" in href:
                reference_doc_url = href
                break
    except Exception:
        pass

    if not reference_doc_url:
        try:
            reference_card = frame.get_by_text(
                re.compile(r"edit\?usp=sharing|reference materials|resources|dos and don", re.I)
            ).first
            try:
                with page.context.expect_page(timeout=4000) as new_page_info:
                    reference_card.click(timeout=3000)
                new_page = new_page_info.value
                new_page.wait_for_load_state("domcontentloaded", timeout=8000)
                candidate_url = new_page.url
                if "docs.google.com" in candidate_url or "drive.google.com" in candidate_url:
                    reference_doc_url = candidate_url
                new_page.close()
            except PlaywrightTimeoutError:
                page.wait_for_timeout(1000)
                if "docs.google.com" in page.url or "drive.google.com" in page.url:
                    reference_doc_url = page.url
                    page.go_back(timeout=5000)
                else:
                    try:
                        iframe_srcs = page.eval_on_selector_all("iframe[src]", "els => els.map(e => e.src)")
                        for src in iframe_srcs:
                            if "docs.google.com" in src or "drive.google.com" in src:
                                reference_doc_url = src
                                break
                    except Exception:
                        pass
        except Exception as exc:
            print(f"[whop_client debug] Could not resolve reference doc link: {exc}")

    if not reference_doc_url:
        lower_body = body_text.lower()
        for marker in ("reference", "dos", "google doc"):
            idx = lower_body.find(marker)
            if idx != -1:
                snippet = body_text[max(0, idx - 80):idx + 200].replace("\n", " | ")
                print(f"[whop_client debug] Context around '{marker}': ...{snippet}...")

    platforms = []
    for platform in ("tiktok", "youtube", "instagram"):
        if platform in body_text.lower():
            platforms.append(platform)

    campaign_id = campaign_url.rstrip("/").split("/")[-1]

    return WhopCampaign(
        campaign_id=campaign_id,
        name=name,
        requirements_text=body_text[:4000],
        source_clip_url="",
        submit_page_url=campaign_url,
        reference_doc_url=reference_doc_url,
        platforms=platforms or ["youtube"],
    )


def _save_new_campaign_urls(new_urls: list[str]) -> None:
    configured = _load_configured_campaigns()
    existing_urls = {entry["url"] for entry in configured}
    for url in new_urls:
        if url not in existing_urls:
            configured.append({"url": url, "name_hint": ""})
    with open(CAMPAIGNS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"joined_campaigns": configured}, f, indent=2)


def _normalize_campaign_url(url: str) -> str:
    """apps.whop.com campaign URL -> stable experiences URL we store."""
    exp = re.search(r"(exp_[A-Za-z0-9]+)", url or "")
    cid = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        url or "",
        re.I,
    )
    if exp and cid:
        return f"https://whop.com/experiences/{exp.group(1)}/campaigns/{cid.group(1)}"
    return url or ""


def _discover_root(page: Page):
    """Discover content lives in the apps.whop.com iframe, not the shell."""
    frame = _wait_for_app_frame(page, timeout_ms=20000)
    if frame is None:
        frame = _get_content_frame(page)
    print(f"[whop_client debug] Discover root url={getattr(frame, 'url', '')}")
    return frame


def discover_and_join_new_campaigns(score_fn=None, max_new: int = 2) -> list[str]:
    known_urls = {entry["url"] for entry in _load_configured_campaigns()}
    newly_joined: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            landed = False
            for start_url in WHOP_DISCOVER_URLS:
                try:
                    print(f"[whop_client debug] Opening Discover {start_url}")
                    _ensure_logged_in(page, start_url)
                    landed = True
                    break
                except PlaywrightTimeoutError:
                    print(f"[whop_client debug] {start_url} timed out")
                except WhopClientError as exc:
                    print(f"[whop_client debug] {start_url} failed: {exc}")
            if not landed:
                print("[whop_client debug] Discover page timed out — skipping auto-join.")
                return newly_joined

            frame = _discover_root(page)
            rate = re.compile(r"\$[\d.]+\s*/\s*1k", re.I)
            card_texts = frame.get_by_text(rate)
            try:
                card_count = min(card_texts.count(), 20)
            except Exception:
                card_count = 0
            print(f"[whop_client debug] Found {card_count} candidate campaign rate labels on Discover (iframe).")

            if card_count == 0:
                # Fall back to in-app Discover of an already-joined experience.
                configured = _load_configured_campaigns()
                if configured:
                    try:
                        _ensure_logged_in(page, configured[0]["url"])
                        app = _wait_for_app_frame(page, timeout_ms=20000)
                        if app is not None:
                            origin = (app.url or "").split("/discover")[0].split("/campaigns")[0]
                            target = f"{origin}/discover"
                            print(f"[whop_client debug] Trying in-app Discover {target}")
                            app.goto(target, timeout=20000)
                            page.wait_for_timeout(2000)
                            frame = _discover_root(page)
                            card_texts = frame.get_by_text(rate)
                            card_count = min(card_texts.count(), 20)
                            print(f"[whop_client debug] In-app Discover cards: {card_count}")
                    except Exception as exc:
                        print(f"[whop_client debug] In-app Discover fallback failed: {exc}")

            joined_this_run = 0
            for i in range(card_count):
                if joined_this_run >= max_new:
                    break
                try:
                    card_texts.nth(i).scroll_into_view_if_needed(timeout=3000)
                    card_texts.nth(i).click(timeout=3000)
                except Exception as exc:
                    print(f"[whop_client debug] Could not open Discover card #{i}: {exc}")
                    continue

                page.wait_for_timeout(1500)
                frame = _discover_root(page)
                try:
                    modal_text = frame.inner_text("body")
                except Exception:
                    modal_text = page.inner_text("body")
                if "not available in your region" in modal_text.lower():
                    print(f"[whop_client debug] Card #{i} is region-locked, skipping.")
                    _close_any_modal(page)
                    continue

                if score_fn is not None:
                    try:
                        score = score_fn(modal_text[:4000])
                        if not score.is_good:
                            print(f"[whop_client debug] AI skipped Discover card #{i}: {score.reason}")
                            _close_any_modal(page)
                            continue
                    except Exception as exc:
                        print(f"[whop_client debug] AI scoring unavailable for card #{i} ({exc}); proceeding anyway.")

                joined = False
                for root in (frame, page):
                    try:
                        root.get_by_role("button", name=re.compile("join campaign", re.I)).first.click(timeout=4000)
                        page.wait_for_timeout(2000)
                        joined = True
                        break
                    except Exception:
                        continue
                if not joined:
                    print(f"[whop_client debug] No 'Join Campaign' button on card #{i} (maybe already joined).")
                    _close_any_modal(page)
                    continue

                page.wait_for_timeout(1500)
                frame = _discover_root(page)
                candidates = [page.url, getattr(frame, "url", "")]
                saved = None
                for raw in candidates:
                    norm = _normalize_campaign_url(raw)
                    if "/campaigns/" in (norm or "") and norm not in known_urls:
                        saved = norm
                        break
                if saved:
                    known_urls.add(saved)
                    newly_joined.append(saved)
                    joined_this_run += 1
                    print(f"[whop_client debug] Joined new campaign: {saved}")
                else:
                    print(
                        f"[whop_client debug] Joined card #{i} but landed on unexpected URL "
                        f"(page={page.url} frame={getattr(frame, 'url', '')})."
                    )

                try:
                    page.goto(WHOP_DISCOVER_URL, timeout=DEFAULT_TIMEOUT_MS)
                    _settle(page)
                    frame = _discover_root(page)
                    card_texts = frame.get_by_text(rate)
                except Exception as exc:
                    print(f"[whop_client debug] Could not return to Discover: {exc}")
                    break
        finally:
            browser.close()

    if newly_joined:
        _save_new_campaign_urls(sorted(known_urls))
    return newly_joined


def _close_any_modal(page: Page) -> None:
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def check_configured_campaigns() -> Optional[WhopCampaign]:
    configured = _load_configured_campaigns()
    if not configured:
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            for entry in configured:
                url = entry["url"]
                name_hint = entry.get("name_hint", "")
                _ensure_logged_in(page, url)
                campaign = _extract_campaign_details(page, url, name_hint=name_hint)
                if campaign is not None:
                    return campaign
            return None
        finally:
            browser.close()


def _select_platform_icon(frame, video_url: str) -> None:
    lower = video_url.lower()
    if "tiktok.com" in lower:
        platform_name = "tiktok"
    elif "youtube.com" in lower or "youtu.be" in lower:
        platform_name = "youtube"
    elif "instagram.com" in lower:
        platform_name = "instagram"
    else:
        return

    try:
        frame.get_by_role("button", name=re.compile(platform_name, re.I)).click(timeout=3000)
    except Exception:
        pass


def _wait_for_app_frame(page: Page, timeout_ms: int = 25000):
    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for f in page.frames:
            if "apps.whop.com" in (f.url or ""):
                return f
        page.wait_for_timeout(400)
    return None


def _open_campaign_app_frame(page: Page, campaign_url: str):
    """Land inside the joined campaign iframe, not Discover / outer shell."""
    uuid_match = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
        campaign_url,
        re.I,
    )
    app_frame = _wait_for_app_frame(page)
    if app_frame is None:
        print("[whop_client debug] apps.whop.com iframe never appeared.")
        return _get_content_frame(page)
    if not uuid_match:
        return _get_content_frame(page)
    cid = uuid_match.group(1)
    origin = (app_frame.url or "").split("/discover")[0].split("/campaigns")[0]
    target = f"{origin}/campaigns/{cid}"
    print(f"[whop_client debug] Opening campaign inside app iframe: {target}")
    try:
        app_frame.goto(target, timeout=20000)
        page.wait_for_timeout(2500)
    except Exception as exc:
        print(f"[whop_client debug] Iframe goto failed: {exc}")
    return _get_content_frame(page)


def _click_submit_clip(frame, *, last: bool = False) -> None:
    locators = [
        frame.get_by_role("button", name=re.compile("submit clip", re.I)),
        frame.get_by_text(re.compile(r"^submit clip$", re.I)),
        frame.locator("button", has_text=re.compile("submit clip", re.I)),
        frame.locator("a", has_text=re.compile("submit clip", re.I)),
    ]
    last_err = None
    for loc in locators:
        try:
            target = loc.last if last else loc.first
            target.click(timeout=8000)
            return
        except Exception as exc:
            last_err = exc
            continue
    raise PlaywrightTimeoutError(str(last_err) if last_err else "Submit clip not found")


def _clean_public_video_url(video_url: str) -> str:
    """Drop tracking query like ?stkn= so Whop sees a normal permalink."""
    raw = (video_url or "").strip()
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    return raw.rstrip("/") + ("/" if "instagram.com/reel/" in raw.lower() else "")


def _submission_looks_accepted(text: str) -> bool:
    lower = (text or "").lower()
    good = ("submitted", "pending review", "under review", "clip submitted", "successfully")
    bad = ("invalid url", "couldn't submit", "could not submit", "already submitted", "error")
    if any(b in lower for b in bad):
        return False
    return any(g in lower for g in good)


def submit_video_link(campaign: WhopCampaign, video_url: str, dry_run: bool = False) -> None:
    video_url = _clean_public_video_url(video_url)
    print(f"[whop] using cleaned url: {video_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            _ensure_logged_in(page, campaign.submit_page_url)
            page.wait_for_timeout(2000)
            frame = _open_campaign_app_frame(page, campaign.submit_page_url)
            print(f"[whop] submit frame url={getattr(frame, 'url', '')}")
            _click_submit_clip(frame, last=False)
            page.wait_for_timeout(1200)
            _select_platform_icon(frame, video_url)
            url_input = frame.get_by_placeholder(re.compile(r"tiktok\.com|youtube\.com|instagram\.com|video", re.I))
            try:
                url_input.first.fill(video_url, timeout=DEFAULT_TIMEOUT_MS)
            except Exception:
                frame.locator("input").first.fill(video_url, timeout=DEFAULT_TIMEOUT_MS)
            try:
                frame.get_by_role("checkbox").first.check(timeout=3000)
            except Exception:
                try:
                    frame.get_by_text(re.compile("read the requirements", re.I)).click(timeout=3000)
                except Exception:
                    print("[whop] no requirements checkbox found — continuing")
            if dry_run:
                print("[whop] DRY RUN — form filled, final Submit not clicked.")
                print(f"[whop] would submit: {video_url}")
                return
            _click_submit_clip(frame, last=True)
            page.wait_for_timeout(2500)
            _settle(page)
            try:
                body = frame.inner_text("body")
            except Exception:
                body = page.inner_text("body")
            print(f"[whop] post-submit text snippet: {body[:400]!r}")
            if not _submission_looks_accepted(body):
                raise WhopClientError(
                    "Clicked Submit but page did not show a success/pending message. "
                    "Treat as NOT submitted. Check My work / Drafts on the phone."
                )
            print(f"[whop] submitted: {video_url}")
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
