# Phase 10 — Dashboard Polish Before New Backend Phases

**Goal:** Bring the PolStock dashboard up to date with the newer short-term signal backend so the UI actually shows the lifecycle and execution data we already compute.

## Task 10.1: Inspect current dashboard data path
- Trace how `dashboard.html` gets signal data from `/api/dashboard` and `/api/signals/daily-summary`.
- Identify backend-computed fields that are not currently visible in the dashboard, especially:
  - `state_label`
  - `signal_state`
  - `rr_ratio`
  - `risk_reward_label`
  - `next_trigger`
  - `transition_trigger_price`
  - `shortlist_eligible`
  - `alert_ready`
  - digest buckets like `degraded_entries`, `triggered_today`, `manage_open_trades`, `exit_updates`, and `changes`

## Task 10.2: Add focused regressions first
- Extend dashboard HTML runtime-hook tests so the new UI sections and helpers are locked down.
- Add a focused API contract test if the dashboard needs an extra backend field or a richer merge contract.

## Task 10.3: Upgrade actionable signals panel
- Keep the existing top three signal buckets, but add missing operational surfaces:
  - Avoid Chasing / degraded entries
  - Triggered Today
  - Manage Open Trades
  - Exit / Failure
  - What Changed Today summary
- Improve the badge/header copy so the panel reads like a trading desk, not raw JSON.

## Task 10.4: Upgrade stock list + ticker modal signal UX
- Show richer signal metadata in the stock list mini badges/cards.
- Make the ticker modal display lifecycle state, reward/risk, shortlist readiness, and next-trigger guidance.
- Ensure the merge path from daily summary enriches `trading_signal` instead of leaving stale/partial frontend state.

## Task 10.5: Verify end-to-end and ship
- Run targeted pytest selections for dashboard hooks and touched API contracts.
- Run broader app regressions.
- Smoke-check the rendered dashboard output via representative JS rendering paths where practical.
- Commit and push once the dashboard reflects the backend signal model.
