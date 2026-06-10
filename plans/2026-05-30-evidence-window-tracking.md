# Evidence Hierarchy + Time-Window Event Tracking Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add evidence-hierarchy scoring and selectable event windows (last 24 hours, last 7 days, last 30 days) so the dashboard can track what happened over the last week or month instead of only the freshest batch.

**Architecture:** Keep the app single-process and flat-file based. Extend the refresh contract with a `window` parameter, thread that through fetch/dedupe/analysis/cache, add event-tracking aggregates to the payload, and enrich article/company evidence with source-tier metadata so the stock links become more explainable and rankable.

**Tech Stack:** FastAPI, vanilla JS, flat JSON (`company_knowledge.json`, `watchlist.json`), pytest.

---

### Task 1: Add window-aware request and cache plumbing

**Objective:** Let the backend accept `24h`, `7d`, and `30d` windows and cache each watchlist/window combination separately.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Steps:**
1. Extend `RefreshRequest` with a `window` field.
2. Add helpers to normalize/validate windows and compute timedelta + labels.
3. Replace the fixed 24-hour filter in article dedupe with window-aware filtering.
4. Include the normalized window in cache keys and payload metadata.
5. Add tests that prove `7d` and `30d` requests survive the pipeline and do not collide with `24h` cache entries.

**Verification:**
- `pytest -q tests/test_app.py -k window`

### Task 2: Add evidence hierarchy primitives

**Objective:** Score and expose evidence quality using explicit source classes instead of only raw source weights.

**Files:**
- Modify: `backend/main.py`
- Modify: `company_knowledge.json`
- Test: `tests/test_app.py`

**Steps:**
1. Add source-tier helpers for article sources (`government`, `regulator`, `company`, `media`, `profile`, `other`).
2. Normalize company evidence records so each item can carry `source_type`, `source_date`, and a derived quality rank.
3. Update evidence scoring to combine source tier, direct mention, theme specificity, and company evidence tier.
4. Expose evidence summary fields on event/stock relationships (for example `article_source_type`, `article_evidence_rank`, `company_evidence_rank`, `evidence_label`).
5. Add tests proving official/regulator signals outrank weak media/profile-only links.

**Verification:**
- `pytest -q tests/test_app.py -k evidence`

### Task 3: Add event-tracking aggregates for last week / last month

**Objective:** Turn the event feed into something users can inspect across a selected time window.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Steps:**
1. Build a small aggregator that rolls analyzed events into daily buckets.
2. Return counts by day, source, category, and policy theme in the refresh payload.
3. Add lightweight headline summaries such as total events, average significance, strongest day, and strongest theme.
4. Ensure the aggregates are generated from the same filtered event set the UI uses.
5. Add tests for daily bucket output and top-theme summaries.

**Verification:**
- `pytest -q tests/test_app.py -k tracking`

### Task 4: Add dashboard controls and rendering for window tracking

**Objective:** Let the user switch between last 24h, last 7d, and last 30d directly in the UI and see tracking summaries.

**Files:**
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Steps:**
1. Add a time-window selector near the refresh controls.
2. Persist the selected window in frontend state and pass it to `/api/refresh` and `/api/dashboard`.
3. Add UI blocks for tracking summary, daily activity, and top themes/sources.
4. Update event badges and footer text to reflect the selected window.
5. Add a UI-contract test for the new DOM hooks.

**Verification:**
- `pytest -q tests/test_app.py -k dashboard`

### Task 5: Update docs and run end-to-end checks

**Objective:** Document the new behavior and verify the app still works as a live dashboard.

**Files:**
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`
- Modify: `SPEC.md`

**Steps:**
1. Document the new `window` parameter and dashboard selector.
2. Document the evidence hierarchy and tracking aggregates.
3. Run the full test suite.
4. Run a local smoke check against `/healthz`, `/api/dashboard`, and `/api/refresh`.
5. Commit and push once verification passes.

**Verification:**
- `pytest -q`
- `python3 -m py_compile backend/main.py`
- smoke-check the local app JSON responses
