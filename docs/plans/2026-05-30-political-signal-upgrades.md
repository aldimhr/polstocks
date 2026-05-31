# Political Signal Precision Upgrade Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Improve the current `politics_stock_mapper` so article-to-stock links are more precise, more explainable, and better validated against real market behavior.

**Architecture:** Keep the current single-process FastAPI app and flat-file data model, but insert three stronger layers into the existing pipeline: (1) a richer political relevance and event-stage classifier before scoring, (2) a structured transmission-path and direction model between policy themes and company knowledge, and (3) an optional market-reaction confirmation layer after text-based prediction. Implement each layer behind additive helper functions in `backend/main.py`, extend the existing JSON payload rather than replacing it, and verify behavior with focused pytest coverage in `tests/test_app.py`.

**Tech Stack:** Python 3.11, FastAPI, requests, static HTML/vanilla JS, flat JSON files, pytest.

---

## Current repo anchors

Use these existing files and functions as the integration points:

- `backend/main.py`
  - global rules/constants near lines `43-319`
  - article dedupe: `dedupe_articles()`
  - article gate: `is_relevant_article()`
  - category/theme analysis: `classify_categories()`, `detect_policy_themes()`
  - stock linking: `build_stock_relationships()`
  - article assembly: `analyze_article()`
  - per-stock scoring: `compute_ticker_score()`
  - event aggregation: `build_event_tracking()`
  - API payload: `build_refresh_payload()`
- `company_knowledge.json`
- `dashboard.html`
- `tests/test_app.py`
- `ARCHITECTURE.md`
- `SPEC.md`
- `README.md`

Do **not** introduce a database, queue, or extra service. Keep all new state flat-file or in-memory, consistent with the current app architecture.

---

## Phase summary

1. Strengthen political relevance classification
2. Add event-stage and contradiction awareness
3. Upgrade company transmission-path modeling and impact direction
4. Add market-reaction confirmation
5. Surface the new reasoning clearly in the API and dashboard
6. Update docs and regression coverage

---

### Task 1: Add a richer policy-analysis schema and seed files

**Objective:** Define the structured metadata that later tasks will use for event stage, impact channel, and market confirmation without breaking the current app.

**Files:**
- Create: `policy_signal_rules.json`
- Create: `market_validation_config.json`
- Modify: `company_knowledge.json`
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Create `policy_signal_rules.json`**

Add a flat-file ruleset with these top-level sections:
- `political_relevance`
  - `institution_terms`
  - `legal_terms`
  - `action_terms`
  - `weak_context_terms`
  - `non_political_terms`
- `event_stage_rules`
  - `proposal`
  - `debate`
  - `approved`
  - `effective`
  - `delayed`
  - `revoked`
  - `enforced`
- `negation_terms`
- `reversal_terms`
- `thread_match_terms`

Use Indonesian-first vocabulary and keep the file human-editable.

**Step 2: Create `market_validation_config.json`**

Add config values for:
- abnormal-return windows: `30m`, `1d`
- confirmation thresholds: `price_sigma`, `volume_ratio`
- lookback lengths used to estimate a baseline
- fallback behavior when live history cannot be fetched

**Step 3: Expand `company_knowledge.json` schema**

For each company record, add additive optional fields:
- `policy_channel_details`
  - list of objects with `channel`, `direction_map`, `keywords`, `confidence`
- `exposure_factors`
  - `revenue_exposure`
  - `input_cost_exposure`
  - `financing_sensitivity`
  - `regulatory_dependency`
  - `export_import_dependency`
- `market_validation_proxy`
  - optional sector or commodity proxy symbol/name

Do not delete current fields used by the live app.

**Step 4: Add loader helpers in `backend/main.py`**

Implement helpers parallel to the existing company knowledge loader:
- `load_policy_signal_rules()`
- `load_market_validation_config()`
- `normalize_policy_rules(raw)`
- `normalize_company_knowledge(raw)` expansion for the new optional fields

**Step 5: Write a schema test**

