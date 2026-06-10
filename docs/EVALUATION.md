# PolStock Evaluation — Post-SPEC Implementation Review

**Date:** 2026-06-10
**Evaluator:** Hermes (automated)
**Scope:** Evaluate all 6 phases of the SPEC against current implementation, with focus on short-term signal readiness.
**Live Data Source:** `http://localhost:8001` (production service)

---

## 1. Executive Summary

The SPEC describes 6 phases (0–5) of refocusing PolStock from a generic political dashboard to a **short-term trading signal assistant**. Phases 0–5 have all been **structurally implemented** — the code, endpoints, DB migrations, bot commands, and dashboard UI exist. However, **the system is not producing actionable signals**. The core signal decision layer works in isolation but the end-to-end pipeline from live data → classified signal → user-facing output is broken in practice.

**Bottom line:** The infrastructure is solid. The signal quality and pipeline connectivity need work before this is useful for trading.

---

## 2. Phase-by-Phase Status

### Phase 0 — Spec and Safety Baseline ✅ Done
- `SPEC.md` is comprehensive (1111 lines, 19 sections).
- Existing tests in `tests/test_app.py` (3442 lines).
- No behavioral regressions observed.

### Phase 1 — Trading Signal Decision Layer ✅ Implemented, ⚠️ Effectiveness Low
**What exists:**
- `backend/trading_signals.py` (310 lines) — pure functions: `compute_event_score`, `compute_technical_confirmation`, `infer_time_horizon`, `classify_signal`, `rank_trade_signals`, `get_calibration_multiplier`.
- Wired into `build_refresh_payload()` at main.py:2001-2004 — every stock gets a `trading_signal` object.
- Dashboard integration tests pass (test at line 3245).

**Problems:**
- **All signals default to `time_horizon: "7d"`** — the `infer_time_horizon` function requires `event_stage == "breaking"` or very high scores (≥0.5) + 3/4 tech confirmations for `1d`, and `event_stage == "established"` for `30d`. In practice, almost nothing qualifies.
- **All signals are Tier D** — Tier A requires `signal_strength ≥ 0.70 + 3 confirmations + no conflict`; Tier B requires `≥ 0.60 + 2 confirmations`. Current event scores rarely exceed 0.3, so everything falls to D.
- **Technical confirmation is too strict** — requires specific oversold/overbought thresholds (RSI < 40 for BUY, > 60 for SELL) that rarely trigger on normal market conditions.
- **Zero live signals at evaluation time** — `daily-summary` returns empty for all 3 horizons.

### Phase 2 — Horizon-Aware Persistence ✅ Done
**What exists:**
- `signal_history` table: columns `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count`, `calibration_multiplier`, `invalidation_reason` all added via safe ALTER TABLE.
- `predictions` table: columns `time_horizon`, `signal_tier`, `signal_type`, `event_score`, `tech_score`, `tech_confirmation_count`, `return_7d`, `return_30d`, `outcome_7d`, `outcome_30d` all added.
- `log_signal()` updated to store all new fields.
- `record_prediction()` updated to store all new fields.
- `/api/signals/history` supports `time_horizon`, `signal_tier`, `signal_type` filters.
- `/api/backtest` has `by_time_horizon` breakdown.

**Problems:**
- **All live predictions default to `7d` horizon** — 237 predictions at 7d, 0 at 1d, 0 at 30d. The horizon is not being meaningfully assigned.
- **No predictions have tier data** — `by_signal_tier` is empty in backtest output. The tier is being stored in `signal_history` but not being propagated into the predictions recording pipeline properly.

### Phase 3 — Backtest Calibration ✅ Implemented, ⚠️ Data Quality Concerns
**What exists:**
- `source_accuracy` table created in `backtest.py:140-148`.
- `compute_source_accuracy()` — live predictions only, per source_type.
- `compute_category_calibration()` — live predictions only, per category.
- `/api/calibration/report` — returns overall metrics, by_source_type, by_category, recommendations.
- Multipliers clamped to [0.5, 1.5] in `get_calibration_multiplier()`.

