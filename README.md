# COREI-OS-MINI

COREI-OS-MINI is a production trading platform built to the lifecycle in the project documents:

**Connect → collect → validate/select data → analyse → evaluate strategy → signal/intent → resolve account children → final child risk → execute → reconcile → monitor.**

**Where the project stands:** see [`docs/GAP_REGISTER.md`](docs/GAP_REGISTER.md) and owner decisions in [`docs/DECISIONS.md`](docs/DECISIONS.md). In short: the Configuration domain (providers, connections, accounts, instruments, symbol mappings, encrypted credentials, order-sending controls, audit log) is in place. Provider adapters (MT5, REST API), the order lifecycle, risk limits and reconciliation are not built yet, so no order can be sent.

## Controls

- **Order sending** is a firm-wide switch per environment (DEMO, LIVE), **off by default**. Turning it on needs a reason and typed confirmation; every change is audited (DEC-19).
- An account can send orders only when its provider and connection are active, its permission is `TRADING`, its credential allows trading, a provider adapter exists and order sending is on for its environment. Each account shows exactly which of these still blocks it.
- **Credentials** (MT5 passwords, API keys, OAuth clients) are encrypted with AES-256-GCM using a server key file, bound to their record, and never returned by the API.
- **Audit log**: every configuration and control change is recorded with actor, before/after state and reason. The log is append-only in the database. Secrets are never written to it.
- **Optimistic versioning**: a save made against a stale copy is rejected instead of overwriting someone else's change.
- All configuration routes require operator sign-in.

## Project layout

```text
backend/
  sql/                    numbered migrations, applied in order by scripts/configure-workstation-database.sh
                          (009 = configuration foundation; 003–007 are historical and dropped by 009)
  src/corei_os_mini/
    api.py                FastAPI app, health and workstation identity
    config_models.py      configuration input rules (exact decimals, per-platform connection settings)
    config_store.py       configuration persistence: cross-record rules, versioning, audit, readiness
    config_api.py         /api/config routes
    credentials.py        credential vault (AES-256-GCM)
    broker_catalog.py     MT5 servers.dat reader (access points)
    operator_auth.py      operator sign-in
    market_data.py        feed validation and deterministic feed selection
    strategy.py, allocation.py, risk.py, workflow.py
                          trading-core logic, currently exercised by tests and a disabled demo route
  tests/                  unittest suites (PostgreSQL suites need COREI_TEST_DATABASE_URL)
connectors/mt5-direct-check/   MT5 servers.dat reader used by Configuration › Connections
docs/                     design documents, owner decisions, gap register
scripts/                  install, database, deploy and repair scripts
ui/                       React + TypeScript workstation (Configuration workspace in src/Configuration.tsx)
```

## Configuration model

| Record | Key fields | Fixed after creation |
|---|---|---|
| Provider | code, name, type (broker / exchange / data vendor), status | code |
| Connection | provider, platform (MT5 / REST API), environment (DEMO / LIVE), purpose (market data / trading / both), platform settings (MT5 server + ordered access points, or REST base/stream URL + auth method), status | provider, code, platform, environment |
| Account | connection, code, broker account number, base currency, trading permission (disabled / read-only / trading), status | connection, code, account number |
| Instrument | symbol, asset class, currencies, price/quantity decimals, tick size, min quantity, quantity step, contract size and unit, session, status | symbol |
| Symbol mapping | connection, instrument, broker symbol, usage, optional broker contract size / price digits | connection, instrument |

MT5 logins are saved on each account (master or investor password); REST API keys or OAuth clients are saved on the connection.

## Tests

```bash
python -m unittest discover -s backend/tests -v      # add COREI_TEST_DATABASE_URL for the PostgreSQL suites
python -m unittest discover -s scripts/tests -v
npm ci --prefix ui && npm test --prefix ui
```

## Final design references

Final Document Index v1.1, Blueprint v1.2, Process Flow v1.4, Technology Inventory v1.3 and Master Work Prompt v1.1 in `docs/`, as amended by `docs/DECISIONS.md`. Older versions are historical.

The sections below are the release history of the workstation and setup scripts.

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


