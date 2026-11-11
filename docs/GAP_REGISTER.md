# COREI-OS-MINI — Gap Register

**Baseline compared:** Blueprint v1.2, Process Flow v1.4, Technology Inventory v1.3, as amended by `DECISIONS.md` (DEC-18 … DEC-22)
**Last updated:** 10 Nov 2026 — release 0.9.0 (users and roles, strategies and deployments)
**Status values:** OPEN, DONE, PARTIAL, SUPERSEDED, BLOCKED (needs an owner decision)

## Position

| Area | Present | Status |
|---|---|---|
| Configuration | Providers, connections (MT5 with full server description and selectable access points, REST API), accounts, instruments, symbol mappings, encrypted credentials, order-sending switch, audit log, readiness | DONE |
| Users and roles | Database users, four roles, server-side sessions, lockout, forced password change | DONE |
| Strategies and deployments | Versioned strategies with approval; deployment matrix with data source and readiness; four-eyes on LIVE | DONE |
| 1. Connections and market data | Feed-quality assessment and deterministic feed selection on supplied observations | No adapter, no live feed |
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
| G-X-04 | Documented backup/restore and operator runbook | OPEN |
| G-X-05 | Live site stuck on 8 Nov build; duplicate `n8n.coreeye.in` in server Caddyfile | OPEN — server-side |
| G-X-06 | Demo paper-run route and synthetic strategy still in code (disabled by default) | OPEN — replace with the real Phase 2–4 pipeline |

### Phase 1 — Connect and collect
| ID | Gap | Status |
|---|---|---|
| G-P1-01 | No working provider adapter; MT5 connection method not chosen (licensed mtapi direct connection vs terminal bridge) | BLOCKED — decision 1 |
| G-P1-02 | Common adapter contract (capabilities, session, subscribe, account state, orders) | OPEN |
| G-P1-03 | Connection status lifecycle and reconnect handling | OPEN (needs G-P1-01) |
| G-P1-04 | Tick/quote storage | OPEN |
| G-P1-05 | Feed assessment on a live stream | OPEN (needs G-P1-01) |
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

## C. Owner decisions

| # | Decision | Status |
|---|---|---|
| 1 | MT5 connection method: licensed mtapi direct connection or terminal bridge; plus which REST broker first | OPEN |
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
