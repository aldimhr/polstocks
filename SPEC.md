# PolStock SPEC — Short-Term Trading Signal Refocus

**Status:** Planning / refocus document  
**Repo:** `/opt/hermes/politics_stock_mapper`  
**Primary user goal:** Help find Indonesian stock trading signals that are tradable in **1d / 7d / max 30d**.  
**Last verified locally:** `http://localhost:8001/api/backtest?window_days=30`

---

## 1. Product Definition

PolStock is not a generic politics dashboard anymore.

PolStock should become a **short-term trading signal assistant** for Indonesian equities. It should answer:

> “Is there a tradable political / policy / regulatory signal for this stock, and should I act today, this week, or within 30 days?”

The product should prioritize:

1. **Actionable signals** over long news summaries.
2. **1d / 7d / 30d horizons** over vague bullish/bearish labels.
3. **Precision** over recall — fewer signals is acceptable if they are cleaner.
4. **Backtest-backed confidence** over manually guessed confidence.
5. **Trader UX** — entry, stop-loss, take-profit, reason, confidence, invalidation.

---

## 2. Current Implementation Inventory

### 2.1 Implemented and Working

#### Backend / API

Implemented in `backend/main.py` and related modules:

- FastAPI backend serving dashboard and mini app.
- `/api/dashboard` for main payload.
- `/api/refresh` for force refresh.
- `/api/ticker/{ticker}` for ticker detail.
- `/api/watchlist`, `/api/watchlist/all`, pin/unpin endpoints.
- `/api/signals/history` and `/api/signals/resolve`.
- `/api/portfolio`, `/api/portfolio/live`, `/api/portfolio/history`, `/api/portfolio/reset`, add/close position endpoints.
- `/api/backtest`, backfill, historical import, replay, resolve, suggestions, indicator analysis.
- `/api/weights`, weight override, auto-tune, reset.
- `/api/predictions/history`.
- `/api/nlp_status`.

#### News and Event Analysis

Implemented mainly in:

- `backend/events.py`
- `backend/scoring.py`
- `backend/sources.py`
- `backend/validation.py`

Current capabilities:

- Fetches political/news data from multiple Indonesian sources.
- Deduplicates articles.
- Groups related articles into event threads.
- Extracts themes, categories, institutions, policy channels.
- Links events to stock tickers using company knowledge / aliases / policy exposure.
- Computes relationship confidence, evidence strength, source quality, corroboration.
- Tracks source conflict, freshness, event recency, thread status.

#### NLP

Implemented in `backend/nlp.py`:

- ML NLP gate: `POLSTOCK_ENABLE_ML_NLP`.
- Indonesian RoBERTa sentiment model.
- IndoBERT NER model.
- Keyword-first financial/political sentiment logic.
- Category classification for political event types.
- Regex fallback when ML unavailable.
- Background warmup on startup.

Current live NLP state is expected to be enabled in production.

#### Technical Indicators

Implemented in `backend/stocks.py` and compatibility helpers in `backend/main.py`:

- RSI.
- MACD.
- SMA trend.
- ATR.
- Bollinger Bands.
- Support / resistance.
- Volume spike detection.
- Basic trade signal generation via `generate_trade_signal()`.

#### Signals

Implemented in `backend/signals.py`:

- `signal_history` table.
- BUY / SELL signal logging.
- Signal deduplication.
- Signal resolution against current/updated prices.
- Signal stats.
- Portfolio table.
- Pinned ticker table.

#### Telegram Bot / Trader UX

Implemented in `/opt/hermes/polstock_bot`:

- `/portfolio`.
- `/buy` and `/sell` using total IDR amount and lot calculation.
- `/close`.
- `/history`.
- `/reset confirm`.
- `/signals`.
- `/pin` and `/unpin`.
- Mini app menu URL configured to `https://polstock.aldimhr.dev/app`.

#### Dashboard

Implemented in `dashboard.html`:

- 5-tab layout: Overview, Events, Watchlist, Portfolio, History.
- Watchlist table with all 30 tickers.
- Pinned / portfolio tickers float to top.
- Portfolio cards.
- Signal cards.
- Technical indicator chips.
- Backtest / source health / reasoning sections.

---

## 3. Current Performance Snapshot

Live API metrics from `GET /api/backtest?window_days=30`:

### All Predictions

- Total predictions: **406**
- Resolved: **382**
- Pending: **24**
- With result: **404**
- Correct: **140**
- Hit rate: **34.7%**
- Neutral baseline: **40.1%**
- Edge vs neutral: **-5.4%**

### Live Predictions Only

From `GET /api/backtest?window_days=30&origin=live`:

- Total predictions: **235**
- Resolved: **211**
- Pending: **24**
- With result: **233**
- Correct: **105**
- Hit rate: **45.1%**
- Neutral baseline: **54.5%**
- Edge vs neutral: **-9.4%**

### Direction Breakdown — Live

- Positive: **30 predictions**, **43.3%** hit rate.
- Negative: **24 predictions**, **0.0%** hit rate.
- Neutral: **179 predictions**, **51.4%** hit rate.

### Significance Breakdown — Live

- High: **198 predictions**, **36.9%** hit rate.
- Medium: **30 predictions**, **100.0%** hit rate.
- Low: **5 predictions**, **40.0%** hit rate.

### Confidence Breakdown — Live

- High: **15 predictions**, **26.7%** hit rate.
- Medium: **75 predictions**, **53.3%** hit rate.
- Low: **143 predictions**, **42.7%** hit rate.

### Interpretation

The system currently has **strong infrastructure** but weak signal quality.

Key observations:

1. **High confidence is not actually reliable.** High-confidence live predictions hit only 26.7%.
2. **Medium confidence/significance performs best.** This suggests the current scoring is overconfident on dramatic political news.
3. **Negative predictions are broken.** 0% hit rate means SELL signals must be disabled or require much stricter confirmation until fixed.
4. **Historical backfill is noisy.** Historical backfill hit rate is 20.5%; it should not drive live product confidence.
5. **Current product should not send many alerts.** Until calibration improves, alerts should be strict and tiered.

---

## 4. Main Gap

Current PolStock is built like this:

```text
Political News → NLP → Event Impact → Stock Prediction → Dashboard
```

What the user wants:

```text
Political/Market Event + Technical Setup + Backtested Calibration
    → Trade Signal
    → Horizon: 1d / 7d / 30d
    → Entry / SL / TP / Reason / Confidence
```

The missing core is a dedicated **Signal Decision Layer**.

Right now, the system has many ingredients but no strict final trader-facing decision engine that says:

- `BUY` / `SELL` / `WATCH` / `IGNORE`
- horizon: `1d`, `7d`, `30d`
- tier: `A`, `B`, `C`, `D`
- entry, stop-loss, take-profit
- reason summary
- invalidation rule
- whether to push Telegram alert

---

## 5. Target Product Behavior

### 5.1 Signal Actions

PolStock should produce four decision types:

#### BUY

Use when the event direction and technical setup both support upside.

Required output:

- Ticker.
- Entry zone.
- Stop-loss.
- Take-profit.
- Horizon: `1d`, `7d`, or `30d`.
- Confidence tier.
- Reason summary.
- Invalidation condition.

#### SELL

Use when downside evidence is strong.

Because current negative accuracy is 0%, SELL must be stricter than BUY.

Initial SELL requirement:

- Event direction negative OR technical breakdown.
- At least 3 technical confirmations.
- Backtest/calibration multiplier must not be weak.
- No alert for SELL until backtest accuracy improves above baseline.

#### WATCH

Use when there is a potential setup but not enough confirmation.

Examples:

- Political event exists but technical setup not ready.
- Technical setup exists but no political/event catalyst.
- Conflicting indicators.
- Source confidence low.

WATCH should appear on dashboard and daily summary, but not send push alerts unless upgraded.

#### IGNORE

Use for noisy events and weak setups.

Examples:

