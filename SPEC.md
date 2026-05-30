# SPEC.md
# Indonesia Political-Stock Impact System — Specification (Simplified)

---

## 1. Purpose

A lightweight on-demand dashboard that lets users check how current Indonesian political events may be impacting IDX-listed stocks. The user manually triggers a refresh; the system fetches fresh news and stock prices, runs NLP analysis, and displays the results.

---

## 2. Scope

### In Scope
- Indonesian national-level political events from free public news RSS feeds
- IDX-listed stocks (user-configurable watchlist, default: top 30 by market cap)
- On-demand fetch triggered by user action (no automatic polling)
- Sentiment analysis in Bahasa Indonesia
- Sector-level impact summary across 11 IDX sectors

### Out of Scope
- Real-time streaming or WebSocket connections
- Persistent database or historical data storage
- User accounts or authentication
- Regional politics unless involving nationally listed companies

---

## 3. Data Sources (Free & Public)

### 3.1 Political & News Data (RSS Feeds)

| Source | RSS URL | Notes |
|---|---|---|
| Antara News | `https://www.antaranews.com/rss/terkini.rss` | State news agency; most authoritative |
| CNBC Indonesia | `https://www.cnbcindonesia.com/rss` | Finance & politics focus |
| Kompas | `https://rss.kompas.com/nasional` | Major national newspaper |
| Detik Finance | `https://finance.detik.com/rss` | Finance + political economy |
| Tempo | `https://rss.tempo.co/nasional` | Investigative political news |
| BeritaSatu | `https://www.beritasatu.com/rss` | Business & political news |

### 3.2 Government Sources (HTTP scrape / RSS, checked on refresh)

| Source | URL | Data |
|---|---|---|
| Sekretariat Kabinet | `https://setkab.go.id` | Presidential press releases |
| OJK | `https://www.ojk.go.id` | Financial regulation news |
| KPK | `https://www.kpk.go.id/id/berita/siaran-pers` | Corruption case announcements |

### 3.3 Stock Data

| Source | Library / URL | Data | Notes |
|---|---|---|---|
| Yahoo Finance | `yfinance` Python library | Current price, OHLCV, % change | Ticker format: `BBCA.JK` |
| IDX Official | `https://www.idx.co.id` | Ticker list, sector classification | Scraped once on startup |
| Stooq | `pandas-datareader` + stooq | Historical OHLCV fallback | No API key needed |

---

## 4. Functional Requirements

### 4.1 Update Button

| ID | Requirement |
|---|---|
| F-01 | User SHALL see a prominent "Update" button on the dashboard |
| F-02 | Clicking Update SHALL trigger a single backend request |
| F-03 | If the last successful fetch was less than 5 minutes ago, the backend SHALL return the cached result without re-fetching |
| F-04 | The UI SHALL show a loading spinner during fetch and a "Last updated: HH:MM WIB" timestamp after |
| F-05 | If fetch fails (network error, rate limit), the UI SHALL show an error toast and retain the previous result |

### 4.2 News Fetching

| ID | Requirement |
|---|---|
| F-10 | On each non-cached refresh, the system SHALL fetch up to the latest 80 articles from each configured RSS source |
| F-11 | Articles older than the selected window (`24h`, `7d`, or `30d`) SHALL be excluded from analysis |
| F-12 | Duplicate articles (same URL or >90% title similarity) SHALL be deduplicated |
| F-13 | Fetch timeout per source SHALL be 5 seconds; failed sources SHALL be skipped gracefully |

### 4.3 Stock Data Fetching

| ID | Requirement |
|---|---|
| F-20 | On each non-cached refresh, the system SHALL fetch current quotes for all tickers in the user's watchlist |
| F-21 | Each ticker response SHALL include: current price (IDR), % change today, volume, and sector |
| F-22 | If yfinance returns stale data (>30 min old outside trading hours), it SHALL be marked as "after-hours" |
| F-23 | Default watchlist SHALL be the top 30 IDX stocks by market cap (LQ45 index composition) |

### 4.4 NLP Analysis

