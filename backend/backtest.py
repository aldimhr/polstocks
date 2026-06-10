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
    BACKEND_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(BACKEND_DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
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

            CREATE TABLE IF NOT EXISTS historical_events (
                article_id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                url TEXT,
                headline TEXT NOT NULL,
                summary TEXT,
                published_at TEXT NOT NULL,
                timestamp_confidence REAL NOT NULL,
                provenance_json TEXT NOT NULL,
                import_status TEXT NOT NULL DEFAULT 'accepted',
                rejection_reason TEXT,
                raw_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_historical_events_published
                ON historical_events(published_at);
            CREATE INDEX IF NOT EXISTS idx_historical_events_source
                ON historical_events(source);
        """)
        # Add new columns for robustness signals (safe if already exist)
        for col, typ in [
            ("market_context_factor", "REAL"),
            ("volume_signal", "REAL"),
            ("source_type_count", "INTEGER"),
            ("rsi_value", "REAL"),
            ("rsi_factor", "REAL"),
            ("macd_histogram", "REAL"),
            ("macd_factor", "REAL"),
            ("sma_trend", "TEXT"),
            ("trend_factor", "REAL"),
            ("event_cluster_count", "INTEGER"),
            ("event_cluster_factor", "REAL"),
            ("atr_value", "REAL"),
            ("atr_pct", "REAL"),
            ("atr_factor", "REAL"),
            ("sector_correlation_count", "INTEGER"),
            ("sector_correlation_factor", "REAL"),
            ("foreign_market_factor", "REAL"),
            ("sentiment_momentum", "TEXT"),
            ("sentiment_momentum_factor", "REAL"),
            ("currency_factor", "REAL"),
            ("prediction_origin", "TEXT DEFAULT 'live'"),
            ("source_article_id", "TEXT"),
            ("time_horizon", "TEXT DEFAULT '7d'"),
            ("signal_tier", "TEXT DEFAULT 'D'"),
            ("signal_type", "TEXT DEFAULT 'event'"),
            ("event_score", "REAL DEFAULT 0"),
            ("tech_score", "REAL DEFAULT 0"),
            ("tech_confirmation_count", "INTEGER DEFAULT 0"),
            ("return_7d", "REAL"),
            ("return_30d", "REAL"),
            ("outcome_7d", "TEXT"),
            ("outcome_30d", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE predictions ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_accuracy (
                source_id TEXT PRIMARY KEY,
                total_predictions INTEGER DEFAULT 0,
                correct_predictions INTEGER DEFAULT 0,
                hit_rate REAL DEFAULT 0.5,
                calibration_multiplier REAL DEFAULT 1.0,
                last_updated TEXT
            );
        """)
        conn.commit()
    finally:
        conn.close()



# ── Historical Backfill Import Safeguards ──────────────────────────

def _parse_historical_timestamp(value: str | None) -> tuple[datetime | None, float, str]:
    if not value or not str(value).strip():
        return None, 0.0, "missing_timestamp"

    raw = str(value).strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        try:
            return datetime.fromisoformat(raw), 0.4, "date_only"
        except ValueError:
            return None, 0.0, "invalid_timestamp"

    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None, 0.0, "invalid_timestamp"

    has_timezone = parsed.tzinfo is not None
    confidence = 1.0 if has_timezone else 0.75
    reason = "exact_with_timezone" if has_timezone else "exact_without_timezone"
    return parsed, confidence, reason


def _historical_article_id(source: str, url: str, headline: str) -> str:
    identity = url.strip().lower() or f"{source.strip().lower()}::{headline.strip().lower()}"
    return "hist_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def normalize_historical_article(article: dict[str, Any], *, min_timestamp_confidence: float = 0.8) -> dict[str, Any]:
    """Normalize one historical article and reject weak timestamps.

    This is intentionally conservative: v1 imports historical internet data into
    a staging table only when provenance and timestamps are strong enough to
    avoid misleading backtest accuracy.
    """
    source = str(article.get("source") or article.get("source_name") or "").strip()
    headline = str(article.get("headline") or article.get("title") or "").strip()
    url = str(article.get("url") or "").strip()
    summary = str(article.get("summary") or article.get("body") or "").strip()

    published_at, timestamp_confidence, timestamp_reason = _parse_historical_timestamp(
        article.get("published_at") or article.get("published") or article.get("date")
    )

    provenance = dict(article.get("provenance") or {})
    provenance.update({
        "source": source,
        "url": url,
        "timestamp_reason": timestamp_reason,
        "historical_backfill_version": 1,
    })

    rejection_reason = ""
    if not source:
        rejection_reason = "missing_source"
    elif not headline:
        rejection_reason = "missing_headline"
    elif not published_at or timestamp_confidence < min_timestamp_confidence:
        rejection_reason = "low_timestamp_confidence"

    accepted = not rejection_reason
    article_id = _historical_article_id(source, url, headline)
    return {
        "accepted": accepted,
        "article_id": article_id,
        "source": source,
        "url": url,
        "headline": headline,
        "summary": summary,
        "published_at": published_at.isoformat() if published_at else "",
        "timestamp_confidence": round(timestamp_confidence, 3),
        "provenance": provenance,
        "rejection_reason": rejection_reason,
        "raw": article,
    }


def import_historical_articles(
    articles: list[dict[str, Any]],
    *,
    dry_run: bool = True,
    min_timestamp_confidence: float = 0.8,
) -> dict[str, Any]:
    """Validate/import historical articles into the staging table.

    Dry-run is the default so broad internet backfills can be audited before any
    database write. Accepted rows are not replayed into predictions here; replay
    is a separate step after timestamp/provenance review.
    """
    normalized = [
        normalize_historical_article(item, min_timestamp_confidence=min_timestamp_confidence)
        for item in articles
    ]
    accepted_items = [item for item in normalized if item["accepted"]]
    rejected_items = [item for item in normalized if not item["accepted"]]
    inserted = 0
    duplicates = 0

    if not dry_run and accepted_items:
        init_backtest_db()
        conn = _get_conn()
        try:
            for item in accepted_items:
                before = conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO historical_events
                       (article_id, source, url, headline, summary, published_at,
                        timestamp_confidence, provenance_json, import_status,
                        rejection_reason, raw_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item["article_id"], item["source"], item["url"],
                        item["headline"], item["summary"], item["published_at"],
                        item["timestamp_confidence"], json.dumps(item["provenance"], ensure_ascii=False),
                        "accepted", "", json.dumps(item["raw"], ensure_ascii=False),
                    ),
                )
                if conn.total_changes > before:
                    inserted += 1
                else:
                    duplicates += 1
            conn.commit()
        finally:
            conn.close()

    return {
        "dry_run": dry_run,
        "total": len(articles),
        "accepted": len(accepted_items),
        "rejected": len(rejected_items),
        "inserted": inserted,
        "duplicates": duplicates,
        "items": normalized,
    }


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
    macd_histogram: float | None = None,
    macd_factor: float | None = None,
    sma_trend: str | None = None,
    trend_factor: float | None = None,
    event_cluster_count: int | None = None,
    event_cluster_factor: float | None = None,
    atr_value: float | None = None,
    atr_pct: float | None = None,
    atr_factor: float | None = None,
    sector_correlation_count: int | None = None,
    sector_correlation_factor: float | None = None,
    foreign_market_factor: float | None = None,
    sentiment_momentum: str | None = None,
    sentiment_momentum_factor: float | None = None,
    currency_factor: float | None = None,
    prediction_origin: str = "live",
    source_article_id: str | None = None,
    time_horizon: str | None = None,
    signal_tier: str | None = None,
    signal_type: str | None = None,
    event_score: float | None = None,
    tech_score: float | None = None,
    tech_confirmation_count: int | None = None,
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
                rsi_value, rsi_factor,
                macd_histogram, macd_factor, sma_trend, trend_factor,
                event_cluster_count, event_cluster_factor,
                atr_value, atr_pct, atr_factor,
                sector_correlation_count, sector_correlation_factor,
                foreign_market_factor, sentiment_momentum, sentiment_momentum_factor,
                currency_factor, prediction_origin, source_article_id,
                time_horizon, signal_tier, signal_type, event_score, tech_score, tech_confirmation_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id, event_headline, published_at, ticker,
                predicted_direction, round(predicted_score, 4), significance,
                round(confidence, 4), relationship_type,
                json.dumps(categories or []), source_type, event_stage,
                price_at_event, "pending",
                market_context_factor, volume_signal, source_type_count,
                rsi_value, rsi_factor,
                macd_histogram, macd_factor, sma_trend, trend_factor,
                event_cluster_count, event_cluster_factor,
                atr_value, atr_pct, atr_factor,
                sector_correlation_count, sector_correlation_factor,
                foreign_market_factor, sentiment_momentum, sentiment_momentum_factor,
                currency_factor, prediction_origin, source_article_id,
                time_horizon or '7d', signal_tier or 'D', signal_type or 'event',
                event_score or 0, tech_score or 0, tech_confirmation_count or 0,
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
                macd_histogram=rel.get("macd_histogram"),
                macd_factor=rel.get("macd_factor"),
                sma_trend=rel.get("sma_trend"),
                trend_factor=rel.get("trend_factor"),
                event_cluster_count=rel.get("event_cluster_count"),
                event_cluster_factor=rel.get("event_cluster_factor"),
                atr_value=rel.get("atr_value"),
                atr_pct=rel.get("atr_pct"),
                atr_factor=rel.get("atr_factor"),
                sector_correlation_count=rel.get("sector_correlation_count"),
                sector_correlation_factor=rel.get("sector_correlation_factor"),
                foreign_market_factor=rel.get("foreign_market_factor"),
                sentiment_momentum=rel.get("sentiment_momentum"),
                sentiment_momentum_factor=rel.get("sentiment_momentum_factor"),
                currency_factor=rel.get("currency_factor"),
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

