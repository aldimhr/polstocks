# PolStock — Short-Term Trading Signal Spec

## Vision

PolStock should be a **short-term political event → stock signal engine** for Indonesian equities.
The goal: detect political/regulatory events early, score their likely market impact, and produce
actionable BUY/SELL signals with defined time horizons (1d / 7d / 30d).

Not a long-term investment tool. Not a news aggregator with a dashboard.
A **signal machine** that answers: *"This just happened — should I trade it, and for how long?"*

---

## Current State (Honest Assessment)

### What Exists

| Layer | Status | Notes |
|-------|--------|-------|
| News ingestion (19 RSS sources) | ✅ Working | 19 Indonesian political/news sources, dedup, freshness |
| NLP (sentiment + NER + category) | ✅ Working | RoBERTa sentiment, IndoBERT NER, keyword category classifier |
| Event→ticker linking | ✅ Working | Policy channel matching, company knowledge base, exposure scoring |
| Technical indicators | ✅ Working | RSI, MACD, SMA trend, Bollinger Bands, ATR, support/resistance, volume spike |
| Trade signal generator | ⚠️ Partial | `generate_trade_signal()` exists but gates on `signal_strength ≥ 0.6` — almost never fires |
| Backtest system | ⚠️ Partial | Tracks predictions, resolves outcomes, but accuracy is below baseline |
| Telegram alerts | ✅ Working | BUY/SELL push with dedup, quiet hours, formatting |
| Portfolio tracking | ✅ Working | Open/close positions, live P&L, trade history |
| Dashboard | ✅ Working | 5-tab UI, watchlist with pins, all 30 tickers |
| Signal history | ✅ Working | Log, resolve, stats with 7-day expiry |
| Weight tuning | ✅ Working | 29 tunable weights, override file, auto-tune suggestions |

### What's Broken

| Problem | Impact | Root Cause |
|---------|--------|------------|
| **45% hit rate vs 55% neutral baseline** | Worse than random | News→price link is weak; most events don't move stocks |
| **Negative predictions: 0% accuracy** | All negatives forced to neutral | System can't predict drops — likely over-indexes on negative sentiment keywords |
| **Signals almost never fire** | `signal_strength ≥ 0.6` gate too strict | Only 30 positive signals out of 235 predictions in 30 days |
| **No time-horizon alignment** | Can't tell "trade today" vs "trade this week" | Timeframe is ad-hoc based on signal strength, not event decay |
| **High-significance events: 37% accuracy** | Worse than low-significance (40%) | Over-confident on big news; scoring weights not calibrated |
| **Medium-confidence predictions: 100% accuracy** | Suspicious — only 30 predictions | Likely a data artifact; not enough sample |
| **Historical backfill: 20% accuracy** | Pollutes metrics | Wayback Machine general news doesn't predict stock movements |

### Key Insight from Data

The **medium-significance bucket (76% accuracy)** and **medium-confidence bucket (53%)** outperform.
This suggests the system works best when it's *uncertain enough to be calibrated* — not when it's
confident. The scoring engine over-weights big dramatic events that are actually priced in already.

---

## Design Principles

1. **Precision over recall** — Better to miss signals than produce bad ones. A user who loses money once won't come back.
2. **Every signal must have a time horizon** — No vague "bullish." Either "BUY, 1-3d horizon" or nothing.
3. **Technical confirmation required** — NLP alone can't predict stock movements. News sets the *direction*, technicals confirm the *timing*.
4. **Backtest-driven tuning** — Every weight adjustment must show improvement in backtest. No gut-feel tuning.
5. **Simplicity** — The system is already 2968 lines in main.py. New features should reduce complexity, not add it.

---

## Target Signal Architecture

### Signal Pipeline (Proposed)

```
[RSS Feed] → [Event Detection] → [NLP Scoring] → [Impact Score]
                                                       ↓
[Yahoo Finance] → [Technical Indicators] → [Tech Score] → [Composite Signal] → [Time Horizon] → [Alert]
                                                       ↑
[Backtest DB] → [Historical Calibration] → [Weight Adjustment]
```

### Signal Types

| Type | Source | Time Horizon | Confidence Gate |
|------|--------|-------------|-----------------|
| **Event-driven** | Political news + NLP | 1-7d | `event_impact ≥ 3` AND `tech_confirm ≥ 2/4` |
| **Technical** | Price patterns only | 1-3d | `signal_strength ≥ 0.5` AND ≥ 2 indicators align |
| **Composite** | Event + Technical | 1-30d | Both scores above threshold, same direction |

### Time Horizons

Every signal MUST carry one of:

| Horizon | Meaning | Resolution Window | Expected Hold |
|---------|---------|-------------------|---------------|
| `1d` | Intraday/next-day move | 24 hours | Hours to 1 day |
| `7d` | Week-long trend | 7 days | 1-5 days |
| `30d` | Multi-week position | 30 days | 1-3 weeks |

