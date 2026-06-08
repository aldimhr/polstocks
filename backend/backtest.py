"""Backtest framework — track predicted impact vs actual stock movement.

Records each event→ticker prediction to SQLite, then resolves outcomes
at 1h/4h/24h windows by fetching actual stock prices.

Tables:
  predictions — per-event-per-ticker prediction + outcome
"""

from __future__ import annotations

import hashlib
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
        # Add new columns for robustness signals (safe if already exist)
        for col, typ in [
            ("market_context_factor", "REAL"),
            ("volume_signal", "REAL"),
            ("source_type_count", "INTEGER"),
            ("rsi_value", "REAL"),
            ("rsi_factor", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
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
    market_context_factor: float | None = None,
    volume_signal: float | None = None,
    source_type_count: int | None = None,
    rsi_value: float | None = None,
    rsi_factor: float | None = None,
) -> bool:
    """Insert a prediction. Returns True if inserted, False if duplicate."""
    conn = _get_conn()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO predictions
               (event_id, event_headline, published_at, ticker,
                predicted_direction, predicted_score, significance, confidence,
                relationship_type, categories, source_type, event_stage,
                price_at_event, outcome_status,
                market_context_factor, volume_signal, source_type_count,
                rsi_value, rsi_factor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, event_headline, published_at, ticker,
                predicted_direction, round(predicted_score, 4), significance,
                round(confidence, 4), relationship_type,
                json.dumps(categories or []), source_type, event_stage,
                price_at_event, "pending",
                market_context_factor, volume_signal, source_type_count,
                rsi_value, rsi_factor,
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
                market_context_factor=rel.get("market_context_factor"),
                volume_signal=rel.get("volume_signal"),
                source_type_count=rel.get("corroboration_source_type_count"),
                rsi_value=rel.get("rsi_value"),
                rsi_factor=rel.get("rsi_factor"),
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


# ── Backfill from Cache ───────────────────────────────────────────

def backfill_from_cache() -> dict[str, int]:
    """Backfill predictions and outcomes from cached events + Yahoo Finance history.

    Uses the events_cache table (monolithic JSON blobs) to extract past events,
    then fetches historical stock prices to compute 1h/4h/24h outcomes.
    Only processes events older than 24h (so all outcome windows are available).

    Returns: {"recorded": N, "resolved": N, "skipped": N, "errors": N}
    """
    from backend.stocks import fetch_ticker_history

    stats = {"recorded": 0, "resolved": 0, "skipped": 0, "errors": 0}

    # 1. Load all cached event payloads
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT data FROM events_cache").fetchall()
    finally:
        conn.close()

    if not rows:
        logger.info("Backfill: no cached events found")
        return stats

    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)

    # 2. Extract events from all cache blobs
    all_events: list[dict] = []
    for row in rows:
        try:
            payload = json.loads(row["data"])
            events = payload.get("events", [])
            all_events.extend(events)
        except (json.JSONDecodeError, KeyError):
            continue

    # Deduplicate by event URL
    seen_urls: set[str] = set()
    unique_events: list[dict] = []
    for event in all_events:
        url = event.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_events.append(event)
        elif not url:
            # Use headline as fallback identifier
            headline = event.get("headline", "")
            if headline not in seen_urls:
                seen_urls.add(headline)
                unique_events.append(event)

    logger.info(f"Backfill: found {len(unique_events)} unique cached events")

    # 3. Group tickers by event for batch history fetch
    ticker_set: set[str] = set()
    event_ticker_pairs: list[tuple[dict, str, dict]] = []  # (event, ticker, relationship)

    for event in unique_events:
        pub_str = event.get("published_at", "")
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            continue

        # Only backfill events older than 24h
        if pub_dt > cutoff_24h:
            continue

        for rel in event.get("stock_relationships", []):
            ticker = rel.get("ticker", "")
            if not ticker:
                continue
            ticker_set.add(ticker)
            event_ticker_pairs.append((event, ticker, rel))

    if not event_ticker_pairs:
        logger.info("Backfill: no events older than 24h with stock relationships")
        return stats

    # 4. Fetch historical prices for all tickers (30d window for coverage)
    price_history: dict[str, list[dict]] = {}  # ticker -> [{time, value}, ...]
    for ticker in ticker_set:
        try:
            history = fetch_ticker_history(ticker, window="30d")
            if history and history.get("history"):
                price_history[ticker] = history["history"]
        except Exception as e:
            logger.warning(f"Backfill: failed to fetch history for {ticker}: {e}")
            stats["errors"] += 1

    logger.info(f"Backfill: fetched price history for {len(price_history)}/{len(ticker_set)} tickers")

    # 5. Record predictions and compute outcomes
    for event, ticker, rel in event_ticker_pairs:
        if ticker not in price_history:
            stats["skipped"] += 1
            continue

        # Use URL-based stable ID (not cache-assigned evt_XXX which collides across cache entries)
        event_url = event.get("url", "")
        event_id = hashlib.md5(event_url.encode()).hexdigest()[:12] if event_url else hashlib.md5(event.get("headline", "").encode()).hexdigest()[:12]
        pub_str = event.get("published_at", "")
        try:
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            stats["skipped"] += 1
            continue

        predicted_direction = rel.get("impact_direction", "neutral")
        rel_confidence = float(rel.get("relationship_confidence", 0.0) or 0.0)
        relevance = float(rel.get("relevance_score", 0.0) or 0.0)
        relevance_norm = min(1.0, relevance / 5.0)
        dir_sign = {"positive": 1, "negative": -1}.get(predicted_direction, 0)
        predicted_score = round(dir_sign * relevance_norm * rel_confidence, 4)

        # Find prices at event time and +1h/+4h/+24h
        prices = price_history[ticker]
        price_at = _find_closest_price(prices, pub_dt)
        price_1h = _find_closest_price(prices, pub_dt + timedelta(hours=1))
        price_4h = _find_closest_price(prices, pub_dt + timedelta(hours=4))
        price_24h = _find_closest_price(prices, pub_dt + timedelta(hours=24))

        if not price_at:
            stats["skipped"] += 1
            continue

        # Record prediction (ignore if duplicate)
        record_prediction(
            event_id=event_id,
            event_headline=event.get("headline", ""),
            published_at=pub_str,
            ticker=ticker,
            predicted_direction=predicted_direction,
            predicted_score=predicted_score,
            significance=float(event.get("significance", 0.0) or 0.0),
            confidence=rel_confidence,
            relationship_type=rel.get("relationship_type", ""),
            categories=event.get("categories", []),
            source_type=event.get("source_type", ""),
            event_stage=event.get("event_stage", ""),
            price_at_event=price_at,
        )
        stats["recorded"] += 1

        # Record outcomes if we have the data
        if any([price_1h, price_4h, price_24h]):
            conn2 = _get_conn()
            try:
                row = conn2.execute(
                    "SELECT id FROM predictions WHERE event_id = ? AND ticker = ?",
                    (event_id, ticker),
                ).fetchone()
                if row:
                    record_outcome(
                        row[0],
                        price_after_1h=price_1h,
                        price_after_4h=price_4h,
                        price_after_24h=price_24h,
                    )
                    stats["resolved"] += 1
            finally:
                conn2.close()

    logger.info(
        f"Backfill complete: recorded={stats['recorded']}, resolved={stats['resolved']}, "
        f"skipped={stats['skipped']}, errors={stats['errors']}"
    )
    return stats


def _find_closest_price(history: list[dict], target_dt: datetime, max_delta_minutes: int = 1440) -> float | None:
    """Find the price closest to target_dt in historical data.
    Args:
        history: list of {"time": ISO_string, "value": float} from Yahoo Finance
        target_dt: datetime to find price for
        max_delta_minutes: max acceptable time difference (2h default)

    Returns:
        Closest price within max_delta, or None
    """
    best_price = None
    best_delta = timedelta(minutes=max_delta_minutes + 1)

    for entry in history:
        try:
            raw_time = entry["time"]
            entry_dt = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
            # Strip timezone for comparison (target_dt is naive/UTC)
            entry_dt = entry_dt.replace(tzinfo=None)
        except (ValueError, KeyError, AttributeError, TypeError):
            continue

        delta = abs(entry_dt - target_dt)
        if delta < best_delta:
            best_delta = delta
            best_price = entry.get("value")

    return best_price if best_price and best_price > 0 else None


# ── Weight Recommendations ────────────────────────────────────────

# Current scoring weights (from scoring.py / events.py)
CURRENT_WEIGHTS = {
    "indirect_relationship_multiplier": {"current": 0.82, "location": "scoring.py:411", "description": "Multiplier for indirect stock relationships"},
    "direct_relationship_multiplier": {"current": 1.0, "location": "scoring.py:411", "description": "Multiplier for direct stock relationships"},
    "directional_sentiment_floor": {"current": 0.45, "location": "scoring.py:421", "description": "Min absolute sentiment for positive/negative direction"},
    "mixed_direction_factor": {"current": 0.35, "location": "scoring.py:423", "description": "Sentiment factor for mixed direction"},
    "significance_base": {"current": 0.35, "location": "scoring.py:393", "description": "Base value in significance formula"},
    "significance_multiplier": {"current": 0.45, "location": "scoring.py:393", "description": "Final multiplier in significance formula"},
    "source_quality_blend": {"current": 0.45, "location": "scoring.py:393", "description": "Source quality weight in significance"},
    "confidence_base": {"current": 0.1, "location": "scoring.py:346", "description": "Base confidence value"},
    "sentiment_confidence_weight": {"current": 0.16, "location": "scoring.py:354", "description": "Weight of sentiment confidence in total confidence"},
    "neutral_direction_penalty": {"current": 0.65, "location": "events.py:133", "description": "Confidence penalty for neutral sentiment + directional prediction"},
    "low_confidence_penalty": {"current": 0.80, "location": "events.py:136", "description": "Confidence penalty for low sentiment confidence + directional prediction"},
    "vagueness_downgrade": {"current": 1.0, "location": "events.py:104", "description": "Whether vague government rhetoric is downgraded to neutral (1=yes)"},
}


def suggest_weight_adjustments(min_samples: int = 10) -> dict[str, Any]:
    """Analyze backtest data and suggest weight adjustments.

    Returns suggestions with current value, suggested value, reason, and confidence.
    Only suggests changes with sufficient sample size (min_samples).
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        metrics = compute_accuracy_metrics(window_days=30)
        suggestions = []

        if metrics["with_result"] < min_samples:
            return {
                "ready": False,
                "reason": f"Need {min_samples} resolved predictions, have {metrics['with_result']}",
                "suggestions": [],
            }

        # ── 1. Positive direction accuracy ──
        pos_stats = metrics["by_direction"].get("positive", {})
        if pos_stats.get("total", 0) >= min_samples:
            pos_hr = pos_stats["hit_rate"]
            if pos_hr < 0.4:
                # Positive predictions are unreliable — increase vagueness penalties
                suggestions.append({
                    "weight": "directional_sentiment_floor",
                    "current_value": 0.45,
                    "suggested_value": 0.55,
                    "reason": f"Positive predictions only {pos_hr*100:.0f}% accurate ({pos_stats['correct']}/{pos_stats['total']}). Higher floor reduces false positives from weak sentiment.",
                    "confidence": "high" if pos_stats["total"] >= 20 else "medium",
                    "sample_size": pos_stats["total"],
                })
                suggestions.append({
                    "weight": "indirect_relationship_multiplier",
                    "current_value": 0.82,
                    "suggested_value": 0.70,
                    "reason": f"Positive predictions unreliable at {pos_hr*100:.0f}%. Lower indirect multiplier reduces spurious positive signals.",
                    "confidence": "high" if pos_stats["total"] >= 20 else "medium",
                    "sample_size": pos_stats["total"],
                })
            elif pos_hr > 0.7:
                suggestions.append({
                    "weight": "directional_sentiment_floor",
                    "current_value": 0.45,
                    "suggested_value": 0.38,
                    "reason": f"Positive predictions strong at {pos_hr*100:.0f}%. Lower floor catches more real opportunities.",
                    "confidence": "medium",
                    "sample_size": pos_stats["total"],
                })

        # ── 2. Negative direction accuracy ──
        neg_stats = metrics["by_direction"].get("negative", {})
        if neg_stats.get("total", 0) >= min_samples:
            neg_hr = neg_stats["hit_rate"]
            if neg_hr < 0.4:
                suggestions.append({
                    "weight": "directional_sentiment_floor",
                    "current_value": 0.45,
                    "suggested_value": 0.55,
                    "reason": f"Negative predictions only {neg_hr*100:.0f}% accurate. Higher floor needed.",
                    "confidence": "high" if neg_stats["total"] >= 20 else "medium",
                    "sample_size": neg_stats["total"],
                })

        # ── 3. Confidence calibration ──
        high_conf = metrics["by_confidence"].get("high", {})
        med_conf = metrics["by_confidence"].get("medium", {})
        low_conf = metrics["by_confidence"].get("low", {})

        if low_conf.get("total", 0) >= min_samples and med_conf.get("total", 0) >= min_samples:
            low_hr = low_conf.get("hit_rate", 0)
            med_hr = med_conf.get("hit_rate", 0)
            if low_hr < 0.4 and med_hr > 0.6:
                # Low confidence is noise — increase the penalty
                suggestions.append({
                    "weight": "low_confidence_penalty",
                    "current_value": 0.80,
                    "suggested_value": 0.65,
                    "reason": f"Low confidence predictions {low_hr*100:.0f}% vs medium {med_hr*100:.0f}%. Penalize low confidence more.",
                    "confidence": "high",
                    "sample_size": low_conf["total"] + med_conf["total"],
                })

        # ── 4. Relationship type performance ──
        direct_stats = metrics["by_relationship_type"].get("direct", {})
        indirect_stats = metrics["by_relationship_type"].get("indirect", {})

        if direct_stats.get("total", 0) >= min_samples and indirect_stats.get("total", 0) >= min_samples:
            direct_hr = direct_stats.get("hit_rate", 0)
            indirect_hr = indirect_stats.get("hit_rate", 0)
            if indirect_hr < direct_hr - 0.15:
                # Indirect relationships underperform — reduce their multiplier
                gap = direct_hr - indirect_hr
                new_mult = max(0.5, 0.82 - gap * 0.5)
                suggestions.append({
                    "weight": "indirect_relationship_multiplier",
                    "current_value": 0.82,
                    "suggested_value": round(new_mult, 2),
                    "reason": f"Indirect ({indirect_hr*100:.0f}%) lags direct ({direct_hr*100:.0f}%) by {gap*100:.0f}pp. Reduce indirect multiplier.",
                    "confidence": "high",
                    "sample_size": indirect_stats["total"] + direct_stats["total"],
                })

        # ── 5. Significance bucket performance ──
        high_sig = metrics["by_significance"].get("high", {})
        low_sig = metrics["by_significance"].get("low", {})

        if high_sig.get("total", 0) >= min_samples and low_sig.get("total", 0) >= min_samples:
            high_hr = high_sig.get("hit_rate", 0)
            low_hr = low_sig.get("hit_rate", 0)
            if low_hr > high_hr + 0.1:
                suggestions.append({
                    "weight": "significance_base",
                    "current_value": 0.35,
                    "suggested_value": 0.25,
                    "reason": f"Low significance ({low_hr*100:.0f}%) outperforms high ({high_hr*100:.0f}%). Adjust base to filter more.",
                    "confidence": "medium",
                    "sample_size": low_sig["total"] + high_sig["total"],
                })

        # ── 6. Bias correction ──
        bias = metrics.get("bias", {})
        if bias.get("direction") == "overestimating" and metrics["with_result"] >= 30:
            suggestions.append({
                "weight": "significance_multiplier",
                "current_value": 0.45,
                "suggested_value": 0.35,
                "reason": f"System overestimates impact (predicted {bias['avg_predicted_score']:+.3f} vs actual {bias['avg_actual_return_24h']:+.3f}). Lower significance multiplier.",
                "confidence": "medium",
                "sample_size": metrics["with_result"],
            })
        elif bias.get("direction") == "underestimating" and metrics["with_result"] >= 30:
            suggestions.append({
                "weight": "significance_multiplier",
                "current_value": 0.45,
                "suggested_value": 0.55,
                "reason": f"System underestimates impact. Raise significance multiplier to surface more events.",
                "confidence": "medium",
                "sample_size": metrics["with_result"],
            })

        # ── 7. Category-specific suggestions ──
        for cat in metrics.get("by_category", []):
            if cat["total"] >= min_samples and cat["hit_rate"] < 0.35:
                suggestions.append({
                    "weight": f"category_penalty_{cat['category'].lower()}",
                    "current_value": 1.0,
                    "suggested_value": 0.7,
                    "reason": f"Category '{cat['category']}' only {cat['hit_rate']*100:.0f}% accurate ({cat['correct']}/{cat['total']}). Consider penalty multiplier.",
                    "confidence": "medium",
                    "sample_size": cat["total"],
                })

        return {
            "ready": True,
            "total_predictions": metrics["total_predictions"],
            "resolved": metrics["resolved"],
            "overall_hit_rate": metrics["hit_rate"],
            "suggestion_count": len(suggestions),
            "suggestions": suggestions,
            "current_weights": CURRENT_WEIGHTS,
        }
    finally:
        conn.close()
