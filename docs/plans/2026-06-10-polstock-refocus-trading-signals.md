# PolStock Refocus — Short-Term Trading Signals Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Refocus PolStock from a politics dashboard into a **1d / 7d / 30d trading signal assistant** — the missing core is a dedicated Signal Decision Layer that turns event+technical data into clear BUY/SELL/WATCH/IGNORE decisions with horizons, tiers, entry/SL/TP, and reasons.

**Architecture:** Add a new pure-function module `backend/trading_signals.py` that consumes the existing stock dict from `build_refresh_payload()` and produces a `trading_signal` object. Wire it into the dashboard payload first (read-only, no alerts yet), then build horizon-aware persistence, calibration, daily summary, and dashboard/bot UX around it. Keep the old `trade_signal` field for backward compatibility throughout.

**Tech Stack:** Python 3.11, FastAPI, SQLite, pytest, existing `backend/stocks.py` + `backend/scoring.py` + `backend/signals.py` + `backend/backtest.py` infrastructure.

**SPEC:** `SPEC.md` (1111 lines, committed as `1f434de`)

**Repo:** `/opt/hermes/politics_stock_mapper`

**Test runner:**
```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/ -q
```

**Service restart / verify:**
```bash
sudo systemctl restart politics-stock-mapper.service
curl -s http://localhost:8001/healthz
curl -s http://localhost:8001/api/dashboard | python3 -m json.tool >/dev/null
```

---

## Key Context

### Current stock dict (from `build_refresh_payload`, line ~1813 in `backend/main.py`)

Each stock in `payload["stocks"]` already carries:

```python
{
    "ticker": "CPIN.JK",
    "price": 3300,
    "change_pct": 0.5,
    "impact_score": 4.2,          # 0-10 scale
    "impact_direction": "positive", # positive/negative/neutral/mixed
    "relationship_confidence": 0.65,
    "confidence": 0.6,
    "corroboration_count": 2,
    "corroboration_score": 0.7,
    "source_confidence": 0.8,
    "source_tier": "tier_1",
    "source_conflict": False,
    "recency_weight": 0.9,
    "relationship_count": 3,
    "validation_status": "confirmed",
    "validation_multiplier": 1.1,
    "historical_reliability_multiplier": 1.0,
    "channel_reliability_multiplier": 1.0,
    "sentiment_momentum": "strengthening",
    "event_cluster_count": 2,
    "rsi_value": 35.0,
    "macd": {"macd": 1.2, "signal": 0.8, "histogram": 0.4},
    "trend": {"trend": "bullish", "strength": 0.7},
    "atr_value": 45.0,
    "bollinger": {"percent_b": 0.3, "squeeze": False, "bandwidth": 0.04},
    "volume_spike": {"spike_ratio": 2.1, "is_spike": True},
    "support_resistance": {"support": [3200], "resistance": [3400]},
    "trade_signal": {"action": "HOLD", "entry": 3300, ...},  # OLD — keep for compat
    "signal_strength": 0.45,
    "pinned": True,
    "in_portfolio": True,
}
```

### Current `generate_trade_signal()` (backend/stocks.py:231)

- Gate: `signal_strength < 0.6` → HOLD
- Gate: direction not positive/negative → HOLD
- Uses RSI, MACD, trend, ATR, BB, volume as dampeners/boosters
- Produces entry, SL (1.5×ATR), TP (3.0×ATR), risk_reward, timeframe, reasons
- Timeframe is ad-hoc: strength ≥0.8 → "1-3d", ≥0.65 → "1w", else "intraday"

### Live backtest numbers (as of 2026-06-10)

| Metric | Value |
|--------|-------|
| Live predictions | 235 |
| Hit rate | 45.1% |
| Neutral baseline | 54.5% |
| Edge | -9.4% |
| Positive predictions | 30, 43.3% hit |
| Negative predictions | 24, 0.0% hit |
| High confidence | 15, 26.7% hit |
| Medium confidence | 75, 53.3% hit |

---

## Phase 0 — Safety Baseline (already partially done)

SPEC.md is written and committed. Phase 0 remaining work: add regression tests that lock down the current API response shapes so we don't accidentally break them during Phase 1.

### Task 0.1: Add API shape snapshot tests

