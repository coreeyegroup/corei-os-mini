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
psql "$COREI_DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/002_create_market_data_assessments.sql
python -m uvicorn corei_os_mini.api:app --host 127.0.0.1 --port 8781
```

The demo route is `POST /api/demo/paper-runs`. The API schema is available at `/docs` while the local server is running. Do not place credentials in source control or command history in a shared environment.

`POST /api/demo/market-data/assess` accepts explicitly mapped demo observations from multiple provider connections. It reports mapping, freshness, clock-confidence, timestamp-order, and sequence checks, summarizes each feed as `READY`, `DEGRADED`, `BLOCKED`, `NO_OBSERVATIONS`, or `DISABLED`, then selects deterministically by configured feed priority and source time. Each submitted batch and result is saved together in PostgreSQL and can be retrieved at `GET /api/market-data/assessments/{assessment_id}`. This demo assessment does not open provider connections or store a live tick stream. A real adapter and feed store require selection of the first provider and its supported market-data contract.

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

## New workstation shell — 0.2.0

This UI is built from `ui/` in this repository. It does not use the old `/var/www/corei-os-mini` deployment or its port 8791 service. The public hostname is reserved for a later authenticated deployment; these commands do not edit Caddy, Cloudflare, databases, or system services.

The shell provides activity navigation, explorer filtering, tab groups, horizontal/vertical splits, drag-to-move tabs between groups (drop on a group's tab strip), close/reopen tabs, a focus-group mode, collapsible panels, adjustable panel sizes in Settings, a command palette (Ctrl/Cmd+Shift+P), and separate saved browser layouts for Research, Execution, and Operations. Small screens hide the sidebars to prioritise the central view. Layout preferences contain no credentials or trading state.

`GET /api/workstation` identifies this backend and reports actual capabilities, paper mode, demo availability, and database connectivity. The UI rejects mismatching identities/contracts. Database connectivity is not a migration/schema-readiness check. Saved market-data assessments can be retrieved by UUID; no simulated quotes or fabricated account data are displayed. Missing features remain explicitly unavailable. The shell does not submit orders or change trading configuration.

### Ubuntu install and start

With the previously installed project Node runtime and Python 3.14:

```bash
cd /home/coreeye/corei-os-mini
bash scripts/install-workstation.sh
.venv-workstation/bin/python scripts/run-workstation.py
```

The installer creates `.venv-workstation`, installs the existing backend dependencies, installs frontend dependencies from `ui/package-lock.json`, and builds the frontend. The launcher starts this backend on **127.0.0.1:8786** and the Vite development UI on **127.0.0.1:5186**, refusing occupied ports. Both processes stop together. No existing `.env` file is automatically loaded. `COREI_DATABASE_URL`, if supplied in the launch environment, must explicitly identify the intended PostgreSQL 18 project database on port 5433. The database and schema still require separate setup. Without database configuration, the shell opens and accurately reports database unavailability.

Open http://127.0.0.1:5186 on core-node. For a remote browser, forward port 5186 using your existing VS Code SSH connection, then open the forwarded localhost URL. Never publish the Vite development server or the unauthenticated API through Caddy. Public deployment requires implemented access controls and a production frontend serving configuration.

### Current boundaries

This is a workstation foundation, not a completed trading platform. Provider/connection/account/instrument/strategy registries, live feeds, operational risk and execution, authentication, and portfolio reconciliation remain unimplemented. Browser-window detachment, arbitrary panel docking, pinned tabs, linked trading contexts, and cross-device preferences are not implemented in this shell. These remain part of the approved workstation direction. Existing project backend modules remain the starting point for subsequent capabilities; this shell does not replace them.

### Repair an incomplete Python installation

A version check alone does not establish runtime readiness. If Python reports that its `ssl` module is unavailable, run `bash scripts/repair-python.sh` before the installer. This script installs Ubuntu build prerequisites, downloads the official Python 3.14.10 source archive, verifies its published SHA-256, builds with `/usr` OpenSSL, and installs via `altinstall` under `/opt/corei-os-mini/toolchain/python-3.14.10`. It checks required modules before installation and verifies HTTPS access to PyPI afterward. It does not replace Ubuntu's system Python or the previous `/usr/local` interpreter. Compilation uses two jobs; build files are retained under the printed temporary directory for diagnosis.

The workstation installer prefers this project interpreter, checks SSL and required standard-library modules before installing dependencies, and preserves a broken or different-base virtual environment as `.venv-workstation.backup.<timestamp>.<pid>` before recreating it. Then rerun `bash scripts/install-workstation.sh`. Do not disable certificate verification or use insecure package-index URLs to bypass missing SSL support.

Source checksum: https://www.python.org/downloads/release/python-31410/
# Ubuntu domain deployment

The new workstation is served at `https://corei-os-mini.coreeye.in/`.
After pulling this repository, run `bash scripts/deploy-workstation.sh` as the
normal repository user. It builds the frontend and backend dependencies, then
requests sudo to deploy. No development server or SSH tunnel is required.