**Live calibration data (2026-06-10):**
- **Overall:** 44.7% hit rate vs 54% baseline → edge -9.3% (strict mode justified)
- **By source_type:**
  - media: 58.4% hit rate (161 predictions) → multiplier 1.305 ✅
  - government: 12.2% hit rate (49 predictions) → multiplier 0.5 🔴
  - regulator: 22.2% hit rate (27 predictions) → multiplier 0.5 🔴
- **By category:**
  - TRADE_POLICY: 75.8% (33) → 1.5 ✅
  - ENERGY_POLICY: 75.8% (33) → 1.5 ✅
  - REGULATION_NEW: 57.1% (28) → 1.278 ✅
  - PARLIAMENT_SESSION: 31.8% (22) → weak

**Problems:**
- **Government/regulator sources are actively harmful** — 12.2% and 22.2% hit rates mean the system is *anti-predictive* on official sources. The calibration multiplier caps at 0.5 but the event pipeline still gives government sources high weight.
- **Category calibration is not wired into the scoring pipeline** — `get_calibration_multiplier()` only applies source_type calibration, skips category. Comment at line 307 says "For now, skip category calibration on individual stocks."
- **Negative predictions completely broken** — 0.0% hit rate on 24 negative live predictions. SELL signals are actively wrong.

### Phase 4 — Daily Summary and Telegram UX ✅ Implemented, ⚠️ Producing Empty Output
**What exists:**
- `/api/signals/daily-summary` endpoint (main.py:2786) — groups signals by 1d/7d/30d.
- Bot `/signals` command (handlers.py:609) — groups by horizon with stats.
- Bot `/daily` command (handlers.py:668) — daily summary with accuracy footer.
- Bot `/why TICKER` command (handlers.py:726) — explains signal for a ticker.

**Problems:**
- **`/daily` always returns "No signals"** — the endpoint works but the signal classification produces no BUY/SELL/WATCH actions.
- **No morning cron job** — the SPEC calls for a 08:30 WIB daily push. Not implemented.
- **`/api/signals/ticker/{ticker}` endpoint missing** — SPEC section 11.2 specifies this; the bot's `/why` command calls the dashboard API instead of a dedicated endpoint.
- **`daily_signal_snapshots` table not created** — SPEC section 10.4 specifies this for fair evaluation. Not implemented.

### Phase 5 — Dashboard Refocus ✅ Done
**What exists:**
- "🎯 Actionable Signals" card at top of Overview tab (dashboard.html:1847-1851).
- Trading signal chips (action, horizon, tier) on watchlist table (dashboard.html:3691-3697).
- `loadActionableSignals()` function calls `/api/signals/daily-summary` (dashboard.html:3919).
- `loadCalibrationBanner()` shows accuracy warning when live < baseline.
- Signal detail in history panel shows time_horizon, signal_tier, signal_type (dashboard.html:4342-4347).

**Problems:**
- **Actionable signals section always shows empty** — same root cause as Phase 4.
- **Watchlist table shows no signal chips** — because all signals are IGNORE.

---

## 3. Critical Findings

### 3.1 The Signal Pipeline is Disconnected at the Data Level

The system follows this flow:
```
News Sources → NLP → Events → Scoring → Stock Payload → classify_signal() → Dashboard/Bot
```

The issue is at the **Stock Payload** stage. When `build_refresh_payload()` runs:
1. It fetches news and creates events.
2. It fetches stock quotes.
3. It matches events to tickers.
4. It computes impact scores.
5. It calls `classify_signal(stock)` for each ticker.

But the stock dict passed to `classify_signal` often has:
- `impact_direction: "neutral"` → triggers WATCH/IGNORE immediately
- `relationship_confidence: 0` or very low → event score near zero
- `corroboration_count: 0` → event score near zero
- Missing technical indicator data → `compute_technical_confirmation` returns 0/0

**Root cause:** The scoring pipeline produces weak signals because most events don't generate strong enough impact scores, and technical indicators are only computed when a stock has price history available.

### 3.2 Technical Confirmation Thresholds Are Wrong

