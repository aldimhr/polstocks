# PolStock Task 5: Persistent Outcome-Based Source Reliability Calibration

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Persist small, bounded validation outcome history by canonical source identity so repeated confirmed/rejected outcomes modestly influence future source confidence without overpowering curated registry quality.

**Architecture:** Keep the existing curated registry trust/freshness scoring as the primary base signal. Add a tiny JSON history store keyed by canonical source identity (`canonical_domain` preferred, fallback to canonical name), compute a narrow historical multiplier from rolling outcomes, and apply it alongside the current-refresh validation multiplier during payload assembly. Expose the historical adjustment transparently on relationships/stocks so consumers can inspect why confidence moved.

**Tech Stack:** Python backend in `backend/main.py`, local JSON data store under `data/`, pytest regressions in `tests/test_app.py`.

---

### Task 5.1: Add failing regressions for bounded persistent calibration

**Objective:** Prove repeated outcomes from the same source influence later refreshes, but only within a modest bound and without replacing registry trust as the primary signal.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**
Add regressions such as:
- `test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds`
- `test_repeated_rejected_outcomes_lower_source_reliability_within_bounds`
- `test_registry_trust_remains_the_base_signal`

Use `tmp_path` + `monkeypatch` to redirect a new history file constant, e.g. `SOURCE_OUTCOME_HISTORY_FILE`, to a temp JSON file.

Test shape:
- run one refresh with a confirmed validation series
- run a second refresh for the same source with a flat/neutral series
- assert the second refresh still receives a modest positive historical lift via new payload fields like:
  - `historical_reliability_multiplier`
  - `historical_outcome_sample_size`
- mirror this for rejected history lowering later confidence
- compare a registry-backed strong source versus a weak fallback source to assert registry trust remains primary even if the weak source has good history

**Step 2: Run tests to verify failure**
Run:
```bash
pytest tests/test_app.py::test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds tests/test_app.py::test_repeated_rejected_outcomes_lower_source_reliability_within_bounds tests/test_app.py::test_registry_trust_remains_the_base_signal -v
```
Expected: FAIL because no persistent store or historical payload fields exist yet.

---

### Task 5.2: Implement minimal persistent source-outcome history

**Objective:** Add a small bounded store and narrow historical multiplier.

**Files:**
- Modify: `backend/main.py`
- Create: `data/source_outcome_history.json`

**Step 1: Add storage helpers**
Add constants/helpers:
- `SOURCE_OUTCOME_HISTORY_FILE = PROJECT_ROOT / "data/source_outcome_history.json"`
- loader/saver that tolerate missing/invalid files
- `source_history_key(...)` that prefers `canonical_domain`, then canonical name
- rolling aggregate/update helpers for meaningful outcomes only

Prefer storing compact aggregates, e.g. sample count + weighted score, rather than raw event logs.

**Step 2: Compute bounded multiplier**
Add a helper that derives a narrow historical multiplier from the stored aggregate, clamped to something like `0.9..1.1`.

Rules:
- `confirmed` adds modest positive weight
- `rejected` adds modest negative weight
- `predicted_only` optional low positive/near-neutral weight
- `unvalidated` / `insufficient_data` should not dominate
- low sample sizes should have small effect

**Step 3: Apply during payload enrichment**
During `build_refresh_payload()` validation enrichment:
- resolve source history key from event/source metadata
- compute historical multiplier before final source-confidence calibration
- expose fields on each relationship and strongest stock projection:
  - `historical_reliability_multiplier`
  - `historical_outcome_sample_size`
  - optional `historical_reliability_score`
- update the persistent store after meaningful validation outcomes are observed

Keep the existing current-refresh validation multiplier intact.

---

### Task 5.3: Verify targeted and broader regressions

**Objective:** Confirm persistence works and existing validation/source-confidence behavior still passes.

**Files:**
- Modify: `tests/test_app.py` only if fixture tuning is needed

**Step 1: Re-run targeted tests**
Run:
```bash
pytest tests/test_app.py::test_repeated_confirmed_outcomes_raise_source_reliability_within_bounds tests/test_app.py::test_repeated_rejected_outcomes_lower_source_reliability_within_bounds tests/test_app.py::test_registry_trust_remains_the_base_signal -v
```
Expected: PASS

**Step 2: Run nearby validation/reliability regressions**
Run:
```bash
pytest tests/test_app.py -k "validation or source_confidence or reliability" -v
```
Expected: PASS

**Step 3: Run full file**
Run:
```bash
pytest tests/test_app.py -q
```
Expected: PASS

---

### Task 5.4: Commit scoped changes only

**Objective:** Land Task 5 without mixing unrelated working-tree noise.

**Files to stage only:**
- `backend/main.py`
- `data/source_outcome_history.json`
- `tests/test_app.py`
- `docs/plans/2026-06-02-task-5-persistent-source-reliability.md`

**Step 1: Review status/diff**
Run:
```bash
git status --short
git diff -- backend/main.py data/source_outcome_history.json tests/test_app.py docs/plans/2026-06-02-task-5-persistent-source-reliability.md --
```
Expected: only Task 5 files staged; leave unrelated `watchlist.json` / other plan docs untouched.

**Step 2: Commit + push**
Run:
```bash
git add backend/main.py data/source_outcome_history.json tests/test_app.py docs/plans/2026-06-02-task-5-persistent-source-reliability.md
git commit -m "feat: persist source reliability calibration"
git push origin main
```
Expected: commit and push succeed.

---

## Verification checklist
- [ ] persistent source outcome history file exists under `data/`
- [ ] history key prefers canonical domain, fallback canonical name
- [ ] historical multiplier remains narrowly bounded
- [ ] registry trust remains the base signal
- [ ] relationship payload exposes historical calibration fields
- [ ] strongest stock payload mirrors those fields
- [ ] targeted tests pass
- [ ] nearby validation/source-confidence tests pass
- [ ] full `tests/test_app.py` passes
- [ ] commit excludes unrelated working-tree changes
