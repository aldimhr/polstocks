# PolStock Source Robustness Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make PolStock harder to fool by ensuring every political-stock link is backed by high-quality, clearly labeled, freshness-aware source evidence instead of a single weak article.

**Architecture:** Keep the current single-process FastAPI app and flat-file configuration model. Add a source registry layer that assigns each feed/article a source tier, freshness score, redundancy group, and provenance label; feed that metadata into article gating, relationship scoring, and UI display. The implementation should stay additive: preserve the existing refresh pipeline, but enrich article objects and API payloads so weak or duplicated sources are downgraded rather than silently treated as strong evidence.

**Tech Stack:** Python 3.11, FastAPI, requests/feedparser/XML parsing, static HTML/vanilla JS, JSON config files, pytest.

---

## Current repo anchors

Use these existing files and helpers as integration points:

- `backend/main.py`
  - source fetchers and RSS parsing near the top of the file
  - article dedupe helpers
  - political relevance gating
  - article analysis and relationship building
  - refresh payload assembly
- `company_knowledge.json`
- `policy_signal_rules.json`
- `market_validation_config.json`
- `dashboard.html`
- `tests/test_app.py`
- `SPEC.md`
- `ARCHITECTURE.md`
- `README.md`

Do **not** introduce a database, queue, or extra service. Keep all new state flat-file or in-memory, consistent with the current app architecture.

---

## Source robustness rules

1. **Source tiering must be explicit.**
   - Official / government / regulator sources outrank general media.
   - Company IR / disclosures outrank commentary.
   - Opinion / blog / repost sources are allowed, but they must never be treated as top-tier evidence by default.

2. **Redundancy must reduce uncertainty.**
   - A single source is a candidate signal.
   - Two independent sources with the same claim are stronger.
   - An official source plus market-follow-through is the strongest path.

3. **Freshness must matter.**
   - Recent sources should score higher than stale ones.
   - Old coverage should decay unless it is a canonical background reference.

4. **Deduplication must happen before confidence inflation.**
   - Reposts and near-duplicates should be collapsed into one canonical thread.
   - Repeated republication of the same story must not multiply confidence.

5. **Weak sources should be visible, not hidden.**
   - If coverage is thin, the UI and payload should say so.
   - PolStock should prefer a clear “low confidence / sparse sources” state over a fake strong conclusion.

---

## Proposed source metadata model

Add a source registry file, for example:
- Create: `source_registry.json`

Use human-editable records like:
- `name`
- `domain`
- `source_type` (`government`, `regulator`, `company`, `media`, `profile`, `other`)
- `tier` (`1` strongest to `4` weakest)
- `country_focus` (`id`, `global`, `mixed`)
- `canonical_domain`
- `trust_weight`
- `freshness_half_life_hours`
- `duplicate_grouping`
- `notes`

Each article object should carry derived fields such as:
- `source_type`
- `source_tier`
- `source_weight`
- `source_freshness_score`
- `source_quality_score`
- `canonical_url`
- `duplicate_group_id`
- `evidence_strength`
- `coverage_warning`

---

## Phase summary

1. Add a source registry and normalization layer
2. Canonicalize/dedupe articles by source and claim
3. Add freshness and redundancy scoring
4. Downgrade weak-source outputs in relationship scoring
5. Surface provenance and confidence in the API/dashboard
6. Add regression tests and docs

---

### Task 1: Add a source registry and loader

**Objective:** Create a structured source registry so the app can score feeds consistently instead of hard-coding trust in many places.

**Files:**
- Create: `source_registry.json`
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Create `source_registry.json`**

Add records for the current configured sources, including at least:
- Antara News
- CNBC Indonesia
- Kompas
- Detik Finance
- Tempo
- BeritaSatu
- Setkab
- OJK
- KPK

Assign tiered trust values and a short note explaining why each source belongs there.

**Step 2: Add a loader in `backend/main.py`**

Implement helpers similar to the existing knowledge file loader:
- `load_source_registry()`
- `normalize_source_registry(raw)`
- `source_profile_for_name(name: str) -> dict[str, Any]`
- `source_profile_for_domain(domain: str) -> dict[str, Any]`

**Step 3: Add normalized source metadata to fetched articles**

When parsing RSS or scraped sources, attach source metadata early so later steps can use it.

**Step 4: Write a loader test**

Assert that a representative source resolves to:
- the expected `source_type`
- a numeric `tier`
- a usable `trust_weight`

**Step 5: Verify**

Run:
```bash
pytest tests/test_app.py -k "source_registry or source_profile" -v
```
Expected: PASS.

**Step 6: Commit**

```bash
git add source_registry.json backend/main.py tests/test_app.py
git commit -m "feat: add source registry and source normalization"
```

---

### Task 2: Canonicalize and deduplicate source coverage

**Objective:** Prevent republishes and near-duplicates from inflating confidence.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Add canonicalization helpers**

Implement helpers such as:
- `canonicalize_article_url(url: str) -> str`
- `canonical_source_key(article: dict[str, Any]) -> str`
- `claim_signature(article: dict[str, Any]) -> str`
- `merge_duplicate_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]`

Use URL normalization plus headline similarity plus source domain grouping to collapse duplicates.

**Step 2: Preserve the canonical representative**

For each duplicate group, keep one canonical article and attach:
- duplicate count
- alternate URLs
- source list
- latest publication time

**Step 3: Add dedupe regression tests**

