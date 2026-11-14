# Market data dashboards — features, options and controls

Two screens, two stages (see `docs/WORKFLOW.md`):

## Stage 1 · Market data › Dashboard — collect and watch (0.29.0)

| Area | Features, options, controls |
| --- | --- |
| **Workspaces** | Tabs; New (empty), Duplicate, One per source, Rename (double-click), Close; "all sources" mode follows new sources, or a fixed, ordered list. Saved for the user. |
| **Workspace controls** | Sources in workspace (searchable, grouped by provider · technology), status (live, stale, no price, paused), minimum quality, asset class, search, 27 columns, sort, expand/collapse all, refresh 0.5–5 s, local/UTC, freeze display, CSV, *Analyse → Market intelligence* |
| **Health strip** | Sources live, instruments live, stale, no price, paused, errors, average quality, latency p95, prices per minute |
| **Source section** | Provider · technology · server · environment · collector state · address · lag · instruments · prices/min · quality; problem in plain words. **+ Instrument** (every collection-plan option: purpose, feed role, instrument link, data types, bar timeframes, depth, conflation, schedule and window, storage days, archive, history, quality thresholds, size estimate, start/pause/archive with approval), **Pause/Resume source**, **Backfill** (timeframe, range, reason, which instruments), **Details** (account, login, server, role, collector, heartbeat, backlog, server clock, plans, last error), **Connectivity**, move up/down, remove from workspace, collapse |
| **Available tab** (0.30.0) | Every symbol the provider offers (MT5 at login, Upstox daily file, FIX security list): class chips with counts, exchange, segment/group, collected or not, search, paging; pick one or many, edit the suggested instrument name, collect with purpose, role, quotes/bars/depth, timeframes, days kept, archive, link instruments, start now; *Refresh symbols* |
| **Instrument rows** | Quote and health columns plus plan status, purpose/role, stored data, days kept, schedule; actions: chart + time & sales (drill-down), settings (full plan editor), pause/start, remove (history kept) |
| **Events** | Events of the workspace's sources |

## Stage 2 · Market intelligence › Select & compare

Market data › **Dashboard**. Built for many providers (20+) and instruments (100+): nothing is limited to a fixed
list, and every selection is a search-and-pick list that grows with the configuration.

## Built (0.28.0)

| Area | Feature | Notes |
| --- | --- | --- |
| **Workspaces** | Tabs; New, Duplicate, One per source, Rename (double-click), Close | Each keeps everything below; saved for the user (follows to other devices) |
| **Selection** | **Sources** picker | Grouped by provider · technology; search; select a whole provider; *only*; shows environment, symbols priced/planned, collector state. Empty = all (new sources join automatically) |
| | **Instruments** picker | Grouped by asset class (FX, Metals, Indices, Commodities, Crypto, Equities, Futures, Options); the instrument register plus every collected broker symbol; *not collected* marked |
| **Filters** | Environment (all/demo/live), feed role (primary, backup, monitoring, research, reference, no plan), status (live, stale, no price), minimum quality, free-text search (`/`) | |
| **Quote board** | 26 columns to show/hide: symbol, instrument, class, source, provider, technology, environment, bid, ask, mid, last, spread, average spread, change %, change points, open, high, low, day range, age, latency, latency p50, ticks/min, quality, broker time, trend | Sort by any column; group by instrument / source / provider / asset class with group summary (best spread, best quality); price-change flashes; silent planned symbols shown as *no price* |
| **Charts** | 1, 2 or 4 panes; candles S1–D1 or ticks; crosshair readout; last price; **compare** with another source of the same instrument (overlay) | Click a pane, then a row |
| **Time & sales** | Last 2 minutes, bid/ask/spread and latency per price | |
| **Sources** | The selected instrument from every source: provider, role, bid/ask, spread, Δ points vs the used feed, latency, age, quality | Matches broker suffixes (XAUUSD.r = XAUUSD) |
| **Health** | Feeds live, sources, instruments, rows, stale · no price, average quality, latency p95, events; collector problems in plain words | |
| **Feeds & events** | Collector per source (host, lag, symbols, heartbeat) and events, for the workspace's sources | |
| **Display** | Refresh 0.5–5 s, pause (board freezes, collection continues), local or UTC time, CSV export of the rows and columns shown | |
| **Keyboard** | `/` search, ↑ ↓ move through rows (charts follow) | |

## Next (proposed order)

1. **Alerts from the board**: price, spread, stale and quality thresholds per row → the existing alert channels.
2. **Depth of market (ladder)** for sources that send depth (FIX, exchanges); collection of depth levels is already a plan option.
3. **Row virtualisation** for 2,000+ rows (20 sources × 100 instruments) at sub-second refresh.
4. **Linked pane groups** (colour links: one click charts the instrument in every linked pane) and pop-out panes for a second monitor.
5. **Analytics panels**: spread history, latency histogram per source, correlation matrix, heat map by asset class.
6. **Session clocks and market hours** (Sydney, Tokyo, London, New York, NSE) next to the board; economic calendar.
7. **Chart studies**: moving averages, VWAP, ATR; history replay from the archive.
8. **Shared team workspaces** with owner, read-only viewers and an audit trail.
