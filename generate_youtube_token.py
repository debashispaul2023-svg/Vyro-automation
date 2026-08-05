"""
generate_youtube_token.py

ONE-TIME LOCAL SETUP SCRIPT — run this on your own computer, not in CI.

Opens a browser for you to log in to your YouTube/Google account and grant
upload permission, then saves the resulting OAuth token to token.json.
That token.json is what youtube_uploader.py (and GitHub Actions) reuse for
every future upload — no browser login needed again unless you revoke access.

Prerequisites:
  1. Go to https://console.cloud.google.com/apis/credentials
  2. Create an OAuth 2.0 Client ID of type "Desktop app"
  3. Download it as client_secrets.json and place it next to this script
  4. Enable the "YouTube Data API v3" for your project

Usage:
  python generate_youtube_token.py
"""

from __future__ import annotations

import os
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRETS_PATH = "client_secrets.json"
TOKEN_PATH = "token.json"


def main() -> int:
    if not os.path.isfile(CLIENT_SECRETS_PATH):
        print(
            f"'{CLIENT_SECRETS_PATH}' not found. Download your OAuth Desktop "
            f"client credentials from Google Cloud Console and place them here.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_PATH, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(creds.to_json())

    print(f"Saved OAuth token to {TOKEN_PATH}.")
    print(
        "For GitHub Actions: base64-encode this file and store it as the "
        "'YOUTUBE_TOKEN_B64' repository secret (see README)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
