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
