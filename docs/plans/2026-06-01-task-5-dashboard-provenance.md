# PolStock Task 5: Dashboard Provenance and Confidence Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make source provenance and confidence visible in the dashboard so users can see when an event is official, fresh, sparse, or duplicated without exposing implementation jargon.

**Architecture:** Reuse the existing backend refresh payload and static `dashboard.html` as the only UI surface. The backend should already compute relationship confidence, source confidence, freshness, coverage warnings, and validation status; Task 5 should simply expose the right payload fields and render them as compact badges and labels in the dashboard. Keep the UI minimal, mobile-friendly, and consistent with the current single-file dashboard approach.

**Tech Stack:** Python 3.11, FastAPI, static HTML/vanilla JS, pytest, JSON payloads.

---

## Current code anchors

Use these existing integration points:

- `backend/main.py`
  - `build_refresh_payload(...)`
  - `build_reasoning_summary(...)`
  - event formatting near the end of the refresh path
  - stock row formatting in the same payload
- `dashboard.html`
  - current event feed rendering
  - stock card / list rendering
  - any existing badge or label helpers
- `tests/test_app.py`
  - `test_dashboard_endpoint_returns_watchlist_and_payload`
  - `test_dashboard_contains_runtime_hooks`
  - `test_refresh_endpoint_returns_json_shape`
  - relationship confidence / validation tests added in Task 4

Do **not** add a new frontend framework or split the dashboard into separate assets. Keep the implementation additive and local to the current files.

---

## Provenance display rules

1. **Keep the UI simple.**
   - Prefer short badges like `Official`, `Fresh`, `Sparse sources`, `Duplicated coverage`, `High confidence`.
   - Avoid exposing internal field names in user-facing copy.

2. **Surface confidence, don’t over-explain it.**
   - Show the result of the scoring, not the math.
   - Use compact labels and tooltips only if the dashboard already has a pattern for that.

3. **Show weak evidence honestly.**
   - Sparse or stale coverage should be visibly downgraded.
   - If relationships are `predicted_only` or `insufficient_data`, the UI should make that obvious.

4. **Preserve existing click/refresh flow.**
   - Dashboard usability must not regress.
   - The main article list, stock list, and window switcher should keep working exactly as before.

---

## Proposed dashboard fields

The backend payload should expose enough information for the dashboard to render badges without extra derivation in the browser. Reuse the fields already added in Tasks 3 and 4, such as:

- `source_type`
- `source_quality_score`
- `source_freshness_score`
- `coverage_warning`
- `relationship_confidence`
- `confidence_label`
- `source_confidence`
- `evidence_strength`
- `validation_status`
- `validation_score`

The dashboard can then map these to friendly labels:

- `government`, `regulator`, `company` → `Official`
- `high_confidence` → `High confidence`
- `stale_coverage` → `Stale`
- `thin_source_coverage` → `Sparse sources`
- `duplicated_coverage` → `Duplicated coverage`
- `predicted_only` / `insufficient_data` → `Unverified` or `Needs more evidence`

---

## Task 5.1: Add dashboard-friendly provenance fields to the payload

**Objective:** Ensure the dashboard receives all source and confidence data it needs without recomputing anything client-side.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Write a failing payload-shape test**

Add or extend a test that inspects `client.get("/api/dashboard?window=7d")` and asserts that:
- at least one event includes `source_type`, `source_quality_score`, `source_freshness_score`, `coverage_warning`
- at least one stock row includes `relationship_confidence`, `confidence_label`, `source_confidence`, and `evidence_strength`
- the reasoning summary still remains present and unchanged in shape

**Step 2: Run the test to confirm the current payload is incomplete**

Run:
```bash
pytest tests/test_app.py -k "dashboard_endpoint_returns_watchlist_and_payload or source_confidence or confidence_label or coverage_warning" -v
```
Expected: FAIL until the payload includes all required fields.

**Step 3: Implement the minimal payload additions**

Update the refresh/dashboard formatting in `backend/main.py` so both event objects and stock objects carry the fields needed by the dashboard.

Prefer to reuse the values already computed by analysis rather than duplicating logic.

**Step 4: Re-run the focused test**

Run:
```bash
pytest tests/test_app.py -k "dashboard_endpoint_returns_watchlist_and_payload or source_confidence or confidence_label or coverage_warning" -v
```
Expected: PASS.

