"""Backtest framework — track predicted impact vs actual stock movement.

Records each event→ticker prediction to SQLite, then resolves outcomes
at 1h/4h/24h windows by fetching actual stock prices.

Tables:
  predictions — per-event-per-ticker prediction + outcome
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Database ──────────────────────────────────────────────────────

BACKEND_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "polstock_backend.db"
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(BACKEND_DB_PATH), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_backtest_db() -> None:
    """Create backtest tables if they don't exist."""
    conn = _get_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL,
                event_headline TEXT,
                published_at TEXT NOT NULL,
                ticker TEXT NOT NULL,
                predicted_direction TEXT NOT NULL,
                predicted_score REAL NOT NULL,
                significance REAL,
                confidence REAL,
                relationship_type TEXT,
                categories TEXT,
                source_type TEXT,
                event_stage TEXT,
                price_at_event REAL,
                price_after_1h REAL,
                price_after_4h REAL,
                price_after_24h REAL,
                actual_return_1h REAL,
                actual_return_4h REAL,
                actual_return_24h REAL,
                actual_direction TEXT,
                is_correct INTEGER,
                outcome_status TEXT DEFAULT 'pending',
                resolved_at TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                UNIQUE(event_id, ticker)
            );

            CREATE INDEX IF NOT EXISTS idx_predictions_status
                ON predictions(outcome_status);
            CREATE INDEX IF NOT EXISTS idx_predictions_ticker
                ON predictions(ticker);
            CREATE INDEX IF NOT EXISTS idx_predictions_published
                ON predictions(published_at);
        """)
        conn.commit()
    finally:
        conn.close()


# ── Record Predictions ────────────────────────────────────────────

def record_prediction(
    event_id: str,
    event_headline: str,
    published_at: str,
    ticker: str,
    predicted_direction: str,
    predicted_score: float,
    significance: float = 0.0,
    confidence: float = 0.0,
    relationship_type: str = "",
    categories: list[str] | None = None,
    source_type: str = "",
    event_stage: str = "",
    price_at_event: float | None = None,
) -> bool:
    """Insert a prediction. Returns True if inserted, False if duplicate."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO predictions
               (event_id, event_headline, published_at, ticker,
                predicted_direction, predicted_score, significance, confidence,
                relationship_type, categories, source_type, event_stage,
                price_at_event, outcome_status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, event_headline, published_at, ticker,
                predicted_direction, round(predicted_score, 4), significance,
                round(confidence, 4), relationship_type,
                json.dumps(categories or []), source_type, event_stage,
                price_at_event, "pending",
            ),
        )
        conn.commit()
        return conn.total_changes > 0
    except Exception as e:
        logger.warning(f"Failed to record prediction: {e}")
        return False
    finally:
        conn.close()


def record_predictions_from_events(events: list[dict[str, Any]], stock_quotes: dict[str, dict] | None = None) -> int:
    """Record predictions for all event→ticker pairs. Returns count recorded.

    Args:
        events: formatted_events list from build_refresh_payload
        stock_quotes: optional dict of {ticker: {price, ...}} for price_at_event
    """
    count = 0
    for event in events:
        event_id = event.get("id", "")
        headline = event.get("headline", "")
        published_at = event.get("published_at", "")
        significance = event.get("significance", 0.0)
        source_type = event.get("source_type", "")
        event_stage = event.get("event_stage", "")
        categories = event.get("categories", [])

        for rel in event.get("stock_relationships", []):
            ticker = rel.get("ticker", "")
            if not ticker:
                continue

            predicted_direction = rel.get("impact_direction", "neutral")
            # Derive a normalized score from available relationship fields
            rel_confidence = float(rel.get("relationship_confidence", 0.0) or 0.0)
            relevance = float(rel.get("relevance_score", 0.0) or 0.0)
            # relevance_score is 0-5 scale, normalize to 0-1
            relevance_norm = min(1.0, relevance / 5.0)
            # Direction sign
            dir_sign = {"positive": 1, "negative": -1}.get(predicted_direction, 0)
            predicted_score = round(dir_sign * relevance_norm * rel_confidence, 4)

            # Get current price from stock quotes if available
            price_at = None
            if stock_quotes and ticker in stock_quotes:
                price_at = stock_quotes[ticker].get("price")

            ok = record_prediction(
                event_id=event_id,
                event_headline=headline,
                published_at=published_at,
                ticker=ticker,
                predicted_direction=predicted_direction,
                predicted_score=predicted_score,
                significance=significance,
                confidence=rel_confidence,
                relationship_type=rel.get("relationship_type", ""),
                categories=categories,
                source_type=source_type,
                event_stage=event_stage,
                price_at_event=price_at,
            )
            if ok:
                count += 1

    if count > 0:
        logger.info(f"Backtest: recorded {count} new predictions")
    return count


