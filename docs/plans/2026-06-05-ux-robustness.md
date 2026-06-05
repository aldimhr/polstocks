# Plan: UX Robustness Layer

**Date:** 2026-06-05
**Status:** Planning
**Goal:** Make PolStock dashboard trustworthy, resilient, and pleasant to use in live conditions

## What Already Exists

| Component | Status |
|-----------|--------|
| Loading state (`setBusy`) | ✅ Shows message + disables button during fetch |
| Last updated time | ✅ `formatTime(payload.fetched_at)` |
| Window selector | ✅ 24h/7d/30d dropdown, persists in state |
| Watchlist editor | ✅ Add/remove/reset tickers, keyboard Enter |
| Ticker modal | ✅ Detail view per ticker |
| Reasoning summary chips | ✅ Relevance, stage, thread, validation breakdowns |
| Dashboard cues | ✅ Status badges (healthy/watch/fragile) |
| Sector grid | ✅ Sector-level impact visualization |
| Source diagnostic badges | ✅ Fallback, stale, thin coverage warnings |
| Provenance badges | ✅ Source type, tier, freshness, fetch status |

## Gaps Identified (Ranked by Impact)

### 🔴 HIGH IMPACT

#### Task 1: Error retry with exponential backoff
**Problem:** If the API call fails (timeout, network error, 500), the error shows as raw text in the status bar. No way to retry except manually clicking refresh.

**Fix:**
- Add retry logic (3 attempts, exponential backoff: 2s, 4s, 8s)
- Show "Retrying… (2/3)" in status bar
- After all retries exhausted, show "Failed — click to retry" with the refresh button pulsing
- Distinguish timeout vs network error vs server error in the message

**Where:** `loadDashboard()` in `dashboard.html`

**Tests:** Frontend-only, manual verification.

#### Task 2: Auto-refresh with staleness indicator
**Problem:** Data goes stale silently. User has to remember to click refresh.

**Fix:**
- Add 5-minute auto-refresh when page is visible (using `document.visibilityState`)
- Show a staleness bar that fills up over 5 minutes
- Pause auto-refresh when tab is hidden (save battery)
- Show "Auto-refreshed · 2m ago" or "Stale · 8m ago" in the footer
- Stop auto-refresh if last 3 attempts failed (avoid hammering a down server)

**Where:** `dashboard.html` — add `setInterval` in `DOMContentLoaded`, visual indicator in footer area.

**Tests:** Frontend-only.

#### Task 3: Skeleton loading states
**Problem:** On first load, the page shows empty tables and "Loading…" text. Doesn't give a sense of structure.

**Fix:**
- Show skeleton rows (pulsing gray bars) in the stock table and event list during initial load
- Replace skeletons with real content on first render
- Keep showing old data with a "Refreshing…" overlay during subsequent refreshes (don't blank the page)

**Where:** `renderStocks()`, `renderEvents()`, `loadDashboard()`

**Tests:** Frontend-only.

### 🟡 MEDIUM IMPACT

#### Task 4: URL hash state persistence
**Problem:** Refreshing the page or sharing a link loses the selected window and watchlist.

**Fix:**
- Encode `window` and optionally `watchlist` in the URL hash: `#window=7d&watchlist=BSDE.JK,BBCA.JK`
- On page load, read hash and apply
- Update hash on window change and watchlist change (debounced)

**Where:** `DOMContentLoaded`, `handleWindowChange()`, `persistWatchlist()`

**Tests:** Frontend-only.

#### Task 5: Toast notifications for user actions
**Problem:** Adding/removing tickers has no visible feedback beyond the watchlist chips updating.

**Fix:**
- Show a brief toast notification: "BSDE.JK added" / "BSDE.JK removed" / "Watchlist reset"
- Auto-dismiss after 3 seconds
- Show "Refreshing data…" toast when watchlist change triggers auto-refresh

**Where:** `addTicker()`, `removeTicker()`, `resetWatchlist()`

**Tests:** Frontend-only.

#### Task 6: Empty state guidance
**Problem:** When no events or stocks match, the page shows "No live events yet" and "No stock data yet" without guidance.

**Fix:**
- Show a helpful empty state card:
  - "No events found" → "Try expanding your watchlist or switching to a longer time window (7d, 30d)"
  - "No stock links" → "No political signals matched your tickers in this window. This is normal during quiet policy periods."
- Show a "Quick tips" section on first load when payload is empty

**Where:** `renderStocks()`, `renderEvents()`

**Tests:** Frontend-only.

#### Task 7: Scroll position preservation on refresh
**Problem:** Clicking refresh scrolls to top (or jumps due to DOM replacement).

**Fix:**
- Save `window.scrollY` before render
- Restore after render using `requestAnimationFrame`

**Where:** `loadDashboard()`, `renderPayload()`

**Tests:** Frontend-only.

### 🟢 LOW IMPACT

#### Task 8: Keyboard shortcuts
**Problem:** Power users want quick access.

**Fix:**
- `R` → trigger refresh
- `Escape` → close ticker modal
- `1/2/3` → switch window (24h/7d/30d)

**Where:** `document.addEventListener('keydown')` — already exists partially.

**Tests:** Frontend-only.

#### Task 9: Connection status indicator
**Problem:** No visual indicator of connection health.

**Fix:**
- Green dot = connected, last refresh < 5m
- Yellow dot = stale, last refresh 5-15m
- Red dot = error or > 15m stale
- Pulse animation during refresh

**Where:** Status bar area near `#lastUpdated`

**Tests:** Frontend-only.

#### Task 10: Refresh progress detail
**Problem:** During refresh, only "Refreshing live data…" shows. No sense of progress.

**Fix:**
- Show substeps: "Fetching news…", "Analyzing articles…", "Validating market data…", "Building dashboard…"
- This requires backend SSE or polling — may be complex. Simple version: just show elapsed time ("Refreshing… 3s")

**Where:** `loadDashboard()`, backend `/api/refresh`

**Tests:** Frontend + backend.

## Implementation Order

| Order | Task | Effort | Type |
|-------|------|--------|------|
| 1 | Task 1: Error retry with backoff | Small | JS |
| 2 | Task 2: Auto-refresh + staleness bar | Medium | JS + CSS |
| 3 | Task 3: Skeleton loading | Medium | JS + CSS |
| 4 | Task 9: Connection status indicator | Small | JS + CSS |
| 5 | Task 5: Toast notifications | Small | JS + CSS |
| 6 | Task 7: Scroll preservation | Trivial | JS |
| 7 | Task 4: URL hash persistence | Small | JS |
| 8 | Task 6: Empty state guidance | Small | JS + HTML |
| 9 | Task 8: Keyboard shortcuts | Trivial | JS |
| 10 | Task 10: Refresh progress detail | Medium | JS + backend |

## Notes
- Tasks 1-9 are all frontend-only (dashboard.html) — no backend changes needed
- Task 10 requires backend support (SSE or polling) — can be deferred
- All tasks are independent — can be implemented in any order
- Total estimated effort: ~2-3 hours for tasks 1-9
