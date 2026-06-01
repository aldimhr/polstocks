# PolStock Task 6: Market Validation Documentation and Verification Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make the additive market-validation layer easy to trust by keeping its tests, docs, and user-facing status language aligned with the current backend behavior.

**Architecture:** The backend already computes market-validation outcomes on each stock relationship and rolls them into stock rows and event payloads. Task 6 should be a small audit-and-polish pass: tighten any missing regression coverage, ensure the docs explain the validation statuses clearly, and verify the full test suite still passes after any wording or payload adjustments.

**Tech Stack:** Python 3.11, FastAPI, vanilla JS dashboard, pytest, Markdown docs.

---

## Current code anchors

Use these existing integration points:

- `backend/main.py`
  - `fetch_market_validation_series(...)`
  - `validate_market_reaction(...)`
  - `build_refresh_payload(...)` stock/event payload assembly
  - `build_reasoning_summary(...)` validation breakdown aggregation
- `tests/test_app.py`
  - validation-specific fake series fixtures
  - confirmed / predicted-only / unavailable regression tests
- `SPEC.md`
  - `F-48`, `F-49`, `F-50` validation and confidence requirements
- `README.md`
  - human-readable feature summary and validation status language

Do **not** add a new validation subsystem. Keep Task 6 focused on aligning the current additive layer with tests and docs.

---

## Validation display rules

1. **Be explicit about status.**
   - Use `confirmed`, `predicted_only`, `rejected`, `insufficient_data`, and `unvalidated` consistently.

2. **Do not hide weak evidence.**
   - If market history is missing or noisy, keep the text-based relationship and mark it clearly.

3. **Keep the wording user-friendly.**
   - Prefer plain language like “confirmed by market move” or “not enough market history” in docs.

4. **Keep payloads additive.**
   - The dashboard and API should keep the existing fields and just add validation metadata.

---

## Task 6.1: Audit the validation regression coverage

**Objective:** Confirm the backend already exposes the validation fields required by the spec and add any missing regression coverage for the edge cases that matter most.

**Files:**
- Modify if needed: `tests/test_app.py`
- Modify if needed: `backend/main.py`

**Step 1: Inspect the existing validation tests**

Review the tests around:
- confirmed market move
- flat/noisy series
- unavailable history
- payload propagation into `/api/dashboard` and `/api/refresh`

Look for any gap where a regression test should assert:
- `validation_reason`
- `validation_series_source`
- `validation_status` on event relationships
- `validation_status` on stock rows

**Step 2: Add a failing test only if a gap exists**

If an edge case is missing, add the smallest possible regression test in `tests/test_app.py`.

Example assertion shape:
```python
assert validation["validation_status"] == "confirmed"
assert validation["validation_reason"]
assert validation["validation_series_source"]
```

**Step 3: Run the focused validation tests**

Run:
```bash
pytest tests/test_app.py -k "validation or confirmed or predicted or insufficient" -v
```

Expected: PASS.

---

## Task 6.2: Align docs with the validation layer

**Objective:** Make the market-validation story obvious in the repo docs without adding implementation jargon.

**Files:**
- Modify: `README.md`
- Modify if needed: `ARCHITECTURE.md`
- Modify if needed: `SPEC.md`

**Step 1: Update the docs copy**

Make sure the docs clearly say that:
- text prediction and market confirmation are separate
- `predicted_only` does not mean the relationship is hidden
- `confirmed` means market history supports the prediction
- `insufficient_data` means the app stayed resilient and did not fail

**Step 2: Keep the copy short and user-friendly**

Do not turn the README into a changelog. Add only the smallest wording changes needed for clarity.

**Step 3: Verify docs consistency**

Re-read the updated sections and make sure the status labels match the backend and tests.

---

## Task 6.3: Verify the full app behavior

**Objective:** Prove the validation layer, docs, and payloads still behave together.

**Files:**
- No new files expected

**Step 1: Run the focused validation subset**

Run:
```bash
pytest tests/test_app.py -k "validation or confirmed or predicted or insufficient or dashboard_endpoint_returns_watchlist_and_payload" -v
```

Expected: PASS.

**Step 2: Run the full app test suite**

Run:
```bash
pytest tests/test_app.py -v
```

Expected: all tests pass.

**Step 3: Review the diff**

Run:
```bash
git status --short
git diff --stat
```

Confirm only intended docs/test edits are present.

---

## Task 6.4: Commit and push the validation polish

**Objective:** Land the validation polish cleanly once tests are green.

**Files:**
- Whatever changed in the steps above

**Step 1: Commit the changes**

```bash
git add backend/main.py tests/test_app.py README.md ARCHITECTURE.md SPEC.md docs/plans/2026-06-01-task-6-market-validation.md
git commit -m "docs: verify market validation layer"
```

**Step 2: Push**

```bash
git push origin main
```

---

## Acceptance criteria

Task 6 is done when:

- the validation layer is covered by focused regression tests
- docs explain `predicted_only` vs `confirmed` vs `insufficient_data`
- `/api/dashboard` and `/api/refresh` continue to expose validation metadata
- the app remains resilient when market history is missing
- the full `tests/test_app.py` suite passes

---

## Notes

- Keep this task small.
- If the implementation already exists, prefer a docs-and-verification pass over refactoring working code.
- Do not introduce a second validation system.
- Use friendly status language in docs and UI copy.
