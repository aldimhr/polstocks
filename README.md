# PolStock

PolStock is a **short-term trading signal assistant** for Indonesian stocks.

Its job is to help answer:

> **Which tickers are bullish right now, backed by strong participation, and tradable over the next 1–14 days?**

The project is now being refocused away from a news-first political-impact dashboard into a **short-swing signal engine** where market structure and participation lead, and news/policy context acts as an optional conviction boost.

## Product direction

PolStock v1 should prioritize:

- **actionable long setups** over broad event summaries
- **1d / 3d / 7d / 14d** holding windows
- **bullish continuation or rebound setups** over generic sentiment labels
- **participation-backed signals** using volume and traded-value proxies
- **clear trade plans**: entry, stop-loss, take-profit, invalidation, reasons

## v1 scope

### Supported signal types

- **breakout continuation**
  - price pushes through / above resistance
  - bullish momentum confirmation
  - participation expansion required

- **support rebound**
  - price near support
  - oversold-to-recovery behavior
  - momentum improving

- **squeeze breakout watch**
  - volatility compression / Bollinger squeeze
  - bullish pressure building
  - upgraded to BUY only when trigger + participation confirm

- **news-accelerated breakout**
  - a valid bullish technical setup gets extra conviction from relevant news/policy context
  - news alone must not create a BUY signal

### v1 signal actions

- `BUY` — bullish setup complete, participation confirmed, risk-defined
- `WATCH` — setup forming, but trigger or participation is incomplete
- `IGNORE` — no actionable edge

### v1 horizon targets

- `1d` — urgent breakout / ignition setup
- `3d` — fresh continuation setup
- `7d` — standard short swing
- `14d` — slower but still actionable bullish development

## v1 non-goals

These are intentionally out of scope for the first usable version:

- production-grade short-selling / bearish strategy optimization
- news-only BUY signals without technical confirmation
- ranking stocks primarily by political relevance instead of trade actionability
- pretending transaction-count data exists when the data source only supports volume/value proxies

## Current architecture focus

PolStock still includes:

- on-demand refresh with `/api/refresh`
- Indonesian market/news ingestion
- stock quote + technical indicator fetching
- event/news analysis and policy-context enrichment
- signal history, snapshots, backtest infrastructure, and dashboard/bot surfaces

But the **primary decision layer is being shifted** toward:

```text
Bullish market structure
+ momentum confirmation
+ participation expansion
+ trigger quality
+ optional event/news boost
= short-term trade signal
```

## Run locally

```bash
cd /opt/hermes/politics_stock_mapper
python3 app.py
```

Then open `http://127.0.0.1/`.

## API

Core endpoints currently in the backend include:

- `GET /` – dashboard
- `GET /healthz` – health check
- `GET /api/dashboard` – consolidated dashboard payload
- `POST /api/refresh` – fetch + analyze + render-ready JSON
- `GET /api/signals/daily-summary` – grouped daily signal summary
- `GET /api/signals/ticker/{ticker}` – ticker-level signal explanation
- `GET /api/signals/history` – persisted signal history
- `GET /api/backtest` – backtest metrics
- `GET /api/watchlist` / `PUT /api/watchlist` – watchlist state

## Documentation

- `docs/short-term-signal-spec.md` — current product spec for the short-term signal pivot
- `docs/SPEC.md` — broader legacy/refocus analysis and implementation inventory
- `docs/ARCHITECTURE.md` — current system architecture overview (will be updated further as the pivot lands)

## Notes

- The app uses live public data and caches refresh results for a short TTL.
- If transaction-count data is unavailable from the upstream source, v1 uses **volume and traded-value proxies** and labels them honestly.
- Relevant news/policy context still matters, but it should **boost** a valid setup rather than **create** one on its own.
- The project should not claim a setup is “actionable” unless the signal has clear entry, risk, and holding-window logic.
