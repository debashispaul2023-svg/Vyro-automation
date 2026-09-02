"""
gmail_otp.py

Reads Vyro's email-OTP login code from Gmail automatically, using a
READ-ONLY Gmail API token (see generate_gmail_token.py for the one-time
setup). This is what lets daily_runner.py complete Vyro's passwordless
email-code login without a human present to type the code in.
"""

from __future__ import annotations

import base64
import os
import re
import time

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
DEFAULT_TOKEN_PATH = "gmail_token.json"

# Matches a standalone 4-8 digit code (most OTP emails use 6 digits).
_OTP_RE = re.compile(r"\b(\d{4,8})\b")


class GmailOtpError(Exception):
    """Raised when the Vyro OTP code can't be read from Gmail."""


def _load_credentials(token_path: str = DEFAULT_TOKEN_PATH) -> Credentials:
    if not os.path.isfile(token_path):
        raise GmailOtpError(
            f"Gmail OAuth token not found at '{token_path}'. Run "
            f"generate_gmail_token.py once locally (see README)."
        )

    creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:  # noqa: BLE001
            raise GmailOtpError(f"Failed to refresh Gmail token: {exc}") from exc
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())

    if not creds or not creds.valid:
        raise GmailOtpError("Gmail OAuth token is invalid — regenerate it.")

    return creds


def _decode_body(payload: dict) -> str:
    """Recursively decode a Gmail message payload (handles multipart)."""
    text = ""
    if "parts" in payload:
        for part in payload["parts"]:
            text += _decode_body(part)
        return text
    data = payload.get("body", {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def wait_for_vyro_otp(
    after_epoch_seconds: float,
    token_path: str = DEFAULT_TOKEN_PATH,
    timeout_seconds: int = 90,
    poll_interval_seconds: int = 5,
) -> str:
    """
    Polls Gmail for a Vyro OTP/verification-code email that arrived AFTER
    after_epoch_seconds (pass time.time() right before clicking "Continue"
    on Vyro's login form, since that's when Vyro sends the email), and
    returns the numeric code found in it.

    Raises GmailOtpError if no matching email shows up within timeout_seconds.
    """
    creds = _load_credentials(token_path)
    service = build("gmail", "v1", credentials=creds)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            results = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q="newer_than:1h (vyro OR code OR verification OR OTP OR \"sign in\")",
                    maxResults=8,
                )
                .execute()
            )
        except Exception as exc:  # noqa: BLE001
            raise GmailOtpError(f"Gmail API search failed: {exc}") from exc

        for msg_ref in results.get("messages", []):
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )
            internal_ts = int(msg.get("internalDate", "0")) / 1000.0
            # Skip emails older than the login attempt (minus a small buffer
            # for clock drift).
            if internal_ts < after_epoch_seconds - 30:
                continue

            code = _OTP_RE.search(msg.get("snippet", ""))
            if code:
                return code.group(1)

            body_text = _decode_body(msg.get("payload", {}))
            code = _OTP_RE.search(body_text)
            if code:
                return code.group(1)

        time.sleep(poll_interval_seconds)

    raise GmailOtpError(
        f"No Vyro OTP email found within {timeout_seconds}s. Check that "
        f"GMAIL_TOKEN_JSON is valid and that Vyro's actual email "
        f"sender/subject matches the search query in gmail_otp.py."
    )


if __name__ == "__main__":
    # Quick manual test: python gmail_otp.py
    # (looks for any matching email from the last 5 minutes)
    print(wait_for_vyro_otp(after_epoch_seconds=time.time() - 300, timeout_seconds=30))