**Objective:** Lock down current `/api/dashboard` and `/api/signals/history` response shapes so future changes are caught.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write tests**

Append to `tests/test_app.py`:

```python
class TestAPIShapeBaseline:
    """Regression tests: lock down current response shapes before refocus."""

    def test_dashboard_has_expected_top_level_keys(self, monkeypatch):
        """Dashboard response must contain these top-level keys."""
        _patch_fetch_news_bundle(monkeypatch, lambda *a, **k: ([], []))
        _patch_fetch_stock_quotes(monkeypatch, lambda *a, **k: ({}, []))
        _patch_fetch_market_index(monkeypatch, lambda *a, **k: ({}))
        _patch_validation_series(monkeypatch, lambda *a, **k: ({}, []))
        resp = client.get("/api/dashboard?window=1d")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("watchlist", "payload", "nlp_status", "dashboard_cues"):
            assert key in data, f"Missing top-level key: {key}"

    def test_dashboard_stock_has_trade_signal(self, monkeypatch):
        """Each stock in payload must have the OLD trade_signal field."""
        _patch_fetch_news_bundle(monkeypatch, lambda *a, **k: ([], []))
        _patch_fetch_stock_quotes(monkeypatch, lambda *a, **k: ({}, []))
        _patch_fetch_market_index(monkeypatch, lambda *a, **k: ({}))
        _patch_validation_series(monkeypatch, lambda *a, **k: ({}, []))
        resp = client.get("/api/dashboard?window=1d")
        assert resp.status_code == 200
        stocks = resp.json().get("payload", {}).get("stocks", [])
        assert len(stocks) > 0
        for s in stocks[:3]:
            assert "trade_signal" in s
            ts = s["trade_signal"]
            assert "action" in ts
            assert ts["action"] in ("BUY", "SELL", "HOLD")

    def test_signals_history_has_expected_shape(self):
        resp = client.get("/api/signals/history")
        assert resp.status_code == 200
        data = resp.json()
        assert "signals" in data or "history" in data

    def test_backtest_has_expected_shape(self):
        resp = client.get("/api/backtest?window_days=30")
        assert resp.status_code == 200
        data = resp.json()
        for key in ("total_predictions", "hit_rate", "baseline"):
            assert key in data
```

**Step 2: Run tests**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestAPIShapeBaseline -v
```

Expected: 4 PASS.

**Step 3: Commit**

```bash
git add tests/test_app.py
git commit -m "test: add API shape regression tests for refocus safety baseline"
```

---

## Phase 1 — Trading Signal Decision Layer

The core new module. Pure functions, no DB changes, no alert changes.

### Task 1.1: Create `backend/trading_signals.py` with `compute_event_score`

**Objective:** Create the new module with the first function that normalizes event data into a 0-1 event score.

**Files:**
- Create: `backend/trading_signals.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
from backend.trading_signals import compute_event_score

class TestComputeEventScore:
    def test_returns_dict_with_score_key(self):
        stock = {"impact_score": 5.0, "relationship_confidence": 0.7,
                 "source_confidence": 0.8, "corroboration_count": 2,
                 "recency_weight": 0.9, "source_conflict": False}
        result = compute_event_score(stock)
        assert "score" in result
        assert 0.0 <= result["score"] <= 1.0

    def test_zero_impact_gives_zero_score(self):
        stock = {"impact_score": 0, "relationship_confidence": 0.0,
                 "source_confidence": 0.0, "corroboration_count": 0,
                 "recency_weight": 0.0, "source_conflict": False}
        result = compute_event_score(stock)
        assert result["score"] == 0.0

    def test_high_impact_no_conflict_gives_high_score(self):
        stock = {"impact_score": 8.0, "relationship_confidence": 0.9,
                 "source_confidence": 0.9, "corroboration_count": 3,
                 "recency_weight": 1.0, "source_conflict": False}
        result = compute_event_score(stock)
        assert result["score"] >= 0.5

    def test_source_conflict_penalizes(self):
        stock = {"impact_score": 8.0, "relationship_confidence": 0.9,
                 "source_confidence": 0.9, "corroboration_count": 3,
                 "recency_weight": 1.0, "source_conflict": True}
        result_conflict = compute_event_score(stock)
        stock["source_conflict"] = False
        result_no_conflict = compute_event_score(stock)
        assert result_conflict["score"] < result_no_conflict["score"]

    def test_returns_direction(self):
        stock = {"impact_score": 5.0, "impact_direction": "positive",
                 "relationship_confidence": 0.5, "source_confidence": 0.5,
                 "corroboration_count": 1, "recency_weight": 0.8,
                 "source_conflict": False}
        result = compute_event_score(stock)
        assert "direction" in result
        assert result["direction"] == "positive"
