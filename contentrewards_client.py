"""Content Rewards (contentrewards.com) reader and submitter, via a signed-in browser session.

STATUS: UNVERIFIED against the live site. It was written from the public page only
(requirements are locked until you join + sign in), so selectors are heuristic and every step
saves a screenshot to output/cr/ so a failure is visible. Dry-run first.

Session: sign in once in a browser, export Playwright storage state (cookies + localStorage) and
store it as the CR_STORAGE_STATE secret (JSON) or CR_STORAGE_STATE_FILE (path).

Safety: VYRO_SKIP_UPLOAD=1 or CR_DRY_RUN=1 -> opens the form, fills it, takes a screenshot, and
does NOT press the final submit button.
"""
from __future__ import annotations

import json
import os
import re
import tempfile

SHOT_DIR = "output/cr"


class ContentRewardsError(RuntimeError):
    pass


def _state_path() -> str:
    path = (os.environ.get("CR_STORAGE_STATE_FILE") or "").strip()
    if path and os.path.isfile(path):
        return path
    raw = (os.environ.get("CR_STORAGE_STATE") or "").strip()
    if raw:
        try:
            json.loads(raw)
        except ValueError:
            raise ContentRewardsError("CR_STORAGE_STATE is not valid JSON")
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(raw)
        tmp.close()
        return tmp.name
    return ""


def session_available() -> bool:
    try:
        return bool(_state_path())
    except ContentRewardsError:
        return False


def _dry() -> bool:
    return (os.environ.get("CR_DRY_RUN") or "").strip() in ("1", "true", "yes") or \
           (os.environ.get("VYRO_SKIP_UPLOAD") or "").strip() in ("1", "true", "yes")


def _open(p):
    state = _state_path()
    if not state:
        raise ContentRewardsError("no Content Rewards session (set CR_STORAGE_STATE)")
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(storage_state=state, viewport={"width": 1280, "height": 1600})
    return browser, context.new_page()


def _shot(page, name: str) -> str:
    os.makedirs(SHOT_DIR, exist_ok=True)
    path = f"{SHOT_DIR}/{name}.png"
    try:
        page.screenshot(path=path, full_page=True)
    except Exception:
        pass
    return path


def _logged_out(page) -> bool:
    body = (page.inner_text("body") or "").lower()
    return "join to unlock" in body or ("sign in" in body and "join campaign" in body)


def fetch_requirements(url: str) -> str:
    """Text of the Requirements section. Raises when the page is still locked."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = _open(p)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            _shot(page, "requirements")
            if _logged_out(page):
                raise ContentRewardsError("requirements still locked: session not signed in or campaign not joined")
            body = page.inner_text("body")
            m = re.search(r"requirements?\s*\n(.+?)(?:\n\s*(?:resources?|reference|submit|payout|about)\b|\Z)", body, re.I | re.S)
            text = (m.group(1) if m else body).strip()
            return text[:4000]
        finally:
            browser.close()


def submit_video_link(campaign, video_url: str, dry_run: bool | None = None) -> None:
    """Open the campaign, add the YouTube link as a clip submission."""
    from playwright.sync_api import sync_playwright
    dry = _dry() if dry_run is None else dry_run
    url = getattr(campaign, "submit_page_url", "") or ""
    if "contentrewards.com" not in url:
        raise ContentRewardsError(f"not a Content Rewards URL: {url}")
    with sync_playwright() as p:
        browser, page = _open(p)
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            _shot(page, "1_campaign")
            if _logged_out(page):
                raise ContentRewardsError("session is signed out or the campaign is not joined")
            opener = page.get_by_role("button", name=re.compile(r"submit|add (clip|content|post|video)|new submission", re.I)).first
            if not opener.count():
                opener = page.get_by_text(re.compile(r"submit (a )?(clip|content|video|link)|add (clip|content)", re.I)).first
            if not opener.count():
                raise ContentRewardsError(f"no submit button found (see {_shot(page, 'no_submit_button')})")
            opener.click()
            page.wait_for_timeout(1500)
            yt = page.get_by_text(re.compile(r"^\s*youtube\s*$", re.I)).first
            if yt.count():
                try:
                    yt.click()
                except Exception:
                    pass
            field = page.locator("input[type=url], input[placeholder*='link' i], input[placeholder*='url' i], input[name*='url' i]").first
            if not field.count():
                raise ContentRewardsError(f"no link field found (see {_shot(page, 'no_link_field')})")
            field.fill(video_url)
            _shot(page, "2_filled")
            confirm = page.get_by_role("button", name=re.compile(r"^\s*(submit|add|save|send)\b", re.I)).last
            if not confirm.count():
                raise ContentRewardsError(f"no confirm button found (see {_shot(page, 'no_confirm')})")
            if dry:
                print(f"[cr] DRY RUN: filled {video_url}; would click '{confirm.inner_text().strip()}'. Screenshot output/cr/2_filled.png")
                return
            confirm.click()
            page.wait_for_timeout(3000)
            _shot(page, "3_after_submit")
            body = (page.inner_text("body") or "").lower()
            if not re.search(r"submitted|success|pending|under review|in review|thank", body):
                raise ContentRewardsError(f"no success message after submit (see {SHOT_DIR}/3_after_submit.png)")
            print(f"[cr] submitted {video_url}")
        finally:
            browser.close()
