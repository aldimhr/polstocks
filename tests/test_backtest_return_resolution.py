from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from backend import backtest as backtestmod
from backend import config


@pytest.fixture()
def isolated_backtest_db(tmp_path, monkeypatch):
    db_path = tmp_path / "polstock_backend.db"
    monkeypatch.setenv("POLSTOCK_BACKEND_DB", str(db_path))
    monkeypatch.setattr(config, "BACKEND_DB_PATH", db_path)
    monkeypatch.setattr(backtestmod, "BACKEND_DB_PATH", db_path)
    backtestmod.init_backtest_db()
    return db_path


def test_resolve_populates_7d_and_30d_returns(isolated_backtest_db, monkeypatch):
    db_path = isolated_backtest_db

    pub_dt = datetime.utcnow() - timedelta(days=31)

    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        INSERT INTO predictions (
            event_id, event_headline, published_at, ticker,
            predicted_direction, predicted_score, significance, confidence,
            relationship_type, categories, source_type, event_stage,
            price_at_event, outcome_status, prediction_origin
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'live')
        """,
        (
            "evt_test_7d30d",
            "Test headline",
            pub_dt.strftime("%Y-%m-%dT%H:%M:%S"),
            "BBCA.JK",
            "positive",
            0.7,
            0.5,
            0.6,
            "direct",
            "[]",
            "media",
            "established",
            1000.0,
        ),
    )
    conn.commit()
    conn.close()

    def fake_fetch_ticker_history(ticker: str, window: str = "3mo"):
        return {
            "ticker": ticker,
            "window": window,
            "ohlc_series": [
                {"time": (pub_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S"), "value": 1010.0},
                {"time": (pub_dt + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%S"), "value": 1020.0},
                {"time": (pub_dt + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S"), "value": 1030.0},
                {"time": (pub_dt + timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S"), "value": 1070.0},
                {"time": (pub_dt + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S"), "value": 1130.0},
            ],
        }

    import backend.stocks as stocksmod
    monkeypatch.setattr(stocksmod, "fetch_ticker_history", fake_fetch_ticker_history)

    resolved = backtestmod.resolve_pending_outcomes()
    assert resolved >= 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT return_7d, return_30d, outcome_7d, outcome_30d, outcome_status FROM predictions WHERE event_id = ?",
        ("evt_test_7d30d",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["return_7d"] is not None
    assert row["return_30d"] is not None
    assert row["outcome_7d"] in {"hit", "miss", "flat"}
    assert row["outcome_30d"] in {"hit", "miss", "flat"}
    assert row["outcome_status"] == "resolved"
