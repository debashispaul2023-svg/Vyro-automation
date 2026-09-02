"""
email_otp.py

Vyro's login is passwordless: you enter your email, Vyro emails you a
one-time code, and you type that code back in. This module reads that
code straight out of your Gmail inbox via IMAP, so the daily automation
never needs a human to check their phone.

Requires a Gmail "App Password" (NOT your normal Gmail password) — see
README.md, section "Gmail App Password বানানো".
"""

from __future__ import annotations

import email
import imaplib
import re
import time
from typing import Optional

IMAP_HOST = "imap.gmail.com"
CODE_RE = re.compile(r"\b(\d{4,8})\b")


def _get_body_text(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type in ("text/plain", "text/html"):
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        return payload.decode(errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        payload = msg.get_payload(decode=True)
        return payload.decode(errors="ignore") if payload else ""
    except Exception:  # noqa: BLE001
        return ""


def _try_fetch_once(gmail_address: str, app_password: str, sender_hint: str) -> Optional[str]:
    conn = imaplib.IMAP4_SSL(IMAP_HOST)
    try:
        conn.login(gmail_address, app_password)
        conn.select("INBOX")
        status, data = conn.search(None, "UNSEEN")
        if status != "OK" or not data or not data[0]:
            return None

        # Newest emails have the highest IDs — check most recent first.
        for msg_id in reversed(data[0].split()):
            status, msg_data = conn.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            from_header = (msg.get("From") or "").lower()
            subject = (msg.get("Subject") or "").lower()
            if sender_hint.lower() not in from_header and sender_hint.lower() not in subject:
                continue

            body = _get_body_text(msg)
            match = CODE_RE.search(body) or CODE_RE.search(subject)
            if match:
                conn.store(msg_id, "+FLAGS", "\\Seen")  # don't reuse this code next time
                return match.group(1)
        return None
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def fetch_latest_otp(
    gmail_address: str,
    app_password: str,
    sender_hint: str = "vyro",
    timeout_s: int = 90,
    poll_interval_s: int = 5,
) -> Optional[str]:
    """
    Polls the inbox for up to timeout_s seconds looking for an unread email
    from/about `sender_hint` containing a 4-8 digit code. Returns the code,
    or None if it never showed up in time.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        code = _try_fetch_once(gmail_address, app_password, sender_hint)
        if code:
            return code
        time.sleep(poll_interval_s)
    return None
