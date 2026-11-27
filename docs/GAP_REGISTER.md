# COREI-OS-MINI — Gap Register

**Baseline compared:** Blueprint v1.2, Process Flow v1.4, Technology Inventory v1.3, as amended by `DECISIONS.md` (DEC-18 … DEC-22)
**Last updated:** 12 Nov 2026 — release 0.21.0 (audit second pass; see section E)
**Status values:** OPEN, DONE, PARTIAL, SUPERSEDED, BLOCKED (needs an owner decision)

## Position

| Area | Present | Status |
|---|---|---|
| Configuration | Providers, connections (MT5 with full server description and selectable access points, REST API), accounts, instruments, symbol mappings, encrypted credentials, order-sending switch, audit log, readiness | DONE |
| Users and roles | Database users, four roles, server-side sessions, lockout, forced password change | DONE |
| Strategies and deployments | Versioned strategies with approval; deployment matrix with data source and readiness; four-eyes on LIVE | DONE |
| 1. Connections and market data | MT5 read-only adapter, live price stream, collection plans, journal → PostgreSQL → Parquet, quality, feed switching, backups | ~70% — live login blocked by SDK (A-01) |
| 2. Strategies and signals | Strategy registry and deployments done; signal generation from live data not yet | ~35% |
| 3. Risk and allocation | Four dispatch modes; per-child risk using request-supplied state | ~15% |
| 4. Execution and reconciliation | Nothing operational | ~0% |

No order can be sent: no provider adapter or order lifecycle exists yet.

## A. Defects in earlier code

| ID | Defect | Status |
|---|---|---|
| D-01 | REPLICATE copied the full parent quantity | DONE (0.7.0) |
| D-02 | Live accounts selectable while documents said paper-only | SUPERSEDED — owner decided production platform (DEC-18); governed by order-sending switch (DEC-19) |
| D-03 | SELECT_ONE had no routing rule | DONE (0.7.0) |
| D-04 | Every broker account created its own connection | SUPERSEDED — configuration rebuilt (DEC-20); accounts are created under an explicit connection |
| D-05 | Risk uses request-supplied state | Moved to G-P3-01 … G-P3-03 |
| D-06 | No trading-core tests | DONE (0.7.0) |
| D-07 | README out of date | DONE |

## B. Missing capabilities

### Configuration and cross-cutting
| ID | Gap | Status |
|---|---|---|
| G-C-01 | Clean provider / connection / account / instrument / mapping model | DONE (0.8.0) |
| G-X-01 | Audit trail | PARTIAL — configuration, order-sending, user, sign-in, strategy and deployment changes audited; order and execution events come with Phase 4 |
| G-X-02 | Users and roles with separation of duties | DONE (0.9.0) — DEC-23, DEC-24 |
| G-X-03 | Trading screens (market data, signals, risk, orders, positions, reconciliation) | OPEN |
| G-X-04 | Documented backup/restore and operator runbook | OPEN — backups fixed (A-04); restore drill still untested |
| G-X-05 | Live site stuck on 8 Nov build; duplicate `n8n.coreeye.in` in server Caddyfile | OPEN — server-side |
| G-X-06 | Demo paper-run route and synthetic strategy still in code (disabled by default) | OPEN — replace with the real Phase 2–4 pipeline |

### Phase 1 — Connect and collect
| ID | Gap | Status |
|---|---|---|
| G-P1-01 | No working provider adapter; MT5 connection method not chosen (licensed mtapi direct connection vs terminal bridge) | PARTIAL (0.13.0) — read-only MT5 data through the licensed SDK (account reads, price stream, history); order sending open; terminal bridge kept as an option |
| G-P1-02 | Common adapter contract (capabilities, session, subscribe, account state, orders) | OPEN |
| G-P1-03 | Connection status lifecycle and reconnect handling | PARTIAL (0.13.0) — market-data stream: status, reconnect through the account addresses, outage backfill; order sessions open |
| G-P1-04 | Tick/quote storage | DONE (0.13.0) — DEC-41..44: journal, PostgreSQL by day, Parquet history, fingerprints, backups |
| G-P1-05 | Feed assessment on a live stream | DONE (0.13.0) — per-minute quality on the live stream, broker comparison, feed switching, price check before orders |
| G-P1-06 | Instrument contract details | DONE (0.8.0) |
| G-P1-07 | MT5 server discovery: broker directory search, servers.dat, per-account address choice, latency test | DONE (0.10.0) — DEC-26..28 |
| G-P1-08 | Refresh a connection's addresses from what the broker sends after login; record order round-trip and slippage per account × address | OPEN (needs G-P1-01) |
| G-C-02 | Audit 11 Nov 2026: provider legal entity/regulator, connection clock/rate/static IP, account broker facts, ownership/mandate, groups, archive, per-account symbols | DONE (0.11.0) — DEC-29, DEC-30, DEC-34, DEC-35 |
| G-C-03 | Four-eyes on LIVE changes, step-up re-authentication, credential-use log, investor passwords, key rotation | DONE (0.11.0) — DEC-31..33 |
| G-C-04 | Health board, set-up checklist, links between records, sortable lists, CSV import/export, per-user window positions | DONE (0.11.0) |
| G-C-05 | Alerts (e-mail, Telegram, webhook) | DONE (0.11.0) — DEC-37; needs SMTP settings or a bot token on the server |
| G-C-06 | Upstox daily login (OAuth and TOTP), static-IP check, rate limiter, pre-send order check | DONE as building blocks (0.11.0) — DEC-36; used by the adapters when built |
| G-C-07 | Facts read at login (margin mode, symbol rules, leverage), live connection status, Test login | DONE for MT5 (0.12.0) — DEC-39, *Log in and read now* on the account; Upstox reads come with its adapter |
| G-C-08 | Credential key on a separate host or hardware key | OPEN — rotation done; separation is an infrastructure step |
| G-C-09 | Infrastructure console: health, PostgreSQL table view/editor, read-only SQL console, edit log | DONE (0.12.0) — DEC-40 |
| G-C-10 | Scheduled broker reads (e.g. at each session open) and alert when broker limits change | OPEN |
| G-MD-01 | MT5 price stream, collection plans, bars, backfill, quality, feed switch, archive, verify, backups, History console | DONE (0.13.0) |
| G-MD-02 | Order-book depth from MT5 (stored when the broker publishes it) | OPEN — plans accept DEPTH; the reader does not stream it yet |
| G-MD-03 | Upstox / other API feeds through the same plans | OPEN — plans are kept, collector reports UNSUPPORTED |
| G-MD-04 | Per-symbol trading-session hours from the broker (SESSIONS schedule uses Mon–Fri and provider holidays until then) | OPEN |
| G-MD-05 | Second collector on another machine (live redundancy) | OPEN — outages are recorded and filled from broker history |

