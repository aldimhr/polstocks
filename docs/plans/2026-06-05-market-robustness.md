# Plan: Market Robustness Layer

**Date:** 2026-06-05
**Status:** Planning
**Goal:** Make PolStock distinguish "interesting text" from "actual market impact"

## What Already Exists

| Component | Status |
|-----------|--------|
| `validate_market_reaction` | ✅ z-score + volume ratio → confirmed/rejected/predicted_only |
| `validation_outcome_multiplier` | ✅ 0.5x for rejected, 1.0x for confirmed |
| `calibrate_source_confidence_from_validation` | ✅ adjusts confidence by validation + history |
| `source_outcome_weight` / `record_source_outcome` | ✅ per-source hit rate tracking |
| `historical_reliability_metrics` | ✅ reliability multiplier from cumulative history |
| Dashboard validation chip | ✅ shows status, score, window |
| Stock payload metrics | ✅ abnormal_return, abnormal_volume_ratio |
| Config-driven windows/thresholds | ✅ `market_validation_config.json` |

## Gaps Identified (Ranked by Impact)

### 🔴 HIGH IMPACT

#### Task 1: Multi-window cross-validation
**Problem:** Validation only checks ONE window (the article's assigned window). A prediction can look "confirmed" at 1d but "rejected" at 1w, and we'd never know.

**Fix:** Validate against BOTH 1d and 1w windows when data allows. If they disagree → flag as "divergent" in validation_reason.

**Where:** `validate_market_reaction` — run a second check with the alternate window, store `cross_window_status` and `cross_window_divergent: bool`.

**Tests:**
- `test_cross_window_confirmed_both` — 1d and 1w both confirm → no divergence
- `test_cross_window_divergent` — 1d confirms, 1w rejects → divergent flag
- `test_cross_window_skipped_when_insufficient` — only 1 window available → no flag

#### Task 2: Time-decayed source outcome history
**Problem:** `record_source_outcome` accumulates indefinitely. A source that was accurate 6 months ago but wrong recently still has a high hit rate.

**Fix:** Apply exponential decay (30-day half-life) to `weighted_outcome_sum` and `sample_size` when loading history. Old entries naturally fade.

**Where:** `historical_reliability_metrics` — apply decay based on last record timestamp.

**Tests:**
- `test_outcome_history_no_decay_recent` — records < 30 days old → no decay
- `test_outcome_history_decays_old` — records > 90 days old → significant decay
- `test_outcome_history_mixed_ages` — mix of old/new → weighted correctly

#### Task 3: Validation confidence penalty on relationship_confidence
**Problem:** `validation_outcome_multiplier` adjusts `source_confidence` but doesn't directly penalize the relationship's `confidence` score. A rejected prediction still carries full relationship confidence.

**Fix:** Apply `validation_multiplier` to `relationship_confidence` in the relationship dict.

**Where:** After `validate_market_reaction` returns, before `compute_ticker_score`.

**Tests:**
- `test_rejected_low_relationship_confidence` — rejected → confidence lowered
- `test_confirmed_preserves_relationship_confidence` — confirmed → unchanged or boosted

### 🟡 MEDIUM IMPACT

#### Task 4: Per-channel validation tracking
**Problem:** We track hit rates per *source* but not per *policy channel*. Some channels (FISCAL_POLICY → banks) may be consistently accurate while others (MARKET_STRUCTURE → tech) are noisy.

**Fix:** Extend `source_outcome_history.json` to track per-channel outcomes. In `record_source_outcome`, also key by `matched_policy_channels`.

**Where:** New `channel_outcome_history` section in the history file.

**Tests:**
- `test_channel_outcome_tracked` — records channel alongside source
- `test_channel_reliability_metrics` — queries hit rate by channel

#### Task 5: Dashboard validation detail panel
**Problem:** Dashboard shows a "Validation" chip (confirmed/rejected) but doesn't surface the *why* — abnormal return %, volume ratio, or cross-window divergence.

**Fix:** Expand the stock row to show validation details on hover/click: abnormal return %, volume ratio, validation reason.

**Where:** `dashboard.html` — expand reasoning chips section.

**No backend tests needed** (frontend-only).

#### Task 6: Validation-aware confidence in reasoning_summary
**Problem:** `reasoning_summary` shows average confidence but doesn't indicate how much validation impacted it.

**Fix:** Add `validation_adjusted_confidence_delta` to the summary, showing how much validation boosted/cut the raw confidence.

**Where:** `build_stock_relationships` / stock payload assembly.

**Tests:**
- `test_reasoning_summary_includes_validation_delta`

### 🟢 LOW IMPACT

#### Task 7: Warn on all-predicted_only coverage
**Problem:** If every prediction is `predicted_only`, the user might trust the signal too much. Should surface a warning.

**Fix:** In `reasoning_summary`, if all validation statuses are `predicted_only`, add a warning chip.

**Tests:**
- `test_all_predicted_only_warning`

## Implementation Order

| Order | Task | Effort |
|-------|------|--------|
| 1 | Task 1: Multi-window cross-validation | Medium |
| 2 | Task 2: Time-decayed source outcomes | Small |
| 3 | Task 3: Validation confidence penalty | Small |
| 4 | Task 4: Per-channel tracking | Medium |
| 5 | Task 5: Dashboard detail panel | Small |
| 6 | Task 6: Validation delta in summary | Small |
| 7 | Task 7: All-predicted_only warning | Trivial |