The installer creates `corei-os-mini-workstation.service`, running the repository
backend as that user on loopback port 18786. Caddy serves the production frontend
from `/var/www/corei-os-mini-workstation/current`, an atomic symlink to a retained
build directory. It replaces only the standalone subdomain block, preserves other
sites, backs up Caddy and any existing service unit, checks backend identity,
validates Caddy before reload, and attempts to restore prior configuration if
deployment fails. Unrecognized or ambiguous host configurations fail closed.
The old `/var/www/corei-os-mini` files and port 8791 are not used by this site.

This is a **public, read-only workstation shell**, not a completed trading release.
Only GET `/api/workstation` is proxied publicly; other backend routes return 403.
The service loads an optional root-owned database environment file and disables
demo mutations. Database connectivity appears unavailable until setup succeeds. Registry access uses operator authentication as described below. Other record and
trading routes remain blocked. This deployment
does not alter the underlying multi-provider/account/instrument/strategy design.

Verify after deployment:

```bash
systemctl is-active corei-os-mini-workstation
curl -fsS https://corei-os-mini.coreeye.in/api/workstation
curl -sSI https://corei-os-mini.coreeye.in/
```

The root URL should return 200, not redirect to the old path. If a browser retains
the earlier permanent redirect, open the root in a private window. For startup
errors use `sudo journalctl -u corei-os-mini-workstation -n 60 --no-pager`.
Updates use the same pull and deployment command. Dependency installation uses
the existing project virtual environment, so this is not an atomic backend release
rollback; only service configuration, Caddy configuration, and the frontend link
are restored on deployment failure. Other projects and database clusters are not
migrated or stopped.

## Workbench controls (0.3)

Workspace, View, Layout, Navigate and Help menus now share a searchable command
palette. Ctrl+Shift+P (or Ctrl+P) opens it; arrow keys select a result and Enter
runs it. Ctrl+B toggles the explorer, Ctrl+Alt+B the inspector, Ctrl+J the bottom
panel, Ctrl+1–4 selects a group, and Alt+Left/Right switches tabs. Browser or OS
shortcuts may take priority. Help lists controls in the app.

Drag the panel separators or focus them and use arrow keys to resize. Open Views
lists tabs across groups. Tabs can move between groups by dragging or using Move.
Layout presets provide one, two, three or four groups (four uses a 2×2 grid);
presets replace open groups. Close-other-tabs and focus mode are available.
Layouts continue to be saved per workspace in this browser. Narrow displays can
scroll horizontally; enabled panels are no longer silently hidden.

This release improves workstation navigation and layout controls. It does not
implement a terminal, extension host, arbitrary panel docking, external windows,
or new trading backend capabilities.

## Appearance and tab controls (0.4)

Choose Standard (neutral dark), Black, Dark Blue or White from the Appearance
menu, command palette or Settings. Settings also provides interface text size
(11–16px), Compact/Comfortable density, activity/status bar visibility and tab
wrapping. Preferences are saved independently of workspace layouts in browser
storage. Reset appearance restores defaults without changing the trading state.
Malformed saved preferences fall back to defaults; storage failures are reported.

Right-click a workspace tab to pin/unpin, close, close other unpinned tabs or move
to the next group. Pinned views sort first and cannot be closed through tab-close
controls until unpinned. Pinning applies to that view across groups and persists in
this browser. Layout presets still replace groups. Workspace > Reopen last closed
tab restores a recently closed view in the active group (session history only).

