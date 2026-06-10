# Phase 3 — Backtest Calibration

**Goal:** Make signal confidence reflect historical performance. Source accuracy tracking, category calibration multipliers, and a calibration report endpoint. No auto-apply until sample size gates exist.

**Depends on:** Phase 1 (trading_signals.py) and Phase 2 (horizon/tier/type columns in predictions).

**Note:** `by_time_horizon`, `by_signal_tier`, `by_signal_type` breakdowns were already added in Phase 2.

---

## Task 3.1: Create `source_accuracy` table + computation

**Objective:** Track per-source prediction accuracy from live predictions only.

**Files:**
- Modify: `backend/backtest.py` — add `source_accuracy` table creation in `init_backtest_db()` + add `compute_source_accuracy()` function
- Modify: `tests/test_app.py` — add test

**Table:**
```sql
CREATE TABLE IF NOT EXISTS source_accuracy (
    source_id TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    hit_rate REAL DEFAULT 0.5,
    calibration_multiplier REAL DEFAULT 1.0,
    last_updated TEXT
);
```

**Function: `compute_source_accuracy(window_days=30, min_samples=5)`**
- Query live predictions grouped by `source_type`
- Compute hit_rate per source
- Compute calibration_multiplier: `hit_rate / overall_hit_rate` clamped to [0.5, 1.5]
- Only compute when `total >= min_samples`
- Return dict of source_id → {total, correct, hit_rate, calibration_multiplier}

---

## Task 3.2: Add category calibration multipliers

**Objective:** Compute per-category calibration from live backtest data.

**Files:**
- Modify: `backend/backtest.py` — add `compute_category_calibration()` function
- Modify: `tests/test_app.py` — add test

**Function: `compute_category_calibration(window_days=30, min_samples=5)`**
- Query live predictions grouped by `categories` (JSON array)
- Compute hit_rate per category
- Compute calibration_multiplier: `category_hit_rate / overall_hit_rate` clamped to [0.5, 1.5]
- Only include when `total >= min_samples`
- Return dict of category → {total, correct, hit_rate, calibration_multiplier}

---

## Task 3.3: Add `/api/calibration/report` endpoint

**Objective:** New endpoint returning calibration health.

**Files:**
- Modify: `backend/main.py` — add `GET /api/calibration/report` endpoint
- Modify: `tests/test_app.py` — add test

**Endpoint: `GET /api/calibration/report?window_days=30&origin=live`**

Returns:
```json
{
  "overall": {"hit_rate": 0.451, "baseline": 0.545, "edge": -0.094, "total": 235},
  "by_source_type": {...},
  "by_category": {...},
  "by_signal_type": {...},
  "by_time_horizon": {...},
  "recommendations": ["Strict mode: negative predictions suppressed", ...]
}
```

---

## Task 3.4: Wire calibration into classify_signal

**Objective:** Replace the `calibration = 1.0` placeholder in `classify_signal()` with actual calibration lookup.

**Files:**
- Modify: `backend/trading_signals.py` — add `get_calibration_multiplier()` function
- Modify: `tests/test_app.py` — add test

**Function: `get_calibration_multiplier(stock, overall_hit_rate=None)`**
- Look up category calibration from `compute_category_calibration()`
- Look up source type calibration from `compute_source_accuracy()`
- Combine: `multiplier = category_mult * source_mult`
- If sample too small (< 5), return 1.0 (neutral)
- Clamp to [0.5, 1.5]

**Wire into `classify_signal()`:**
- Replace `calibration = 1.0` with `calibration = get_calibration_multiplier(stock)`
