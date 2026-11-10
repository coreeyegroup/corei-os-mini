# COREI-OS-MINI — Gap Register

**Baseline compared:** Blueprint v1.2, Process Flow and Build Phases v1.4, Technology Inventory v1.3
**Code reviewed:** commit `e70b14e` (10 Nov 2026); fixes below applied in the following commit
**Status values:** OPEN, FIXED, RECLASSIFIED, BLOCKED (needs an owner decision)

## Position by build phase

| Phase (v1.4) | Present | Approx. |
|---|---|---|
| 1. Connections and market data | Provider/connection/account/instrument/mapping registries; feed-quality assessment and deterministic feed selection on supplied observations; encrypted broker credential storage | ~25% |
| 2. Strategies and signals | One demo moving-average rule, no persisted config | ~10% |
| 3. Risk and allocation | Four dispatch modes; per-child risk using request-supplied state | ~15% |
| 4. Paper execution and reconciliation | Instant simulated fill; JSON run record | ~5% |

No broker connection, live data feed, real strategy, order book or reconciliation exists yet.

## A. Defects in existing code

| ID | Defect | Status | Resolution |
|---|---|---|---|
| D-01 | REPLICATE copied the full parent quantity to every account | FIXED | Each account must declare `replicate_scale_bps`; child = target × scale / 10000 (floor). Missing scale is rejected. |
| D-02 | Broker account form offered only SANDBOX/LIVE; registries accepted LIVE | FIXED | API and UI accept PAPER/SANDBOX only. Migration `008_paper_only_boundary.sql` disables existing LIVE rows and adds database triggers that block creating or enabling LIVE accounts or connector profiles. |
| D-03 | SELECT_ONE had no routing rule (behaved like INDIVIDUAL) | FIXED | Versioned policy `SELECT_ONE_PRIORITY_V1`: fresh risk state first, highest `route_priority`, then `account_id`. Policy name is recorded in each run result. |
| D-04 | Every broker account created its own connection | FIXED | Accounts on the same broker server share one connection (`MT5 <server>`) and one connector profile. Records created before this fix are unchanged. |
| D-05 | Risk uses request-supplied limits/positions; no aggregate exposure or reservation | RECLASSIFIED | Cannot be fixed without stored limits, positions and reservations. Tracked as G-P3-01 … G-P3-03. |
| D-06 | No tests for strategy, allocation, risk, workflow, market data | FIXED | `backend/tests/test_trading_core.py` (25 tests). |
| D-07 | README described the first slice only | FIXED | README updated. |

## B. Missing capabilities

### Phase 1 — Connect and collect
| ID | Gap | Status |
|---|---|---|
| G-P1-01 | No working broker connector; connection approach not chosen | BLOCKED — decision 1 |
| G-P1-02 | No common adapter contract (capabilities, session, subscribe, account state, orders) | OPEN |
| G-P1-03 | No connection status lifecycle or reconnect handling | OPEN (needs G-P1-01) |
| G-P1-04 | No storage for raw/normalised ticks or quotes | OPEN |
| G-P1-05 | Feed assessment only runs on demo input, not a live stream | OPEN (needs G-P1-01) |
| G-P1-06 | Instruments lack tick/lot size, scales, currency, trading session | OPEN |

### Phase 2 — Strategy and signal
| ID | Gap | Status |
|---|---|---|
| G-P2-01 | No strategy registry, versions or lifecycle | OPEN |
| G-P2-02 | No strategy ↔ instrument ↔ account eligibility assignments (Blueprint §8.5) | OPEN |
| G-P2-03 | Signals, decisions and TradeIntents not stored; no frozen input snapshot | OPEN |
| G-P2-04 | No conflict rule between strategies on one instrument | OPEN |
| G-P2-05 | No real strategy | BLOCKED — decision 3 |

### Phase 3 — Risk and allocation
| ID | Gap | Status |
|---|---|---|
| G-P3-01 | No stored risk limits with units, scope, version and firm→instrument hierarchy | OPEN (values: decision 5) |
| G-P3-02 | No stored position/exposure state for risk to read | OPEN |
| G-P3-03 | No atomic per-account capacity reservation or release | OPEN |
| G-P3-04 | No kill controls | OPEN |
| G-P3-05 | No declared group policy when one child fails | OPEN |

### Phase 4 — Execution and reconciliation
| ID | Gap | Status |
|---|---|---|
| G-P4-01 | No orders table or order state machine | OPEN |
| G-P4-02 | No idempotent submission or dispatch worker | OPEN |
| G-P4-03 | No UNKNOWN-outcome handling (query and reconcile before retry) | OPEN |
| G-P4-04 | No fill/fee ledger, position or P&L projection | OPEN |
| G-P4-05 | No reconciliation against provider state or break workflow | OPEN |
| G-P4-06 | No realistic paper simulator (partial fills, rejects, timeouts) | OPEN (method: decision 4) |

### Cross-cutting
| ID | Gap | Status |
|---|---|---|
| G-X-01 | No audit trail of configuration and execution actions | OPEN |
| G-X-02 | Single operator login; no roles (admin, trading ops, risk approver, observer) | OPEN |
| G-X-03 | No trading screens (market data, signals, risk, orders, positions, reconciliation) | OPEN |
| G-X-04 | No documented backup/restore or operator runbook | OPEN |
| G-X-05 | Live site stuck at 8 Nov build; deploy rolls back on duplicate `n8n.coreeye.in` in the server Caddyfile | OPEN — server-side, unverified from repo |

## C. Owner decisions

| # | Decision | Unblocks |
|---|---|---|
| 1 | Connector: licensed mtapi direct connection, terminal bridge, or an official-API broker first (e.g. Upstox / crypto testnet) | G-P1-01, G-P1-03, G-P1-05 |
| 2 | First instrument and market session | G-P1-06, Phase 2 |
| 3 | First real strategy rules | G-P2-05 |
| 4 | Paper method: provider sandbox, own simulator, or both | G-P4-06 |
| 5 | Initial risk limit values and units | G-P3-01 |

## D. Completion order

1. Fix defects D-01 … D-07 — **done**.
2. Phase 2–4 foundation that does not depend on the broker: strategy registry and assignments, stored signals/intents, risk limits and reservations, order state machine, fill ledger, audit trail, paper simulator — all with tests.
3. Connector once decision 1 is made, behind the adapter contract (G-P1-02).
4. One real feed → reconciled paper order: one instrument, one strategy, two accounts.
5. Trading screens on real data.