- Repeated news already priced in.
- Vague political statements.
- Weak source.
- No volume/price confirmation.
- Event category historically underperforms.

---

## 6. Time Horizons

Every signal must have one horizon.

### 6.1 1d Signal

Use for short-lived or fast-moving setups.

Typical conditions:

- Fresh breaking event.
- Sudden volume spike.
- Price near support/resistance breakout.
- Bollinger squeeze release.
- MACD histogram turning.

Resolution:

- Evaluate after 24 hours.

Expected hold:

- Intraday to next trading day.

### 6.2 7d Signal

Use for event-driven swing trades.

Typical conditions:

- Policy development likely to affect sector for several sessions.
- News is fresh but not fully priced in.
- Technical trend confirms direction.
- Sector correlation appears.

Resolution:

- Evaluate after 7 calendar days or 5 trading days.

Expected hold:

- 2-5 trading days.

### 6.3 30d Signal

Use for policy/regulation themes with slower market absorption.

Typical conditions:

- Regulation passed or materially confirmed.
- Budget/fiscal/energy/trade policy with sector-wide exposure.
- Strong corroboration across sources.
- Medium-term trend agrees.

Resolution:

- Evaluate after 30 calendar days.

Expected hold:

- 1-3 weeks.

---

## 7. Proposed Signal Decision Model

### 7.1 Inputs

For each ticker:

#### Event Inputs

Already available or mostly available:

- `impact_score`.
- `impact_direction`.
- `relationship_confidence`.
- `corroboration_count`.
- `source_confidence`.
- `source_tier`.
- `event_category`.
- `event_stage`.
- `recency_weight`.
- `source_conflict`.
- `validation_status`.
- `historical_reliability_multiplier`.
- `channel_reliability_multiplier`.
- `sentiment_momentum`.
- `event_cluster_count`.

#### Technical Inputs

Already available or mostly available:

- `price`.
- `change_pct`.
- RSI.
- MACD histogram.
- SMA trend.
- ATR.
- Bollinger percent-b / squeeze.
- Support / resistance.
- Volume spike ratio.
- Foreign market factor.
- Currency factor.
- Sector correlation count.

#### Backtest Inputs

Already partially available:

- Hit rate by direction.
- Hit rate by category.
- Hit rate by confidence bucket.
- Hit rate by significance bucket.
- Hit rate by origin.
- Indicator analysis.

Need to add:

- Hit rate by horizon.
- Hit rate by source.
- Hit rate by signal tier.
- Hit rate by signal type: event-only, technical-only, composite.

---

## 8. Signal Scoring Model

### 8.1 Event Score

```text
event_score = normalized_impact
            × relationship_confidence
            × source_quality_multiplier
            × corroboration_multiplier
            × recency_multiplier
            × category_calibration_multiplier
            × novelty_multiplier
            × conflict_penalty
```

Where:

- `normalized_impact = min(abs(impact_score) / 10, 1.0)`.
- `relationship_confidence` already exists.
- `source_quality_multiplier` already exists conceptually via source quality/freshness.
- `corroboration_multiplier` already exists.
- `recency_multiplier` already exists as recency weight.
- `category_calibration_multiplier` should be computed from live backtest only.
- `novelty_multiplier` needs improvement.
- `conflict_penalty` already partially exists.

### 8.2 Technical Confirmation Score

```text
tech_confirm_count = number of aligned indicators
tech_total = number of available indicators
tech_score = tech_confirm_count / tech_total
```

Minimum confirmations:

- BUY: 2 of 4 core indicators.
- SELL: 3 of 4 core indicators until SELL accuracy improves.
- WATCH: 1 of 4 or mixed setup.

Core indicators:

1. RSI.
2. MACD.
3. Bollinger / support-resistance.
4. Volume spike / trend confirmation.

### 8.3 Composite Signal Strength

```text
signal_strength = event_score * 0.55
                + tech_score * 0.35
                + backtest_calibration_score * 0.10
```

Reason:

- Event signal defines *why* a move should happen.
- Technical signal defines *when* to trade.
- Backtest calibration prevents over-trusting bad categories/sources.

### 8.4 Signal Tier

#### Tier A — Push Alert

Requirements:

- Composite setup.
- `signal_strength >= 0.70`.
- At least 3 technical confirmations.
- No source conflict.
- Category/source calibration not weak.
- Has entry, stop-loss, take-profit.

#### Tier B — Push Alert

Requirements:

- Composite setup.
- `signal_strength >= 0.60`.
- At least 2 technical confirmations.
- No major conflict.

#### Tier C — Dashboard / Daily Summary Only

Requirements:

- Event-only or technical-only setup.
- `signal_strength >= 0.45`.
- Not enough for push alert.

#### Tier D — Logged / Ignored

Requirements:

- Weak, noisy, repeated, vague, or conflicting.

---

## 9. What We Need To Build

### 9.1 Core New Module: `backend/trading_signals.py`

Create a dedicated module for final trading decisions.

Responsibilities:

- Convert current stock payload into final signal decisions.
- Compute event score.
- Compute technical confirmation count.
- Select action: BUY / SELL / WATCH / IGNORE.
- Assign horizon: 1d / 7d / 30d.
- Assign tier: A / B / C / D.
- Produce entry / stop-loss / take-profit.
- Produce concise reason list.
- Produce invalidation rule.

Suggested functions:

```python
def compute_event_score(stock: dict[str, Any]) -> dict[str, Any]: ...
def compute_technical_confirmation(stock: dict[str, Any]) -> dict[str, Any]: ...
def infer_time_horizon(stock: dict[str, Any], event_score: dict[str, Any], tech: dict[str, Any]) -> str: ...
def classify_signal(stock: dict[str, Any]) -> dict[str, Any]: ...
def rank_trade_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]: ...
```

### 9.2 Add Horizon-Aware Backtesting

Current prediction resolution is mostly 1h / 4h / 24h.

Need:

- Add `time_horizon` to predictions.
- Add `return_at_horizon`.
- Add `resolved_at_horizon`.
- Resolve `1d`, `7d`, `30d` separately.
- Add backtest metrics by horizon.

### 9.3 Add Source Accuracy Calibration

Need new per-source calibration table:

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

Use only `origin='live'` predictions for product calibration.

### 9.4 Improve Technical-Only Signal Support

Currently technical indicators mainly support event signals.

Need standalone technical signal detection:

- Oversold bounce candidate.
- Momentum breakout candidate.
- Bollinger squeeze breakout candidate.
- Volume breakout candidate.
- Support bounce / resistance rejection candidate.

These should become WATCH or Tier C unless very strong.

### 9.5 Daily Trading Summary

Need endpoint:

```text
GET /api/signals/daily-summary?horizon=1d|7d|30d|all
```

Response should include:

- Top BUY signals.
- Top SELL/WATCH signals.
- Existing open portfolio risk notes.
- Backtest accuracy note.
- “No trade” message when nothing qualifies.

Telegram should send it at market-prep time, e.g. 08:30 WIB.

### 9.6 Dashboard Refocus

Dashboard should prioritize signals over raw events.

New top-level order:

1. Today’s actionable signals.
2. Watchlist signals by horizon.
3. Portfolio impact / risk.
4. Event reasoning.
5. Backtest/calibration health.

Current dashboard tabs can stay, but Overview should become signal-first.

### 9.7 Bot UX Refocus

Add or adjust commands:

- `/signals` → show top actionable signals grouped by 1d / 7d / 30d.
- `/signal TICKER` → explain current signal for ticker.
- `/daily` → daily summary.
- `/watch` → WATCH candidates, not just BUY/SELL.
- `/why TICKER` → concise reasoning: event + tech + backtest.

---

## 10. Data Model Changes

### 10.1 `signal_history` Additions

