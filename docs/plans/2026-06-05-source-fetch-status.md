# Per-Relationship Source Fetch Status — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Expose `source_fetch_status` (how the source profile was resolved: registry, inference, fallback) on each event and relationship so the dashboard can show per-article source provenance quality.

**Architecture:** The `source_metadata_for()` function already computes `source_profile_resolution` and `used_registry_profile` for each article. These fields propagate into the event dict via `analyze_article → article_context`. The gap is that `formatted_events` (the frontend-facing payload) strips them out. We need to (a) include them in `formatted_events`, (b) expose them on each relationship, (c) render them in the dashboard provenance chips, and (d) add tests.

**Tech Stack:** Python (backend/main.py), HTML/JS (dashboard.html), pytest

---

### Task 1: Add `source_fetch_status` to `formatted_events`

**Objective:** Include source resolution metadata in the event payload so the frontend can access it.

**Files:**
- Modify: `backend/main.py:3886-3914` (`formatted_events` construction)

**Step 1: Write failing test**

Add to `tests/test_app.py`:

```python
def test_formatted_events_include_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    event = payload["events"][0]
    assert "source_fetch_status" in event
    assert event["source_fetch_status"] in {
        "registry_exact", "registry_alias", "registry_domain",
        "inferred_fallback", "url_inference", "heuristic_fallback", "unknown",
    }
```

**Step 2: Run test to verify failure**

Run: `cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py::test_formatted_events_include_source_fetch_status -v`
Expected: FAIL — `KeyError: 'source_fetch_status'`

**Step 3: Add field to `formatted_events`**

In `backend/main.py`, inside the `formatted_events.append({...})` block (around line 3912), add:

```python
"source_fetch_status": str(event.get("source_profile_resolution", "unknown") or "unknown"),
```

**Step 4: Run test to verify pass**

Run: `cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py::test_formatted_events_include_source_fetch_status -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: expose source_fetch_status on formatted_events"
```

---

### Task 2: Add `source_fetch_status` to each stock relationship

**Objective:** Each relationship gets the event's source resolution method so downstream consumers can see per-link fetch quality.

**Files:**
- Modify: `backend/main.py:2994-3020` (inside `build_stock_relationships`, where the relationship dict is built)

**Step 1: Write failing test**

```python
def test_stock_relationships_include_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    link = payload["events"][0]["stock_relationships"][0]
    assert "source_fetch_status" in link
    assert isinstance(link["source_fetch_status"], str)
```

**Step 2: Run test to verify failure**

Run: `cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py::test_stock_relationships_include_source_fetch_status -v`
Expected: FAIL

**Step 3: Add field to relationship dict**

In `backend/main.py`, inside `build_stock_relationships`, where the relationship dict is built (around line 3020), add to the returned dict:

```python
"source_fetch_status": str(article.get("source_profile_resolution", "unknown") or "unknown"),
```

**Step 4: Run test to verify pass**

Run: `cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py::test_stock_relationships_include_source_fetch_status -v`
Expected: PASS

**Step 5: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: expose source_fetch_status on stock relationships"
```

---

### Task 3: Propagate to stock-level payload

**Objective:** The stock summary object should carry `source_fetch_status` from its strongest link (for dashboard display).

**Files:**
- Modify: `backend/main.py:3820-3879` (stock dict construction in `build_refresh_payload`)

**Step 1: Write failing test**

```python
def test_stock_payload_includes_source_fetch_status():
    payload = appmod.build_refresh_payload(
        ["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher,
        stock_fetcher=fake_stock_fetcher,
        market_fetcher=fake_market_fetcher,
    )
    stock = payload["stocks"][0]
    assert "source_fetch_status" in stock
```

**Step 2: Run test to verify failure**

Expected: FAIL

**Step 3: Add field to stock dict**

In the stock dict construction (around line 3877), add:

```python
"source_fetch_status": strongest_link[1].get("source_fetch_status", "unknown") if strongest_link else "unknown",
```

**Step 4: Run test to verify pass**

Expected: PASS

**Step 5: Commit**

```bash
git add backend/main.py tests/test_app.py
git commit -m "feat: propagate source_fetch_status to stock payload"
```

---

### Task 4: Render source_fetch_status in dashboard provenance chips

**Objective:** Show the fetch status as a provenance chip on each event card in the dashboard.

**Files:**
- Modify: `dashboard.html` — `renderProvenanceBadges()` function (around line 1419)

**Step 1: Add a provenance chip renderer for fetch status**

In `dashboard.html`, add a new helper function near the other provenance helpers (around line 1381):

```javascript
function provenanceFetchStatusLabel(fetchStatus) {
  const map = {
    registry_exact: 'Registry matched',
    registry_alias: 'Registry alias',
    registry_domain: 'Registry domain',
    inferred_fallback: 'Inferred source',
    url_inference: 'URL inferred',
    heuristic_fallback: 'Heuristic source',
    unknown: '',
  };
  return map[String(fetchStatus || '').trim().toLowerCase()] || '';
}
```

**Step 2: Add the chip to `renderProvenanceBadges`**

Inside `renderProvenanceBadges` (around line 1429), after the confidence chip, add:

```javascript
const fetchLabel = provenanceFetchStatusLabel(meta.source_fetch_status);
if (fetchLabel) chips.push(`<span class="reasoning-chip ${provenanceChipClass('fetch', fetchLabel)}">${escapeHtml(fetchLabel)}</span>`);
```

**Step 3: Add CSS for the fetch chip tone**

Add to the CSS (near the existing provenance chip styles):

```css
.reasoning-chip.fetch { color: var(--blue, #6aa9ff); border-color: rgba(106,169,255,0.25); }
```

**Step 4: Verify in browser**

Open the dashboard and check that event cards show the "Registry matched" or "Inferred source" chip.

**Step 5: Commit**

```bash
git add dashboard.html
git commit -m "feat: render source_fetch_status provenance chip in dashboard"
```

---

### Task 5: Run full regression

**Objective:** Verify all 53+ tests pass and no regressions.

**Step 1:** Run full suite

```bash
cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py -v
```

Expected: All tests pass (53 existing + 3 new = 56)

**Step 2: Commit any test fixture cleanup if needed**