```

**Step 2: Run tests — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestComputeEventScore -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'backend.trading_signals'`

**Step 3: Implement `backend/trading_signals.py`**

```python
"""Trading signal decision layer.

Pure functions that convert stock payload dicts into trading signals
with action (BUY/SELL/WATCH/IGNORE), time horizon (1d/7d/30d),
confidence tier (A/B/C/D), entry/SL/TP, and reasons.

Does NOT touch the database or send alerts — that's the caller's job.
"""
from __future__ import annotations

from typing import Any

DIRECTION_MAP = {"positive": 1.0, "negative": -1.0, "neutral": 0.0, "mixed": 0.0}


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


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

    score = _clamp(
        normalized_impact
        * max(confidence, 0.1)
        * max(source_conf, 0.1)
        * max(corroboration, 0.1)
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

**Step 4: Run tests — verify PASS**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestComputeEventScore -v
```

Expected: 5 PASS.

**Step 5: Commit**

```bash
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: add trading_signals.py with compute_event_score"
```

---

### Task 1.2: Add `compute_technical_confirmation`

**Objective:** Score how many core technical indicators agree with the signal direction.

**Files:**
- Modify: `backend/trading_signals.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
from backend.trading_signals import compute_technical_confirmation

class TestComputeTechnicalConfirmation:
    def _bullish_stock(self):
        return {
            "price": 3300, "impact_direction": "positive",
            "rsi_value": 35.0,  # oversold → BUY confirm
            "macd": {"histogram": 0.5},  # positive → BUY confirm
            "bollinger": {"percent_b": 0.15, "squeeze": False},  # near lower → BUY confirm
            "volume_spike": {"is_spike": True, "spike_ratio": 2.5},  # spike → BUY confirm
            "trend": {"trend": "bullish"},
            "support_resistance": {"support": [3200], "resistance": [3400]},
        }

    def test_all_4_bullish_confirm(self):
        result = compute_technical_confirmation(self._bullish_stock())
        assert result["confirm_count"] == 4
        assert result["total"] == 4
        assert result["score"] == 1.0

    def test_no_indicators_returns_zero(self):
        stock = {"price": 100, "impact_direction": "neutral"}
        result = compute_technical_confirmation(stock)
        assert result["confirm_count"] == 0
        assert result["total"] == 0
        assert result["score"] == 0.0

    def test_mixed_indicators_partial_score(self):
        stock = {
            "price": 100, "impact_direction": "positive",
            "rsi_value": 35.0,       # BUY confirm
            "macd": {"histogram": -0.3},  # NOT confirm (negative for BUY)
            "bollinger": {"percent_b": 0.5},  # NOT confirm (middle)
            "volume_spike": {"is_spike": False},
        }
        result = compute_technical_confirmation(stock)
        assert result["confirm_count"] == 1
        assert result["score"] == 0.25

    def test_negative_direction_checks_sell_conditions(self):
        stock = {
            "price": 100, "impact_direction": "negative",
            "rsi_value": 75.0,  # overbought → SELL confirm
            "macd": {"histogram": -0.5},  # negative → SELL confirm
        }
        result = compute_technical_confirmation(stock)
        assert result["confirm_count"] >= 2
```

