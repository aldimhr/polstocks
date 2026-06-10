# Portfolio Trade History Section

## Goal
Add a Portfolio-tab section that shows the user's trade lifecycle clearly: when a position was bought/opened, when it was sold/closed, and realized P&L for completed trades.

## Existing data sources
- `/api/portfolio/live` returns open positions with `entry_date`, entry price, lots/shares, and live unrealized P&L.
- `/api/portfolio/history` returns closed positions with `entry_date`, `exit_date`, entry/exit prices, realized P&L, and summary stats.

## UX
1. Keep the existing portfolio summary and open positions list.
2. Add a `Trade History` card area under open positions.
3. Show summary chips: closed trades, wins/losses, win rate, realized P&L.
4. Render a timeline:
   - `BUY` / opened events from entry data
   - `SELL` / closed events from exit data for closed positions
   - current open positions as `OPEN`
5. Use friendly empty states; no fake/sample production data.

## Verification
- Dashboard hook regression test includes trade-history DOM/function hooks.
- Existing portfolio add/close API test also validates `/api/portfolio/history` exposes entry/exit dates and realized P&L.
- Inline JavaScript parses.
- Running dashboard endpoint serves the new section.
