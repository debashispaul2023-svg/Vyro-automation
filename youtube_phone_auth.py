"""
youtube_phone_auth.py

Phone-friendly YouTube OAuth. Run inside GitHub Actions.

Reads client_id/client_secret from YOUTUBE_TOKEN_JSON (client_secrets
style is OK). Starts Google's device-code login, prints a URL + short
code, waits until you Allow on the phone, then writes token.json.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.parse
import urllib.request

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"


class AuthError(Exception):
    pass


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise AuthError(f"HTTP {exc.code} from {url}: {raw[:400]}") from exc


def _load_client() -> tuple[str, str]:
    raw = (os.environ.get("YOUTUBE_TOKEN_JSON") or "").strip()
    if not raw:
        raise AuthError("YOUTUBE_TOKEN_JSON secret is empty.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError(
            "YOUTUBE_TOKEN_JSON is not valid JSON. It should be the "
            "client_secrets file downloaded from Google Cloud Console."
        ) from exc

    if not isinstance(data, dict):
        raise AuthError("YOUTUBE_TOKEN_JSON must be a JSON object.")

    block = data.get("installed") or data.get("web") or data
    client_id = (block.get("client_id") or data.get("client_id") or "").strip()
    client_secret = (block.get("client_secret") or data.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise AuthError(
            "Could not find client_id + client_secret in YOUTUBE_TOKEN_JSON. "
            "Top-level keys: " + ", ".join(data.keys())
        )
    print(f"Using OAuth client_id ending ...{client_id[-18:]}")
    return client_id, client_secret


def main() -> int:
    print("=" * 70)
    print("YouTube phone login (device code)")
    print("=" * 70)

    try:
        client_id, client_secret = _load_client()
    except AuthError as exc:
        print(f"❌ {exc}")
        return 1

    start = _post(
        DEVICE_CODE_URL,
        {"client_id": client_id, "scope": SCOPE},
    )
    if start.get("error"):
        print(f"❌ Google device-code start failed: {start}")
        print(
            "\nIf error is unauthorized_client: this OAuth client is a "
            "Desktop app, which often cannot use device login.\n"
            "On your phone open Google Cloud Console → APIs & Services → "
            "Credentials → Create credentials → OAuth client ID → "
            "application type: TVs and Limited Input devices.\n"
            "Enable YouTube Data API v3. Download the JSON and REPLACE "
            "the YOUTUBE_TOKEN_JSON secret with that file, then re-run "
            "this Action."
        )
        return 1

    verify_url = start.get("verification_url") or "https://www.google.com/device"
    user_code = start.get("user_code")
    device_code = start.get("device_code")
    expires_in = int(start.get("expires_in") or 600)
    interval = int(start.get("interval") or 5)

    print("\n" + "!" * 70)
    print("PHONE STEPS — do this now:")
    print(f"  1. Open: {verify_url}")
    print(f"  2. Type this code: {user_code}")
    print("  3. Choose the Google account that owns the YouTube channel")
    print("  4. Tap Allow")
    print("!" * 70)
    print(f"Waiting up to {expires_in}s for you to Allow...\n")

    deadline = time.time() + expires_in - 15
    while time.time() < deadline:
        time.sleep(interval)
        result = _post(
            TOKEN_URL,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        err = result.get("error")
        if err == "authorization_pending":
            print("... still waiting")
            continue
        if err == "slow_down":
            interval += 5
            print("... slowing poll")
            continue
        if err:
            print(f"❌ Google token poll failed: {result}")
            return 1

        access = result.get("access_token")
        refresh = result.get("refresh_token")
        if not access or not refresh:
            print(f"❌ Got a token response but no refresh_token: keys={list(result)}")
            print("Try again and make sure you tap Allow on the consent screen.")
            return 1

        token = {
            "token": access,
            "refresh_token": refresh,
            "token_uri": TOKEN_URL,
            "client_id": client_id,
            "client_secret": client_secret,
            "scopes": SCOPE.split(),
            "type": "authorized_user",
        }
        with open("token.json", "w", encoding="utf-8") as f:
            json.dump(token, f)
        compact = json.dumps(token, separators=(",", ":"))
        print(f"\n✅ Wrote token.json ({os.path.getsize('token.json')} bytes)")
        print("\nNEXT: GitHub → Settings → Secrets → YOUTUBE_TOKEN_JSON → Update")
        print("Copy the SINGLE line between the markers. Do not add spaces or quotes.")
        print("-----BEGIN YOUTUBE TOKEN JSON-----")
        print(compact)
        print("-----END YOUTUBE TOKEN JSON-----")
        print("\nThen re-run TEST - Metadata + YouTube (Part 5).")
        return 0

    print("❌ Timed out. Re-run this Action and Allow faster.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
