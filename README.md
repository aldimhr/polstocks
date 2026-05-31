# Politics Stock Mapper

A lightweight Indonesia political-stock impact dashboard that follows the simplified spec:

- on-demand refresh with `/api/refresh`
- RSS + government source fetching
- stock quote fetching
- heuristic NLP fallback for sentiment, taxonomy, sector mapping, and impact scoring
- source-backed company knowledge layer for stricter policy-to-stock matching
- scored political relevance classification using `policy_signal_rules.json`
- event-stage detection and reversal flags for policy articles
- additive market-validation layer in `market_validation_config.json` to label links as `predicted_only`, `confirmed`, `rejected`, or `insufficient_data`
- in-memory cache with 5 minute TTL
- watchlist endpoints
- selectable event windows (`24h`, `7d`, `30d`) with tracking summaries
- evidence-hierarchy scoring across article and company sources
- browser dashboard with update controls and summary panels

## Run locally

```bash
cd /opt/hermes/politics_stock_mapper
python3 app.py
```

Then open `http://127.0.0.1/`.

## API

- `GET /` – dashboard
- `GET /healthz` – health check
- `GET /api/watchlist` – current watchlist
- `GET /api/dashboard` – consolidated dashboard payload (optional `?window=24h|7d|30d`)
- `PUT /api/watchlist` – replace watchlist
- `POST /api/refresh` – fetch + analyze + render-ready JSON (`window` supports `24h`, `7d`, `30d`)

## Notes

- The app uses live public sources and returns cached results for up to 5 minutes.
- You can switch the dashboard between the last 24 hours, last 7 days, and last 30 days to track what happened over different windows.
- Refresh payloads now include `tracking` aggregates (daily buckets, top themes, top sources) plus evidence-tier metadata for explainability.
- Political relevance is no longer a raw keyword check: the backend scores institution/legal/action signals and returns `relevance_score` plus `relevance_label` on each analyzed event.
- Stock relationships and stock rows now carry market-reaction validation fields like `validation_status`, `validation_window`, `abnormal_return`, `abnormal_volume_ratio`, and `validation_score` so the UI can distinguish text prediction from observed follow-through; statuses include `unvalidated`, `predicted_only`, `confirmed`, `rejected`, and `insufficient_data`.
- If live sources fail, the UI keeps the last cached result and surfaces warnings instead of silently showing demo rows.
- Watchlist state is persisted to `watchlist.json`; there is no database.
- Company-specific policy context is seeded in `company_knowledge.json` and used to reject broad sector-only matches.
