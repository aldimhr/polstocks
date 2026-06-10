# Stockbit-like All Stocks List UX

## Goal
Make the PolStock stock list feel closer to Stockbit's all-stocks view: fast to scan on mobile, searchable, sortable, and centered on short-term signal usefulness.

## Scope
- `dashboard.html` only unless tests reveal a missing backend field.
- Preserve existing ticker detail modal behavior.
- Keep existing sort options but make them feel like app chips.
- Use current payload fields: ticker, name, sector, price, change_pct, impact_score, relationship metadata, trading_signal, pinned/in_portfolio, technical chips.

## Tasks
1. Add lightweight HTML hooks for a Stockbit-style list shell:
   - search input
   - count/status line
   - card/list container replacing the desktop-first table
2. Add CSS for compact app rows:
   - left: ticker, company, sector/link metadata
   - right: price, daily change, impact/signal badge
   - horizontal chips for reasoning/technical cues
   - mobile-safe tap targets
3. Add JS state/filtering:
   - `stockSearchQuery`
   - filter by ticker, company, sector, relationship type
   - preserve sort buttons and ticker modal click behavior
4. Regression checks:
   - dashboard contains stock-list hooks/classes
   - inline JS parses
   - served dashboard exposes the new hooks
5. Commit and push a scoped dashboard/doc change.

## Non-goals
- Do not add fake market data.
- Do not copy Stockbit branding/assets.
- Do not change backend scoring or signal generation.
