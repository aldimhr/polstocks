# Politics Stock Mapper

A lightweight Indonesia political-stock impact dashboard that follows the simplified spec:

- on-demand refresh with `/api/refresh`
- RSS + government source fetching
- stock quote fetching
- heuristic NLP fallback for sentiment, taxonomy, sector mapping, and impact scoring
- source-backed company knowledge layer for stricter policy-to-stock matching
- in-memory cache with 5 minute TTL
- watchlist endpoints
- browser dashboard with update controls and summary panels

## Run locally

```bash
cd /opt/hermes/politics_stock_mapper
python3 app.py
```

Then open `http://127.0.0.1:8000/`.

## API

- `GET /` – dashboard
- `GET /healthz` – health check
- `GET /api/watchlist` – current watchlist
- `PUT /api/watchlist` – replace watchlist
- `POST /api/refresh` – fetch + analyze + render-ready JSON

## Notes

- The app uses live public sources and returns cached results for up to 5 minutes.
- If live sources fail, the UI keeps the last cached result and surfaces warnings instead of silently showing demo rows.
- Watchlist state is persisted to `watchlist.json`; there is no database.
- Company-specific policy context is seeded in `company_knowledge.json` and used to reject broad sector-only matches.
