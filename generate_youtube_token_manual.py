"""
generate_youtube_token_manual.py

PHONE-FRIENDLY OAuth setup — no computer, no local server needed. Google's
normal "InstalledAppFlow.run_local_server()" flow needs a browser and a
script running on the SAME machine, which doesn't work when the script
runs in GitHub Actions but you're granting access from your phone. This
does the same OAuth exchange manually, in two steps, both run as GitHub
Actions workflow_dispatch calls (see .github/workflows/generate_youtube_token.yml):

STEP 1 — get the authorization link:
  Run the workflow with no "auth_code" input. It prints a Google
  sign-in URL in the logs. Open that URL in your phone's browser, sign in
  with the Google account you'll upload videos as, and click Allow.

STEP 2 — the browser will try to redirect to a "localhost" address that
  doesn't exist on your phone, so it'll show an error page (that's
  expected — ignore the error). Look at the ADDRESS BAR: it'll contain
  something like:
      http://localhost/?code=4/0AY....&scope=...
  Copy just the value after "code=" and before the next "&".

STEP 3 — run the workflow AGAIN, this time pasting that code into the
  "auth_code" input box. This exchanges it for real tokens and prints the
  full token.json content in the logs — copy that and save it as the
  YOUTUBE_TOKEN_JSON GitHub secret (replacing what's there now, which is
  actually just the client_secrets.json content, not a real token).

Required env var: CLIENT_SECRETS_JSON (the OAuth Desktop client
credentials JSON — what's currently, mistakenly, stored in the
YOUTUBE_TOKEN_JSON secret; reused here as input on purpose).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse

import requests

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
REDIRECT_URI = "http://localhost"
AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


def _load_client_config() -> dict:
    raw = os.environ.get("CLIENT_SECRETS_JSON", "")
    if not raw.strip():
        print("CLIENT_SECRETS_JSON environment variable is empty.", file=sys.stderr)
        sys.exit(1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"CLIENT_SECRETS_JSON is not valid JSON: {exc}", file=sys.stderr)
        print(f"Length: {len(raw)} chars.", file=sys.stderr)
        print(f"First 150 chars: {raw[:150]!r}", file=sys.stderr)
        print(f"Chars 0-30 as codepoints: {[hex(ord(c)) for c in raw[:30]]}", file=sys.stderr)
        sys.exit(1)

    # Google's client_secrets.json wraps everything under "installed" or "web".
    inner = data.get("installed") or data.get("web") or data
    for key in ("client_id", "client_secret"):
        if key not in inner:
            print(f"CLIENT_SECRETS_JSON is missing '{key}'. Is this really a Desktop OAuth client file?", file=sys.stderr)
            sys.exit(1)
    return inner


def print_auth_url(client_config: dict) -> None:
    params = {
        "client_id": client_config["client_id"],
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",   # required to get a refresh_token
        "prompt": "consent",        # force refresh_token even on repeat runs
    }
    url = f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"
    print("\n" + "=" * 70)
    print("STEP 1: Open this URL in your phone's browser and click Allow:")
    print("=" * 70)
    print(url)
    print("=" * 70)
    print(
        "\nAfter clicking Allow, the browser will try to open a 'localhost' "
        "page and fail — that's expected. Copy the 'code=' value from the "
        "address bar, then re-run this workflow with that value in the "
        "'auth_code' input box.\n"
    )


def exchange_code(client_config: dict, code: str) -> None:
    resp = requests.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_config["client_id"],
            "client_secret": client_config["client_secret"],
            "redirect_uri": REDIRECT_URI,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        print(f"❌ Token exchange failed (status {resp.status_code}):\n{resp.text}", file=sys.stderr)
        print(
            "\nCommon causes: the code was already used once (codes are "
            "single-use — get a fresh one via Step 1), or too much time "
            "passed since you got the code (they expire in a few minutes).",
            file=sys.stderr,
        )
        sys.exit(1)

    tokens = resp.json()
    if "refresh_token" not in tokens:
        print(
            "⚠️  No refresh_token in the response. This usually means you've "
            "already granted access before without revoking it — go to "
            "https://myaccount.google.com/permissions , remove access for "
            "this app, then redo Step 1 with a fresh consent.",
            file=sys.stderr,
        )
        sys.exit(1)

    token_json = {
        "token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"],
        "token_uri": TOKEN_ENDPOINT,
        "client_id": client_config["client_id"],
        "client_secret": client_config["client_secret"],
        "scopes": SCOPES,
    }

    print("\n" + "=" * 70)
    print("✅ SUCCESS — copy everything between the lines below and save it")
    print("as the YOUTUBE_TOKEN_JSON GitHub secret (replace the old value):")
    print("=" * 70)
    print(json.dumps(token_json, indent=2))
    print("=" * 70)


def main() -> int:
    client_config = _load_client_config()
    auth_code = (os.environ.get("AUTH_CODE") or "").strip()

    if not auth_code:
        print_auth_url(client_config)
    else:
        exchange_code(client_config, auth_code)
    return 0


if __name__ == "__main__":
    sys.exit(main())
