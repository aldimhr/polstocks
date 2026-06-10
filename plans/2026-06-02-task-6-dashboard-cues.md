# PolStock Task 6: Final Dashboard Cue Pass

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a compact dashboard-oriented cue block that summarizes the most important source robustness signals now that backend semantics for conflicts, thread status, source health, and historical reliability have stabilized.

**Architecture:** Keep the change additive at the dashboard seam. Derive a small `dashboard_cues` object from the already-built refresh payload rather than changing relationship scoring again. Expose it on `/api/dashboard`, then let the frontend render a single batch-level robustness strip/card from that cue block.

**Tech Stack:** FastAPI backend (`backend/main.py`), static dashboard UI (`dashboard.html`), pytest (`tests/test_app.py`).

---

## Intended cue contract

Add a top-level `dashboard_cues` object on `/api/dashboard` with stable, frontend-friendly fields such as:
- `headline`: one short line summarizing robustness state
- `status`: overall bucket like `healthy`, `watch`, or `fragile`
- `chips`: array of compact badges/alerts
- `counts`: compact numeric fields the UI can display without recomputing from raw payload

The cue block should be derived from existing payload signals, especially:
- `payload.source_health_summary`
- `payload.reasoning_summary`
- top stock/event metadata already carrying:
  - `source_conflict_label`
  - `thread_status`
  - `historical_reliability_multiplier`
  - `historical_outcome_sample_size`
  - `coverage_warning`

## Task breakdown

### Task 6A: Lock the dashboard cue contract with a failing API regression
- Modify `tests/test_app.py`
- Add a focused test near `test_dashboard_endpoint_returns_watchlist_and_payload`
- Assert `/api/dashboard` includes `dashboard_cues`
- Assert it contains additive cue-ready fields and that at least one chip reflects source robustness semantics from the payload
- Run the narrow test and confirm RED

### Task 6B: Build compact cue aggregation in the backend
- Modify `backend/main.py`
- Add a helper that derives `dashboard_cues` from the already-built dashboard payload
- Keep logic additive and compact; do not rewrite scoring or payload assembly
- Wire the helper into `api_dashboard()` only
- Re-run the narrow regression and confirm GREEN

### Task 6C: Render the cue block in the static dashboard
- Modify `dashboard.html`
- Add a dedicated robustness strip/card near the reasoning summary/tracking surface
- Render `dashboard_cues.headline` and cue chips directly from the API result
- Keep existing per-row provenance/conflict chips intact
- Add/update dashboard runtime-hook tests if needed

### Task 6D: Verify nearby and full regressions
- Run targeted tests covering:
  - dashboard endpoint
  - refresh payload summaries
  - conflict/thread status
  - persistent reliability calibration
- Run full `pytest tests/test_app.py -q`

### Task 6E: Commit and push scoped files
- Stage only:
  - `backend/main.py`
  - `dashboard.html`
  - `tests/test_app.py`
  - this plan doc
- Use a conventional commit message
- Push to `origin/main`

## Notes / guardrails
- Do not stage unrelated `watchlist.json` churn.
- Do not pull in the older untracked hardening-plan doc.
- Keep Task 6 additive and UX-focused; backend semantics are considered stabilized for this pass.
- Prefer cue labels that are directly explainable from payload data rather than opaque score math.