# ── Resolve Outcomes ──────────────────────────────────────────────

def get_pending_predictions() -> list[dict[str, Any]]:
    """Get predictions that need price outcome resolution."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, ticker, published_at, price_at_event, outcome_status,
                      price_after_1h, price_after_4h, price_after_24h
               FROM predictions
               WHERE outcome_status = 'pending'
               ORDER BY published_at ASC
               LIMIT 100"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_outcome(
    prediction_id: int,
    price_after_1h: float | None = None,
    price_after_4h: float | None = None,
    price_after_24h: float | None = None,
) -> None:
    """Update a prediction with actual price outcomes."""
    conn = _get_conn()
    try:
        # Fetch current state
        row = conn.execute(
            """SELECT price_at_event, price_after_1h, price_after_4h, price_after_24h,
                      predicted_direction
               FROM predictions WHERE id = ?""",
            (prediction_id,),
        ).fetchone()
        if not row:
            return

        price_at = row[0]
        existing_1h = row[1] or price_after_1h
        existing_4h = row[2] or price_after_4h
        existing_24h = row[3] or price_after_24h
        pred_dir = row[4]

        # Compute returns
        ret_1h = _compute_return(price_at, existing_1h) if existing_1h else None
        ret_4h = _compute_return(price_at, existing_4h) if existing_4h else None
        ret_24h = _compute_return(price_at, existing_24h) if existing_24h else None

        # Determine actual direction from 24h return (or 4h if 24h not available)
        best_return = ret_24h if ret_24h is not None else (ret_4h if ret_4h is not None else ret_1h)
        actual_dir = _classify_direction(best_return)

        # Check if prediction was correct (direction match, >0.5% threshold)
        is_correct = None
        if actual_dir is not None and pred_dir:
            is_correct = int(_directions_match(pred_dir, actual_dir))

        # Determine if fully resolved
        all_filled = all(r is not None for r in [ret_1h, ret_4h, ret_24h])
        status = "resolved" if all_filled else "pending"

        conn.execute(
            """UPDATE predictions SET
               price_after_1h = COALESCE(?, price_after_1h),
               price_after_4h = COALESCE(?, price_after_4h),
               price_after_24h = COALESCE(?, price_after_24h),
               actual_return_1h = COALESCE(?, actual_return_1h),
               actual_return_4h = COALESCE(?, actual_return_4h),
               actual_return_24h = COALESCE(?, actual_return_24h),
               actual_direction = COALESCE(?, actual_direction),
               is_correct = COALESCE(?, is_correct),
               outcome_status = ?,
               resolved_at = CASE WHEN ? = 'resolved' THEN datetime('now') ELSE resolved_at END
               WHERE id = ?""",
            (
                price_after_1h, price_after_4h, price_after_24h,
                ret_1h, ret_4h, ret_24h,
                actual_dir, is_correct, status, status, prediction_id,
            ),
        )
        conn.commit()
    except Exception as e:
        logger.warning(f"Failed to record outcome for prediction {prediction_id}: {e}")
    finally:
        conn.close()


def _compute_return(price_before: float | None, price_after: float | None) -> float | None:
    """Compute percentage return."""
    if not price_before or not price_after or price_before <= 0:
        return None
    return round((price_after - price_before) / price_before * 100, 4)


def _classify_direction(return_pct: float | None) -> str | None:
    """Classify a return into a direction. 0.5% threshold to filter noise."""
    if return_pct is None:
        return None
    if return_pct > 0.5:
        return "positive"
    if return_pct < -0.5:
        return "negative"
    return "neutral"


def _directions_match(predicted: str, actual: str) -> bool:
    """Check if predicted direction matches actual."""
    return predicted.lower() == actual.lower()


# ── Accuracy Metrics ──────────────────────────────────────────────

def compute_accuracy_metrics(window_days: int = 30) -> dict[str, Any]:
    """Compute backtest accuracy metrics for the given time window."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()

        # Overall stats
        total = conn.execute(
            "SELECT COUNT(*) as n FROM predictions WHERE created_at >= ?", (cutoff,)
        ).fetchone()["n"]

        resolved = conn.execute(
            "SELECT COUNT(*) as n FROM predictions WHERE outcome_status = 'resolved' AND created_at >= ?",
            (cutoff,),
        ).fetchone()["n"]

        with_result = conn.execute(
            "SELECT COUNT(*) as n FROM predictions WHERE is_correct IS NOT NULL AND created_at >= ?",
            (cutoff,),
        ).fetchone()["n"]

        correct = conn.execute(
            "SELECT COUNT(*) as n FROM predictions WHERE is_correct = 1 AND created_at >= ?",
            (cutoff,),
        ).fetchone()["n"]

        hit_rate = correct / with_result if with_result > 0 else 0.0

        # By predicted direction
        direction_stats = {}
        for direction in ["positive", "negative", "neutral"]:
            dir_total = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE predicted_direction = ? AND is_correct IS NOT NULL AND created_at >= ?",
                (direction, cutoff),
            ).fetchone()["n"]
            dir_correct = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE predicted_direction = ? AND is_correct = 1 AND created_at >= ?",
                (direction, cutoff),
            ).fetchone()["n"]
            direction_stats[direction] = {
                "total": dir_total,
                "correct": dir_correct,
                "hit_rate": round(dir_correct / dir_total, 3) if dir_total > 0 else 0.0,
            }

        # By significance bucket
        sig_stats = {}
        for label, lo, hi in [("high", 0.1, 999), ("medium", 0.05, 0.1), ("low", 0.015, 0.05)]:
            bucket_total = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE significance >= ? AND significance < ? AND is_correct IS NOT NULL AND created_at >= ?",
                (lo, hi, cutoff),
            ).fetchone()["n"]
            bucket_correct = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE significance >= ? AND significance < ? AND is_correct = 1 AND created_at >= ?",
                (lo, hi, cutoff),
            ).fetchone()["n"]
            sig_stats[label] = {
                "total": bucket_total,
                "correct": bucket_correct,
                "hit_rate": round(bucket_correct / bucket_total, 3) if bucket_total > 0 else 0.0,
            }

        # By confidence bucket
        conf_stats = {}
        for label, lo, hi in [("high", 0.7, 999), ("medium", 0.4, 0.7), ("low", 0.0, 0.4)]:
            bucket_total = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE confidence >= ? AND confidence < ? AND is_correct IS NOT NULL AND created_at >= ?",
                (lo, hi, cutoff),
            ).fetchone()["n"]
            bucket_correct = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE confidence >= ? AND confidence < ? AND is_correct = 1 AND created_at >= ?",
                (lo, hi, cutoff),
            ).fetchone()["n"]
            conf_stats[label] = {
                "total": bucket_total,
                "correct": bucket_correct,
                "hit_rate": round(bucket_correct / bucket_total, 3) if bucket_total > 0 else 0.0,
            }

        # By relationship type
        rel_stats = {}
        for rel_type in ["direct", "indirect"]:
            type_total = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE relationship_type = ? AND is_correct IS NOT NULL AND created_at >= ?",
                (rel_type, cutoff),
            ).fetchone()["n"]
            type_correct = conn.execute(
                "SELECT COUNT(*) as n FROM predictions WHERE relationship_type = ? AND is_correct = 1 AND created_at >= ?",
                (rel_type, cutoff),
            ).fetchone()["n"]
            rel_stats[rel_type] = {
                "total": type_total,
                "correct": type_correct,
                "hit_rate": round(type_correct / type_total, 3) if type_total > 0 else 0.0,
            }

        # Bias: avg predicted score vs avg actual return
        bias_row = conn.execute(
            """SELECT AVG(predicted_score) as avg_pred, AVG(actual_return_24h) as avg_actual
               FROM predictions WHERE is_correct IS NOT NULL AND created_at >= ?""",
            (cutoff,),
        ).fetchone()
        avg_predicted = round(bias_row["avg_pred"] or 0, 4)
        avg_actual = round(bias_row["avg_actual"] or 0, 4)

        # By category (top 5)
        cat_stats = []
        rows = conn.execute(
            """SELECT categories, COUNT(*) as n, SUM(is_correct) as hits
               FROM predictions WHERE is_correct IS NOT NULL AND created_at >= ?
               GROUP BY categories ORDER BY n DESC LIMIT 10""",
            (cutoff,),
        ).fetchall()
        for row in rows:
            cats = json.loads(row["categories"]) if row["categories"] else []
            for cat in cats:
                cat_stats.append({
                    "category": cat,
                    "total": row["n"],
                    "correct": row["hits"] or 0,
                    "hit_rate": round((row["hits"] or 0) / row["n"], 3) if row["n"] > 0 else 0.0,
                })

        return {
            "window_days": window_days,
            "total_predictions": total,
            "resolved": resolved,
            "pending": total - resolved,
            "with_result": with_result,
            "correct": correct,
            "hit_rate": round(hit_rate, 3),
            "by_direction": direction_stats,
            "by_significance": sig_stats,
            "by_confidence": conf_stats,
            "by_relationship_type": rel_stats,
            "bias": {
                "avg_predicted_score": avg_predicted,
                "avg_actual_return_24h": avg_actual,
                "direction": "overestimating" if avg_predicted > avg_actual + 0.1 else "underestimating" if avg_predicted < avg_actual - 0.1 else "balanced",
            },
            "by_category": cat_stats[:5],
            "statistical_significance": with_result >= 30,
        }
    finally:
        conn.close()