## Connection workspace clarity — release 0.6.1

The provider registry now includes field-level information buttons, related-record
selection by name, an expandable connection guide, aligned responsive forms, and
compact tables with expandable full record details. Internal IDs remain available
without dominating every row. Configuration is distinct from observed runtime state.

- Provider: organisation supplying access. Connection: a named access configuration.
- Connector profile: platform/protocol and runtime settings for that connection.
- Account: provider-issued trading login/account identity, currently registered under
  a connection. Stable identity across multiple connections remains future work.
- Terminal computer / terminal instance: optional inventory labels added by migration
  005. These are NOT connection endpoints, authenticated agent identities or credentials.
  Existing profiles remain valid with blank labels.

Each workspace group's top-right toolbar opens its active view in a new tab or
requests a new window. Selected registry context is included in a validated URL;
unsaved forms, passwords and account records are never included. Detached workspaces
start with one group and do not overwrite the main saved layout. Browser settings
can open a requested window as a tab or block it. A web app cannot choose a different
browser executable. Same-browser windows share the operator cookie; logout broadcasts
a non-secret notification to clear other open registry views. Another browser needs
its own login. Opening a view does not start another backend or terminal session.

Update using the same pull → configure database → deploy command above. Expected
release: `0.6.1-connection-workspace`, connector_schema_ready true (migration 005).

### Remote terminal implementation contract (planned; not an active endpoint)

Host A is the Ubuntu COREI VPS. Host B runs Windows MetaTrader or MetaTrader under
Ubuntu/Wine, at a different IP if needed. Host B signs into the broker using the
broker account login, password and broker server. The bridge on Host B will make
outbound TLS-authenticated requests to the COREI domain on 443. Do not expose RDP,
Wine, terminal desktop ports, or PostgreSQL for this channel. Broker-server identity
is different from both Host A and Host B addresses. No static public Host B IP is
required by this design.

The next bridge implementation must provide:
1. A scoped, revocable bridge credential bound to one terminal instance, connection
   and expected account/server. This is separate from operator and broker passwords.
2. Identity handshake including platform, instance, account, broker server,
   demo/live mode and protocol version; reject mismatches before accepting data.
3. Sequenced heartbeat/quote envelopes with source and receive timestamps, bounded
   payloads and queues, retry backoff, duplicate detection and explicit stale state.
4. Instrument discovery and native-symbol mapping, with no claim that a symbol name
   alone establishes contract equivalence. Reconnect revalidates account identity.
5. Read-only market-data validation before any execution channel. Order routing,
   durable intent, deduplication and uncertain-outcome reconciliation belong to the
   later execution phase. No HTTP request returning success can imply a fill.

A simple EA HTTPS transport is suitable to validate the remote flow; MetaQuotes
WebRequest is synchronous and requires an allowed URL. It must use bounded timeouts
and must not be represented as a proven HFT transport. Measured performance will
inform a later persistent transport if needed. Windows/Wine compatibility must be
verified on the actual terminal hosts. No bridge binaries or endpoints are shipped
in 0.6.1; this section specifies the implementation boundary without inventing a
working connection.

Research references checked 8 November 2026:
- Westernpips Webcliker Connections documents Analyse IP using MT4 `.srv` and MT5
  `.dat` / `config/servers.dat` files. This feature discovers broker endpoints;
  it is not remote access to a terminal computer and not a substitute for credentials.
  https://docs.westernpips.com/documentation/westernpips-webcliker/wc-connections/
- Westernpips separately documents EA terminal connection configuration:
  https://docs.westernpips.com/documentation/westernpips-private-7/westernpips-private-7-algos/setting-up-a-connection-with-the-mt4-mt5-broker-via-the-ea-connector/
- MetaQuotes WebRequest:
  https://www.mql5.com/en/docs/network/webrequest

No `.srv`/`.dat` importer or proprietary MetaTrader direct-server protocol is
implemented or claimed. A future importer requires format validation and bounded,
non-executable parsing; discovery metadata must never silently choose the live
account/server or overwrite a verified connection.


## Terminal installation panel — 0.6.2

