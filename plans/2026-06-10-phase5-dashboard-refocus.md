# Phase 5 — Dashboard Refocus

**Goal:** Make the dashboard signal-first. Add actionable signals section to Overview, add horizon/tier chips to Watchlist, add calibration banner, update signal card.

**Depends on:** Phases 1-4 complete.

**File:** `/opt/hermes/politics_stock_mapper/dashboard.html` (4429 lines, single-file HTML dashboard)

---

## Task 5.1: Add Actionable Signals section to Overview tab

**Objective:** New card at the top of Overview that shows top signals from `/api/signals/daily-summary`, grouped by horizon.

**Location:** After the overview-grid (after line 1841), before the main-grid (line 1844).

**HTML:** New card with `data-tab="overview"`, id `actionableSignalsCard`, with three horizon sections (1d/7d/30d).

**JS:** New `renderActionableSignals()` function called from `renderPayload()`. Fetches from `/api/signals/daily-summary` and renders signal cards.

---

## Task 5.2: Add horizon/tier chips to Watchlist table

**Objective:** In `renderStocks()`, add trading_signal chips showing action (BUY/SELL/WATCH), horizon (1d/7d/30d), and tier (A/B/C/D) next to the ticker name.

**Location:** In the `renderStocks()` function (line 3620), modify the ticker cell HTML.

**JS:** Read `stock.trading_signal` and render chips before the reasoning line.

---

## Task 5.3: Add calibration warning banner

**Objective:** Show a warning banner when live accuracy is below neutral baseline.

**Location:** Top of overview tab, before metrics.

**JS:** Fetch from `/api/calibration/report`, show banner if edge < 0.

---

## Task 5.4: Update signal card with horizon/tier

**Objective:** Update `loadSignals()` to show time_horizon, signal_tier, and signal_type from the Phase 2 columns.

**Location:** `loadSignals()` function (line 4199).
