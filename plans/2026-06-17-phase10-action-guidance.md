# Phase 10 — Action Guidance + Exit Intelligence

**Goal:** make PolStock tell the trader what to do after a setup is found or triggered, not just what state it is in.

## Task 10.1: Inspect lifecycle and digest insertion points
- Trace `classify_signal()` and daily summary bucketing for post-entry lifecycle states.
- Keep unrelated backend repo changes out of scope.
- Confirm where Telegram/dashboard surfaces already read digest fields.

## Task 10.2: Add focused regressions first
- Extend signal-classification tests for:
  - `action_guidance`
  - `partial_profit_zone`
  - `trailing_stop_level`
  - `breakeven_ready`
  - lifecycle-specific management copy
- Extend daily-summary tests so guidance fields survive into digest buckets.
- Extend bot formatting tests so the new guidance appears in `/daily` output.

## Task 10.3: Implement backend action guidance
- Map each lifecycle state to trader-facing guidance:
  - `ready_to_buy` → valid buy zone now
  - `triggered_today` → manage size, avoid chasing, define first risk control
  - `active_trade` → hold while above stop / trail if cushion exists
  - `tp_hit` → scale out / lock gains
  - `sl_hit` → exit and wait reset
  - `failed_breakout` → cut risk quickly
- Add compact management fields where possible:
  - `partial_profit_zone`
  - `trailing_stop_level`
  - `breakeven_ready`
  - `management_notes`

## Task 10.4: Expose guidance in output surfaces
- Include the new fields in `/api/signals/daily-summary` responses.
- Update bot daily formatting so management sections show concrete guidance, not just state labels.
- Keep wording end-user friendly and short-trader oriented.

## Task 10.5: Verify and ship
- Run targeted and broader regressions.
- Re-render representative bot summary output.
- Commit, push, restart service if needed, and report next recommended phase.