**Step 2: Run tests — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestComputeTechnicalConfirmation -v
```

Expected: FAIL — `ImportError: cannot import name 'compute_technical_confirmation'`

**Step 3: Implement in `backend/trading_signals.py`**

Append after `compute_event_score`:

```python
def compute_technical_confirmation(stock: dict[str, Any]) -> dict[str, Any]:
    """Count how many core technical indicators agree with the stock's direction.

    Core indicators:
    1. RSI: BUY if < 40, SELL if > 60
    2. MACD histogram: BUY if > 0, SELL if < 0
    3. Bollinger %B: BUY if < 0.2, SELL if > 0.8
    4. Volume spike: confirm if is_spike AND direction aligns

    Returns dict: confirm_count, total, score (0-1), details.
    """
    direction = str(stock.get("impact_direction", "neutral") or "neutral")
    if direction not in ("positive", "negative"):
        return {"confirm_count": 0, "total": 0, "score": 0.0, "details": []}

    is_buy = direction == "positive"
    confirmations: list[str] = []
    total = 0

    # 1. RSI
    rsi = stock.get("rsi_value")
    if rsi is not None:
        total += 1
        if is_buy and rsi < 40:
            confirmations.append("RSI oversold")
        elif not is_buy and rsi > 60:
            confirmations.append("RSI overbought")

    # 2. MACD histogram
    macd = stock.get("macd")
    hist = None
    if isinstance(macd, dict):
        hist = macd.get("histogram")
    elif isinstance(macd, (int, float)):
        hist = macd
    if hist is not None:
        total += 1
        if is_buy and hist > 0:
            confirmations.append("MACD histogram positive")
        elif not is_buy and hist < 0:
            confirmations.append("MACD histogram negative")

    # 3. Bollinger %B
    bb = stock.get("bollinger")
    if isinstance(bb, dict) and bb.get("percent_b") is not None:
        total += 1
        pct_b = bb["percent_b"]
        if is_buy and pct_b < 0.2:
            confirmations.append("Bollinger %B near lower band")
        elif not is_buy and pct_b > 0.8:
            confirmations.append("Bollinger %B near upper band")

    # 4. Volume spike
    vol = stock.get("volume_spike")
    if isinstance(vol, dict) and vol.get("is_spike"):
        total += 1
        confirmations.append("Volume spike")

    score = len(confirmations) / total if total > 0 else 0.0
    return {
        "confirm_count": len(confirmations),
        "total": total,
        "score": round(score, 4),
        "details": confirmations,
    }
```

**Step 4: Run tests — verify PASS**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestComputeTechnicalConfirmation -v
```

Expected: 4 PASS.

**Step 5: Commit**

```bash
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: add compute_technical_confirmation to trading_signals"
```

---

### Task 1.3: Add `infer_time_horizon`

**Objective:** Determine whether a signal is 1d, 7d, or 30d based on event recency, stage, and technical setup.

**Files:**
- Modify: `backend/trading_signals.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
from backend.trading_signals import infer_time_horizon

class TestInferTimeHorizon:
    def test_default_is_7d(self):
        assert infer_time_horizon({}, {}, {}) == "7d"

    def test_breaking_event_short_horizon(self):
        stock = {"event_stage": "breaking"}
        event = {"score": 0.8}
        tech = {"confirm_count": 2, "total": 4}
        assert infer_time_horizon(stock, event, tech) == "1d"

    def test_strong_tech_with_high_event_is_1d(self):
        stock = {}
        event = {"score": 0.7}
        tech = {"confirm_count": 3, "total": 4}
        assert infer_time_horizon(stock, event, tech) == "1d"

    def test_established_event_is_30d(self):
        stock = {"event_stage": "established"}
        event = {"score": 0.5}
        tech = {"confirm_count": 2, "total": 4}
        assert infer_time_horizon(stock, event, tech) == "30d"

    def test_weak_tech_moderate_event_is_7d(self):
        stock = {}
        event = {"score": 0.4}
        tech = {"confirm_count": 1, "total": 4}
        assert infer_time_horizon(stock, event, tech) == "7d"
```

**Step 2: Run tests — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestInferTimeHorizon -v
```

Expected: FAIL.

**Step 3: Implement in `backend/trading_signals.py`**

Append:

```python
def infer_time_horizon(
    stock: dict[str, Any],
    event_score: dict[str, Any],
    tech: dict[str, Any],
) -> str:
    """Determine signal time horizon: '1d', '7d', or '30d'.

    Rules:
    - event_stage == "breaking" → 1d
    - event_stage == "established" → 30d
    - event_score >= 0.5 AND tech confirm >= 3/4 → 1d (strong setup, act fast)
    - event_score >= 0.3 AND tech confirm >= 2/4 → 7d (developing swing)
    - else → 7d default
    """
    stage = str(stock.get("event_stage", "") or "")
    ev_score = float(event_score.get("score", 0) or 0)
    confirm = int(tech.get("confirm_count", 0) or 0)
    total = int(tech.get("total", 0) or 0)

    if stage == "breaking":
        return "1d"
    if stage == "established":
        return "30d"

    if ev_score >= 0.5 and total >= 4 and confirm >= 3:
        return "1d"
    if ev_score >= 0.3 and total >= 4 and confirm >= 2:
        return "7d"

    return "7d"
