"""Telegram push alerts for trade signals.

Formats BUY/SELL signals into human-readable Telegram messages and sends
them via the Bot API. Respects alert_prefs (quiet hours, min impact, categories).
Deduplicates: same ticker+action within 24h = no re-alert.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

from backend.config import BACKEND_DB_PATH

# Dedup tracking — same ticker+action within this window = skip
ALERT_DEDUP_HOURS = 24

# Telegram Bot API base
TELEGRAM_API = "https://api.telegram.org"


def _get_conn() -> sqlite3.Connection:
    BACKEND_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKEND_DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_alert_tables() -> None:
    """Create alert_dedup table if it doesn't exist."""
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_dedup (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                action TEXT NOT NULL,
                sent_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alert_dedup_ticker_action ON alert_dedup(ticker, action)")
        conn.commit()
    finally:
        conn.close()


def format_signal_alert(signal: dict[str, Any]) -> str:
    """Format a trade signal into a Telegram-friendly message.

    Args:
        signal: dict with ticker, action, signal_strength, price_at_signal,
                stop_loss, take_profit, risk_reward, timeframe, reasons,
                event_headline, event_source

    Returns formatted message string.
    """
    action = signal.get("action", "HOLD")
    ticker = signal.get("ticker", "???")
    strength = signal.get("signal_strength", 0)
    price = signal.get("price_at_signal", 0)
    sl = signal.get("stop_loss")
    tp = signal.get("take_profit")
    rr = signal.get("risk_reward")
    tf = signal.get("timeframe", "?")
    reasons = signal.get("reasons", [])
    headline = signal.get("event_headline", "")
    source = signal.get("event_source", "")

    # Star rating (1-5)
    stars = min(5, max(1, int(strength * 5)))
    star_str = "★" * stars + "☆" * (5 - stars)

    # Action icon
    icon = "🟢" if action == "BUY" else "🔴"

    lines = [
        f"{icon} *{action} SIGNAL: {ticker}*",
        f"Strength: {star_str} ({strength:.2f})",
        f"Entry: {price:,.0f}",
    ]

    if sl and tp:
        lines.append(f"SL: {sl:,.0f} | TP: {tp:,.0f}")
    if rr:
        lines.append(f"Risk/Reward: 1:{rr:.1f}")
    lines.append(f"Timeframe: {tf}")

    if headline:
        # Truncate long headlines
        hl = headline[:100] + ("..." if len(headline) > 100 else "")
        lines.append(f"📰 {hl}")
    if source:
        lines.append(f"📡 {source}")

    if reasons:
        lines.append("")
        lines.append("Reasons:")
        for r in reasons[:5]:  # max 5 reasons
            lines.append(f"  • {r}")

    return "\n".join(lines)


def _is_quiet_hours(alert_prefs: dict[str, Any] | None) -> bool:
    """Check if current time is within quiet hours."""
    if not alert_prefs:
        return False
    start = alert_prefs.get("alert_quiet_start", -1)
    end = alert_prefs.get("alert_quiet_end", -1)
    if start < 0 or end < 0:
        return False
    now_hour = datetime.utcnow().hour
    if start <= end:
        return start <= now_hour < end
    else:  # wraps midnight
        return now_hour >= start or now_hour < end


def _is_deduped(ticker: str, action: str) -> bool:
    """Check if we already sent an alert for this ticker+action recently."""
    _init_alert_tables()
    conn = _get_conn()
    try:
        from datetime import timedelta
        cutoff_dt = datetime.utcnow() - timedelta(hours=ALERT_DEDUP_HOURS)
        cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
        row = conn.execute(
            "SELECT id FROM alert_dedup WHERE ticker = ? AND action = ? AND sent_at > ? LIMIT 1",
            (ticker, action, cutoff),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _record_alert_sent(ticker: str, action: str) -> None:
    """Record that an alert was sent for dedup tracking."""
    _init_alert_tables()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO alert_dedup (ticker, action) VALUES (?, ?)",
            (ticker, action),
        )
        conn.commit()
    finally:
        conn.close()


def should_send_alert(
    signal: dict[str, Any],
    alert_prefs: dict[str, Any] | None = None,
) -> bool:
    """Determine if an alert should be sent for this signal.

    Checks:
    - Action is BUY or SELL (not HOLD)
    - Signal strength >= 0.6
    - Not in quiet hours
    - Not deduped (same ticker+action within 24h)
    - Min impact threshold from alert_prefs
    """
    action = signal.get("action")
    if action not in ("BUY", "SELL"):
        return False

    strength = signal.get("signal_strength", 0)
    if strength < 0.6:
        return False

    if _is_quiet_hours(alert_prefs):
        return False

    ticker = signal.get("ticker", "")
    if _is_deduped(ticker, action):
        return False

    # Check min impact from alert_prefs
    if alert_prefs and alert_prefs.get("alert_min_impact", 0) > 0:
        if strength < alert_prefs["alert_min_impact"]:
            return False

    return True


def send_telegram_alert(message: str) -> bool:
    """Send a message via Telegram Bot API.

    Uses POLSTOCK_TELEGRAM_BOT_TOKEN and POLSTOCK_TELEGRAM_CHAT_ID env vars.
    Returns True if sent successfully.
    """
    token = os.environ.get("POLSTOCK_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("POLSTOCK_TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("Telegram alert not sent: POLSTOCK_TELEGRAM_BOT_TOKEN or POLSTOCK_TELEGRAM_CHAT_ID not set")
        return False

    try:
        resp = requests.post(
            f"{TELEGRAM_API}/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"Telegram alert sent to {chat_id}")
            return True
        else:
            logger.warning(f"Telegram alert failed: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.warning(f"Telegram alert error: {e}")
        return False


def get_alert_prefs(user_id: int | None = None) -> dict[str, Any] | None:
    """Read alert preferences from the bot's SQLite database."""
    bot_db = os.environ.get("POLSTOCK_BOT_DB_PATH", "")
    if not bot_db or not Path(bot_db).exists():
        return None

    conn = sqlite3.connect(bot_db, timeout=10)
    try:
        uid = user_id or 519613720  # default admin user
        row = conn.execute(
            "SELECT alert_min_impact, alert_categories, alert_quiet_start, alert_quiet_end FROM users WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if not row:
            return None
        cats = []
        if row[1]:
            try:
                cats = json.loads(row[1])
            except (json.JSONDecodeError, TypeError):
                pass
        return {
            "alert_min_impact": row[0] or 0,
            "alert_categories": cats,
            "alert_quiet_start": row[2] if row[2] is not None else -1,
            "alert_quiet_end": row[3] if row[3] is not None else -1,
        }
    finally:
        conn.close()


def check_and_alert(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check a list of trade signals and send alerts for qualifying ones.

    Args:
        signals: list of signal dicts from build_refresh_payload

    Returns list of alerts that were sent.
    """
    alert_prefs = get_alert_prefs()
    sent: list[dict[str, Any]] = []

    for sig in signals:
        if not should_send_alert(sig, alert_prefs):
            continue

        message = format_signal_alert(sig)
        if send_telegram_alert(message):
            _record_alert_sent(sig["ticker"], sig["action"])
            sent.append({"ticker": sig["ticker"], "action": sig["action"], "sent": True})

    return sent
