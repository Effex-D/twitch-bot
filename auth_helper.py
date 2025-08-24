#!/usr/bin/env python3
"""
Twitch OAuth (Device Code) helper — no redirect server
- Pure `requests` (+ optional `python-dotenv` for .env convenience)
- Gets a USER ACCESS TOKEN with scopes for chat bots and writes it to `.env`
- Can also refresh/validate later

Usage:
  pip install requests python-dotenv
  export TWITCH_CLIENT_ID=your_app_client_id
  # optional: export SCOPES="user:read:chat user:write:chat user:bot"

  # Start device flow (prints a code and URL for you to visit once):
  python device_oauth.py start

  # Refresh later using saved refresh token:
  python device_oauth.py refresh

  # Validate current token in .env
  python device_oauth.py validate

Writes/updates in .env:
  BOT_USER_ACCESS_TOKEN=...
  BOT_REFRESH_TOKEN=...

Docs: https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/#device-code-grant-flow
"""

import os
import sys
import time
import json
from typing import Dict

import requests

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

ID_BASE = "https://id.twitch.tv/oauth2"
DEFAULT_SCOPES = os.getenv("SCOPES", "user:read:chat user:write:chat user:bot")
ENV_PATH = os.path.join(os.getcwd(), ".env")


def _write_env(updates: Dict[str, str]):
    # naive .env updater that preserves other lines
    lines = []
    existing = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        for ln in lines:
            if "=" in ln and not ln.strip().startswith("#"):
                k, v = ln.split("=", 1)
                existing[k] = v
    existing.update(updates)
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")
    print(f"Updated {ENV_PATH} with keys: {', '.join(updates.keys())}")


def start_device_flow():
    client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
    if not client_id:
        sys.exit("Set TWITCH_CLIENT_ID in environment or .env before running.")

    data = {
        "client_id": client_id,
        "scopes": DEFAULT_SCOPES,
    }
    r = requests.post(f"{ID_BASE}/device", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code != 200:
        sys.exit(f"Device start failed: {r.status_code} {r.text}")
    payload = r.json()
    device_code = payload["device_code"]
    user_code = payload["user_code"]
    verify_url = payload["verification_uri"]
    interval = int(payload.get("interval", 5))
    expires_in = int(payload.get("expires_in", 600))

    print("=== Device Code ===")
    print("Visit:", verify_url)
    print("Enter code:", user_code)
    print("Scopes:", DEFAULT_SCOPES)

    # Poll for token
    print("Waiting for approval ... (Ctrl+C to cancel)")
    deadline = time.time() + expires_in
    while time.time() < deadline:
        time.sleep(interval)
        tok = requests.post(
            f"{ID_BASE}/token",
            data={
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if tok.status_code == 200:
            j = tok.json()
            access_token = j["access_token"]
            refresh_token = j.get("refresh_token")
            scope = j.get("scope", [])
            print("Approved! scopes:", scope)
            _write_env({
                "BOT_USER_ACCESS_TOKEN": access_token,
                **({"BOT_REFRESH_TOKEN": refresh_token} if refresh_token else {}),
            })
            validate(access_token)
            return
        else:
            try:
                err = tok.json()
            except Exception:
                err = {"error": tok.text}
            if err.get("error") in {"authorization_pending", "slow_down"}:
                if err.get("error") == "slow_down":
                    interval += 2
                continue
            elif err.get("error") in {"access_denied", "expired_token"}:
                sys.exit(f"Stopped: {err}")
            else:
                print("Poll error:", err)
                # keep trying until deadline unless it's a hard error
    sys.exit("Timed out waiting for user approval.")


def refresh():
    client_id = os.getenv("TWITCH_CLIENT_ID", "").strip()
    refresh_token = os.getenv("BOT_REFRESH_TOKEN", "").strip()
    if not client_id or not refresh_token:
        sys.exit("Need TWITCH_CLIENT_ID and BOT_REFRESH_TOKEN set.")
    r = requests.post(
        f"{ID_BASE}/token",
        data={
            "client_id": client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if r.status_code != 200:
        sys.exit(f"Refresh failed: {r.status_code} {r.text}")
    j = r.json()
    access_token = j["access_token"]
    new_refresh = j.get("refresh_token", refresh_token)
    _write_env({
        "BOT_USER_ACCESS_TOKEN": access_token,
        "BOT_REFRESH_TOKEN": new_refresh,
    })
    validate(access_token)


def validate(token: str = ""):
    token = token or os.getenv("BOT_USER_ACCESS_TOKEN", "").strip()
    if not token:
        sys.exit("No BOT_USER_ACCESS_TOKEN set.")
    r = requests.get(
        f"{ID_BASE}/validate",
        headers={"Authorization": f"Bearer {token}"},
    )
    if r.status_code != 200:
        sys.exit(f"Validate failed ({r.status_code}): {r.text}")
    info = r.json()
    print("Token valid for:")
    print(json.dumps(info, indent=2))


if __name__ == "__main__":
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "start").lower()
    if cmd == "start":
        start_device_flow()
    elif cmd == "refresh":
        refresh()
    elif cmd == "validate":
        validate()
    else:
        print("Usage: python device_oauth.py [start|refresh|validate]")
