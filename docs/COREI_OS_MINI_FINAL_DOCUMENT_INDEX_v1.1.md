# COREI-OS-MINI — Final Document Index

**Document ID:** COM-INDEX-TRADING-001  
**Version:** 1.1  
**Date:** 4 November 2026  
**Status:** Current project baseline for planning and initial paper implementation; not live-trading authorization

## Authoritative documents

| Order | Document | Version | Purpose |
|---:|---|---:|---|
| 1 | `COREI_OS_MINI_END_TO_END_TRADING_PLATFORM_BLUEPRINT_v1.2.md` | 1.2 | Architecture, relationships, state/data flow, controls, and failure behavior. |
| 2 | `COREI_OS_MINI_BUILDING_PHASE_DOCUMENTS_v1.4.md` | 1.4 | End-to-end trading process and build sequence. |
| 3 | `COREI_OS_MINI_TECHNOLOGY_TOOL_INVENTORY_v1.3.md` | 1.3 | Technology shortlist and initial code selections. |
| 4 | `COREI_OS_MINI_MASTER_WORK_PROMPT_v1.1.md` | 1.1 | Operating instructions for future project work. |

## Current code

The initial `corei-os-mini` backend is a local, paper-only code slice. It accepts synthetic price data, evaluates a deterministic sample moving-average rule, resolves explicit account allocation, applies per-account fail-closed risk checks, produces simulated paper fills, and records each run in PostgreSQL.

It does not connect to a real broker or market-data provider. It does not include the operator frontend, real account/portfolio state, shared concurrent reservations, provider reconciliation, production authentication, deployment automation, or live execution. The demo route is disabled by default. Keep the API bound to loopback and use it only for local development.

## Final order and risk boundary

**Connect → collect → validate/select data → analyze → evaluate strategy → signal/intent → resolve candidate destinations and proposed children → run aggregate and per-account final risk/sizing/reservation → submit approved paper orders → reconcile → monitor and improve.**

Candidate allocation does not authorize execution. Each child must pass final risk and permission checks before paper submission. Real provider/account state must replace the current synthetic/demo inputs before an adapter is integrated.

## Multi-dimensional model

Providers/connections, accounts, instruments, and strategies remain independent dimensions joined by explicit eligibility and allocation mappings. Operators may begin from any dimension. Data-source selection remains independent from order routing. The platform does not automatically activate every possible combination.

## Superseded documents

- Building Phase Documents v1.1 and v1.3 are superseded by v1.4.
- Trading Blueprint v1.1 is superseded by v1.2.
- Technology Inventory v1.1 and v1.2 are superseded by v1.3.
- Master Work Prompt v1.0 and the earlier long research prompt are superseded by v1.1.
- Final Document Index v1.0 is superseded by v1.1.

Use older versions only as history.

## Inputs needed for the next implementation step

Confirm the first real provider and connection method, initial instrument and strategy, paper environment, host suitability, and numeric risk limits. The current sample strategy and simulated fill model are demonstration-only. Exact dependency patches should be locked after provider SDK compatibility is known.

## Safety boundary

These documents define the production-quality design target and initial paper prototype. The code has not been deployed or validated as a production trading system. Live trading requires a separate future decision and operational evidence.

## Owner decisions after v1.1 (10 November 2026)

`DECISIONS.md` records DEC-18 … DEC-22, which govern where they conflict with the documents above. In short: COREI-OS-MINI is a production platform (not paper-only); live order sending is controlled by an audited, firm-wide switch per environment that is off by default; the configuration domain was rebuilt (migration 009); launch platforms are MT5 and broker REST/WebSocket APIs. Current implementation status is in `GAP_REGISTER.md`.
