# PolStock Source Robustness Next-Phase Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Improve source robustness beyond the current registry/scoring baseline by deduplicating mirrored coverage, making freshness and corroboration drive confidence more explicitly, and surfacing conflict/quality signals in the dashboard.

**Architecture:** Keep the current pipeline shape. Extend the existing source registry and article/relationship metadata instead of adding a new subsystem. First tighten source identity and duplicate collapse, then introduce corroboration and conflict logic, then calibrate freshness and source reliability from observed outcomes, and finally expose the new signals in the dashboard and docs.

**Tech Stack:** Python backend (`backend/main.py`), JSON source registry (`source_registry.json`), dashboard UI (`dashboard.html`), regression tests (`tests/test_app.py`), docs (`SPEC.md`, `ARCHITECTURE.md`, `README.md`).

---

## Task 1: Add canonical article identity and mirror deduplication

**Objective:** Prevent mirrored or near-duplicate articles from inflating coverage, confidence, or relationship counts.

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Add regression tests that prove:
- the same story from mirrored domains collapses to one canonical item
- title/url duplicates do not multiply relationship confidence
- deduped items preserve the highest-trust source metadata

**Step 2: Implement canonical identity helpers**

Add helpers in `backend/main.py` that derive a canonical article key from:
- normalized URL
- normalized headline
- source domain / canonical domain
- publication window

Use the key during ingestion so duplicates are merged before scoring.

**Step 3: Preserve best provenance on merge**

When two records merge, keep:
- the best `source_profile`
- the highest `source_quality_score`
- the strongest evidence text snippet
- a `duplicate_count` or equivalent marker

**Step 4: Re-run tests**

Run: `pytest tests/test_app.py -k "dedupe or canonical or duplicate" -v`

Expected: new dedupe tests pass.

**Step 5: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 6: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: dedupe mirrored coverage"
```

---

## Task 2: Make corroboration explicit for weak and medium sources

**Objective:** Require stronger corroboration before weak sources can create high-confidence relationships.

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Add tests that prove:
- a single weak commentary-style article cannot create a strong relationship
- two or more independent corroborating sources can raise confidence
- official sources remain able to create strong links with less corroboration

**Step 2: Add corroboration scoring helpers**

Extend relationship assembly with a corroboration factor that considers:
- source tier
- duplicate count
- number of independent domains
- agreement across sources

**Step 3: Gate high confidence**

Require high confidence to come from either:
- tier-1 / official coverage, or
- repeated independent corroboration

Weak sources should remain visible but downgraded.

**Step 4: Re-run focused tests**

Run: `pytest tests/test_app.py -k "corroborat or weak_source or official" -v`

Expected: corroboration tests pass.

**Step 5: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 6: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: add corroboration-aware confidence"
```

---

## Task 3: Calibrate freshness decay by source class and validation outcome

**Objective:** Make freshness decay source-aware and let downstream validation outcomes tune the decay behavior over time.

**Files:**
- Modify: `backend/main.py`
- Modify: `source_registry.json`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Add tests that prove:
- official sources decay slower than fast-moving media
- stale items get lower freshness scores than recent items
- validation-confirmed sources do not disappear, but old ones weaken

**Step 2: Refine freshness helper**

Use the existing `freshness_half_life_hours` field as the basis for decay, and ensure the helper:
- respects source tier
- respects source trust weight
- treats stale coverage as weaker, not invalid

**Step 3: Add outcome-aware tuning hooks**

Add a small calibration path so validation outcomes can nudge freshness/quality guidance later without rewriting the pipeline.

**Step 4: Re-run focused tests**

Run: `pytest tests/test_app.py -k "freshness or stale or validation" -v`

Expected: freshness tests pass.

**Step 5: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 6: Commit**

```bash
git add backend/main.py source_registry.json tests/test_app.py
git commit -m "feat: tune source freshness decay"
```

---

## Task 4: Detect and surface source conflicts

**Objective:** Flag articles and relationships when official sources and media coverage disagree.

**Files:**
- Modify: `backend/main.py`
- Modify: `tests/test_app.py`

**Step 1: Write failing tests**