### Phase 2 — Strategy and signal
| ID | Gap | Status |
|---|---|---|
| G-P2-01 | Strategy registry, versions, lifecycle | DONE (0.9.0) |
| G-P2-02 | Deployment matrix (Blueprint §8.5), viewable from provider, account, instrument or strategy | DONE (0.9.0) |
| G-P2-03 | Stored signals, decisions, TradeIntents with frozen inputs | OPEN — next |
| G-P2-04 | Conflict rule between strategies | OPEN |
| G-P2-05 | First real strategy | BLOCKED — decision 3 |

### Phase 3 — Risk and allocation
| ID | Gap | Status |
|---|---|---|
| G-P3-01 | Stored risk limits (units, scope, version, firm → instrument) | OPEN (values: decision 5) |
| G-P3-02 | Stored position/exposure state | OPEN |
| G-P3-03 | Atomic per-account capacity reservation | OPEN |
| G-P3-04 | Kill controls | PARTIAL — firm-wide order-sending switch per environment (0.8.0); account/strategy/instrument scopes open |
| G-P3-05 | Group policy when one child fails | OPEN |
| G-P3-06 | Position sizing per order (strategy, signal strength, portfolio risk), rounded to the broker's step | OPEN — DEC-38; nothing in configuration fixes a size |

### Phase 4 — Execution and reconciliation
| ID | Gap | Status |
|---|---|---|
| G-P4-01 | Orders table and state machine | OPEN |
| G-P4-02 | Idempotent submission and dispatch worker | OPEN |
| G-P4-03 | UNKNOWN-outcome handling | OPEN |
| G-P4-04 | Fill/fee ledger, positions, P&L | OPEN |
| G-P4-05 | Reconciliation and break workflow | OPEN |

## E. Audit 12 Nov 2026 (release 0.18.0)

Full report: Claude Doc "COREI-OS-MINI — Full Audit and Completion Plan". Status here is authoritative.

| ID | Sev | Finding | Status |
|---|---|---|---|
| A-01 | Critical | Live MT5 login refused (OLD_VERSION / 1035), then on VT Markets accepted but unreadable: the direct SDK is behind the protocol | RESOLVED BY DESIGN 0.23.0 — terminal bridge (DEC-54); owner: set up the bridge per docs/MT5_TERMINAL_BRIDGE.md |
| A-02 | Critical | Login storm: up to 12 addresses × 9 builds per attempt, retried within seconds (lockout risk) | DONE 0.17.0 |
| A-03 | Critical | Order sending ON for DEMO and LIVE with no order adapter, risk limits or reconciliation; LIVE account permission TRADING | PARTIAL — warning + chip 0.18.0; owner to switch off |
| A-04 | High | Backups failing (pg_dump 14 vs PostgreSQL 18) | DONE 0.17.0; restore drill OPEN |
| A-05 | High | Connection marked LIVE on a demo server; nothing stopped it | DONE 0.16/0.17 (warning, environment from server name) |
| A-06 | High | Collector raised TimeoutExpired: reader lingered after a failed login | DONE 0.17.0 |
| A-07 | High | Stored paper runs / assessments readable without sign-in; demo POSTs without session | DONE 0.18.0 |
| A-08 | High | Unnamed refusal codes treated as transient and retried fast; not explained | DONE 0.17.1 |
| A-09 | High | CI red on every push since 10 Nov (ran unittest; setup lives in pytest conftest) | DONE 0.18.0 — run 43127003373 green |
| A-22 | High | On PostgreSQL 18 a blocked delete (23001 restrict) returned 503 "database unavailable" instead of 409 | DONE 0.18.0 |
| A-10 | High | Single price source per instrument; feed switching has nothing to switch to | OPEN |
| A-11 | High | Credential key on the same host as the data (G-C-08) | OPEN |
| A-12 | Medium | /api/workstation capability text stale ("no provider adapter", "no live feed") | DONE 0.18.0 |
| A-13 | Medium | Gap register last updated at 0.9.0 | DONE 0.18.0 |
| A-14 | Medium | Backup location on the same disk / not off-site | OPEN — owner: COREI_BACKUP_DIR on another disk or host |
| A-15 | Medium | Readiness screen used large tiles; detail window not movable; blockers listed twice | DONE 0.17–0.18 |
| A-16 | Medium | Connection clock, DST rule and order rate limit unset | OPEN — owner data; clock measured at login when it succeeds |
| A-17 | Medium | Data model: platform "Meta Trader" stored as a broker; "UPSTOK" typo; empty Upstox connection | OPEN — owner data |
| A-18 | Medium | Demo paper-run route and synthetic strategy still in code (G-X-06) | OPEN — remove when Phase 2 lands |
| A-19 | Medium | No dependency audit for Python | PARTIAL — pip-audit in CI (report only) |
| A-20 | Low | Wide tables hide columns on narrow windows | OPEN |
| A-21 | Low | No end-to-end test against a real broker (stub SDK only) | OPEN — needs a working demo login |
| A-23 | High | Refused broker login shown without the broker's reason; not audited | DONE 0.21.0 |
| A-24 | High | Collector supported MT5 only; Upstox data could not flow | DONE 0.19.0 |
| A-25 | High | Four-eyes deadlock for a single-user firm | DONE 0.20.0 (sole approver self-approves with reason) |
| A-26 | Medium | Three set-up entry points, no stated order; plans demanded an instrument | DONE 0.20.0–0.20.1 |
| A-27 | High | VT Markets LIVE account TRADING, never logged in; LIVE order sending ON | OPEN — owner: log in and read now; Read only; LIVE orders OFF |
| A-28 | Medium | Server egress IP not Indian: Upstox edge may block; SEBI static IP | OPEN — owner: API check; Indian egress if blocked |
| A-29 | Low | Collector list kept stale rows of archived accounts | DONE 0.21.0 |
| A-30 | High | Data model narrowed to MT5 and Upstox; no provider-agnostic adapter list, no data/execution split | DONE 0.24.0 (adapter catalogue, connection roles, FIX 4.4) |
| A-31 | High | mtapi SDK behind the MT5 servers (1035; IndexOutOfRange after login) | OPEN — owner: current mtapi licence with update subscription; compare mt5api.dll versions |
| A-32 | Medium | Order adapters (MT5, FIX, Upstox) not built | OPEN — next stage (OMS) |
| A-33 | High | Set-up organised by record type, not by how a firm thinks (technology › who › accounts) | DONE 0.25.0 (Connectivity tree) |
| A-34 | High | Strategy, allocation and risk run only in the paper workflow on synthetic prices; not fed by collected prices | OPEN — next build step |
| A-35 | High | Fills, positions and P&L reconciliation | OPEN — after order adapters |
| A-36 | Medium | One collector node: no standby for prices | OPEN |
| A-37 | Low | Configuration.tsx (1,750 lines) and config_store.py (1,100 lines) to split by domain | OPEN — before order adapters |
| A-38 | Low | UI bundle 555 kB in one chunk | OPEN |
| A-39 | High | MT5 server level in the tree; servers added by hand; latency order only on request | DONE 0.26.0 |
| A-40 | Medium | No market dashboard; live prices only as a table | DONE 0.27.0 (workspaces dashboard) |
| A-41 | Medium | Generic "database unavailable" message hid the real cause (seen on the Upstox login callback, 14 Nov) | DONE 0.27.0 (specific messages); OPEN: owner to read the log line if it recurs |
| A-42 | High | Symbols typed by hand; provider symbol lists not loaded (Upstox file, MT5 list beyond 2,000, FIX security list) | DONE 0.30.0 (symbol catalogue) |

