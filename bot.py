#!/usr/bin/env python3
"""
EventSub (WebSocket) + Send Chat Message (Helix) skeleton — MULTI-CHANNEL
Now with external JSON wordlists for prize generation.

Setup
1) pip install websockets requests python-dotenv
2) Create a Twitch application (Client ID) at https://dev.twitch.tv/console/apps
3) Create a **User Access Token** for your bot account with scopes:
   user:read:chat user:write:chat user:bot
4) .env next to this file:
   TWITCH_CLIENT_ID=your_client_id
   BOT_USER_ACCESS_TOKEN=your_user_access_token
   BOT_LOGIN=your_bot_username
   BROADCASTER_LOGINS=alice,bob,charlie   # comma-separated list of channels to join
5) Ensure `prize_words.json` is next to this file.
6) python eventsub_sendchat_ws.py

`prize_words.json` should contain:
{
  "adjectives": ["Golden", "Inflatable", ...],
  "nouns": ["Toaster", "Banjo", ...],
  "abstracts": ["Disappointment", "Confusion", ...]
}
"""

import os
import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional, Dict, List
import random

import requests
import websockets

EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"
HELIX_BASE = "https://api.twitch.tv/helix"

# --- config ---
@dataclass
class Config:
    client_id: str
    user_token: str
    bot_login: str
    broadcaster_logins: List[str]


def cfg_from_env() -> Config:
    cid = os.getenv("TWITCH_CLIENT_ID", "").strip()
    tok = os.getenv("BOT_USER_ACCESS_TOKEN", "").strip()
    bot_login = os.getenv("BOT_LOGIN", "").strip().lower()
    logins_env = os.getenv("BROADCASTER_LOGINS", "").strip()
    broadcaster_logins = [s.strip().lower() for s in logins_env.split(",") if s.strip()]
    if not (cid and tok and bot_login and broadcaster_logins):
        raise SystemExit("Missing env: TWITCH_CLIENT_ID, BOT_USER_ACCESS_TOKEN, BOT_LOGIN, BROADCASTER_LOGINS")
    return Config(cid, tok, bot_login, broadcaster_logins)

# --- Local Lights API (passthrough) ---

LIGHTS_API_BASE = os.getenv("LIGHTS_API_BASE", "http://localhost:5000").rstrip("/")

def set_lights_passthrough(raw_value: str, timeout: float = 3.0) -> tuple[bool, str]:
    """
    Pass the colour value straight through to the internal lights API.
    No preprocessing here; the server handles names/hex/stripping/casing.
    We send both keys so the server can accept either schema.
    Returns (ok, message).
    """
    url = f"{LIGHTS_API_BASE}/set_colour"
    try:
        payload = {"colour": raw_value, "color": raw_value}
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:120]}"
        return True, "ok"
    except requests.RequestException as e:
        return False, str(e)


# --- Helix helpers ---

def helix_headers(cfg: Config) -> dict:
    return {
        "Authorization": f"Bearer {cfg.user_token}",
        "Client-Id": cfg.client_id,
        "Content-Type": "application/json",
    }


def get_user_id(cfg: Config, login: str) -> str:
    r = requests.get(f"{HELIX_BASE}/users", params={"login": login}, headers=helix_headers(cfg))
    r.raise_for_status()
    data = r.json().get("data", [])
    if not data:
        raise RuntimeError(f"User not found: {login}")
    return data[0]["id"]