In `tests/test_app.py`, add a test that representative loaders return normalized dicts with expected keys.

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "knowledge or rules" -v
```
Expected: PASS for the new schema-focused tests.

**Step 7: Commit**

```bash
git add policy_signal_rules.json market_validation_config.json company_knowledge.json backend/main.py tests/test_app.py
git commit -m "feat: add structured policy analysis rule files"
```

---

### Task 2: Replace the brittle relevance gate with a scored political relevance model

**Objective:** Keep the fast keyword gate, but turn it into a scored classifier that distinguishes strong policy articles from weak/noisy mentions.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`
- Modify: `SPEC.md`
- Modify: `ARCHITECTURE.md`

**Step 1: Add a new helper in `backend/main.py`**

Implement:
- `score_political_relevance(article: dict[str, Any]) -> dict[str, Any]`

Return fields like:
- `relevance_score` (`0.0-1.0`)
- `relevance_label` (`political`, `maybe`, `not_political`)
- `relevance_signals` (matched institutions/actions/legal terms)
- `relevance_penalties` (weak context / non-political terms)

**Step 2: Preserve `is_relevant_article()` as a wrapper**

Change `is_relevant_article()` so it calls `score_political_relevance()` and returns `True` only when score clears a threshold, instead of simple `any(keyword in text ...)` logic.

**Step 3: Thread the score into `analyze_article()`**

Add these fields to analyzed event output:
- `relevance_score`
- `relevance_label`
- `relevance_signals`

Use the relevance score as an input to overall event confidence/significance.

**Step 4: Add tests for false positives and true positives**

In `tests/test_app.py`, add coverage for:
- strong policy article with institutions + action + legal terms -> `political`
- weak macro article with one generic word -> `maybe` or filtered out
- clearly non-political article -> `not_political`

Use synthetic fixtures similar to `FAKE_ARTICLE` and `VAGUE_ARTICLE`.

**Step 5: Update docs**

In `SPEC.md` and `ARCHITECTURE.md`, replace references that imply a pure keyword gate with a two-stage relevance scorer.

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "relevance or vague" -v
```
Expected: PASS, and the vague article test should prove weaker articles are filtered more aggressively than before.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_app.py SPEC.md ARCHITECTURE.md
git commit -m "feat: add scored political relevance classification"
```

---

### Task 3: Add event-stage detection and reversal handling

**Objective:** Distinguish proposed, approved, effective, delayed, and revoked policies so the system stops treating all policy mentions as equally actionable.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`
- Modify: `README.md`

**Step 1: Implement event-stage helpers**

Add:
- `detect_event_stage(text: str) -> dict[str, Any]`
- `detect_negation_or_reversal(text: str) -> dict[str, Any]`

Return fields such as:
- `event_stage`
- `event_stage_confidence`
- `event_stage_signals`
- `is_reversal`
- `is_tentative`

**Step 2: Integrate into `analyze_article()`**

Append these event fields to every analyzed article. Adjust `significance` so:
- `proposal` / `debate` < `approved` < `effective` / `enforced`
- `revoked` and `delayed` reduce or reverse prior implied direction where appropriate

**Step 3: Add contradiction-aware event ranking**

In `build_refresh_payload()`, when sorting analyzed articles, keep the raw event but add flags that later tasks can use to group contradictory policy coverage.

**Step 4: Add tests**

Add fixtures for:
- proposal article
- approved article
- canceled/revoked article

Assert that:
- event stages differ
- later stronger stages get higher significance than tentative stages
- reversal flags are set for cancellation language

**Step 5: Verify**

Run:
```bash
pytest tests/test_app.py -k "stage or revoked or proposal" -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add backend/main.py tests/test_app.py README.md
git commit -m "feat: model policy event stages and reversals"
```

---

### Task 4: Upgrade company knowledge from theme matching to transmission-path scoring

**Objective:** Make stock linkage depend on concrete business pathways, not just theme overlap plus aliases.

**Files:**
- Modify: `backend/main.py`
- Modify: `company_knowledge.json`
- Test: `tests/test_app.py`
- Modify: `ARCHITECTURE.md`

**Step 1: Add transmission-path helpers**

Implement helpers such as:
- `match_policy_channels(text: str, knowledge: dict[str, Any], themes: list[dict[str, Any]]) -> list[dict[str, Any]]`
- `score_company_exposure(knowledge: dict[str, Any], matched_channels: list[dict[str, Any]], direct_alias_hit: bool) -> dict[str, Any]`
- `expected_direction_for_company(themes, matched_channels, knowledge) -> dict[str, Any]`

Each helper should expose both numeric output and explainability data.

**Step 2: Refactor `build_stock_relationships()`**

Replace broad fallback logic with a clearer chain:
1. direct mention path
2. knowledge-backed matched channel path
3. reject everything else

Use channel-level evidence to compute:
- `transmission_clarity`
- `company_exposure`
- `impact_direction` (`positive`, `negative`, `mixed`, `neutral`)
- `direction_rationale`
- `matched_policy_channels`

**Step 3: Keep the anti-noise rule strict**

Preserve the design rule from the prior company-knowledge work:
- no knowledge + no direct mention = reject relationship
- broad sector overlap alone is insufficient

**Step 4: Extend relationship payloads**

Each `stock_relationships[]` item should now include:
- `impact_direction`
- `direction_rationale`
- `matched_policy_channels`
- `channel_confidence`
- `exposure_factors`

Also expose the best of these fields on each stock row in `build_refresh_payload()`.

**Step 5: Add tests**

Cover:
- direct company mention -> `direct` relationship survives
- matched downstreaming/housing/banking channel -> indirect relationship survives with non-empty `matched_policy_channels`
- broad sector article without channel evidence -> no relationship
- same article can be negative for one company and neutral/mixed for another depending on channel logic

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "relationship or channel or direction" -v
```
Expected: PASS, with at least one assertion proving that sector-only spillover no longer creates a surviving relationship.