```

**Step 4: Run tests — verify PASS**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestInferTimeHorizon -v
```

Expected: 5 PASS.

**Step 5: Commit**

```bash
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: add infer_time_horizon to trading_signals"
```

---

### Task 1.4: Add `classify_signal` — the core decision function

**Objective:** Combine event score + tech confirmation + horizon into a final BUY/SELL/WATCH/IGNORE decision with tier, entry/SL/TP, and reasons.

**Files:**
- Modify: `backend/trading_signals.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Append to `tests/test_app.py`:

```python
from backend.trading_signals import classify_signal

class TestClassifySignal:
    def _strong_buy_stock(self):
        return {
            "ticker": "CPIN.JK", "price": 3300,
            "impact_score": 7.0, "impact_direction": "positive",
            "relationship_confidence": 0.8, "source_confidence": 0.8,
            "corroboration_count": 3, "recency_weight": 1.0,
            "source_conflict": False, "event_stage": "developing",
            "rsi_value": 35.0,
            "macd": {"histogram": 0.5},
            "bollinger": {"percent_b": 0.15},
            "volume_spike": {"is_spike": True, "spike_ratio": 2.0},
            "trend": {"trend": "bullish"},
            "atr_value": 50.0,
            "support_resistance": {"support": [3200], "residence": [3400]},
        }

    def test_strong_stock_classifies_as_buy(self):
        result = classify_signal(self._strong_buy_stock())
        assert result["action"] == "BUY"
        assert result["time_horizon"] in ("1d", "7d", "30d")
        assert result["signal_tier"] in ("A", "B", "C", "D")
        assert result["entry_price"] > 0
        assert result["stop_loss"] is not None
        assert result["take_profit"] is not None
        assert isinstance(result["reasons"], list)

    def test_neutral_direction_is_watch_or_ignore(self):
        stock = {
            "ticker": "BBCA.JK", "price": 9500,
            "impact_score": 2.0, "impact_direction": "neutral",
            "relationship_confidence": 0.3, "source_confidence": 0.3,
            "corroboration_count": 0, "recency_weight": 0.5,
            "source_conflict": False,
        }
        result = classify_signal(stock)
        assert result["action"] in ("WATCH", "IGNORE")

    def test_source_conflict_downgrades(self):
        stock = self._strong_buy_stock()
        stock["source_conflict"] = True
        result = classify_signal(stock)
        # Should be WATCH or lower tier, not Tier A
        if result["action"] == "BUY":
            assert result["signal_tier"] != "A"

    def test_result_has_all_required_keys(self):
        result = classify_signal(self._strong_buy_stock())
        required = {"action", "time_horizon", "signal_tier", "signal_type",
                     "signal_strength", "event_score", "tech_score",
                     "tech_confirmation_count", "entry_price", "stop_loss",
                     "take_profit", "reasons", "invalidation"}
        assert required.issubset(result.keys())
```

**Step 2: Run tests — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestClassifySignal -v
```

Expected: FAIL.

**Step 3: Implement `classify_signal` in `backend/trading_signals.py`**

Append:

