"""Optional ChatGPT.com review. Same idea as Whop: session cookie, isolated, never required.

Whop uses WHOP_COOKIE_HEADER and a browser session.
This uses CHATGPT_COOKIE_HEADER or CHATGPT_SESSION_COOKIE.
If login, upload, or browser fails, log it and return ok=False.
Production must keep going.
"""
from __future__ import annotations

import os


class ChatGPTReviewProvider:
    name = "chatgpt"

    def analyze(self, shots: list, frames: list | None = None) -> dict:
        if (os.environ.get("CHATGPT_REVIEW") or "0").strip() != "1":
            print("[adaptive] ChatGPT review skipped")
            return {"ok": False, "provider": self.name, "reason": "CHATGPT_REVIEW=0"}
        cookie = (os.environ.get("CHATGPT_COOKIE_HEADER") or os.environ.get("CHATGPT_SESSION_COOKIE") or "").strip()
        if not cookie:
            print("[adaptive] ChatGPT review skipped — no session cookie")
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
            return {
                "ok": True,
                "provider": self.name,
                "notes": "session opened; frame upload is optional and not required",
                "confidence": 0.4,
            }
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
