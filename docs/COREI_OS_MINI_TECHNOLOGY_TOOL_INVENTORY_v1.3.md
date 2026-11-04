# COREI-OS-MINI — Simple Technology and Tool Options

**Document ID:** COM-TTI-TRADING-001  
**Version:** 1.3  
**Date:** 4 November 2026  
**Status:** Final lightweight technology baseline with first-slice backend choices recorded  
**Process baseline:** COREI-OS-MINI Trading Process Flow and Build Phases v1.4  
**Implementation:** Initial local paper-workflow backend is present; no broker integration or deployment is claimed

---

## 1. Recommendation

Use one modular application, one PostgreSQL database, and a small React operator interface. Keep provider-specific code behind adapters. Reuse the existing development host only for local development and paper work after checking capacity, access, and patch state. Add a tool only when a confirmed provider or operating need requires it.

This keeps the prototype inexpensive and understandable while preserving the contracts needed to expand providers, accounts, instruments, and strategies. Do not begin with microservices, Kubernetes, Docker, Kafka, Redis, or monitoring platforms.

## 2. Recommended starting options

| Area | Recommended starting choice | Alternative | Choose the alternative when |
|---|---|---|---|
| Host | Existing core-node for local development and paper work only after capacity, access, and patch-state review | Dedicated Ubuntu 24.04 LTS host | Choose a dedicated host if isolation or capacity requires it; Ubuntu 24.04 standard security maintenance runs through May 2029 |
| Backend | Python 3.14 (project supports 3.13–3.14), FastAPI, and Uvicorn | Python 3.13 where a required provider dependency needs it | First code slice uses FastAPI, Uvicorn, and Psycopg 3 with bounded version ranges; exact lockfile waits for provider selection |
| Database | PostgreSQL 18, latest stable minor | SQLite for offline demos only | SQLite is suitable for isolated prototypes with no concurrent account/order workflow; use PostgreSQL for the trading process |
| Frontend | React + TypeScript + Vite; use Node 26 LTS | Existing COREI UI components where they fit | Reuse a component only if it does not pull the mini project into the larger COREI infrastructure and is compatible with the selected Node LTS |
| Provider connection | The selected provider's official API/SDK behind one adapter | MT5 terminal integration if the chosen broker requires MT5 | Decide only after broker, account, data, and operating-system requirements are known |
| Process/service | Native Linux process managed by systemd | Separate worker service on the same host | Split the worker when operational isolation is useful; no separate deployment platform is required |
| HTTPS | Reuse Caddy on core-node if that host is selected | No public exposure during local-only development | Expose remotely only after TLS, authentication, and access rules are configured |
| Validation | pytest for the core workflow | Hypothesis for key numeric/state edge cases; Playwright for UI workflows | Add these when the corresponding risk or interface exists |
| Operations | Structured logs, health/status view, systemd/journalctl, PostgreSQL backup and restore | Prometheus/Grafana later | Add only when measured alerting/history needs justify them |
| Automation | n8n for notifications or reports only | None | Never use it to authorize, size, or submit orders |

**Support notes checked 4 November 2026:** Ubuntu 22.04 standard security maintenance runs through May 2027; Ubuntu 24.04 through May 2029. Python 3.14 is in bug-fix support through October 2030; Python 3.13 receives security fixes through October 2029. PostgreSQL 18 is supported through November 2030. Node.js 26 is the current LTS line. Confirm provider SDK compatibility and current patch versions before implementation. ([Ubuntu lifecycle](https://ubuntu.com/about/release-cycle); [Python version status](https://devguide.python.org/versions/); [PostgreSQL version policy](https://www.postgresql.org/support/versioning/); [Node.js releases](https://nodejs.org/en/about/previous-releases))

## 3. First code slice and current boundary

The initial local backend uses Python 3.13–3.14, FastAPI, Uvicorn, Psycopg 3, and PostgreSQL. Dependency ranges are recorded in the project `pyproject.toml`; exact patch versions are not locked until the first provider SDK is selected.

The code currently provides synthetic market input, a deterministic sample moving-average decision, explicit account allocation modes, per-account fail-closed risk checks, simulated paper fills, and an append-only PostgreSQL paper-run record. It has no broker adapter, real market-data connection, operator frontend, authenticated access, portfolio ledger, or live-order path. Its API is local-development-only and the demo route is disabled by default.

## 4. Simple application shape

Keep these responsibilities inside one application with clear boundaries:

- Provider adapters collect data and perform approved account operations.
- Market-data processing validates, maps, compares, and selects feeds.
- Strategy processing analyzes market state and emits signals/intents.
- Risk processing checks shared exposure and account capacity.
- Execution processing routes and tracks each account's orders.
- Portfolio processing records fills, fees, balances, positions, and reconciliation.
- The operator interface displays status and sends authenticated commands.

Store configuration, assignments, orders, fills, portfolio state, and audit history in PostgreSQL. Start with normal relational tables. Add partitioning or a time-series extension only after data volume demonstrates the need.

For order dispatch, persist the approved order intent and status before calling a provider. A small worker can process pending dispatches from the database. If the provider response is unclear, mark the order unknown and reconcile before retrying. No distributed message broker is needed for the first deployment.

Use fixed-point integer values with explicit instrument scales for financial, quantity, and risk calculations. Record provider observations and timestamps separately from normalized data.

## 5. Keep these safety requirements

These are part of the trading process:

- Provider, account, instrument, strategy, data source, route, and mode must be explicitly assigned.
- Data selection stays separate from execution routing.
- Risk checks include existing and pending exposure shared across strategies and instruments.
- Group dispatch creates separate account orders and outcomes; no silent size multiplication or residual redistribution.
- Unknown provider submission outcomes are reconciled before retry or alternate routing.
- Paper mode cannot reach live endpoints.
- Store credentials outside Git and never put them in logs.
- Require login and basic roles before remote operation; keep the first role set small.

## 6. Defer

Do not add these to the initial prototype:

- Kubernetes, Docker/Compose, Helm, Kafka, Redis, or a replacement distributed stack
- Microservices or service mesh
- Multi-node high availability
- TimescaleDB, Prometheus, Grafana, or centralized tracing
- Full identity-management infrastructure before the deployment audience is known
- Automatic broker failover, live trading, HFT targets, and autonomous strategy changes

Add them only when measured load, reliability needs, or a confirmed provider requirement makes the benefit clear.

## 7. Choices to confirm before coding

| Choice | Current position |
|---|---|
| First broker/exchange and connection method | Open; determines adapter and SDK compatibility |
| First instrument and strategy | Open; determines data mapping and test scenarios |
| Paper environment | Choose provider sandbox, simulator, or both |
| Host | Prefer existing core-node for early work; confirm its resources and access boundary |
| User access | Start with a small authenticated role set; choose identity integration if remote multi-user use is required |
| Data volume and retention | Measure with the first feed before adding storage extensions |
| Exact dependency versions | Pin after provider compatibility is checked |

This inventory is the final lightweight recommendation set and records the first backend selections; it does not claim deployment or provider connectivity. Confirm the first provider, instrument, strategy, host, and paper environment before pinning exact dependency versions. Then build the first vertical slice: **collect one selected market feed, validate it, evaluate one strategy, create a signal/intent, resolve a candidate account child, run final child-level risk and reserve capacity, then complete a paper order through reconciliation.**

No live trading is authorized by this inventory.
