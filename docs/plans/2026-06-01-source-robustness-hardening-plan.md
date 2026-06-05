# Source Robustness Hardening Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make the source-ingestion and evidence pipeline more robust against false corroboration, false conflicts, opaque fallback provenance, and short-memory trust calibration.

**Architecture:** Keep the current single-backend pipeline centered in `backend/main.py`, but harden it in bounded layers: claim-scoped conflict logic, independent-vs-syndicated corroboration, persistent outcome calibration, and stronger source-health summaries. Prefer additive payload fields and regression-first changes over broad score rewrites.

**Tech Stack:** FastAPI, Python, requests, static `dashboard.html`, pytest.

---

## Audit Summary

### What is already strong
- Registry-backed source normalization exists via `source_profile_resolution(...)` and `source_metadata_for(...)`.
- URL canonicalization and duplicate collapse exist via `canonicalize_article_url(...)` and `merge_duplicate_articles(...)`.
- Freshness and quality scoring already exist via `source_quality_metrics_for_article(...)`.
- Corroboration and conflict cues already flow into relationships and the dashboard payload.
- Refresh payload already exposes structured source diagnostics with:
  - `name`
  - `kind`
  - `status`
  - `warning`
  - `article_count`
  - `used_registry_profile`
  - `resolution_method`
  - `date_enrichment_attempted`
  - `date_enrichment_success_count`
  - `date_fallback_count`
- Outcome-based calibration now exists for the current refresh via `calibrate_source_confidence_from_validation(...)`.
- Tests already cover core provenance, freshness, conflict, validation, and payload-shape regressions in `tests/test_app.py`.

### Highest-priority gaps still open
1. **Conflict detection is still ticker-scoped, not claim-scoped.**
   - In `backend/main.py`, `apply_source_conflicts_to_events(...)` still groups by ticker only.
   - Result: unrelated positive and negative stories about the same ticker can be downgraded as if they contradicted each other.
   - This is the highest-risk scoring bug because it can create false conflict penalties on valid coverage.

2. **Corroboration still conflates independent reporting with syndicated or mirrored coverage.**
   - `source_corroboration_metrics_for_article(...)` derives corroboration from `source_names`, `source_urls`, domains, and duplicate count.
   - There is still no first-class distinction between:
     - raw coverage count
     - independent coverage count
     - syndicated/mirrored coverage count
   - Result: multiple outlets carrying the same underlying wire/report can still look more independent than they really are.

3. **Outcome calibration is not persistent across refreshes.**
   - Current validation calibration only affects the current payload.
   - There is no stored bounded history keyed by canonical source/domain.
   - Result: repeated confirmed or repeatedly weak/rejected sources do not modestly refine future trust.

4. **The synthetic diagnostics fallback path is shallow.**
   - `summarize_source_diagnostics_from_articles(...)` returns minimal inferred diagnostics when a fetcher only returns `(articles, warnings)`.
   - It always reports `status: ok` for grouped articles and zeros out enrichment counters.
   - Result: local tests and alternate fetchers can look healthier than the full live path.

5. **Batch-level robustness summary metrics are missing.**
   - The payload has per-source diagnostics, but not a compact batch summary such as:
     - fallback source count
     - conflicted relationship count
     - independent vs syndicated corroboration totals
     - HTML date fallback rate
   - Result: the dashboard/API consumer must infer robustness by scanning many records.

### Lower-priority or already-partially-addressed gaps
- Registry/fallback provenance visibility is mostly solved in payloads.
- HTML date-enrichment observability exists on the live fetch path.
- Dashboard surfacing can still improve, but the bigger remaining risk is scoring correctness rather than presentation.

---

## Recommended implementation order
1. Claim-scoped conflict detection
2. Independent-vs-syndicated corroboration
3. Batch robustness summary metrics
4. Strengthen synthetic source diagnostics fallback path
5. Persistent outcome-based source reliability calibration
6. Final dashboard cue pass after backend semantics stabilize

---

## Task 1: Make conflict detection claim-scoped instead of ticker-scoped

**Objective:** Prevent unrelated same-ticker stories from being marked as contradictory coverage.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Write failing tests**
Add two regressions:
- `test_source_conflict_ignores_same_ticker_different_claims`
- `test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction`

Use two ANTM stories with opposite direction but different claim signatures / thread shapes for the first test, and same-claim opposite-direction stories for the second.

**Step 2: Run tests to verify failure**
Run:
```bash
pytest tests/test_app.py::test_source_conflict_ignores_same_ticker_different_claims tests/test_app.py::test_source_conflict_still_flags_same_ticker_same_claim_opposite_direction -v
```
Expected: first test FAILS on current ticker-only grouping.

