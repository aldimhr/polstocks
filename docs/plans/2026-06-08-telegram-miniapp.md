# PolStock Telegram Bot + Mini App — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Wrap the existing Politics Stock Mapper with an aiogram Telegram bot and adapt its dashboard as a Telegram Mini App, with push alerts for watchlist hits.

**Architecture:** The existing FastAPI app stays on port 8001. Nginx reverse-proxies `polstock.aldimhr.dev` with SSL to that port. An aiogram bot runs as a separate service, calling the existing REST API. The dashboard.html is adapted to load `telegram-web-app.js` and read user context.

**Tech Stack:** Python 3.11, aiogram 3, FastAPI (existing), nginx, certbot, Telegram WebApp API

---

## Task 1: Move PolStock from port 80 to port 8001

**Objective:** Free port 80 for nginx, run PolStock behind a reverse proxy.

**Files:**
- Modify: `/etc/systemd/system/politics-stock-mapper.service` (Environment=PORT=8001)

**Steps:**
1. Edit the systemd service to use PORT=8001
2. Reload systemd and restart the service
3. Verify it responds on port 8001

```bash
# Edit service
sudo sed -i 's/PORT=80/PORT=8001/' /etc/systemd/system/politics-stock-mapper.service
sudo systemctl daemon-reload
sudo systemctl restart politics-stock-mapper
curl -s http://127.0.0.1:8001/healthz
```

**Verify:** `curl http://127.0.0.1:8001/healthz` returns 200 OK.

---

## Task 2: Set up nginx + SSL for polstock.aldimhr.dev

**Objective:** Serve PolStock over HTTPS via nginx reverse proxy (required for Telegram Mini Apps).

**Files:**
- Create: `/etc/nginx/sites-available/polstock`
- Symlink: `/etc/nginx/sites-enabled/polstock`

**Steps:**
1. Create nginx config for `polstock.aldimhr.dev` on port 80 (HTTP first)
2. Enable the site and reload nginx
3. Get SSL cert via certbot
4. Certbot auto-updates nginx config to add HTTPS

```nginx
# /etc/nginx/sites-available/polstock
upstream polstock_backend {
    server 127.0.0.1:8001;
    keepalive 8;
}

server {
    listen 80;
    server_name polstock.aldimhr.dev;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
        default_type "text/plain";
    }

    location / {
        proxy_pass http://polstock_backend;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/polstock /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d polstock.aldimhr.dev --non-interactive --agree-tos --email dirikuin@gmail.com
```

**Verify:** `curl -I https://polstock.aldimhr.dev/healthz` returns 200 with valid SSL.

---

## Task 3: Create aiogram bot project structure

**Objective:** Set up the bot project with uv venv, aiogram, and config.

**Files:**
- Create: `/opt/hermes/polstock_bot/` (project root)
- Create: `/opt/hermes/polstock_bot/bot.py` (main entry)
- Create: `/opt/hermes/polstock_bot/config.py` (settings)
- Create: `/opt/hermes/polstock_bot/.env` (secrets)

**Steps:**
```bash
mkdir -p /opt/hermes/polstock_bot
cd /opt/hermes/polstock_bot
uv venv .venv
source .venv/bin/activate
uv pip install aiogram aiohttp python-dotenv
```

**config.py:**
```python
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
POLSTOCK_API = os.getenv("POLSTOCK_API", "http://127.0.0.1:8001")
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://polstock.aldimhr.dev")
ADMIN_ID = int(os.getenv("ADMIN_ID", "519613720"))
```

**.env:**
```
BOT_TOKEN=***REDACTED***
POLSTOCK_API=http://127.0.0.1:8001
MINI_APP_URL=https://polstock.aldimhr.dev
ADMIN_ID=519613720
```

---

## Task 4: Build bot handlers (start, watchlist, refresh, dashboard)

**Objective:** Core bot commands that call the existing PolStock API.

**Files:**
- Create: `/opt/hermes/polstock_bot/handlers.py`
- Modify: `/opt/hermes/polstock_bot/bot.py`

**Commands:**
- `/start` — Welcome + inline button to open Mini App
- `/watchlist` — Show current watchlist tickers
- `/add <ticker>` — Add ticker to watchlist
- `/remove <ticker>` — Remove ticker from watchlist
- `/refresh [window]` — Trigger API refresh, show summary
- `/dashboard` — Open Mini App button

**Key implementation:**
- Use `aiohttp.ClientSession` to call PolStock REST API
- `InlineKeyboardButton(text="📊 Open Dashboard", web_app=WebAppInfo(url=MINI_APP_URL))`
- Format responses with Telegram-friendly markdown (emoji + bold)

---

## Task 5: Adapt dashboard.html for Telegram Mini App

**Objective:** Make the existing dashboard work inside Telegram's WebView.

**Files:**
- Create: `/opt/hermes/polstock_mapper/miniapp.html` (adapted copy of dashboard.html)

**Changes from original dashboard.html:**
1. Add `<script src="https://telegram.org/js/telegram-web-app.js"></script>` in `<head>`
2. Call `Telegram.WebApp.ready()` and `Telegram.WebApp.expand()` on load
3. Read `Telegram.WebApp.initDataUnsafe.user` for personalization
4. Use `Telegram.WebApp.colorScheme` to respect dark/light theme
5. Add a "Send to Bot" action using `Telegram.WebApp.MainButton` if needed
6. Keep all existing functionality — the API calls stay the same

**Also:**
- Add route in FastAPI to serve `miniapp.html` at `/app` (so `/app` serves the Telegram version, `/` stays the original)

---

## Task 6: Add Mini App route to FastAPI backend

**Objective:** Serve the Telegram-adapted dashboard at `/app` endpoint.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/main.py` (add route near line 4174)

**Add route:**
```python
MINIAPP_FILE = PROJECT_ROOT / "miniapp.html"

@app.get("/app", response_class=HTMLResponse)
async def miniapp():
    return MINIAPP_FILE.read_text(encoding="utf-8")
```

---

## Task 7: Set up systemd service for the bot

**Objective:** Run the PolStock bot as a persistent service.

**Files:**
- Create: `/etc/systemd/system/polstock-bot.service`

```ini
[Unit]
Description=PolStock Telegram Bot
After=network.target politics-stock-mapper.service

[Service]
Type=simple
WorkingDirectory=/opt/hermes/polstock_bot
ExecStart=/opt/hermes/polstock_bot/.venv/bin/python -m bot
Restart=on-failure
RestartSec=3
EnvironmentFile=/opt/hermes/polstock_bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now polstock-bot
```

**Verify:** `sudo systemctl status polstock-bot` shows active/running.

---

## Task 8: Test end-to-end

**Objective:** Verify everything works together.

1. Send `/start` to the bot → should see welcome + dashboard button
2. Tap "Open Dashboard" → Mini App opens with PolStock dashboard
3. Send `/watchlist` → shows current tickers
4. Send `/add BBCA.JK` → adds to watchlist
5. Send `/refresh 7d` → triggers refresh, shows summary
6. Verify the Mini App loads data and displays correctly

---

## Future (not in this plan)
- Push alerts: cron job that checks `/api/refresh` periodically and DMs users on high-impact events
- Inline mode: share stock cards in group chats
- Per-user watchlists (would need a database)