Create fixtures where the same article appears through multiple outlets or mirrored URLs. Assert that:
- the group collapses to one canonical item
- duplicate count is correct
- confidence does not multiply because of reposts

**Step 4: Verify**

Run:
```bash
pytest tests/test_app.py -k "dedupe or duplicate" -v
```
Expected: PASS.

**Step 5: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: deduplicate mirrored political coverage"
```

---

### Task 3: Add freshness decay and source quality scoring

**Objective:** Make old or low-quality coverage contribute less than fresh, authoritative coverage.

**Files:**
- Modify: `backend/main.py`
- Modify: `SPEC.md`
- Test: `tests/test_app.py`

**Step 1: Add freshness helpers**

Implement helpers such as:
- `source_freshness_score(published_at: datetime, source_profile: dict[str, Any]) -> float`
- `source_quality_score(article: dict[str, Any]) -> dict[str, Any]`

Score should consider:
- source tier
- age in hours/days
- whether the source is canonical or duplicated
- whether the article has direct policy language or only commentary

**Step 2: Use freshness in article ranking**

When selecting the top events, prefer:
- fresh government/regulator/company sources
- then high-quality media with independent confirmation
- then weaker commentary only when clearly labeled

**Step 3: Add weak-source warnings**

If all available coverage for a claim is weak or stale, set a warning like:
- `coverage_warning = "thin_source_coverage"`
- `coverage_warning = "stale_coverage"`

**Step 4: Add scoring tests**

Assert that:
- a fresh government source scores above a stale commentary source
- duplicate republications do not improve quality
- stale sources decay below fresher coverage

**Step 5: Update the spec**

Document the new freshness/quality rules in `SPEC.md` so the contract matches the implementation.

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "freshness or quality or stale" -v
```
Expected: PASS.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_app.py SPEC.md
git commit -m "feat: add source freshness and quality scoring"
```

---

### Task 4: Downgrade weak-source relationships in scoring

**Objective:** Make source quality affect ticker impact so weak evidence cannot produce confident stock links.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`
- Modify: `ARCHITECTURE.md`

**Step 1: Feed source quality into relationship scoring**

Update the article-to-ticker path so the relationship score includes source quality and redundancy, not just text similarity.

Suggested derived fields:
- `source_confidence`
- `evidence_strength`
- `relationship_confidence`
- `relationship_type` (`direct`, `indirect`, `thematic`)

**Step 2: Add weak-source thresholds**

If coverage is thin, the relationship should default to one of:
- `predicted_only`
- `insufficient_data`
- `low_confidence`

rather than pretending the link is confirmed.

**Step 3: Keep direct official sources strong**

A direct official announcement should remain high-confidence even if there is no second article yet.

**Step 4: Add scoring regression tests**

Create tests that prove:
- strong official sources produce stronger relationships than generic media
- weak opinion-only coverage cannot dominate a direct official source
- low-source-coverage articles remain visible but downgraded

**Step 5: Update architecture docs**

Describe source robustness as a first-class part of the pipeline.

**Step 6: Verify**

Run:
```bash
pytest tests/test_app.py -k "relationship or confidence or evidence" -v
```
Expected: PASS.

**Step 7: Commit**

```bash
git add backend/main.py tests/test_app.py ARCHITECTURE.md
git commit -m "feat: incorporate source quality into relationship scoring"
```

---

### Task 5: Surface source provenance and confidence in the dashboard

**Objective:** Make source quality visible to the user so PolStock feels trustworthy instead of magical.

**Files:**
- Modify: `backend/main.py`
- Modify: `dashboard.html`
- Test: `tests/test_app.py`

**Step 1: Add source fields to the API payload**

Expose source-related metadata for each event and relationship, such as:
- source name
- source type
- tier badge
- freshness label
- duplicate count
- coverage warning

**Step 2: Update the dashboard UI**

Show lightweight badges like:
- `Official`
- `High confidence`
- `Fresh`
- `Sparse sources`
- `Duplicated coverage`

Keep the UI minimal and avoid exposing implementation jargon.

**Step 3: Add a UI contract test**

Verify the payload contains the new source metadata fields and that the dashboard can render them without breaking the existing ticker click flow.

**Step 4: Verify**

Run:
```bash
pytest tests/test_app.py -v
```
Expected: all relevant tests pass.

**Step 5: Commit**

```bash
git add backend/main.py dashboard.html tests/test_app.py
git commit -m "feat: show source provenance and confidence in dashboard"
```

---

## Acceptance criteria

PolStock source robustness is done when:

- Official sources are clearly ranked above commentary.
- Duplicate republications do not inflate confidence.
- Freshness changes the effective weight of a source.
- Weak or sparse coverage is labeled instead of hidden.
- Ticker relationships include source provenance in the payload.
- The dashboard shows source trust cues without becoming cluttered.
- Regression tests cover source tiering, dedupe, freshness, and weak-source fallback.

---

## Recommended execution order

1. Task 1 — source registry and loader
2. Task 2 — dedupe and canonicalization
3. Task 3 — freshness and quality scoring
4. Task 4 — relationship scoring changes
5. Task 5 — dashboard visibility

---

## Notes

- Keep the implementation additive.
- Prefer explicit metadata over hidden heuristics.
- If a claim is weak, say so.
- If sources are sparse, the UI should admit it.
- Do not let repeated copies of the same story masquerade as stronger evidence.