**Step 3: Write minimal implementation**
In `backend/main.py`:
- Extend the grouping key in `apply_source_conflicts_to_events(...)` from ticker-only to a compound key such as:
  - ticker
  - `thread_id` when present
  - else `duplicate_group_id`
  - else `claim_signature`
  - optionally category family / primary policy channel if needed for tie-breaking
- Keep current penalty math unchanged for now.

**Step 4: Run tests to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run nearby regressions**
Run:
```bash
pytest tests/test_app.py -k "source_conflict or thread or contradiction" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: scope source conflicts by claim"
```

---

## Task 2: Separate independent corroboration from syndicated coverage

**Objective:** Stop mirrored or wire-derived coverage from boosting confidence like independent reporting.

**Files:**
- Modify: `backend/main.py`
- Modify: `source_registry.json`
- Test: `tests/test_app.py`

**Step 1: Write failing tests**
Add regressions:
- `test_syndicated_coverage_does_not_count_as_independent_corroboration`
- `test_truly_independent_domains_still_raise_corroboration`

Assert new payload/relationship fields such as:
- `raw_coverage_count`
- `independent_coverage_count`
- `syndicated_coverage_count`
- `independent_domain_count`

Also assert a syndicated pair produces a smaller corroboration boost than a truly independent pair.

**Step 2: Run tests to verify failure**
Run:
```bash
pytest tests/test_app.py::test_syndicated_coverage_does_not_count_as_independent_corroboration tests/test_app.py::test_truly_independent_domains_still_raise_corroboration -v
```
Expected: FAIL because the new fields and semantics do not exist yet.

**Step 3: Write minimal implementation**
- Add optional registry metadata such as `syndication_group` or `primary_wire` in `source_registry.json`.
- In `backend/main.py`, enhance corroboration logic to:
  - count every source in `raw_coverage_count`
  - collapse sources sharing a syndication group into one independent corroborator
  - retain `syndicated_coverage_count` for transparency
- Keep current labels, but make them depend on independent corroboration rather than raw counts.

**Step 4: Run tests to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run nearby regressions**
Run:
```bash
pytest tests/test_app.py -k "corroboration or duplicate or source_quality" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add backend/main.py source_registry.json tests/test_app.py
git commit -m "feat: separate independent and syndicated corroboration"
```

---

## Task 3: Add batch robustness summary metrics to the payload

**Objective:** Give the dashboard/API one compact place to inspect overall source-health quality for the refresh.

**Files:**
- Modify: `backend/main.py`
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Step 1: Write failing test**
Add a regression like `test_refresh_payload_exposes_batch_robustness_summary` asserting a new payload section such as `payload["source_health_summary"]` includes bounded summary metrics, e.g.:
- `source_count`
- `fallback_source_count`
- `errored_source_count`
- `empty_source_count`
- `date_fallback_count`
- `date_enrichment_success_count`
- `conflicted_relationship_count`
- `independent_corroborated_relationship_count`
- `syndicated_coverage_count`

**Step 2: Run test to verify failure**
Run:
```bash
pytest tests/test_app.py::test_refresh_payload_exposes_batch_robustness_summary -v
```
Expected: FAIL

**Step 3: Write minimal implementation**
- In `backend/main.py`, derive the summary from `sources`, `events`, and relationship-level corroboration/conflict fields.
- Expose it in the refresh/dashboard payload.
- In `dashboard.html`, add a minimal robustness summary strip or diagnostics section using the new summary object.

**Step 4: Run test to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run nearby regressions**
Run:
```bash
pytest tests/test_app.py -k "source_fetch_diagnostics or dashboard or refresh_payload" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add backend/main.py dashboard.html tests/test_app.py
git commit -m "feat: expose batch source health summary"
```

---

## Task 4: Strengthen the synthetic source-diagnostics fallback path

**Objective:** Make non-live or simplified fetchers produce diagnostics that are less misleading and closer to the real fetch path.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Write failing test**
Add a regression like `test_summarized_source_diagnostics_preserve_resolution_and_article_signals` asserting `summarize_source_diagnostics_from_articles(...)` or the payload derived from a 2-tuple fetcher:
- preserves `resolution_method`
- preserves `used_registry_profile`
- infers `status` more carefully
- does not pretend HTML enrichment stats are known when they are not

Prefer explicit `None`/`unknown` semantics over fake zeros if the data is unavailable.

