# COREI-OS-MINI

COREI-OS-MINI is a paper-only prototype for the trading lifecycle defined in the final project documents. The initial code slice implements a synthetic market-data input, deterministic sample strategy, explicit account allocation, fail-closed child risk checks, simulated paper fills, and durable run records in PostgreSQL.

## Safety and scope

- This slice accepts synthetic prices only. It does not connect to a broker or exchange.
- It has no live-order path. The API refuses startup if a non-paper execution mode is configured.
- Demo endpoints are disabled unless explicitly enabled with `COREI_ENABLE_DEMO=true`.
- The API is unauthenticated and intended for local development only. Bind it to `127.0.0.1`; do not expose it to a network.
- A risk profile is required for every candidate account. Missing or stale risk/account state blocks the affected child.
- Simulated fills are deterministic demo outcomes, not evidence of executable market prices or broker behavior.

## Project layout

```text
backend/
  pyproject.toml
  sql/001_create_paper_runs.sql
  src/corei_os_mini/
    api.py
    allocation.py
    config.py
    contracts.py
    persistence.py
    risk.py
    strategy.py
    workflow.py
```

## Start locally

Use a supported Python release and PostgreSQL 18. Configure a local database, apply the SQL file in `backend/sql`, then run the FastAPI application from `backend`:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
export COREI_DATABASE_URL='postgresql://corei:change-me@127.0.0.1:5432/corei_os_mini'
export COREI_ENABLE_DEMO=true
export COREI_EXECUTION_MODE=paper
psql "$COREI_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/001_create_paper_runs.sql
python -m uvicorn corei_os_mini.api:app --host 127.0.0.1 --port 8781
```

The demo route is `POST /api/demo/paper-runs`. The API schema is available at `/docs` while the local server is running. Do not place credentials in source control or command history in a shared environment.

## Initial API behavior

The request supplies a synthetic price series, strategy window lengths, a target quantity, an explicit group-dispatch mode, and the risk state for each eligible account. The workflow:

1. Validates the synthetic market input and strategy parameters.
2. Produces a deterministic BUY, SELL, or HOLD decision using integer price units.
3. Creates proposed account children according to INDIVIDUAL, SELECT_ONE, ALLOCATE, or REPLICATE semantics.
4. Applies per-account quantity, position, notional, and short-position checks using the supplied account state.
5. Simulates a paper fill only for approved children.
6. Persists the input and complete result in an append-only API-level paper-run record.

Prices and quantities are integer fixed-point units interpreted using the supplied scales. `max_order_notional_units` uses the exact product of price units and quantity units. Set account risk limits and short-selling permission explicitly for every candidate account; the demo does not load real provider or portfolio state.

The sample moving-average strategy and immediate-fill model are demonstration components. They are not approved trading strategies or realistic execution models.

## Current implementation boundary

This is the first code slice, not a complete platform or production deployment. Real provider adapters, credential management, authenticated operator UI, configurable strategy registry, position/portfolio ledger, provider reconciliation, migrations, backup/restore procedures, and operational alerting remain future work. The current database table is an append-only run record for this slice; it is not the final trading ledger.

## Final design references

Use the final COREI-OS-MINI document index, blueprint v1.2, process flow v1.4, technology inventory v1.3, and master work prompt v1.1 as the design baseline. Older versions are historical.
