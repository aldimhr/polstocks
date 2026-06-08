"""Stock data fetching, history, and formatting."""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from backend.config import (
    SOURCE_TIMEOUT_SECONDS,
    SECTORS,
    STOCK_MASTER, DEFAULT_WATCHLIST, DEFAULT_EVENT_WINDOW, EVENT_WINDOWS,
    STOCK_HISTORY_WINDOWS, REQUEST_HEADERS, WIB,
)
from backend.utils import (
    now_wib, now_iso, normalize_ticker, strip_tags, safe_text,
    parse_datetime, extract_html_published_at, clamp, normalize_match_text,
    collect_phrase_hits, normalize_event_window, event_window_config,
    event_window_delta, event_window_label, text_similarity,
    is_stale_article, within_trading_hours, sector_for_ticker,
    company_name_for_ticker, article_text, normalize_ticker,
)


def compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute Relative Strength Index (RSI) from closing prices.

    Args:
        closes: list of closing prices in chronological order
        period: RSI lookback period (default 14)

    Returns:
        RSI value (0-100), or None if insufficient data
    """
    if len(closes) < period + 1:
        return None
    # Calculate price changes
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    # Use last `period` deltas for initial average
    recent = deltas[-(period):]
    gains = [d if d > 0 else 0 for d in recent]
    losses = [-d if d < 0 else 0 for d in recent]
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0  # no losses = max RSI
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _ema(values: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average series."""
    if len(values) < period:
        return []
    multiplier = 2.0 / (period + 1)
    ema_series = [sum(values[:period]) / period]
    for v in values[period:]:
        ema_series.append((v - ema_series[-1]) * multiplier + ema_series[-1])
    return ema_series


def compute_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float] | None:
    """Compute MACD (Moving Average Convergence Divergence).

    Args:
        closes: closing prices in chronological order
        fast: fast EMA period (default 12)
        slow: slow EMA period (default 26)
        signal: signal line EMA period (default 9)

    Returns:
        dict with 'macd', 'signal', 'histogram' values, or None if insufficient data
    """
    if len(closes) < slow + signal:
        return None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    # Align: ema_fast has (slow - fast) more values at the start
    offset = len(ema_fast) - len(ema_slow)
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    if len(macd_line) < signal:
        return None
    signal_line = _ema(macd_line, signal)
    if not signal_line:
        return None
    offset2 = len(macd_line) - len(signal_line)
    histogram = macd_line[-1] - signal_line[-1]
    return {
        "macd": round(macd_line[-1], 4),
        "signal": round(signal_line[-1], 4),
        "histogram": round(histogram, 4),
    }


def compute_sma(closes: list[float], period: int) -> float | None:
    """Compute Simple Moving Average for the last `period` values."""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def compute_trend(closes: list[float]) -> dict[str, Any] | None:
    """Compute trend indicators: SMA20, SMA50, crossover signal.

    Returns:
        dict with sma20, sma50, trend ('bullish','bearish','neutral'), trend_strength
    """
    sma20 = compute_sma(closes, 20)
    sma50 = compute_sma(closes, 50)
    if sma20 is None or sma50 is None:
        return None
    current_price = closes[-1]
    if sma20 > sma50:
        trend = "bullish"
        strength = (sma20 - sma50) / sma50
    elif sma20 < sma50:
        trend = "bearish"
        strength = (sma50 - sma20) / sma50
    else:
        trend = "neutral"
        strength = 0.0
    return {
        "sma20": sma20,
        "sma50": sma50,
        "price": round(current_price, 2),
        "above_sma20": current_price > sma20,
        "above_sma50": current_price > sma50,
        "trend": trend,
        "trend_strength": round(strength, 4),
    }


def fetch_live_quote(ticker: str) -> dict[str, Any]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(ticker)}?range=1d&interval=1d&includePrePost=false&events=div,splits"
    response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
    response.raise_for_status()
    payload = response.json()["chart"]["result"][0]
    meta = payload["meta"]
    closes = [value for value in payload.get("indicators", {}).get("quote", [{}])[0].get("close", []) if value is not None]
    price = meta.get("regularMarketPrice")
    change_pct = meta.get("regularMarketChangePercent")
    change_points = meta.get("regularMarketChange")
    volume = meta.get("regularMarketVolume")
    market_time = meta.get("regularMarketTime")
    previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
    if price is None and closes:
        price = closes[-1]
    if change_points is None and price is not None and previous_close not in (None, 0):
        change_points = float(price) - float(previous_close)
    if change_pct is None and change_points is not None and previous_close not in (None, 0):
        change_pct = (float(change_points) / float(previous_close)) * 100.0
    market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
    return {
        "ticker": ticker,
        "name": company_name_for_ticker(ticker),
        "sector": sector_for_ticker(ticker),
        "price": float(price) if price is not None else None,
        "change_pct": float(change_pct) if change_pct is not None else None,
        "volume": int(volume) if volume is not None else None,
        "market_time": market_dt,
        "after_hours": (not within_trading_hours(market_dt)) and (now_wib() - market_dt > timedelta(minutes=30)),
        "source": "yahoo-finance",
    }