Add tests that prove:
- official-vs-media disagreement produces a conflict label
- conflict does not hide the relationship entirely
- strong conflict lowers relationship confidence and/or validation score

**Step 2: Add conflict detection**

Compute a conflict flag when sources disagree on:
- direction
- policy channel
- timing / enforcement status
- confirmation vs denial

**Step 3: Downgrade, don’t discard**

Keep the item visible, but attach:
- `source_conflict`
- `coverage_warning`
- a lower `confidence_label`

**Step 4: Re-run focused tests**

Run: `pytest tests/test_app.py -k "conflict or disagreement or coverage_warning" -v`

Expected: conflict tests pass.

**Step 5: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 6: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: flag source conflicts"
```

---

## Task 5: Add stronger source-quality badges and conflict cues to the dashboard

**Objective:** Make the robustness signals easy to understand visually.

**Files:**
- Modify: `dashboard.html`
- Modify: `tests/test_app.py`
- Modify: `ARCHITECTURE.md`
- Modify: `SPEC.md`

**Step 1: Write failing UI tests**

Add tests that verify the dashboard HTML/payload includes visible cues for:
- duplicate coverage
- source conflict
- freshness / staleness
- source quality labels

**Step 2: Render badges in the stock and event cards**

Extend the dashboard to show compact badges such as:
- `Official`
- `High trust`
- `Mirrored`
- `Stale`
- `Conflict`
- `Weak coverage`

**Step 3: Keep the UI minimal**

Use the existing card layout and add only small badges/hints, not a new control surface.

**Step 4: Update docs**

Document the new source-quality cues in `SPEC.md` and `ARCHITECTURE.md`.

**Step 5: Re-run focused tests**

Run: `pytest tests/test_app.py -k "dashboard or badge or provenance or conflict" -v`

Expected: UI tests pass.

**Step 6: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 7: Commit**

```bash
git add dashboard.html tests/test_app.py SPEC.md ARCHITECTURE.md
git commit -m "feat: surface source robustness badges"
```

---

## Task 6: Calibrate source reliability from outcomes

**Objective:** Let real validation results nudge source trust over time instead of keeping all source weights static forever.

**Files:**
- Modify: `backend/main.py`
- Modify: `source_registry.json`
- Modify: `tests/test_app.py`
- Modify: `README.md`

**Step 1: Write failing tests**

Add tests that prove:
- consistently confirmed sources are not downgraded
- repeatedly rejected sources lose trust over time
- calibration changes are bounded and reversible

**Step 2: Add a small calibration store or update path**

Keep the mechanism lightweight:
- adjust trust weight slowly
- keep explicit bounds
- preserve manual registry values as a floor/ceiling guardrail

**Step 3: Avoid overfitting**

Calibration must never override official-source precedence or wipe out source class semantics.

**Step 4: Re-run focused tests**

Run: `pytest tests/test_app.py -k "calibrat or trust_weight or confirmed or rejected" -v`

Expected: calibration tests pass.

**Step 5: Run full regression**

Run: `pytest tests/test_app.py -v`

Expected: full suite passes.

**Step 6: Update README**

Add a brief note explaining that source trust is partly static and partly refined by validation outcomes.

**Step 7: Commit**

```bash
git add backend/main.py source_registry.json tests/test_app.py README.md
git commit -m "feat: calibrate source reliability"
```

---

## Suggested execution order

1. Deduplicate mirrored coverage
2. Add corroboration rules
3. Calibrate freshness decay
4. Detect source conflicts
5. Surface the new signals in the dashboard
6. Add reliability calibration from outcomes

---

## Verification rule for every task

Before merging a task:
- run the focused regression subset first
- then run `pytest tests/test_app.py -v`
- inspect the live dashboard if the task changes visible ordering or badges
- remove unrelated timestamp-only churn before committing
- commit each task separately

---

## Success criteria

This phase is complete when:
- mirrored stories do not inflate confidence
- weak sources need corroboration for strong claims
- freshness differs by source class
- source conflicts are visible and downgraded, not hidden
- the dashboard clearly shows source-robustness cues
- tests prove the behavior end to end