```python
def classify_signal(stock: dict[str, Any]) -> dict[str, Any]:
    """Full signal classification: action, horizon, tier, entry/SL/TP, reasons.

    This is the main entry point for the trading signal decision layer.
    Takes a stock dict from build_refresh_payload and returns a trading_signal.
    """
    price = float(stock.get("price", 0) or 0)
    atr = float(stock.get("atr_value", 0) or 0)
    if atr <= 0 and price > 0:
        atr = price * 0.02  # fallback: 2% of price

    # Step 1: Event score
    ev = compute_event_score(stock)
    ev_score = ev["score"]
    direction = ev["direction"]

    # Step 2: Technical confirmation
    tech = compute_technical_confirmation(stock)
    tech_score = tech["score"]
    confirm_count = tech["confirm_count"]
    tech_total = tech["total"]

    # Step 3: Composite signal strength
    calibration = 1.0  # placeholder for Phase 3
    signal_strength = round(
        ev_score * 0.55 + tech_score * 0.35 + calibration * 0.10, 4
    )

    # Step 4: Determine action
    reasons: list[str] = []
    has_conflict = bool(stock.get("source_conflict", False))

    if direction not in ("positive", "negative"):
        action = "WATCH" if ev_score > 0.1 else "IGNORE"
        reasons.append(f"Direction is {direction} — no directional conviction")
    elif signal_strength < 0.20:
        action = "IGNORE"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.20 minimum")
    elif signal_strength < 0.45:
        action = "WATCH"
        reasons.append(f"Signal strength {signal_strength:.2f} below 0.45 action threshold")
    else:
        # We have enough strength — classify BUY or SELL
        if direction == "positive":
            if confirm_count < 2:
                action = "WATCH"
                reasons.append(f"Only {confirm_count}/4 tech confirmations, need 2+ for BUY")
            else:
                action = "BUY"
        else:  # negative
            if confirm_count < 3:
                action = "WATCH"
                reasons.append(f"Only {confirm_count}/4 tech confirmations, need 3+ for SELL (strict mode)")
            else:
                action = "SELL"

    # Step 5: Horizon
    time_horizon = infer_time_horizon(stock, ev, tech)

    # Step 6: Tier
    signal_tier = "D"
    if action == "BUY" and signal_strength >= 0.70 and confirm_count >= 3 and not has_conflict:
        signal_tier = "A"
    elif action == "BUY" and signal_strength >= 0.60 and confirm_count >= 2 and not has_conflict:
        signal_tier = "B"
    elif action in ("BUY", "SELL") and signal_strength >= 0.45:
        signal_tier = "C"
    elif action == "WATCH":
        signal_tier = "C"
    # else D

    # Step 7: Entry / SL / TP
    entry_price = price
    stop_loss = None
    take_profit = None
    if action == "BUY" and price > 0:
        stop_loss = round(price - 1.5 * atr, 2)
        take_profit = round(price + 3.0 * atr, 2)
    elif action == "SELL" and price > 0:
        stop_loss = round(price + 1.5 * atr, 2)
        take_profit = round(price - 3.0 * atr, 2)

    # Step 8: Reasons enrichment
    if confirm_count > 0:
        reasons.extend(tech["details"])
    if has_conflict:
        reasons.append("Source conflict detected — reduced confidence")

    # Step 9: Invalidation
    invalidation = ""
    if action == "BUY":
        invalidation = f"Close below {stop_loss} or direction reversal"
    elif action == "SELL":
        invalidation = f"Close above {stop_loss} or direction reversal"

    return {
        "action": action,
        "time_horizon": time_horizon,
        "signal_tier": signal_tier,
        "signal_type": "composite" if ev_score > 0 and tech_score > 0 else ("event" if ev_score > 0 else "technical"),
        "signal_strength": signal_strength,
        "event_score": ev_score,
        "tech_score": tech_score,
        "tech_confirmation_count": confirm_count,
        "entry_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "reasons": reasons,
        "invalidation": invalidation,
    }
```