Connector profiles → Install terminal files now provides a downloadable source kit,
platform/OS selectors, PowerShell and Ubuntu/Wine install commands, compile/attach
instructions, checksum and removal guidance. MT4/MT5 profile rows preselect the
correct family/runtime. No new migration is required.

The kit contains COREIOSMiniTerminalCheck.mq4/.mq5, install-terminal.ps1,
install-terminal.sh and INSTALL.txt. The checker prints local account/server
identity matching and the chart symbol's quote timestamp every 30 seconds. It has
no network, credential or order functions. VPS=NOT_CONNECTED is expected. This
installation kit is a readiness stage, not the authenticated bridge promised in
the Phase 1 roadmap. Do not mark any provider connected after installing it.

Source and installers live in connectors/terminal. The UI prebuild invokes
scripts/build-terminal-kit.py to generate a deterministic ZIP, individual files,
SHA256SUMS.txt and manifest.json in ui/public/install (ignored generated output).
Vite copies those files into the deployed static build. Python 3 is required for
this packaging step; the Ubuntu install already requires Python. No remote URLs,
secrets or user-provided scripts are used by the builder.

Installers copy source into the terminal owner's selected data folder and preserve
an existing source backup. They do not compile, replace a running compiled EA,
change server files or start broker sessions. Compile with MetaEditor F7, require
zero errors, and attach to a separate chart. No compiled .ex4/.ex5 files are shipped;
MetaEditor compilation and Windows/Wine compatibility remain unverified here.
PowerShell installer was reviewed but not executed in this Linux environment.

Update using git pull and scripts/deploy-workstation.sh. Expected runtime release:
0.6.2-terminal-installation. Database schema remains migration 005. Validate the
public kit manifest at /install/manifest.json and download through the workspace.

Validation includes frontend build/DOM interactions, reproducible ZIP/checksum
checks, and executing the Linux installer against temporary terminal directories
(including spaces, backup preservation and wrong-platform rejection). These tests
do not establish MetaTrader or broker connectivity.


## Workspace experience — release 0.6.3

The workstation now uses a consistent visual hierarchy across the existing themes:
labeled vector navigation icons, grouped explorer entries, a refined Standard dark
palette, readable status/context panels, consistent spacing and quieter controls.
Existing saved themes/layouts, split groups, keyboard controls, tab/window actions
and terminal installation remain available. The inspector shows real application
readiness and relevant help instead of unbound provider/account selection placeholders.

Registry pages now prioritise records. Choose New provider/account/instrument/etc.
to open a native modal side editor, with keyboard focus containment and Escape/close
controls. Save closes the editor only after success. Validation errors also appear
inside the editor. Existing connector profile editing and disabled-by-default
creation are preserved. Filter this page searches only the records already loaded
on the current paginated page, including resolved parent names; it is not a global
database search. Information buttons and advanced internal-ID inputs remain.

The explorer's implemented provider, connection, connector-profile, account,
instrument and symbol-mapping entries now navigate directly to those registries.
Future strategy/execution capabilities remain explicitly unavailable.

Build output is explicitly cleaned by Vite before rebuilding. The DOM regression
suite reads the entry script from dist/index.html instead of selecting an arbitrary
old JavaScript file. This prevents tests from exercising a previous bundle when
multiple hashed files have accumulated.

Validation: TypeScript/production build and DOM regression suite (including record
creation/editing, page filtering, installation, five themes, split groups and window
controls). Chromium checks used mocked API responses, inspected overview, registry
and side editor screens at 1600x1080 and 1024x768, checked all five themes and verified
native-dialog focus and Escape. These are UI checks; they do not verify broker
connectivity. No dependencies, database migration or broker execution changes were
introduced. The downloadable local terminal-readiness kit remains version 0.6.2.

Update: git pull --ff-only origin main, then bash scripts/deploy-workstation.sh.
Expected /api/workstation release: 0.6.3-workspace-ux. Existing database and operator
credentials are retained; no database setup command is required for this release.


### Dense workspace UX — 0.6.4

Compact tables, toolbars and state panels prioritize working information. Text
sizes use the shared interface-size control. Comfortable density and all five
colour themes remain available. New layouts start with Inspector and bottom panel
closed; existing saved layouts are preserved and their toggles remain available.

