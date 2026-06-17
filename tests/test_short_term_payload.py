from __future__ import annotations

from backend import main as appmod
from backend import stocks as stocksmod


def _patch_fetch_ticker_history(monkeypatch, fake_fn):
    monkeypatch.setattr(appmod, "fetch_ticker_history", fake_fn)
    monkeypatch.setattr(stocksmod, "fetch_ticker_history", fake_fn)


def fake_news_fetcher():
    article = {
        "source": "Setkab",
        "headline": "Pemerintah dukung hilirisasi dan investasi industri dasar",
        "url": "https://example.com/phase3-payload",
        "published_at": appmod.now_wib(),
        "summary": "Pemerintah mendorong investasi industri dasar dan infrastruktur yang menguntungkan emiten terkait.",
        "source_weight": 1.0,
        "source_type": "government",
    }
    return [article], []


def fake_stock_fetcher(tickers):
    quotes = {}
    for ticker in tickers:
        quotes[ticker] = {
            "ticker": ticker,
            "name": appmod.company_name_for_ticker(ticker),
            "sector": appmod.sector_for_ticker(ticker),
            "price": 107.0,
            "change_pct": 2.8,
            "volume": 900_000,
            "after_hours": False,
            "source": "fake",
        }
    return quotes, []


def fake_market_fetcher():
    return {
        "symbol": "^JKSE",
        "name": "IHSG",
        "value": 7100,
        "change_pct": 0.6,
        "change_points": 42,
        "series": [6980, 7005, 7030, 7060, 7100],
        "market_time": appmod.now_iso(),
        "source": "fake",
    }, []


def fake_validation_series_flat(ticker, range_name, interval):
    return {
        "ticker": ticker,
        "range": range_name,
        "interval": interval,
        "prices": [100.0, 100.1, 100.0, 100.2, 100.3, 100.25],
        "volumes": [1000, 1005, 995, 1010, 1008, 1002],
        "market_time": appmod.now_iso(),
        "source": "fake-validation",
        "warnings": [],
    }


def fake_breakout_history(ticker, window=None):
    closes = [
        90, 91, 92, 93, 94, 95, 96, 97, 98, 99,
        100, 101, 102, 103, 104, 105, 103, 101, 100, 99,
        98, 97, 96, 95, 96, 97, 98, 99, 100, 101,
        102, 103, 104, 105, 104, 103, 102, 101, 100, 99,
        100, 101, 102, 103, 104, 105, 104, 103, 104, 105,
        104, 103, 102, 101, 102, 103, 104, 105, 104, 107,
    ]
    volumes = [100_000] * 59 + [900_000]
    ohlc = []
    for idx, close in enumerate(closes):
        low = close - 2 if idx not in (22, 39) else close - 4
        high = close + 1
        ohlc.append({
            "open": float(close - 1),
            "high": float(high),
            "low": float(low),
            "close": float(close),
        })
    volume_series = [{"volume": int(v)} for v in volumes]
    return {
        "ticker": ticker,
        "name": appmod.company_name_for_ticker(ticker),
        "sector": appmod.sector_for_ticker(ticker),
        "window": window or "3mo",
        "series": [float(c) for c in closes],
        "ohlc_series": ohlc,
        "volume_series": volume_series,
        "price": 107.0,
        "market_time": appmod.now_iso(),
        "source": "fake-history",
        "warnings": [],
    }


def test_compute_short_term_features_exposes_participation_and_structure():
    from backend.stocks import compute_short_term_features

    hist = fake_breakout_history("ANTM.JK")
    features = compute_short_term_features(
        price=107.0,
        ohlc_series=hist["ohlc_series"],
        volume_series=[row["volume"] for row in hist["volume_series"]],
        support_resistance={"support": [98.0], "resistance": [105.0]},
        trend={"sma20": 103.0, "sma50": 99.0},
    )

    assert features["value_traded_estimate"] > 90_000_000
    assert features["distance_to_support_pct"] > 0.07
    assert features["distance_to_resistance_pct"] < 0.03
    assert features["close_above_resistance"] is True
    assert features["return_5d"] > 0
    assert features["price_above_sma20"] is True
    assert features["price_above_sma50"] is True


def test_refresh_payload_includes_short_term_market_features(monkeypatch):
    _patch_fetch_ticker_history(monkeypatch, fake_breakout_history)
    monkeypatch.setattr(appmod, "fetch_market_validation_series", fake_validation_series_flat)

    payload = appmod.build_refresh_payload(
        ["ANTM"],
        force=True,
        window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )

    stock = payload["stocks"][0]
    assert stock["value_traded_estimate"] > 90_000_000
    assert stock["distance_to_support_pct"] is not None
    assert stock["distance_to_resistance_pct"] is not None
    assert stock["close_above_resistance"] is True
    assert stock["reclaim_from_support"] is False
    assert stock["return_1d"] > 0
    assert stock["return_3d"] > 0
    assert stock["return_5d"] > 0
    assert stock["price_above_sma20"] is True
    assert stock["price_above_sma50"] is True
