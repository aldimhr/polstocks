# Signal Quality Upgrade — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Turn 20 generic WATCH signals into 3-5 high-conviction signals by adding sector-relative strength, volume trend confirmation, signal decay, and sector deduplication.

**Architecture:** Pure function upgrades in `trading_signals.py` + post-processing in `main.py`. No DB migrations needed.

---

## Task 1: Sector-Relative RSI Boost

**Objective:** Boost signal strength when a stock is more oversold than its sector peers. A stock at RSI 25 in a sector where peers average RSI 45 is a stronger bounce candidate.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing tests**

```python
class TestSectorRelativeRSI:
    def test_sector_outperformer_gets_boost(self):
        """Stock with RSI much lower than sector average gets strength boost."""
        from backend.trading_signals import compute_sector_rsi_boost
        stock = {"rsi_value": 25, "sector": "Energy"}
        sector_avg_rsi = {"Energy": 45.0}
        boost = compute_sector_rsi_boost(stock, sector_avg_rsi)
        assert boost > 0, "Outperformer should get boost"

    def test_sector_underperformer_gets_no_boost(self):
        """Stock with RSI near sector average gets no boost."""
        from backend.trading_signals import compute_sector_rsi_boost
        stock = {"rsi_value": 44, "sector": "Energy"}
        sector_avg_rsi = {"Energy": 45.0}
        boost = compute_sector_rsi_boost(stock, sector_avg_rsi)
        assert boost == 0, "Inline with sector should not boost"

    def test_missing_sector_data_returns_zero(self):
        """No sector data = no boost."""
        from backend.trading_signals import compute_sector_rsi_boost
        stock = {"rsi_value": 25, "sector": "Unknown"}
        boost = compute_sector_rsi_boost(stock, {})
        assert boost == 0
```

**Step 2: Run to verify failure**

**Step 3: Implement `compute_sector_rsi_boost`**

```python
def compute_sector_rsi_boost(
    stock: dict[str, Any],
    sector_avg_rsi: dict[str, float],
) -> float:
    """Boost strength when stock RSI is significantly below sector average.

    Returns 0.0-0.15 boost value.
    """
    rsi = stock.get("rsi_value")
    sector = str(stock.get("sector", "") or "")
    if rsi is None or not sector or sector not in sector_avg_rsi:
        return 0.0

    avg = sector_avg_rsi[sector]
    delta = avg - rsi  # positive = stock is more oversold than peers

    if delta >= 20:
        return 0.15  # significantly more oversold than sector
    elif delta >= 10:
        return 0.10
    elif delta >= 5:
        return 0.05
    return 0.0
```

**Step 4: Integrate into `detect_technical_setup` and `classify_signal`**

In `classify_signal`, compute sector_avg_rsi from all stocks, then pass to scoring.

**Step 5: Commit**

---

## Task 2: Volume Trend Confirmation

**Objective:** Boost signals when volume is rising alongside price recovery. A stock bouncing from oversold with increasing volume is stronger than one with declining volume.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py`

**Step 1: Write failing tests**

```python
class TestVolumeTrend:
    def test_rising_volume_gets_boost(self):
        """Rising volume on bounce = stronger signal."""
        from backend.trading_signals import compute_volume_trend_boost
        vol = {"is_spike": False, "spike_ratio": 1.5, "avg_volume": 1000000, "current_volume": 1500000}
        boost = compute_volume_trend_boost(vol)
        assert boost > 0

    def test_declining_volume_no_boost(self):
        """Declining volume = no boost."""
        from backend.trading_signals import compute_volume_trend_boost
        vol = {"is_spike": False, "spike_ratio": 0.5, "avg_volume": 1000000, "current_volume": 500000}
        boost = compute_volume_trend_boost(vol)
        assert boost == 0
```

**Step 2: Implement**

```python
def compute_volume_trend_boost(vol: dict[str, Any]) -> float:
    """Boost when current volume is above average (rising interest).

    Returns 0.0-0.10 boost.
    """
    if not vol or not isinstance(vol, dict):
        return 0.0
    spike_ratio = float(vol.get("spike_ratio", 0) or 0)
    if spike_ratio >= 2.0:
        return 0.10  # strong volume surge
    elif spike_ratio >= 1.3:
        return 0.05  # moderate volume increase
    return 0.0
```

**Step 3: Integrate into `detect_technical_setup`**

**Step 4: Commit**

---

## Task 3: Signal Decay

**Objective:** Downgrade signals that have been WATCH for multiple days without price movement. A WATCH signal from 5 days ago with the same price is stale.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py`
- Modify: `/opt/hermes/politics_stock_mapper/backend/main.py` (post-processing)

**Step 1: Write failing tests**

```python
class TestSignalDecay:
    def test_fresh_signal_no_decay(self):
        """Signal from today should not decay."""
        from backend.trading_signals import apply_signal_decay
        signal = {"action": "WATCH", "signal_strength": 0.50}
        apply_signal_decay(signal, days_since_signal=0)
        assert signal["signal_strength"] == 0.50

    def test_stale_signal_decays(self):
        """Signal from 5 days ago should decay."""
        from backend.trading_signals import apply_signal_decay
        signal = {"action": "WATCH", "signal_strength": 0.50}
        apply_signal_decay(signal, days_since_signal=5)
        assert signal["signal_strength"] < 0.50
```