def fetch_stock_quotes(tickers: list[str]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    tickers = [normalize_ticker(ticker) for ticker in tickers if normalize_ticker(ticker)]
    quotes: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    if not tickers:
        return quotes, ["No tickers requested."]
    with ThreadPoolExecutor(max_workers=min(8, len(tickers))) as pool:
        futures = {pool.submit(fetch_live_quote, ticker): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                quotes[ticker] = future.result()
            except Exception as exc:  # pragma: no cover - network failures are expected in some environments
                warnings.append(f"{ticker}: {exc}")
    return quotes, warnings


def fetch_market_index() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    symbol = "%5EJKSE"
    urls = [
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m&includePrePost=false&events=div,splits",
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d&includePrePost=false&events=div,splits",
    ]
    last_error: str | None = None
    for url in urls:
        try:
            response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
            response.raise_for_status()
            payload = response.json()["chart"]["result"][0]
            meta = payload["meta"]
            closes = [value for value in payload.get("indicators", {}).get("quote", [{}])[0].get("close", []) if value is not None]
            price = meta.get("regularMarketPrice")
            change_pct = meta.get("regularMarketChangePercent")
            change_points = meta.get("regularMarketChange")
            market_time = meta.get("regularMarketTime")
            previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None and closes:
                price = closes[-1]
            if change_points is None and price is not None and previous_close not in (None, 0):
                change_points = float(price) - float(previous_close)
            if change_pct is None and change_points is not None and previous_close not in (None, 0):
                change_pct = (float(change_points) / float(previous_close)) * 100.0
            market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
            return {
                "symbol": "^JKSE",
                "name": "IHSG",
                "value": float(price) if price is not None else None,
                "change_pct": float(change_pct) if change_pct is not None else None,
                "change_points": float(change_points) if change_points is not None else None,
                "series": [float(value) for value in closes[-48:]],
                "market_time": market_dt.isoformat(timespec="seconds"),
                "source": "yahoo-finance",
            }, warnings
        except Exception as exc:  # pragma: no cover - network failures are expected in some environments
            last_error = str(exc)
    if last_error:
        warnings.append(f"IHSG: {last_error}")
    return {
        "symbol": "^JKSE",
        "name": "IHSG",
        "value": None,
        "change_pct": None,
        "change_points": None,
        "series": [],
        "market_time": now_iso(),
        "source": "unavailable",
    }, warnings


def stock_history_window_config(window: str | None) -> dict[str, Any]:
    return STOCK_HISTORY_WINDOWS[normalize_event_window(window)]


def fetch_ticker_history(ticker: str, window: str | None = None) -> dict[str, Any]:
    normalized_ticker = normalize_ticker(ticker)
    config = stock_history_window_config(window)
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(normalized_ticker)}"
        f"?range={quote(config['range'])}&interval={quote(config['interval'])}&includePrePost=false&events=div,splits"
    )
    warnings: list[str] = []
    try:
        response = requests.get(url, timeout=SOURCE_TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
        response.raise_for_status()
        chart = response.json().get("chart", {}).get("result", [])
        if not chart:
            raise ValueError("empty history")
        result = chart[0]
        meta = result.get("meta", {})
        quote_data = result.get("indicators", {}).get("quote", [{}])[0]
        raw_timestamps = list(result.get("timestamp", []) or [])
        raw_closes = list(quote_data.get("close", []) or [])
        raw_opens = list(quote_data.get("open", []) or [])
        raw_highs = list(quote_data.get("high", []) or [])
        raw_lows = list(quote_data.get("low", []) or [])
        raw_volumes = list(quote_data.get("volume", []) or [])
        series_pairs = [
            (int(ts), float(price))
            for ts, price in zip(raw_timestamps, raw_closes)
            if ts is not None and price is not None
        ]
        prices = [price for _, price in series_pairs]
        series = [
            {
                "time": datetime.fromtimestamp(ts, tz=WIB).isoformat(timespec="seconds"),
                "value": price,
            }
            for ts, price in series_pairs
        ]
        # Build OHLC series for candlestick chart
        ohlc_series = []
        for i, ts in enumerate(raw_timestamps):
            if ts is None:
                continue
            o = raw_opens[i] if i < len(raw_opens) else None
            h = raw_highs[i] if i < len(raw_highs) else None
            l = raw_lows[i] if i < len(raw_lows) else None
            c = raw_closes[i] if i < len(raw_closes) else None
            if all(v is not None for v in (o, h, l, c)):
                ohlc_series.append({
                    "time": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                })
        # Build volume series with color
        volume_series = []
        for i, ts in enumerate(raw_timestamps):
            if ts is None:
                continue
            vol = raw_volumes[i] if i < len(raw_volumes) else None
            c = raw_closes[i] if i < len(raw_closes) else None
            o = raw_opens[i] if i < len(raw_opens) else None
            if vol is not None:
                volume_series.append({
                    "time": int(ts),
                    "value": float(vol),
                    "color": "rgba(77,219,142,0.35)" if (c is not None and o is not None and float(c) >= float(o)) else "rgba(255,92,92,0.35)",
                })
        volumes = [float(value) for value in raw_volumes if value is not None]
        price = meta.get("regularMarketPrice")
        change_pct = meta.get("regularMarketChangePercent")
        change_points = meta.get("regularMarketChange")
        volume = meta.get("regularMarketVolume")
        market_time = meta.get("regularMarketTime")
        previous_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None and prices:
            price = prices[-1]
        if change_points is None and price is not None and previous_close not in (None, 0):
            change_points = float(price) - float(previous_close)
        if change_pct is None and change_points is not None and previous_close not in (None, 0):
            change_pct = (float(change_points) / float(previous_close)) * 100.0
        market_dt = datetime.fromtimestamp(market_time, tz=WIB) if market_time else now_wib()
        start_price = prices[0] if prices else None
        period_change_points = float(price) - float(start_price) if price is not None and start_price not in (None, 0) else None
        period_change_pct = (period_change_points / float(start_price)) * 100.0 if period_change_points is not None and start_price not in (None, 0) else None
        return {
            "ticker": normalized_ticker,
            "name": company_name_for_ticker(normalized_ticker),
            "sector": sector_for_ticker(normalized_ticker),
            "window": normalize_event_window(window),
            "window_label": event_window_label(window),
            "range": config["range"],
            "interval": config["interval"],
            "price": float(price) if price is not None else None,
            "change_pct": float(change_pct) if change_pct is not None else None,
            "change_points": float(change_points) if change_points is not None else None,
            "period_change_pct": float(period_change_pct) if period_change_pct is not None else None,
            "period_change_points": float(period_change_points) if period_change_points is not None else None,
            "volume": int(volume) if volume is not None else None,
            "series": prices,
            "history": series,
            "series_points": len(prices),
            "series_start": series[0]["time"] if series else None,
            "series_end": series[-1]["time"] if series else None,
            "series_high": max(prices) if prices else None,
            "series_low": min(prices) if prices else None,
            "ohlc_series": ohlc_series,
            "volume_series": volume_series,
            "market_time": market_dt.isoformat(timespec="seconds"),
            "source": "yahoo-finance",
            "warnings": warnings,
        }
    except Exception as exc:  # pragma: no cover - network failures are environment-dependent
        warnings.append(f"history unavailable for {normalized_ticker}: {exc}")
        return {
            "ticker": normalized_ticker,
            "name": company_name_for_ticker(normalized_ticker),
            "sector": sector_for_ticker(normalized_ticker),
            "window": normalize_event_window(window),
            "window_label": event_window_label(window),
            "range": config["range"],
            "interval": config["interval"],
            "price": None,
            "change_pct": None,
            "change_points": None,
            "period_change_pct": None,
            "period_change_points": None,
            "volume": None,
            "series": [],
            "history": [],
            "series_points": 0,
            "series_start": None,
            "series_end": None,
            "series_high": None,
            "series_low": None,
            "ohlc_series": [],
            "volume_series": [],
            "market_time": now_iso(),
            "source": "unavailable",
            "warnings": warnings,
        }


def sort_stocks_by_impact(stocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ranked = []
    for index, stock in enumerate(stocks):
        relationship_count = int(stock.get("relationship_count", 0) or 0)
        impact_score = float(stock.get("impact_score", 0.0) or 0.0)
        impacted = relationship_count > 0 or abs(impact_score) > 0.0001
        ranked.append(
            (
                0 if impacted else 1,
                -abs(impact_score),
                -float(relationship_count),
                index,
                stock,
            )
        )
    ranked.sort()
    return [stock for *_, stock in ranked]


def compute_sector_summary(stocks: list[dict[str, Any]]) -> dict[str, float]:
    totals = {sector: 0.0 for sector in SECTORS}
    counts = {sector: 0 for sector in SECTORS}
    for stock in stocks:
        sector = stock.get("sector", "")
        if sector not in totals:
            continue
        totals[sector] += float(stock.get("impact_score", 0.0))
        counts[sector] += 1
    return {
        sector: round(totals[sector] / counts[sector], 3) if counts[sector] else 0.0
        for sector in SECTORS
    }


THREAD_CATEGORY_FAMILIES = {
    "REGULATION_NEW": "REGULATION",
    "REGULATION_REPEAL": "REGULATION",
    "STATE_BUDGET": "FISCAL",
    "MONETARY_SIGNAL": "MONETARY",
    "TRADE_POLICY": "TRADE_POLICY",
    "ENERGY_POLICY": "ENERGY_POLICY",
    "INVESTMENT_POLICY": "INVESTMENT_POLICY",
    "PARLIAMENT_SESSION": "LEGISLATIVE",
}


EVENT_STAGE_ORDER = {
    "proposal": 1,
    "debate": 2,
    "approved": 3,
    "effective": 4,
    "enforced": 5,
    "delayed": 1,
    "revoked": 0,
    "unspecified": 1,
}


THREAD_STATUS_RANK = {
    "active": 1,
    "confirmed": 2,
    "contested": 3,
    "reversed": 4,
}


