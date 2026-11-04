# COREI-OS-MINI — End-to-End Trading Platform Blueprint

**Document ID:** COM-BP-TRADING-001  
**Version:** 1.2  
**Date:** 4 November 2026  
**Status:** Final architecture baseline for phase and implementation planning  
**Scope:** Trading platform architecture and operating lifecycle  
**Technology:** See the companion technology inventory; confirm exact choices against the selected provider and operating scope before coding

---

## 1. Executive Summary

COREI-OS-MINI is a trading operating platform that governs the complete lifecycle from provider and market data through strategy decisions, risk approval, order submission, execution, portfolio state, reconciliation, and operator learning. The platform must make the relationship between a decision and its final account outcome visible, controlled, traceable, and recoverable.

The architecture treats multiple providers, multiple accounts per provider, multiple portfolios, strategies, instruments, and operator roles as first-class concepts. Provider adapters translate provider-specific behavior into COREI contracts while exposing capability differences. No adapter or integration is assumed to support every order type, data stream, or account operation.

The four trading dimensions—provider, account, instrument, and strategy—are modeled independently and composed through explicit, versioned eligibility and allocation mappings. The architecture supports provider-first, account-first, instrument-first, and strategy-first operation. It does not activate every possible combination by default: only approved, compatible combinations may collect data or route execution.

The mini project has a deliberate infrastructure boundary: it does not implement Kubernetes, Docker or Compose, Helm, Kafka, Redis, or a distributed replacement stack. This boundary removes infrastructure products, not trading requirements. Determinism, durable state, risk enforcement, audit, safe order handling, recovery, and operational visibility remain mandatory. The physical technology that satisfies these contracts is not selected by this blueprint.

This document defines the logical platform, authoritative state, component responsibilities, lifecycle flows, controls, failure behavior, multi-account model, operational views, and architecture gaps. It is the trading blueprint only. It does not define building phase documents, select tools or technology, or authorize live trading.

**Disposition:** This is the finalized architecture baseline for COREI-OS-MINI. Provider, instrument, strategy, operating-mode, and numeric risk inputs remain to be confirmed before the corresponding implementation work. The companion phase and technology documents define the next planning steps; this blueprint does not authorize live trading.

---

## 2. Mission and Design Outcome

COREI-OS-MINI follows the COREI operating chain:

> **Observe → Discover → Understand → Decide → Act → Monitor → Learn → Improve**

For every material trading action, authorized users must be able to determine:

- What happened and which source reported it
- Which provider, account, portfolio, strategy, instrument, and configuration were involved
- What decision was made from which exact inputs and versions
- Which risk and policy rules allowed, modified, rejected, or halted the action
- What the provider acknowledged, rejected, filled, or left uncertain
- What the current reconciled financial state is
- What operator action is available, who may take it, and what it will affect

The outcome is an operational system that exposes trading state, authority, lineage, exceptions, and recovery—not a set of disconnected screens.

---

## 3. Authority, Scope, and Interpretation

### 3.1 Source hierarchy

This blueprint was reconciled against the supplied COREI source set in its declared dependency order:

1. COREI ANCHOR LAW and PROJECT INDEX & CONTEXT GUIDE
2. Master Phases Blueprint and Trading Platform Blueprint 3.1
3. Universal Phase Structure
4. Trading definitions in Phase A, Phase B, Phase C, and Phase D
5. Validation and governance sources: Team Blue, Team Red, authoritative stage index, platform execution model, lifecycle, build flow, and continuity documents

The mini-project scope in the adopted COREI-OS-MINI persona takes precedence where the wider COREI implementation assumes excluded infrastructure. Trading mission, phase ownership, risk authority, event-time discipline, fixed-point financial calculations, traceability, audit, and recovery intent remain in force.

### 3.2 Included

- Trading-domain architecture and boundaries
- Market/reference-data-to-trade lifecycle
- Multi-provider, account, portfolio, and strategy relationships
- State authority and record lineage
- Controls, failure behavior, reconciliation, and operations
- Logical requirements for a lean deployment

### 3.3 Deferred

This blueprint does not select programming languages, frameworks, databases, brokers, APIs, market-data vendors, message buses, observability products, hosting, deployment products, or UI libraries. It does not set provider-specific numerical limits or claim a provider supports an operation until current documentation and sandbox behavior establish it.

---

## 4. Evidence-Based Reference Findings

References inform operating concepts; they are not copied implementation or unverified claims.

