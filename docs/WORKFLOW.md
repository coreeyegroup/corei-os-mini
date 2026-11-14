# COREI workflow: eight stages, each with one job

Data moves one way: **collect (1) → select and compare (2) → signal (3) → strategy (4) → risk (5) → allocate (6) → execute (7) → review (8)**.
Each stage reads what the previous stage produced and does not repeat its work.

| # | Stage (screen) | Its one job | Main features, options and controls | State |
|---|---|---|---|---|
| 0 | **Setup › Connectivity** | Connect providers and accounts | Technology › who › accounts; MT5 server and fastest route per account; FIX sessions; broker APIs; credentials (write-only); approvals | Built |
| 1 | **Market data** | Collect, store and watch prices; no analysis | Dashboard: workspaces › sources › instruments (add instrument with every plan option, pause/resume source and instrument, backfill, details, columns, filters, sort, CSV); collection plans; quality; events; backfill; collection switches; archive and backups | Built |
| 2 | **Market intelligence** | Select the collected data for analysis and compare it | Select & compare: source and instrument pickers, filters, 26 columns, grouping, 1/2/4 charts with source overlay, time & sales, same instrument from every source, CSV. Next: analysis sets (instrument × chosen sources → the feed strategies use), features, sessions, regimes, screening | Comparison built; analysis sets next |
| 3 | **Signal intelligence** | Turn the selected data into signals | Indicators and features, timeframes, signal rules, lifecycle, history | Planned |
| 4 | **Strategy intelligence** | Combine signals into strategies and test them | Builder, parameters, versions, backtest, paper run, deployments | Partly built (registry, paper workflow) |
| 5 | **Risk intelligence** | Decide what is allowed | Limits per account/instrument/portfolio, pre-trade checks, kill switches, margin | Partly built (checks) |
| 6 | **Portfolio intelligence** | Decide which accounts trade and what size | Account groups, allocation rules, lot sizing per account, symbol mapping | Partly built (allocation rules) |
| 7 | **Execution** | Send orders to accounts | Order adapters (MT5, FIX, Upstox), routing, slippage control, order-sending switch | Switch built; adapters planned |
| 8 | **Post-trade** | Check what happened | Fills, positions, reconciliation, P&L, TCA, reports, audit | Audit built; rest planned |
