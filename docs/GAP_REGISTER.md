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
