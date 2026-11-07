# COREI-OS-MINI — Trading Process Flow and Build Phases

**Document ID:** COM-BPD-TRADING-001  
**Version:** 1.4  
**Date:** 4 November 2026  
**Status:** Final simplified process-flow baseline  
**Parent blueprint:** COREI-OS-MINI End-to-End Trading Platform Blueprint v1.2  
**Scope:** Complete prototype trading workflow; technology and code remain separate stages

---

## 1. Purpose

COREI-OS-MINI follows one understandable operating loop:

**Connect → Collect → Validate → Analyze → Apply strategies → Generate signals → Resolve candidate account allocation → Run final child-level risk → Execute → Reconcile → Monitor and improve.**

The prototype should implement this flow with a small, simple deployment. It must preserve correct market data, explain each trading decision, prevent unauthorized or excessive orders, handle provider failures safely, and show the outcome for every account. The first release activates only a small approved subset; the underlying model supports growth across providers, accounts, instruments, and strategies.

This document defines the complete build sequence and the minimum controls needed for a reliable trading process. It keeps the workflow direct and avoids adding administrative governance that does not protect a trading decision or account outcome.

---

## 2. Multi-dimensional operating model

These are independent dimensions connected through explicit assignments. They are not nested in a way that forces one provider, one account, one instrument, or one strategy.

| Dimension | Required relationship |
|---|---|
| Provider / exchange | One provider may have multiple connections and accounts. A connection may provide data, trading, or both. |
| Account | One account may be eligible for many instruments and strategies. Each account retains separate capital, orders, fills, and risk. |
| Instrument | One canonical instrument may map to several provider symbols, data sources, strategies, and execution accounts. |
| Strategy | One strategy may evaluate many instruments and route eligible intents through multiple provider/account destinations. |

The same approved assignments must support all operator entry points:

- **Provider-first:** choose provider, then its connections/accounts, instruments, data sources, strategies, and permitted execution destinations.
- **Account-first:** choose account, then its eligible instruments, assigned strategies, allowed data sources, and limits.
- **Instrument-first:** choose instrument, compare provider feeds, run assigned strategies, and route eligible intents to accounts.
- **Strategy-first:** choose strategy, evaluate its eligible instrument universe, select data sources, and route each intent to approved provider/accounts.

**Data selection and order routing are separate.** COREI may collect XAUUSD data from several providers, select or compare feeds for a strategy, and execute the resulting intent through a different eligible provider/account.

A mapping must be explicit before it can be used. The platform supports many combinations without automatically enabling every possible combination.

---

## 3. End-to-end trading process

| Step | Process | Result and minimum control |
|---|---|---|
| 1. Configure | Register provider connections, authorized accounts, canonical instruments, provider-symbol mappings, strategies, account eligibility, operating mode, and risk limits. | Incomplete or disabled mappings cannot be used. Credentials stay outside source code and logs. |
| 2. Connect | Establish provider sessions and identify each connection's capabilities: data, trading, account discovery, order lookup, and supported order actions. | Connection health and trading permission are separate statuses. |
| 3. Collect | Receive market data and reference/account data from every enabled provider/account. Preserve provider, connection, native symbol, source time, receipt time, and sequence where available. | Keep source observations distinct; do not collapse feeds merely because symbol and timestamp match. |
| 4. Validate and map | Check message shape, instrument mapping, freshness, ordering/gaps, price/quantity bounds, session status, and source clock confidence. | Invalid or stale inputs are marked and excluded from strategies that require valid data. |
| 5. Select data | For each instrument and strategy, apply its data-source policy: eligible mappings, freshness, completeness, spread/quote sanity, source health, clock confidence, and measured latency. Retain comparison feeds. | Choose an eligible primary/comparison feed or produce no-trade. A fast-looking but invalid feed is never selected. |
| 6. Analyze market | Build the required market state/features from the approved observation set and strategy configuration. | The decision records the exact input snapshot and versions used. |
| 7. Evaluate strategies | Run all enabled strategy versions assigned to the instrument, or scan each strategy's approved instrument universe. | Strategies are independent evaluations; conflicts use an explicit rule or produce no-trade. |
| 8. Generate signal and intent | Emit a signal, decision, and candidate TradeIntent with instrument, side/action, desired size basis, validity/expiry, reason, strategy version, and lineage. | No direct broker order is created at this stage. |
| 9. Resolve candidate destinations | Find eligible provider connections/accounts and define the dispatch mode: INDIVIDUAL, SELECT_ONE, ALLOCATE, or REPLICATE. Form proposed child intents with their proposed sizes. | Candidate allocation is not approval to submit. Never copy full parent size to every account or redistribute a failed child silently. |
| 10. Run final child risk and permission | Check each proposed child against current account state and aggregate strategy, instrument, portfolio, and firm exposure. Apply limits, policy, mode, session, order constraints, and approved sizing. | Block any child that fails. Reserve account capacity atomically for approved children before submission. Missing or stale risk/account state blocks new exposure. |
| 11. Execute and track | Submit only risk-approved, reserved orders through the provider adapter; record acknowledgement, rejection, partial fill, fill, cancellation, or timeout/unknown state. | Stable order identity prevents duplicate submission. Unknown outcomes are queried/reconciled before retry or alternate routing. |
| 12. Update and reconcile | Record fills, fees, balances/positions, ledger entries, portfolio projection, and provider statement/evidence. Compare COREI state with provider state. | Show each account's result separately; preserve breaks and require an explicit resolution before affected trading resumes. |
| 13. Monitor and learn | Show data health, selected sources, active strategies, risk blocks, orders, fills, exposure, P&L basis, and reconciliation status. Use outcomes for evaluation and propose strategy changes. | Learning can propose a new version; it does not silently change an active strategy or risk limit. |

