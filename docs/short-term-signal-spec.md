# Short-Term Signal Spec

**Project:** `/opt/hermes/politics_stock_mapper`  
**Status:** Approved Phase 1 product direction  
**Primary objective:** Turn PolStock into a usable **short-term bullish trading signal assistant** for Indonesian stocks.

---

## 1. Product statement

PolStock should help answer this practical trading question:

> **Which stocks are bullish now, supported by heavy participation, and suitable for a 1–14 day trade?**

This means the product is no longer defined primarily as a political/news impact dashboard.

News, policy, and regulatory context are still useful, but only as **secondary conviction inputs**. The primary signal must come from **market behavior**.

---

## 2. Core product principle

The signal engine should be driven by:

1. **bullish structure**
2. **momentum confirmation**
3. **participation expansion**
4. **clear trade trigger**
5. **defined risk and exit plan**

In short:

```text
Bullish setup + participation + trigger + risk plan = tradable signal
```

Not:

```text
Interesting news headline = buy signal
```

---

## 3. v1 product scope

### 3.1 Market bias

PolStock v1 is **long-only** for ranking and user-facing recommendations.

That means:

- the system should focus on **BUY**, **WATCH**, and **IGNORE** outcomes
- bearish / SELL logic may still exist internally for research, but must not dominate user-facing output
- no production claim should be made that PolStock is already a reliable short-selling engine

### 3.2 Supported holding windows

The supported holding windows for v1 are:

- `1d`
- `3d`
- `7d`
- `14d`

These windows represent **intended holding period / urgency**, not guaranteed expiry.

### 3.3 User-facing signal actions

- `BUY`
  - setup is complete
  - participation confirms the move
  - entry / stop-loss / take-profit are defined

- `WATCH`
  - setup is promising but incomplete
  - either trigger is not confirmed yet or participation is still weak

- `IGNORE`
  - no actionable edge
  - contradictory setup or weak evidence

---

## 4. Supported setup families

### 4.1 Breakout continuation

Use when:
- price is pressing into or through resistance
- momentum is bullish
- volume / participation expands
- breakout is fresh enough for a short hold

Typical v1 characteristics:
- RSI in a bullish but not exhausted range
- positive MACD histogram or improving momentum
- breakout / range expansion condition
- participation confirmation required

Preferred outcome:
- `BUY` when participation confirms
- `WATCH` when breakout pressure exists but confirmation is incomplete

### 4.2 Support rebound

Use when:
- price is near support
- downside is stabilizing
- momentum is recovering
- a bounce setup can be risk-defined cleanly

Typical v1 characteristics:
- oversold or recently oversold RSI behavior
- improving MACD / momentum
- price reclaiming from support area
- optional participation boost

Preferred outcome:
- `BUY` if rebound trigger is confirmed
- `WATCH` if rebound thesis exists but reversal is not proven yet

### 4.3 Squeeze breakout watch

Use when:
- volatility compression is present
- direction is leaning bullish
- the market is preparing for expansion

Typical v1 characteristics:
- Bollinger squeeze / narrow range
- positive directional tilt
- improving momentum or rising participation

Preferred outcome:
- `WATCH` by default
- can upgrade to `BUY` only after the breakout trigger is clearly confirmed

### 4.4 News-accelerated breakout

Use when:
- technical setup is already valid
- relevant political/news context increases conviction or urgency

Rules:
- news can improve tier / ranking / confidence
- news can shorten horizon if the move becomes urgent
- news alone must **not** create a BUY signal without technical confirmation

---

## 5. Participation rule

The user goal explicitly includes:

> “when many transaction and bullish i will buy it”

So v1 must represent this honestly using the best available upstream data.

### 5.1 Required interpretation for v1

If transaction-count data is unavailable, use:

- **volume spike ratio**
- **volume vs average**
- **estimated traded value** (`price × volume` proxy)

These must be described as **participation proxies**, not fake transaction counts.

### 5.2 Product rule

A breakout-style `BUY` should generally require strong participation confirmation.

If the stock looks bullish but participation is weak:
- degrade to `WATCH`
- do not overstate conviction

---

## 6. Signal design rules

### 6.1 BUY rules

A `BUY` requires all of the following:

1. bullish directional structure
2. one valid setup family is detected
3. trigger condition is complete or fresh
4. participation is sufficient for the setup type
5. entry, stop-loss, and take-profit can be defined
6. horizon can be assigned clearly (`1d/3d/7d/14d`)

### 6.2 WATCH rules

A `WATCH` is appropriate when:

- the setup is partially formed
- participation is not yet convincing
- momentum is improving but not finished
- technical context is good enough to monitor, not buy yet

### 6.3 IGNORE rules

Use `IGNORE` when:

- there is no coherent bullish setup
- the setup is too noisy or contradictory
- price structure and participation disagree too strongly
- the only “signal” is narrative/news without market confirmation

---

## 7. Horizon rules

### `1d`
Use for:
- breakout ignition
- urgent squeeze release
- very strong participation + momentum alignment

### `3d`
Use for:
- fresh breakout continuation
- strong short-term follow-through setup

### `7d`
Use for:
- normal short swing
- support rebound with confirmation
- bullish continuation that is actionable but less urgent

### `14d`
Use for:
- slower-developing bullish setup
- good structure, but lower urgency than `1d/3d/7d`

---

## 8. Risk plan requirement

Every `BUY` must be explainable as a trade plan, not just a label.

Required user-facing outputs for `BUY`:
- ticker
- setup type
- holding window
- entry price
- stop-loss
- take-profit
- invalidation rule
- top reasons

A signal that cannot provide these is not ready to be called actionable.

---

## 9. Role of news / policy context

News and policy analysis are still useful, but their role changes.

### News can:
- boost conviction
- improve ranking
- explain urgency
- add context to a breakout or rebound

### News cannot:
- replace technical confirmation
- create BUY signals by itself
- outweigh clearly weak participation

This protects the product from becoming a headline-reactor instead of a trading tool.

---

## 10. v1 non-goals

The following are out of scope for the first usable release:

- production-grade bearish / short-selling strategy
- narrative-only or politics-only BUY signals
- pretending upstream data contains transaction-count granularity when it does not
- optimizing for long-horizon investing beyond the short-swing window
- maximizing signal count at the expense of clarity

---

## 11. Success criteria for the next phases

Phase 1 is complete when the product direction is explicit.

The next implementation phases should prove that:

1. fresh backend output can produce real bullish `BUY` signals, not only `WATCH`
2. those `BUY` signals are backed by participation-aware logic
3. horizons are distributed across `1d/3d/7d/14d`, not stuck at one bucket
4. backtest reporting can evaluate long-only actionable setups separately from generic event predictions
5. daily summaries become concise and tradable

---

## 12. Implementation implications

This spec implies the next coding phases must:

- build a dedicated short-term setup scorer
- enrich market-data features for breakout/rebound/participation logic
- persist richer signal evidence in SQLite
- redesign ranking and backtest around actionable long setups

That implementation work belongs to the next phases, not this document.
