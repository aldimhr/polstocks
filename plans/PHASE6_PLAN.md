# Phase 6 — Actionable BUY Signals & Feedback Loop

**Date:** 2026-06-10
**Goal:** Generate Tier A/B BUY signals with entry/SL/TP, and close the feedback loop with automated outcome tracking.

---

## Current State

| Metric | Value | Status |
|--------|-------|--------|
| WATCH signals/day | 8 | ✅ flowing |
| BUY signals/day | 0 | 🔴 blocked |
| Predictions | 416 | 87% neutral direction |
| Accuracy | 34.2% | 🔴 worse than random |
| Tier distribution | 100% D | 🔴 no A/B signals |
| Outcome tracking | 382/416 resolved | ⚠️ no return data |

## Problem Analysis

1. **No BUY signals**: Event scores too low + tech confirmations too weak → everything WATCH/IGNORE
2. **Neutral predictions dominate**: `record_predictions_from_events` records all event→ticker pairs, even with no directional signal
3. **No return tracking**: `return_7d` and `return_30d` are NULL for all resolved predictions
4. **Support/resistance unused**: Data exists but doesn't factor into signal scoring

## Tasks

### Task 1: Support/Resistance Proximity Boost
**Why**: Stocks near support with positive event → bounce play. Stocks near resistance → breakout or reversal.
- Read `support` and `resistance` from stock payload
- Add `compute_sr_proximity_boost(stock)` to trading_signals.py
- Boost when price within 3% of support (for BUY) or resistance (for SELL)
- Wire into `detect_technical_setup()` and `classify_signal()`

### Task 2: Bollinger Squeeze Detection
**Why**: Squeeze = low volatility → imminent breakout. High-conviction 1d signal.
- Detect when Bollinger bandwidth is at 20-day low
- Add `detect_bollinger_squeeze(stock)` to trading_signals.py
- When squeeze + positive event → 1d BUY candidate
- When squeeze + no event → WATCH (breakout pending)

### Task 3: Fix Prediction Recording — Directional Only
**Why**: 87% neutral predictions pollute backtest with noise.
- In `record_predictions_from_events`, skip relationships where `impact_direction == "neutral"`
- Only record predictions with clear positive/negative direction
- This will dramatically improve backtest signal-to-noise

### Task 4: Return Tracking via Resolution
**Why**: Can't evaluate signal quality without actual returns.
- Update `resolve_predictions()` to compute `return_7d` and `return_30d`
- Use Yahoo Finance to fetch price at +7d and +30d from event date
- Store actual returns in predictions table
- Update `is_correct` based on direction vs actual return

### Task 5: BUY Signal Test — Force Refresh & Verify
**Why**: Verify end-to-end BUY signal flow works.
- Force refresh after all changes
- Check if any BUY signals appear in daily-summary
- Verify entry/SL/TP are calculated
- Verify signal logging to signal_history

## Acceptance Criteria

- At least 1 BUY signal appears in `/api/signals/daily-summary` within 24h of implementation
- `by_signal_tier` in backtest shows non-empty A/B categories
- Predictions table has directional-only entries (no neutral)
- `return_7d` populated for predictions older than 7 days
- All existing tests pass