Time horizon is determined by:
- **Event decay**: Breaking news → 1d, policy announcement → 7d, regulation passed → 30d
- **Signal strength**: Stronger signals get shorter horizons (more conviction = act faster)
- **Technical setup**: Bollinger squeeze → 1d, trend reversal → 7d, breakout → 30d

### Technical Confirmation Matrix

For a BUY signal to fire, at least 2 of 4 must agree:

| Indicator | BUY confirm | SELL confirm |
|-----------|-------------|--------------|
| RSI | < 40 (oversold) | > 60 (overbought) |
| MACD histogram | > 0 and rising | < 0 and falling |
| Bollinger %B | < 0.2 (near lower band) | > 0.8 (near upper band) |
| Volume spike | ≥ 1.5x avg AND direction aligns | ≥ 1.5x avg AND direction aligns |

For a SELL signal, same logic inverted.

If indicators conflict → signal downgraded to WATCH, not sent as alert.

### Signal Strength Formula (Proposed Revision)

Current formula:
```
signal_strength = 0.35*confidence + 0.25*corroboration + 0.20*validation + 0.20*tech_alignment
```

Proposed:
```
signal_strength = event_score × tech_confirmation × calibration_multiplier
```

Where:
- `event_score` = impact_score (0-10) × confidence × corroboration × category_weight
- `tech_confirmation` = (agreeing_indicators / total_indicators), range 0-1
- `calibration_multiplier` = backtest-derived per-category, per-confidence multiplier

This forces signals to have BOTH event conviction AND technical agreement.

---

## What Needs to Change

### Phase 1: Fix the Foundation

**Goal**: Get the signal pipeline producing accurate signals before adding features.