def compute_accuracy_metrics(window_days: int = 30, origin: str = "all") -> dict[str, Any]:
    """Compute backtest accuracy metrics for the given time window.

    origin='all' includes every prediction. origin='live' excludes future
    historical replay rows, keeping live forward-test quality separate from
    internet backfills.
    """
    init_backtest_db()
    allowed_origins = {"all", "live", "historical_backfill", "cache_backfill"}
    if origin not in allowed_origins:
        origin = "all"

    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
        where = "created_at >= ?"
        base_params: tuple[Any, ...] = (cutoff,)
        if origin != "all":
            where += " AND COALESCE(prediction_origin, 'live') = ?"
            base_params = (cutoff, origin)

        def count_where(extra: str = "", params: tuple[Any, ...] = ()) -> int:
            prefix = f"{extra} AND " if extra else ""
            return conn.execute(
                f"SELECT COUNT(*) as n FROM predictions WHERE {prefix}{where}",
                (*params, *base_params),
            ).fetchone()["n"]

        total = count_where()
        resolved = count_where("outcome_status = 'resolved'")
        with_result = count_where("is_correct IS NOT NULL")
        correct = count_where("is_correct = 1")
        hit_rate = correct / with_result if with_result > 0 else 0.0

        direction_stats = {}
        for direction in ["positive", "negative", "neutral"]:
            dir_total = count_where("predicted_direction = ? AND is_correct IS NOT NULL", (direction,))
            dir_correct = count_where("predicted_direction = ? AND is_correct = 1", (direction,))
            direction_stats[direction] = {
                "total": dir_total,
                "correct": dir_correct,
                "hit_rate": round(dir_correct / dir_total, 3) if dir_total > 0 else 0.0,
            }

        sig_stats = {}
        for label, lo, hi in [("high", 0.1, 999), ("medium", 0.05, 0.1), ("low", 0.015, 0.05)]:
            bucket_total = count_where("significance >= ? AND significance < ? AND is_correct IS NOT NULL", (lo, hi))
            bucket_correct = count_where("significance >= ? AND significance < ? AND is_correct = 1", (lo, hi))
            sig_stats[label] = {
                "total": bucket_total,
                "correct": bucket_correct,
                "hit_rate": round(bucket_correct / bucket_total, 3) if bucket_total > 0 else 0.0,
            }

        conf_stats = {}
        for label, lo, hi in [("high", 0.7, 999), ("medium", 0.4, 0.7), ("low", 0.0, 0.4)]:
            bucket_total = count_where("confidence >= ? AND confidence < ? AND is_correct IS NOT NULL", (lo, hi))
            bucket_correct = count_where("confidence >= ? AND confidence < ? AND is_correct = 1", (lo, hi))
            conf_stats[label] = {
                "total": bucket_total,
                "correct": bucket_correct,
                "hit_rate": round(bucket_correct / bucket_total, 3) if bucket_total > 0 else 0.0,
            }

        rel_stats = {}
        for rel_type in ["direct", "indirect"]:
            type_total = count_where("relationship_type = ? AND is_correct IS NOT NULL", (rel_type,))
            type_correct = count_where("relationship_type = ? AND is_correct = 1", (rel_type,))
            rel_stats[rel_type] = {
                "total": type_total,
                "correct": type_correct,
                "hit_rate": round(type_correct / type_total, 3) if type_total > 0 else 0.0,
            }

        neutral_correct = count_where("actual_direction = 'neutral' AND is_correct IS NOT NULL")
        neutral_baseline = neutral_correct / with_result if with_result > 0 else 0.0

        weighted_row = conn.execute(
            f"""SELECT
                    SUM(CASE WHEN is_correct = 1 THEN ABS(COALESCE(actual_return_24h, 0)) ELSE 0 END) AS weighted_correct,
                    SUM(ABS(COALESCE(actual_return_24h, 0))) AS weighted_total,
                    AVG(CASE WHEN predicted_direction = 'positive' THEN actual_return_24h END) AS avg_positive_return,
                    AVG(CASE WHEN predicted_direction = 'negative' THEN actual_return_24h END) AS avg_negative_return
                FROM predictions
                WHERE is_correct IS NOT NULL AND {where}""",
            base_params,
        ).fetchone()
        weighted_total = float(weighted_row["weighted_total"] or 0.0)
        weighted_correct = float(weighted_row["weighted_correct"] or 0.0)
        return_weighted_accuracy = weighted_correct / weighted_total if weighted_total > 0 else 0.0

        origin_rows = conn.execute(
            """SELECT COALESCE(prediction_origin, 'live') AS origin,
                       COUNT(*) AS total,
                       SUM(CASE WHEN outcome_status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                       SUM(CASE WHEN is_correct IS NOT NULL THEN 1 ELSE 0 END) AS with_result,
                       SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
                FROM predictions
                WHERE created_at >= ?
                GROUP BY COALESCE(prediction_origin, 'live')
                ORDER BY total DESC""",
            (cutoff,),
        ).fetchall()
        by_origin = {}
        for row in origin_rows:
            row_with_result = int(row["with_result"] or 0)
            row_correct = int(row["correct"] or 0)
            by_origin[row["origin"]] = {
                "total": int(row["total"] or 0),
                "resolved": int(row["resolved"] or 0),
                "with_result": row_with_result,
                "correct": row_correct,
                "hit_rate": round(row_correct / row_with_result, 3) if row_with_result else 0.0,
            }

        # Signal type breakdown (new Phase 2 column)
        sig_type_stats = {}
        try:
            for stype in ["event", "technical", "composite"]:
                st_total = count_where("COALESCE(signal_type, 'event') = ? AND is_correct IS NOT NULL", (stype,))
                st_correct = count_where("COALESCE(signal_type, 'event') = ? AND is_correct = 1", (stype,))
                sig_type_stats[stype] = {
                    "total": st_total,
                    "correct": st_correct,
                    "hit_rate": round(st_correct / st_total, 3) if st_total > 0 else 0.0,
                }
        except Exception:
            pass  # signal_type column may not exist yet

        # Horizon breakdown
        horizon_stats = {}
        try:
            for horizon in ["1d", "7d", "30d"]:
                h_total = count_where("COALESCE(time_horizon, '7d') = ? AND is_correct IS NOT NULL", (horizon,))
                h_correct = count_where("COALESCE(time_horizon, '7d') = ? AND is_correct = 1", (horizon,))
                horizon_stats[horizon] = {
                    "total": h_total,
                    "correct": h_correct,
                    "hit_rate": round(h_correct / h_total, 3) if h_total > 0 else 0.0,
                }
        except Exception:
            pass

        bias_row = conn.execute(
            f"""SELECT AVG(predicted_score) as avg_pred, AVG(actual_return_24h) as avg_actual
               FROM predictions WHERE is_correct IS NOT NULL AND {where}""",
            base_params,
        ).fetchone()
        avg_predicted = round(bias_row["avg_pred"] or 0, 4)
        avg_actual = round(bias_row["avg_actual"] or 0, 4)

        cat_stats = []
        rows = conn.execute(
            f"""SELECT categories, COUNT(*) as n, SUM(is_correct) as hits
               FROM predictions WHERE is_correct IS NOT NULL AND {where}
               GROUP BY categories ORDER BY n DESC LIMIT 10""",
            base_params,
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
            "origin": origin,
            "total_predictions": total,
            "resolved": resolved,
            "pending": total - resolved,
            "with_result": with_result,
            "correct": correct,
            "hit_rate": round(hit_rate, 3),
            "baseline": {
                "neutral_hit_rate": round(neutral_baseline, 3),
                "edge_vs_neutral": round(hit_rate - neutral_baseline, 3),
            },
            "return_weighted": {
                "accuracy": round(return_weighted_accuracy, 3),
                "weighted_correct_return": round(weighted_correct, 4),
                "weighted_total_abs_return": round(weighted_total, 4),
                "avg_return_when_predicted_positive": round(weighted_row["avg_positive_return"] or 0, 4),
                "avg_return_when_predicted_negative": round(weighted_row["avg_negative_return"] or 0, 4),
            },
            "by_origin": by_origin,
            "by_signal_type": sig_type_stats,
            "by_time_horizon": horizon_stats,
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


def compute_source_accuracy(window_days: int = 30, min_samples: int = 5) -> dict[str, dict[str, Any]]:
    """Compute per-source prediction accuracy from live predictions only.

    Returns dict: source_type -> {total, correct, hit_rate, calibration_multiplier}
    Only includes sources with >= min_samples predictions.
    """
    init_backtest_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
        rows = conn.execute(
            """SELECT COALESCE(source_type, 'unknown') AS source_type,
                      COUNT(*) AS total,
                      SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
               FROM predictions
               WHERE created_at >= ?
                 AND COALESCE(prediction_origin, 'live') = 'live'
                 AND is_correct IS NOT NULL
               GROUP BY COALESCE(source_type, 'unknown')
               HAVING COUNT(*) >= ?
               ORDER BY total DESC""",
            (cutoff, min_samples),
        ).fetchall()

        # Overall live hit rate for calibration baseline
        overall_row = conn.execute(
            """SELECT COUNT(*) AS total, SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
               FROM predictions
               WHERE created_at >= ?
                 AND COALESCE(prediction_origin, 'live') = 'live'
                 AND is_correct IS NOT NULL""",
            (cutoff,),
        ).fetchone()
        overall_hit_rate = (overall_row["correct"] or 0) / overall_row["total"] if overall_row["total"] else 0.5

        result = {}
        for row in rows:
            total = int(row["total"])
            correct = int(row["correct"] or 0)
            hit_rate = correct / total if total > 0 else 0.5
            # Calibration: how much better/worse than overall, clamped
            calibration = max(0.5, min(1.5, hit_rate / overall_hit_rate)) if overall_hit_rate > 0 else 1.0
            result[row["source_type"]] = {
                "total": total,
                "correct": correct,
                "hit_rate": round(hit_rate, 3),
                "calibration_multiplier": round(calibration, 3),
            }
        return result
    finally:
        conn.close()


def compute_category_calibration(window_days: int = 30, min_samples: int = 5) -> dict[str, dict[str, Any]]:
    """Compute per-category calibration multipliers from live predictions.

    Returns dict: category -> {total, correct, hit_rate, calibration_multiplier}
    Only includes categories with >= min_samples predictions.
    """
    init_backtest_db()
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
        rows = conn.execute(
            """SELECT categories, COUNT(*) AS total,
                      SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
               FROM predictions
               WHERE created_at >= ?
                 AND COALESCE(prediction_origin, 'live') = 'live'
                 AND is_correct IS NOT NULL
               GROUP BY categories
               HAVING COUNT(*) >= ?
               ORDER BY total DESC""",
            (cutoff, min_samples),
        ).fetchall()

        overall_row = conn.execute(
            """SELECT COUNT(*) AS total, SUM(CASE WHEN is_correct = 1 THEN 1 ELSE 0 END) AS correct
               FROM predictions
               WHERE created_at >= ?
                 AND COALESCE(prediction_origin, 'live') = 'live'
                 AND is_correct IS NOT NULL""",
            (cutoff,),
        ).fetchone()
        overall_hit_rate = (overall_row["correct"] or 0) / overall_row["total"] if overall_row["total"] else 0.5

        result = {}
        for row in rows:
            cats = json.loads(row["categories"]) if row["categories"] else []
            total = int(row["total"])
            correct = int(row["correct"] or 0)
            hit_rate = correct / total if total > 0 else 0.5
            calibration = max(0.5, min(1.5, hit_rate / overall_hit_rate)) if overall_hit_rate > 0 else 1.0
            for cat in cats:
                if cat not in result:  # deduplicate if category appears in multiple rows
                    result[cat] = {
                        "total": total,
                        "correct": correct,
                        "hit_rate": round(hit_rate, 3),
                        "calibration_multiplier": round(calibration, 3),
                    }
        return result
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
    """Fetch current prices for pending predictions and record outcomes.

    Uses historical OHLC data to get the actual price at each hour mark
    (1h, 4h, 24h after publication), not the current price.
    """
    from backend.stocks import fetch_ticker_history

    pending = get_pending_predictions()
    if not pending:
        return 0

    resolved_count = 0
    now = datetime.utcnow()

    # Group by ticker to minimize API calls
    tickers_needed: set[str] = set()
    for pred in pending:
        try:
            pub_dt = datetime.fromisoformat(pred["published_at"].replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            continue
        age_hours = (now - pub_dt).total_seconds() / 3600
        if age_hours < 1.0:
            continue
        tickers_needed.add(pred["ticker"])

    if not tickers_needed:
        return 0

    # Fetch historical OHLC data for all tickers (30d window covers all needs)
    price_history: dict[str, list[dict]] = {}
    for ticker in tickers_needed:
        try:
            hist = fetch_ticker_history(ticker, "1mo")
            ohlc = hist.get("ohlc_series", [])
            if ohlc:
                price_history[ticker] = ohlc
        except Exception as e:
            logger.warning(f"Failed to fetch history for {ticker}: {e}")

    # Update predictions using historical prices at exact hour marks
    for pred in pending:
        ticker = pred["ticker"]
        if ticker not in price_history:
            continue

        try:
            pub_dt = datetime.fromisoformat(pred["published_at"].replace("Z", "+00:00").split("+")[0])
        except (ValueError, AttributeError):
            continue

        age_hours = (now - pub_dt).total_seconds() / 3600
        ohlc = price_history[ticker]

        # Find prices at 1h, 4h, 24h after publication
        target_1h = pub_dt + timedelta(hours=1)
        target_4h = pub_dt + timedelta(hours=4)
        target_24h = pub_dt + timedelta(hours=24)

        kwargs = {}
        if age_hours >= 1.0 and not pred.get("price_after_1h"):
            p = _find_closest_price(ohlc, target_1h)
            if p:
                kwargs["price_after_1h"] = p
        if age_hours >= 4.0 and not pred.get("price_after_4h"):
            p = _find_closest_price(ohlc, target_4h)
            if p:
                kwargs["price_after_4h"] = p
        if age_hours >= 24.0 and not pred.get("price_after_24h"):
            p = _find_closest_price(ohlc, target_24h)
            if p:
                kwargs["price_after_24h"] = p

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


def backfill_new_indicators() -> dict[str, int]:
    """Backfill new indicator columns for existing predictions.

    Fetches current indicator data from Yahoo Finance and updates predictions
    that have NULL values for the new indicator columns.

    Returns: {"updated": N, "skipped": N, "errors": N}
    """
    from backend.stocks import fetch_ticker_history
    from backend.main import compute_atr, compute_macd, compute_trend, compute_rsi

    stats = {"updated": 0, "skipped": 0, "errors": 0}

    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        # Get predictions missing new indicator data
        rows = conn.execute(
            "SELECT id, ticker FROM predictions WHERE atr_value IS NULL OR sector_correlation_count IS NULL"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.info("Backfill indicators: all predictions already have indicator data")
        return stats

    # Group by ticker to minimize Yahoo Finance calls
    ticker_ids: dict[str, list[int]] = {}
    for row in rows:
        ticker = row["ticker"]
        ticker_ids.setdefault(ticker, []).append(row["id"])

    logger.info(f"Backfill indicators: {len(ticker_ids)} tickers, {len(rows)} predictions")

    for ticker, ids in ticker_ids.items():
        try:
            hist = fetch_ticker_history(ticker, "6mo")
            closes = hist.get("series", [])
            prices = [float(p) for p in closes if p is not None]

            rsi = compute_rsi(prices) if prices else None
            macd = compute_macd(prices) if len(prices) >= 35 else None
            trend = compute_trend(prices) if len(prices) >= 50 else None

            ohlc = hist.get("ohlc_series", [])
            atr_val = None
            atr_pct = None
            if len(ohlc) >= 15:
                highs = [float(d["high"]) for d in ohlc]
                lows = [float(d["low"]) for d in ohlc]
                cls = [float(d["close"]) for d in ohlc]
                atr_val = compute_atr(highs, lows, cls)
                if atr_val and prices:
                    atr_pct = round((atr_val / prices[-1]) * 100, 2)

            macd_hist = macd.get("histogram") if macd else None
            macd_factor = 1.0
            if macd_hist is not None:
                if macd_hist > 0:
                    macd_factor = 1.06
                elif macd_hist < 0:
                    macd_factor = 0.94

            trend_dir = trend.get("trend") if trend else None
            trend_factor = 1.0
            if trend_dir == "bullish":
                trend_factor = 1.05
            elif trend_dir == "bearish":
                trend_factor = 0.93

            atr_factor = 1.0
            if atr_pct:
                if atr_pct > 5.0:
                    atr_factor = 1.10
                elif atr_pct > 3.0:
                    atr_factor = 1.06
                elif atr_pct < 1.0:
                    atr_factor = 0.94

            # Update all predictions for this ticker
            conn = _get_conn()
            try:
                for pred_id in ids:
                    conn.execute(
                        """UPDATE predictions SET
                           rsi_value = COALESCE(rsi_value, ?),
                           rsi_factor = COALESCE(rsi_factor, ?),
                           macd_histogram = COALESCE(macd_histogram, ?),
                           macd_factor = COALESCE(macd_factor, ?),
                           sma_trend = COALESCE(sma_trend, ?),
                           trend_factor = COALESCE(trend_factor, ?),
                           atr_value = COALESCE(atr_value, ?),
                           atr_pct = COALESCE(atr_pct, ?),
                           atr_factor = COALESCE(atr_factor, ?)
                           WHERE id = ? AND (atr_value IS NULL OR atr_pct IS NULL)""",
                        (rsi, 1.0, macd_hist, macd_factor, trend_dir, trend_factor,
                         atr_val, atr_pct, atr_factor, pred_id),
                    )
                conn.commit()
                stats["updated"] += len(ids)
            finally:
                conn.close()

        except Exception as e:
            logger.warning(f"Backfill indicators: error for {ticker}: {e}")
            stats["errors"] += 1

    logger.info(f"Backfill indicators complete: updated={stats['updated']}, errors={stats['errors']}")
    return stats


def fix_prediction_data() -> dict[str, int]:
    """Fix known data quality issues in the predictions table.

    Fixes:
    1. Transition stuck 'pending' predictions that have is_correct set to 'resolved'
    2. Clear identical multi-horizon returns (re-resolve with historical prices)
    3. Fix sentiment_momentum storing 'None' string instead of NULL

    Returns: {"stuck_fixed": N, "returns_reset": N, "none_fixed": N}
    """
    stats = {"stuck_fixed": 0, "returns_reset": 0, "none_fixed": 0}

    conn = _get_conn()
    try:
        # Fix 1: Transition stuck pending predictions
        cur = conn.execute(
            "UPDATE predictions SET outcome_status = 'resolved' "
            "WHERE outcome_status = 'pending' AND is_correct IS NOT NULL"
        )
        stats["stuck_fixed"] = cur.rowcount

        # Fix 2: Clear identical multi-horizon returns (where 1h == 4h == 24h)
        cur = conn.execute(
            "UPDATE predictions SET "
            "price_after_1h = NULL, price_after_4h = NULL, price_after_24h = NULL, "
            "actual_return_1h = NULL, actual_return_4h = NULL, actual_return_24h = NULL, "
            "actual_direction = NULL, is_correct = NULL, "
            "outcome_status = 'pending', resolved_at = NULL "
            "WHERE outcome_status = 'resolved' "
            "AND price_after_1h IS NOT NULL AND price_after_4h IS NOT NULL AND price_after_24h IS NOT NULL "
            "AND price_after_1h = price_after_4h AND price_after_4h = price_after_24h"
        )
        stats["returns_reset"] = cur.rowcount

        # Fix 3: Fix sentiment_momentum 'None' string
        cur = conn.execute(
            "UPDATE predictions SET sentiment_momentum = NULL "
            "WHERE sentiment_momentum = 'None'"
        )
        stats["none_fixed"] = cur.rowcount

        conn.commit()
    finally:
        conn.close()

    logger.info(
        f"Data fix: stuck_pending={stats['stuck_fixed']}, "
        f"returns_reset={stats['returns_reset']}, "
        f"none_string_fixed={stats['none_fixed']}"
    )
    return stats


def _find_closest_price(history: list[dict], target_dt: datetime, max_delta_minutes: int = 7200) -> float | None:
    """Find the price closest to target_dt in historical data.
    Args:
        history: list of {"time": ISO_string_or_unix_ts, "value"/"close": float}
        target_dt: datetime to find price for
        max_delta_minutes: max acceptable time difference (5 days default for weekends/holidays)

    Returns:
        Closest price within max_delta, or None
    """
    best_price = None
    best_delta = timedelta(minutes=max_delta_minutes + 1)

    for entry in history:
        try:
            raw_time = entry["time"]
            if isinstance(raw_time, (int, float)):
                # Unix timestamp
                entry_dt = datetime.utcfromtimestamp(raw_time)
            else:
                # ISO string
                entry_dt = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
                entry_dt = entry_dt.replace(tzinfo=None)
        except (ValueError, KeyError, AttributeError, TypeError):
            continue

        delta = abs(entry_dt - target_dt)
        if delta < best_delta:
            best_delta = delta
            best_price = entry.get("value") or entry.get("close")

    return best_price if best_price and best_price > 0 else None


# ── Weight Recommendations ────────────────────────────────────────

# Current scoring weights (from scoring.py / events.py)
def current_weight_snapshot() -> dict[str, dict[str, Any]]:
    from backend.weights import get_weight

    return {
        "indirect_relationship_multiplier": {"current": get_weight("indirect_relationship_multiplier"), "location": "weights.py", "description": "Multiplier for indirect stock relationships"},
        "direct_relationship_multiplier": {"current": 1.0, "location": "scoring.py", "description": "Multiplier for direct stock relationships"},
        "directional_sentiment_floor": {"current": get_weight("directional_sentiment_floor"), "location": "weights.py", "description": "Min absolute sentiment for positive/negative direction"},
        "mixed_direction_factor": {"current": 0.35, "location": "scoring.py", "description": "Sentiment factor for mixed direction"},
        "significance_base": {"current": get_weight("significance_base"), "location": "weights.py", "description": "Base value in significance formula"},
        "significance_multiplier": {"current": get_weight("significance_multiplier"), "location": "weights.py", "description": "Final multiplier in significance formula"},
        "source_quality_blend": {"current": 0.45, "location": "scoring.py", "description": "Source quality weight in significance"},
        "confidence_base": {"current": 0.1, "location": "scoring.py", "description": "Base confidence value"},
        "sentiment_confidence_weight": {"current": 0.16, "location": "scoring.py", "description": "Weight of sentiment confidence in total confidence"},
        "neutral_direction_penalty": {"current": 0.65, "location": "events.py", "description": "Confidence penalty for neutral sentiment + directional prediction"},
        "low_confidence_penalty": {"current": 0.80, "location": "events.py", "description": "Confidence penalty for low sentiment confidence + directional prediction"},
        "vagueness_downgrade": {"current": 1.0, "location": "events.py", "description": "Whether vague government rhetoric is downgraded to neutral (1=yes)"},
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
                "reason": "System underestimates impact. Raise significance multiplier to surface more events.",
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

        current_weights = current_weight_snapshot()
        filtered_suggestions = []
        for suggestion in suggestions:
            weight = suggestion.get("weight")
            current_meta = current_weights.get(weight)
            if not current_meta:
                filtered_suggestions.append(suggestion)
                continue
            current_value = current_meta["current"]
            suggestion["current_value"] = current_value
            if float(current_value) == float(suggestion.get("suggested_value")):
                continue
            filtered_suggestions.append(suggestion)

        return {
            "ready": True,
            "total_predictions": metrics["total_predictions"],
            "resolved": metrics["resolved"],
            "overall_hit_rate": metrics["hit_rate"],
            "suggestion_count": len(filtered_suggestions),
            "suggestions": filtered_suggestions,
            "current_weights": current_weights,
        }
    finally:
        conn.close()


def analyze_indicator_effectiveness(min_samples: int = 5) -> dict[str, Any]:
    """Analyze how well each indicator predicted correct outcomes.

    For each indicator, compares predictions where the indicator boosted
    confidence (factor > 1.0) vs dampened (factor < 1.0) and checks
    if the boost/dampen was justified by actual outcomes.
    """
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        # Get resolved predictions with indicator factors
        cursor.execute("""
            SELECT
                is_correct, actual_return_24h, predicted_direction,
                rsi_factor, macd_factor, trend_factor,
                atr_factor, sector_correlation_factor,
                foreign_market_factor, sentiment_momentum_factor, currency_factor,
                event_cluster_factor
            FROM predictions
            WHERE outcome_status = 'resolved'
              AND predicted_direction != 'neutral'
        """)
        rows = [dict(r) for r in cursor.fetchall()]

        if len(rows) < min_samples:
            return {
                "ready": False,
                "reason": f"Need {min_samples} resolved predictions, have {len(rows)}",
                "indicators": {},
            }

        indicators = {}
        indicator_cols = {
            "rsi": "rsi_factor",
            "macd": "macd_factor",
            "sma_trend": "trend_factor",
            "atr": "atr_factor",
            "sector_correlation": "sector_correlation_factor",
            "foreign_market": "foreign_market_factor",
            "sentiment_momentum": "sentiment_momentum_factor",
            "currency": "currency_factor",
            "event_cluster": "event_cluster_factor",
        }

        for name, col in indicator_cols.items():
            # Filter rows where this indicator was applied (factor != 1.0 and not None)
            active = [r for r in rows if r[col] is not None and r[col] != 1.0]
            if len(active) < min_samples:
                indicators[name] = {
                    "coverage": len(active),
                    "total_resolved": len(rows),
                    "ready": False,
                    "reason": f"Only {len(active)} predictions with {name} data",
                }
                continue

            # Split into boosted (>1.0) and dampened (<1.0)
            boosted = [r for r in active if r[col] > 1.0]
            dampened = [r for r in active if r[col] < 1.0]

            boost_hr = (sum(1 for r in boosted if r["is_correct"]) / len(boosted)) if boosted else None
            dampen_hr = (sum(1 for r in dampened if r["is_correct"]) / len(dampened)) if dampened else None
            overall_hr = sum(1 for r in active if r["is_correct"]) / len(active)

            # Average factor when correct vs incorrect
            correct_factors = [r[col] for r in active if r["is_correct"]]
            incorrect_factors = [r[col] for r in active if not r["is_correct"]]
            avg_correct_factor = sum(correct_factors) / len(correct_factors) if correct_factors else None
            avg_incorrect_factor = sum(incorrect_factors) / len(incorrect_factors) if incorrect_factors else None

            # Direction analysis: did the indicator help?
            # If boosted predictions have higher hit rate than dampened → indicator is useful
            effectiveness = "unknown"
            suggested_adjustment = 0.0

            if boost_hr is not None and dampen_hr is not None:
                if boost_hr > dampen_hr + 0.1:
                    effectiveness = "helpful"
                    # Indicator correctly boosted/dampened — could be more aggressive
                    suggested_adjustment = min(0.05, (boost_hr - dampen_hr) * 0.1)
                elif dampen_hr > boost_hr + 0.1:
                    effectiveness = "counterproductive"
                    # Indicator is hurting — should be dampened
                    suggested_adjustment = max(-0.05, (dampen_hr - boost_hr) * -0.1)
                else:
                    effectiveness = "neutral"
                    suggested_adjustment = 0.0
            elif boost_hr is not None:
                effectiveness = "boost_only"
                suggested_adjustment = 0.02 if boost_hr > 0.6 else -0.02 if boost_hr < 0.4 else 0
            elif dampen_hr is not None:
                effectiveness = "dampen_only"
                suggested_adjustment = 0.02 if dampen_hr > 0.6 else -0.02 if dampen_hr < 0.4 else 0

            indicators[name] = {
                "coverage": len(active),
                "total_resolved": len(rows),
                "ready": True,
                "active_count": len(active),
                "boosted_count": len(boosted),
                "dampened_count": len(dampened),
                "boost_hit_rate": round(boost_hr, 3) if boost_hr is not None else None,
                "dampen_hit_rate": round(dampen_hr, 3) if dampen_hr is not None else None,
                "overall_hit_rate": round(overall_hr, 3),
                "avg_factor_when_correct": round(avg_correct_factor, 4) if avg_correct_factor else None,
                "avg_factor_when_incorrect": round(avg_incorrect_factor, 4) if avg_incorrect_factor else None,
                "effectiveness": effectiveness,
                "suggested_adjustment": round(suggested_adjustment, 4),
            }

        # Generate auto-tune weight adjustments based on indicator analysis
        auto_tune = {}
        for name, data in indicators.items():
            if not data.get("ready"):
                continue
            adj = data["suggested_adjustment"]
            if abs(adj) >= 0.01:
                # Map indicator name to weight keys
                weight_map = {
                    "rsi": None,  # RSI uses thresholds, not multipliers
                    "macd": None,  # part of technical alignment
                    "sma_trend": None,  # part of technical alignment
                    "atr": "atr_high_mult",
                    "sector_correlation": "sector_2plus_mult",
                    "foreign_market": "foreign_aligned_mult",
                    "sentiment_momentum": "momentum_strong_mult",
                    "currency": "currency_exporter_mult",
                    "event_cluster": "cluster_2_mult",
                }
                weight_key = weight_map.get(name)
                if weight_key:
                    from backend.weights import get_weight, DEFAULTS
                    current = get_weight(weight_key)
                    default = DEFAULTS[weight_key]
                    # Adjust relative to default, not current
                    new_val = round(default + adj, 3)
                    new_val = max(0.8, min(1.2, new_val))  # safety clamp
                    if abs(new_val - current) > 0.005:
                        auto_tune[weight_key] = {
                            "current": current,
                            "suggested": new_val,
                            "reason": f"{name}: {data['effectiveness']} "
                                      f"(boost={data.get('boost_hit_rate', 'N/A')}, "
                                      f"dampen={data.get('dampen_hit_rate', 'N/A')})",
                            "sample_size": data["active_count"],
                        }

        # Also adjust technical_cap based on RSI/MACD/SMA collective performance
        tech_indicators = [indicators.get(k) for k in ("rsi", "macd", "sma_trend") if indicators.get(k, {}).get("ready")]
        if tech_indicators:
            avg_effectiveness = sum(
                1 if t["effectiveness"] == "helpful" else -1 if t["effectiveness"] == "counterproductive" else 0
                for t in tech_indicators
            ) / len(tech_indicators)
            from backend.weights import get_weight as gw, DEFAULTS
            current_cap = gw("technical_cap")
            if avg_effectiveness < -0.3:
                auto_tune["technical_cap"] = {
                    "current": current_cap,
                    "suggested": max(0.05, current_cap - 0.03),
                    "reason": "Technical indicators collectively counterproductive — reduce cap",
                    "sample_size": sum(t["active_count"] for t in tech_indicators),
                }
            elif avg_effectiveness > 0.3:
                auto_tune["technical_cap"] = {
                    "current": current_cap,
                    "suggested": min(0.25, current_cap + 0.03),
                    "reason": "Technical indicators collectively helpful — increase cap",
                    "sample_size": sum(t["active_count"] for t in tech_indicators),
                }

        return {
            "ready": True,
            "total_resolved": len(rows),
            "indicators": indicators,
            "auto_tune": auto_tune,
            "auto_tune_count": len(auto_tune),
        }
    finally:
        conn.close()


def list_predictions(
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """List predictions with details for the history panel."""
    conn = _get_conn()
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()

        where = ""
        params: list[Any] = []
        if status:
            where = "WHERE outcome_status = ?"
            params.append(status)

        # Get total count
        cursor.execute(f"SELECT COUNT(*) as cnt FROM predictions {where}", params)
        total = cursor.fetchone()["cnt"]

        # Get predictions
        cursor.execute(f"""
            SELECT
                id, event_id, event_headline, published_at,
                ticker, predicted_direction, predicted_score,
                significance, confidence, relationship_type,
                price_at_event, price_after_24h,
                actual_return_24h, actual_direction, is_correct,
                outcome_status, resolved_at, created_at,
                rsi_factor, macd_factor, trend_factor, atr_factor,
                event_cluster_factor, market_context_factor, volume_signal
            FROM predictions
            {where}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """, params + [limit, offset])
        rows = [dict(r) for r in cursor.fetchall()]

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "predictions": rows,
        }
    finally:
        conn.close()
