# PolStock Backend Split — Implementation Plan

> **For Hermes:** Implement task-by-task. Run tests after each task. Commit after each.

**Goal:** Split the 4286-line `backend/main.py` monolith into focused modules for maintainability.

**Architecture:** 7 modules with clean dependency flow:

```
config.py        ← constants, stock data, sector maps (no imports from other modules)
utils.py         ← pure helpers: dates, text, normalization (imports: config)
sources.py       ← source fetching, parsing, quality, corroboration (imports: config, utils)
scoring.py       ← NLP, policy matching, article analysis (imports: config, utils)
stocks.py        ← stock quotes, history, sector summary (imports: config, utils)
validation.py    ← market validation, source conflicts, reliability (imports: config, utils)
events.py        ← event building, threading, tracking, dashboard cues (imports: config, utils, scoring, sources, stocks, validation)
routes.py        ← FastAPI routes + Pydantic models (imports: everything)
main.py          ← entry point: creates app, wires routes, startup
```

**Dependency rule:** No circular imports. `config` and `utils` are leaves. `events` is the top of the chain.

**Test strategy:** 79 existing tests in `tests/test_app.py` import from `backend.main`. After the split, `backend/main.py` re-exports everything so tests keep working without changes.

---

## Task 1: Create `backend/config.py`

**Objective:** Extract all constants, stock seed data, sector maps, and config values.

**Lines to move:** 36–355 (PROJECT_ROOT through MIN_RELATIONSHIP_SCORE)

**Also move:** All `STOCK_SEED`, `STOCK_MASTER`, `SECTORS`, `SECTOR_KEYWORDS`, `CATEGORY_RULES`, `CATEGORY_TO_SECTORS`, `POLICY_THEMES`, `NEWS_SOURCES`, `POLITICAL_SIGNAL_KEYWORDS`, `TICKER_EXPOSURE_PROFILES`

**Keep in main.py for now:** Everything else (we'll move incrementally)

**Verify:** `python3 -c "from backend.config import SECTORS; print(len(SECTORS))"`

---

## Task 2: Create `backend/utils.py`

**Objective:** Pure helper functions with no business logic.

**Functions to move:** `now_wib`, `now_iso`, `normalize_ticker`, `strip_tags`, `local_name`, `safe_text`, `parse_datetime`, `_parse_human_date_text`, `extract_html_published_at`, `clamp`, `normalize_match_text`, `collect_phrase_hits`, `normalize_event_window`, `event_window_config`, `event_window_delta`, `event_window_label`, `text_similarity`, `is_stale_article`, `within_trading_hours`, `sector_for_ticker`, `company_name_for_ticker`, `article_text`

**Imports:** `from backend.config import ...`

**Verify:** `python3 -c "from backend.utils import now_wib; print(now_wib())"`

---

## Task 3: Create `backend/sources.py`

**Objective:** Source fetching, parsing, quality scoring, corroboration.

**Functions to move:** `score_political_relevance`, `detect_event_stage`, `detect_negation_or_reversal`, `is_relevant_article`, `source_weight`, `infer_source_type`, `normalize_domain`, `canonicalize_article_url`, `canonical_source_key`, `claim_signature`, `_article_merge_priority`, `merge_duplicate_articles`, source registry functions (lines 856–1265), `normalize_watchlist_values`, `normalize_company_knowledge`, `load_company_knowledge_from_disk`, `company_knowledge_for_ticker`, `normalize_policy_signal_rules`, `load_policy_signal_rules`, `normalize_market_validation_config`, `load_market_validation_config`, `load_watchlist_from_disk`, `save_watchlist_to_disk`, `get_watchlist`, `set_watchlist`, RSS/HTML parsing (lines 1493–1620), source diagnostics (lines 1620–1770), `fetch_source`, `fetch_news_bundle`, `dedupe_articles`

**Imports:** `from backend.config import ...` + `from backend.utils import ...`

**Verify:** `python3 -c "from backend.sources import fetch_news_bundle"`

---

## Task 4: Create `backend/scoring.py`

**Objective:** NLP scoring, policy matching, article analysis.

**Functions to move:** `evidence_quality_score`, `recency_weight_for_article`, `infer_article_policy_signal`, `match_policy_channels`, `score_company_exposure`, `expected_direction_for_company`, `relationship_type_for_link`, `relationship_confidence_label`, `analyze_article`, `compute_ticker_score`

**Imports:** `from backend.config import ...` + `from backend.utils import ...`

**Verify:** `python3 -c "from backend.scoring import analyze_article"`

---

## Task 5: Create `backend/stocks.py`

**Objective:** Stock data fetching and formatting.

**Functions to move:** `fetch_live_quote`, `fetch_stock_quotes`, `fetch_market_index`, `stock_history_window_config`, `fetch_ticker_history`, `sort_stocks_by_impact`, `compute_sector_summary`

**Imports:** `from backend.config import ...` + `from backend.utils import ...`

**Verify:** `python3 -c "from backend.stocks import fetch_live_quote"`

---

## Task 6: Create `backend/validation.py`

**Objective:** Market validation, source conflicts, historical reliability.

**Functions to move:** `article_source_domain`, `corroboration_group_key`, `corroboration_multiplier_for_group`, `apply_corroboration_to_events`, source outcome history functions (lines 2760–2975), `source_conflict_scope_key`, `apply_source_conflicts_to_events`

**Imports:** `from backend.config import ...` + `from backend.utils import ...`

**Verify:** `python3 -c "from backend.validation import apply_corroboration_to_events"`

---

## Task 7: Create `backend/events.py`

**Objective:** Event building, threading, tracking, dashboard cues.

**Functions to move:** `build_stock_relationships`, thread functions (lines 3416–3595), `build_event_tracking`, `build_reasoning_summary`, `build_dashboard_cues`, `_background_refresh`, `build_refresh_payload`

**Imports:** from all other modules

**Verify:** `python3 -c "from backend.events import build_refresh_payload"`

---

## Task 8: Create `backend/routes.py` + slim `backend/main.py`

**Objective:** Move routes to their own file, leave main.py as entry point.

**Move to routes.py:** Pydantic models, all `@app.get/post/put/head` route functions

**main.py becomes:**
```python
from backend.routes import app
from backend.events import _prewarm_cache
# startup hook, main() entry point
```

**Critical:** Add re-exports in `backend/main.py` so existing tests keep working:
```python
from backend.config import *
from backend.utils import *
from backend.sources import *
from backend.scoring import *
from backend.stocks import *
from backend.validation import *
from backend.events import *
from backend.routes import *
```

**Verify:** `python3 -m pytest tests/ --tb=short -q` — all 79 tests pass

---

## Task 9: Final commit and push

```bash
git add -A
git commit -m "refactor: split 4286-line main.py into 8 focused modules"
git push origin main
```