**Step 7: Commit**

```bash
git add backend/main.py company_knowledge.json tests/test_app.py ARCHITECTURE.md
git commit -m "feat: score stock links through transmission paths"
```

---

### Task 5: Add event-thread clustering and contradiction summaries

**Objective:** Collapse repeated coverage of the same policy thread and flag when newer articles weaken or reverse earlier ones.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`
- Modify: `dashboard.html`

**Step 1: Create a thread-key helper**

Implement:
- `build_event_thread_key(article: dict[str, Any]) -> str`
- `group_articles_into_threads(events: list[dict[str, Any]]) -> list[dict[str, Any]]`

Base the thread key on a compact mix of:
- top theme
- key institution/regulator
- normalized named company/entity
- category

**Step 2: Build thread summaries**

For each thread, compute:
- `thread_id`
- `thread_status` (`active`, `confirmed`, `contested`, `reversed`)
- `article_count`
- `latest_event_stage`
- `latest_headline`
- `contradiction_count`

**Step 3: Integrate into refresh payload**

In `build_refresh_payload()`, add a top-level `event_threads` array and attach `thread_id` / `thread_status` to each event.

**Step 4: Add tests**

Create a test with 2-3 synthetic articles about the same policy where later text introduces a reversal or clarification. Assert that:
- they share one thread
- the thread status becomes `contested` or `reversed`
- duplicate counting is reduced at the summary layer

**Step 5: Verify**

Run:
```bash
pytest tests/test_app.py -k "thread or contradiction" -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add backend/main.py tests/test_app.py dashboard.html
git commit -m "feat: add event thread clustering and contradiction summaries"
```

---

### Task 6: Add market-reaction confirmation behind an additive validation layer

**Objective:** Separate text-predicted relevance from observed market confirmation so the product can say "predicted" vs "confirmed" instead of implying causality from text alone.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`
- Modify: `SPEC.md`
- Modify: `README.md`

**Step 1: Add a historical quote fetcher**

Implement a helper using the existing Yahoo Finance chart endpoint pattern:
- `fetch_market_validation_series(ticker: str, range: str, interval: str) -> dict[str, Any]`

Keep it optional and resilient; on failure return warnings, not crashes.

**Step 2: Implement validation logic**

Add:
- `validate_market_reaction(article, ticker, quote, relationship) -> dict[str, Any]`

Suggested output:
- `validation_status` (`unvalidated`, `predicted_only`, `confirmed`, `rejected`, `insufficient_data`)
- `validation_window`
- `abnormal_return`
- `abnormal_volume_ratio`
- `validation_score`

**Step 3: Integrate into stock/event payloads**

For each surviving relationship, attach the validation result. Roll the strongest relationship validation up into each stock row.

**Step 4: Keep feature degradable**

If the extra market data is unavailable:
- do not hide the text-based relationship
- mark it explicitly as `predicted_only` or `insufficient_data`
- append a warning instead of failing refresh

**Step 5: Add deterministic tests**