| Reference | Publicly documented finding | COREI-OS-MINI use | Evidence limit |
|---|---|---|---|
| [COREI repository](https://github.com/coreeyegroup/corei) | Contains COREI architecture, phase blueprints, portfolio/execution stages, and governance decisions. Its trading blueprint defines Phase 0/A/B/C/D, market-data and decision flow, account routing, risk, orders, fills, reconciliation, and state. | Preserve COREI ownership boundaries and decision-to-fill traceability; adapt infrastructure assumptions explicitly. | Documents establish intended architecture, not proof that every component is implemented or operational. |
| [Core Neha repository](https://github.com/coreeyegroup/core-neha) | README describes modular backend/frontend boundaries, role/access functions, decision records, and operator documentation. Operations guide covers setup, diagnosis, backup, restore testing, and rollback. | Transfer clear module ownership, operator visibility, decision records, and operational documentation. | It is an HMS and uses a container stack. Neither its domain model nor deployment technology is adopted here. |
| [Westernpips public documentation](https://docs.westernpips.com/doc/public/) and [Connections guide](https://docs.westernpips.com/documentation/westernpips-webcliker/wc-connections/) | Public docs expose a connector catalogue and connection workflow with named account/provider fields, duplicate-account handling, and saved connections used as quote sources. | Study connector capability catalogs, independent connection configuration, source selection, and operational connection views. | Public docs do not disclose complete internal implementation, execution guarantees, or suitability for COREI. |
| [BlackRock Aladdin](https://www.blackrock.com/aladdin) and [Aladdin FAQs](https://www.blackrock.com/aladdin/about-us/about/faqs) | Describes integrated portfolio, trading, compliance, operations, data, and risk oversight workflows. | Benchmark integrated context and portfolio/risk visibility across operator functions. | Product descriptions do not specify a buildable COREI architecture. |
| [Binance Institutional](https://www.binance.com/en/vip-institutional-services) and [Portfolio Accounts announcement](https://www.binance.com/en/support/announcement/detail/192a7ab5b39b42d2a24330f7d15ef3e8) | Describes institutional services and portfolio-account structures for trading-team and strategy workflows. | Benchmark explicit portfolio/account segregation and strategy operations. | Exchange-native account semantics are provider-specific and cannot be generalized to every broker. |
| “Boomerang” | The name does not uniquely identify a product from the current project instructions. | Keep as unresolved. Add only after its exact URL and product identity are confirmed. | Do not infer which vendor or product was intended. |

Public-reference review date: 4 November 2026. Product capabilities can change; phase documents must re-check provider material before specifying integrations.

---

## 5. Architecture Principles and Invariants

1. **Single responsibility by domain.** Strategy logic produces decisions; risk governs permission; execution translates approved intent into provider orders; adapters translate protocols; portfolio accounting derives financial state.
2. **Risk can block execution.** No strategy, operator, adapter, or router can bypass risk and policy.
3. **Deterministic decision logic.** Given the same canonical inputs, configuration versions, and governed event order, strategy, decision, risk, sizing, and policy outcomes must be reproducible.
4. **Event time and processing time are distinct.** Preserve source event time and local receipt/processing times separately. Missing or invalid source time is visible and governed, never disguised as exchange time.
5. **Financial precision is explicit.** Prices, quantities, money, exposure, P&L, and risk use defined fixed-point scales, rounding, and overflow behavior. Binary floating point is prohibited for authoritative financial calculations.
6. **External inputs are isolated.** CORE and EXTERNAL intents are classified, validated, risk-checked, policy-checked, and traceable. External inputs cannot modify strategy logic or configuration.
7. **Account boundaries are enforced.** Assignment, capital, risk, permissions, mode, provider binding, and kill controls are scoped to accounts and portfolios.
8. **No blind resubmission.** A timeout after order submission creates an uncertain state until provider state is queried or reconciled.
9. **No implicit cross-provider failover.** An order cannot silently move to another broker account. Multi-destination routing must be explicitly configured, risk-approved, and represented as separately accountable per-account orders.
10. **Provider behavior is capability-declared.** Support for order types, cancel/replace, data, account state, or history is established per adapter.
11. **Views are derived; actions are governed.** UI state is a view of authoritative platform/provider records, never a hidden source of financial truth.
12. **Fail safe and explain.** If required market, account, risk, or configuration state is stale or unknown, block new exposure-increasing orders. Protective actions follow an explicit policy.
13. **Paper-first activation.** Initial validation defaults to live-off and paper workflows. Live activation requires separate readiness evidence and explicit authorization.
14. **Technology-light by design.** These are logical contracts. The companion inventory provides the shortlist; exact choices are confirmed against the selected provider and operating scope before coding.

---

## 6. System Boundary and Context

### 6.1 External actors and systems

- Broker, exchange, liquidity, and market-data providers
- Historical/reference-data sources
- Strategy research and external signal systems
- Authorized operators, researchers, risk reviewers, and auditors
- Notification and reporting consumers

### 6.2 Platform responsibility

COREI-OS-MINI owns canonical identifiers, normalized contracts, assignment and policy state, decision provenance, risk/policy decisions, internal order lifecycle, provider request correlation, portfolio projections, reconciliation status, user authorization, and audit evidence.

A provider remains authoritative for its own received order state, executions, balances, positions, fees, and account restrictions. COREI records and reconciles provider evidence; it does not overwrite a provider fact merely because internal state disagrees.

### 6.3 Lifecycle overview

~~~mermaid
flowchart TB
    P[Provider feeds and account data] --> N[Validate, normalize, preserve provenance]
    N --> S[Market state and strategy evaluation]
    S --> D[Signal and decision, or controlled external input]
    D --> R[Account routing, risk, policy, and sizing]
    R --> O[Order state machine and provider adapter]
    O --> X[Provider acknowledgements and executions]
    X --> L[Financial ledger and portfolio projections]
    L --> C[Reconciliation, monitoring, and operator control]
    C --> S
~~~

The feedback path represents governed research and approved version changes. It does not permit live results to automatically rewrite a strategy or its parameters.

---

## 7. Logical Architecture and Ownership

| Domain | Owns | Must not own |
|---|---|---|
| Governance/configuration | Roles, approval authority, versioned configuration, environment/mode policy, limits, release status | Order execution or hidden strategy changes |
| Provider connectivity | Sessions, protocol translation, capability declaration, health, request/response correlation | Strategy decisions, risk approval, portfolio policy |
| Market/reference data | Source records, canonical mapping, validation, quality, freshness, historical access | Broker order state or strategy-specific conclusions |
| Research/strategy | Strategy definitions, input requirements, signals, lifecycle and version | Provider calls, order submission, risk overrides |
| Decision | Signal validation, aggregation/conflict resolution, TradeIntent | Risk approval or account mutation |
| CEIL | External/manual intent intake, validation, source identity, deduplication and isolation | Entry into internal strategy/signal path |
| Account routing | Resolve eligible account/portfolio assignments and modes; create per-account intent | Changing intent economics silently or transferring capital |
| Risk/policy | Pre-trade limits, exposure/reservation checks, policy decision, kill/disable enforcement | Broker protocol handling or strategy generation |
| Sizing/order construction | Convert approved intent into bounded canonical order request and lifecycle identity | Re-evaluating alpha or bypassing risk |
| OMS | Canonical order state, legal transitions, provider correlation, duplicate/late response handling | Market interpretation or signal generation |
| EMS/provider adapter | Translate canonical orders/subscriptions to provider contracts and capture responses | Changing risk-approved economics without a new decision |
| Fill/financial ledger | Immutable execution facts, fee/cash events, positions, realized/unrealized views | Treating an unconfirmed request as a fill |
| Portfolio/exposure | Per-account and aggregate state, attribution, exposure and P&L projections | Mutating source events or hiding discrepancies |
| Reconciliation | Compare provider facts with internal ledger/projections and create breaks | Silently repairing or deleting history |
| Operations/API/UI | Present context, permissions, state, alerts, approvals, and controls | Becoming alternate financial authority |
| Audit/observability | Record who/what/when/why, lineage, health, metrics, alerts, operational events | Storing secrets or changing trading decisions |

This is a logical modular architecture. It does not prescribe process count or deployment topology.

---

## 8. Core Entities and Relationships

### 8.1 Entity hierarchy

~~~text
COREI instance
└── Operating entity / desk
    ├── User, role, permission, approval authority
    ├── Provider
    │   └── Provider connection
    │       └── Provider account
    ├── Portfolio
    │   ├── Strategy allocation
    │   └── Account allocation / eligible account set
    ├── Canonical instrument
    │   └── Provider instrument mapping(s)
    ├── Strategy definition and immutable version(s)
    ├── Market-data source(s)
    └── Environment / operating mode
~~~

### 8.2 Relationship rules

- One provider may expose multiple connections and accounts.
- One account belongs to one provider identity and cannot be silently rebound to another provider.
- A portfolio has explicit account and strategy allocations. Changes are versioned and effective-dated.
- A strategy is assigned to selected accounts and instruments only through explicit configuration.
- Multiple strategies may target one account only when capital/risk allocation, order interaction, and portfolio aggregation are defined.
- Accounts may use different operating modes. Mode is enforced at routing and again before submission.
- A canonical instrument can map to provider symbols with different contract specifications, sessions, tick/lot sizes, currencies, and permissions. Mapping is versioned and validated.
- Credentials are referenced by secure secret identifiers; they are not embedded in business records or audit messages.
- Every order and fill carries stable COREI identities plus provider-native identifiers when available.

### 8.3 Routing relationship

**Provider → Connection → Account → Portfolio allocation → Strategy assignment → Instrument mapping → Per-account intent → Order**

Every edge is validated at decision time and revalidated immediately before submission. Disabled, stale, unauthorized, or incompatible relationships block new orders.

### 8.4 Relationship cardinality

| Relationship | Cardinality | Governing link |
|---|---|---|
| Provider ↔ connections | One provider to many independent data/trading connections; a connection may expose multiple authorized accounts | ProviderConnection and declared capabilities |
| Provider ↔ accounts | One provider to many accounts; each account has one provider identity and may use distinct data and trading sessions where the provider supports it | ProviderAccount and account binding |
| Account ↔ instruments | Many-to-many; an account can trade many instruments and an instrument can be available in many accounts | AccountInstrumentEligibility with provider instrument mapping |
| Account ↔ strategies | Many-to-many; each account can run multiple strategies and each strategy can be assigned to multiple accounts | StrategyAccountAssignment with mode and allocation |
| Strategy ↔ instruments | Many-to-many; a strategy may select a universe and an instrument may be evaluated by many strategies | StrategyInstrumentAssignment or versioned universe rule |
| Instrument ↔ providers | Many-to-many; an instrument may have data and execution mappings at multiple providers | ProviderInstrumentMapping and capability/quality status |
| Strategy ↔ execution destinations | Many-to-many through the approved combination of strategy, instrument, portfolio, provider, connection, and account | StrategyExecutionAssignment / route policy |

The logical model can represent all four user-described perspectives:

1. **Provider-first:** select a provider and its eligible accounts, then choose instruments, data sources, strategies, and execution mode.
2. **Account-first:** select an account, then choose eligible instruments and one or more assigned strategies.
3. **Instrument-first:** select an instrument, compare approved data sources, run eligible strategies, then route approved intents to eligible accounts/providers.
4. **Strategy-first:** select a strategy, evaluate its eligible instrument universe, select approved data feeds, and route each intent to eligible accounts/providers.

These are views over the same assignment model, not separate implementations with different ownership rules.

### 8.5 Explicit assignment matrix

Represent a tradable deployment as an approved row or rule over:

**strategy version × canonical instrument × portfolio × provider × connection × account × operating mode**

Each row declares eligibility, data-feed policy reference, execution route/group, allocation rule, risk profile, effective interval, approval state, and configuration version. Ineligible or missing mappings deny by default. Broad universe or account-group rules must resolve to the same explicit, auditable effective set before they can authorize orders.

---

## 9. Canonical Data, Time, and Lineage

### 9.1 Data layers

1. **Provider raw observation:** preserve native source, native symbol/ID, source timestamp, receipt timestamp, sequence/ID, payload digest, and connection identity. Redact secrets.
2. **Validated canonical observation:** validate shape, numeric bounds, time, ordering/sequence, instrument mapping, and quality. Keep a reference to raw evidence and transformation/configuration version.
3. **Derived market state:** build bars, features, indicators, and synchronized snapshots with explicit windows and ordering rules. Derived data is versioned and reproducible.
4. **Decision inputs:** freeze the exact market/reference/risk/configuration snapshot used for each decision. Later changes cannot alter what a historical decision claims to have observed.

### 9.2 Time rules

Records distinguish:

- **Source event time:** provider/exchange time, when supplied and valid
- **Received time:** when COREI received the record
- **Processed time:** when a component processed it
- **Effective time:** when configuration or an operational action applies

Decision logic uses governed event time and explicit staleness/ordering policy. If source time is missing, invalid, future-dated, or stale, mark quality and block strategies that require trusted time. Receipt time may be used only under a named fallback policy that records the fallback; it is never presented as source time.

### 9.3 Ordering and deduplication

- Preserve provider and instrument context. Do not collapse two providers’ quotes solely because canonical symbol and timestamps appear similar; they may be distinct execution venues or latency-arbitrage inputs.
- Deduplicate only under a declared identity rule, such as provider + connection + native instrument + sequence/execution ID. Payload digest may detect retransmission where no sequence exists.
- Apply a deterministic tie-breaker for equal event times. The ordering key is explicit and versioned.
- Preserve a serialized logical sequence per account so concurrent intents cannot each pass against the same stale exposure snapshot.

Every source observation remains available for comparison even when a selected strategy feed is chosen. “Best feed” selection does not delete or overwrite other broker/exchange observations.

### 9.4 Lineage

Lineage connects:

**Raw observation/input → canonical record/snapshot → strategy version → signal → decision/TradeIntent → routing decision → risk/policy/sizing result → order → provider response → fill → ledger entry → portfolio projection/reconciliation**

Use immutable IDs and canonical serialization for hashes. Hashing aids integrity; it does not prove source authenticity or replace access controls, provider evidence, or audit retention.

### 9.5 Multi-source data selection and latency

Data-source selection is independent of execution routing. A market-data connection may be data-only, trading-only, or support both; a quote provider need not be the account that receives the order.

For each canonical instrument and strategy (or approved strategy group), a versioned DataSourcePolicy selects eligible source observations using declared criteria:

- Source entitlement and instrument/contract mapping validity
- Data freshness and event-to-receipt age
- Sequence gaps, duplicates, completeness, and out-of-order rate
- Spread/quote sanity, market state, and source health
- Clock quality and timestamp confidence
- Measured feed-processing and strategy-input latency

The policy can select a primary source, retain additional comparison/shadow sources, and define deterministic degraded/no-trade behavior. It must not select a stale or malformed feed merely because it reports a numerically attractive price. Tie-breaking and fallback are deterministic and recorded with each decision.

For HFT-related assessment, measure separately: provider source event time to local receipt when clocks are trustworthy; local receipt to normalized availability; normalized availability to strategy decision; decision to local order send; send to provider acknowledgement; and acknowledgement to execution/fill. Record distributions and clock-confidence metadata, not only averages. Provider-side or exchange-side one-way latency may be reported only when the provider exposes trustworthy timestamps and clock comparability is established. Numerical latency targets and required clock/hardware techniques are deferred to the phase documents and technology-selection step.

---

## 10. End-to-End Workflow

### Step 0 — Governance and configuration

Establish users, roles, approval authority, operating entity, environment, providers, connections, accounts, instrument universe, strategy versions, risk limits, and modes. Each setting has an owner, scope, version, approval status, and effective time. Snapshot the full configuration used by each decision. Reject incomplete/conflicting configuration; do not fill missing limits from unapproved code defaults.

### Step 1 — Provider session and data intake

The adapter establishes a provider session using a referenced credential and required protocol. Record connection state, capability set, account identity, permissions, rate limits, and health evidence. Receive market, reference, account, order, fill, and balance data as separately classified contracts. A healthy connection does not mean data is fresh or trading is enabled.

### Step 2 — Validation, normalization, and reference mapping

Validate schema, source identity, event/receipt time, sequence, numeric fields, instrument mapping, and quality. Preserve raw provenance and transformation versions. Mark each item valid, delayed, stale, incomplete, out-of-sequence, unmapped, rejected, or quarantined. Unknown instruments or incompatible contract specifications are blocked.

### Step 3 — Market state, history, and derived features

Build canonical quote/bar/trade representations while preserving provider-specific observations. A strategy/instrument DataSourcePolicy compares all eligible provider feeds, selects its governed input set, and records excluded/degraded sources and reasons. Generate features from frozen input windows and versioned rules. Expose freshness, gaps, source coverage, measured latency, clock confidence, and quality alongside values. If strategy data requirements fail, strategy state is blocked/no-trade with an explicit reason.

### Step 4 — Strategy evaluation and signal lifecycle

Strategy consumes only declared canonical inputs and configuration version. Lifecycle is research → historical validation → approval → paper → monitored deployment → pause/rollback/retirement. Signals identify strategy/version, instrument, direction, score, validity, event time, input snapshot, and deterministic identity. Expired, duplicate, invalid, or out-of-scope signals are rejected with a reason. A signal expresses intent, not permission.

### Step 5 — Decision and controlled external inputs

Decision logic validates signal eligibility, resolves conflicts with versioned rules, and emits TradeIntent or no-trade. Every intent identifies origin CORE or EXTERNAL.

External/manual intents enter through CEIL, not through the internal signal engine. CEIL validates source, schema, identity, time, instrument, direction, size, duplication, freshness, and rate limits. CORE and EXTERNAL intents pass the same risk and policy controls. EXTERNAL may receive tighter constraints and isolated capital.

The intent and configuration snapshot are immutable and traceable.

#### CEIL-specific controls

- Canonical external payload contains source identity, instrument, direction, size, source time, and explicit identifiers/configuration needed for validation. The payload is immutable after acceptance and has a deterministic identity under a versioned canonical encoding.
- Validate schema, completeness, instrument eligibility, positive size, direction, timestamp age, duplicate identity, and source authorization before risk evaluation.
- Process a set of external intents in a deterministic order, for example by source event time then external-intent ID. Define a maximum rate per source, instrument, and time window; reject excess inputs deterministically.
- External capital/risk allocation is explicitly bounded and isolated from CORE strategy allocation. All external intents still pass the common risk engine before policy and execution.
- An independent external-input shutdown blocks CEIL intake without disabling unrelated CORE-generated intents.
- CEIL output never feeds the internal strategy, signal, or decision-generation path.

### Step 6 — Resolve candidate destinations and allocation

Resolve the eligible provider connection/account destinations from the approved assignment matrix. Confirm the strategy version, instrument, portfolio, provider connection, account, session, operating mode, and declared dispatch semantics. Create proposed child intents for the selected destinations, but do not treat routing or allocation as execution approval.

Every fan-out must explicitly mean INDIVIDUAL, SELECT_ONE, ALLOCATE, or REPLICATE. Define how target quantity/notional is divided or replicated before creating proposed children. Never copy the full parent quantity to every account by default or redistribute a rejected/failed child's remainder silently. Keep each child independently identifiable and trackable.

### Step 7 — Final child risk, sizing, and reservation

Run final checks against each proposed child and the combined exposure of all children sharing an account, portfolio, or firm limit. Check identity, permission, environment, account binding, data/config freshness, mode, market/session, current positions and pending orders, buying power/margin, strategy/instrument limits, order constraints, and policy approvals. Calculate the child size from approved inputs and instrument specifications using declared rounding and overflow rules.

Reserve account capacity atomically before any provider submission. Define how reservations are released or corrected after rejection, cancellation, fill, expiry, or an uncertain outcome. A child that fails a mandatory check is blocked; other children follow the declared group policy. Re-evaluate shared exposure when concurrent intents could consume the same capacity. No router, operator, strategy, or adapter can bypass this final check.

### Step 8 — Order construction and OMS

Create a canonical order only from an approved, sized intent. Record quantity, price constraints, time in force, account, instrument mapping, mode, client order ID, risk/policy references, and lineage. Enforce legal state transitions and monotonic cumulative executed quantity. Track COREI client order IDs separately from provider order and execution IDs. Preserve late, contradictory, or uncorrelated responses as exceptions.

Canonical lifecycle:

~~~text
CREATED → VALIDATED → RISK_APPROVED → POLICY_APPROVED → READY
       → SUBMISSION_PENDING → SUBMITTED/UNKNOWN
       → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
~~~

Alternative states include REJECTED, CANCEL_PENDING, CANCELLED, EXPIRED, REPLACE_PENDING, REPLACED, BLOCKED, and RECONCILIATION_REQUIRED. UNKNOWN is not successful or terminal and blocks duplicate submission until resolved.

### Step 9 — EMS/provider execution

Translate the canonical order only through declared adapter capabilities and current account/instrument constraints. Send one logical order with a stable client correlation identity where supported. Capture request time, response, provider order ID, rejection code, and transport state. On timeout/disconnect after send, query/reconcile provider state before deciding whether retry is safe. Never infer timeout means rejection. Unsupported order options are rejected before transmission with a reason.

### Step 10 — Acknowledgements, executions, and ledger

Record provider acknowledgements and executions as immutable source facts. A partial fill updates cumulative execution, remaining quantity, reservation, and position projection consistently. Record fees, commissions, funding, interest, corporate actions, and other cash adjustments as separate financial events where applicable. Link each fill to provider execution ID and COREI order/intent lineage. Unconfirmed requests never create fills. Corrections/busts are compensating events, not silent edits.

### Step 11 — Portfolio, exposure, and P&L

Calculate per-account, per-strategy, per-portfolio, and firm-level state using explicit ownership rules. Track positions, average cost, realized/unrealized P&L, fees, exposure by instrument/currency/asset class, buying power/margin where available, drawdown, and allocation. Define mark source, FX conversion source/time, valuation method, rounding, fees, and session/day boundaries. Track slippage as execution-quality evidence; do not subtract it again if already embedded in execution price and realized P&L. Aggregate related accounts while retaining account-level isolation and attribution.

### Step 12 — Reconciliation and feedback

Reconcile internal orders, executions, positions, balances, and cash events against provider evidence as provider capabilities allow. Classify breaks: missing/duplicate execution, quantity mismatch, price/fee/currency mismatch, unknown order, stale snapshot, mapping error, or unsupported event.

A material unresolved break blocks new exposure-increasing orders on the affected account or defined scope. It does not block unrelated accounts unless firm policy says so. Resolution records evidence, operator, authority, reason, before/after values, and linked compensating event. Performance analytics may inform research; strategy or parameter changes require versioning, review, validation, approval, and rollback plan. No automatic live deployment.

---

## 11. State Authority and Persistence Contract

Physical persistence products are deferred. The logical authority model is fixed:

| State | Authority | Derived view | Conflict behavior |
|---|---|---|---|
| Provider session | Provider response plus local adapter observations | UI health view | Show disagreement; block if required session state unknown |
| Market observation | Original provider observation | Canonical data and features | Preserve both; invalidate dependent derived state when quality fails |
| Configuration/assignments | Approved, versioned COREI configuration | Effective configuration view/cache | Reject stale/ambiguous config; attach version to decisions |
| Strategy/signal/decision | COREI record and input snapshot | UI/history projection | Reconstruct from governed inputs and versioned rules |
| Risk/policy | COREI decision record tied to limit/config snapshot | Risk dashboard | Never overwrite; correction is a new decision |
| Order | COREI OMS state plus provider evidence | Open-order view | Disagreement is unresolved; do not erase provider evidence |
| Fill/execution | Provider execution evidence recorded in COREI ledger | Position/P&L projection | Deduplicate by provider identity; corrections are compensating events |
| Balance/position | Provider snapshot plus COREI ledger-derived view, source/time visible | Portfolio projection | Show breaks; define provider-specific authority and procedure |
| Audit | Append-only audit record and correlated source references | Search/report view | No silent modification or deletion |
| UI state | None; view and user preferences only | Browser/session state | Never authoritative for trading/account state |

The mini architecture must provide a durable ordered record of accepted state-changing events and governed commands, atomically consistent with materialized state changes. Specific storage and retention choices are deferred to the technology stage. This is not a claim of Kafka-equivalent distributed durability or exactly-once external execution.

---

## 12. Multi-Provider and Multi-Account Model

### 12.1 Provider adapter capability contract

Each adapter declares:

- Supported asset/instrument types
- Data types, subscription behavior, timestamps, sequence IDs, and historical availability
- Order types, time in force, cancel/replace, client IDs, and idempotency behavior
- Account discovery and account-state capabilities
- Position, balance, execution, fee, and statement access
- Rate limits, sessions, reconnect, sandbox availability, and limitations
- Provider-specific mappings and normalization rules

Adapters share canonical contracts but expose differences. Unsupported, unknown, and degraded are distinct states.

### 12.2 Account assignment and capital isolation

- Allocation is explicit per account/portfolio/strategy and versioned.
- Orders/fills are attributed to the actual account; pooling cannot hide account constraints.
- Disablement, kill state, margin restriction, or provider outage affects configured scope unless a higher-level control escalates.
- Copy/multi-account dispatch creates independent PerAccountIntents and independent risk decisions. A parent intent does not require every child to trade.

### 12.3 Routing versus failover

- A route group contains only destinations eligible for instrument, ownership, mandate, permission, and risk policy.
- Venue selection within one governed account is allowed only where provider/account semantics support it and order meaning is equivalent.
- Sending an intent to several brokerage accounts is allocation, not transparent broker failover; it requires explicit sizing and per-account risk.
- After ambiguous submission, do not fail over to another account while the first outcome is unknown.
- Routing policy, destinations, and scoring inputs are versioned and explainable. No fallback based only on heartbeat failure.

### 12.4 Independent data and execution selection

COREI-OS-MINI separates two decisions that are often incorrectly joined:

1. **Data selection:** Which provider observations are permitted and appropriate inputs for this instrument and strategy at this time?
2. **Execution selection:** Which provider connection and account may receive the approved order for this instrument, strategy, portfolio, and operating mode?

The data source, strategy owner, and executing account may all be different. A chosen data source never grants execution permission, and an execution account does not automatically become the strategy’s sole data source.

The effective mapping must support all of these directions without changing the underlying rules:

| Operator’s starting point | Resolve next | Required check |
|---|---|---|
| Provider/exchange | Its connections and accounts; supported instruments; data-only/trading capability; assigned strategies | Account/provider identity, provider capability, permissions, instrument mapping |
| Account | Eligible instruments; assigned strategy versions; permitted data sources; route/mode | Account restrictions, allocation, strategy assignment, risk limits |
| Instrument | All provider/native-symbol mappings; eligible feeds; strategies; portfolios/accounts that may execute it | Contract equivalence, feed quality, market/session, per-account limits |
| Strategy | Eligible instrument universe; required data; portfolios/accounts/providers; allocation/routing rules | Strategy version approval, instrument eligibility, source policy, per-destination risk |

The engine resolves each path to the same effective assignment matrix. Search or selection from any starting point must not create an implicit assignment.

### 12.5 Operating examples

#### Example A — Instrument-first, XAUUSD

1. Select canonical XAUUSD and load all approved provider/native-symbol mappings.
2. Collect quotes/trades from eligible data connections across multiple brokers/exchanges, preserving each source observation and timestamp.
3. Evaluate source freshness, clock confidence, sequence gaps, spread/quality, and measured local latency. Select the feed set required by each strategy; retain competing observations for comparison.
4. Run every strategy version explicitly assigned to XAUUSD and enabled in the current mode. Strategies may consume different governed feed policies for the same instrument.
5. Each strategy emits its own traceable signal/intent or no-trade result. The decision layer applies configured conflict/aggregation rules; it does not combine incompatible strategies implicitly.
6. Resolve approved destination rows: selected portfolios, providers, connections, and individual accounts or account groups.
7. For group execution, use the configured select-one, allocate, or replicate semantics. Size and risk-check each account independently while also checking aggregate exposure across the linked portfolio/firm.
8. Submit only approved per-account orders through compatible adapters. Track each provider acknowledgement, fill, rejection, and reconciliation outcome separately.

This supports collecting XAUUSD data from multiple providers, assessing feed quality/latency, applying multiple assigned strategies, and executing through selected individual or grouped accounts. It does not guarantee that a faster-looking source is accurate or that every target account receives an identical fill.

#### Example B — Strategy-first, scan instruments and execute across accounts

1. Select an approved strategy version and its effective universe rule.
2. Enumerate instruments that satisfy the strategy’s asset class, provider mapping, trading session, data requirements, and account/portfolio mandate.
3. For each instrument, resolve eligible data sources and apply the strategy’s source/freshness policy.
4. Evaluate the strategy independently per instrument and produce a separate signal/intent with instrument-specific lineage.
5. Resolve each intent to the portfolio and destination accounts/providers assigned to that strategy/instrument pair.
6. Apply instrument, strategy, account, portfolio, and firm risk with reservations that prevent simultaneous instruments or strategies from over-consuming the same account capacity.
7. Execute approved child orders and reconcile them independently.

This supports one strategy across multiple instruments, brokers, and accounts, subject to explicit assignments and aggregate risk. The strategy cannot send to an account merely because that account is connected.

#### Example C — Account-first

Select an account, view its permitted provider capabilities and instrument universe, then enable one or more approved strategies. Data can still come from another authorized connection. All strategies and instruments share account-level exposure, open-order, rate, and kill controls; separate strategy views do not create isolated capital unless allocation rules explicitly establish it.

#### Example D — Provider-first

Select a broker/exchange, connect or inspect multiple authorized accounts, then view supported instruments, data subscriptions, assigned strategy versions, and execution permissions. The operator may collect data from the provider without enabling trading, or enable order execution without selecting that provider as a strategy’s primary data source, when provider capabilities permit.

### 12.6 Group and individual execution semantics

Every dispatch request declares one mode:

- **INDIVIDUAL:** target one explicitly selected account.
- **SELECT_ONE:** choose exactly one eligible destination using a versioned route policy.
- **ALLOCATE:** divide a strategy-level target across a set of accounts using approved weights/capacity; each child quantity and rounding remainder are explicit.
- **REPLICATE:** create independently sized child intents for each selected account. This is deliberate multi-account deployment, not a claim of equal fills.

In all modes, each account receives a separate identity, risk/policy decision, reservation, order lifecycle, provider identifiers, and reconciliation record. A parent intent can therefore have partial success across destinations without hiding which accounts did or did not execute.

---

## 13. Risk, Policy, and Emergency Controls

### 13.1 Risk hierarchy

**Firm/operating entity → portfolio → account → strategy → instrument → order**

Effective permission is the intersection of applicable limits and policies. Lower layers cannot relax higher-level hard limits. Each numeric limit has units, scope, effective time, owner, version, and breach behavior.

### 13.2 Minimum control domains

Model controls for maximum quantity/notional/order rate; per-trade, strategy, account, portfolio, and firm loss/exposure; open/pending order count and concentration; buying power/margin and provider restrictions; price collars, tick/lot validity, market session, and eligibility; stale data/account/risk state; duplicate intents/orders and account serialization; drawdown/daily loss; and external-intent capital/risk.

Risk aggregation spans all simultaneously active strategies and instruments on each account, then rolls up through portfolio and firm scopes across connected providers/accounts. Per-account checks must use reservations to prevent races; aggregate checks must not assume that different brokers, instruments, or strategies represent independent risk.

Exact thresholds remain unset in this blueprint. Phase documents derive them from trading model and risk mandate.

### 13.3 Kill controls

Support global, entity, portfolio, account, strategy, instrument, and external-input scopes where appropriate. Activation must be durable, visible, audited, and immediate under the defined authority model. Deactivation requires higher authority and recovery checks.

Each scope must define whether it blocks new orders, cancels open orders, attempts position reduction, leaves protective orders active, and affects linked accounts. No flatten-all behavior is implied by the term kill switch.

---

## 14. Failure, Degradation, and Recovery

| Failure | Required response | Trading effect | Recovery evidence |
|---|---|---|---|
| Provider disconnect before order | Mark degraded/disconnected; bounded reconnect; alert | Block new orders using affected connection/account | Authenticated session, fresh account state/data, order reconciliation |
| Disconnect after send/timeout | Mark outcome UNKNOWN; query provider orders/executions | Block duplicate submission; restrict new exposure as configured | Provider evidence resolves accepted/rejected/filled state |
| Stale/malformed/out-of-order data | Quarantine/mark quality; invalidate derived state | Block strategies whose prerequisites fail | Fresh valid data and recomputed state |
| Risk/config unavailable or stale | Identify last approved version; no silent fallback | Block if current policy cannot be proven | Approved version/scope validated and audit correlation restored |
| Duplicate intent/order/fill | Enforce identity and idempotency; record collision | Do not create duplicate exposure or position | Original and duplicate relation visible; projection reconciled |
| Provider rejection | Record native and normalized reason | Retry only if classified retryable and checks are repeated | Corrected/new intent and provider capability established |
| Partial fill/cancel race | Process provider events in declared order | Reserve only remaining quantity; no overfill assumptions | Provider cumulative execution and OMS quantity agree |
| Account state unavailable/stale | Mark account unknown and age | Block new exposure; protective behavior follows policy | Fresh balances, positions, orders, reconciliation |
| Internal restart/crash | Recover durable state and outstanding operations | Do not replay an order to provider just because process restarted | Ledger, outstanding orders, provider status, audit agree |
| Storage corruption/loss | Restore verified backup and rebuild derived views where supported | Keep trading disabled | Restore integrity, sequence/lineage, reconciliation, approval |
| Lineage/config mismatch | Isolate and alert | Halt affected scope | Reconstructed evidence and authorized resolution |
| Reconciliation break | Open case with severity and owner | Block material new exposure on affected account | Evidence-backed resolution and authorized closure |
| UI/API/auth outage | Preserve control/audit; alert operators | No ungoverned workaround | Auth restored, command/idempotency checked, audit continuity verified |

Recovery is controlled: restore visibility and state, verify provider reality, reconcile, prove controls, then re-enable the affected scope with authorized approval. A process restart is not a recovery certificate.

---

## 15. Roles and Operator Responsibilities

| Role | Primary authority | Must not do alone |
|---|---|---|
| Platform administrator | User/role and non-trading platform configuration | Approve own risk or live activation change where separation applies |
| Researcher/strategy owner | Create/analyze strategy versions and review performance | Enable provider execution or bypass risk |
| Risk officer | Define/review limits, approve risk-sensitive changes, deactivate kill controls after recovery | Alter provider evidence or conceal breaks |
| Trading operations | Manage connections, modes, exceptions, approved operational controls | Modify strategy logic or raise limits without authority |
| Execution operator | Review/approve/cancel/replace orders within scope | Resubmit uncertain order without resolving provider state |
| Reconciliation/finance operator | Investigate breaks, verify executions, fees, balances, P&L | Rewrite source records; corrections must be additive and audited |
| Auditor/read-only reviewer | Inspect lineage, versions, controls, reports | Mutate platform state |
| System service identity | Execute narrowly scoped machine functions | Hold human approval authority or unrestricted account access |

Exact permission combinations, maker-checker thresholds, and approval hierarchy are specified in phase documents. No implicit all-powerful trading administrator is assumed.

---

## 16. Trading Workstation and Navigation

Navigation follows operator tasks and state ownership, with shared context visible.

| Workspace | Primary questions | Key objects/actions |
|---|---|---|
| Command/Overview | What is active, degraded, blocked, or awaiting approval? | Environment/mode, provider health, data freshness, account/risk state, alerts |
| Providers & Connections | Which feeds/sessions are connected and permitted? | Capabilities, connection state, account mapping, permissions, freshness, reconnect/disable |
| Market & Instruments | What market state is available and how trustworthy is it? | Canonical instrument, mappings, quotes/bars, source timestamps, quality, session, history |
| Research & Strategies | Which strategy version is under study, paper, approved, paused, or retired? | Lifecycle, inputs, signals, performance, configuration, review/version controls |
| Portfolios & Accounts | How are capital, strategies, and accounts assigned? | Provider binding, allocation, exposure, mode, limits, positions, account health |
| Decisions & Risk | Why was an intent approved, reduced, or blocked? | Input snapshot, intent, routing targets, limits, policy, sizing, approval/override |
| Orders & Executions | What was submitted and what did provider report? | Order state, provider IDs, fills, cancels/replaces, uncertainty, lineage |
| Portfolio & Reconciliation | Does internal state agree with provider records? | Positions, cash, P&L basis, exposures, breaks, resolution |
| Operations & Audit | What changed, who acted, and how is recovery progressing? | Incidents, alerts, audit, backup/recovery evidence, access/config history |

Execution tables carry environment/mode and account context. Distinguish paper/live, provider-reported/derived, current/stale, confirmed/uncertain. A control is shown as operational only when its backend effect, permission, scope, and audit behavior are defined.

---

## 17. Observability and Audit

### 17.1 Required operational signals

- Provider state and last successful market/account/order update
- Data lag, source-time age, sequence gaps, rejects, and mapping failures
- Strategy evaluation, state, signal count/expiry, no-trade/block reasons
- Intent-to-risk and risk-to-submit latency distributions
- Accepted/rejected/unknown order counts by provider/account/instrument/reason
- Acknowledgement delay, partial fills, cancel/replace outcomes, slippage and fees
- Per-account position, exposure, reserved exposure, and risk headroom
- Reconciliation status, open-break severity/age, resolution time
- Kill/mode/config changes, privileged actions, failed authorization
- Application health, storage capacity, backup/restore status, recovery mode

### 17.2 Audit record minimum

Record actor/service identity, action, authority, target scope, reason, event/receipt/effective time, before/after version, correlation ID, result, source evidence, and linked lineage IDs. Do not log secrets or unredacted authentication payloads.

Audit is append-only in behavior. Retention, export, tamper evidence, and jurisdiction-specific obligations are detailed in phase documents.

---

## 18. Blueprint Acceptance Criteria

The blueprint is complete when each item is traceable to source, architecture decision, and eventual phase acceptance criteria:

1. Every internal/external input has an entry boundary and identity.
2. Data quality, timestamps, mappings, and stale-data behavior are defined.
3. Strategy/decision intent is separate from risk and execution.
4. Provider, account, instrument, and strategy cardinalities support provider-first, account-first, instrument-first, and strategy-first operation through explicit eligibility mappings.
5. Data-source selection is independent of execution-provider/account routing and preserves all relevant source observations.
6. Group/individual execution semantics are explicit; each child order has independent identity, risk, and outcome.
7. Multi-provider/account/portfolio/strategy relationships and isolation are explicit.
8. Exposure-increasing orders pass applicable risk, policy, and mode checks with concurrent and aggregate exposure accounted for.
9. Order states cover acknowledgement, partial fill, cancel race, rejection, and uncertainty.
10. Provider evidence and COREI ledger have explicit authority/conflict handling.
11. Fills, balances, positions, cash, fees, and P&L are attributable and reconcilable.
12. Failure/recovery exists for provider, data, state, UI/auth, and storage failures.
13. Roles, controls, approvals, and audit evidence are defined.
14. Infrastructure exclusions are respected without claiming distributed guarantees.
15. The phase flow is finalized; the companion technology shortlist is conditional on the provider and operating scope.

These are blueprint criteria, not a claim that software implements them or has passed production validation.

---

## 19. Gap and Resolution Register

| ID | Gap/tension | Risk | Blueprint resolution | Status |
|---|---|---|---|---|
| G-01 | COREI design makes Kafka the source of truth; mini excludes Kafka. | Split-brain state or false replay guarantees. | Define architecture-neutral durable ordered state-change contract, transactionally consistent with derived state; defer physical store and document actual guarantees. | Logical resolution; implementation design deferred |
| G-02 | COREI Phase C includes service mesh, containers, orchestration, distributed brokers. | Violates mini scope. | Retain domain ownership/reliability behavior; use logical modules and a later selected topology. No replacement orchestration stack. | Resolved by scope |
| G-03 | Existing blueprint suggests automatic broker fallback. | Duplicate exposure or changed ownership after ambiguous submission. | No implicit account/provider failover; reconcile provider state first. Cross-account routing is explicit allocation with independent risk decisions. | Resolved |
| G-04 | Connector abstraction assumes identical provider methods. | Provider behavior differs materially. | Canonical contracts with explicit adapter capabilities and unsupported states. | Resolved |
| G-05 | Cross-provider tick deduplication is suggested. | Could erase distinct quotes or venues. | Deduplicate source retransmissions only under provider-scoped identity. | Resolved |
| G-06 | Event-time fallback to ingestion time may mask missing provider time. | Incorrect trading-time semantics. | Preserve all time fields; label fallback; block when strategy timing contract cannot be met. | Resolved |
| G-07 | Partition key account + order may not serialize account-wide exposure. | Concurrent orders can pass using stale exposure. | Require account-scoped sequence/reservation independent of selected technology. | Logical resolution |
| G-08 | Multi-account blueprint conflicts with later single-account containment values. | Capability and activation guardrail conflated. | Model multi-account capability; permit conservative deployment caps as operating policy, not hard-coded architecture. | Reconciled; caps deferred |
| G-09 | Target blueprint has LIVE_AUTO while Stage-15 is paper-only. | Premature live activation. | Paper-first; live disabled until separately authorized and evidenced. | Resolved |
| G-10 | Copy-trade examples prescribe equity-ratio sizing and sub-50ms latency. | Unsuitable formula or unsupported target. | Treat fan-out as independent per-account intents; sizing and latency require strategy/provider evidence. | Resolved; values deferred |
| G-11 | P&L example may subtract slippage twice. | Incorrect P&L. | Specify accounting basis; report slippage separately unless separately booked as cash cost. | Principle resolved |
| G-12 | Retry after timeout may create duplicate order. | Duplicate live exposure. | UNKNOWN state blocks blind retry; query/reconcile first. | Resolved |
| G-13 | Source contains specific tools, numeric limits, latency, retention. | Arbitrary or stale constraints. | Set numerical requirements from strategy/provider/risk/capacity evidence before implementing the affected controls. | Deferred to phases |
| G-14 | First asset classes/providers not selected. | v1 implementation scope unbounded. | Provider-neutral architecture; confirm initial scope before its implementation slice. | Open, non-blocking for architecture |
| G-15 | “Boomerang” is ambiguous. | Wrong product may be researched. | Require exact URL/product before using as evidence. | Open, non-blocking |
| G-16 | Roles, maker-checker thresholds, limits, retention, SLOs, RTO/RPO are not quantified. | Cannot safely authorize live operation. | Specify owner, unit, scope, threshold, escalation, and evidence before implementing or activating the affected control. | Deferred; mandatory before affected implementation |
| G-17 | Source export/repository documents can vary in naming and maturity. | Conflicting text could be treated equally. | Supplied project index/order governs; re-verify relevant canonical repository sources during implementation planning. | Managed by source governance |
| G-18 | Prior blueprint described multi-entity support but did not fully specify all provider/account/instrument/strategy relationship cardinalities or operator starting perspectives. | One dimension could become a fixed parent of another, blocking the user’s instrument-first, strategy-first, account-first, and provider-first workflows. | Add explicit many-to-many assignments and an approved deployment matrix across strategy version, instrument, portfolio, provider, connection, account, and mode. | Resolved in this revision |
| G-19 | Data-source choice and execution destination were not sufficiently separated. | A market-data provider could be mistaken for the order destination, or a connected trading account could be treated as the only valid feed. | Define separate DataSourcePolicy and execution route/assignment; allow data-only, trading-only, or combined connections where provider supports them. | Resolved in this revision |
| G-20 | “Group execution” could mean allocate, select one, or replicate, with different exposure outcomes. | Accidental quantity multiplication or hidden partial execution across accounts. | Require explicit INDIVIDUAL, SELECT_ONE, ALLOCATE, or REPLICATE semantics and independent child risk/order tracking. | Resolved in this revision |
| G-21 | HFT feed comparison can confuse observed local feed latency with provider/exchange one-way latency. | Incorrect source ranking or unsupported HFT claims. | Measure each observable latency segment with clock-confidence metadata; defer numerical targets and hardware/tool choices to later phases. | Measurement contract defined; targets deferred |
| G-22 | Concurrent strategies/instruments can consume the same account/portfolio capacity. | Risk checks may each pass against stale state and jointly breach limits. | Require account reservations/serialization and aggregate risk across strategy, instrument, account, portfolio, and firm scopes. | Resolved logically |
| G-23 | Candidate allocation and final child-level risk were described in different orders across the architecture and phase flow. | A proposed route could be mistaken for a risk-approved order, or a child could be sized without its final account exposure. | Resolve candidate destinations and proposed children first; run aggregate and per-child risk, sizing, and atomic reservation before provider submission. | Resolved in v1.2 |

### Inputs to confirm before implementation

- Initial asset classes and instrument universe
- First providers and data-only vs trading scope per provider
- First strategy types and external signal sources
- Paper simulator behavior and sandbox comparison
- Whether v1 includes smart routing or explicit account allocation only
- Role separation, approval thresholds, risk limits, operating envelope
- Data retention, backup objectives, and measurable service targets

These are product and operational inputs to confirm before the affected implementation. The companion technology inventory is a shortlist, not a substitute for provider compatibility checks.

---

## 20. Architecture Decisions

| ID | Decision |
|---|---|
| DEC-01 | No Kubernetes, Docker/Compose, Helm, Kafka, Redis, or equivalent distributed replacement stack in COREI-OS-MINI. |
| DEC-02 | Use logical modular ownership; physical process/deployment structure is deferred. |
| DEC-03 | Risk and policy are mandatory gates before provider orders. |
| DEC-04 | Multiple providers and multiple accounts per provider are first-class; provider/account binding and capability differences are explicit. |
| DEC-05 | Unknown provider outcome blocks blind retry and alternate-account failover until reconciled. |
| DEC-06 | Provider owns provider-side facts; COREI owns governed decisions and internal ledger/audit; views cannot overwrite source evidence. |
| DEC-07 | Preserve source-level market observations and all relevant times; do not collapse feeds by symbol alone. |
| DEC-08 | Use fixed-point financial values with defined scales, rounding, and overflow behavior. |
| DEC-09 | Paper-first; live disabled until separately authorized and evidenced. |
| DEC-10 | Finalize the process phases before choosing implementation tools; treat the companion technology inventory as a shortlist, then confirm exact choices against provider and operating requirements before coding. |
| DEC-11 | Public benchmarks inform concepts only; no vendor internals or universal capabilities are assumed. |
| DEC-12 | Provider, account, instrument, and strategy are independently addressable dimensions joined through explicit many-to-many eligibility and allocation mappings. |
| DEC-13 | Market-data feed selection is independent from execution provider/account selection; preserve source observations and record source selection per strategy/instrument decision. |
| DEC-14 | Every execution fan-out declares individual, select-one, allocate, or replicate semantics and creates independently governed child intents/orders. |
| DEC-15 | Risk aggregates concurrent exposure across strategies and instruments, with serialized account reservations and higher-level portfolio/firm aggregation. |
| DEC-16 | HFT latency is segmented by observable boundaries and clock confidence; no numeric target or provider/exchange latency claim is assumed in the blueprint. |
| DEC-17 | Candidate account children are formed before final child risk; each child passes aggregate and account-level checks and reserves capacity before submission. |

---

## 21. Source Traceability

| Blueprint domain | Primary source(s) | Applied requirement |
|---|---|---|
| Mission, simplicity, operator understanding | COREI ANCHOR LAW; PROJECT INDEX & CONTEXT GUIDE | COREI operating chain, purposeful workspaces, preserve understanding/control/continuity |
| Governance and phase gates | Master Phases Blueprint; Universal Phase Structure; authoritative platform execution model; stage index; lifecycle | Versioned decisions, explicit scope, assumptions, validation, ownership, traceability, lock criteria |
| Strategy/signal/decision | Phase A; Trading Platform Blueprint 3.1 | Strategy lifecycle, deterministic signals, decision/TradeIntent, separation from execution |
| Constraints and risk | Phase B; Master Phases Blueprint; Phase D | Quantified hierarchical controls, snapshots, rejection/reduction policy, firm-to-order precedence |
| Runtime/state/replay | Phase C; Infrastructure Blueprint 3.1; continuity sources | State ownership, ordering, replay intent, recovery, observability; products adapted to mini limits |
| Execution/provider lifecycle | Phase D; Trading Platform Blueprint; Stage-15 repository blueprint | Per-account execution, risk/policy/size/order flow, provider evidence, paper-only boundary and lineage |
| Portfolio/reconciliation/activation | Stages 12–21 supplied; repository Stage-13/15 blueprints | Account/portfolio state, paper validation, operations, recovery; activation conditions retained as gates |
| Validation/adversarial review | Team Blue; Team Red | Consistency, traceability, unsafe assumptions, failure and edge-case challenge |
| Operations documentation patterns | Core Neha README, operations guide, ADR index | Modular ownership, operator instructions, backup/restore, explicit decisions; no HMS domain/container stack |

The repository’s Stage-13 source identifies portfolio state and fixed-point/replay rules; Stage-15 defines the execution sequence and explicitly remains paper-only before broker connectivity. Broader COREI infrastructure sources assume products excluded from this mini project, so this blueprint retains behavioral contracts and defers physical guarantees.

---

## 22. Final baseline and remaining inputs

This blueprint is finalized as the architecture baseline for phase planning and implementation design. The first implementation scope still requires concrete values for the selected provider(s), account(s), instrument(s), strategy, paper environment, and applicable risk limits. Confirm those values in the implementation work order before building the affected capability.

The blueprint defines required behavior; it does not claim that software has been implemented or production-validated. Paper-only operation remains the initial execution boundary. Live trading requires a separate future decision and validation.

---

## 23. References

### Supplied COREI project sources

- COREI ANCHOR LAW.md
- PROJECT INDEX & CONTEXT GUIDE.md
- master-phases-blueprint.txt
- BLUEPRINT_TRADING_PLATFORM_3.1.md
- BLUEPRINT_INFRASTRUCTURE_3.1.md (scope reconciliation only)
- universal-phase-structure-V1.txt
- phase-a-step-1-to-8.txt
- phase-b-step-1-to-8.txt
- phase-c-step-1-to-8.txt
- phase-d-step-1-to-8.txt
- team-blue.txt
- team-red.txt
- MASTER BUILD FLOW_v2.md
- authoritative-platform-execution-model.md.md
- authoritative-stage-index.md.md
- institutional-stage-execution-lifecycle.md.md
- platform-continuity-restoration-block.md.md
- stage-12-12a-13-14-14a-15-16-17-17a.md
- stage-18-18a-18b-18c-18d-18e-18f-18r.md
- stage-19-19pending-20-21-1.md

### Public references

- COREI: https://github.com/coreeyegroup/corei
- Core Neha: https://github.com/coreeyegroup/core-neha
- Westernpips docs: https://docs.westernpips.com/doc/public/
- Westernpips Connections: https://docs.westernpips.com/documentation/westernpips-webcliker/wc-connections/
- BlackRock Aladdin: https://www.blackrock.com/aladdin
- BlackRock Aladdin FAQs: https://www.blackrock.com/aladdin/about-us/about/faqs
- Binance Institutional: https://www.binance.com/en/vip-institutional-services
- Binance Portfolio Accounts: https://www.binance.com/en/support/announcement/detail/192a7ab5b39b42d2a24330f7d15ef3e8

---

**End of blueprint.** Companion documents: Trading Process Flow and Build Phases v1.4; Simple Technology and Tool Options v1.2; COREI-OS-MINI Final Document Index v1.0.