Registry navigation is scoped to its domain. Active tabs show the selected
registry. Open views remain mounted across ordinary tab switches and focus mode.
Dirty forms warn before discard or destructive layout changes and page unload;
this is an in-memory draft, not persistent recovery after browser restart.

Registry queries support bounded server-side `q`, validated `sort`, and
`direction=asc|desc`. Reference selectors search all records, returning at most
200 matching suggestions. Record lists have sortable headers and compact selected
record details. PUT supports configuration corrections in all registries, keeping
parent/identity fields immutable (account identity/environment, instrument
symbol/class, and mapping identity require a new record). These controls never
connect a provider or submit orders. No database migration is needed.

Backend readiness refreshes every 30 seconds while the page is visible and shows
the last successful check. Future workspaces show dependency/action tables rather
than large empty panels; the dependencies are not a claim of verified trading
readiness. Terminal kit remains version 0.6.2, local diagnostics only.

Remaining audit scope: record-specific independent document tabs, saved/custom
column views, centralized contextual Inspector, resumable multi-step onboarding,
assessment browser/structured result presentation, and complete menu accessibility.
Authenticated terminal pairing/quote ingestion and operational trading remain
separate backend implementation work, not completed by this UX release.

Validation for 0.6.4: TypeScript/Vite build; production-bundle DOM tests including
search, dirty-form discard/cancel, scoped navigation and browser shortcuts; 15
backend tests (database calls mocked); Chromium visual checks with populated mock
records in all five themes at 1440x900 and 1024x768. Backend checks ran on Python
3.12 with FastAPI 0.115.12 and psycopg 3.2.6, not the deployment's Python 3.14.
No live broker or PostgreSQL integration is claimed by these tests.


### Table-first registry and floating editor — 0.6.5

Registry tables follow the compact search/action bar. Definitions, connection
guidance and operating limits are collapsed below the records. Installation
instructions also follow the table. Configuration-only status remains visible.

Add/Edit opens a centred modal window, sized initially to its form content. Drag
the title strip to move it, drag the bottom-right grip to resize, or use arrow
keys while focusing either handle. Centre/reset restores the window geometry.
The window stays within the viewport; its form scrolls when needed. Escape and
the close button retain dirty-form confirmation. Clicking the backdrop does not
discard a form. Modal focus containment remains; this is an in-page dialog, not
a separate browser window. No schema or trading behaviour changes.

Validation: production build and DOM interactions, plus Chromium pointer dragging,
resizing, reset, dirty-form protection and table/reference order with mocked data
in all five themes. No live provider connection is claimed.

## Gap fixes — release 0.7.0 (paper-only and shared-connection items superseded by 0.8.0)

Closes defects D-01 … D-07 in `docs/GAP_REGISTER.md`:

- **REPLICATE** no longer copies the full parent quantity; each account declares `replicate_scale_bps`.
- **SELECT_ONE** uses a deterministic, versioned routing rule; the rule name is recorded in each run.
- **Paper-only boundary**: LIVE removed from the account form and registries; migration `008_paper_only_boundary.sql` disables existing LIVE accounts/profiles and blocks creating or enabling them.
- **Shared connections**: broker accounts on the same server now share one connection and connector profile. Existing records are left as they are.
- **Tests**: 25 new trading-core tests (strategy, allocation, risk, workflow, market data).

Update on the server with the normal pull, `scripts/configure-workstation-database.sh` (applies migration 008) and `scripts/deploy-workstation.sh`.

## Configuration rebuild — release 0.8.0

Replaces the earlier registries, broker-account form, connector profiles and terminal-installation kit with one Configuration workspace (DEC-20). Migration `009_configuration_foundation.sql` **deletes the old configuration tables and their records**, including saved broker passwords, and creates the new model. Paper-run and market-data-assessment history is kept. The paper-only account restriction from 0.7.0 is withdrawn (DEC-18); order sending is instead governed by the firm-wide switch (DEC-19).

After updating, re-enter providers, connections and accounts in **Configuration**.