## C. Owner decisions

| # | Decision | Status |
|---|---|---|
| 1 | MT5 connection method: licensed mtapi direct connection or terminal bridge; plus which REST broker first | DECIDED DEC-55: direct SDK with updates, bridge fallback; Upstox first |
| 2 | First instrument and session | OPEN (XAUUSD used as example) |
| 3 | First real strategy rules | OPEN |
| 4 | Pre-live test method: broker demo accounts, own simulator, or both | OPEN |
| 5 | Initial risk limit values and units | OPEN |
| 6 | Production vs paper | DECIDED — production (DEC-18) |
| 7 | Live order control | DECIDED — firm-wide switch per environment, off by default (DEC-19) |
| 8 | Launch platforms | DECIDED — MT5 and broker REST/WebSocket API (DEC-21) |

## D. Completion order

1. Configuration foundation — **done (0.8.0)**.
2. User roles (G-X-02) and strategy deployments (G-P2-01, G-P2-02) — **done (0.9.0)**.
3. Signals and intents (G-P2-03), risk limits (G-P3-01 … 03), order lifecycle, risk limits and reservations, fill ledger (G-P3-01 … 03, G-P4-01 … 04), all tested.
4. First adapter once decision 1 is made, behind the adapter contract (G-P1-02).
5. One real feed through to a reconciled order on DEMO accounts; then the LIVE switch can be considered.
6. Trading screens on real data.

## E. Wave 0 — 23 Nov 2026 audits (release 0.53.0)

Source: Claude end-to-end audit and build specification, ChatGPT lifecycle audit, commit 673ccf3. Evidence: `backend/tests/test_wave0.py`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| C-01 / L-1 | LIVE order sending possible with no limit policy | DONE 0.53.0 (switch refused; dispatch refuses new LIVE risk) | test_live_policy_gates_switch_dispatch_and_home |
| C-03 / C-21 | Reconciliation read 3 days by wall clock; aged-out breaks closed as matched | DONE 0.53.0 | test_old_deal_is_still_checked_and_aged_break_stays_open, test_verified_interval_drives_history_read, test_broker_read_failure_clears_nothing |
| C-04 | Setup › Database could edit the trading ledger | DONE 0.53.0 | test_db_admin.test_trading_ledger_is_never_edited_here |
| C-12 | Working orders not reserved against limits | DONE 0.53.0 | test_working_orders_count_against_position_limit, test_concurrent_checks_cannot_both_pass |
| C-14 | Home did not flag unsafe LIVE state | DONE 0.53.0 | test_live_policy_gates_switch_dispatch_and_home |
| C-16 | Release identity inconsistent | DONE 0.53.0 | test_workstation_reports_release_commit_and_schema |
| C-17 | Paper book mixed into broker risk | DONE 0.53.0 | test_paper_results_never_reach_the_broker_book; test_trading.test_broker_mode_refused_and_loss_limit_stops |
| C-18 | Unvalued holding counted as zero / dropped | DONE 0.53.0 | test_unvalued_holding_refuses_risk_increase_but_allows_reduction |
| C-19 | Broker sizing fell back to internal equity | DONE 0.53.0 | test_broker_sizing_never_falls_back_to_paper_equity |
| C-20 | Fill with no position row invisible | DONE 0.53.0 | test_fill_without_position_row_is_a_break |
| C-23 | Manual resolution could overfill | DONE 0.53.0 | test_manual_resolution_capped_and_overfill_recorded |
| C-28 / C-13 | Order rate limiter never called; no Upstox Algo Name | DONE 0.53.0 (single-process limiter) | test_dispatch_respects_the_connection_order_rate, test_upstox_algo_name_header_on_sending_calls |
| C-22 | "Brokers agree" without working orders or cash compared | PARTIAL 0.53.0 (coverage recorded, text corrected; UI per-account coverage in W1-3) | run coverage in test_old_deal… |
| C-25 | Daily loss adds all current unrealised P&L (UTC day) | OPEN — W1-6 (owner to define the trading day) | — |
| C-05 | No pending-replace state for amendments | OPEN — W1-1 | — |
| C-02 | No paper-to-live promotion gate | OPEN — W1-2 | — |
| C-06 | Approved risk/sizing graphs not executed | OPEN — W1-4 | — |
| C-24 | No account entitlements | OPEN — W1-7 (owner decision 7) | — |

