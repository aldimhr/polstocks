# Backtest Framework for PolStock

## Problem
The scoring system has 30+ hardcoded weights (0.24, 0.26, 0.14, 0.45, 0.82, etc.) with zero feedback on whether predictions are accurate. We need a validation loop: predicted impact → actual stock movement → accuracy metrics → weight tuning.

## Architecture

### New File: `backend/backtest.py`
Core backtest engine — clean separation from existing code.

### New SQLite Table: `predictions`
Store each event→ticker prediction individually (not as JSON blob):

```sql
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    event_headline TEXT,
    published_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    predicted_direction TEXT NOT NULL,  -- 'positive'/'negative'/'neutral'
    predicted_score REAL NOT NULL,      -- -1.0 to +1.0 (from compute_ticker_score)
    significance REAL,
    confidence REAL,
    relationship_type TEXT,             -- 'direct'/'indirect'
    categories TEXT,                    -- JSON array
    source_type TEXT,
    event_stage TEXT,
    -- Actual outcomes (filled later)
    price_at_event REAL,
    price_after_1h REAL,
    price_after_4h REAL,
    price_after_24h REAL,
    actual_return_1h REAL,              -- % change
    actual_return_4h REAL,
    actual_return_24h REAL,
    actual_direction TEXT,              -- derived from actual_return_24h
    is_correct INTEGER,                 -- 1 if predicted == actual direction
    outcome_status TEXT DEFAULT 'pending',  -- 'pending'/'resolved'/'expired'
    resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(event_id, ticker)
);
```

### New SQLite Table: `weight_performance`
Track accuracy per scoring dimension over rolling windows:

```sql
CREATE TABLE IF NOT EXISTS weight_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dimension TEXT NOT NULL,       -- 'sentiment_score'/'significance'/'confidence'/'source_type'/'category'
    bucket TEXT NOT NULL,          -- e.g. 'high_positive'/'low_negative'/'direct_impact'
    sample_count INTEGER,
    hit_count INTEGER,
    hit_rate REAL,
    avg_predicted REAL,
    avg_actual REAL,
    bias REAL,                     -- avg_predicted - avg_actual
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(dimension, bucket)
);
```

## Implementation Plan

### Phase 1: Event Persistence & Price Capture
1. **`backend/backtest.py`** — new module
   - `init_backtest_db(db_path)` — create tables if not exist
   - `record_prediction(db, event, ticker, relationship)` — insert into predictions table
   - `record_outcome(db, prediction_id, price_data)` — fill in actual prices & compute returns
   - `get_pending_predictions(db)` — predictions older than 24h that need outcome resolution

2. **Hook into `build_refresh_payload`** — after building events, call `record_prediction()` for each event→ticker pair

3. **Cron job / background thread** — every hour, resolve pending predictions:
   - Fetch current price via `fetch_live_quote`
   - Compare to `price_at_event`
   - Compute returns at 1h/4h/24h
   - Mark as `resolved` when all windows filled or 24h+ elapsed

### Phase 2: Accuracy Metrics
4. **`compute_accuracy_metrics(db, window_days=30)`** — aggregate metrics:
   - Overall hit rate (predicted direction == actual direction)
   - Hit rate by direction (positive/negative/neutral predictions)
   - Hit rate by source_type, category, relationship_type
   - Average predicted vs actual (bias detection)
   - Precision@recall for top-significance predictions

5. **Dimension-level analysis**:
   - `sentiment_score` buckets: strongly positive (>0.5), mildly positive (0.1-0.5), neutral, mildly negative, strongly negative
   - `significance` buckets: high (>0.1), medium (0.05-0.1), low (0.015-0.05)
   - `confidence` buckets: high (>0.7), medium (0.4-0.7), low (<0.4)
   - Hit rate per bucket → reveals which predictions to trust

### Phase 3: Dashboard Integration
6. **New API endpoint**: `GET /api/backtest` — returns accuracy metrics
7. **Dashboard panel**: "Prediction Accuracy" card showing:
   - Overall hit rate (gauge/meter)
   - Hit rate by direction (bar chart)
   - Predicted vs Actual scatter (bias visualization)
   - Best/worst performing categories
   - Sample count & statistical significance

### Phase 4: Weight Recommendations
8. **`suggest_weight_adjustments(db)`** — based on backtest data:
   - If strongly positive predictions are only 40% accurate → reduce `directional_sentiment` floor (0.45)
   - If indirect relationships have low hit rate → reduce `relationship_multiplier` (0.82)
   - If high-significance predictions are accurate → keep significance formula
   - Output: list of `{weight, current_value, suggested_value, reason, sample_size}`

## Files to Create/Modify

| File | Action | Description |
|------|--------|-------------|
| `backend/backtest.py` | **CREATE** | Core engine: DB init, record, resolve, metrics |
| `backend/main.py` | MODIFY | Hook `record_prediction` into `build_refresh_payload`, add `/api/backtest` endpoint |
| `backend/stocks.py` | NO CHANGE | `fetch_live_quote` already exists |
| `dashboard.html` | MODIFY | Add backtest panel with hit rate visualization |
| `tests/test_backtest.py` | **CREATE** | Unit tests for backtest engine |

## Key Design Decisions

1. **Individual rows, not JSON blobs** — Events are currently stored as one big JSON blob. Predictions need individual rows for SQL aggregation.
2. **Async resolution** — Price capture happens later (1h/4h/24h after event), not at event time. Use background thread or cron.
3. **Direction-based hit rate** — Simplest metric: did the predicted direction match actual? (positive = stock went up >0.5%, negative = down >0.5%, neutral = within ±0.5%)
4. **Conservative thresholds** — Only count as "correct" if actual movement exceeds noise (>0.5% in predicted direction)
5. **No auto-adjustment yet** — Phase 4 suggests adjustments but doesn't auto-apply. User reviews and approves changes.

## Success Criteria
- All 79 existing tests still pass
- New predictions are recorded for every event→ticker pair
- After 24h, outcomes are resolved with actual price data
- Dashboard shows hit rate and bias metrics
- At least 50 predictions collected before weight suggestions are meaningful
