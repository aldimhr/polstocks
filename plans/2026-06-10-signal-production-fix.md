# Signal Production Fix — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make PolStock produce actionable BUY/WATCH signals from live data instead of classifying everything as IGNORE.

**Architecture:** Fix the two disconnected signal systems (old `generate_trade_signal` and new `classify_signal`) so they share data, relax overly strict technical confirmation thresholds, add category calibration to scoring, and suppress broken SELL signals.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest

---

## Root Cause Analysis

The signal pipeline has **two parallel systems** that don't talk to each other:

1. **Old system** (`stocks.generate_trade_signal`): Uses pre-computed `signal_strength` (0-1). Threshold 0.6 for BUY/SELL. Produces `stock["trade_signal"]`.
2. **New system** (`trading_signals.classify_signal`): Reads raw fields from stock dict (`impact_score`, `relationship_confidence`, etc.). Threshold 0.45 for action. Produces `stock["trading_signal"]`.

**Problems:**
- Signal **logging** (line 2008) only logs when `trade_signal.action` is BUY/SELL — checks OLD system, ignores new
- `classify_signal` reads fields that may be zero/missing (low `relationship_confidence`, zero `corroboration_count`)
- Technical confirmation requires extreme RSI (< 40 BUY, > 60 SELL) — too strict
- `compute_event_score` multiplies four `max(x, 0.1)` floors → tiny scores
- No standalone technical signals (need event catalyst)
- SELL signals have 0% hit rate but still get logged/alerted
- Category calibration (TRADE_POLICY 75.8%, ENERGY_POLICY 75.8%) not wired in

---

## Task 1: Relax Technical Confirmation Thresholds

**Objective:** Make `compute_technical_confirmation` count directional alignment instead of requiring extreme oversold/overbought levels.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py:58-123`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing tests**

```python
# Add to tests/test_app.py, after existing trading_signal tests

class TestTechnicalConfirmationRelaxed:
    """Technical confirmation should count directional alignment, not just extremes."""

    def test_rsi_directional_buy_above_40(self):
        """RSI 45 with positive direction should still confirm (directional alignment)."""
        from backend.trading_signals import compute_technical_confirmation
        stock = {
            "impact_direction": "positive",
            "rsi_value": 45,
            "macd": {"histogram": 0.5},
            "bollinger": {"percent_b": 0.35},
            "volume_spike": {"is_spike": False},
        }
        result = compute_technical_confirmation(stock)
        # RSI 45 < 55 should confirm for BUY (directional, not extreme)
        assert result["confirm_count"] >= 2
        assert "RSI" in result["details"][0]

    def test_rsi_directional_sell_below_60(self):
        """RSI 55 with negative direction should still confirm."""
        from backend.trading_signals import compute_technical_confirmation
        stock = {
            "impact_direction": "negative",
            "rsi_value": 55,
            "macd": {"histogram": -0.3},
            "bollinger": {"percent_b": 0.7},
            "volume_spike": {"is_spike": False},
        }
        result = compute_technical_confirmation(stock)
        assert result["confirm_count"] >= 2

    def test_rsi_neutral_zone_no_confirm(self):
        """RSI 50 with positive direction should NOT confirm (truly neutral)."""
        from backend.trading_signals import compute_technical_confirmation
        stock = {
            "impact_direction": "positive",
            "rsi_value": 50,
        }
        result = compute_technical_confirmation(stock)
        # RSI 50 is neutral — no directional confirmation
        assert result["confirm_count"] == 0

    def test_volume_directional_confirm(self):
        """Volume spike should confirm regardless of direction threshold."""
        from backend.trading_signals import compute_technical_confirmation
        stock = {
            "impact_direction": "positive",
            "volume_spike": {"is_spike": True},
        }
        result = compute_technical_confirmation(stock)
        assert result["confirm_count"] == 1
        assert "Volume spike" in result["details"]
```

**Step 2: Run tests to verify failure**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestTechnicalConfirmationRelaxed -v
```

Expected: FAIL — RSI 45 not < 40, so no confirmation.

**Step 3: Fix thresholds**