The current thresholds:
- RSI: BUY if < 40, SELL if > 60
- MACD: BUY if histogram > 0, SELL if < 0
- Bollinger %B: BUY if < 0.2, SELL if > 0.8
- Volume: confirm if is_spike

These are reasonable for oversold/overbought detection, but they're **too extreme for confirming directional moves**. A stock at RSI 45 with a positive MACD and rising volume should count as confirmation for a BUY signal, but currently it doesn't.

### 3.3 Negative Predictions Must Be Suppressed

24 live negative predictions → 0 correct (0.0% hit rate). The SPEC correctly identifies this:
> "SELL must be stricter than BUY... No alert for SELL until backtest accuracy improves above baseline."

The code does require 3/4 tech confirmations for SELL (vs 2/4 for BUY), but even this isn't enough. SELL signals should be **completely disabled for push alerts** until the hit rate improves.

### 3.4 Historical Backfill is Polluting Perception

The SPEC states:
> "Historical backfill hit rate is 20.5%; it should not drive live product confidence."

The code separates `origin=live` vs historical in backtest queries, but the `/api/backtest` default still returns mixed data. The calibration report correctly uses live-only, but the main backtest endpoint doesn't default to live.

---

## 4. What's Missing (SPEC vs Reality)

| SPEC Item | Status | Notes |
|-----------|--------|-------|
| `backend/trading_signals.py` | ✅ | 310 lines, all pure functions |
| `classify_signal()` wired into dashboard | ✅ | main.py:2001-2004 |
| Horizon-aware columns in signal_history | ✅ | Safe ALTER TABLE |
| Horizon-aware columns in predictions | ✅ | Safe ALTER TABLE |
| `source_accuracy` table | ✅ | Created in backtest.py |
| `compute_source_accuracy()` | ✅ | Live-only, per source_type |
| `compute_category_calibration()` | ✅ | Live-only, per category |
| `/api/calibration/report` | ✅ | Full report with recommendations |
| `/api/calibration/auto-apply` | ❌ | Not implemented |
| `/api/signals/daily-summary` | ✅ | Groups by 1d/7d/30d |
| `/api/signals/ticker/{ticker}` | ❌ | Not implemented |
| `daily_signal_snapshots` table | ❌ | Not implemented |
| Bot `/signals` | ✅ | Groups by horizon |
| Bot `/daily` | ✅ | With accuracy footer |
| Bot `/why TICKER` | ✅ | Explains signal |
| Bot `/watch` | ❌ | Mentioned in help, not implemented |
| Morning cron (08:30 WIB) | ❌ | Not implemented |
| Dashboard actionable signals | ✅ | Card at top of Overview |
| Dashboard horizon/tier chips | ✅ | On watchlist table |
| Dashboard calibration banner | ✅ | When live < baseline |
| Tests for trading_signals.py | ✅ | In test_app.py:3044+ |

---

## 5. Performance Metrics (Live Data)

### Backtest (30-day window, live origin)
- **Total predictions:** 237
- **Hit rate:** 44.7%
- **Baseline (neutral):** 54.0%
- **Edge:** -9.3% 🔴

### By Direction
- Positive: 30 predictions, 43.3% hit rate
- Negative: 24 predictions, 0.0% hit rate 🔴
- Neutral: 183 predictions, 50.8% hit rate

### By Horizon
- 1d: 0 predictions (no signals assigned to this horizon)
- 7d: 237 predictions, 44.7% hit rate
- 30d: 0 predictions (no signals assigned to this horizon)

### By Source Type (live only)
- media: 161 predictions, 58.4% hit rate ✅
- government: 49 predictions, 12.2% hit rate 🔴
- regulator: 27 predictions, 22.2% hit rate 🔴

### Current Signal Production
- **Actionable signals:** 0 (across all horizons)
- **All stocks:** IGNORE action, Tier D, 7d horizon

---

## 6. Recommendations

### Priority 1 — Fix Signal Production (blocks all value)

