# PolStock Task 3: Batch Robustness Summary Metrics Execution Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add one compact top-level payload block that summarizes overall source-health and evidence-robustness quality for a refresh batch.

**Architecture:** Keep the implementation additive and backend-first. Derive the batch summary from existing payload inputs that already exist at refresh time — `sources`, formatted `events`, and their `stock_relationships` — rather than rewriting scoring code. Expose a single top-level summary object in `build_refresh_payload()` so dashboard/API consumers can read overall robustness without re-deriving it client-side.

**Tech Stack:** Python backend in `backend/main.py`, pytest regressions in `tests/test_app.py`, markdown plan in `docs/plans/`.

---

## Task breakdown

### Task 3.1: Add payload regression for compact robustness summary

**Objective:** Prove the refresh payload exposes a bounded summary block with both source-diagnostic and relationship-level robustness counts.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write failing test**
Add a regression near the existing diagnostics payload tests:
- `test_refresh_payload_exposes_batch_robustness_summary`

Assert `payload["source_health_summary"]` exists and includes metrics derived from existing data, including:
- `source_count`
- `ok_source_count`
- `fallback_source_count`
- `errored_source_count`
- `empty_source_count`
- `warning_source_count`
- `registry_backed_source_count`
- `date_enrichment_success_count`
- `date_fallback_count`
- `displayed_event_count`
- `conflicted_relationship_count`
- `independent_corroborated_relationship_count`
- `weak_single_source_relationship_count`
- `syndicated_coverage_count`
- `stale_event_count`

Use a custom `news_fetcher` that returns 3-tuple diagnostics with a mix of:
- one registry-backed/ok source with date enrichment success,
- one fallback/errored source,
- one empty/warning source.

Use article fixtures that produce:
- at least one conflicting relationship,
- at least one independently corroborated relationship,
- at least one mirrored/syndicated coverage increment.

**Step 2: Run test to verify failure**
Run:
```bash
pytest tests/test_app.py::test_refresh_payload_exposes_batch_robustness_summary -v
```
Expected: FAIL because `source_health_summary` does not exist yet.

---

### Task 3.2: Implement minimal summary aggregation

**Objective:** Compute the compact batch summary from existing diagnostics and relationships without changing existing payload fields.

**Files:**
- Modify: `backend/main.py`

**Step 1: Add helper(s)**
Add a helper near the diagnostics section, for example:
- `build_source_health_summary(sources: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]`

Aggregate from `sources`:
- counts by `status` (`ok`, `error`, `empty`)
- count of entries with `used_registry_profile == True`
- count of entries whose `resolution_method` is not registry-backed (`inferred_fallback` / non-registry methods)
- count of entries with any non-empty `warning`
- summed `date_enrichment_success_count`
- summed `date_fallback_count`

Aggregate from `events[*].stock_relationships[*]`:
- total relationships
- count with `source_conflict == True`
- count with `corroboration_label == "independently_corroborated"`
- count with `corroboration_label == "single_weak_source"`
- summed `syndicated_coverage_count`

Aggregate from `events`:
- displayed event count
- stale event count via event/article `coverage_warning == "stale_coverage"`
- thin event count via `coverage_warning == "thin_source_coverage"`
- duplicated event count via `coverage_warning == "duplicated_coverage"`

Keep the summary additive and bounded. Do not remove or rename existing payload fields.

**Step 2: Wire it into payload assembly**
In `build_refresh_payload()`:
- compute `sources` first as today
- compute `source_health_summary = build_source_health_summary(sources, formatted_events)`
- add it to the top-level payload as `"source_health_summary": source_health_summary`

**Step 3: Keep API compatibility**
Do not change helper return shapes used elsewhere. Only add the new top-level block.

---

### Task 3.3: Verify targeted and broader regressions

**Objective:** Confirm the new summary is correct without breaking existing payload/diagnostic behavior.

**Files:**
- Modify: `tests/test_app.py` (only if the first pass exposes fixture issues)

**Step 1: Re-run targeted test**
Run:
```bash
pytest tests/test_app.py::test_refresh_payload_exposes_batch_robustness_summary -v
```
Expected: PASS

**Step 2: Run nearby payload regressions**
Run:
```bash
pytest tests/test_app.py -k "source_fetch_diagnostics or refresh_payload or corroboration or source_conflict" -v
```
Expected: PASS

**Step 3: Run full file**
Run:
```bash
pytest tests/test_app.py -q
```
Expected: PASS

---

### Task 3.4: Commit scoped files only

**Objective:** Land the batch robustness summary cleanly without mixing unrelated working-tree noise.

**Files:**
- Stage only:
  - `backend/main.py`
  - `tests/test_app.py`
  - `docs/plans/2026-06-02-task-3-batch-robustness-summary.md`

**Step 1: Review diff and status**
Run:
```bash
git status --short
git diff -- backend/main.py tests/test_app.py docs/plans/2026-06-02-task-3-batch-robustness-summary.md --
```
Expected: only Task 3 files staged; leave unrelated `watchlist.json` and other plan-doc noise out.

**Step 2: Commit**
Run:
```bash
git add backend/main.py tests/test_app.py docs/plans/2026-06-02-task-3-batch-robustness-summary.md
git commit -m "feat: add batch source health summary"
git push origin main
```
Expected: commit and push succeed.

---

## Verification checklist
- [ ] `source_health_summary` exists at top level of refresh payload
- [ ] summary uses existing diagnostics + relationships rather than re-implementing scoring
- [ ] summary counts are bounded integers and remain additive
- [ ] existing `sources`, `events`, `stocks`, and `reasoning_summary` payload sections remain intact
- [ ] targeted test passes
- [ ] nearby payload regressions pass
- [ ] full `tests/test_app.py` passes
- [ ] commit excludes unrelated working-tree changes