def get_user_ids(cfg: Config, logins: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    chunk = 100
    for i in range(0, len(logins), chunk):
        params = [("login", login) for login in logins[i:i+chunk]]
        r = requests.get(f"{HELIX_BASE}/users", params=params, headers=helix_headers(cfg))
        r.raise_for_status()
        for row in r.json().get("data", []):
            out[row["login"].lower()] = row["id"]
    missing = [l for l in logins if l.lower() not in out]
    if missing:
        raise RuntimeError(f"These logins were not found: {missing}")
    return out


def send_chat(cfg: Config, broadcaster_id: str, sender_id: str, message: str, reply_parent_message_id: Optional[str] = None):
    body = {"broadcaster_id": broadcaster_id, "sender_id": sender_id, "message": message}
    if reply_parent_message_id:
        body["reply_parent_message_id"] = reply_parent_message_id
    r = requests.post(f"{HELIX_BASE}/chat/messages", headers=helix_headers(cfg), data=json.dumps(body))
    if r.status_code == 401:
        raise RuntimeError("Unauthorized: check user token and scopes (user:read:chat user:write:chat user:bot)")
    if r.status_code >= 400:
        raise RuntimeError(f"Send chat failed {r.status_code}: {r.text}")
    return r.json()


def create_subscription(cfg: Config, session_id: str, sub_type: str, condition: dict, version: str = "1"):
    body = {
        "type": sub_type,
        "version": version,
        "condition": condition,
        "transport": {"method": "websocket", "session_id": session_id},
    }
    r = requests.post(f"{HELIX_BASE}/eventsub/subscriptions", headers=helix_headers(cfg), data=json.dumps(body))
    if r.status_code == 401:
        raise RuntimeError("Unauthorized to create EventSub subscription; check token/scopes")
    if r.status_code >= 400:
        raise RuntimeError(f"Subscription create failed {r.status_code}: {r.text}")
    return r.json()


# --- Bot ---
class EventSubBot:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bot_user_id: Optional[str] = None
        self.broadcaster_ids: Dict[str, str] = {}
        self.id_to_login: Dict[str, str] = {}
        self._last_send = 0.0
        self._send_interval = 1.2
        self._last_lights = 0.0  # global cooldown marker for !lights

    async def run(self):
        self.bot_user_id = get_user_id(self.cfg, self.cfg.bot_login)
        self.broadcaster_ids = get_user_ids(self.cfg, self.cfg.broadcaster_logins)
        self.id_to_login = {uid: login for login, uid in self.broadcaster_ids.items()}
        print(f"Bot {self.cfg.bot_login} -> {self.bot_user_id}")
        print("Broadcasters:")
        for login, uid in self.broadcaster_ids.items():
            print(f"  {login} -> {uid}")

        url = EVENTSUB_WS_URL
        while True:
            try:
                async with websockets.connect(url, ping_interval=None) as ws:
                    url = await self._on_connected(ws)
                    async for raw in ws:
                        await self._handle_message(raw)
            except websockets.ConnectionClosedError as e:
                print(f"WebSocket closed: {e}. Reconnecting...")
                await asyncio.sleep(2)
                continue
            except KeyboardInterrupt:
                print("Shutting down...")
                return
            except Exception as e:
                print("Error:", e)
                await asyncio.sleep(2)

    async def _on_connected(self, ws: websockets.WebSocketClientProtocol) -> str:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            mtype = msg.get("metadata", {}).get("message_type") or msg.get("message_type")
            if mtype == "session_welcome":
                session = msg["payload"]["session"]
                session_id = session["id"]
                keepalive = session.get("keepalive_timeout_seconds")
                print(f"Welcome. session_id={session_id} keepalive={keepalive}")
                assert self.bot_user_id
                for login, bid in self.broadcaster_ids.items():
                    create_subscription(
                        self.cfg,
                        session_id=session_id,
                        sub_type="channel.chat.message",
                        condition={
                            "broadcaster_user_id": bid,
                            "user_id": self.bot_user_id,
                        },
                    )
                    print(f"Subscribed: channel.chat.message for {login} ({bid})")
                return EVENTSUB_WS_URL
            elif mtype == "session_reconnect":
                reconnect_url = msg["payload"]["session"]["reconnect_url"]
                print("Server requested reconnect to:", reconnect_url)
                return reconnect_url
            else:
                pass

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            print("Non-JSON frame:", raw[:80])
            return
        m = msg.get("metadata", {})
        mtype = m.get("message_type") or msg.get("message_type")
        if mtype == "session_keepalive":
            return
        if mtype == "session_reconnect":
            print("Reconnect hint received")
            return
        if mtype == "notification":
            await self._handle_notification(msg.get("payload", {}))
            return
        if mtype == "revocation":
            print("Subscription revoked:\n", json.dumps(msg, indent=2))
            return

    async def _handle_notification(self, payload: dict):
        sub = payload.get("subscription", {})
        typ = sub.get("type")
        event = payload.get("event", {})
        if typ == "channel.chat.message":
            b_id = event.get("broadcaster_user_id")
            text = extract_plain_text_from_message(event.get("message", {}))
            chatter = event.get("chatter_user_name") or event.get("chatter_user_login")
            message_id = event.get("message_id")
            channel_login = self.id_to_login.get(b_id, b_id)
            print(f"[{channel_login}] <{chatter}> {text}")
            await self._commands(text, reply_to=message_id, broadcaster_id=b_id, chatter=chatter)

    async def _commands(self, text: str, reply_to: Optional[str], broadcaster_id: str, chatter: Optional[str] = None):
        t = (text or "").strip()
        if not t.startswith("!"):
            return
        lower = t.lower()
        if lower == "!hello":
            await self._reply("Hey there! o/", reply_to, broadcaster_id)
        elif lower.startswith("!echo "):
            await self._reply(t[6:], reply_to, broadcaster_id)
        elif lower.startswith("!lights"):
            # Usage: !lights rebecca purple  |  !lights #0af  |  !lights #112233
            arg = t[len("!lights"):].strip()
            if not arg:
                await self._reply("Usage: !lights <colour name or #hex>", reply_to, broadcaster_id)
            else:
                cooldown = 300.0  # 5 minutes
                now = time.time()
                remaining = cooldown - (now - self._last_lights)
                if remaining > 0:
                    mins = int(remaining // 60)
                    secs = int(remaining % 60)
                    await self._reply(
                        f"Lights cooldown: try again in {mins}m {secs}s",
                        reply_to,
                        broadcaster_id,
                    )
                else:
                    ok, msg = set_lights_passthrough(arg)
                    if ok:
                        self._last_lights = now
                        await self._reply(f"Lights set to {arg}", reply_to, broadcaster_id)
                    else:
                        await self._reply(f"Lights error: {msg}", reply_to, broadcaster_id)

        elif lower == "!help":
            await self._reply("Commands: !hello, !echo <text>, !prize", reply_to, broadcaster_id)
        elif lower.startswith("!prize"):
            parts = t.split()
            recipient = (chatter or "someone")
            count = 1
            if len(parts) >= 2:
                if parts[1].isdigit():
                    count = int(parts[1])
                else:
                    recipient = parts[1].lstrip("@") or recipient
                    if len(parts) >= 3 and parts[2].isdigit():
                        count = int(parts[2])
            count = max(1, min(5, count))
            prizes = [generate_prize() for _ in range(count)]
            if count == 1:
                msgout = f"{recipient} receives a prize: {prizes[0]}"
            else:
                msgout = f"{recipient} receives prizes: " + "; ".join(prizes)
            await self._reply(msgout, reply_to, broadcaster_id)

    async def _reply(self, msg: str, reply_to: Optional[str], broadcaster_id: str):
        now = time.time()
        if now - self._last_send < self._send_interval:
            await asyncio.sleep(self._send_interval - (now - self._last_send))
        assert self.bot_user_id
        send_chat(self.cfg, broadcaster_id, self.bot_user_id, msg, reply_parent_message_id=reply_to)
        self._last_send = time.time()


# --- util ---

def extract_plain_text_from_message(message_obj: dict) -> str:
    frags = message_obj.get("fragments", [])
    parts = []
    for f in frags:
        if f.get("type") == "text":
            parts.append(f.get("text", ""))
        else:
            parts.append(f.get("text", ""))
    return "".join(parts) if parts else (message_obj.get("text") or "")

# --- prize generator ---
_DEF_TWO_WORD_PROB = 0.5
_EASTER_EGG_PROB = 0.04

_wordlist = None

def load_wordlist():
    global _wordlist
    if _wordlist is None:
        here = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(here, "prize_words.json")
        with open(path, "r", encoding="utf-8") as f:
            _wordlist = json.load(f)
    return _wordlist


def generate_prize() -> str:
    wl = load_wordlist()
    adjectives = wl.get("adjectives", [])
    nouns = wl.get("nouns", [])
    abstracts = wl.get("abstracts", [])

    if random.random() < _EASTER_EGG_PROB and abstracts:
        return random.choice(abstracts)

    three_words = random.random() >= _DEF_TWO_WORD_PROB
    if three_words and len(adjectives) >= 2:
        a1, a2 = random.sample(adjectives, 2)
        noun = random.choice(nouns)
        return f"{a1} {a2} {noun}"
    else:
        adj = random.choice(adjectives)
        noun = random.choice(nouns)
        return f"{adj} {noun}"


async def main():
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv()
    except Exception:
        pass
    bot = EventSubBot(cfg_from_env())
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())
