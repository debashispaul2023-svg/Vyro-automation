"""
generate_gmail_token.py

ONE-TIME LOCAL SETUP SCRIPT — run this on your own computer, not in CI.

Vyro logs you in with an email OTP (a code it emails you), not a password.
To fully automate login, the bot needs to read that OTP from your inbox —
this script grants it READ-ONLY access to Gmail (it can only read messages,
never send, delete, or modify anything) and saves the token to
gmail_token.json.

Prerequisites (can reuse the SAME Google Cloud project you made for YouTube):
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Enable the "Gmail API" for your project (APIs & Services -> Library)
  3. You can reuse the same client_secrets.json from the YouTube step —
     no need to create a second OAuth client.

Usage:
  python generate_gmail_token.py
"""

from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CLIENT_SECRETS_PATH = "client_secrets.json"
TOKEN_PATH = "gmail_token.json"


def main() -> int:
    if not os.path.isfile(CLIENT_SECRETS_PATH):
        print(
            f"'{CLIENT_SECRETS_PATH}' not found. Download your OAuth Desktop "
            f"client credentials from Google Cloud Console and place them here "
            f"(the same file you used for generate_youtube_token.py works).",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"Saved read-only Gmail OAuth token to {TOKEN_PATH}.")
    print(
        "For GitHub Actions: copy this file's full JSON content into the "
        "'GMAIL_TOKEN_JSON' repository secret (see README)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