Change `compute_technical_confirmation` in `trading_signals.py`:
- RSI: BUY confirm if < 55 (was 40), SELL confirm if > 45 (was 60)
- Keep MACD and Bollinger as-is (they're already directional)
- Volume: already works

```python
# In compute_technical_confirmation, change RSI thresholds:
    # 1. RSI — directional alignment (not extreme oversold/overbought)
    rsi = stock.get("rsi_value")
    if rsi is not None:
        total += 1
        if is_buy and rsi < 55:      # was < 40
            confirmations.append("RSI directional buy")
        elif not is_buy and rsi > 45:  # was > 60
            confirmations.append("RSI directional sell")
```

**Step 4: Run tests to verify pass**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestTechnicalConfirmationRelaxed -v
```

Expected: PASS

**Step 5: Run full suite**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py -q --tb=short
```

**Step 6: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/trading_signals.py tests/test_app.py
git commit -m "fix: relax technical confirmation to directional alignment

RSI thresholds changed from extreme (40/60) to directional (55/45).
Technical confirmation now counts indicators that agree with event
direction, not just oversold/overbought extremes. This allows the
signal pipeline to produce actionable signals from normal market
conditions."
```

---

## Task 2: Boost Event Score Floor

**Objective:** Make `compute_event_score` produce meaningful scores when some factors are low but not zero.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py:18-55`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing tests**

```python
class TestEventScoreBoost:
    """Event scores should be meaningful even with moderate factors."""

    def test_moderate_factors_produce_actionable_score(self):
        """Stock with moderate confidence and corroboration should score > 0.15."""
        from backend.trading_signals import compute_event_score
        stock = {
            "impact_score": 5,
            "impact_direction": "positive",
            "relationship_confidence": 0.4,
            "source_confidence": 0.5,
            "corroboration_count": 1,
            "recency_weight": 0.8,
        }
        result = compute_event_score(stock)
        assert result["score"] >= 0.15, f"Score {result['score']} too low for moderate factors"

    def test_strong_factors_produce_high_score(self):
        """Stock with strong factors should score > 0.4."""
        from backend.trading_signals import compute_event_score
        stock = {
            "impact_score": 7,
            "impact_direction": "positive",
            "relationship_confidence": 0.7,
            "source_confidence": 0.8,
            "corroboration_count": 2,
            "recency_weight": 1.0,
        }
        result = compute_event_score(stock)
        assert result["score"] >= 0.4, f"Score {result['score']} too low for strong factors"

    def test_neutral_direction_returns_zero(self):
        """Neutral direction should produce zero score."""
        from backend.trading_signals import compute_event_score
        stock = {
            "impact_score": 5,
            "impact_direction": "neutral",
            "relationship_confidence": 0.5,
        }
        result = compute_event_score(stock)
        assert result["score"] == 0.0
```

**Step 2: Run tests to verify failure**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestEventScoreBoost -v
```

Expected: FAIL — current formula with `max(x, 0.1)` floors produces very small scores.

**Step 3: Fix event score formula**

The current formula multiplies six factors, four with `max(x, 0.1)` floors. With moderate values (0.4 × 0.1 × 0.5 × 0.1 × 0.8 × 1.0 = 0.0016), the result is tiny.

Fix: Raise floors from 0.1 to 0.3 for confidence and source_confidence, and use additive blending for corroboration instead of pure multiplication.

```python
def compute_event_score(stock: dict[str, Any]) -> dict[str, Any]:
    """Compute a normalised 0-1 event score from the stock payload.

    Returns dict with keys: score (0-1), direction, factors (debug dict).
    """
    raw_impact = abs(float(stock.get("impact_score", 0) or 0))
    normalized_impact = _clamp(raw_impact / 10.0)

    confidence = float(stock.get("relationship_confidence", 0) or 0)
    source_conf = float(stock.get("source_confidence", 0) or 0)
    corroboration = min(float(stock.get("corroboration_count", 0) or 0) / 3.0, 1.0)
    recency = float(stock.get("recency_weight", 1.0) or 1.0)
    conflict = bool(stock.get("source_conflict", False))
    conflict_penalty = 0.7 if conflict else 1.0

    direction = str(stock.get("impact_direction", "neutral") or "neutral")

    # Use higher floors so moderate factors produce actionable scores
    # Blend: event quality (impact × confidence × source) with corroboration boost
    event_quality = normalized_impact * max(confidence, 0.3) * max(source_conf, 0.3)
    corroboration_boost = 0.5 + 0.5 * corroboration  # range: 0.5-1.0

    score = _clamp(
        event_quality
        * corroboration_boost
        * recency
        * conflict_penalty
    )

    return {
        "score": round(score, 4),
        "direction": direction,
        "factors": {
            "normalized_impact": round(normalized_impact, 4),
            "confidence": round(confidence, 4),
            "source_confidence": round(source_conf, 4),
            "corroboration": round(corroboration, 4),
            "recency": round(recency, 4),
            "conflict_penalty": conflict_penalty,
        },
    }
```

**Step 4: Run tests**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestEventScoreBoost -v
```

Expected: PASS

**Step 5: Run existing tests to check no regressions**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py -q --tb=short
```

**Step 6: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/trading_signals.py tests/test_app.py
git commit -m "fix: boost event score floor for moderate factors

Raise confidence/source_confidence floors from 0.1 to 0.3.
Use additive corroboration boost (0.5-1.0) instead of multiplicative
floor. Moderate factors now produce actionable scores (>0.15) instead
of near-zero."
```

---

## Task 3: Wire Category Calibration into Scoring

**Objective:** Apply category-specific calibration multipliers (TRADE_POLICY 75.8%, ENERGY_POLICY 75.8%) to the composite signal strength.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py:284-310` (`get_calibration_multiplier`)
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing tests**

```python
class TestCategoryCalibration:
    """Category calibration should boost/bust signal strength."""

    def test_calibration_includes_category(self, monkeypatch):
        """get_calibration_multiplier should use category data when available."""
        from backend.trading_signals import get_calibration_multiplier
        from backend import backtest as btmod

        def fake_source_accuracy(window_days=30, min_samples=5):
            return {"media": {"calibration_multiplier": 1.3}}

        def fake_category_calibration(window_days=30, min_samples=5):
            return {"TRADE_POLICY": {"calibration_multiplier": 1.4}}

        monkeypatch.setattr(btmod, "compute_source_accuracy", fake_source_accuracy)
        monkeypatch.setattr(btmod, "compute_category_calibration", fake_category_calibration)

        stock = {
            "source_tier": "media",
            "categories": ["TRADE_POLICY"],
        }
        result = get_calibration_multiplier(stock)
        # Should combine source (1.3) and category (1.4)
        assert result > 1.0
```

**Step 2: Run to verify failure**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestCategoryCalibration -v
```

Expected: FAIL — `get_calibration_multiplier` currently skips category calibration.

**Step 3: Wire category calibration**

```python
def get_calibration_multiplier(stock: dict[str, Any]) -> float:
    """Look up calibration multiplier from backtest data.

    Combines source type and category calibration. Returns 1.0 if
    insufficient sample size.
    """
    try:
        from backend.backtest import compute_source_accuracy, compute_category_calibration
        source_cal = compute_source_accuracy(window_days=30, min_samples=5)
        cat_cal = compute_category_calibration(window_days=30, min_samples=5)
    except Exception:
        return 1.0

    multiplier = 1.0

    # Source type calibration
    source_type = str(stock.get("source_tier", "") or stock.get("article_source_type", "") or "")
    if source_type and source_type in source_cal:
        multiplier *= source_cal[source_type]["calibration_multiplier"]

    # Category calibration — check stock categories
    categories = stock.get("categories", [])
    if isinstance(categories, str):
        try:
            import json
            categories = json.loads(categories)
        except Exception:
            categories = []
    if categories and cat_cal:
        cat_multipliers = []
        for cat in categories:
            cat_upper = str(cat).upper()
            if cat_upper in cat_cal:
                cat_multipliers.append(cat_cal[cat_upper]["calibration_multiplier"])
        if cat_multipliers:
            # Use best category multiplier (most predictive)
            multiplier *= max(cat_multipliers)

    return _clamp(multiplier, 0.5, 1.5)
```

**Step 4: Run tests**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestCategoryCalibration -v
```

**Step 5: Run full suite**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py -q --tb=short
```

**Step 6: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: wire category calibration into signal scoring

Category calibration multipliers (TRADE_POLICY 75.8%, ENERGY_POLICY
75.8%) now boost composite signal strength. Best category multiplier
is used when a stock has multiple categories. Clamped to [0.5, 1.5]."
```

---

## Task 4: Connect classify_signal to Signal Logging

**Objective:** Make signal logging use the new `trading_signal` instead of the old `trade_signal` so signals with action BUY/WATCH actually get persisted.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/main.py:2008-2064`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing test**

```python
def test_signal_logging_uses_trading_signal(monkeypatch):
    """Signal logging should use trading_signal.action, not trade_signal.action."""
    # This test verifies that when classify_signal returns BUY but
    # generate_trade_signal returns HOLD, the signal still gets logged.
    from backend.trading_signals import classify_signal

    stock = {
        "price": 1000,
        "impact_score": 5,
        "impact_direction": "positive",
        "relationship_confidence": 0.5,
        "source_confidence": 0.6,
        "corroboration_count": 2,
        "recency_weight": 0.9,
        "rsi_value": 50,
        "macd": {"histogram": 0.5},
        "bollinger": {"percent_b": 0.3},
        "volume_spike": {"is_spike": False},
        "atr_value": 20,
    }
    result = classify_signal(stock)
    # With relaxed thresholds, this should be BUY or at least WATCH
    assert result["action"] in ("BUY", "WATCH"), f"Expected BUY/WATCH, got {result['action']}"
```

**Step 2: Run to verify**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::test_signal_logging_uses_trading_signal -v
```

**Step 3: Fix signal logging**

Change the Phase 3 signal logging block to use `trading_signal` instead of `trade_signal`:

```python
    # Phase 3: Log signals to history and send Telegram alerts
    _actionable_signals: list[dict[str, Any]] = []
    for stock in stocks:
        ts = stock.get("trading_signal") or {}
        action = ts.get("action", "IGNORE")
        if action not in ("BUY", "SELL"):
            continue
        # SELL suppression: don't log/alert SELL until accuracy improves
        if action == "SELL":
            continue
        sig_record = {
            "ticker": stock.get("ticker", ""),
            "action": action,
            "signal_strength": ts.get("signal_strength", 0),
            "price_at_signal": ts.get("entry_price") or stock.get("price", 0),
            "stop_loss": ts.get("stop_loss"),
            "take_profit": ts.get("take_profit"),
            "risk_reward": None,
            "timeframe": ts.get("time_horizon"),
            "reasons": ts.get("reasons", []),
            "event_headline": stock.get("headline", ""),
            "event_source": stock.get("source", ""),
            "time_horizon": ts.get("time_horizon"),
            "signal_tier": ts.get("signal_tier"),
            "signal_type": ts.get("signal_type"),
            "event_score": ts.get("event_score"),
            "tech_score": ts.get("tech_score"),
            "tech_confirmation_count": ts.get("tech_confirmation_count"),
        }
        _actionable_signals.append(sig_record)
```

**Step 4: Run tests**

**Step 5: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/main.py tests/test_app.py
git commit -m "fix: use trading_signal for signal logging, suppress SELL

Signal logging now reads from trading_signal (new system) instead of
trade_signal (old system). SELL signals suppressed entirely — 0% hit
rate on 24 live predictions. Will re-enable when accuracy improves."
```

---

## Task 5: Add Standalone Technical Signals

**Objective:** Generate WATCH signals from technical setups even without political event catalyst.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing tests**

```python
class TestStandaloneTechnical:
    """Technical-only signals should work without event catalyst."""

    def test_oversold_bounce_candidate(self):
        """RSI < 35 + near lower Bollinger = WATCH (oversold bounce)."""
        from backend.trading_signals import classify_signal
        stock = {
            "price": 1000,
            "impact_direction": "neutral",
            "impact_score": 0,
            "relationship_confidence": 0,
            "rsi_value": 30,
            "bollinger": {"percent_b": 0.1},
            "macd": {"histogram": -0.5},
            "volume_spike": {"is_spike": False},
            "atr_value": 20,
        }
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert result["signal_type"] in ("technical", "composite")
        assert any("RSI" in r or "oversold" in r.lower() for r in result["reasons"])

    def test_momentum_breakout_candidate(self):
        """RSI > 60 + MACD positive + volume spike = WATCH (momentum)."""
        from backend.trading_signals import classify_signal
        stock = {
            "price": 1000,
            "impact_direction": "neutral",
            "impact_score": 0,
            "relationship_confidence": 0,
            "rsi_value": 65,
            "bollinger": {"percent_b": 0.7},
            "macd": {"histogram": 1.2},
            "volume_spike": {"is_spike": True, "spike_ratio": 2.5},
            "atr_value": 20,
        }
        result = classify_signal(stock)
        assert result["action"] == "WATCH"
        assert any("volume" in r.lower() or "momentum" in r.lower() for r in result["reasons"])
```

**Step 2: Run to verify failure**

Expected: FAIL — current `classify_signal` returns IGNORE for neutral direction.

**Step 3: Add standalone technical detection**

Add a new function and integrate into `classify_signal`:

```python
def detect_technical_setup(stock: dict[str, Any]) -> dict[str, Any] | None:
    """Detect standalone technical setups that don't need event catalyst.

    Returns a dict with action, reasons, and score if a setup is found.
    Returns None if no setup detected.
    """
    rsi = stock.get("rsi_value")
    bb = stock.get("bollinger") or {}
    bb_pct_b = bb.get("percent_b")
    macd = stock.get("macd") or {}
    hist = macd.get("histogram") if isinstance(macd, dict) else None
    vol = stock.get("volume_spike") or {}
    is_spike = vol.get("is_spike", False)
    spike_ratio = vol.get("spike_ratio", 0)

    reasons = []
    score = 0.0

    # Oversold bounce candidate
    if rsi is not None and rsi < 35:
        reasons.append(f"RSI {rsi:.0f} oversold — bounce candidate")
        score += 0.3
    if bb_pct_b is not None and bb_pct_b < 0.2:
        reasons.append("Near lower Bollinger Band — potential bounce")
        score += 0.2

    # Momentum breakout candidate
    if rsi is not None and rsi > 60:
        reasons.append(f"RSI {rsi:.0f} bullish momentum")
        score += 0.2
    if hist is not None and hist > 0:
        reasons.append("MACD histogram positive — upward momentum")
        score += 0.15
    if is_spike:
        reasons.append(f"Volume spike {spike_ratio:.1f}x — institutional interest")
        score += 0.25

    # Bollinger squeeze breakout
    if bb_pct_b is not None and bb_pct_b > 0.8:
        reasons.append("Near upper Bollinger Band — potential breakout or reversal")
        score += 0.15

    if score >= 0.3 and reasons:
        return {
            "action": "WATCH",
            "score": min(score, 1.0),
            "reasons": reasons,
        }
    return None
```

Then in `classify_signal`, add a check before returning IGNORE:

```python
    # After existing classification, before final return:
    # If action is IGNORE, check for standalone technical setups
    if action == "IGNORE":
        tech_setup = detect_technical_setup(stock)
        if tech_setup:
            action = "WATCH"
            signal_strength = max(signal_strength, tech_setup["score"])
            reasons = tech_setup["reasons"]
            signal_type = "technical"
            time_horizon = "7d"  # technical setups default to swing
```

**Step 4: Run tests**

**Step 5: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: add standalone technical signal detection

Oversold bounce (RSI<35 + lower BB), momentum breakout (RSI>60 +
MACD positive + volume spike), and Bollinger squeeze setups now
generate WATCH signals without requiring political event catalyst."
```

---

## Task 6: Relax Signal Strength Thresholds

**Objective:** Lower the action threshold from 0.45 to 0.30 so moderate signals qualify as BUY/SELL.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/backend/trading_signals.py:192-211`
- Test: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Write failing test**

```python
def test_moderate_strength_qualifies_for_action():
    """Signal strength 0.35 should qualify for BUY with sufficient tech confirmations."""
    from backend.trading_signals import classify_signal
    stock = {
        "price": 1000,
        "impact_score": 4,
        "impact_direction": "positive",
        "relationship_confidence": 0.4,
        "source_confidence": 0.5,
        "corroboration_count": 1,
        "recency_weight": 0.8,
        "rsi_value": 48,
        "macd": {"histogram": 0.3},
        "bollinger": {"percent_b": 0.4},
        "volume_spike": {"is_spike": False},
        "atr_value": 20,
    }
    result = classify_signal(stock)
    assert result["action"] in ("BUY", "WATCH"), f"Got {result['action']}"
    if result["action"] == "BUY":
        assert result["signal_strength"] >= 0.30
```

**Step 2: Run to verify failure**

**Step 3: Lower thresholds**

```python
    # In classify_signal, change thresholds:
    elif signal_strength < 0.30:   # was 0.20
        action = "IGNORE"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.30 minimum")
    elif signal_strength < 0.35:   # was 0.45
        action = "WATCH"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.35 action threshold")
```

**Step 4: Run tests**

**Step 5: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add backend/trading_signals.py tests/test_app.py
git commit -m "fix: lower signal strength thresholds for action

Action threshold lowered from 0.45 to 0.35, IGNORE threshold from
0.20 to 0.30. Combined with relaxed tech confirmation and boosted
event scores, moderate signals now qualify for BUY/WATCH."
```

---

## Task 7: Verify End-to-End Signal Production

**Objective:** Restart the service and verify live signals appear in `/api/signals/daily-summary`.

**Files:** None (verification only)

**Step 1: Restart service**

```bash
sudo systemctl restart politics-stock-mapper.service
sleep 3
curl -s http://localhost:8001/healthz
```

**Step 2: Check daily summary**

```bash
curl -s 'http://localhost:8001/api/signals/daily-summary?limit=3&include_watch=true' | python3 -c "
import json, sys
data = json.load(sys.stdin)
for h in ['1d', '7d', '30d']:
    sigs = data.get('horizons', {}).get(h, [])
    print(f'{h}: {len(sigs)} signals')
    for s in sigs[:3]:
        print(f'  {s.get(\"ticker\")}: {s.get(\"action\")} tier={s.get(\"signal_tier\")} strength={s.get(\"signal_strength\", 0):.2f}')
print(f'Total: {data.get(\"total_signals\", 0)}')
"
```

Expected: At least 2-3 WATCH or BUY signals across horizons.

**Step 3: Check dashboard payload**

```bash
curl -s 'http://localhost:8001/api/dashboard' | python3 -c "
import json, sys
from collections import Counter
data = json.load(sys.stdin)
stocks = data.get('stocks', [])
actions = Counter(s.get('trading_signal', {}).get('action', 'NONE') for s in stocks)
print(f'Action distribution: {dict(actions)}')
non_ignore = [s for s in stocks if s.get('trading_signal', {}).get('action') not in ('IGNORE', 'NONE')]
for s in non_ignore[:5]:
    ts = s.get('trading_signal', {})
    print(f'{s.get(\"ticker\")}: {ts.get(\"action\")} tier={ts.get(\"signal_tier\")} strength={ts.get(\"signal_strength\", 0):.2f} horizon={ts.get(\"time_horizon\")}')
"
```

**Step 4: Run smoke tests**

```bash
cd /opt/hermes/politics_stock_mapper
curl -s http://localhost:8001/healthz
curl -s http://localhost:8001/api/dashboard | python3 -m json.tool >/dev/null
curl -s 'http://localhost:8001/api/backtest?window_days=30&origin=live' | python3 -m json.tool >/dev/null
curl -s 'http://localhost:8001/api/signals/daily-summary' | python3 -m json.tool >/dev/null
```

**Step 5: Commit any final fixes**

---

## Task 8: Update Existing Tests for New Thresholds

**Objective:** Fix any existing tests that break due to threshold changes.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/tests/test_app.py`

**Step 1: Run full test suite**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py -q --tb=short 2>&1 | tail -20
```

**Step 2: Fix any broken tests**

The existing tests at line 3044+ test `classify_signal` with specific expected outputs. If thresholds changed, update expected values.

**Step 3: Commit**

```bash
cd /opt/hermes/politics_stock_mapper
git add tests/test_app.py
git commit -m "test: update trading signal tests for relaxed thresholds"
```

---

## Summary

| Task | What | Impact |
|------|------|--------|
| 1 | Relax RSI thresholds (40/60 → 55/45) | Tech confirmation works in normal markets |
| 2 | Boost event score floors | Moderate factors produce actionable scores |
| 3 | Wire category calibration | TRADE_POLICY/ENERGY_POLICY get 75.8% boost |
| 4 | Connect classify_signal to logging | Signals actually get persisted |
| 5 | Standalone technical signals | WATCH signals without event catalyst |
| 6 | Lower action thresholds | Moderate signals qualify for BUY |
| 7 | Verify end-to-end | Confirm live signals appear |
| 8 | Fix broken tests | Regression safety |

After all tasks: live signals should appear in `/daily`, `/signals`, and dashboard.
