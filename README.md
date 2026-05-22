# Subscription info bot (3x-ui)

Telegram bot: user sends a **subscription link**, bot reads client info from the [3x-ui API](https://github.com/MHSanaei/3x-ui/wiki/Configuration#api-documentation).

## Setup

```bash
cd subbot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp panels.json.example panels.json
cp .env.example .env
# Edit panels.json and .env
python bot.py
```

## `panels.json`

Each key is the **subscription base URL** (the part before the token):

```json
{
  "https://host:2096/sub": {
    "name": "Server 1",
    "api_url": "https://host:2053",
    "username": "admin",
    "password": "panel_password"
  }
}
```

- `api_url`: panel web URL (usually port 2053)
- Subscription links usually use port 2096 (`/sub/TOKEN`)

## Flow

1. User sends e.g. `https://host:2096/sub/TOKEN`
2. Bot extracts `subId` and finds the matching panel in `panels.json`
3. API login → find client by `subId` → `getClientTraffics`
4. Shows usage, expiry, status, subscription URL, and vless/vmess links

## Environment

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `PANELS_FILE` | Path to panels file (default: `panels.json`) |
| `VERIFY_SSL` | `true` only if the panel has a valid SSL certificate |
