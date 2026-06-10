# PolStock Dashboard / UX Polish — Implementation Plan

> **For Hermes:** Implement task-by-task, commit after each.

**Goal:** Improve the PolStock dashboard with Telegram theme integration, event search/filter, and a source health panel.

**Architecture:** All changes are in the frontend (dashboard.html and miniapp.html). No backend changes needed — the API already returns all the data we need.

---

## Task 1: Telegram theme integration in Mini App

**Objective:** When opened inside Telegram, override CSS variables with Telegram's theme colors so the dashboard feels native.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/miniapp.html`
- Modify: `/opt/hermes/politics_stock_mapper/dashboard.html` (add postMessage listener)

**Changes to miniapp.html:**
- Already sends `tg_init` postMessage — no changes needed

**Changes to dashboard.html:**
- Add a `window.addEventListener('message', ...)` listener
- When receiving `tg_init`, apply Telegram theme params to CSS variables:
  - `tg.themeParams.bg_color` → `--bg`
  - `tg.themeParams.secondary_bg_color` → `--surface`
  - `tg.themeParams.text_color` → `--text`
  - `tg.themeParams.hint_color` → `--muted`
  - `tg.themeParams.button_color` → `--accent`
  - `tg.themeParams.button_text_color` → (accent text)
  - `tg.themeParams.link_color` → `--blue`
  - `tg.themeParams.destructive_text_color` → `--red`
- Only override if values exist (graceful fallback to default dark theme)
- Add a small Telegram user badge in the header when in Mini App mode

**Verify:** Open Mini App in Telegram → colors should match Telegram's dark theme.

---

## Task 2: Event search and filter bar

**Objective:** Add a search input + category filter chips above the event list so users can quickly find relevant events.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/dashboard.html`

**Changes:**
1. Add CSS for the search bar (`.event-search-bar`, `.filter-chip`)
2. Add HTML above `#eventList`:
   ```html
   <div class="event-search-bar">
     <input id="eventSearch" type="text" placeholder="Search events…">
     <div class="filter-chips" id="filterChips">
       <button class="filter-chip active" data-filter="all">All</button>
       <button class="filter-chip" data-filter="REGULATION">Regulation</button>
       <button class="filter-chip" data-filter="ENERGY">Energy</button>
       <button class="filter-chip" data-filter="TRADE">Trade</button>
       <button class="filter-chip" data-filter="MONETARY">Monetary</button>
       <button class="filter-chip" data-filter="CORRUPTION">Corruption</button>
       <button class="filter-chip" data-filter="BUDGET">Budget</button>
     </div>
   </div>
   ```
3. Add JS: filter events by search text (headline) and category
   - `state.eventFilter = { text: '', category: 'all' }`
   - `renderEvents()` respects filters
   - Debounce search input (300ms)
   - Clicking a filter chip toggles it
   - "All" chip resets filters
   - Filter count badge shows "X of Y events"

**Verify:** Type in search box → events filter in real-time. Click category chip → only matching events show.

---

## Task 3: Source health panel

**Objective:** Show which sources are healthy, stale, or failing to help diagnose data quality.

**Files:**
- Modify: `/opt/hermes/politics_stock_mapper/dashboard.html`

**Data source:** The API already returns `dashboard_cues` with:
- `chips[].label` and `chips[].tone` — info/warn/error chips
- `counts.source_count`, `counts.fallback_source_count`
- Per-event: `source_freshness_score`, `source_quality_score`, `source_fetch_status`

**Changes:**
1. Add CSS for `.source-health-panel` (compact grid of source badges)
2. Add HTML in the footer area (below sector heatmap):
   ```html
   <div class="card source-health-card">
     <div class="card-header">
       <span class="card-title">Source Health</span>
       <span class="card-badge" id="sourceBadge">0 sources</span>
     </div>
     <div class="source-health-grid" id="sourceHealthGrid">
       Loading source data…
     </div>
   </div>
   ```
3. Add JS: `renderSourceHealth(payload, cues)`:
   - Aggregate unique sources from events
   - For each source, show: name, freshness score (color-coded), quality score, fetch status
   - Green/Yellow/Red badge per source based on freshness + quality
   - Show fallback count as a warning chip

**Verify:** Open dashboard → source health panel shows at bottom with color-coded badges.

---

## Task 4: Commit and push

```bash
cd /opt/hermes/politics_stock_mapper
git add -A
git commit -m "feat: Telegram theme integration, event search/filter, source health panel"
git push origin main
```
