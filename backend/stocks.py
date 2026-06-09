"""Stock data fetching, history, and formatting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import requests

from backend.config import (
    SOURCE_TIMEOUT_SECONDS,
    SECTORS,
    STOCK_HISTORY_WINDOWS, REQUEST_HEADERS, WIB,
)
from backend.utils import (
    now_wib, now_iso, normalize_event_window, event_window_label, within_trading_hours, sector_for_ticker,
    company_name_for_ticker, normalize_ticker,
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


def compute_bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0) -> dict[str, Any]:
    """Compute Bollinger Bands from closing prices.

    Returns dict with upper, middle, lower, bandwidth, squeeze, percent_b.
    Squeeze = True when bandwidth < 3% (low volatility, breakout imminent).
    percent_b = where price is within the bands (0 = lower, 1 = upper).
    """
    if len(closes) < period:
        return {"upper": 0, "middle": 0, "lower": 0, "bandwidth": 0, "squeeze": False, "percent_b": 0.5}
    recent = closes[-period:]
    middle = sum(recent) / period
    variance = sum((x - middle) ** 2 for x in recent) / period
    std = variance ** 0.5
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    bandwidth = (upper - lower) / middle if middle else 0
    current = closes[-1]
    percent_b = (current - lower) / (upper - lower) if upper != lower else 0.5
    squeeze = bandwidth < 0.03
    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "bandwidth": round(bandwidth, 4),
        "squeeze": squeeze,
        "percent_b": round(min(max(percent_b, 0.0), 2.0), 3),
    }


def compute_support_resistance(ohlc_series: list[dict], lookback: int = 50) -> dict[str, list[float]]:
    """Compute support and resistance levels from OHLC data using pivot points.

    Returns dict with 'support' (list of up to 2 levels) and 'resistance' (list of up to 2 levels).
    """
    if len(ohlc_series) < 5:
        return {"support": [], "resistance": []}

    recent = ohlc_series[-lookback:] if len(ohlc_series) >= lookback else ohlc_series
    highs = [float(e.get("high", 0) or 0) for e in recent if e.get("high")]
    lows = [float(e.get("low", 0) or 0) for e in recent if e.get("low")]

    if not highs or not lows:
        return {"support": [], "resistance": []}

    # Find pivot highs (local maxima) and pivot lows (local minima)
    pivot_highs = []
    pivot_lows = []
    for i in range(2, len(recent) - 2):
        h = float(recent[i].get("high", 0) or 0)
        low_val = float(recent[i].get("low", 0) or 0)
        if h > 0 and all(h >= float(recent[j].get("high", 0) or 0) for j in range(max(0, i-2), min(len(recent), i+3))):
            pivot_highs.append(h)
        if low_val > 0 and all(low_val <= float(recent[j].get("low", 0) or 0) for j in range(max(0, i-2), min(len(recent), i+3))):
            pivot_lows.append(low_val)

    # Fallback to simple high/low ranges if no pivots found
    if not pivot_highs:
        pivot_highs = sorted(set(highs))[-2:]
    if not pivot_lows:
        pivot_lows = sorted(set(lows))[:2]

    # Cluster nearby levels (within 1% of each other)
    def cluster_levels(levels: list[float], max_pct: float = 0.01) -> list[float]:
        if not levels:
            return []
        sorted_levels = sorted(levels)
        clusters = [[sorted_levels[0]]]
        for lv in sorted_levels[1:]:
            if abs(lv - clusters[-1][-1]) / max(clusters[-1][-1], 1) < max_pct:
                clusters[-1].append(lv)
            else:
                clusters.append([lv])
        return [round(sum(c) / len(c), 2) for c in clusters]

    resistance = cluster_levels(pivot_highs)[-2:]
    support = cluster_levels(pivot_lows)[:2]

    return {"support": support, "resistance": resistance}


def detect_volume_spike(volumes: list[float | int], period: int = 20) -> dict[str, Any]:
    """Detect unusual volume compared to rolling average.

    Returns dict with spike_ratio, is_spike (ratio >= 2x), avg_volume, current_volume.
    """
    if len(volumes) < 2:
        return {"spike_ratio": 1.0, "is_spike": False, "avg_volume": 0, "current_volume": 0}
    current = float(volumes[-1])
    window = [float(v) for v in volumes[-(period+1):-1] if v and float(v) > 0]
    if not window:
        return {"spike_ratio": 1.0, "is_spike": False, "avg_volume": 0, "current_volume": current}
    avg = sum(window) / len(window)
    ratio = current / avg if avg > 0 else 1.0
    return {
        "spike_ratio": round(ratio, 2),
        "is_spike": ratio >= 2.0,
        "avg_volume": int(avg),
        "current_volume": int(current),
    }


def generate_trade_signal(
    price: float,
    signal_strength: float,
    impact_direction: str,
    rsi: float | None,
    macd_histogram: float | None,
    trend_direction: str,
    atr: float | None,
    bb_percent_b: float | None,
    volume_spike_ratio: float | None,
) -> dict[str, Any]:
    """Generate an explicit BUY/SELL/HOLD trade signal with entry, stop-loss, take-profit.

    Returns dict with action, entry, stop_loss, take_profit, risk_reward, timeframe, reasons.
    """
    reasons = []

    # Gate: must have minimum signal strength
    if signal_strength < 0.6:
        reasons.append(f"signal_strength {signal_strength:.2f} < 0.6 threshold")
        return {"action": "HOLD", "entry": price, "stop_loss": None, "take_profit": None, "risk_reward": None, "timeframe": None, "reasons": reasons}

    if impact_direction not in ("positive", "negative"):
        reasons.append(f"direction is {impact_direction}, need positive or negative")
        return {"action": "HOLD", "entry": price, "stop_loss": None, "take_profit": None, "risk_reward": None, "timeframe": None, "reasons": reasons}

    # ATR for stop-loss/take-profit sizing
    atr_val = atr if atr and atr > 0 else price * 0.02  # fallback: 2% of price

    if impact_direction == "positive":
        action = "BUY"
        # Check for overbought
        if rsi is not None and rsi >= 70:
            reasons.append(f"RSI {rsi:.0f} overbought — HOLD instead")
            return {"action": "HOLD", "entry": price, "stop_loss": None, "take_profit": None, "risk_reward": None, "timeframe": None, "reasons": reasons}
        # Check MACD alignment
        if macd_histogram is not None and macd_histogram < 0:
            reasons.append("MACD histogram negative — weak momentum")
            signal_strength *= 0.8
        # Check trend alignment
        if trend_direction == "bearish":
            reasons.append("fighting bearish trend")
            signal_strength *= 0.7
        stop_loss = round(price - 1.5 * atr_val, 2)
        take_profit = round(price + 3.0 * atr_val, 2)
    else:
        action = "SELL"
        if rsi is not None and rsi <= 30:
            reasons.append(f"RSI {rsi:.0f} oversold — HOLD instead")
            return {"action": "HOLD", "entry": price, "stop_loss": None, "take_profit": None, "risk_reward": None, "timeframe": None, "reasons": reasons}
        if macd_histogram is not None and macd_histogram > 0:
            reasons.append("MACD histogram positive — weak bearish momentum")
            signal_strength *= 0.8
        if trend_direction == "bullish":
            reasons.append("fighting bullish trend")
            signal_strength *= 0.7
        stop_loss = round(price + 1.5 * atr_val, 2)
        take_profit = round(price - 3.0 * atr_val, 2)

    # Volume bonus
    if volume_spike_ratio and volume_spike_ratio >= 2.0:
        reasons.append(f"volume spike {volume_spike_ratio:.1f}x — institutional interest")
        signal_strength = min(1.0, signal_strength * 1.1)

    # Bollinger squeeze bonus
    if bb_percent_b is not None:
        if action == "BUY" and bb_percent_b < 0.2:
            reasons.append("near lower Bollinger Band — potential bounce")
        elif action == "SELL" and bb_percent_b > 0.8:
            reasons.append("near upper Bollinger Band — potential reversal")

    risk_reward = abs(take_profit - price) / abs(price - stop_loss) if abs(price - stop_loss) > 0 else 0

    # Timeframe based on signal strength
    if signal_strength >= 0.8:
        timeframe = "1-3d"
    elif signal_strength >= 0.65:
        timeframe = "1w"
    else:
        timeframe = "intraday"

    return {
        "action": action,
        "entry": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "risk_reward": round(risk_reward, 2),
        "timeframe": timeframe,
        "reasons": reasons,
        "signal_quality": round(signal_strength, 3),
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
            low = raw_lows[i] if i < len(raw_lows) else None
            c = raw_closes[i] if i < len(raw_closes) else None
            if all(v is not None for v in (o, h, low, c)):
                ohlc_series.append({
                    "time": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(low),
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