### 3.1 Core operator examples

**Instrument-first — XAUUSD:** connect approved provider feeds → collect all eligible XAUUSD observations → compare quality and latency → choose the feed set for each assigned strategy → evaluate strategies → create separate intents → resolve candidate account allocation → final risk-check each child and reserve capacity → execute and reconcile each account independently.

**Strategy-first:** select an approved strategy → enumerate eligible instruments → resolve each instrument's data sources and mappings → evaluate and emit separate intents → resolve candidate provider/accounts and child allocations → aggregate and run final child risk across shared accounts → execute and reconcile each result independently.

---

## 4. Build phases

The phases describe the order of building usable capabilities. Each phase ends with a working demonstration and simple pass/fail checks.

### Phase 1 — Provider connections and market data

Build provider/account/instrument registration, provider adapters, feed collection, symbol mapping, validation, source-quality status, and market-data visibility.

**Demonstrate:** one canonical instrument can receive and display data from multiple enabled feeds; source identity and timestamps remain visible; stale or malformed data is excluded or causes no-trade.

**Gate:** connections, mappings, data quality, reconnect behavior, and failure visibility work for the selected providers.

**Implementation checkpoint — connector profiles (0.6.0, 8 November 2026):**
Provider/account/instrument registries and editable connector profiles are implemented.
Migration 004 adds one profile per connection: connector family, intended adapter
identifier, PAPER/SANDBOX/LIVE environment, terminal/API runtime and broker server
label. Existing records are retained. Configuration does not establish a session.
Runtime status is explicitly NOT_IMPLEMENTED / NOT_CONNECTED / NOT_OBSERVED.

**Usability checkpoint — 0.6.1:** field help and named selections clarify the
registries; optional terminal host/instance inventory labels distinguish remote
terminal machines from broker servers. Workspace views can open separately without
replacing the main saved layout. These changes do not satisfy runtime verification.
The README defines the planned outbound authenticated bridge and its acceptance
requirements, including separate operator, broker and bridge credentials.

The next Phase 1 work is the first authenticated terminal bridge and verified
account/session binding, instrument discovery, live quote ingestion and recovery.
The Phase 1 demonstration and gate above have NOT passed yet. MT4/MT5 Windows and
Ubuntu/Wine, cTrader, broker/exchange APIs, FIX and dedicated feeds remain within
scope; listing a connector family is not implementation of its adapter.

Before operational execution, complete stable account identity across connections,
contract specifications, strategy/account attribution, shared account risk and
reservations, durable order identity, uncertain-outcome reconciliation and scoped
operator controls. These belong within the existing phases, not additional services.

### Phase 2 — Market analysis, strategies, and signals

Build market snapshots/features, strategy configuration/version, instrument universes, strategy evaluation, conflict/no-trade behavior, signals, decisions, and TradeIntents.

**Demonstrate:** multiple strategies can evaluate the same instrument; one strategy can evaluate multiple instruments; the same input and configuration produce the same decision; each signal explains its inputs and reason.

**Gate:** strategy outputs are reproducible, bounded by their configured universe, and traceable to data and strategy version.

### Phase 3 — Risk, account eligibility, and allocation

Build candidate destination selection and allocation, shared exposure calculations, policy checks, per-child sizing, and atomic account reservations. Candidate routing must be resolved before final account-level risk; no provider order is submitted until every child passes its final check.