**Step 2: Implement**

Add to `trading_signals.py`:
```python
def apply_signal_decay(signal: dict[str, Any], days_since_signal: int) -> None:
    """Apply decay to signal strength based on age.

    WATCH signals lose 10% per day after day 1.
    BUY signals are not decayed (they have entry/SL/TP).
    """
    if signal.get("action") != "WATCH":
        return
    if days_since_signal <= 1:
        return
    decay = min(0.5, (days_since_signal - 1) * 0.10)  # max 50% decay
    signal["signal_strength"] = round(
        signal["signal_strength"] * (1 - decay), 4
    )
    if signal["signal_strength"] < 0.15:
        signal["action"] = "IGNORE"
        signal["reasons"] = signal.get("reasons", []) + ["Signal stale — auto-decayed"]
```

**Step 3: Integrate in `main.py` post-processing**

After `classify_signal`, check `daily_signal_snapshots` to see when this ticker was last WATCH, and apply decay.

**Step 4: Commit**

---

## Task 4: Sector Deduplication

**Objective:** When multiple stocks in the same sector have WATCH signals, keep only the strongest one. Avoids concentrated risk (e.g., 5 agriculture stocks all bouncing).

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/main.py` (post-processing in `build_refresh_payload`)

**Step 1: Write failing tests**

```python
def test_sector_dedup_keeps_strongest():
    """Multiple WATCH signals in same sector → keep only strongest."""
    from backend.trading_signals import deduplicate_by_sector
    signals = [
        {"ticker": "CPIN.JK", "action": "WATCH", "signal_strength": 0.50, "sector": "Consumer Non-Cyclicals"},
        {"ticker": "JPFA.JK", "action": "WATCH", "signal_strength": 0.30, "sector": "Consumer Non-Cyclicals"},
        {"ticker": "ADRO.JK", "action": "WATCH", "signal_strength": 0.60, "sector": "Energy"},
    ]
    result = deduplicate_by_sector(signals)
    tickers = [s["ticker"] for s in result]
    assert "CPIN.JK" in tickers  # strongest in sector
    assert "JPFA.JK" not in tickers  # weaker in same sector
    assert "ADRO.JK" in tickers  # different sector
```

**Step 2: Implement**

```python
def deduplicate_by_sector(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the strongest WATCH signal per sector.

    BUY/SELL signals are always kept (they have entry/SL/TP).
    """
    best_by_sector: dict[str, dict[str, Any]] = {}
    always_keep: list[dict[str, Any]] = []

    for sig in signals:
        if sig.get("action") in ("BUY", "SELL"):
            always_keep.append(sig)
            continue
        sector = sig.get("sector", "unknown")
        if sector not in best_by_sector or (
            sig.get("signal_strength", 0) > best_by_sector[sector].get("signal_strength", 0)
        ):
            best_by_sector[sector] = sig

    return always_keep + list(best_by_sector.values())
```

**Step 3: Integrate in daily-summary and dashboard**

**Step 4: Commit**

---

## Task 5: IHSG Market Regime Check

**Objective:** Suppress BUY signals when IHSG is in a downtrend. When the overall market is falling, even good setups have higher failure rates.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py`

**Step 1: Write failing tests**

```python
def test_ihsg_downtrend_suppresses_buy():
    """BUY signal should be downgraded to WATCH when IHSG is in downtrend."""
    from backend.trading_signals import apply_market_regime
    signal = {"action": "BUY", "signal_strength": 0.55}
    ihsg = {"change_pct": -2.0, "series": [6000, 5900, 5800, 5700, 5600]}
    apply_market_regime(signal, ihsg)
    assert signal["action"] == "WATCH"

def test_ihsg_uptrend_preserves_buy():
    """BUY signal should be preserved when IHSG is rising."""
    from backend.trading_signals import apply_market_regime
    signal = {"action": "BUY", "signal_strength": 0.55}
    ihsg = {"change_pct": 1.5, "series": [5600, 5700, 5800, 5900, 6000]}
    apply_market_regime(signal, ihsg)
    assert signal["action"] == "BUY"
```

**Step 2: Implement**

```python
def apply_market_regime(signal: dict[str, Any], market_index: dict[str, Any]) -> None:
    """Downgrade BUY signals when IHSG is in a clear downtrend."""
    if signal.get("action") != "BUY":
        return
    change = float(market_index.get("change_pct", 0) or 0)
    series = market_index.get("series", [])
    if len(series) >= 5:
        sma5 = sum(series[-5:]) / len(series[-5:])
        last = series[-1]
        if last < sma5 and change < -1.0:
            signal["action"] = "WATCH"
            signal["reasons"] = signal.get("reasons", []) + [
                f"IHSG downtrend ({change:+.1f}%) — BUY suppressed"
            ]
```

**Step 3: Commit**

---

## Summary

| Task | What | Impact |
|------|------|--------|
| 1 | Sector-relative RSI boost | Boosts stocks more oversold than peers |
| 2 | Volume trend confirmation | Rising volume = stronger bounce signal |
| 3 | Signal decay | Stale WATCH signals auto-downgrade |
| 4 | Sector dedup | Keep best signal per sector only |
| 5 | IHSG market regime | Suppress BUY in market downtrend |

After all tasks: 20 WATCH → ~5 high-conviction signals with sector diversification.
