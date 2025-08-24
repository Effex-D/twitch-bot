# Twitch Chat Bot (EventSub + Send Chat Message)

A lightweight Twitch chat bot written in **Python**, using:
- `websockets` for EventSub WebSocket events
- `requests` for Twitch Helix API calls
- `python-dotenv` for environment variable loading

This bot listens to chat messages in one or more channels and can reply with fun commands like `!hello`, `!echo`, and `!prize`.

## Features
- **Multi-channel support**: Join multiple broadcasters at once.
- **EventSub WebSocket**: Modern Twitch chat event API (no IRC).
- **Send Chat Message API**: Replies directly into Twitch chat.
- **Nonsense prize generator**: `!prize` gives out silly prizes composed of adjectives + nouns (with occasional abstract “easter egg” prizes).
- **External wordlist**: Prize words live in `prize_words.json` for easy editing and expansion.

## Requirements
- Python 3.9+
- Twitch Developer Application (Client ID + User Access Token)

## Installation
```bash
pip install websockets requests python-dotenv
```

## Setup
1. **Create a Twitch Developer Application**
   - Go to <https://dev.twitch.tv/console/apps>
   - Note your **Client ID**

2. **Generate a User Access Token** (with scopes `user:read:chat user:write:chat user:bot`)
   - Use the included `device_oauth.py` helper:
     ```bash
     export TWITCH_CLIENT_ID=<your_client_id>
     python device_oauth.py start
     ```
   - Follow the on-screen instructions to authorize your bot account.

3. **Configure environment variables**
   Create a `.env` file in the project root:
   ```ini
   TWITCH_CLIENT_ID=your_client_id
   BOT_USER_ACCESS_TOKEN=your_user_access_token
   BOT_LOGIN=your_bot_username
   BROADCASTER_LOGINS=alice,bob,charlie
   ```
   - `BOT_LOGIN` is your bot account’s Twitch username.
   - `BROADCASTER_LOGINS` is a comma-separated list of channel usernames to join.

4. **Prize wordlist**
   - Ensure a `prize_words.json` file is present (see included example).
   - Edit this file to add/remove adjectives, nouns, or abstract words.

## Running the Bot
```bash
python eventsub_sendchat_ws.py
```

The bot will connect to Twitch EventSub WS, subscribe to chat messages, and begin listening for commands.

## Commands
- `!hello` → Greets the user.
- `!echo <text>` → Repeats your text.
- `!prize` → Awards a random nonsense prize to the user.
- `!prize <username>` → Awards a prize to someone else.
- `!prize <n>` → Gives the user `n` prizes (up to 5).
- `!prize <username> <n>` → Gives someone else multiple prizes.

## File Overview
- `bot.py` — Main bot implementation.
- `auth_helper.py` — Device flow helper to generate/refresh tokens.
- `prize_words.json` — Wordlist for prize generation.

## Todo
- Wrap entire OAuth life-cycle into the bot.
- Figure out something for the bot to.. you know... do.
- ?
- Profit?

## Notes
- **Rate limits**: The bot includes a simple pacing mechanism, but Twitch API rate limits still apply.
- **Production**: For a production deployment, add proper token refresh, reconnection backoff, and persistent subscription handling.

## License
MIT (modify and have fun!)
