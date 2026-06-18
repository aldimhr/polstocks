# Phase 10.2 — Execution Quality

**Goal:** improve PolStock short-term usability for 1–14 day trades by grading entry quality, making trade-management state explicit, decaying stale setups harder, and prioritizing digest output around fresh triggers and management needs.

## Tasks
1. Add focused tests for:
   - entry quality (`ideal` / `acceptable` / `stretched`)
   - management state (`fresh_entry` / `hold` / `reduce` / `exit`)
   - stale setup decay / ranking downgrade
   - digest prioritization for fresh trigger → active management → exit updates
2. Implement backend signal payload enrichment in `backend/trading_signals.py`.
3. Update `backend/main.py` daily summary ranking and digest ordering.
4. Surface the new fields in `dashboard.html` and bot formatting.
5. Run targeted and broader regressions, commit, push, restart, verify.

## Scope guard
Do not stage unrelated working tree files already present in backend repo (`backend/backtest.py`, DB files, watchlist, `.hermes/`, backups, other plans/tests).
