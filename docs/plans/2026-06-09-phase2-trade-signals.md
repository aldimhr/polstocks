# Phase 2: Actionable Entry/Exit Signals — Implementation Plan

> **For Hermes:** Execute task-by-task with TDD.

**Goal:** Convert PolStock's technical indicators into explicit BUY/SELL/HOLD trade signals with entry price, stop-loss, take-profit, Bollinger Bands, support/resistance, and volume spike detection.

**Architecture:** Add computation functions in `backend/stocks.py`, expose via stock payload in `backend/main.py`, display in `dashboard.html`. All new indicators computed from existing `ohlc_series` data (750 × 1h candles).

**Tech Stack:** Python stdlib (no new deps), existing yfinance OHLC data, TradingView lightweight-charts for visualization.

---

### Task 1: Bollinger Bands computation
**Objective:** Add `compute_bollinger_bands()` to `backend/stocks.py`

**Files:**
- Modify: `backend/stocks.py`
- Test: `tests/test_app.py`

**Step 1: Write failing test**
```python
def test_compute_bollinger_bands():
    from backend.stocks import compute_bollinger_bands
    # 20 data points with known values
    closes = [100 + i * 0.5 for i in range(30)]
    result = compute_bollinger_bands(closes, period=20, std_dev=2.0)
    assert "upper" in result
    assert "middle" in result
    assert "lower" in result
    assert "bandwidth" in result
    assert "squeeze" in result
    assert result["upper"] > result["middle"] > result["lower"]
    assert isinstance(result["squeeze"], bool)
```

**Step 2: Run test to verify failure**
Run: `POLSTOCK_ENABLE_ML_NLP=0 python3 -m pytest tests/test_app.py::test_compute_bollinger_bands -v`

**Step 3: Implement**
```python
def compute_bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2.0) -> dict:
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
    # Squeeze: bandwidth < 0.03 (3%) means low volatility → breakout imminent
    squeeze = bandwidth < 0.03
    return {
        "upper": round(upper, 2),
        "middle": round(middle, 2),
        "lower": round(lower, 2),
        "bandwidth": round(bandwidth, 4),
        "squeeze": squeeze,
        "percent_b": round(percent_b, 3),
    }
```

**Step 4: Run test**
**Step 5: Commit**

---

### Task 2: Support/Resistance levels
**Objective:** Add `compute_support_resistance()` to `backend/stocks.py`

**Files:**
- Modify: `backend/stocks.py`
- Test: `tests/test_app.py`

**Approach:** Use pivot point method — find local minima (support) and maxima (resistance) from recent OHLC data.

```python
def compute_support_resistance(ohlc_series: list[dict], lookback: int = 50) -> dict:
    # Extract highs and lows
    # Find pivot points (local extrema)
    # Return nearest 2 support + 2 resistance levels
```

---

### Task 3: Volume spike detection
**Objective:** Add `detect_volume_spike()` to `backend/stocks.py`

**Files:**
- Modify: `backend/stocks.py`
- Test: `tests/test_app.py`

```python
def detect_volume_spike(volume_series: list[dict], period: int = 20) -> dict:
    # Compare current volume to rolling average
    # Return spike ratio, is_spike (bool), avg_volume
```

---

### Task 4: Trade signal generation
**Objective:** Add `generate_trade_signal()` that combines all indicators into BUY/SELL/HOLD

**Files:**
- Modify: `backend/stocks.py`
- Modify: `backend/main.py` (add to stock payload)
- Test: `tests/test_app.py`

**Logic:**
```
BUY when:
  - signal_strength >= 0.6
  - impact_direction == positive
  - RSI < 70 (not overbought)
  - MACD histogram > 0 (bullish momentum)
  - price above SMA20 (uptrend)
  - stop_loss = price - 1.5 * ATR
  - take_profit = price + 3.0 * ATR (1:2 risk-reward)

SELL when:
  - signal_strength >= 0.6
  - impact_direction == negative (currently disabled)
  - RSI > 30 (not oversold)
  - MACD histogram < 0
  - stop_loss = price + 1.5 * ATR
  - take_profit = price - 3.0 * ATR

HOLD otherwise
```

---

### Task 5: Expose indicators in stock payload
**Objective:** Add Bollinger, support/resistance, volume spike, trade signal to `/api/dashboard` stocks

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

---

### Task 6: Dashboard visualization
**Objective:** Show trade signals, Bollinger Bands, support/resistance in dashboard

**Files:**
- Modify: `dashboard.html`

---

### Task 7: Tests, restart, verify, commit/push