**Step 2: Run test to verify failure**
Run:
```bash
pytest tests/test_app.py::test_summarized_source_diagnostics_preserve_resolution_and_article_signals -v
```
Expected: FAIL

**Step 3: Write minimal implementation**
- Adjust `summarize_source_diagnostics_from_articles(...)` so it can emit more honest fallback diagnostics.
- Consider fields like:
  - `status: inferred_ok`
  - `date_enrichment_attempted: null`
  - `date_enrichment_success_count: null`
  - `date_fallback_count: null`
- Keep the live fetch path unchanged.

**Step 4: Run test to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run nearby regressions**
Run:
```bash
pytest tests/test_app.py -k "source_fetch_diagnostics or source_metadata or source_registry" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: improve fallback source diagnostics"
```

---

## Task 5: Add persistent outcome-based source reliability calibration

**Objective:** Let repeated market-validation outcomes modestly inform future source trust without overpowering curated registry quality.

**Files:**
- Modify: `backend/main.py`
- Create: `data/source_outcome_history.json` or similar small local store
- Test: `tests/test_app.py`

**Step 1: Write failing tests**
Add regressions such as:
- `test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds`
- `test_repeated_rejected_outcomes_lower_source_reliability_within_bounds`
- `test_registry_trust_remains_the_base_signal`

Assert bounded behavior, for example:
- historical multiplier stays in a narrow range such as `0.9` to `1.1`
- current validation still matters, but base registry quality remains primary

**Step 2: Run tests to verify failure**
Run:
```bash
pytest tests/test_app.py::test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds tests/test_app.py::test_repeated_rejected_outcomes_lower_source_reliability_within_bounds -v
```
Expected: FAIL

**Step 3: Write minimal implementation**
- Store bounded rolling aggregates keyed by canonical source identity (`canonical_domain` preferred).
- Update history only when validation is meaningful (`confirmed` / `rejected`, maybe `predicted_only` with low weight).
- Apply the historical multiplier before or alongside the current-refresh validation multiplier.
- Keep the multiplier modest and transparent in payloads, e.g.:
  - `historical_reliability_multiplier`
  - `historical_outcome_sample_size`

**Step 4: Run tests to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run nearby regressions**
Run:
```bash
pytest tests/test_app.py -k "validation or source_confidence or reliability" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add backend/main.py data/source_outcome_history.json tests/test_app.py
git commit -m "feat: persist source reliability calibration"
```

---

## Task 6: Final dashboard cue pass after backend semantics stabilize

**Objective:** Make the new robustness semantics obvious to users without overloading the main UI.

**Files:**
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Step 1: Write failing test**
Add a dashboard regression asserting the UI contains hooks/labels for:
- claim-scoped conflicts
- independent vs syndicated corroboration
- historical source reliability calibration
- source health summary metrics

**Step 2: Run test to verify failure**
Run:
```bash
pytest tests/test_app.py::test_dashboard_contains_source_robustness_health_hooks -v
```
Expected: FAIL

**Step 3: Write minimal implementation**
- Add lightweight badges/cues for the new fields.
- Keep severity obvious:
  - conflict = warning/downgrade
  - fallback provenance = caution
  - syndicated corroboration = informational, not strength
  - historical calibration = subtle trust nudge, not headline signal

**Step 4: Run test to verify pass**
Run the same targeted pytest command.
Expected: PASS

**Step 5: Run dashboard regressions**
Run:
```bash
pytest tests/test_app.py -k "dashboard" -v
```
Expected: PASS

**Step 6: Commit**
```bash
git add dashboard.html tests/test_app.py
git commit -m "feat: surface source robustness health cues"
```

---

## Full verification after all tasks
Run:
```bash
pytest tests/test_app.py -q
```
Expected: PASS

If additional test files are added later, finish with:
```bash
pytest -q
```

---

## Recommended first execution slice
If we only do one batch next, do this exact order:
1. Task 1 — claim-scoped conflicts
2. Task 2 — independent vs syndicated corroboration
3. Task 3 — batch robustness summary

That gives the biggest correctness win before spending time on persistent calibration.

---

## Notes for implementation
- Preserve unrelated working tree churn out of each commit (`watchlist.json`, timestamp-only files, ad hoc notes).
- Keep new fields additive; do not remove old ones until the dashboard is updated.
- Prefer `claim_signature` / `thread_id` reuse over inventing a second thread model.
- Keep trust adjustments bounded and inspectable in payloads.
- Do not let persistent calibration overshadow registry trust, freshness, and explicit evidence quality.