**Step 4: Run tests — verify PASS**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestClassifySignal -v
```

Expected: 4 PASS.

**Step 5: Commit**

```bash
git add backend/trading_signals.py tests/test_app.py
git commit -m "feat: add classify_signal — core trading signal decision layer"
```

---

### Task 1.5: Wire `trading_signal` into dashboard payload

**Objective:** Add `trading_signal` to each stock in `build_refresh_payload` alongside the existing `trade_signal`, then add integration test.

**Files:**
- Modify: `backend/main.py` (after line ~1996 where pinned tagging runs, before `sort_stocks_by_impact`)
- Modify: `tests/test_app.py`

**Step 1: Write failing integration test**

Append to `tests/test_app.py`:

```python
class TestDashboardTradingSignal:
    def test_dashboard_stocks_have_trading_signal(self, monkeypatch):
        _patch_fetch_news_bundle(monkeypatch, lambda *a, **k: ([], []))
        _patch_fetch_stock_quotes(monkeypatch, lambda *a, **k: ({}, []))
        _patch_fetch_market_index(monkeypatch, lambda *a, **k: ({}))
        _patch_validation_series(monkeypatch, lambda *a, **k: ({}, []))
        resp = client.get("/api/dashboard?window=1d")
        assert resp.status_code == 200
        stocks = resp.json().get("payload", {}).get("stocks", [])
        assert len(stocks) > 0
        for s in stocks[:3]:
            assert "trading_signal" in s
            ts = s["trading_signal"]
            assert "action" in ts
            assert ts["action"] in ("BUY", "SELL", "WATCH", "IGNORE")
            assert ts["time_horizon"] in ("1d", "7d", "30d")
            assert ts["signal_tier"] in ("A", "B", "C", "D")
            # Old field must still exist
            assert "trade_signal" in s
```

**Step 2: Run test — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestDashboardTradingSignal -v
```

Expected: FAIL — `trading_signal` not in stock dict.

**Step 3: Wire into `backend/main.py`**

In `backend/main.py`, after the pinned tagging block (around line 2000) and before `stocks = sort_stocks_by_impact(stocks)`, add:

```python
    # Trading signal decision layer
    from backend.trading_signals import classify_signal
    for stock in stocks:
        stock["trading_signal"] = classify_signal(stock)
```

**Step 4: Run test — verify PASS**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestDashboardTradingSignal -v
```

Expected: PASS.

**Step 5: Run full suite — verify no regressions**

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py -q
```

Expected: All tests pass (existing + new).

**Step 6: Restart service and verify live**

```bash
sudo systemctl restart politics-stock-mapper.service
sleep 3
curl -s http://localhost:8001/api/dashboard | python3 -c "
import json, sys
d = json.load(sys.stdin)
stocks = d.get('payload', {}).get('stocks', [])
for s in stocks[:3]:
    ts = s.get('trading_signal', {})
    print(f\"{s['ticker']}: {ts.get('action')} {ts.get('time_horizon')} Tier {ts.get('signal_tier')} strength={ts.get('signal_strength')}\")"
```

Expected: Output like `CPIN.JK: WATCH 7d Tier C strength=0.32`.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: wire trading_signal into dashboard payload alongside trade_signal"
```

---

### Task 1.6: Add `rank_trade_signals` helper

**Objective:** Sort classified signals by strength for daily summary and dashboard display.

**Files:**
- Modify: `backend/trading_signals.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing test**

Append to `tests/test_app.py`:

```python
from backend.trading_signals import rank_trade_signals

class TestRankTradeSignals:
    def test_buy_signals_rank_before_watch(self):
        signals = [
            {"action": "WATCH", "signal_strength": 0.5, "signal_tier": "C", "ticker": "A.JK"},
            {"action": "BUY", "signal_strength": 0.6, "signal_tier": "B", "ticker": "B.JK"},
        ]
        ranked = rank_trade_signals(signals)
        assert ranked[0]["ticker"] == "B.JK"

    def test_higher_tier_ranks_first(self):
        signals = [
            {"action": "BUY", "signal_strength": 0.6, "signal_tier": "C", "ticker": "A.JK"},
            {"action": "BUY", "signal_strength": 0.7, "signal_tier": "A", "ticker": "B.JK"},
        ]
        ranked = rank_trade_signals(signals)
        assert ranked[0]["ticker"] == "B.JK"

    def test_empty_list_returns_empty(self):
        assert rank_trade_signals([]) == []
```

**Step 2: Run — verify FAIL**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestRankTradeSignals -v
```

**Step 3: Implement**

Append to `backend/trading_signals.py`:

```python
_ACTION_ORDER = {"BUY": 0, "SELL": 1, "WATCH": 2, "IGNORE": 3}
_TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}