```sql
ALTER TABLE signal_history ADD COLUMN time_horizon TEXT DEFAULT '1d';
ALTER TABLE signal_history ADD COLUMN signal_tier TEXT DEFAULT 'D';
ALTER TABLE signal_history ADD COLUMN signal_type TEXT DEFAULT 'event';
ALTER TABLE signal_history ADD COLUMN event_score REAL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN tech_score REAL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN tech_confirmation_count INTEGER DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN calibration_multiplier REAL DEFAULT 1.0;
ALTER TABLE signal_history ADD COLUMN invalidation_reason TEXT;
ALTER TABLE signal_history ADD COLUMN resolved_at_horizon TEXT;
ALTER TABLE signal_history ADD COLUMN return_at_horizon REAL;
```

### 10.2 `predictions` Additions

```sql
ALTER TABLE predictions ADD COLUMN time_horizon TEXT DEFAULT '1d';
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

### 10.3 New `source_accuracy`

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

### 10.4 New `daily_signal_snapshots`

Purpose: store exactly what the user saw each morning so later evaluation is fair.

```sql
CREATE TABLE IF NOT EXISTS daily_signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL,
    ticker TEXT NOT NULL,
    action TEXT NOT NULL,
    time_horizon TEXT NOT NULL,
    signal_tier TEXT NOT NULL,
    entry_price REAL,
    stop_loss REAL,
    take_profit REAL,
    signal_strength REAL,
    reason_json TEXT,
    created_at TEXT NOT NULL
);
```

---

## 11. API Changes

### 11.1 Modify Existing

#### `GET /api/dashboard`

Add per stock:

```json
{
  "trading_signal": {
    "action": "BUY|SELL|WATCH|IGNORE",
    "time_horizon": "1d|7d|30d",
    "signal_tier": "A|B|C|D",
    "signal_type": "event|technical|composite",
    "signal_strength": 0.0,
    "event_score": 0.0,
    "tech_score": 0.0,
    "tech_confirmation_count": 0,
    "entry_price": 0,
    "stop_loss": 0,
    "take_profit": 0,
    "reasons": [],
    "invalidation": ""
  }
}
```

#### `GET /api/signals/history`

Add filters:

- `time_horizon`.
- `signal_tier`.
- `signal_type`.
- `min_strength`.

#### `GET /api/backtest`

Add breakdowns:

- `by_time_horizon`.
- `by_signal_tier`.
- `by_signal_type`.
- `by_source`.

Default should emphasize `origin=live` for product decision-making.

### 11.2 New Endpoints

#### `GET /api/signals/daily-summary`

Query params:

- `horizon=1d|7d|30d|all`.
- `limit=3`.
- `include_watch=true|false`.

#### `GET /api/signals/ticker/{ticker}`

Returns detailed signal explanation for one ticker.

#### `GET /api/calibration/report`

Returns calibration health:

- Accuracy by horizon.
- Accuracy by tier.
- Accuracy by source.
- Accuracy by category.
- Suggested multipliers.

#### `POST /api/calibration/auto-apply`

Applies safe calibration changes only when sample size is sufficient.

---

## 12. Dashboard UX Requirements

### 12.1 Overview Tab

Top section should be **Trading Signals**, not generic market/news.

Display cards:

- “Actionable Today” count.
- “1d signals”.
- “7d signals”.
- “30d signals”.
- “Live accuracy vs baseline”.

### 12.2 Signal Card

Each signal card must show:

```text
BUY CPIN.JK · Tier B · 7d
Entry: 3,300
SL: 3,225 · TP: 3,450 · RR: 1:2
Strength: 0.64 · Tech: 3/4
Reason: Trade policy positive; RSI near support; volume rising
Invalidation: Close below 3,225 or policy reversal
```

### 12.3 Watchlist Table

Add columns/chips:

- Action.
- Horizon.
- Tier.
- Tech confirmations.
- Event score.
- Alert eligibility.

Pinned tickers remain at top.

### 12.4 Backtest/Calibration Tab

Should show:

- Live-only accuracy by horizon.
- Signal tier accuracy.
- Direction accuracy.
- Source accuracy.
- “Do not trust” warnings when sample size is small.

---

## 13. Telegram UX Requirements

### 13.1 `/signals`

Output grouped by horizon:

```text
📈 PolStock Signals