Do not use live finance calls in pytest. Mock the validation fetcher with synthetic price/volume series and assert:
- a strong move after the event -> `confirmed`
- flat/noisy series -> `predicted_only` or `rejected`

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "validation or confirmed or predicted" -v
```
Expected: PASS.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_app.py SPEC.md README.md
git commit -m "feat: add market reaction confirmation layer"
```

---

### Task 7: Expose the new reasoning cleanly in the dashboard and API contract

**Objective:** Make the richer backend outputs visible to users without overloading the dashboard.

**Files:**
- Modify: `dashboard.html`
- Modify: `tests/test_app.py`
- Modify: `SPEC.md`
- Modify: `ARCHITECTURE.md`

**Step 1: Extend API payload docs**

Document new top-level and nested fields in `SPEC.md`:
- event fields: `relevance_label`, `event_stage`, `thread_id`, `thread_status`
- relationship fields: `impact_direction`, `direction_rationale`, `validation_status`
- stock fields: `relationship_type`, `impact_direction`, `validation_status`
- payload fields: `event_threads`

**Step 2: Update dashboard rendering**

In `dashboard.html`, add compact badges/lines for:
- event stage
- impact direction
- validation status (`Predicted`, `Confirmed`, `Insufficient data`)
- thread status when an event is contested/reversed

Prefer badges and one-line subtext over large new panels.

**Step 3: Add runtime-hook regression coverage**

Add test assertions that key UI hooks still exist after the HTML changes, plus any new IDs required by the renderer.

**Step 4: Verify**

Run:
```bash
pytest tests/test_app.py -k "dashboard or endpoint or runtime hooks" -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add dashboard.html tests/test_app.py SPEC.md ARCHITECTURE.md
git commit -m "feat: surface policy stages and validation in dashboard"
```

---

### Task 8: End-to-end verification and release cleanup

**Objective:** Prove the full upgraded pipeline works together and leave the repo documented and shippable.

**Files:**
- Modify as needed from prior tasks only
- Create: `docs/plans/2026-05-30-political-signal-upgrades.md` (this file, already created)

**Step 1: Run the full test suite**

Run:
```bash
pytest -q
```
Expected: all tests pass.

**Step 2: Run syntax verification**

Run:
```bash
python3 -m py_compile backend/main.py app.py
```
Expected: no output.

**Step 3: Smoke-check representative payloads**

Run a small Python snippet or targeted test that calls `build_refresh_payload()` with fake fetchers and prints/asserts the presence of:
- `relevance_label`
- `event_stage`
- `impact_direction`
- `validation_status`
- `event_threads`

**Step 4: Review git diff**

Run:
```bash
git diff --stat
git diff
```
Confirm only intended files changed.

**Step 5: Final commit**

```bash
git add backend/main.py company_knowledge.json policy_signal_rules.json market_validation_config.json dashboard.html tests/test_app.py README.md SPEC.md ARCHITECTURE.md docs/plans/2026-05-30-political-signal-upgrades.md
git commit -m "feat: improve political signal precision and validation"
```

**Step 6: Push**

```bash
git push
```

---

## Implementation notes for the current repo

- Keep new loaders in `backend/main.py` unless the file becomes too large. If it grows unwieldy, split only into small local modules under `backend/` such as:
  - `backend/policy_rules.py`
  - `backend/market_validation.py`
  - `backend/relationships.py`
- Do not change existing endpoint URLs unless strictly necessary; extend payloads additively.
- Maintain compatibility with the current watchlist persistence and cache behavior.
- Prefer deterministic pytest fixtures over live network calls.
- Preserve the existing anti-noise philosophy: no weak sector-only spillover.

## Suggested execution order if implementing in smaller PRs

1. Task 1 + Task 2
2. Task 3 + Task 4
3. Task 5
4. Task 6
5. Task 7 + Task 8

This order minimizes merge pain because it upgrades the analysis core before touching the UI contract.

## Acceptance criteria

The upgrade is complete when:
- vague political-ish headlines no longer survive as strong events
- proposal/approved/revoked articles are scored differently
- stock links show a concrete transmission path and direction
- repeated/contradictory policy coverage is grouped into threads
- the app can distinguish `predicted_only` from `confirmed`
- `/api/dashboard` and `/api/refresh` expose the new fields without breaking current consumers
- `pytest -q` passes
