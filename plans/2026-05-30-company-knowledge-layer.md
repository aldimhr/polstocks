# Company Knowledge Layer Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Add a source-backed company knowledge layer so political-to-stock links rely on company-specific exposure notes and evidence, not just sector/theme overlap.

**Architecture:** Introduce a local knowledge base for watchlist companies with structured exposure facts, policy channels, and public-source evidence URLs. Feed that knowledge into article scoring and API output so each surfaced link can cite company-specific context.

**Tech Stack:** FastAPI, Python 3.11, static JSON seed data, pytest, vanilla JS dashboard.

---

### Task 1: Add a structured company knowledge dataset

**Objective:** Create a reusable knowledge file that stores company-specific policy exposure facts for the default watchlist.

**Files:**
- Create: `company_knowledge.json`
- Modify: `README.md`
- Modify: `ARCHITECTURE.md`

**Step 1: Define the schema**

Each company record should include:
- `ticker`
- `name`
- `sector`
- `summary`
- `policy_exposures` (list of themes)
- `business_lines` (list)
- `policy_channels` (list of explainable transmission paths)
- `evidence` (list of `{label, url, note}`)
- `aliases` (optional extra matching)

**Step 2: Seed the default watchlist names**

Create entries for the currently supported default watchlist tickers so the runtime can look up every default stock.

**Step 3: Document the file**

Update docs to explain:
- why the knowledge layer exists
- where the file lives
- how it improves anti-garbage filtering

**Step 4: Verify**

Run:
- `python3 -m py_compile backend/main.py` after integration work later

**Step 5: Commit**

Commit with the larger feature after implementation is complete.

---

### Task 2: Load and validate the knowledge layer in the backend

**Objective:** Make backend scoring depend on structured company knowledge instead of loose sector matching.

**Files:**
- Modify: `backend/main.py`
- Test: `tests/test_app.py`

**Step 1: Add loader helpers**

Implement helpers to:
- read `company_knowledge.json`
- normalize ticker keys to `.JK`
- provide fallback-safe accessors

**Step 2: Add validation logic**

At load time, verify each record has:
- ticker
- at least one `policy_exposures` entry
- at least one `policy_channels` entry
- at least one `evidence` item with URL

**Step 3: Integrate into scoring**

Use knowledge records to improve:
- transmission clarity
- company exposure
- rationale generation
- evidence output

Hard rule: do not surface a stock relationship unless knowledge-layer support exists or the company is directly named in the article.

**Step 4: Expose knowledge in API payloads**

For stock rows and event relationships, include:
- knowledge summary
- policy channel
- company evidence list

**Step 5: Verify**

Run targeted tests after adding/adjusting assertions.

---

### Task 3: Add tests for the knowledge gate

**Objective:** Prove that vague stories do not create links, while knowledge-backed stories do.

**Files:**
- Modify: `tests/test_app.py`

**Step 1: Add schema-level assertions**

Test that loaded company knowledge exists for representative tickers and includes evidence URLs.

**Step 2: Add relationship tests**

Cover:
- direct mention article -> direct surviving relationship
- theme article + matching knowledge -> indirect surviving relationship
- vague macro article -> no surviving relationships
- non-matching ticker -> filtered out even if sector is broad

**Step 3: Run tests**

Run:
- `pytest -q`
Expected: all tests pass.

---

### Task 4: Improve dashboard explainability

**Objective:** Show users the company-specific reason behind surfaced links.

**Files:**
- Modify: `dashboard.html`

**Step 1: Add stock-row context**

Display a short knowledge-backed note under each ticker when available.

**Step 2: Add event-link context**

Show the best surviving relationship rationale and policy channel in the event card.

**Step 3: Keep UI compact**

Do not overload the dashboard; prefer one-line rationale plus tooltip/title for details.

**Step 4: Verify**

Use `/api/dashboard` or `/api/refresh` output and confirm the UI renders without missing fields.

---

### Task 5: End-to-end verification, commit, and push

**Objective:** Verify the feature works, then ship it.

**Files:**
- Modify as needed from prior tasks only

**Step 1: Run verification**

Run:
- `pytest -q`
- `python3 -m py_compile backend/main.py`
- one small smoke script that prints representative stock relationship fields

**Step 2: Review git diff**

Check that only intended files changed.

**Step 3: Commit**

```bash
git add company_knowledge.json backend/main.py dashboard.html tests/test_app.py README.md ARCHITECTURE.md docs/plans/2026-05-30-company-knowledge-layer.md
git commit -m "feat: add company knowledge layer for stock mapping"
```

**Step 4: Push**

```bash
git push
```
