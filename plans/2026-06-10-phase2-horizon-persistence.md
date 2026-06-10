# Phase 2 — Horizon-Aware Persistence

**Goal:** Store horizon/tier/type in signal_history and predictions tables, wire the new fields through log_signal and record_prediction, and add API filters.

**Depends on:** Phase 1 complete (trading_signals.py with classify_signal)

---

## Task 2.1: Add migration columns to `signal_history` + update `log_signal`

**Objective:** Extend signal_history table with new columns, update log_signal to accept them, and update the caller in build_refresh_payload.

**Files:**
- Modify: `backend/signals.py` — `init_signal_tables()` (add ALTER TABLE migration) + `log_signal()` (new params + INSERT) + `get_signal_history()` (include new cols)
- Modify: `backend/main.py` — signal logging block (~line 2008-2040) to pass trading_signal fields
- Modify: `tests/test_app.py` — test log_signal with new fields

**New columns:**
```sql
ALTER TABLE signal_history ADD COLUMN time_horizon TEXT DEFAULT '7d';
ALTER TABLE signal_history ADD COLUMN signal_tier TEXT DEFAULT 'D';
ALTER TABLE signal_history ADD COLUMN signal_type TEXT DEFAULT 'event';
ALTER TABLE signal_history ADD COLUMN event_score REAL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN tech_score REAL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN tech_confirmation_count INTEGER DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN calibration_multiplier REAL DEFAULT 1.0;
ALTER TABLE signal_history ADD COLUMN invalidation_reason TEXT;
```

**log_signal changes:**
- Add optional params: `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count`, `calibration_multiplier`, `invalidation_reason`
- Update INSERT statement to include new columns
- Update return dict to include new fields

**main.py signal logging changes:**
- Read from `stock["trading_signal"]` instead of `stock["trade_signal"]` for the new fields
- Pass `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count` to `log_signal()`

---

## Task 2.2: Add migration columns to `predictions` + update `record_prediction`

**Objective:** Extend predictions table with new columns.

**Files:**
- Modify: `backend/backtest.py` — `init_backtest_db()` (add ALTER TABLE) + `record_prediction()` (new params)
- Modify: `tests/test_app.py` — test record_prediction with new fields

**New columns:**
```sql
ALTER TABLE predictions ADD COLUMN time_horizon TEXT DEFAULT '7d';
ALTER TABLE predictions ADD COLUMN signal_tier TEXT DEFAULT 'D';
ALTER TABLE predictions ADD COLUMN signal_type TEXT DEFAULT 'event';
ALTER TABLE predictions ADD COLUMN event_score REAL DEFAULT 0;
ALTER TABLE predictions ADD COLUMN tech_score REAL DEFAULT 0;
ALTER TABLE predictions ADD COLUMN tech_confirmation_count INTEGER DEFAULT 0;
ALTER TABLE predictions ADD COLUMN return_7d REAL;
ALTER TABLE predictions ADD COLUMN return_30d REAL;
ALTER TABLE predictions ADD COLUMN outcome_7d TEXT;
ALTER TABLE predictions ADD COLUMN outcome_30d TEXT;
```

---

## Task 2.3: Add horizon-aware resolution to `resolve_signals`

**Objective:** Resolve signals at their time horizon (1d/7d/30d) instead of only 24h/72h.

**Files:**
- Modify: `backend/signals.py` — `resolve_signals()` + add horizon constants
- Modify: `tests/test_app.py` — test horizon resolution

**Changes:**
- Read `time_horizon` from signal record
- For 1d signals: resolve at 24h (existing behavior)
- For 7d signals: resolve at 7d
- For 30d signals: resolve at 30d
- Store resolution price at the horizon window

---

## Task 2.4: Update `/api/signals/history` and `/api/backtest` filters

**Objective:** Add new query params to existing endpoints.

**Files:**
- Modify: `backend/main.py` — `api_signal_history()` and `api_backtest()`
- Modify: `backend/signals.py` — `get_signal_history()` (new filter params)
- Modify: `backend/backtest.py` — `compute_accuracy_metrics()` (new breakdowns)
- Modify: `tests/test_app.py` — test new filters

**New filters for `/api/signals/history`:**
- `time_horizon` (optional)
- `signal_tier` (optional)
- `signal_type` (optional)

**New breakdowns for `/api/backtest`:**
- `by_time_horizon` — accuracy per horizon
- `by_signal_type` — accuracy per type (event/technical/composite)
