# PolStock Task 4: Claim-Scoped Source Conflict Execution Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Prevent unrelated same-ticker stories from being marked as contradictory coverage while preserving real opposite-direction conflict penalties for the same claim thread.

**Architecture:** Keep the change tightly scoped to `backend/main.py` and `tests/test_app.py`. Use TDD at the refresh-payload seam: write regressions that compare same-ticker/different-claim coverage against same-ticker/same-claim opposite-direction coverage, then minimally narrow the grouping key inside `apply_source_conflicts_to_events(...)` without changing the existing penalty math.

**Tech Stack:** Python 3.11, FastAPI, pytest, static dashboard payload generation in `backend/main.py`.

---

## Current code anchors
- `backend/main.py`
  - `apply_source_conflicts_to_events(...)`
  - `build_refresh_payload(...)`
  - `group_articles_into_threads(...)` (thread semantics reference)
- `tests/test_app.py`
  - `test_source_conflict_flags_opposite_direction_coverage_and_downgrades_confidence`
  - existing thread fixtures / direct mention fixtures

## Acceptance criteria
1. Two same-ticker stories with opposite direction but different claim/thread identity do **not** get `source_conflict=True`.
2. Two same-ticker stories with opposite direction and the same claim/thread identity **do** still get conflict penalties.
3. Existing source-conflict/thread regressions continue to pass.
4. Commit remains scoped to `backend/main.py`, `tests/test_app.py`, and this task plan file if committed.

---

## Task 4.1: Add same-ticker different-claim regression

**Objective:** Prove the current ticker-only grouping is too broad.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write failing test**
Add:
- `test_source_conflict_ignores_same_ticker_different_claims`

Build a payload with two `ANTM` stories that have opposite direction but different claim identity, preferring real thread/claim fields if the fixtures already expose them. Assert:
- the direct story relationship remains `source_conflict is False`
- `source_conflict_penalty == 1.0`
- warnings do not gain the conflict message from this pair alone

**Step 2: Run RED test**
```bash
pytest tests/test_app.py::test_source_conflict_ignores_same_ticker_different_claims -v
```
Expected: FAIL because current code groups only by ticker.

---

## Task 4.2: Add same-claim opposite-direction regression

**Objective:** Preserve legitimate conflict detection.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write passing/failing guard test**
Add:
- `test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction`

Use two `ANTM` stories with opposite direction that deliberately share a claim/thread identity. Assert:
- `source_conflict is True`
- `source_conflict_count >= 1`
- `source_conflict_penalty < 1.0`
- stock summary still surfaces the conflict

**Step 2: Run both targeted tests**
```bash
pytest tests/test_app.py::test_source_conflict_ignores_same_ticker_different_claims tests/test_app.py::test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction -v
```
Expected: first FAILS before implementation; second may already PASS or also FAIL depending on fixture shape.

---

## Task 4.3: Narrow conflict grouping to claim scope

**Objective:** Change only the grouping seam, not the penalty formula.

**Files:**
- Modify: `backend/main.py`

**Step 1: Implement minimal helper/logic**
Inside `apply_source_conflicts_to_events(...)`, change the grouping key from ticker-only to a compound scope that prefers:
1. normalized ticker
2. `thread_id` when present
3. else `duplicate_group_id` when present
4. else `claim_signature` when present
5. else a conservative fallback (ticker-only)

If needed, add a tiny local helper for extracting the conflict-scope key from an event/relationship pair.

**Step 2: Keep unchanged**
Do **not** alter:
- conflict penalty math
- warning strings
- confidence label thresholds

---

## Task 4.4: Verify targeted and nearby regressions

**Objective:** Prove the fix is narrow and preserves surrounding behavior.

**Files:**
- Test: `tests/test_app.py`

**Step 1: Re-run targeted tests**
```bash
pytest tests/test_app.py::test_source_conflict_ignores_same_ticker_different_claims tests/test_app.py::test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction -v
```
Expected: PASS

**Step 2: Run nearby coverage**
```bash
pytest tests/test_app.py -k "source_conflict or thread or contradiction" -v
```
Expected: PASS

**Step 3: Run full file**
```bash
pytest tests/test_app.py -q
```
Expected: PASS

---

## Task 4.5: Commit and push

**Objective:** Land only Task 4 scope.

**Files:**
- Stage: `backend/main.py`
- Stage: `tests/test_app.py`
- Optional stage if desired now: `docs/plans/2026-06-02-task-4-claim-scoped-conflicts.md`

**Step 1: Check scope**
```bash
git status --short
```
Do not include unrelated `watchlist.json`.

**Step 2: Commit**
```bash
git add backend/main.py tests/test_app.py docs/plans/2026-06-02-task-4-claim-scoped-conflicts.md
git commit -m "feat: scope source conflicts by claim"
```

**Step 3: Push**
```bash
git push origin main
```

---

## Notes
- Prefer existing fixtures/helpers over inventing large new fixtures.
- If thread metadata is easier to control than claim signatures, use thread identity as the primary regression lever.
- If a fallback path must remain ticker-only due to missing metadata, keep the tests focused on the richer payload shape where claim/thread identity exists.