#### 1.1 Remove negative prediction suppression
- **Current**: `expected_direction_for_company()` force-converts "negative" → "neutral"
- **Change**: Remove suppression. Instead, require SELL signals to have ≥ 3 technical confirmations (stricter than BUY's 2)
- **Why**: Suppression hides real problems. Better to understand why negatives fail.

#### 1.2 Revise signal strength gate
- **Current**: `signal_strength ≥ 0.6` — almost never fires
- **Change**: Separate gates for event-only vs technical-confirmed signals:
  - Event-only: `event_impact ≥ 4` AND `confidence ≥ 0.5` (high bar, rare)
  - Tech-confirmed: `event_impact ≥ 2` AND `tech_agreement ≥ 2/4` (lower event bar, needs tech backup)
  - Technical-only: `tech_agreement ≥ 3/4` AND `signal_strength ≥ 0.5` (no news required)

#### 1.3 Add time horizon to every signal
- **Current**: Timeframe is ad-hoc string based on strength
- **Change**: Compute from event category + stage + decay:
  - `event_stage == "breaking"` → `1d`
  - `event_stage == "developing"` → `7d`
  - `event_stage == "established"` → `30d`
  - Technical-only signals → `1d` default
- Store `time_horizon` in `signal_history` table and `predictions` table

#### 1.4 Calibrate scoring from backtest data
- **Current**: Weights are manually tuned or auto-suggested but rarely applied
- **Change**: Compute calibration multipliers from resolved predictions:
  - Per category: `actual_hit_rate / expected_hit_rate`
  - Per confidence bucket: same
  - Apply as multiplier in signal strength formula
- **Auto-apply** weekly (cron) with human-readable diff logged

#### 1.5 Fix resolution windows
- **Current**: 1h/4h/24h resolution only
- **Change**: Add resolution at signal's time horizon:
  - `1d` signal → resolve at 24h
  - `7d` signal → resolve at 7d
  - `30d` signal → resolve at 30d
- Store resolution at each horizon for multi-window accuracy tracking

### Phase 2: Improve Signal Quality

#### 2.1 Standalone technical signals
- RSI oversold + MACD crossover + volume spike → BUY (no news needed)
- RSI overbought + MACD crossover down + volume spike → SELL
- Must have ≥ 3/4 indicators agreeing
- Default time horizon: 1d (technical setups are short-lived)

#### 2.2 Event novelty scoring
- **Current**: Novelty dampens repeated events (1.0 → 0.8 → 0.6 → 0.4)
- **Change**: Also boost truly novel events:
  - First event in category for ticker in 30d → 1.3x boost
  - First event from source in 7d → 1.1x boost
  - Repeated event within 24h → 0.3x (nearly suppress)

#### 2.3 Source quality refinement
- **Current**: Source tiers (tier_1/2/3) with quality/freshness scores
- **Change**: Track per-source prediction accuracy:
  - Sources with >60% accuracy on ≥10 predictions → boost confidence by 1.15x
  - Sources with <30% accuracy on ≥10 predictions → dampen by 0.85x
  - New sources (<5 predictions) → neutral 1.0x

#### 2.4 Sector rotation detection
- If multiple tickers in a sector get same direction signal → boost by `sector_alignment_factor`
- If sector signals conflict → dampen all signals in that sector
- Already have `sector_correlation_count` but it's not used for signal gating

### Phase 3: Make It Useful

#### 3.1 Daily signal summary (Telegram)
- **New cron job**: Every market day at 08:30 WIB (before market open)
- Sends: Top 3 signals for the day, with entry/SL/TP/horizon
- Format: concise, actionable, no jargon
- Example:
  ```
  📊 PolStock Daily — 10 Jun 2026
  
  🟢 BUY CPIN.JK — 7d horizon
  Entry: 3,300 → TP: 3,450 / SL: 3,225
  Reason: Trade policy positive, RSI oversold, volume rising
  
  🟡 WATCH ADRO.JK — 1d horizon
  Energy policy developing, MACD near crossover
  
  📈 30d accuracy: 52% (11/21 correct)
  ```

#### 3.2 Signal confidence tiers
| Tier | Meaning | Alert? | Auto-trade? |
|------|---------|--------|-------------|
| A | Event + 3+ tech confirm + calibrated | ✅ Push | ❌ (future) |
| B | Event + 2 tech confirm | ✅ Push | ❌ |
| C | Event-only OR tech-only | ❌ Dashboard only | ❌ |
| D | Low confidence / conflicting | ❌ Logged only | ❌ |

#### 3.3 Weekly calibration report
- Every Sunday: accuracy by category, by source, by direction, by time horizon
- Compare to baseline, flag degraded categories
- Auto-suggest weight adjustments with expected impact
- Push summary to Telegram

#### 3.4 Clean historical data
- Remove or isolate `historical_backfill` predictions (20% accuracy pollutes metrics)
- Only count `origin=live` for real-time calibration
- Historical data useful for training but not for live accuracy tracking

---

## Database Schema Changes

### signal_history (add)
```sql
ALTER TABLE signal_history ADD COLUMN time_horizon TEXT DEFAULT '1d';
ALTER TABLE signal_history ADD COLUMN signal_tier TEXT DEFAULT 'C';
ALTER TABLE signal_history ADD COLUMN tech_confirmation_count INTEGER DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN event_score REAL DEFAULT 0;
ALTER TABLE signal_history ADD COLUMN calibration_multiplier REAL DEFAULT 1.0;
ALTER TABLE signal_history ADD COLUMN resolved_at_horizon TEXT;
ALTER TABLE signal_history ADD COLUMN return_at_horizon REAL;
```

### predictions (add)
```sql
ALTER TABLE predictions ADD COLUMN time_horizon TEXT DEFAULT '1d';
ALTER TABLE predictions ADD COLUMN tech_confirmation_count INTEGER DEFAULT 0;
ALTER TABLE predictions ADD COLUMN signal_tier TEXT DEFAULT 'C';
```

### New: source_accuracy
```sql
CREATE TABLE IF NOT EXISTS source_accuracy (
    source_id TEXT PRIMARY KEY,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    hit_rate REAL DEFAULT 0.5,
    last_updated TEXT
);
```

---

## API Changes

### Modified Endpoints

- `GET /api/signals/history` — Add `time_horizon`, `signal_tier` filters
- `GET /api/backtest` — Add `by_time_horizon` breakdown, separate `live` vs `historical` by default
- `GET /api/dashboard` — Stocks include `time_horizon` and `signal_tier` for top signals

### New Endpoints

- `GET /api/signals/daily-summary` — Today's top signals for Telegram cron
- `GET /api/calibration/report` — Weekly calibration metrics
- `POST /api/calibration/auto-apply` — Apply calibration multipliers from backtest

---

## Success Metrics

| Metric | Current | Target (30d) | Target (90d) |
|--------|---------|--------------|--------------|
| Hit rate (live, non-neutral) | 43% | 55% | 60% |
| Edge vs neutral baseline | -9.4% | +5% | +10% |
| Signals per week | ~4 | 5-10 | 10-20 |
| Alert accuracy (A+B tier) | N/A | 55% | 65% |
| Time horizon accuracy | N/A | 50% at horizon | 60% at horizon |
| User-reported usefulness | N/A | "Useful 2x/week" | "Daily driver" |

---

## What We're NOT Building

- ❌ Automated trading (no broker API integration)
- ❌ Options/derivatives signals (cash equities only)
- ❌ Crypto signals
- ❌ Long-term portfolio optimization
- ❌ Real-time intraday scanning (RSS is delayed, not suitable for scalping)
- ❌ Sentiment analysis of social media (too noisy for Indonesian market)

---

## Implementation Priority

1. **Phase 1** (this week): Fix signal pipeline — remove suppression, revise gates, add time horizons, fix resolution windows
2. **Phase 2** (next week): Improve quality — technical signals, novelty boost, source calibration
3. **Phase 3** (following week): Make it useful — daily summary cron, confidence tiers, weekly report

Each phase should be testable independently and show measurable improvement in backtest before moving on.
