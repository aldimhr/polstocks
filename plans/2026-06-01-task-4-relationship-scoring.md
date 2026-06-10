# PolStock Task 4: Relationship Scoring Downgrade Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make source quality materially affect ticker relationship scoring so weak, sparse, or commentary-heavy coverage cannot produce confident stock links.

**Architecture:** Reuse the existing source registry, freshness scoring, and deduped article pipeline already in `backend/main.py`. Task 4 should stay additive: enrich the relationship-building path with source-confidence inputs, convert low-quality coverage into explicit low-confidence states, and keep official / regulator / company sources strong when they are direct and fresh. The API should continue returning relationships, but with clearer confidence labeling and weaker evidence should be downgraded rather than hidden.

**Tech Stack:** Python 3.11, FastAPI, static JSON/config files, pytest, existing article analysis pipeline.

---

## Current code anchors

These are the relevant integration points in the current repo:

- `backend/main.py`
  - `build_stock_relationships(...)`
  - `relationship_type_for_link(...)`
  - `analyze_article(...)`
  - `compute_ticker_score(...)`
  - `evidence_quality_score(...)`
  - `source_quality_metrics_for_article(...)`
  - `source_quality_score_for_profile(...)`
  - `source_freshness_score(...)`
- `tests/test_app.py`
- `ARCHITECTURE.md`
- `SPEC.md`

Do **not** add a database or a new service. Keep the logic inside the current backend pipeline.

---

## Relationship robustness rules

1. **Source quality must influence relationship confidence.**
   - Direct official sources should remain strong.
   - Weak opinion-only coverage should not inflate ticker certainty.

2. **Redundancy should help, but only when independent.**
   - Duplicate republishes from the same source family should not create stronger stock links.
   - Independent corroboration should improve confidence.

3. **Low-coverage situations must be labeled explicitly.**
   - When evidence is thin or indirect, the relationship should read as `low_confidence`, `predicted_only`, or `insufficient_data` instead of looking confirmed.

4. **Relationship type should stay honest.**
   - Direct alias hits can remain `direct`.
   - Indirect thematic matches should stay `indirect` or downgrade to a lower-confidence state when evidence is weak.

5. **UI/API consumers should get the truth, not a forced certainty.**
   - If confidence is weak, the payload should say so with explicit fields.

---

## Proposed payload additions

For each stock relationship, expose derived fields such as:

- `source_confidence`
- `evidence_strength`
- `relationship_confidence`
- `confidence_label`
- `relationship_type`
- `source_quality_score`
- `source_freshness_score`
- `duplicate_count`
- `coverage_warning`

These fields should be derived from existing article metadata when possible, not duplicated manually.

---

## Task 4.1: Add a source-aware relationship scoring helper

**Objective:** Factor source quality into the relationship score calculation in one place so the rest of the pipeline stays simple.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Write a failing regression test**

Add a test that compares two otherwise similar articles:
- one with a strong official source and direct company mention
- one with weak commentary coverage and the same rough topical language

The test should assert that the official source produces a higher relationship confidence and a stronger evidence label.

**Step 2: Run the test to confirm the current behavior is insufficient**

Run:
```bash
pytest tests/test_app.py -k "relationship and confidence" -v
```
Expected: at least one test fails or the new test is absent before implementation.

**Step 3: Implement the minimal helper**

In `backend/main.py`, update `build_stock_relationships(...)` so relationship scoring includes source-derived inputs, for example:

- `source_quality_score`
- `source_freshness_score`
- `duplicate_count`
- `coverage_warning`

Use those inputs to derive:

- `source_confidence`
- `evidence_strength`
- `relationship_confidence`

Keep the formula conservative. A weak article should not become strong just because it mentions a company name.

**Step 4: Re-run the focused test**

Run:
```bash
pytest tests/test_app.py -k "relationship and confidence" -v
```
Expected: PASS.