**Demonstrate:** multiple strategies/instruments share an account without exceeding combined limits; each account child has its own size and risk result; missing or stale risk/account state blocks new exposure.

**Gate:** candidate children are allocated deterministically; aggregate and per-account risk use current state; each approved child reserves capacity before submission; allocation and partial-group behavior are visible.

### Phase 4 — Paper execution, order handling, and reconciliation

Build paper/sandbox execution, provider order adapters for the selected provider, order lifecycle, fills/fees, portfolio updates, reconciliation, and operator controls.

**Demonstrate:** orders can be submitted, partially filled, rejected, cancelled, or timed out; every account outcome is tracked; unknown submissions are reconciled before retry; paper mode cannot reach a live endpoint.

**Gate:** the complete path from data to reconciled account outcome works for the chosen paper scope and failure cases.

---

## 5. Minimum operating controls

These are part of the successful trading process, not optional administrative ceremony:

1. **Explicit assignments:** provider, connection, account, instrument, strategy, mode, and route must be enabled for the specific action.
2. **Good inputs:** strategies use validated and sufficiently fresh data; source quality and time confidence are visible.
3. **Explainable decision:** every signal and intent identifies the strategy, instrument, input snapshot, configuration version, and reason.
4. **Final risk before submission:** after proposed child allocation, risk uses current positions and pending orders; shared exposure is checked across strategies and instruments; account capacity is reserved before provider submission.
5. **Separate child orders:** group execution creates independently sized and tracked per-account children.
6. **Safe unknown state:** no blind retry, failover, or residual redistribution after an ambiguous provider submission.
7. **Paper boundary:** prototype execution cannot reach live trading.
8. **Visible reconciliation:** provider facts and COREI records are compared; unresolved differences remain visible.
9. **Basic access and records:** protect sensitive configuration, risk settings, and execution controls with authenticated access; record material changes and execution actions.

---

## 6. Failure handling

| Failure | System response |
|---|---|
| Provider/data feed disconnects | Mark the source unavailable; use an approved eligible alternative or no-trade. Do not hide the feed gap. |
| Stale, malformed, or unmapped data | Exclude it from affected strategies and record the reason. |
| No eligible strategy, account, or route | Produce no-trade/block; do not infer a mapping. |
| Risk limit reached or state unavailable | Reject/block the new exposure and show the applicable reason. |
| Provider rejects order | Record the provider reason and account-specific result. |
| Provider response is unknown | Hold the order as UNKNOWN, query provider state, then reconcile before any retry. |
| Partial fill or group failure | Update each child separately. Keep filled, rejected, and unknown quantities distinct. |
| Account/portfolio mismatch | Mark reconciliation break and block affected new exposure until resolved. |
| Operator interface unavailable | Keep backend safety checks authoritative; restore the interface and reconcile operator actions afterward. |

---

## 7. Simple operating configuration

Keep configuration limited to what is needed to operate the initial scope:

- Providers and their data/trading capabilities
- Connections and authorized accounts
- Canonical instruments and provider-symbol mappings
- Strategies and eligible instruments
- Per-strategy data-source policy
- Strategy/account/instrument/provider eligibility assignments
- Operating mode and dispatch mode
- Approved risk limits and allocation weights
- Basic user roles: administrator, trading operations, risk approver, observer

Use clear status values such as **enabled, disabled, degraded, blocked, and unknown**. Show the reason for every non-enabled state. No large workflow engine or multi-layer approval chain is required for the prototype.

---

## 8. Deferred items

Keep these outside the initial process-flow implementation: Kubernetes, Docker/Compose, Helm, Kafka, Redis, distributed replacement systems, microservices/service mesh, multi-node high availability, automated broker failover, live trading, high-frequency performance guarantees, autonomous model learning, broad asset-class rollout, and extensive enterprise approval workflows.

Add them only when a demonstrated product, capacity, reliability, or operational requirement justifies them.

---

## 9. Decisions needed before the first implementation slice

| Decision | Needed to start |
|---|---|
| First provider(s) and connection method | Provider API/sandbox capabilities and account support |
| First instrument and market session | Canonical mapping and contract/session details |
| First strategy | Versioned rules and reproducible examples |
| Initial accounts and allocation | Approved paper account set and dispatch behavior |
| Paper method | Provider sandbox, simulator, or both |
| Initial risk limits | Values, units, scope, and breach behavior |
| Deployment target | Existing host or new host and access boundary |

The technology inventory is a separate proposal and must be checked against these choices before implementation. This process-flow document does not authorize live trading.
