# ARCHITECTURE.md
# Indonesia Political-Stock Impact System (Simplified)

## Overview

A lightweight on-demand FastAPI web app that serves a static HTML dashboard, fetches Indonesian political news and current IDX stock prices when the user clicks "Update", then analyzes the political impact on stocks using NLP. No streaming, no message queues, no external databases.

---

## System Architecture Diagram

```
┌─────────────────────────────────────────────────────┐
│             DASHBOARD (static HTML + JS)             │
│                                                     │
│   [ Update Button ] ──► triggers API call           │
│   [ Stock Cards   ] ◄── renders impact results      │
│   [ Event Feed    ] ◄── renders political events    │
└─────────────────────┬───────────────────────────────┘
                      │  HTTP POST /api/refresh
                      ▼
┌─────────────────────────────────────────────────────┐
│                  BACKEND (FastAPI)                   │
│                                                     │
│  ┌─────────────┐   ┌─────────────┐                 │
│  │ RSS Fetcher │   │  yfinance   │                 │
│  │ (news feed) │   │ (stock data)│                 │
│  └──────┬──────┘   └──────┬──────┘                 │
│         └────────┬─────────┘                       │
│                  ▼                                  │
│         ┌────────────────┐                         │
│         │  NLP Engine    │                         │
│         │  (IndoBERT)    │                         │
│         │  sentiment +   │                         │
│         │  sector map    │                         │
│         └───────┬────────┘                         │
│                 ▼                                   │
│         ┌────────────────┐                         │
│         │  In-Memory     │                         │
│         │  Cache (dict)  │  ← stores last result   │
│         └───────┬────────┘                         │
└─────────────────┼───────────────────────────────────┘
                  │  JSON response
                  ▼
            Frontend renders
```

---

## Component Breakdown

| Component | Technology | Responsibility |
|---|---|---|
| Dashboard | Static HTML + vanilla JS | UI, Update button, window selector, watchlist editor, compact reasoning badges, renders results |
| Backend | FastAPI (Python) | Serves dashboard, orchestrates fetch → NLP → response |
| RSS Fetcher | `requests` + XML parsing | Pulls latest political news from free RSS feeds |
| Stock Fetcher | Yahoo Finance chart endpoint | Gets current prices for IDX tickers (`.JK`) and IHSG |
| NLP Engine | Heuristic rules + scored relevance | Sentiment, political-relevance scoring, sector/theme classification, freshness-aware source quality scoring, and transmission-path directionality in Bahasa Indonesia |
| Company Knowledge Layer | `company_knowledge.json` | Stores company-specific policy channels, exposure factors, evidence source types, and evidence URLs |
| Policy Rules Layer | `policy_signal_rules.json` | Institution/legal/action vocab used by the scored political relevance gate |
| Market Validation Config | `market_validation_config.json` | Threshold scaffold for later predicted-vs-confirmed market validation |
| Event Tracking Layer | in-memory aggregation | Builds daily buckets and top themes/sources for 24h/7d/30d windows |
| In-Memory Cache | Python `dict` | Stores last fetch result per watchlist + window; avoids redundant calls |

---

## Request Flow

```
1. User clicks "Update" on the dashboard

2. Dashboard sends:
   POST /api/refresh
   Body: { tickers: ["BBCA.JK", "TLKM.JK", ...], window: "24h" | "7d" | "30d" }

3. Backend checks cache:
   - If last fetch < 5 minutes ago for the same watchlist + window → return cached result immediately
   - Otherwise → proceed to fetch

4. RSS Fetcher pulls latest articles from configured news sources

5. yfinance fetches current OHLCV for requested tickers

6. NLP Engine processes each article:
   - Political relevance score + label from institution/legal/action signals
   - Event stage + reversal flags (proposal / approved / effective / revoked, etc.)
   - Sentiment score (-1.0 to +1.0)
   - Political category (e.g. ENERGY_POLICY, CORRUPTION_CASE)
   - Company linking through two strict paths only: direct mention or matched company-specific policy channel
   - Transmission-path outputs such as matched policy channels, channel confidence, and per-company impact direction
   - Evidence tier (government / regulator / company / media / profile / other)
   - Freshness-aware source quality scoring and coverage warnings when evidence is stale, thin, or duplicated

7. Event Tracking layer groups surviving events into daily buckets and top theme/source summaries for the selected window, while the refresh payload also computes a compact `reasoning_summary` for relevance, stage, thread, direction, validation, and source-coverage breakdowns

8. Impact scores computed per (event, ticker) pair

9. Result stored in memory cache with timestamp

10. JSON response returned to frontend

11. Dashboard renders updated stock cards + event feed + tracking summary + compact reasoning badges
```

---

## Folder Structure

```
project/
├── backend/
│   └── main.py              # FastAPI app + fetch/analyze endpoints
├── company_knowledge.json   # Company-level exposure facts, channels, and evidence URLs
├── policy_signal_rules.json # Political relevance and event-stage vocabulary
├── market_validation_config.json # Thresholds for later market confirmation work
├── dashboard.html           # Static dashboard UI served at /
├── watchlist.json           # Persisted watchlist state
├── tests/
│   └── test_app.py          # API and UI contract tests
├── SPEC.md
├── ARCHITECTURE.md
└── README.md
```

---

## Infrastructure

Single-process deployment is enough for the current implementation:

```
[FastAPI]
  └── serves dashboard.html + JSON API on port 80
```

No Kafka. No Flink. No database. No Redis.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11+ (backend), HTML/CSS/vanilla JS (frontend) |
| Backend Framework | FastAPI |
| News Fetching | `feedparser`, `httpx` |
| Stock Data | Yahoo Finance chart endpoint via `requests` |
| NLP | Heuristic rules (current implementation) |
| Frontend | Static HTML, CSS, vanilla JS |
| Cache | Python in-memory dict (no external store) |
| Containerization | Optional; single service is sufficient |