| ID | Requirement |
|---|---|
| F-30 | Each article SHALL be scored for sentiment: positive / negative / neutral with a score of -1.0 to +1.0 |
| F-31 | Each article SHALL be classified into one or more political categories (see Section 6) |
| F-32 | Named entities SHALL be extracted: persons, organizations, commodities, laws |
| F-33 | Each article SHALL be mapped to one or more IDX sectors based on content |
| F-34 | An impact score SHALL be computed per (article, ticker) pair (see Section 7) |
| F-35 | NLP processing SHALL complete within 10 seconds for a batch of 100 articles |

### 4.5 Dashboard Display

| ID | Requirement |
|---|---|
| F-40 | Stock cards SHALL display: ticker, company name, current price, % change today, and political impact score |
| F-41 | Impact score SHALL be color-coded: red (negative), green (positive), grey (neutral) |
| F-42 | Event feed SHALL list the top 10 most politically significant articles from the current refresh and selected window |
| F-43 | Each event SHALL show: headline, source, published time, political category badge, impacted tickers, and evidence context |
| F-44 | A sector summary bar SHALL show average impact score per IDX sector |
| F-45 | User SHALL be able to add/remove tickers from their watchlist |
| F-46 | User SHALL be able to switch between `24h`, `7d`, and `30d` windows from the dashboard |
| F-47 | Refresh payloads SHALL include daily event-tracking aggregates plus top themes and sources for the selected window |

---

## 5. Non-Functional Requirements

| Category | Requirement |
|---|---|
| Response time | Full refresh (fetch + NLP + response) SHALL complete within 15 seconds |
| Cache TTL | In-memory cache expires after 5 minutes |
| Concurrent users | Designed for single-user or small team use (no concurrency handling required for v1) |
| Offline behaviour | If all sources fail, display last cached result with a warning |
| Deployment | Runnable locally with `docker compose up` on any machine with 8GB RAM |

---

## 6. Political Event Taxonomy

| Category | Description | Primarily Impacted IDX Sectors |
|---|---|---|
| `CABINET_RESHUFFLE` | Minister appointment or dismissal | All sectors |
| `REGULATION_NEW` | New law, PP, Perpres, or Permen | Sector-specific |
| `REGULATION_REPEAL` | Law or regulation revoked | Sector-specific |
| `ELECTION_EVENT` | Election milestones, results, campaigns | All sectors |
| `CORRUPTION_CASE` | KPK investigation, arrest, or verdict | Named company + sector |
| `STATE_BUDGET` | APBN proposal, revision, or approval | Finance, Infrastructure |
| `TRADE_POLICY` | Export/import bans, tariffs | Consumer Goods, Basic Materials |
| `ENERGY_POLICY` | Mining quotas, oil/gas pricing, renewables | Energy, Basic Materials |
| `INVESTMENT_POLICY` | FDI rules, investment restriction changes | Finance, Infrastructure |
| `MONETARY_SIGNAL` | Bank Indonesia rate decision, inflation | Finance, Property |
| `PARLIAMENT_SESSION` | DPR bill vote, committee hearing | Sector-specific |
| `PROTEST_UNREST` | Labor strikes, mass protests | Consumer, Infrastructure |

---

## 7. Impact Scoring Formula

```
ImpactScore(article, ticker) =
    sentiment_score                    ← -1.0 to +1.0 from IndoBERT
  × sector_relevance_weight            ← 1.0 if sector directly named, 0.5 if adjacent
  × entity_mention_boost               ← 1.5 if company name appears in article, else 1.0
  × model_confidence                   ← 0.0 to 1.0
```

Final score is clamped to [-1.0, +1.0].

Per-ticker score across all current articles = weighted average, heavier weight on more recent articles.

---

## 8. API Contract

### `POST /api/refresh`

**Request**
```json
{
  "tickers": ["BBCA.JK", "TLKM.JK", "ASII.JK"],
  "force": false,
  "window": "7d"
}
```
- `force: true` bypasses the 5-minute cache

