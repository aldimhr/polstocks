# Grouped Top-10 Ticker List UX

## Goal
Make the all-stocks list friendlier for mobile users by grouping tickers by industry/sector and showing only the top 10 rows for the current search/sort filter by default, with a clear button to show all.

## Current behavior
- `renderStocks()` renders a flat Stockbit-like row list.
- Search filters ticker/company/sector/signal fields.
- Sort buttons determine ranking (`Signal`, `Mover`, `Price`, `A-Z`).

## New behavior
1. Add dashboard state `stockShowAll: false`.
2. Keep the existing search and sort semantics.
3. After filtering + sorting, show top 10 by default.
4. Group visible rows by sector/industry with compact headers.
5. Add a `Show all N` button when more than 10 rows match.
6. Add `Show top 10` button when expanded.
7. Reset to top-10 mode when the search query or sort changes.
8. Preserve row click/ticker modal behavior and keyboard accessibility.

## Verification
- Dashboard hook test checks grouping/toggle helper names and DOM hooks.
- Inline JS parses with `node --check`.
- `tests/test_app.py` passes.
- Running dashboard endpoint serves the new grouped list hooks.