def rank_trade_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort signals by action priority (BUY > SELL > WATCH > IGNORE), then tier, then strength."""
    return sorted(
        signals,
        key=lambda s: (
            _ACTION_ORDER.get(s.get("action", "IGNORE"), 4),
            _TIER_ORDER.get(s.get("signal_tier", "D"), 4),
            -float(s.get("signal_strength", 0) or 0),
        ),
    )
```

**Step 4: Run — verify PASS + commit**

```bash
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/test_app.py::TestRankTradeSignals -v && \
git add backend/trading_signals.py tests/test_app.py && \
git commit -m "feat: add rank_trade_signals helper"
```

---

## Phase 2 — Horizon-Aware Persistence

Store horizon/tier/type in `signal_history` and `predictions` tables.

### Task 2.1: Add migration columns to `signal_history`

**Objective:** Add `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count`, `calibration_multiplier`, `invalidation_reason` columns to signal_history.

**Files:**
- Modify: `backend/signals.py` (in `init_signal_tables`, around line 41)
- Modify: `tests/test_app.py`

**Approach:** Use the existing pattern in `backend/backtest.py:126` — `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in a migration block that runs after table creation. SQLite `ALTER TABLE ADD COLUMN` is safe for existing tables.

**Step 1:** Write test that verifies new columns exist after init.
**Step 2:** Add ALTER TABLE statements in `init_signal_tables()`.
**Step 3:** Update `log_signal()` to accept and store new fields.
**Step 4:** Run tests, restart service, commit.

---

### Task 2.2: Add migration columns to `predictions`

**Objective:** Add `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count`, `return_7d`, `return_30d`, `outcome_7d`, `outcome_30d` to predictions.

**Files:**
- Modify: `backend/backtest.py` (migration block around line 126)
- Modify: `tests/test_app.py`

**Approach:** Same ALTER TABLE pattern. Update `record_prediction()` signature.

---

### Task 2.3: Add horizon-aware resolution

**Objective:** Resolve predictions at 1d/7d/30d horizons instead of only 1h/4h/24h.

**Files:**
- Modify: `backend/backtest.py` (in `resolve_pending_outcomes` and `record_outcome`)

**Approach:** When a prediction has `time_horizon`, resolve at the appropriate window. Use Yahoo Finance historical OHLC at T+1d, T+7d, T+30d.

---

### Task 2.4: Update `/api/signals/history` and `/api/backtest` filters

**Objective:** Add `time_horizon`, `signal_tier`, `signal_type` query params.

**Files:**
- Modify: `backend/main.py` (endpoints at ~line 2100 and ~line 2530)
- Modify: `tests/test_app.py`

---

## Phase 3 — Backtest Calibration

### Task 3.1: Create `source_accuracy` table + computation

### Task 3.2: Add category calibration multipliers

### Task 3.3: Add `by_time_horizon`, `by_signal_tier`, `by_signal_type` to `/api/backtest`

### Task 3.4: Add `/api/calibration/report` endpoint

---

## Phase 4 — Daily Summary and Telegram UX

### Task 4.1: Add `/api/signals/daily-summary` endpoint

### Task 4.2: Add bot `/daily` command

### Task 4.3: Refocus `/signals` to group by 1d/7d/30d

### Task 4.4: Add `/why TICKER` command

### Task 4.5: Add morning cron job (08:30 WIB)

---

## Phase 5 — Dashboard Refocus

### Task 5.1: Add Actionable Signals section to Overview tab

### Task 5.2: Add horizon/tier chips to Watchlist table

### Task 5.3: Add calibration warning banner

### Task 5.4: Reorder tabs (Signals first, Events second)

---

## Execution Order

```
Phase 0 (1 task)    → safety tests, already partially done
Phase 1 (6 tasks)   → core module + dashboard wiring  ← START HERE
Phase 2 (4 tasks)   → DB migrations + horizon resolution
Phase 3 (4 tasks)   → calibration
Phase 4 (5 tasks)   → bot UX + daily summary
Phase 5 (4 tasks)   → dashboard refocus
```

Phase 1 is the critical path — it creates the missing product core without requiring DB migrations or breaking existing alerts. Phases 2-5 build on top of it.

Each phase should be verified against the smoke test before proceeding:

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/ -q
/opt/hermes/polstock_bot/.venv/bin/python -m py_compile backend/*.py
sudo systemctl restart politics-stock-mapper.service
curl -s http://localhost:8001/healthz
curl -s http://localhost:8001/api/dashboard | python3 -m json.tool >/dev/null
```