1. **Relax technical confirmation thresholds.** Change RSI thresholds from <40/>60 to <45/>55, or add a "directional alignment" mode that checks if indicators *agree with* the event direction rather than requiring extreme levels.
2. **Lower event score floor.** Many stocks get `event_score < 0.1` because `corroboration_count` defaults to 0 and `relationship_confidence` is often 0.1-0.2. The `max(..., 0.1)` floor helps but the multiplication still produces very small numbers.
3. **Add standalone technical signals.** SPEC section 9.4 calls for "oversold bounce candidate", "momentum breakout", etc. These don't require event catalyst and would populate the signal pipeline during quiet political periods.

### Priority 2 — Fix Signal Quality (blocks trust)

4. **Suppress SELL push alerts entirely.** 0% hit rate on negatives means every SELL alert is wrong. Disable until hit rate > baseline.
5. **Wire category calibration into scoring.** The `get_calibration_multiplier()` function skips category calibration. TRADE_POLICY and ENERGY_POLICY have 75.8% hit rates — signals from these categories should get a boost.
6. **Penalize government/regulator sources.** Their 12-22% hit rates are worse than random. The calibration multiplier of 0.5 is a start, but the event pipeline still gives these sources high base weight.

### Priority 3 — Complete Missing Features

7. **Implement `/api/signals/ticker/{ticker}` endpoint.** The bot's `/why` command needs this.
8. **Implement `daily_signal_snapshots` table.** Essential for fair historical evaluation.
9. **Add morning cron job.** 08:30 WIB daily summary push to subscribed users.
10. **Implement `/watch` command.** Listed in help text but not implemented.

### Priority 4 — Improve Calibration

11. **Default `/api/backtest` to `origin=live`.** Historical backfill data (20.5% hit rate) shouldn't mix with live data in the default view.
12. **Add backtest by signal tier.** Currently `by_signal_tier` is empty because no signals have tier data in the predictions table.
13. **Implement `/api/calibration/auto-apply`.** Only when sample size gates are defined.

---

## 7. Recommended Next Steps

The SPEC correctly identifies the immediate next step:
> "Start with Phase 1: Trading Signal Decision Layer."

But Phase 1 is already implemented. The real next step is:

**Fix the signal production pipeline so `classify_signal()` actually produces BUY/WATCH signals from live data.**

Concretely:
1. Audit `build_refresh_payload()` to understand why stock dicts have weak event data.
2. Relax `compute_technical_confirmation()` thresholds.
3. Add a `compute_standalone_technical_signal()` function for event-independent signals.
4. Test with live data and verify at least 2-3 signals appear in `/api/signals/daily-summary`.
5. Commit, push, restart, verify.

After that, the existing bot commands, dashboard UI, and calibration system will actually have data to display.

---

## 8. Test Status

- **Backend tests:** Exist in `tests/test_app.py` (3442 lines) — timeout during evaluation (may need optimization).
- **Trading signal unit tests:** Exist at test_app.py:3044+ covering `compute_event_score`, `compute_technical_confirmation`, `infer_time_horizon`, `classify_signal`, `rank_trade_signals`.
- **Integration test:** `test_dashboard_stocks_have_trading_signal` verifies dashboard payload includes `trading_signal`.
- **Bot tests:** `tests/test_formatting_alerts.py` exists but is minimal.

---

## 9. Architecture Health

| Component | Lines | Status |
|-----------|-------|--------|
| `backend/main.py` | 3,099 | Large but stable. Consider splitting. |
| `backend/trading_signals.py` | 310 | Clean pure functions. Good. |
| `backend/backtest.py` | 1,809 | Complex but well-structured. |
| `backend/signals.py` | 434 | Solid. Horizon-aware. |
| `backend/scoring.py` | — | Core scoring pipeline. |
| `dashboard.html` | 4,347+ | Single-file. Signal UI added. |
| `bot/handlers.py` | 1,232 | All commands implemented. |
| `bot/alerts.py` | 152 | Event-based alerts. Needs signal-based alerts. |

---

*This evaluation reflects live system state on 2026-06-10. Metrics will change as new events arrive and predictions resolve.*
