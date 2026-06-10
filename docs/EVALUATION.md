# PolStocks — Robustness Plan

> Deep analysis of [aldimhr/polstocks](https://github.com/aldimhr/polstocks) with a prioritized roadmap to make it production-grade.

---

## Table of Contents

1. [Current State Snapshot](#1-current-state-snapshot)
2. [What's Already Solid](#2-whats-already-solid)
3. [Critical Gaps](#3-critical-gaps)
4. [Signal Quality — Root Cause Analysis](#4-signal-quality--root-cause-analysis)
5. [Infrastructure Gaps](#5-infrastructure-gaps)
6. [Phased Roadmap](#6-phased-roadmap)
7. [Quick Wins (Do These Today)](#7-quick-wins-do-these-today)

---

## 1. Current State Snapshot

| Metric | Value | Status |
|---|---|---|
| Signal accuracy (total) | 34.7% | 🔴 worse than random |
| Signal accuracy (live) | 45.1% | 🔴 below 54.5% neutral baseline |
| Edge vs neutral baseline | -9.4% | 🔴 negative |
| BUY signals/day | 0 | 🔴 fully blocked |
| SELL accuracy | 0% | 🔴 do not use |
| Neutral predictions | 87% of total | 🟡 polluting backtest |
| High-confidence hit rate | 26.7% | 🔴 worse than low-confidence |
| Persistent storage | None | 🔴 all data lost on restart |
| Architecture quality | Modular FastAPI | ✅ solid |
| ML NLP stack | IndoBERT + RoBERTa | ✅ ready |
| Technical indicators | RSI, MACD, BB, ATR, S/R | ✅ implemented |

---

## 2. What's Already Solid

### Well-structured modular backend
Clean separation across `events.py`, `scoring.py`, `sources.py`, `nlp.py`, `signals.py`, and `stocks.py`. Each module has a clear responsibility and can be extended without breaking adjacent code.

### Strong evidence hierarchy design
Source dedup, freshness decay, corroboration scoring, and the company-specific knowledge layer (`company_knowledge.json`) are conceptually correct and rare in projects of this scale. The two-path linking rule (direct mention or matched policy channel) prevents false positives well.

### Honest self-assessment in SPEC.md
The SPEC documents real backtest numbers including the -9.4% edge, broken SELL signals, and 87% neutral prediction problem. This is exactly the right foundation to build calibration improvements on — the problem is already correctly diagnosed.

### ML NLP stack is ready
Indonesian RoBERTa sentiment + IndoBERT NER already integrated, with keyword fallback and feature-flag control via `POLSTOCK_ENABLE_ML_NLP`. Background warmup on startup avoids cold-start latency.

### Technical indicators fully implemented
RSI, MACD, Bollinger Bands, ATR, support/resistance, and volume spike detection all exist in `backend/stocks.py`. The plumbing is there — the signal decision layer just isn't wired to it correctly yet.

### Phase-by-phase planning discipline
The existence of `SPEC.md`, `ARCHITECTURE.md`, `EVALUATION.md`, and `PHASE6_PLAN.md` shows strong planning hygiene. The team knows where they are and where they're going.

---

## 3. Critical Gaps

### 3.1 Storage & Persistence 🔴

**Problem: All prediction and calibration data is lost on restart.**

The README says "no database" and watchlist.json is the only durable state. Even if SQLite is being used internally, if it's in-memory or in a temp path it resets on every deploy. Building a calibration history that improves signal quality over weeks requires predictions to survive restarts.

**Problem: No migration strategy.**

SPEC.md defines many `ALTER TABLE` statements inline but there's no migration runner. Schema drift between dev and prod is inevitable without numbered migration files.

**Fix:**

```bash
# Switch SQLite to a persistent path
POLSTOCKS_DB=/data/polstocks.db  # env var, default to /data/polstocks.db

# Add numbered migration files
/migrations/
  001_initial_schema.sql
  002_add_horizon_tier_columns.sql
  003_add_source_accuracy_table.sql
  004_add_daily_snapshots_table.sql

# Startup migration runner (Python, ~20 lines)
# Runs all unapplied migrations on app start
```

```python
# migrations/runner.py
import sqlite3, os, glob

def run_migrations(db_path: str, migrations_dir: str = "migrations"):
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE IF NOT EXISTS _migrations (name TEXT PRIMARY KEY)")
    applied = {r[0] for r in conn.execute("SELECT name FROM _migrations")}
    for f in sorted(glob.glob(f"{migrations_dir}/*.sql")):
        name = os.path.basename(f)
        if name not in applied:
            conn.executescript(open(f).read())
            conn.execute("INSERT INTO _migrations VALUES (?)", (name,))
            conn.commit()
            print(f"Applied migration: {name}")
    conn.close()
```

**Daily backup (add to cron):**
```bash
0 2 * * * cp /data/polstocks.db /data/backups/$(date +%Y%m%d).db
```

---

### 3.2 Error Handling & Resilience 🟡

**Problem: Yahoo Finance is a single point of failure.**

`yfinance` scrapes undocumented Yahoo endpoints. Rate limits, structure changes, or outages silently return stale or empty data with no fallback. Stock cards should clearly show "data stale since X" when the fetcher fails.

**Problem: RSS feeds fail silently.**

If an Indonesian news source changes its RSS structure or goes down, the app returns the last cached result without flagging which sources failed. Users can't distinguish "quiet market" from "broken fetcher."

**Problem: No request timeouts.**

A slow RSS source or delayed Yahoo Finance response can block the entire `/api/refresh` endpoint with no timeout, causing the frontend to hang indefinitely.

**Problem: No circuit breaker.**

A source returning errors 10× in a row keeps getting retried every cycle, wasting time and potentially triggering rate limits.

**Fix:**

```python
# Use httpx with explicit timeouts
import httpx

async def fetch_rss(url: str) -> list[dict]:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return parse_feed(resp.text)
    except Exception as e:
        record_source_failure(url, str(e))
        return []

# Circuit breaker state in SQLite
# Open after 5 consecutive failures
# Half-open probe every 15 minutes
# Expose via GET /api/source-health
```

---

### 3.3 Security 🟡

**Problem: No authentication on write endpoints.**

`PUT /api/watchlist`, `POST /api/refresh`, and all portfolio endpoints are publicly accessible. Anyone who finds the URL can reset the portfolio or spam refresh calls.

**Problem: No rate limiting on `/api/refresh`.**

A bot can trigger dozens of refreshes per minute, hammering Yahoo Finance and RSS sources.

**Fix:**

```python
# requirements.txt additions
slowapi>=0.1.9
python-dotenv>=1.0

# .env
API_KEY=your-secret-key-here

# FastAPI middleware
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.post("/api/refresh")
@limiter.limit("2/minute")
async def refresh(request: Request, x_api_key: str = Header(None)):
    if x_api_key != os.getenv("API_KEY"):
        raise HTTPException(status_code=401)
    ...
```

---

## 4. Signal Quality — Root Cause Analysis

The 34.7% accuracy (below the 40.1% random baseline) is caused by three compounding problems. Fixing any one will help; all three need addressing to reach the 55% target.

### Root Cause 1: Neutral predictions dominate (87%) 🔴

`record_predictions_from_events` records every event→ticker pair regardless of whether there's any directional signal. This means 87% of the backtest is neutral noise.

**Fix (one line):**
```python
def record_predictions_from_events(events, stocks):
    for event in events:
        for relationship in event["relationships"]:
            # Add this guard
            if relationship.get("impact_direction") == "neutral":
                continue
            record_prediction(event, relationship)
```

### Root Cause 2: No actual return data to evaluate 🔴

`return_7d` and `return_30d` are NULL for all 382 resolved predictions. Without knowing whether the stock moved the predicted direction at the predicted horizon, calibration is impossible.

**Fix:**
```python
def resolve_predictions(conn):
    pending = conn.execute(
        "SELECT id, ticker, predicted_at, signal_direction FROM predictions "
        "WHERE return_7d IS NULL AND predicted_at < datetime('now', '-7 days')"
    ).fetchall()

    for pred in pending:
        try:
            price_at_signal = get_price_at(pred["ticker"], pred["predicted_at"])
            price_7d_later  = get_price_at(pred["ticker"], pred["predicted_at"], offset_days=7)
            price_30d_later = get_price_at(pred["ticker"], pred["predicted_at"], offset_days=30)

            return_7d  = (price_7d_later  - price_at_signal) / price_at_signal
            return_30d = (price_30d_later - price_at_signal) / price_at_signal

            direction_correct_7d = (
                (pred["signal_direction"] == "positive" and return_7d > 0) or
                (pred["signal_direction"] == "negative" and return_7d < 0)
            )

            conn.execute(
                "UPDATE predictions SET return_7d=?, return_30d=?, is_correct=? WHERE id=?",
                (return_7d, return_30d, direction_correct_7d, pred["id"])
            )
        except Exception:
            pass  # Price fetch failed; skip, retry next cycle
```

### Root Cause 3: High-confidence scores are uncalibrated 🟡

High-confidence predictions hit 26.7% — worse than medium (53.3%) and low (42.7%). The scoring is likely over-weighting dramatic political events (corruption cases, cabinet reshuffles) which get high `impact_score` but historically don't move individual stocks predictably.

**Fix (after resolving issues 1 and 2):**
```python
# Compute per-category calibration multipliers from live backtest data
def compute_category_calibration(conn) -> dict[str, float]:
    rows = conn.execute("""
        SELECT event_category,
               COUNT(*) as n,
               AVG(CASE WHEN is_correct THEN 1.0 ELSE 0.0 END) as hit_rate
        FROM predictions
        WHERE origin = 'live' AND is_correct IS NOT NULL
        GROUP BY event_category
        HAVING COUNT(*) >= 15
    """).fetchall()

    baseline = 0.50
    return {
        row["event_category"]: row["hit_rate"] / baseline
        for row in rows
    }
```

### Root Cause 4: Support/resistance unused in signal scoring 🟡

`support` and `resistance` fields exist on each stock payload but don't feed into `trading_signals.py`. A stock near support with a positive event is a much stronger BUY setup.

**Fix (add to `trading_signals.py`):**
```python
def compute_sr_proximity_boost(stock: dict) -> float:
    price      = stock.get("price", 0)
    support    = stock.get("support")
    resistance = stock.get("resistance")

    if not price:
        return 1.0

    boost = 1.0
    if support and abs(price - support) / price <= 0.03:
        boost += 0.20   # within 3% of support → +20% boost for BUY
    if resistance and abs(price - resistance) / price <= 0.03:
        boost += 0.10   # near resistance → mild boost for SELL / breakout watch

    return boost
```

---

## 5. Infrastructure Gaps

### 5.1 No Dockerfile

The app runs via `python app.py` with no container. CPU-only PyTorch + Transformers means dependency installation is environment-sensitive and slow. A Dockerfile makes deploys reproducible.

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Persistent data directory
VOLUME ["/data"]
ENV POLSTOCKS_DB=/data/polstocks.db

EXPOSE 8001
CMD ["python", "app.py"]
```

```yaml
# docker-compose.yml
services:
  polstocks:
    build: .
    ports:
      - "8001:8001"
    volumes:
      - ./data:/data
    env_file: .env
    restart: unless-stopped
```

---

### 5.2 No CI/CD

There's no `.github/workflows/` folder. Tests don't run automatically on push. A broken import in `backend/*.py` could be deployed without detection.

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Syntax check
        run: python -m py_compile backend/*.py app.py

      - name: Run tests (NLP disabled for speed)
        run: POLSTOCK_ENABLE_ML_NLP=0 pytest tests/ -q --tb=short
```

---

### 5.3 Unpinned Dependencies

`requirements.txt` uses `>=` version bounds for all packages. A major version bump in FastAPI, transformers, or httpx could silently break the app.

```txt
# Before (fragile)
fastapi>=0.115
uvicorn>=0.30

# After (stable)
fastapi==0.115.6
uvicorn==0.30.6
requests==2.32.3
torch==2.6.0+cpu
transformers==4.44.2
safetensors==0.4.5
tokenizers==0.19.1
slowapi==0.1.9
structlog==24.4.0
```

---

### 5.4 No Structured Logging

Python `print()` or basic `logging` with no structured format makes production debugging hard.

```python
# Replace print() calls with:
import structlog

log = structlog.get_logger()

# In RSS fetcher:
log.info("rss_fetch_complete",
    source=source_name,
    articles=len(articles),
    latency_ms=int((time.time() - t0) * 1000),
    status="ok"
)

# In signal generator:
log.info("signal_classified",
    ticker=ticker,
    action=signal["action"],
    tier=signal["tier"],
    strength=round(signal["signal_strength"], 3)
)
```

---

## 6. Phased Roadmap

### Phase 6 — Actionable BUY signals & feedback loop *(current)*

**Goal:** Generate at least 1 Tier A/B BUY signal and close the feedback loop with actual return data.

| Task | File | Effort |
|---|---|---|
| Add `compute_sr_proximity_boost()` | `trading_signals.py` | 1h |
| Add `detect_bollinger_squeeze()` | `trading_signals.py` | 1h |
| Filter neutral predictions at recording | `signals.py` | 15min |
| Update `resolve_predictions()` to compute `return_7d`, `return_30d` | `signals.py` | 2h |
| Verify ≥1 BUY in `/api/signals/daily-summary` | manual | 30min |

**Acceptance criteria:**
- At least 1 BUY signal appears in `/api/signals/daily-summary` within 24h
- `by_signal_tier` in backtest shows non-empty A/B categories
- `return_7d` populated for predictions older than 7 days

---

### Phase 7 — Persistent storage & data durability

**Goal:** Ensure all predictions, signals, and calibration history survive restarts.

| Task | File | Effort |
|---|---|---|
| Move SQLite to `/data/polstocks.db` with env var | `backend/main.py` | 1h |
| Write migration runner | `migrations/runner.py` | 2h |
| Add Phase 10.1 & 10.2 ALTER TABLE migrations | `migrations/002_*.sql` | 1h |
| Add `source_accuracy` + `daily_signal_snapshots` tables | `migrations/003_*.sql` | 30min |
| Add daily backup cron | `scripts/backup.sh` | 15min |
| Validate data survives `systemctl restart` | manual | 15min |

---

### Phase 8 — Resilience & error handling

**Goal:** Make the system survive bad external conditions gracefully.

| Task | File | Effort |
|---|---|---|
| Add `httpx` with `timeout=10s` on all external calls | `sources.py`, `stocks.py` | 2h |
| Add per-source circuit breaker (open after 5 failures) | `sources.py` | 3h |
| Surface source health in `/api/source-health` | `backend/main.py` | 1h |
| Add `slowapi` rate limiter on `/api/refresh` | `backend/main.py` | 30min |
| Add API key header check on write endpoints | `backend/main.py` | 30min |
| Add `structlog` structured logging | all backend modules | 2h |

---

### Phase 9 — Source calibration & confidence recalibration

**Goal:** Fix inverted confidence scores (high-confidence currently worse than low).

| Task | File | Effort |
|---|---|---|
| Populate `source_accuracy` from live-only outcomes | `signals.py` | 2h |
| Compute per-category calibration multipliers | `scoring.py` | 2h |
| Add `/api/calibration/report` endpoint | `backend/main.py` | 1h |
| Wire multipliers into `compute_event_score()` | `trading_signals.py` | 2h |
| Investigate and fix high-confidence inversion | `trading_signals.py` | 3h |
| Add `/api/calibration/auto-apply` with n≥30 guard | `backend/main.py` | 1h |

---

### Phase 10 — CI/CD, Docker & production hardening

**Goal:** Make it deployable, maintainable, and observable.

| Task | File | Effort |
|---|---|---|
| Write `Dockerfile` + `docker-compose.yml` | root | 1h |
| Add GitHub Actions test CI | `.github/workflows/test.yml` | 30min |
| Add GitHub Actions deploy workflow | `.github/workflows/deploy.yml` | 1h |
| Pin all dependency versions | `requirements.txt` | 15min |
| Add CORS origin whitelist | `backend/main.py` | 15min |
| Add detailed `/healthz` response (DB, sources, last refresh) | `backend/main.py` | 1h |
| Add Telegram alert on 3 consecutive refresh failures | Telegram bot | 1h |

---

## 7. Quick Wins (Do These Today)

These can each be done in under 30 minutes with near-zero risk.

### 5-minute wins

**Pin dependency versions**
Change all `>=` bounds to exact versions in `requirements.txt`. Prevents surprise breakages when a new major version ships.

**Add `.env.example`**
Document all env vars: `POLSTOCK_ENABLE_ML_NLP`, `API_KEY`, `POLSTOCKS_DB`, `TELEGRAM_BOT_TOKEN`. Makes onboarding and self-hosting dramatically easier.

**Move runtime JSON files**
Move `data.json`, `watchlist.json`, `source_registry.json` to `/data/` or `/config/`. The root directory should contain only code, not runtime state.

---

### 30-minute wins

**Add request timeouts**
Add `timeout=10` to every `requests.get()` call. This single change prevents the most common "server hangs on refresh" class of bugs.

```python
# Before
resp = requests.get(url)

# After
resp = requests.get(url, timeout=10)
```

**Add GitHub Actions CI**
The `tests/` folder already exists. Add the workflow YAML so tests run on every push. Catches syntax errors and import failures before they hit production.

**Filter neutral predictions**
Single-line fix in `record_predictions_from_events`. Immediately improves backtest signal quality.

```python
if relationship.get("impact_direction") == "neutral":
    continue
```

**Make SQLite persistent**
Change the SQLite connection string from in-memory (or temp path) to a file-backed path controlled by an env var. Create `/data/` on startup if it doesn't exist.

```python
import os

DB_PATH = os.getenv("POLSTOCKS_DB", "/data/polstocks.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
```

---

## Success Metrics

| Metric | Now | Phase 6 target | Phase 9 target |
|---|---|---|---|
| Live non-neutral hit rate | ~43% | 50% | 55%+ |
| Edge vs neutral baseline | -9.4% | 0% | +5% |
| Tier A/B signals/week | 0 | ≥1 | 5–10 |
| SELL signal status | blocked (0% acc.) | still blocked | unblock when >baseline |
| Backtest return tracking | 0% populated | 100% for >7d old | — |
| Data durability | lost on restart | persistent | persistent + backed up |

---

*Generated: June 2026 | Repo: [aldimhr/polstocks](https://github.com/aldimhr/polstocks)*