# ── Background Outcome Resolution ─────────────────────────────────

_resolution_thread: threading.Thread | None = None
_resolution_lock = threading.Lock()


def start_outcome_resolver(interval_seconds: int = 3600) -> None:
    """Start background thread that resolves pending predictions every interval."""
    global _resolution_thread
    with _resolution_lock:
        if _resolution_thread and _resolution_thread.is_alive():
            return

        def _resolver_loop():
            logger.info(f"Backtest outcome resolver started (interval={interval_seconds}s)")
            while True:
                try:
                    time.sleep(interval_seconds)
                    resolve_pending_outcomes()
                except Exception as e:
                    logger.warning(f"Backtest resolver error: {e}")

        _resolution_thread = threading.Thread(target=_resolver_loop, daemon=True, name="backtest-resolver")
        _resolution_thread.start()


def resolve_pending_outcomes() -> int:
    """Fetch current prices for pending predictions and record outcomes."""
    from backend.stocks import fetch_live_quote

    pending = get_pending_predictions()
    if not pending:
        return 0

    resolved_count = 0
    # Group by ticker to minimize API calls
    tickers_needed = set()
    now = datetime.utcnow()

    for pred in pending:
        try:
            pub_dt = datetime.fromisoformat(pred["published_at"].replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            continue

        age_hours = (now - pub_dt).total_seconds() / 3600

        # Only resolve if enough time has passed
        if age_hours < 1.0:
            continue

        tickers_needed.add(pred["ticker"])

    if not tickers_needed:
        return 0

    # Fetch current prices
    prices: dict[str, float] = {}
    for ticker in tickers_needed:
        try:
            quote = fetch_live_quote(ticker)
            if quote and quote.get("price"):
                prices[ticker] = quote["price"]
        except Exception as e:
            logger.warning(f"Failed to fetch price for {ticker}: {e}")

    # Update predictions
    for pred in pending:
        ticker = pred["ticker"]
        if ticker not in prices:
            continue

        try:
            pub_dt = datetime.fromisoformat(pred["published_at"].replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            continue

        age_hours = (now - pub_dt).total_seconds() / 3600
        current_price = prices[ticker]

        kwargs = {}
        if age_hours >= 1.0 and not pred.get("price_after_1h"):
            kwargs["price_after_1h"] = current_price
        if age_hours >= 4.0 and not pred.get("price_after_4h"):
            kwargs["price_after_4h"] = current_price
        if age_hours >= 24.0 and not pred.get("price_after_24h"):
            kwargs["price_after_24h"] = current_price

        if kwargs:
            record_outcome(pred["id"], **kwargs)
            resolved_count += 1

    if resolved_count > 0:
        logger.info(f"Backtest: resolved {resolved_count} prediction outcomes")
    return resolved_count