Waves 1–3 and their acceptance criteria: COREI-OS-MINI Final Build Specification (claude.ai artifact LcgAGcpqD6fTsWWgEW9Har).

## F. Stage 1 · Market data — 23 Nov 2026 audit (release 0.54.0)

Source: COREI-OS-MINI Stage 1 Market Data: Deep Audit and Polish Plan (claude.ai doc Py38xF5fdsmfxqodMzh1Ab). Evidence:
`backend/tests/test_market_data_stage1.py`. Migration `045_market_data_integrity.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| MD-C1 / LM-1 | Plan symbol never re-checked against the source's list (NIFTY50 "Nifty 50") | DONE 0.54.0 (INVALID_SYMBOL, unsubscribed, suggestions, CRITICAL event) | test_unlisted_symbol_is_caught_left_out_and_explained |
| MD-C2 / LM-3 | Broken plan shown green "collecting"; header counted collectors | DONE 0.54.0 (md_plan_health; header and KPI count plan problems) | same |
| MD-C3 | Bars by receipt time | DONE 0.54.0 (exchange time when within 2 min of receipt; basis stored) | test_bars_use_exchange_time_when_plausible |
| MD-C4 | Bars UTC-aligned for NSE/BSE/MCX | DONE 0.54.0 for new bars (session-anchored; old bars kept, labels NULL) | test_nse_bars_align_to_the_session_open |
| MD-C5 / MD-C6 | Upstox day volume summed; exchange bars from bid | DONE 0.54.0 | test_exchange_instruments_use_last_price_and_day_volume_increase |
| MD-C7 | Feed quality never reached trading | DONE 0.54.0 (risk check gate: md_block_states, md_min_quality_score) | test_feed_gate |
| MD-C8 / LM-2 | Failed automatic backfill re-queued forever | DONE 0.54.0 (backoff after md_backfill_max_failures) | test_failed_automatic_backfills_wait_and_events_name_the_symbol |
| MD-C9 / LM-5 | Clock offset fixed by the first tick | DONE 0.54.0 (median, re-measured every 15 min; Upstox stated) | collector code; covered by end-to-end tests |
| MD-C10 | Journal replay never existed | DONE 0.54.0 (read-only check `python -m corei_os_mini.md.replay`) | test_journal_replay_rebuilds_the_stored_bars |
| MD-C14 | Events without symbol | DONE 0.54.0 | test_failed_automatic_backfills_wait_and_events_name_the_symbol |
| MD-C15 / LM-6 | Old score shown as today's quality | DONE 0.54.0 (score only from the last 5 min on the dashboard) | store.latest |
| — | One Upstox key rejected failed every key's quotes; old trade time read as latency | DONE 0.54.0 | test_bad_key_is_set_aside_and_others_keep_flowing |
| D-MD-7 / D-MD-8 / D-MD-9 | Misleading words; negative latency; silent "Live" filter | DONE 0.54.0 (health words, "clock?", hidden-row message) | UI scenarios |
| MD-C17 | Float price storage | OPEN — MD-6 (owner decision: NUMERIC now or scaled int64) | — |
| MD-C11 / MD-C12 | Exchange holiday feed; F&O roll; corporate actions | OPEN — MD-9 | — |
| MD-C13 / C4 / C6 | Depth; Upstox WebSocket; standby collector | OPEN — MD-12 | — |
| D-MD-1..5 | Instrument discovery in four places; explorer/tab duplication | OPEN — MD-11 (screen consolidation) | — |
| F3 | Nightly gap scan of stored history | OPEN — MD-10 part 2 | — |

## G. Stage 2 · Market intelligence — 23 Nov 2026 audit (release 0.55.0)

Source: COREI-OS-MINI Stage 2 Market Intelligence: Deep Audit and Polish Plan (claude.ai doc JZc1YUzEQq66yzMJhX8cJe).
Evidence: `backend/tests/test_intelligence_stage2.py`. Migration `046_market_intelligence_integrity.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| MI-C1 / LI-3 | Stage 2 re-bucketed session markets at UTC | DONE 0.55.0 (shared Stage 1 buckets; bar ends at the session close; feature version 4) | test_nse_hours_follow_the_session_and_end_at_the_close |
| MI-C2 / MI-C3 / LI-1 | Stale broker bars preferred by count; same rule in runtime and backtest | DONE 0.55.0 (bar-source policy: BROKER, M1, MIXED; broker grid followed) | test_broker_grid_is_followed_and_a_stopped_broker_series_is_continued; runtime parity test |
| MI-C4 / LI-2 | Freshness by recompute time | DONE 0.55.0 (FRESH, LAGGING, STALE, CLOSED by market hours and holidays) | test_fresh_lagging_stale_and_closed |
| MI-C5 | Regime unused by trading | DONE 0.55.0 (allowed_regimes, blocked_volatility per strategy; off by default; backtest and live alike) | test_entries_only_in_allowed_states_and_exits_always |
| MI-C6 | Wrong-symbol plans screened; no feed health | DONE 0.55.0 | screener feed_health |
| MI-C7 / LI-4 / LI-5 | FX year, FX sessions for every market | DONE 0.55.0 (trading year per market, exchange session label, session VWAP) | test_trading_year_session_vwap_and_label |
| MI-C8 / LI-6 | No exchange holidays | DONE 0.55.0 (Upstox import; Stage 1 and event risk use them) | test_upstox_holidays_import |
| MI-C9 / MI-C10 | Event window hard-coded; no event risk for NSE | DONE 0.55.0 | test_exchange_markets_have_event_risk |
| MI-C12 | Signals not traceable to features | DONE 0.55.0 (feature_version, bars_source on the signal's feature row) | runtime parity test |
| MI-C13 | Overlapping runs | DONE 0.55.0 (one run at a time; incremental: unchanged bars only refresh freshness) | test_one_run_at_a_time |
| LI-7 | Rules that never fire | DONE 0.55.0 (preview on 30 days; warnings) | test_rule_preview_and_strength_warning |
| MI-C16 / LI-8 | Strength from one pair | DONE 0.55.0 (warning under 3 currencies) | same |
| MI-C11 | Rule fires cannot drive a strategy | OPEN — MI-8 part 2 (the RULES algorithm already takes the same conditions) | — |
| MI-C14 / H2 | Feature loop and trading cycle share one loop; engine in the API process | OPEN — MI-10 part 2 (owner decision: separate service) | — |
| MI-C15 / LI-9 | One screener row per source | OPEN — MI-9 (instrument roll-up) | — |
| — | Economic-calendar provider | OPEN — owner decision (CSV until chosen) | — |
| — | Volatility risk-limit multiplier | OPEN — MI-4 part 2 | — |

## H. Stage 3 · Signal intelligence — 24 Nov 2026 audit (release 0.56.0)

Source: COREI-OS-MINI Stage 3 Signal Intelligence: Deep Audit and Build (claude.ai doc 3fyTWBacW6JJF8fM7ZurtN).
Evidence: `backend/tests/test_signal_stage3.py`. Migration `047_signal_intelligence_integrity.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| SI-C1 / LS-1 | ~900 M5 flips a day: scores wobble around the threshold | DONE 0.56.0 (exit threshold per definition = hysteresis; off by default; dashboard warns) | test_exit_threshold_holds_a_side_and_cuts_flips; test_exit_threshold_definition_and_fewer_flips |
| SI-C2 | Research t-stat overstated by overlapping returns | DONE 0.56.0 (n ÷ h) | test_overlap_adjusted_t_and_out_of_sample |
| SI-C3 | PREDICTIVE with no out-of-sample check; many signals tested on the same bars | DONE 0.56.0 (last 30% must agree; multiple-testing note) | same; research-all API test |
| SI-C4 | Edge shown gross of cost | DONE 0.56.0 (net edge per horizon) | same |
| SI-C5 | Scores on stale bars counted in the composite | DONE 0.56.0 (Stage 2 freshness; stale scores shown, left out) | test_stale_scores_leave_the_consensus |
| SI-C6 | Strategy signal quality gross of spread; manual signals never measured | DONE 0.56.0 (cost, net, hit after cost; bar the signal fell in) | test_quality_net_of_the_spread_and_manual_signals_measured |
| SI-C7 | Flips not traceable to a definition version | DONE 0.56.0 (definition_version on sig_events) | same |
| SI-C8 | SIGNAL_BLEND references formula kinds, not library definitions (a tuned or graph-bound definition cannot be blended) | OPEN — pin definition code + version into the strategy version | — |
| SI-C9 | Signal horizon fixed at 10 bars for strategy-signal quality | OPEN — per-deployment horizon | — |
| SI-C10 | Library signals and strategy signals both called "signals" in different stages | OPEN — naming: "Library scores" (Stage 3) vs "Strategy signals" (Stage 4) | — |


## I. My Workspace control desk — 25 Nov 2026 (release 0.57.0)

Source: owner request and the approved design (Input / Processing / Output, Start / Pause / Stop, Results; shared with an owner;
create and adopt). Evidence: `backend/tests/test_workspace_control.py`, `ui/tests/trading.cjs` (control desk scenario). Migration `048_workspace_control.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| WS-1 | A workspace could not run anything (scan only) | DONE 0.57.0 (Start creates owned runs; Observe / Paper / DEMO / LIVE) | test_plan_checks_explain_and_start_creates_owned_runs |
| WS-2 | Two desks could drive the same strategy × account (duplicate orders) | DONE 0.57.0 (one controller per deployment, database-enforced; others watch) | test_one_controller_per_run_and_adoption_needs_confirmation |
| WS-3 | Existing deployments could not be brought under a desk | DONE 0.57.0 (adopt with confirmation; unconfirmed are watched, unchanged) | same |
| WS-4 | No Start / Pause / Stop for a desk; no stop policy | DONE 0.57.0 (keep / cancel orders / close positions with confirmation; UNKNOWN never cancelled) | test_pause_resume_stop_policies_never_touch_unknown |
| WS-5 | Editing a running desk changed it at once | DONE 0.57.0 (staged → review → apply) | test_staged_changes_apply_only_after_review |
| WS-6 | No readiness before running | DONE 0.57.0 (Validate: sources, plan health, approved versions, timeframes, accounts, order sending, risk, ownership; live check of unsaved settings) | plan tests; UI scenario |
| WS-7 | No one-click emergency for a desk | DONE 0.57.0 (deployment-scoped kill switches, audited) | test_emergency_stop_engages_scoped_kill_switches |
| WS-8 | Layouts were private per user only | DONE 0.57.0 (control desks shared with an owner; optionally private; layouts kept and importable) | test_private_desks_roles_and_results; test_import_saved_layout |
| WS-9 | Data-source backup does not switch automatically when the primary feed fails | OPEN — the deployment holds one data mapping; failover needs a runtime change (proposal) | — |
| WS-10 | One strategy on several timeframes for the same account and instrument | OPEN — the deployment key has no timeframe; use a second strategy version (DEC-101 §6) | — |
| WS-11 | Workspace-level notifications (Telegram, e-mail) | OPEN — alerts are firm-wide today (proposal) | — |
| WS-12 | Per-desk P&L and schedule (start / stop at session times) | OPEN — proposal | — |

## J. Stage 4 Strategy and Stage 5 Risk · governance — 25 Nov 2026 (release 0.58.0)

Source: the live-site check of 25 Nov (8 of 9 strategies draft, no backtest, 0 risk limits with DEMO and LIVE sending on) and the owner's decisions.
Evidence: `backend/tests/test_governance.py`, `ui/tests/trading.cjs` (risk profiles, strategy evidence). Migration `049_strategy_risk_governance.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| S1 | Approval needed no evidence | DONE 0.58.0 (profile gate on the exact backtest; exception recorded where allowed) | test_approval_needs_evidence_or_a_recorded_exception |
| S2 | No lifecycle from draft to live | DONE 0.58.0 (Draft → Backtested → Paper-proven → Approved, shown per version; LIVE needs the paper record) | test_live_activation_needs_the_paper_record |
| S2b | Edited draft kept old results | DONE 0.58.0 (evidence only from a backtest of the exact parameters) | test_editing_a_draft_voids_its_backtest |
| S4 | One strategy, one timeframe | DONE 0.58.0 (copy to timeframe as a new draft with its own evidence) | test_timeframe_copy_is_a_new_draft |
| R1 | Orders on with no limits | DONE 0.58.0 (DEMO switch and desk Start need the profile minimum; LIVE pre-LIVE policy now checked in desk plans too) | test_demo_switch_needs_minimum_limits_and_presets_never_loosen |
| R2 | Limits only as absolute numbers | DONE 0.58.0 (presets as % of equity, previewed, never loosen) | same |
| R3 | Loss stop only per account | DONE 0.58.0 (strategy and run daily-loss limits stop only those runs) | test_run_scope_loss_stops_only_that_run |
| R4 | Sizing not checked against limits before Start | DONE 0.58.0 (desk plan: order value and order quantity vs limits) | test_desk_says_before_start_when_limits_are_missing_or_size_is_refused |
| S3 | Backtest costs: no swap, no slippage model, no partial fills | OPEN — proposal | — |
| S5 | Side-by-side comparison of versions | OPEN — proposal | — |
| S7 | Live vs backtest drift alert and auto-pause | OPEN — drift exists; alert rule is a proposal | — |
| R5 | Net exposure and correlation across workspaces | OPEN — proposal | — |
| R6 | Second approver for LIVE limit changes | OPEN — proposal (four-eyes exists for LIVE configuration) | — |
| R2b | Workspace-scope limits | OPEN — proposal (strategy and run scopes exist) | — |

## K. Setup: systematic flow, gates and menus — 26 Nov 2026 (release 0.59.0)

Source: the owner's request for clarity ("all features but no systematic flow") and the final design (DEC-103).
Evidence: `backend/tests/test_setup_gates.py`, `ui/tests/configuration.cjs` (setup path, menus, override, gate levels). Migration `050_setup_gates.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| SU-1 | No order and no "done" in Setup | DONE 0.59.0 (8 computed steps per track, next step, Do it) | test_status_is_computed_per_track |
| SU-2 | Safety steps could be skipped (orders on, no limits) | DONE 0.59.0 (gates at order sending, activation, workspace; hard at dispatch) | test_soft_gate_blocks_until_fixed_or_overridden; test_activation_and_workspace_plan_honour_the_gates |
| SU-3 | No controlled bypass | DONE 0.59.0 (soft / supervised / hard; scoped, time-boxed, revocable overrides) | test_supervised_needs_a_second_person_and_hard_takes_none; test_account_override_covers_only_that_account |
| SU-4 | Gate changes uncontrolled | DONE 0.59.0 (Admin / Risk approver, reason; LIVE loosening needs a second person) | test_loosening_live_waits_for_a_second_person |
| SU-5 | No landing and no setup indicator | DONE 0.59.0 (landing on Setup until the target track is set up; header chip) | test_landing_until_the_target_track_is_complete |
| SU-6 | Menus not in work order; duplicate homes | DONE 0.59.0 (8 ordered groups; stage-owned items are links) | configuration.cjs |
| SU-7 | Setup report for audit | DONE 0.59.0 (state per track, overrides, last 200 gate decisions; print) | test_landing_until_the_target_track_is_complete |
| SU-8 | Alert-channel test delivery as part of step 1 | OPEN — step 1 checks an active channel; proof of a delivered test is a proposal | — |
| SU-9 | Market-data history depth in step 4 | OPEN — history is checked by the evidence profile (step 6); a depth check per plan is a proposal | — |
| SU-10 | Choosing the target track in the screen | OPEN — set by API today (default DEMO); a selector is a small follow-up | — |

## L. Paper trading: multiple accounts, any currency, forex and India — 26 Nov 2026 (release 0.60.0)

Source: owner request (account 1, 2 … each any instrument, any strategy; currency and amount; international forex and Indian market).
Evidence: `backend/tests/test_paper_accounts.py`, `ui/tests/configuration.cjs` (paper accounts, FX rates). Migration `051_paper_accounts.sql`.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| PA-1 | Paper needed a broker account | DONE 0.60.0 (standalone paper accounts on the COREI simulator) | test_paper_account_trades_on_the_feed_with_its_own_costs |
| PA-2 | One amount and currency for all paper books | DONE 0.60.0 (per account: any currency, any amount; funds; start again) | test_funds_clone_and_risk_profile |
| PA-3 | Paper fills cost nothing | DONE 0.60.0 (per lot, % of value, minimum; own slippage; gross vs net kept apart) | test_paper_account_trades_on_the_feed_with_its_own_costs |
| PA-4 | INR and other currencies could not value USD instruments without a direct pair | DONE 0.60.0 (cross through USD/EUR; rates entered by a person) | test_usd_and_inr_accounts_value_the_same_trade_in_their_own_currency |
| PA-5 | No market scope (forex vs India) | DONE 0.60.0 (any / forex / India / custom, enforced pre-trade) | test_scope_is_enforced_and_closing_is_always_allowed |
| PA-6 | Paper runs required a broker trading symbol | DONE 0.60.0 (a paper run needs only the data feed) | test_a_paper_account_never_reaches_a_broker |
| PA-7 | Indian lot sizes, F&O expiries, options chains | OPEN — instruments carry contract size and step today; an Upstox instrument import with lots and expiries is a proposal | — |
| PA-8 | Overnight financing (swap) and statutory charges itemised (STT, GST, stamp duty) | OPEN — one % of value covers them today; an itemised Indian charges model is a proposal | — |
| PA-9 | Performance P&L net of costs in the post-trade reports | OPEN — equity and positions are net; the performance table shows gross P&L and costs separately is a proposal | — |
| PA-10 | Margin model for paper (leverage, margin calls) | OPEN — limits (leverage, gross exposure) refuse orders; a margin-call simulation is a proposal | — |

## M. Polishing: paper account page, settings, top bar — 26 Nov 2026 (release 0.61.0)

Source: owner request (paper page blank space and no account detail; Settings incomplete and unorganised; top ribbon
review against VS Code with layout toggles at the top right).
Evidence: `backend/tests/test_paper_accounts.py::test_account_page_shows_what_a_broker_terminal_shows`,
`ui/tests/configuration.cjs` (paper account page), `ui/tests/workbench.cjs` (top bar and Settings). No migration.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| PO-1 | Paper page: stretched button, blank space, tall rows | DONE 0.61.0 (KPI bar, compact clickable rows, grouped form) | configuration.cjs |
| PO-2 | A paper account could not be opened | DONE 0.61.0 (account page: KPIs, positions + close, orders + cancel, history, performance, runs, activity, ticket) | test_account_page_shows_what_a_broker_terminal_shows |
| PO-3 | Settings held only appearance | DONE 0.61.0 (time & region, trading, startup, top bar, shortcuts, firm links, about; per-account storage) | workbench.cjs |
| PO-4 | Top bar hid kill switches and unknown orders | DONE 0.61.0 (always shown) | workbench.cjs |
| PO-5 | No layout toggles / notifications / account menu in the top bar | DONE 0.61.0 | workbench.cjs |
| PO-6 | Settings sync across browsers for appearance and layout | OPEN — appearance and layout stay per browser (as VS Code without Settings Sync); moving them to the account is a proposal | — |
| PO-7 | Market clock knows NSE holidays | OPEN — the clock shows weekday sessions only; the trading calendar already holds holidays and could feed it | — |
| PO-8 | Keyboard shortcuts can be re-bound | OPEN — the list is read-only today | — |
| PO-9 | Paper ticket shows a live bid/ask before sending | OPEN — fills use the live price; a quote line in the ticket is a proposal | — |

## N. Workbench polish to VS Code conventions — 26 Nov 2026 (release 0.62.0)

Source: owner request (duplicate explorer in Settings, layout break without side bar, always-on explanations, menu
names, right side bar, VS Code fonts and spacing). Evidence: `ui/tests/workbench.cjs`, `ui/tests/trading.cjs`; headless
Chrome screenshots of Market data, Risk and Settings with and without side bars. No backend change, no migration.

| ID | Gap | Status | Evidence |
|---|---|---|---|
| WB-1 | Settings categories shown twice | DONE 0.62.0 (side bar is the table of contents) | workbench.cjs |
| WB-2 | Hiding the side bar stacked a section column above the content | DONE 0.62.0 (column removed; sub-tabs remain) | screenshots |
| WB-3 | Explanations always on screen | DONE 0.62.0 (ⓘ; "Always shown" setting) | workbench.cjs |
| WB-4 | Six custom menus | DONE 0.62.0 (View, Go, Window, Help; compact ☰) | workbench.cjs |
| WB-5 | Right side bar held only attention | DONE 0.62.0 (accounts, positions, orders, watchlist) | workbench.cjs |
| WB-6 | Mixed sizes and fonts | DONE 0.62.0 for the shell, headers, tabs, menus, settings; individual screens still carry older local styles — reviewed screen by screen as each is touched | — |
| WB-7 | About 480 short notes across screens | PARTIAL — long notes are one line with ⓘ automatically; rewriting each into the ⓘ of its control is a proposal | — |
| WB-8 | Views movable between side bars (VS Code drag and drop) | OPEN — proposal | — |

## O. Portfolio paper accounts and end-to-end visual audit — 27 Nov 2026 (release 0.63.0)

Evidence: `backend/tests/test_paper_accounts.py` (paper flag), `ui/tests/configuration.cjs` (Setup entry opens
Portfolio › Paper accounts), `ui/tests/workbench.cjs` (distinct activity icons, stage number in tooltip); headless
Chrome screenshots of Home, Market data, Strategies, Risk, Portfolio, Execution, Setup, My workspaces, Settings.

| ID | Gap | Status |
|---|---|---|
| VA-1 | Paper accounts in Setup; "Paper capital" mislabelled in Portfolio | DONE 0.63.0 |
| VA-2 | Activity bar icons repeated; tiny digits | DONE 0.63.0 |
| VA-3 | Editor tabs too small | DONE 0.63.0 (35px) |
| VA-4 | Uneven tile shapes and type sizes across screens | DONE 0.63.0 (one scale, one tile size) |
| VA-5 | "undefined" shown in Home stage cards and Execution when a count is missing | DONE 0.63.0 (0) |
| VA-6 | Market data dashboard tiles keep their own style (uppercase labels, monospace values) | OPEN — align when that screen is next changed |
| VA-7 | Remaining per-screen stylesheets still carry older sizes beneath the final layer | OPEN — consolidate into one stylesheet (proposal; large, no visible change expected) |

## P. Alignment pass — 27 Nov 2026 (release 0.63.1)

Source: owner review of Settings › Layout and Portfolio › Paper accounts after 0.63.0 ("spacing and gaps increased,
alignment missing"). Method: headless Chrome screenshots plus a script measuring, for 147 screens and sections, the
left edge of every top-level block and the vertical gap between blocks (all blocks share one left edge; no gap over
20px; the only flags were buttons sharing a toolbar row). Visual review of the densest panels found the issues below.

| ID | Gap | Status |
|---|---|---|
| AL-1 | Settings: each checkbox on its own line, title and rows on different left edges, reset link floating mid-page | DONE 0.63.1 (VS Code layout: checkbox on the description line, one left edge, "Reset all" beside the title, shortcuts as one-line rows) |
| AL-2 | Paper accounts: own tile style, actions floating right, long footer | DONE 0.63.1 (standard toolbar and tiles; footer behind ⓘ; account page tiles match) |
| AL-3 | Risk › Limits profile row: two-line label broke the row | DONE 0.63.1 |
| AL-4 | Home stage cards uneven (questions 4–6 lines) | DONE 0.63.1 (question in tooltip; shown again with Help text › Always shown) |
| AL-5 | Setup header repeated name, roles, password and sign-out | DONE 0.63.1 (account menu has Change password; header keeps two small links) |

## Q. Compact density pass — 27 Nov 2026 (release 0.64.0)

Evidence: live site inspected read-only in Chrome at the owner's window size; headless Chrome screenshots at 742×773 and
1600×900; layout measured on 147 screens/sections (one left edge everywhere; no gap over 20px; the only flag is the
Database screen's intended two-column layout). UI tests 77/77.

| ID | Gap | Status |
|---|---|---|
| CD-1 | Settings three lines per setting on narrow windows | DONE 0.64.0 (one row; description on hover) |
| CD-2 | Scrollbars covering tab labels; thick scrollbars | DONE 0.64.0 |
| CD-3 | Side bar 38% of a narrow window | DONE 0.64.0 (capped ≈ 26%) |
| CD-4 | Market data tiles with clipped labels and a grey filler cell | DONE 0.64.0 |
| CD-5 | Competing style layers | PARTIAL — Settings consolidated; consolidating every component into one stylesheet is a proposal (no visible change, removes the cause for good) |

## R. Production setup, stage 0 — 27 Nov 2026 (release 0.64.1)

Guided production setup started (plan: claude.ai project doc `claude/production-setup-plan-2026-11-27.md`). Read-only
check of the live system: Paper 5/5, DEMO 3/8, LIVE 4/8; LIVE held by trading permission, hard/supervised gates and
the pre-LIVE readiness policy.

| ID | Gap | Status |
|---|---|---|
| PS-1 | Setup path listed one missing trading symbol once per strategy (8× on the live system) | DONE 0.64.1 (`test_one_missing_trading_symbol_is_reported_once_not_per_strategy`, fails on 0.64.0 with 8 copies) |
| PS-2 | No exchange trading calendar for India LIVE (build spec W1-6) | OPEN — before India LIVE |
| PS-3 | No per-account entitlements (W1-7) | OPEN — before LIVE |
| PS-4 | No PENDING_REPLACE (W1-1) | OPEN — before any LIVE order modify |

## S. Integration gateway — 27 Nov 2026 (release 0.65.0)

Acceptance criteria and evidence (backend `tests/test_integration_gateway.py`, UI `tests/configuration.cjs`):

| ID | Criterion | Status |
|---|---|---|
| IG-1 | Password minimum 2 characters (server, UI, setup script) | TESTED (`test_password_minimum_is_two_characters_without_complexity`) |
| IG-2 | Bearer refusals: missing, invalid, query-string, revoked, expired, disabled, network, rate limit | TESTED (`test_bearer_keys_every_refusal_has_a_reason_and_keys_are_never_stored`) |
| IG-3 | Permissions per endpoint; UNAVAILABLE and LIVE permissions cannot be granted; empty scope grants nothing | TESTED |
| IG-4 | Snapshot formats, quality, missing datasets, budget reduction, 413 | TESTED (AI_COMPACT; other formats share the shaping path) |
| IG-5 | Scan jobs 202, duplicate request, concurrency limit, isolation between connections, cancel, job notice | TESTED |
| IG-6 | Signals need provenance; risk assessment creates no order | TESTED |
| IG-7 | Paper orders idempotent; LIVE refused; DEMO needs permission + environment + DEMO account | TESTED (DEMO refusals; a DEMO order reaching a broker NOT tested — no adapter) |
| IG-8 | Webhooks: SSRF/http refusal, signature, event id, retry then send, alert fan-out | TESTED (local receiver mock) |
| IG-9 | OpenAPI lists only gateway paths, each with its required permissions | TESTED |
| IG-10 | Integrations screen: profile defaults, LIVE permission disabled, key shown once, real Bearer test without session cookie, explorer | TESTED (UI scenario with mocked server) |
| IG-11 | n8n workflows A–F importable, credential by name, no secrets, no LIVE | TESTED (`test_n8n_workflows_are_importable_and_hold_no_secrets`, structure only); import into a real n8n — NEEDS VERIFICATION by the owner |
| IG-12 | Bearer request through Caddy on the live site | NEEDS VERIFICATION after deploy (Test & setup tab) |

Open gaps:

| ID | Gap | Status |
|---|---|---|
| IG-G1 | Outbound COREI → CORE-AI calls | OPEN — contract documented |
| IG-G2 | Real-time stream (WebSocket/SSE) | OPEN — webhooks and polling instead |
| IG-G3 | Open interest, Greeks, option chains, market depth not collected (Upstox reader is quote polling + candles) | OPEN |
| IG-G4 | `market_data` prices stored as DOUBLE PRECISION (pre-existing); the gateway returns strings | OPEN — schema change needs owner approval |
| IG-G5 | Backtests through the gateway | OPEN |
| IG-G6 | LIVE, approvals, release and amend through integrations | NOT OFFERED by design (DEC-109) |
| IG-G7 | Rate limit counted from the usage table (one database) — fine for one server, not for several | ACCEPTED for the mini platform |

## T. Platform mode — 27 Nov 2026 (release 0.66.0)

| ID | Criterion | Status |
|---|---|---|
| PM-1 | Starts in Production; only an administrator switches; reason; typed PRODUCTION; stale version refused; audited | TESTED (`test_starts_in_production_and_only_an_administrator_switches`) |
| PM-2 | Passwords: 6 in Production, 2 in Development | TESTED (same test; gateway test runs in Development) |
| PM-3 | Entering Development turns LIVE off (audited); switch, pre-trade check and last check refuse LIVE; LIVE allowance needs typed LIVE; removing it turns LIVE off; Production never turns LIVE on | TESTED (`test_development_keeps_live_off_unless_allowed`) |
| PM-4 | Development: PAPER/DEMO gates warn only; LIVE gates and configured levels unchanged | TESTED (`test_development_gates_only_warn_but_live_gates_keep_their_levels`) |
| PM-5 | Reset: refused in Production, needs RESET, archives with history, fresh copies `_R2`/`_R3` | TESTED (`test_reset_test_data_only_in_development_and_keeps_history`); skipping an account with active runs relies on the existing clone check — not separately tested |
| PM-6 | TEST banner on every screen in Development; Platform mode page | TESTED (UI `workbench.cjs`, `configuration.cjs`, mocked server) |
| PM-7 | Behaviour on the live server after deploy | NEEDS VERIFICATION by the owner |

Open: reset covers paper accounts only (not DEMO broker positions, scan runs or signals) — PM-G1, by design for now.

### T-fix · 0.66.1 (27 Nov 2026)

| ID | Defect | Status |
|---|---|---|
| CF-1 | Any MT5 or FIX connection with a stored server credential (terminal bridge token, FIX login) could not be edited: every save was refused with "The stored credential does not match the new authentication method" because the check only knew REST authentication methods. Found by the owner editing clock and order rate on METAQUOTES_MT5_DEMO and VTMARKET_MT5_DEMO. | FIXED — checked per platform; TESTED (`test_mt5_connection_with_bridge_token_can_be_edited`, fails without the fix) |