**Step 5: Commit if this task is being executed independently**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: make relationship scoring source-aware"
```

---

## Task 4.2: Add explicit low-confidence fallback labels

**Objective:** Make thin evidence obvious instead of pretending the relationship is confirmed.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Add a failing test for weak coverage**

Create a fixture where the article is politically relevant but only weakly tied to a ticker, such as:
- opinion-only language
- generic sector chatter
- no direct alias hit

Assert the returned relationship is labeled with one of:
- `predicted_only`
- `insufficient_data`
- `low_confidence`

**Step 2: Implement the fallback logic**

Extend `relationship_type_for_link(...)` or the scoring path so weak coverage can downgrade to an explicit low-confidence label.

Suggested behavior:
- direct official coverage can stay `direct`
- indirect but strong coverage can stay `indirect`
- thin evidence should map to a low-confidence label instead of a normal-looking relationship

**Step 3: Verify the new label behavior**

Run:
```bash
pytest tests/test_app.py -k "low confidence or insufficient data or predicted" -v
```
Expected: PASS.

---

## Task 4.3: Keep official sources strong when direct

**Objective:** Ensure source-quality penalties do not overcorrect and accidentally weaken direct official announcements.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Add a direct-official-source regression test**

Use a fixture like a `Setkab` or `OJK` article that directly names a company or policy path.

Assert that:
- the relationship remains high-confidence
- the relationship is not downgraded just because there is no second article
- the relationship type remains `direct`

**Step 2: Calibrate the scoring formula if needed**

If the test fails, adjust the weights in `build_stock_relationships(...)` and/or `compute_ticker_score(...)` so direct official coverage keeps a strong score.

Do not add special-case exceptions unless the test proves the general formula cannot be tuned safely.

**Step 3: Verify**

Run:
```bash
pytest tests/test_app.py -k "official or direct" -v
```
Expected: PASS.

---

## Task 4.4: Surface the new fields in article/relationship payloads

**Objective:** Make the stronger/weakness distinctions visible to downstream consumers without changing the API shape more than necessary.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Add payload assertions**

Add tests that confirm the relationship payload includes the new metadata fields:
- `source_confidence`
- `evidence_strength`
- `relationship_confidence`
- `confidence_label`
- `coverage_warning`

**Step 2: Populate the fields in the analyzer output**

Update `analyze_article(...)` and `build_stock_relationships(...)` to include the new values in the returned article/event payloads.

**Step 3: Verify payload shape**

Run:
```bash
pytest tests/test_app.py -k "payload or relationship or confidence" -v
```
Expected: PASS.

---

## Task 4.5: Update architecture docs

**Objective:** Keep the docs aligned with the scoring model so future edits do not drift from the implementation.

**Files:**
- Modify: `ARCHITECTURE.md`
- Optionally modify: `SPEC.md` if the acceptance contract changes

**Step 1: Document the scoring change**

Add a short section describing that ticker relationship confidence now incorporates:
- source tier/quality
- freshness
- redundancy
- explicit low-confidence fallback labels

**Step 2: Verify docs are consistent**

Read the updated section back and confirm it matches the implemented behavior.

---

## Verification command

When the task is implemented, run:

```bash
pytest tests/test_app.py -k "relationship or confidence or evidence or official or direct or low confidence or insufficient data or predicted" -v
```

Expected: all relevant tests pass.

Then run the full suite if the focused run passes:

```bash
pytest tests/test_app.py -v
```

---

## Commit plan

If Task 4 is completed in one pass, commit the code and doc changes together:

```bash
git add backend/main.py tests/test_app.py ARCHITECTURE.md SPEC.md
git commit -m "feat: downgrade weak-source relationship scoring"
```

---

## Acceptance criteria

Task 4 is done when:

- Direct official coverage still produces strong relationships.
- Weak opinion-only coverage cannot create confident ticker links.
- Low-source situations are labeled explicitly instead of hidden.
- Relationship payloads expose source/confidence metadata.
- Regression tests prove the above.

---

## Notes

- Keep the implementation additive.
- Prefer a single scoring path over scattered heuristics.
- Do not let duplicate republishes inflate confidence.
- If a claim is weak, say so.
- If coverage is sparse, label it.
