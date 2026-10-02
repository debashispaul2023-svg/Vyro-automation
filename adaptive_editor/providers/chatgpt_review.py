"""Optional ChatGPT.com review. Same idea as Whop: session cookie, isolated, never required.

Whop uses WHOP_COOKIE_HEADER and a browser session.
This uses CHATGPT_COOKIE_HEADER or CHATGPT_SESSION_COOKIE.
If login, upload, or browser fails, log it and return ok=False.
Production must keep going.
"""
from __future__ import annotations

import os


REQUIRED = ("hook", "structure", "pacing", "issues", "suggestions")


def accept_review(payload) -> dict:
    if not isinstance(payload, dict) or any(key not in payload for key in REQUIRED):
        print("[chatgpt-review] malformed response — ignored")
        return {"ok": False, "provider": "chatgpt", "structured": False, "reason": "malformed"}
    if not isinstance(payload.get("issues"), list) or not isinstance(payload.get("suggestions"), list):
        print("[chatgpt-review] malformed response — ignored")
        return {"ok": False, "provider": "chatgpt", "structured": False, "reason": "malformed"}
    return {"ok": True, "provider": "chatgpt", "structured": True, "review": payload}


class ChatGPTReviewProvider:
    name = "chatgpt"

    def analyze(self, shots: list, frames: list | None = None) -> dict:
        if (os.environ.get("CHATGPT_REVIEW") or "0").strip() != "1":
            print("[chatgpt-review] skipping optional review")
            return {"ok": False, "provider": self.name, "reason": "CHATGPT_REVIEW=0"}
        cookie = (os.environ.get("CHATGPT_COOKIE_HEADER") or os.environ.get("CHATGPT_SESSION_COOKIE") or "").strip()
        if not cookie:
            print("[chatgpt-review] cookie unavailable")
            print("[chatgpt-review] skipping optional review")
            print("[adaptive] continuing with local analyzer")
            return {"ok": False, "provider": self.name, "reason": "no cookie"}
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            print(f"[adaptive] ChatGPT review skipped ({exc})")
            return {"ok": False, "provider": self.name, "reason": "playwright missing"}
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context()
                context.add_cookies(_cookies(cookie))
                page = context.new_page()
                page.goto("https://chatgpt.com/", timeout=20000)
                title = page.title()
                browser.close()
            print(f"[adaptive] ChatGPT session opened: {title}")
            print("[chatgpt-review] no structured review — ignored")
            return {"ok": False, "provider": self.name, "structured": False, "reason": "no structured review"}
        except Exception as exc:
            print(f"[adaptive] ChatGPT review skipped ({exc})")
            return {"ok": False, "provider": self.name, "reason": str(exc)[:180]}


def _cookies(header: str) -> list:
    rows = []
    for part in header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        rows.append({"name": name, "value": value, "domain": ".chatgpt.com", "path": "/"})
    return rows
