# PolStock Task 2: Independent-vs-Syndicated Corroboration Execution Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Stop mirrored or same-wire coverage from inflating corroboration like independent reporting while preserving stronger boosts for truly independent sources.

**Architecture:** Keep the change tightly scoped to `backend/main.py` and `tests/test_app.py`. Use TDD at two seams: (1) article-level corroboration metrics from merged duplicate coverage, and (2) refresh-payload corroboration multipliers across multiple supporting events. Prefer source-family collapsing over score rewrites: derive a canonical corroboration family key from source metadata/profile, treat raw coverage and independent coverage as separate concepts, and expose additive transparency fields rather than replacing existing payload fields silently.

**Tech Stack:** Python 3.11, FastAPI, pytest, static refresh payload generation in `backend/main.py`.

---

## Current code anchors
- `backend/main.py`
  - `source_corroboration_metrics_for_article(...)`
  - `corroboration_multiplier_for_group(...)`
  - `apply_corroboration_to_events(...)`
  - `source_metadata_for(...)`
  - registry normalization / `duplicate_grouping`
- `tests/test_app.py`
  - `test_source_corroboration_from_independent_sources_raises_relationship_confidence`
  - `test_weak_source_requires_corroboration_to_raise_confidence`

## Acceptance criteria
1. Mirrored coverage from the same underlying source family does **not** count as multiple independent corroborators.
2. Truly independent sources still raise corroboration counts and confidence more than mirrored coverage.
3. Relationship payloads expose separate fields for raw coverage vs independent coverage.
4. Existing corroboration/confidence regressions continue to pass.
5. Commit remains scoped to Task 2 files only.

---

## Task 2.1: Add mirrored-coverage regression

**Objective:** Prove same-family mirrored coverage is overcounted today.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write failing test**
Add:
- `test_mirrored_coverage_does_not_count_as_independent_corroboration`

Use `merge_duplicate_articles(...)` on two same-family mirror articles such as `Antara News` and the registry alias `Antara Terkini`, then analyze the merged article.

Assert new relationship fields such as:
- `raw_coverage_count == 2`
- `independent_coverage_count == 1`
- `syndicated_coverage_count == 1`
- `corroboration_source_count == 1`
- `corroboration_domain_count == 1`

**Step 2: Run RED test**
```bash
pytest tests/test_app.py::test_mirrored_coverage_does_not_count_as_independent_corroboration -v
```
Expected: FAIL because current code counts raw duplicates as independent corroboration.

---

## Task 2.2: Add independent-source guard regression

**Objective:** Preserve stronger corroboration for truly independent outlets.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write second failing/guard test**
Add:
- `test_truly_independent_domains_still_raise_corroboration`

Compare:
- a mirrored-coverage payload using same-family sources
- an independent-coverage payload using distinct source families/domains

Assert the independent case has:
- larger `independent_coverage_count`
- smaller `syndicated_coverage_count`
- larger `corroboration_multiplier`
- higher `relationship_confidence`

**Step 2: Run both targeted tests**
```bash
pytest tests/test_app.py::test_mirrored_coverage_does_not_count_as_independent_corroboration tests/test_app.py::test_truly_independent_domains_still_raise_corroboration -v
```
Expected: at least the mirrored test FAILS before implementation.

---

## Task 2.3: Implement minimal source-family collapsing

**Objective:** Separate raw coverage from independent corroboration without broad score rewrites.

**Files:**
- Modify: `backend/main.py`

**Step 1: Add helper logic**
Introduce a small helper that derives a corroboration-family key from source metadata, preferring:
1. optional `syndication_group` when present in `source_profile`
2. `duplicate_grouping`
3. canonical domain
4. normalized source name/url fallback

**Step 2: Apply it at both corroboration seams**
- In `source_corroboration_metrics_for_article(...)`, compute and expose:
  - `raw_coverage_count`
  - `independent_coverage_count`
  - `syndicated_coverage_count`
  - `independent_domain_count`
- In `corroboration_multiplier_for_group(...)` / `apply_corroboration_to_events(...)`, base the corroboration boost on independent source families/domains rather than raw support count.

**Step 3: Preserve compatibility**
- Keep `corroboration_count` as raw grouped support count for backwards compatibility.
- Keep `corroboration_source_count` mapped to independent corroborators.
- Keep labels/thresholds as close as possible, only switching them to the independent counts.

---

## Task 2.4: Verify targeted and nearby regressions

**Objective:** Prove the fix is narrow and preserves surrounding behavior.

**Files:**
- Test: `tests/test_app.py`

**Step 1: Re-run targeted tests**
```bash
pytest tests/test_app.py::test_mirrored_coverage_does_not_count_as_independent_corroboration tests/test_app.py::test_truly_independent_domains_still_raise_corroboration -v
```
Expected: PASS

**Step 2: Run nearby corroboration coverage**
```bash
pytest tests/test_app.py -k "corroboration or duplicate or source_conflict" -v
```
Expected: PASS

**Step 3: Run full file**
```bash
pytest tests/test_app.py -q
```
Expected: PASS

---

## Task 2.5: Commit and push

**Objective:** Land only Task 2 scope.

**Files:**
- Stage: `backend/main.py`
- Stage: `tests/test_app.py`
- Optional stage: `docs/plans/2026-06-02-task-2-independent-corroboration.md`

**Step 1: Check scope**
```bash
git status --short
```
Do not include unrelated `watchlist.json` or older untracked plan docs.

**Step 2: Commit**
```bash
git add backend/main.py tests/test_app.py docs/plans/2026-06-02-task-2-independent-corroboration.md
git commit -m "feat: separate independent and mirrored corroboration"
```

**Step 3: Push**
```bash
git push origin main
```

---

## Notes
- Prefer existing alias/profile resolution instead of inventing a large syndication registry immediately.
- It is acceptable for the initial fix to support `syndication_group` metadata without requiring new registry entries yet.
- If a payload-level overwrite in `apply_corroboration_to_events(...)` stomps article-level fields, fix that seam rather than broadening the scope elsewhere.