⚡ 1d
No Tier A/B signals. Watch: ADRO.JK

📅 7d
🟢 BUY CPIN.JK · Tier B
Entry 3,300 · SL 3,225 · TP 3,450
Why: trade policy + RSI support + volume rising

🗓 30d
No clean setup.
```

### 13.2 `/daily`

Morning summary:

```text
📊 PolStock Daily — 10 Jun 2026

Best setup: BUY CPIN.JK · 7d · Tier B
Entry: 3,300 · SL: 3,225 · TP: 3,450
Reason: Trade policy positive, 3/4 tech confirmations

Watch only: ADRO.JK, TLKM.JK

Accuracy: live 30d 45.1% vs baseline 54.5% — strict mode ON.
```

### 13.3 `/why TICKER`

Explain one ticker:

```text
CPIN.JK signal: WATCH → not BUY yet
Event: positive trade policy exposure
Tech: 1/4 confirmations only
Missing: volume confirmation, MACD still weak
Action: wait for breakout above 3,350
```

---

## 14. Alert Rules

Until hit rate improves, Telegram push alerts should be strict.

### Push Alert Allowed

- Tier A always.
- Tier B if action is BUY and horizon is 1d or 7d.
- SELL only if SELL backtest accuracy improves above neutral baseline.

### Push Alert Blocked

- Tier C/D.
- WATCH / IGNORE.
- Any signal with source conflict.
- Any category with insufficient sample and weak confidence.
- Repeated same ticker/action within 24h.

### Strict Mode

Because current live edge is -9.4%, strict mode should be ON by default.

Strict mode means:

- Require technical confirmation.
- Suppress SELL push alerts.
- Show WATCH instead of weak BUY.
- Display accuracy warning in `/daily`.

---

## 15. Implementation Plan

### Phase 0 — Spec and Safety Baseline

Goal: Make the refocus explicit and protect current behavior.

Tasks:

1. Complete this `SPEC.md`.
2. Add tests that snapshot current `/api/dashboard`, `/api/signals/history`, `/api/backtest` response shape.
3. Ensure existing tests still pass.
4. Commit docs and safety tests.

Acceptance:

- SPEC describes current implementation and target implementation.
- No feature behavior changed yet.

### Phase 1 — Trading Signal Decision Layer

Goal: Add `backend/trading_signals.py` without disrupting existing event pipeline.

Tasks:

1. Create `backend/trading_signals.py`.
2. Add `compute_event_score(stock)` tests.
3. Add `compute_technical_confirmation(stock)` tests.
4. Add `infer_time_horizon(stock, event_score, tech)` tests.
5. Add `classify_signal(stock)` tests for BUY / SELL / WATCH / IGNORE.
6. Wire `classify_signal()` into dashboard stock payload as `trading_signal`.
7. Keep existing `trade_signal` for backward compatibility during transition.

Acceptance:

- Dashboard payload includes both old `trade_signal` and new `trading_signal`.
- No Telegram alerts use new logic yet.
- Tests cover core classification.

### Phase 2 — Horizon-Aware Persistence

Goal: Store horizon/tier/type for signals and predictions.

Tasks:

1. Add migration-safe columns to `signal_history`.
2. Add migration-safe columns to `predictions`.
3. Update `log_signal()` to store horizon/tier/type/scores.
4. Update prediction recording to store horizon/tier/type/scores.
5. Update `/api/signals/history` filters.
6. Update `/api/backtest` breakdowns.

Acceptance:

- New columns are backward-compatible.
- Existing rows still load.
- New signals include horizon/tier/type.

### Phase 3 — Backtest Calibration

Goal: Make confidence reflect historical performance.

Tasks:

1. Add `source_accuracy` table.
2. Compute live-only source accuracy.
3. Compute live-only category calibration.
4. Add `by_time_horizon`, `by_signal_tier`, `by_signal_type` metrics.
5. Add `/api/calibration/report`.
6. Do not auto-apply until sample size gates exist.

Acceptance:

- Calibration report separates live vs historical.
- Historical backfill no longer pollutes product confidence.

### Phase 4 — Daily Summary and Telegram UX

Goal: Make the system useful every trading day.

Tasks:

1. Add `/api/signals/daily-summary`.
2. Add bot `/daily` command.
3. Refocus `/signals` around 1d/7d/30d.
4. Add `/why TICKER`.
5. Add optional cron for morning summary.

Acceptance:

- User can ask Telegram for current 1d/7d/30d opportunities.
- If no clean signal exists, bot says “No clean setup” clearly.

### Phase 5 — Dashboard Refocus

Goal: Make dashboard signal-first.

Tasks:

1. Add Actionable Signals section to Overview.
2. Add horizon/tier chips to Watchlist.
3. Add Signal detail modal or expanded row.
4. Add calibration warning banner when live accuracy < baseline.
5. Keep Events tab but make it secondary.

Acceptance:

- First screen tells user what to trade/watch, not just what happened.

---

## 16. Testing Requirements

### Unit Tests

Add tests for:

- Event score normalization.
- Technical confirmation count.
- BUY classification.
- SELL strict classification.
- WATCH downgrade.
- IGNORE for conflict/repeated weak event.
- Horizon inference.
- Tier inference.

### Integration Tests

Add tests for:

- `/api/dashboard` includes `trading_signal`.
- `/api/signals/history` accepts horizon/tier filters.
- `/api/backtest` includes horizon/tier/type breakdowns.
- `/api/signals/daily-summary` returns stable shape.

### Smoke Tests

Run after each phase:

```bash
cd /opt/hermes/politics_stock_mapper
POLSTOCK_ENABLE_ML_NLP=0 /opt/hermes/polstock_bot/.venv/bin/python -m pytest tests/ -q
/opt/hermes/polstock_bot/.venv/bin/python -m py_compile backend/*.py
sudo systemctl restart politics-stock-mapper.service
curl -s http://localhost:8001/healthz
curl -s http://localhost:8001/api/dashboard | python3 -m json.tool >/dev/null
curl -s 'http://localhost:8001/api/backtest?window_days=30&origin=live' | python3 -m json.tool >/dev/null
```

---

## 17. Success Metrics

### Product Metrics

- User can open Telegram and immediately see 1d/7d/30d trade candidates.
- User can understand why a signal exists in under 10 seconds.
- “No clean setup” is allowed and common.

### Quantitative Metrics

Initial targets:

- Live non-neutral hit rate: from ~43% to **55%+**.
- Edge vs neutral baseline: from **-9.4%** to **+5%**.
- Tier A/B alert accuracy: **60%+** before increasing alert volume.
- Signal frequency: **5-10 signals/week**, not dozens/day.
- SELL signals remain dashboard-only until SELL accuracy improves above baseline.

### Guardrail Metrics

- If live 30d edge is negative, strict mode stays ON.
- If a category/source has insufficient sample size, do not over-calibrate.
- If historical backfill disagrees with live data, live data wins.

---

## 18. Non-Goals

PolStock should NOT become:

- An automated trading bot.
- A broker integration.
- A crypto/forex signal system.
- A long-term fundamental analysis platform.
- A generic news reader.
- A social media sentiment scraper.
- A scalping tool; RSS/news latency is too slow for true scalping.

---

## 19. Immediate Next Step

Start with **Phase 1: Trading Signal Decision Layer**.

Why Phase 1 first:

- It does not require DB migrations.
- It does not break existing alerts.
- It creates the missing product core.
- It lets dashboard/bot consume better signal objects later.

Recommended first implementation task:

> Create `backend/trading_signals.py` with pure functions and tests for `compute_event_score`, `compute_technical_confirmation`, `infer_time_horizon`, and `classify_signal`.

After that, wire the new `trading_signal` object into `/api/dashboard` while keeping the old `trade_signal` field for compatibility.
