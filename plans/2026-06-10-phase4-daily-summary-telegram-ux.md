# Phase 4 — Daily Summary and Telegram UX

**Goal:** Make the system useful every trading day. New `/daily`, `/why TICKER` commands, refocused `/signals` grouped by horizon, and morning cron job.

**Depends on:** Phase 1 (trading_signals.py), Phase 2 (horizon columns), Phase 3 (calibration).

---

## Task 4.1: Add `/api/signals/daily-summary` endpoint

**Objective:** Backend endpoint that returns top actionable signals grouped by horizon.

**Files:**
- Modify: `backend/main.py` — add new endpoint
- Modify: `tests/test_app.py` — add test

**Endpoint: `GET /api/signals/daily-summary`**

Query params: `limit=3`, `include_watch=true`

Logic:
1. Get latest dashboard payload stocks
2. Extract `trading_signal` from each stock
3. Filter: only BUY/SELL/WATCH (not IGNORE)
4. Group by time_horizon (1d, 7d, 30d)
5. Within each horizon, sort by signal_strength descending
6. Return top N per horizon
7. Include calibration edge vs baseline for context

---

## Task 4.2: Add API client methods to bot

**Objective:** Add `get_daily_summary()` and `get_signal_explain()` to bot's api.py.

**Files:**
- Modify: `/opt/hermes/polstock_bot/api.py`

---

## Task 4.3: Add `/daily` and `/why TICKER` commands + refocus `/signals`

**Objective:** New bot commands.

**Files:**
- Modify: `/opt/hermes/polstock_bot/handlers.py` — add cmd_daily, cmd_why, update cmd_signals
- Modify: `/opt/hermes/polstock_bot/bot.py` — register new commands

**`/daily` format:**
```
📊 PolStock Daily — 10 Jun 2026

⚡ 1d signals
No Tier A/B signals. Watch: ADRO.JK

📅 7d
🟢 BUY CPIN.JK · Tier B
Entry 3,300 · SL 3,225 · TP 3,450
Why: trade policy + RSI support

🗓 30d
No clean setup.

📈 Accuracy: 45.1% vs 54.5% baseline — strict mode ON
```

**`/why TICKER` format:**
```
CPIN.JK: WATCH → not BUY yet
Event: positive trade policy exposure
Tech: 1/4 confirmations only
Calibration: 1.50x (TRADE_POLICY strong)
Missing: volume confirmation, MACD weak
Action: wait for breakout above 3,350
```

**`/signals` refocus:** Group by horizon instead of flat list.

---

## Task 4.4: Update help text and register commands

**Objective:** Update /help and BOT_COMMANDS to include /daily and /why.

**Files:**
- Modify: `/opt/hermes/polstock_bot/handlers.py` — help text
- Modify: `/opt/hermes/polstock_bot/bot.py` — BOT_COMMANDS list

---

## Task 4.5: Add morning cron job

**Objective:** Daily Telegram push at 08:30 WIB with top signals.

**Files:**
- Create cron job via Hermes cronjob tool

**Schedule:** `30 1 * * *` (UTC) = 08:30 WIB (UTC+7)

**Prompt:** Fetch `/api/signals/daily-summary`, format as Telegram message, send to user.
