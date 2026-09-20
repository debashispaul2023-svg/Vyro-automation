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


def _safe_goto(page: Page, target_url: str) -> None:
    page.goto(target_url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("domcontentloaded", timeout=8000)
    except PlaywrightTimeoutError:
        pass


def _ensure_logged_in(page: Page, target_url: str) -> None:
    _safe_goto(page, target_url)
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
    _leave_personal_home(page)


def _leave_personal_home(page: Page) -> None:
    """The 2x2 companies icon sits on personal Home ($0.00). Bot must leave it."""
    blob = ""
    try:
        blob = (page.inner_text("body") or "")[:2000].lower()
    except Exception:
        pass
    personal = (
        "total balance" in blob
        or "recommended for you" in blob
        or "your balance will appear" in blob
        or "set up your business" in blob
    )
    if not personal:
        return
    print("[whop] personal Home ($0.00) — jumping to BloxClips campaigns")
    for url in (BLOXCLIPS_APP, BLOXCLIPS_HOME):
        try:
            _safe_goto(page, url)
            page.wait_for_timeout(1800)
            return
        except Exception as exc:
            print(f"[whop] BloxClips open failed ({url}): {exc}")


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
            if any(x in url for x in ("hcaptcha", "captcha", "stripecdn", "recaptcha")):
                continue
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


def _click_google_docs(page: Page, frame) -> str:
    """Campaign page only shows a Google Docs chip. User will not paste the URL."""
    labels = (
        r"google docs",
        r"reference materials",
        r"resources",
        r"footage",
        r"content folder",
    )
    roots = [frame, page] + list(page.frames)
    for pat in labels:
        for root in roots:
            try:
                chip = root.get_by_text(re.compile(pat, re.I)).first
                try:
                    with page.context.expect_page(timeout=5000) as popped:
                        chip.click(timeout=2500, force=True)
                    doc_page = popped.value
                    doc_page.wait_for_load_state("domcontentloaded", timeout=10000)
                    url = doc_page.url or ""
                    print(f"[whop] opened reference tab {url[:120]}")
                    if "docs.google.com" in url or "drive.google.com" in url:
                        doc_page.close()
                        return url
                    doc_page.close()
                except PlaywrightTimeoutError:
                    chip.click(timeout=2000, force=True)
                    page.wait_for_timeout(1200)
                    if "docs.google.com" in page.url or "drive.google.com" in page.url:
                        url = page.url
                        page.go_back(timeout=5000)
                        return url
            except Exception:
                continue
    return ""


def _extract_campaign_details(
page: Page, campaign_url: str, name_hint: str = "") -> Optional[WhopCampaign]:
    try:
        page.get_by_text(re.compile(r"submit clip|budget|views", re.I)).first.wait_for(
            state="visible", timeout=12000
        )
    except PlaywrightTimeoutError:
        print(f"[whop_client debug] Campaign content never appeared on the main page for {campaign_url} (checking iframes next).")

    frame = _get_content_frame(page)
    if "bloxclips" in (campaign_url or "") or "exp_EfN9" in (campaign_url or ""):
        frame = _open_campaigns_grid(page)
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

    if (looks_like_list or "submit clip" not in body_text.lower()) and not name_hint:
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


def _harvest_asset_links(frame, page, body_text: str) -> tuple[str, str]:
    """Pull Drive folder + Google Doc URLs from the open campaign page."""
    chunks = [body_text or ""]
    try:
        chunks.append(frame.content() or "")
    except Exception:
        pass
    try:
        chunks.append(page.content() or "")
    except Exception:
        pass
    try:
        hrefs = frame.eval_on_selector_all("a[href]", "els => els.map(e => e.href)")
        chunks.extend(hrefs or [])
    except Exception:
        pass
    blob = "\n".join(chunks)
    folders = re.findall(r"https://drive\.google\.com/drive/folders/[\w-]+", blob)
    files = re.findall(r"https://drive\.google\.com/file/d/[\w-]+", blob)
    docs = re.findall(r"https://docs\.google\.com/document/d/[\w-]+", blob)
    folder = folders[0] if folders else ""
    doc = docs[0] if docs else ""
    if folder:
        print(f"[whop] harvested Drive folder {folder}")
    if doc:
        print(f"[whop] harvested Google Doc {doc}")
    if files and not folder:
        print(f"[whop] harvested {len(files)} Drive file link(s)")
    return folder, doc

    folder, doc = _harvest_asset_links(frame, page, body_text)
    if not folder and not doc:
        opened = _click_google_docs(page, frame)
        if opened:
            print(f"[whop] Google Docs chip opened {opened[:140]}")
            if "/folders/" in opened:
                folder = opened
            else:
                doc = opened
            folder2, doc2 = _harvest_asset_links(frame, page, body_text)
            folder = folder or folder2
            doc = doc or doc2

    budget_match = re.search(r"\$([\d.]+)K?\s*/\s*\$([\d.]+)K", body_text)
    if budget_match:
        used = float(budget_match.group(1))
        total = float(budget_match.group(2))
        if total > 0 and used / total >= 0.90:
            print(f"[whop] skip {name_hint or 'card'} — budget {used}/{total} >= 90%")
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
                re.compile(r"google docs|reference materials|resources|edit\?usp=sharing|dos and don", re.I)
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
        source_clip_url=folder or "",
        submit_page_url=campaign_url,
        reference_doc_url=doc or reference_doc_url,
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


BLOX_CAMPAIGN_IDS = {
    "Tongue Escape": "ce2f887e-f54d-43b0-a2b9-e8da505f7b7a",
    "How to Fisch": "b59bb70c-58bf-44c1-9e44-0b54c59d90f4",
}


def _campaign_uuid(campaign) -> str:
    cid = (getattr(campaign, "campaign_id", "") or "").strip()
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", cid, re.I):
        return cid
    blob = f"{getattr(campaign, 'name', '')} {getattr(campaign, 'submit_page_url', '')}"
    for key, uid in BLOX_CAMPAIGN_IDS.items():
        if key.lower() in blob.lower():
            return uid
    low = blob.lower()
    if "tongue" in low:
        return BLOX_CAMPAIGN_IDS["Tongue Escape"]
    if "fisch" in low:
        return BLOX_CAMPAIGN_IDS["How to Fisch"]
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", blob, re.I)
    return m.group(1) if m else ""


def _app_origin(page: Page) -> str:
    app = _wait_for_app_frame(page, timeout_ms=12000)
    if app is None:
        return ""
    return (app.url or "").split("/discover")[0].split("/campaigns")[0]


def _open_campaigns_grid(page: Page):
    """Prefer Discover brand grid (6 cards). /campaigns often only shows already-opened Fisch."""
    app = _wait_for_app_frame(page, timeout_ms=15000)
    if app is None:
        print("[whop] no app iframe for campaigns grid")
        return _get_content_frame(page)
    origin = _app_origin(page)
    for path in ("/discover", "/campaigns"):
        target = f"{origin}{path}"
        print(f"[whop] board -> {target}")
        try:
            app.goto(target, timeout=20000)
            page.wait_for_timeout(2000)
            app.evaluate("window.scrollTo(0, 800)")
            page.wait_for_timeout(600)
        except Exception as exc:
            print(f"[whop] board nav {path} failed: {exc}")
    try:
        snippet = (app.inner_text("body") or "")[:500].replace("\n", " | ")
        print(f"[whop] board text: {snippet!r}")
    except Exception:
        pass
    return _wait_for_app_frame(page, timeout_ms=8000) or _get_content_frame(page)


def _open_named_board_campaign(page: Page, title: str) -> bool:
    origin = _app_origin(page)
    cid = ""
    for key, uuid in BLOX_CAMPAIGN_IDS.items():
        if key.lower() in title.lower() or title.lower() in key.lower():
            cid = uuid
            break
    if origin and cid:
        target = f"{origin}/campaigns/{cid}"
        print(f"[whop] direct open {title} -> {target}")
        app = _wait_for_app_frame(page, timeout_ms=8000)
        if app is not None:
            try:
                app.goto(target, timeout=20000)
                page.wait_for_timeout(1800)
                return True
            except Exception as exc:
                print(f"[whop] direct open failed: {exc}")
    frame = _open_campaigns_grid(page)
    needles = [title, title.replace("+1 ", ""), title.split("[")[0].strip()]
    if "tongue" in title.lower():
        needles += ["Tongue", "+1 Tongue"]
    if "steal" in title.lower():
        needles += ["Steal", "SEED"]
    if "athletics" in title.lower():
        needles += ["Athletics", "WORLD ATHLETICS"]
    if "fisch" in title.lower():
        needles += ["Fisch", "HOW TO FISCH"]
    for root in [frame, page] + list(page.frames):
        for needle in needles:
            try:
                root.get_by_text(re.compile(re.escape(needle), re.I)).first.click(timeout=2500, force=True)
                print(f"[whop] clicked text {needle!r}")
                page.wait_for_timeout(1500)
                return True
            except Exception:
                continue
    return False


def discover_and_join_new_campaigns(score_fn=None, max_new: int = 2) -> list[str]:
    known_urls = {entry["url"] for entry in _load_configured_campaigns()}
    newly_joined: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            landed = False
            board = [BLOXCLIPS_APP]
            for start_url in board:
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

            frame = _open_campaigns_grid(page)
            named = ("Tongue Escape", "Steal A Seed", "World Athletics")
            for title in named:
                try:
                    print(f"[whop] board click '{title}'")
                    if not _open_named_board_campaign(page, title):
                        raise PlaywrightTimeoutError(f"no visible text '{title}' on campaigns grid")
                    frame = _discover_root(page)
                    body = ""
                    try:
                        body = frame.inner_text("body")
                    except Exception:
                        body = page.inner_text("body")
                    if "forgegui" in body.lower():
                        print("[whop] opened ForgeGUI by mistake — skip")
                        _close_any_modal(page)
                        continue
                    joined = False
                    for root in (frame, page):
                        try:
                            root.get_by_role("button", name=re.compile("join campaign", re.I)).first.click(timeout=2500)
                            joined = True
                            print(f"[whop] joined '{title}'")
                            break
                        except Exception:
                            continue
                    if joined:
                        newly_joined.append(page.url)
                    _close_any_modal(page)
                except Exception as exc:
                    print(f"[whop] could not open '{title}': {exc}")
            print("[whop] marketplace $1k card loop off — BloxClips board names only")
            if newly_joined:
                _save_new_campaign_urls(newly_joined)
                return newly_joined
            card_texts = page.locator("not-a-real-thing")
            card_count = 0

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
                low = modal_text.lower()
                if "forgegui" in low or "forge gui" in low:
                    print(f"[whop_client debug] Card #{i} ForgeGUI — skip (red list).")
                    _close_any_modal(page)
                    continue
                if "application" in low and "forgegui" in low:
                    print(f"[whop_client debug] Card #{i} ForgeGUI application — skip.")
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
                    page.goto(WHOP_DISCOVER_URL, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
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



def _campaign_from_config(entry: dict) -> Optional[WhopCampaign]:
    """When Whop SPA does not finish loading, still run off the JSON + Drive folder."""
    source = (entry.get("source_clip_url") or "").strip()
    name = (entry.get("name_hint") or "configured-campaign").strip()
    if not source and not name:
        return None
    url = entry.get("url") or ""
    cid = "configured"
    m = re.search(r"campaigns/([0-9a-fA-F-]{16,})", url)
    if m:
        cid = m.group(1)
    elif "fisch" in name.lower():
        cid = "how-to-fisch"
    elif "tongue" in name.lower():
        cid = "ce2f887e-f54d-43b0-a2b9-e8da505f7b7a"
    rules = name
    if "fisch" in name.lower():
        rules = (
            f"{name}\n"
            "The name How to Fisch must be spoken somewhere in the video.\n"
            "The How to Fisch game icon must be visibly shown at the end of the video.\n"
            "A clear CTA must be included. Example: Game is called How to Fisch on Roblox.\n"
            "Low-quality poorly presented videos will not be accepted.\n"
            "Only Roblox-dedicated accounts may upload. English-based."
        )
    elif "tongue" in name.lower():
        rules = (
            f"{name}\n"
            "The name +1 Tongue Escape must be spoken somewhere in the video.\n"
            "The Tongue Escape game title or icon must be visibly shown somewhere in the video.\n"
            "A clear CTA must be included. Example: Game is called +1 Tongue Escape on Roblox.\n"
            "Put the game link in your bio while participating.\n"
            "Only Roblox accounts may upload. English-based. 1% engagement minimum.\n"
            "Codes: WELCOME1, BONUS500, FREEBOOST.\n"
            "https://www.roblox.com/games/122245938604556/1-Tongue-Escape"
        )
    elif "seed" in name.lower():
        cid = "steal-a-seed"
        rules = (
            f"{name}\n"
            "The name Steal A Seed must be spoken somewhere in the video.\n"
            "The Steal A Seed game icon must be visibly shown.\n"
            "A clear CTA must be included. Example: Game is called Steal A Seed on Roblox.\n"
            "Only Roblox accounts. English-based."
        )
    elif "athletics" in name.lower():
        cid = "world-athletics"
        rules = (
            f"{name}\n"
            "The name World Athletics must be spoken somewhere in the video.\n"
            "Show the World Athletics game title or icon.\n"
            "A clear CTA must be included. Example: Game is called World Athletics on Roblox.\n"
            "Only Roblox accounts. English-based."
        )
    print(f"[whop_client] config fallback campaign={cid} source={source[:80]}")
    return WhopCampaign(
        campaign_id=cid,
        name=name,
        requirements_text=rules,
        source_clip_url=source,
        submit_page_url=url,
        reference_doc_url=entry.get("reference_doc_url"),
        platforms=["instagram", "youtube", "tiktok"],
    )


def _looks_like_whop_chrome(text: str) -> bool:
    low = (text or "").lower()
    if len(low) < 80:
        return True
    chrome = ("your balance will appear", "ctrl+k", "recommended for you", "set up your business", "total balance")
    if any(x in low for x in chrome) and "how to fisch" not in low and "submit clip" not in low:
        return True
    return False


def check_configured_campaigns() -> Optional[WhopCampaign]:
    configured = _load_configured_campaigns()
    if not configured:
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            fisch_last = None
            for entry in configured:
                url = entry["url"]
                name_hint = entry.get("name_hint", "")
                try:
                    _ensure_logged_in(page, url)
                    campaign = _extract_campaign_details(page, url, name_hint=name_hint)
                except Exception as exc:
                    print(f"[whop_client] page open failed ({exc}); using config fallback if possible")
                    campaign = None
                if campaign is not None and _looks_like_whop_chrome(campaign.requirements_text):
                    print("[whop_client] scraped page is Whop chrome, not the campaign — using JSON fallback")
                    campaign = None
                if campaign is None:
                    campaign = _campaign_from_config(entry)
                if campaign is None:
                    continue
                extra = (entry.get("source_clip_url") or "").strip()
                if extra:
                    campaign.source_clip_url = extra
                    print(f"[whop_client debug] Using configured source clip: {extra}")
                doc = (entry.get("reference_doc_url") or campaign.reference_doc_url or "").strip()
                if doc:
                    campaign.reference_doc_url = doc
                uuid = (entry.get("campaign_uuid") or "").strip()
                if uuid:
                    campaign.campaign_id = uuid
                    if "campaigns/" not in (campaign.submit_page_url or ""):
                        campaign.submit_page_url = (
                            f"https://whop.com/bloxclips/exp_EfN9ClEYDL8Bh9/app/campaigns/{uuid}"
                        )
                blob = f"{campaign.name} {name_hint}".lower()
                has_assets = bool((campaign.source_clip_url or "").strip() or (campaign.reference_doc_url or "").strip())
                if "fisch" in blob:
                    print("[whop] How to Fisch saved as last resort — trying newer BloxClips campaigns first")
                    fisch_last = campaign
                    continue
                if not has_assets:
                    print(f"[whop] '{campaign.name}' has no Drive/Doc yet — skip to next card")
                    continue
                print(f"[whop] selected board campaign '{campaign.name}' doc={campaign.reference_doc_url}")
                return campaign
            if fisch_last is not None:
                print("[whop] no newer campaign usable — falling back to How to Fisch")
                return fisch_last
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


def _goto_campaign_detail(page: Page, uid: str):
    """Open the one campaign detail that has the Submit form. Never stay on the grid."""
    app = _wait_for_app_frame(page, timeout_ms=20000)
    if app is None:
        raise WhopClientError("apps.whop.com iframe missing — cannot open campaign detail")
    origin = (app.url or "").split("/discover")[0].split("/campaigns")[0]
    target = f"{origin}/campaigns/{uid}"
    print(f"[whop] detail goto {target}")
    try:
        app.goto(target, timeout=25000)
    except Exception as exc:
        print(f"[whop] iframe goto failed ({exc}); retry")
        page.wait_for_timeout(800)
        app.goto(target, timeout=25000)
    page.wait_for_timeout(2000)
    deadline = time.time() + 15
    while time.time() < deadline:
        app = _wait_for_app_frame(page, timeout_ms=3000) or app
        url = getattr(app, "url", "") or ""
        if uid.lower() in url.lower() and "/campaigns/" in url:
            print(f"[whop] on detail {url}")
            return app
        page.wait_for_timeout(500)
    print(f"[whop] still not on uuid page, url={getattr(app, 'url', '')}")
    return _get_content_frame(page)


def _fill_submit_url(page: Page, frame, video_url: str):
    roots = []
    for root in [frame, page] + list(page.frames):
        ru = (getattr(root, "url", "") or "").lower()
        if any(x in ru for x in ("hcaptcha", "captcha", "stripecdn", "recaptcha")):
            continue
        roots.append(root)
    for root in roots:
        try:
            _select_platform_icon(root, video_url)
        except Exception:
            pass
    selectors = [
        "input[placeholder*='http' i]",
        "input[placeholder*='url' i]",
        "input[placeholder*='youtube' i]",
        "input[placeholder*='instagram' i]",
        "input[placeholder*='tiktok' i]",
        "input[placeholder*='paste' i]",
        "input[type='url']",
        "textarea",
        "input[type='text']",
    ]
    for root in roots:
        for sel in selectors:
            try:
                box = root.locator(sel).last
                box.wait_for(state="visible", timeout=2500)
                box.click(timeout=2000)
                box.fill("")
                box.fill(video_url, timeout=4000)
                print(f"[whop] filled URL via {sel} on {getattr(root, 'url', '')[:80]}")
                return root
            except Exception:
                continue
    for root in roots:
        try:
            handle = root.evaluate_handle(
                """(url) => {
                    const nodes = [...document.querySelectorAll('input,textarea')];
                    const box = nodes.reverse().find(el =>
                        (el.offsetParent !== null) &&
                        (el.type === 'url' || el.type === 'text' || el.tagName === 'TEXTAREA')
                    );
                    if (!box) return false;
                    box.focus();
                    box.value = url;
                    box.dispatchEvent(new Event('input', {bubbles:true}));
                    box.dispatchEvent(new Event('change', {bubbles:true}));
                    return true;
                }""",
                video_url,
            )
            if handle.json_value():
                print("[whop] filled URL via DOM eval")
                return root
        except Exception:
            continue
    raise WhopClientError("Submit form opened but no URL box was found.")


def _tick_submit_confirmations(page: Page, form) -> None:
    """Tick every confirm row + the requirements checkbox on the submit sheet."""
    labels = (
        r"posted from one of your linked",
        r"not already submitted",
        r"posted within the last 30 minutes",
        r"i'?ve read the requirements",
        r"read the requirements",
        r"accept that non-compliant",
    )
    roots = [form, page] + list(page.frames)
    for label in labels:
        clicked = False
        for root in roots:
            ru = (getattr(root, "url", "") or "").lower()
            if any(x in ru for x in ("hcaptcha", "captcha", "stripecdn")):
                continue
            try:
                root.get_by_text(re.compile(label, re.I)).first.click(timeout=2500, force=True)
                print(f"[whop] ticked: {label}")
                clicked = True
                break
            except Exception:
                continue
        if not clicked:
            print(f"[whop] could not tick: {label}")
    for root in roots:
        try:
            boxes = root.get_by_role("checkbox")
            n = boxes.count()
            for i in range(min(n, 6)):
                try:
                    boxes.nth(i).check(timeout=1500)
                except Exception:
                    try:
                        boxes.nth(i).click(timeout=1500, force=True)
                    except Exception:
                        pass
            if n:
                print(f"[whop] checked {n} checkbox(es)")
                break
        except Exception:
            continue


def _click_submit_clip(frame, *, last: bool = False) -> None:
    locators = [
        frame.get_by_role("button", name=re.compile("submit clip", re.I)),
        frame.get_by_text(re.compile(r"submit clip", re.I)),
        frame.locator("button", has_text=re.compile("submit clip", re.I)),
        frame.locator("a", has_text=re.compile("submit clip", re.I)),
        frame.locator("[class*='submit' i]"),
    ]
    last_err = None
    for loc in locators:
        try:
            target = loc.last if last else loc.first
            target.scroll_into_view_if_needed(timeout=4000)
            target.click(timeout=12000, force=True)
            return
        except Exception as exc:
            last_err = exc
            continue
    raise PlaywrightTimeoutError(str(last_err) if last_err else "Submit clip not found")


def _click_submit_anywhere(page: Page, *, last: bool = False) -> object:
    """Search the outer page and every iframe for Submit clip."""
    roots = [page] + list(page.frames)
    last_err = None
    for root in roots:
        try:
            _click_submit_clip(root, last=last)
            return root
        except Exception as exc:
            last_err = exc
            continue
    raise PlaywrightTimeoutError(str(last_err) if last_err else "Submit clip not found in any frame")


def _open_named_campaign(page: Page, campaign: WhopCampaign) -> None:
    hint = (campaign.name or "").strip()
    app = _wait_for_app_frame(page, timeout_ms=12000)
    if app is not None:
        base = (app.url or "").split("/discover")[0].split("/campaigns")[0]
        if base.startswith("http"):
            for path in ("/campaigns", "/my-work", "/submissions"):
                try:
                    print(f"[whop] iframe -> {base}{path}")
                    app.goto(base + path, timeout=15000)
                    page.wait_for_timeout(1500)
                    break
                except Exception as exc:
                    print(f"[whop] iframe nav {path} failed: {exc}")
    for root in [page] + list(page.frames):
        try:
            root.get_by_text(re.compile(r"^Campaigns$", re.I)).first.click(timeout=2500, force=True)
            print("[whop] force-clicked Campaigns")
            page.wait_for_timeout(1200)
            break
        except Exception:
            continue
    if hint:
        for root in [page] + list(page.frames):
            try:
                root.get_by_text(re.compile(re.escape(hint), re.I)).first.click(timeout=3000, force=True)
                print(f"[whop] opened campaign card '{hint}'")
                page.wait_for_timeout(1500)
                break
            except Exception:
                continue


def _clean_public_video_url(video_url: str) -> str:
    """Keep watch?v= / reel id. Drop only tracking junk like stkn."""
    raw = (video_url or "").strip()
    low = raw.lower()
    if "youtube.com/watch" in low:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(raw).query)
        vid = (q.get("v") or [None])[0]
        if vid:
            return f"https://www.youtube.com/watch?v={vid}"
        return raw
    if "youtu.be/" in low:
        return raw.split("?")[0]
    if "instagram.com/reel/" in low:
        return raw.split("?")[0].rstrip("/") + "/"
    if "?" in raw:
        raw = raw.split("?", 1)[0]
    return raw


def _submission_looks_accepted(text: str) -> bool:
    lower = (text or "").lower()
    if "submit video link" in lower:
        return False
    bad = ("invalid url", "couldn't submit", "could not submit", "error submitting")
    if any(b in lower for b in bad):
        return False
    good = (
        "pending review", "under review", "awaiting review",
        "clip submitted", "submission received", "successfully submitted",
        "thanks for submitting", "sent for review",
    )
    return any(g in lower for g in good)


def submit_video_link(campaign: WhopCampaign, video_url: str, dry_run: bool = False) -> None:
    video_url = _clean_public_video_url(video_url)
    print(f"[whop] using cleaned url: {video_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = _new_context_with_session(browser)
        page = context.new_page()
        try:
            uid = _campaign_uuid(campaign)
            if not uid:
                raise WhopClientError(f"No campaign UUID for '{campaign.name}' — cannot open submit form")
            board = "https://whop.com/bloxclips/exp_EfN9ClEYDL8Bh9/app/"
            print(f"[whop] submit start uid={uid}")
            _ensure_logged_in(page, board)
            page.wait_for_timeout(1200)
            frame = _goto_campaign_detail(page, uid)
            print(f"[whop] submit frame url={getattr(frame, 'url', '')}")
            try:
                seen = ((frame.inner_text("body") if frame else "") or page.inner_text("body") or "")[:2500].lower()
            except Exception:
                seen = ""
            if "forgegui" in seen and "tongue" not in seen and "fisch" not in seen:
                raise WhopClientError(
                    f"WRONG CAMPAIGN on screen (ForgeGUI). Refusing submit for {campaign.name}."
                )
            try:
                frame = _click_submit_anywhere(page, last=False)
            except Exception as exc:
                print(f"[whop] first Submit clip click missed ({exc})")
                frame = _get_content_frame(page)
                _click_submit_clip(frame, last=False)
            page.wait_for_timeout(1800)
            try:
                frame.get_by_text(re.compile(r"submit video link", re.I)).first.wait_for(timeout=8000)
                print("[whop] submit sheet visible")
            except Exception:
                print("[whop] submit sheet heading not seen — still filling")
            form = _fill_submit_url(page, frame, video_url)
            page.wait_for_timeout(1500)
            _tick_submit_confirmations(page, form)
            page.wait_for_timeout(800)
            if dry_run:
                print("[whop] DRY RUN — form filled, final Submit not clicked.")
                print(f"[whop] would submit: {video_url}")
                return
            clicked_final = False
            for root in [form, frame, page] + list(page.frames):
                try:
                    btn = root.get_by_role("button", name=re.compile(r"^submit clip$", re.I)).last
                    btn.wait_for(state="visible", timeout=4000)
                    btn.click(timeout=8000, force=True)
                    print("[whop] clicked form Submit clip")
                    clicked_final = True
                    break
                except Exception:
                    continue
            if not clicked_final:
                try:
                    _click_submit_anywhere(page, last=True)
                    clicked_final = True
                except Exception:
                    _click_submit_clip(form, last=True)
                    clicked_final = True
            page.wait_for_timeout(3500)
            _settle(page)
            try:
                body = frame.inner_text("body")
            except Exception:
                body = page.inner_text("body")
            print(f"[whop] post-submit text snippet: {body[:400]!r}")
            still_form = "submit video link" in (body or "").lower()
            if still_form or not _submission_looks_accepted(body):
                raise WhopClientError(
                    "Submit clip clicked but Whop did not confirm. "
                    "Form may still be open. Treat as NOT submitted."
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