**Response**
```json
{
  "fetched_at": "2025-05-30T09:15:00+07:00",
  "from_cache": false,
  "window": "7d",
  "window_label": "7 hari terakhir",
  "events": [
    {
      "id": "evt_001",
      "headline": "Pemerintah larang ekspor batu bara",
      "source": "antaranews.com",
      "url": "https://...",
      "published_at": "2025-05-30T08:00:00+07:00",
      "categories": ["ENERGY_POLICY", "TRADE_POLICY"],
      "sentiment": "negative",
      "sentiment_score": -0.72,
      "impacted_sectors": ["Energy", "Basic Materials"],
      "impacted_tickers": ["ADRO.JK", "PTBA.JK"]
    }
  ],
  "stocks": [
    {
      "ticker": "BBCA.JK",
      "name": "Bank Central Asia",
      "sector": "Finance",
      "price": 9500,
      "change_pct": -0.52,
      "volume": 12500000,
      "impact_score": -0.18,
      "related_event_ids": ["evt_003"]
    }
  ],
  "sector_summary": {
    "Energy": -0.65,
    "Finance": -0.18,
    "Consumer Goods": 0.10
  }
}
```

### `GET /api/watchlist`
Returns current watchlist tickers.

### `PUT /api/watchlist`
```json
{ "tickers": ["BBCA.JK", "GOTO.JK"] }
```
Updates watchlist (stored in memory for session).

---

## 9. NLP Model

- **Model**: `indobenchmark/indobert-base-p2` via HuggingFace Transformers
- **Tasks**: Sentiment classification, named entity recognition, political category multi-label classification
- **Language**: Bahasa Indonesia
- **Inference**: Runs locally on CPU (no GPU required for v1); ~0.5s per article
- **Fallback**: If model fails to load, use keyword-based rule classifier as fallback

---

## 10. Default Watchlist (LQ45 Top 30)

```
BBCA.JK, BBRI.JK, BMRI.JK, TLKM.JK, ASII.JK,
GOTO.JK, BYAN.JK, ADRO.JK, UNVR.JK, ICBP.JK,
PTBA.JK, ANTM.JK, INDF.JK, SMGR.JK, KLBF.JK,
HMSP.JK, PGAS.JK, JSMR.JK, EXCL.JK, INCO.JK,
TOWR.JK, MNCN.JK, ITMG.JK, HRUM.JK, BSDE.JK,
CPIN.JK, JPFA.JK, ESSA.JK, BRPT.JK, MEDC.JK
```

---

## 11. Development Phases

### Phase 1 — Core (Weeks 1–2)
- [ ] FastAPI backend with `/api/refresh` endpoint
- [ ] RSS fetcher with deduplication
- [ ] yfinance stock data integration
- [ ] In-memory cache with 5-minute TTL
- [ ] Basic Next.js dashboard with Update button + stock cards

### Phase 2 — NLP (Weeks 3–4)
- [ ] IndoBERT sentiment pipeline
- [ ] Political category classifier
- [ ] Sector + ticker impact mapping
- [ ] Impact score calculation
- [ ] Event feed on dashboard with category badges

### Phase 3 — Polish (Week 5)
- [ ] Sector summary bar
- [ ] Watchlist editor
- [ ] Error handling (failed sources, timeouts)
- [ ] "Last updated" timestamp + loading state
- [ ] Docker Compose packaging

---

## 12. Glossary

| Term | Definition |
|---|---|
| IDX | Indonesia Stock Exchange (Bursa Efek Indonesia / BEI) |
| WIB | Waktu Indonesia Barat — Western Indonesia Time (UTC+7) |
| LQ45 | IDX index of 45 most liquid stocks; used as default watchlist basis |
| DPR RI | Dewan Perwakilan Rakyat — Indonesian House of Representatives |
| OJK | Otoritas Jasa Keuangan — Financial Services Authority |
| KPK | Komisi Pemberantasan Korupsi — Corruption Eradication Commission |
| IndoBERT | Pre-trained BERT model for Bahasa Indonesia (HuggingFace) |
| OHLCV | Open, High, Low, Close, Volume — standard stock price fields |
| TTL | Time To Live — cache expiry duration |