**Step 5: Commit if this task is being executed independently**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: expose dashboard provenance metadata"
```

---

## Task 5.2: Render compact source badges in `dashboard.html`

**Objective:** Show friendly provenance badges in the dashboard without cluttering the layout.

**Files:**
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Step 1: Add a failing HTML/runtime-hook test**

Extend `test_dashboard_contains_runtime_hooks` or add a new test that asserts the dashboard HTML contains the badge labels or rendering hooks needed for:
- `Official`
- `High confidence`
- `Fresh`
- `Sparse sources`
- `Duplicated coverage`

If the dashboard uses JavaScript helpers, assert those helper names or DOM hooks exist in the HTML.

**Step 2: Run the test to confirm the UI does not yet expose the badges**

Run:
```bash
pytest tests/test_app.py -k "dashboard_contains_runtime_hooks or provenance or badge" -v
```
Expected: FAIL until the labels and hooks are added.

**Step 3: Implement the minimal dashboard rendering**

Update `dashboard.html` to display compact badges for the new metadata.

Implementation guidance:
- Keep badges small and readable on mobile.
- Use short copy only.
- Reuse existing card structure and CSS if possible.
- Do not add a framework.

**Step 4: Re-run the test**

Run:
```bash
pytest tests/test_app.py -k "dashboard_contains_runtime_hooks or provenance or badge" -v
```
Expected: PASS.

---

## Task 5.3: Add confidence-aware labels to stock cards and event cards

**Objective:** Make it obvious when a stock link is strong, weak, or unverified.

**Files:**
- Modify: `dashboard.html`
- Modify: `backend/main.py` only if a tiny payload helper is needed
- Test: `tests/test_app.py`

**Step 1: Write a regression test for displayed labels**

Add a test that inspects the `/api/dashboard` payload and asserts at least one stock or event includes a confidence label compatible with the new UI copy.

The test should prove that the UI can distinguish between:
- strong/official evidence
- weak or sparse evidence
- predicted-only / insufficient-data states

**Step 2: Implement label mapping in the UI**

Add a small mapping function in `dashboard.html` that turns backend values into short user-facing labels.

Examples:
- `government`, `regulator`, `company` → `Official`
- `high_confidence` → `High confidence`
- `low_confidence` → `Low confidence`
- `predicted_only` → `Needs more evidence`
- `stale_coverage` → `Stale`

**Step 3: Verify the rendered dashboard still loads**

Run:
```bash
pytest tests/test_app.py -k "root_serves_dashboard_html or dashboard_contains_runtime_hooks or dashboard_endpoint_returns_watchlist_and_payload" -v
```
Expected: PASS.

---

## Task 5.4: Keep the dashboard responsive and non-cluttered

**Objective:** Ensure the new badges do not break mobile layout or overload the screen.

**Files:**
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Step 1: Add a layout regression test**

Extend the existing dashboard HTML test to assert that the page still contains the responsive structure already used by the app.

If there are existing viewport/meta/grid hooks, keep them intact and verify them.

**Step 2: Adjust CSS if needed**

If badge rows or provenance labels wrap awkwardly, adjust the CSS in `dashboard.html` to keep the layout compact.

Do not introduce a heavy design system.

**Step 3: Verify the page still renders as expected**

Run:
```bash
pytest tests/test_app.py -v
```
Expected: all dashboard and payload tests pass.

---

## Task 5.5: Update docs to describe the visible provenance cues

**Objective:** Keep the docs aligned with the dashboard behavior so future changes do not drift.

**Files:**
- Modify: `ARCHITECTURE.md`
- Optionally modify: `SPEC.md` if a new dashboard requirement needs to be added

**Step 1: Document the dashboard provenance layer**

Add a short note saying the dashboard now shows:
- source type / official status
- confidence labels
- freshness cues
- sparse / duplicated coverage warnings

**Step 2: Verify the docs match the UI copy**

Make sure the documentation uses the same friendly language the dashboard uses.

---

## Verification command

When Task 5 is implemented, run:

```bash
pytest tests/test_app.py -k "dashboard or provenance or badge or confidence or coverage_warning" -v
```

Then run the full suite:

```bash
pytest tests/test_app.py -v
```

Expected: all relevant tests pass.

---

## Commit plan

If Task 5 is completed in one pass, commit the code and docs together:

```bash
git add backend/main.py dashboard.html tests/test_app.py ARCHITECTURE.md SPEC.md
git commit -m "feat: show provenance and confidence in dashboard"
```

---

## Acceptance criteria

Task 5 is done when:

- The dashboard shows friendly source provenance cues.
- Strong official evidence is clearly distinguishable from weak coverage.
- Freshness and sparse/duplicated coverage are visible.
- The API payload contains everything the dashboard needs.
- Existing refresh and click behavior still works.
- Regression tests cover the payload and the dashboard HTML.

---

## Notes

- Keep it minimal.
- Prefer badges over verbose text.
- Use user-friendly language, not backend terminology.
- Do not add a separate frontend build step.
- If a label is weak, say so plainly.