Run `npm test --prefix ui` to build and run the DOM interaction regression checks.
These checks cover menus, layout groups, theme switching/persistence, font size,
visibility, wrapping, context menus, pin protection and reopening. They do not
replace visual browser review or add backend capabilities.

## COREI Orbit theme

Appearance > Color theme: COREI Orbit adds the source Orbit palette while keeping
all four previous themes and the existing default. It is also available in Settings
and the command palette, and persists through the existing appearance preference.

Source reviewed: [Orbit variables.css](https://github.com/coreeyegroup/corei/blob/main/ui/corei-orbit/src/styles/variables.css),
blob `c69bef9c95148349d6b5d226613fab0c70b460bf`, plus theme-dark.css, layout.css,
TopRibbon.css and StatusBar.css. Primary background `#0B0E14`, panels `#0F151E`,
hover/input `#1A2330`, borders `#1F2937`, primary text `#E2E8F0`, muted text
`#94A3B8`, teal `#00E5A0`, danger `#FF4D4D` and amber `#F59E0B` come directly
from Orbit's variables. Mini's selection surface maps to Orbit's hover color.
The dark status bar, translucent header and thin scrollbars follow Orbit's source
styles. This is a color-theme adaptation to mini's existing components; Orbit's
layout, services and application state are not imported.

## Dedicated PostgreSQL setup

Continue the provider/market-data phase by configuring persistence first:

```bash
cd /home/coreeye/corei-os-mini
git pull --ff-only origin main
bash scripts/configure-workstation-database.sh
bash scripts/deploy-workstation.sh
```

The database installer verifies PostgreSQL major version 18 and data directory
`/var/lib/postgresql/18/corei_os_mini` on port 5433. It creates database
`corei_os_mini` and login `corei_os_mini_app` only as needed. PostgreSQL 14 on port
5432 is not accessed. A generated password stays in the root-owned mode-600
`/etc/corei-os-mini/workstation.env`; it is not printed or committed. Preserve this
file in your server backup. Existing credentials are reused, not reset; an existing
role without the file or a database owned by another role stops setup.

Numbered SQL migrations run under a transaction and advisory lock with a checksum
ledger. Changed applied migrations stop the run; add a new migration instead.
The installer checks application read/write access using a rolled-back probe.
The deployment service reads the environment file through systemd; later deploys
preserve it. The public domain still exposes only workstation status, not stored
records or order submission. No provider credentials or live execution are added.

Verify:

```bash
systemctl is-active corei-os-mini-workstation
curl -fsS https://corei-os-mini.coreeye.in/api/workstation
```

Expect `database_connected: true`, `schema_ready: true`, `execution_mode: PAPER`
and `demo_enabled: false`. Schema readiness checks required readable columns; it
is not a claim that provider connectivity or the full trading lifecycle is ready.

Validation: `python3 -m unittest discover -s scripts/tests -v` covers cluster and
ownership refusal, explicit connection targeting, secret-safe errors and migration
wrapping. `npm test --prefix ui` covers the production UI bundle. Actual PostgreSQL
migration and read/write verification runs on Ubuntu during setup.

## Registration slice (0.5)

The Providers, Accounts and Instruments workspaces now contain authenticated
registry forms and paginated tables. The tabs cover Providers, Connections,
Accounts, Instruments and Symbol mappings. Register provider → connection →
account, then instruments and mappings. Select/copy UUIDs to establish relationships.
Multiple connections per provider, multiple accounts per connection and mappings
from many connections to a canonical instrument are supported. Account/strategy
eligibility, account groups, live adapters and feed subscriptions remain unfinished.

New rows default to disabled. Enable/disable controls update configuration only;
no record creates a session, reserves risk, or submits an order. LIVE is an account
classification, not an execution switch. Existing names/references are protected by
scope-specific uniqueness; missing parents are rejected by foreign keys. There is
no deletion or general record editing in this slice. Do not enter API keys or
passwords into registry text fields. The first 200 parent records appear as input
suggestions; UUID input supports any registered parent and lists paginate by 50.

Install/update on Ubuntu:

```bash
cd /home/coreeye/corei-os-mini
git pull --ff-only origin main
bash scripts/configure-workstation-database.sh
sudo python3 scripts/configure-workstation-login.py
bash scripts/deploy-workstation.sh
```

The login setup prompts locally for a username and password (minimum 6 characters),
stores a salted PBKDF2-SHA256 hash in the existing root-only environment file, and
preserves configured credentials on reruns. Do not send the password in chat.
Open Providers and sign in once. The browser receives an eight-hour Secure,
HttpOnly, SameSite=Strict cookie; passwords are not stored in localStorage. Sign out
clears the cookie and registry views in this page. Copied session cookies remain
valid until expiry or credential rotation; this is a single-operator baseline,
not multi-user RBAC or a central session-revocation system. Login attempts are
limited to ten/minute per backend process; use the documented single-worker service.

Caddy exposes only `/api/operator/*` and `/api/registry/*` in addition to public
workstation status. Registry endpoints require the operator session, and mutations
require a same-origin workstation header. Other operational APIs remain blocked.
HTTPS is required for login; the localhost development launcher cannot use this
Secure cookie over plain HTTP. No new external ports are exposed.

Migration 003 creates the registries transactionally. After deployment, workstation
status reports `registry_schema_ready: true` when
required columns are readable. `schema_ready` continues to report the earlier
paper/assessment schema separately. Provider connectivity remains unavailable.

Validation: production build and DOM regression tests (`npm test --prefix ui`),
plus API tests (`PYTHONPATH=backend/src python -m unittest discover -s backend/tests -v`,
with FastAPI, psycopg and httpx installed). API tests cover sessions, CSRF headers,
expiry, invalid input, rate limiting and database-error mapping using mocked database
connections. Live PostgreSQL relationships and deployment must also be verified on
Ubuntu; these tests do not prove live broker behavior.

Password setup and login accept 6–256 characters. Existing passwords are preserved
by normal updates. To explicitly replace the existing operator password locally:

```bash
sudo python3 scripts/configure-workstation-login.py --reset-password
sudo systemctl restart corei-os-mini-workstation
```

Reset keeps the username and database settings, prompts for the new password twice,
and invalidates prior sessions after service restart. Do not put the password in
shell arguments or chat. Setup without the flag preserves the configured login.


## Connector profiles — release 0.6.0

Providers → Connector profiles adds editable connection configuration for MT4 EA,
MT5 EA, cTrader Open API, broker APIs, exchange APIs, FIX, data feeds and custom
adapters. Create a provider and connection first, then link a profile using that
connection UUID. There is one profile per connection; create additional connections
for independent environments/configurations. Profile edits retain the connection
identity. This is metadata, not terminal/session creation.

- MT4/MT5 require a Windows or Ubuntu/Wine terminal runtime and broker server label.
- Other families use SERVER_API. An adapter identifier such as `upstox` names the
  intended implementation; entering it does not install or load an adapter.
- API responses derive NOT_IMPLEMENTED, NOT_CONNECTED, NOT_OBSERVED, unverified
  account and unavailable execution statuses. Enabling configuration cannot change
  these facts. PAPER/SANDBOX/LIVE labels do not enable execution.
- Do not enter passwords, tokens or secret-bearing URLs. This release has no broker
  credential storage, broker authentication, runtime agent, health probe or order route.
- `.egg-info/` is generated Python installation metadata and is now ignored by Git.

Install/update on the existing Ubuntu server (keeps existing database/login credentials):

```bash
cd /home/coreeye/corei-os-mini && git pull --ff-only origin main && bash scripts/configure-workstation-database.sh && bash scripts/deploy-workstation.sh
curl --fail --silent --show-error https://corei-os-mini.coreeye.in/api/workstation | python3 -m json.tool
```

Expected: release `0.6.0-connector-profiles`, `connector_schema_ready: true`, and
connector_profiles capability available. Provider connectivity and execution remain
unavailable. Migration 004 is additive and is applied by the existing transactional,
checksum-guarded migration runner. Do not modify migrations 001–003.

Validation for this change: backend profile/auth/readiness tests and existing auth
suite, deployment-script unit tests, TypeScript production build and DOM interaction
regressions including profile create/edit. Backend database calls and browser API
responses in those suites are mocked. Real PostgreSQL migration and Windows/Wine
terminal connectivity are not established by these tests; verify schema readiness
on Ubuntu after deployment. No live broker connection is claimed.
