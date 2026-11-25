# COREI-OS-MINI

COREI-OS-MINI is a production trading platform built to the lifecycle in the project documents:

**Connect → collect → validate/select data → analyse → evaluate strategy → signal/intent → resolve account children → final child risk → execute → reconcile → monitor.**

**Where the project stands:** see [`docs/GAP_REGISTER.md`](docs/GAP_REGISTER.md) and owner decisions in [`docs/DECISIONS.md`](docs/DECISIONS.md). In short: the Configuration domain (providers, connections, accounts, instruments, symbol mappings, encrypted credentials, order-sending controls, audit log), users and roles, and the strategy registry with its deployment matrix are in place. Provider adapters (MT5, REST API), the order lifecycle, risk limits and reconciliation are not built yet, so no order can be sent.

## Controls

- **Order sending** is a firm-wide switch per environment (DEMO, LIVE), **off by default**. Turning it on needs a reason and typed confirmation; every change is audited (DEC-19).
- An account can send orders only when its provider and connection are active, its permission is `TRADING`, its credential allows trading, a provider adapter exists and order sending is on for its environment. Each account shows exactly which of these still blocks it.
- **Credentials** (MT5 passwords, API keys, OAuth clients) are encrypted with AES-256-GCM using a server key file, bound to their record, and never returned by the API.
- **Audit log**: every configuration and control change is recorded with actor, before/after state and reason. The log is append-only in the database. Secrets are never written to it.
- **Optimistic versioning**: a save made against a stale copy is rejected instead of overwriting someone else's change.
- **Users and roles** (DEC-23): Administrator, Trading operations, Risk approver, Observer. Sessions are server-side and end when a user is disabled or their roles change. Temporary passwords must be changed at first sign-in; 5 failed sign-ins lock an account for 15 minutes.
- **Strategies and deployments** (DEC-25): approved strategy versions cannot change; a strategy trades only through an ACTIVE deployment row. On LIVE accounts a different person must activate a deployment than the one who proposed it (DEC-24).

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
    strategy_registry.py  strategies, versions, algorithm catalogue, deployment matrix and readiness
    broker_catalog.py     MT5 servers.dat reader (access points)
    operator_auth.py      users, roles, sessions, password rules, user administration
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

MT5 logins are saved on each account (master or investor password); REST API keys or OAuth clients are saved on the connection. Naming conventions and worked examples (MT5 multi-broker/holder/account, Upstox OAuth): [`docs/CONFIGURATION_GUIDE.md`](docs/CONFIGURATION_GUIDE.md).

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

## Users, roles, strategies and deployments — release 0.9.0

- MT5 connections: full server description from `servers.dat`, searchable server picker, selectable and searchable access points (re-run `scripts/setup-broker-accounts.sh` once to rebuild the server-file reader).
- Migration 010: users and sessions. Sign in once with the existing server login; it becomes the first administrator. Create the other users under **Configuration › Users**.
- Migration 011: strategies, immutable approved versions and the deployment matrix.

### servers.dat analyser (Westernpips-style)

**Configuration › Connections › New connection (MT5) › Analyze servers.dat** reads every broker server and access point in a terminal's `servers.dat` (up to 32 MiB, 100,000 servers) into one table of *address · server name · company · access point*, with a **broker filter** (server name or company) and an **address or name** filter. Tick rows of one server and choose *Use selected access points*; they are used in the order ticked and the server's other access points are kept unselected. Extended (newer-format) access records are read where they contain an address; anything unreadable is counted and shown, never silently dropped. Parsed files stay in memory for 30 minutes for the user who uploaded them and are not stored.

If brokers that another tool shows are missing or listed as *no readable address*, run `bash scripts/inspect-servers-dat.sh /path/to/servers.dat` on the server. It reports how many access records the installed SDK could decode and prints the structure of any it could not, so the reader can be extended for that layout.

## MT5 servers, routing and one-step accounts — release 0.10.0

- **Accounts › Add MT5 account**: login, holder, password and server in one window. The server's connection is reused (or created) automatically; see docs/CONFIGURATION_GUIDE.md and DEC-26.
- **Find servers** searches the MT5 broker directory live (SDK `Broker.Search`), next to *Open servers.dat*. Check from the server with `bash scripts/search-mt5-broker.sh VTMarkets`.
- **Per-account address choice** (DEC-27): server's addresses (default), fastest first, or the account's own list. Switching keeps the account's own list.
- **Test latency** (DEC-28) on connections and accounts: TCP connect time from the COREI server, public addresses only, kept as history.
- Migration 013 adds `cfg_accounts.access_mode`, `cfg_accounts.access_points` and `cfg_latency_checks`. Apply migrations, re-run `scripts/setup-broker-accounts.sh` (reader update), then deploy.

## Institutional controls — release 0.11.0

From the provider/connection/account audit of 11 Nov 2026 (DEC-29..37). Migration 014.

- **Health** (new first screen): every account's readiness and reasons, connection state and clock, outbound IP, clock sync, pending approvals, failed alerts, and a set-up checklist.
- **Approvals** (four-eyes): LIVE address additions, LIVE reactivation, LIVE password replacement and LIVE one-step account set-up wait for a second person. Single operator: a Risk approver can switch the policy off under *Order sending › Policies* (recorded, alerted, and shown as a warning).
- **Re-enter password** before saving credentials, turning order sending on, approving, and changing policies or users.
- Providers: legal entity, regulator, licence, jurisdiction, execution model, contacts, exposure limit, holidays. Connections: connection method (both MT5 methods), server clock and DST, order rate limit, registered static IP, maintenance windows. Accounts: margin mode, broker group, leverage, stop-out, broker limits, expiry, ownership and mandate, other users of the login, account groups; master and investor passwords; password-use log; symbol rules and an order check.
- Archive instead of delete; per-account symbol mappings; sortable lists, filters, links between records; CSV import (checked, all-or-nothing) and export.
- **Alerts:** e-mail (set `COREI_SMTP_HOST`, `COREI_SMTP_PORT`, `COREI_SMTP_USER`, `COREI_SMTP_PASSWORD`, `COREI_SMTP_FROM` in `/etc/corei-os-mini/workstation.env`), Telegram (bot token on the channel) or webhook.
- **Upstox daily login** from the account window (OAuth redirect `/api/oauth/upstox/callback` or authenticator code + PIN).
- Outbound IP is discovered automatically; set `COREI_EGRESS_IP` in the env file if the server sits behind a fixed NAT.
- Key rotation: `scripts/rotate-credential-key.py` (see its header).

## Broker reads and Infrastructure — release 0.12.0

Migration 015 (DEC-38..40).

- **Lot sizes stay flexible.** Instrument and broker quantities are labelled as contract and broker limits; the size of each order is decided per order by the strategy, signal strength and portfolio risk (G-P3-06). Health, Approvals, Alerts and Order sending now scroll.
- **Real data from MT5:** on an MT5 account, *Real data from the broker › Log in and read now* logs in once (investor password preferred), stores margin mode, leverage, group, stop-out, the server clock offset, the address that answered, and every symbol's broker limits, then disconnects. Each read is kept with every address tried. Nothing is traded.
  - Run `bash scripts/describe-mt5-api.sh` once on the server and send the file it writes: it lists the installed SDK's exact login and symbol fields so any field not yet matched can be added.
- **Infrastructure** (new item in the left bar): health of the database, services, host, clock, network and broker tools; for administrators a spreadsheet-style table editor (filter per column, sort, edit in place with the keyboard, add and delete rows, export CSV), a read-only SQL console, live database sessions and the edit log. Every change asks for a reason and your password and is logged before and after. Configuration tables open for direct editing only when the policy `infra_edit_controlled_tables` is turned on (Config › Order sending › Policies).

## Market data — release 0.13.0

Migration 016 (DEC-41..44). Open source only: PostgreSQL, Parquet, DuckDB.

- **Market data** (left bar) — *Live* prices, spread, age and score with each collector's state; *Collection plans* (matrix of instruments × accounts; what to collect, when, how long to keep, quality rules; size estimate before starting); *Quality* (broker comparison per instrument); *Events*; *Backfill* from broker history; *Controls* (pause all or one account, with a reason); *Archive* (history files with fingerprints, backups).
- **Collector service** `corei-os-mini-collector` streams prices from MT5 (investor password, read-only), writes the journal first, then PostgreSQL every second; reconnects through the account's addresses and fills outages from broker history.
- **Nightly timer** `corei-os-mini-md-nightly` (02:30 IST): archive to Parquet with fingerprints, journal fingerprints and retirement, verification of every file, backups.
- **Backups:** set `COREI_BACKUP_DIR=/path/on/another/disk` in `/etc/corei-os-mini/workstation.env`, then redeploy. Until then Health shows backups as not configured.
- **Infrastructure › History**: read-only DuckDB over the archive (views `ticks` and `bars`).
- Every order check now also checks the live price: age, spread and feed state.
- Data folder: `/var/lib/corei-os-mini/market-data` (journal/, archive/).

## Workstation shell — release 0.14.0

DEC-45. No migration.

- **Ctrl K — Go to anything:** type a few letters (e.g. *acc*, *plans*, *tables*) to jump to any screen or section, or run a command.
- **Top bar** shows live status: market-data feeds, order sending, approvals. Click a chip to go there.
- **Rail** (left): Home · Market · Strategies · Orders · Portfolio · Monitor · Setup · System · Settings. A red number means something there needs attention.
- **Home** lists what needs attention, each with an *Open* button, plus quick actions. **Monitor** shows the same list and the health of every part of the system.
- Every screen lists its sections on its left (Setup groups its 14 sections under five headings).
- Splits, tabs, the navigator (Ctrl B) and the bottom panel are still there (Layout menu), off by default. Ctrl-click a rail icon to open a screen as an extra tab; right-click a tab to open it in a separate window.
- Broker login errors are explained in plain words on Market data › Live (e.g. *Broker refused the connector version*), retried every 5 minutes when retrying cannot help, and alerted once.

## Symbols come from the provider — release 0.30.0

DEC-59. Migration `021_symbol_catalog.sql`. **Reader rebuild needed** (`scripts/setup-broker-accounts.sh`): the MT5 reader now returns the full symbol-name list.

- Every source has an **Available** tab next to *Collected* (Market data › Dashboard): every symbol the provider offers, loaded automatically —
  - **MT5**: the server's symbol list and rules at every login (*Refresh symbols* logs in; names beyond the 2,000 with full rules are listed too);
  - **Upstox**: its daily instrument file (NSE, BSE, MCX: equities, indices, futures, options with lot, tick, expiry, strike), reloaded nightly;
  - **FIX**: the venue's list via SecurityListRequest.
- Filter by asset class (with counts), exchange, segment/group, collected or not; search symbol and name; 100 per page.
- Pick one or many → **Collect** with the options shown (purpose, role, quotes/bars/depth, timeframes, days kept, archive, start now). COREI **suggests the instrument name** (XAUUSD.r → XAUUSD, SPX500.cash → US500, Nifty 50 → NIFTY50; editable), creates or links the instrument and its symbol mapping, so the same instrument joins across sources.
- API: `GET /api/md/catalog/{account}`, `POST /api/md/catalog/{account}/refresh`, `POST /api/md/catalog/{account}/collect`; nightly job `catalogues`.

## Stages: Market data collects, Market intelligence compares — release 0.29.0

No migration, no reader rebuild. Workflow: `docs/WORKFLOW.md`; features: `docs/MARKET_DASHBOARD.md`.

- **Stage 1 · Market data › Dashboard** is now *workspaces › sources › instruments*: each source is its own section (state, address, lag, quality, problem) with **+ Instrument** (every collection-plan option), **pause/resume source**, **backfill**, **details**, reorder and remove; each instrument row has its quote and health, plan status and what is stored, with chart + time & sales, settings, pause/start and remove.
- **Stage 2 · Market intelligence › Select & compare** holds the cross-source board (pickers, filters, 26 columns, grouping, 1/2/4 charts with source overlay, time & sales, sources comparison, CSV); the stage map stays as a tab.

## Market dashboard controls for many sources and instruments — release 0.28.0

No migration, no reader rebuild. Full list: `docs/MARKET_DASHBOARD.md`.

- **Sources** and **Instruments** pickers (searchable, grouped by provider · technology and by asset class, whole-group select, *only*); filters for environment, feed role, status and minimum quality; search.
- 26 board columns to show or hide, sort by any, group by instrument / source / provider / asset class; planned symbols without a price are shown, not hidden.
- 1, 2 or 4 chart panes with compare-to-another-source; refresh rate, pause, local/UTC time, CSV export; workspaces: new, duplicate, one per source, rename, close.
- API: `GET /api/md/sources`, `GET /api/md/instruments`; `md/latest` carries provider, technology, environment, role, instrument, latency and last-minute quality.

## Market dashboard with workspaces — release 0.27.0

DEC-58. No migration, no reader rebuild. How prices are collected, stored and used: `docs/MARKET_DATA_FLOW.md`.

- **Market data › Dashboard** (opens first): workspaces as tabs — *All sources*, one per account (automatic), and your own (any accounts and symbols; they follow you to other devices). Each workspace: health strip (feeds live, symbols, stale, quality, prices collected, events), **quote board** (bid/ask with change flashes, spread, day change, day range, age, quality, 5-minute trend), **chart** of the selected symbol (candles S1…D1 or ticks, crosshair readout, last price), **time & sales** with latency, **sources** (the same instrument from every account, difference in points, which feed is used) and **feeds & events** of the workspace's accounts. The previous table stays as *Table*.
- API: `GET /api/md/ticks/{account}/{symbol}`; `GET /api/md/latest` now includes the day's open, high and low.
- Database errors now say what failed (schema out of date, permission, unreachable, or the SQL error code) instead of one generic message; the Upstox login retries saving its token when the database is briefly unreachable.
- MT5: when the SDK is older than the server, the collector retries once an hour instead of every 5 minutes (each try is a real login).

## MT5: broker › accounts, fastest address per account — release 0.26.0

DEC-57. No migration, no reader rebuild. Audit: `docs/AUDIT_2026-11-14.md`.

- **MetaTrader 5 › broker › accounts.** No server level: each account line shows its server name and the latency of the address it uses first. Server records are kept automatically from the accounts.
- **Server & route on every MT5 account:** *Fastest (automatic)* is the default, re-measured every 6 hours and before each login; *Server's order* and *Pinned* remain. **Measure now**, **Refresh from broker** (new addresses the broker publishes are added and measured; LIVE additions go to approval) and **Change server…** (pick another server of the same broker from its published list; LIVE moves need approval).
- **Add who** under MT5 asks only for the broker, then opens *Add MT5 account*.

## Connectivity: technology › who › accounts — release 0.25.0

DEC-56. Migration `020_provider_types.sql` (provider types). No reader rebuild needed.

Setup › **Connectivity** is one tree, in the order a firm thinks about its sources and accounts:

| Level | What | Examples |
| --- | --- | --- |
| 1 Technology | how COREI reaches the provider | MetaTrader 5, MetaTrader 4, cTrader, FIX 4.4/4.2, Broker & exchange APIs, Fast feeds & data vendors, Exchange direct & data centre |
| 2 Who | the provider and its server, session or API | MT5 › VT Markets › VTMarkets-Live 3; FIX › LMAX Global › price session and order session; API › Upstox |
| 3 Accounts | the logins on that server | MT5 logins 1298617, 5056977914; LMAX execution accounts |

- **Left: the tree** (expand, collapse, filter, arrow keys; status dot per item: ready, needs action, failed, not connected, off/planned). **Right: the selected item** with its facts and the next action: a technology has *Add who*, a who has *Add server / session*, a server has *Add account*, an account shows its readiness and *Log in and read now*.
- **Group by Technology or by Provider type** (broker, exchange, ECN, prime broker, liquidity provider, fast feed, data vendor, data centre). Providers not yet connected are listed under *Not connected yet*.
- **Add who in one step** (`POST /api/config/venues`): an existing provider or a new one, plus its MT5 server (addresses, direct SDK or terminal bridge), FIX session (CompIDs, host, port, TLS) or API, with environment and role. LIVE connections with new addresses still go to approval.
- Planned technologies (MT4, cTrader, fast-feed native protocols, exchange native protocols) are shown greyed with their adapters, so the whole map is visible; nothing can be added to them until built.
- The record lists stay as tabs: *Who (providers)*, *Servers & sessions*, *Accounts*. Nothing was removed.
- `GET /api/config/tree` returns the whole tree with readiness.

## Any provider, through any adapter — release 0.24.0

DEC-55. Migration `019_adapter_catalogue.sql` (platform FIX, credential FIX_LOGIN). No reader rebuild needed.

- **Providers are anybody**: a broker directly, a broker through MT5/MT4/cTrader, an ECN or prime broker over FIX, a fast feed or data vendor. Each is reached through an **adapter**; Setup › Connectivity (each technology) lists every adapter with what it does for prices and orders and its build state (built: MT5 direct, MT5 terminal bridge, FIX 4.4/4.2, Upstox; planned: MT4, cTrader, LMAX, Rithmic, crypto exchanges, vendor feeds). API: `GET /api/config/adapters`.
- **Every connection has a role**: *Data source* (a few chosen, high-quality price feeds), *Execution* (trading accounts) or both. Collection plans take prices only from data sources; an execution-only connection is refused with an explanation. The tree shows each connection's role.
- **FIX 4.4 / 4.2 data adapter** (`md/fix_reader.py`): logon (CompIDs, sub-IDs, optional username/password, TLS), MarketDataRequest for top of book, snapshots and incremental refreshes, heartbeats and TestRequest, sequence-reset handling, rejects shown verbatim. *Log in and read now* checks the session. Usable today for broker FIX APIs, LMAX, ECNs, prime brokers and vendor FIX feeds.
- **MT5: the direct SDK is the recommended method again** (as institutional bridges such as Westernpips use it), on condition that the mtapi licence includes its update subscription; the terminal bridge stays as the fallback.

## MT5 through the official terminal — release 0.23.0

DEC-54. Migration `018_terminal_bridge.sql` (bridge token credential kind, CLOCK_MEASURED event). Guide: `docs/MT5_TERMINAL_BRIDGE.md`.

- **The direct SDK route is a dead end for live brokers.** Both MetaQuotes-Demo and VTMarkets-Live 3 now accept the login and then answer in a protocol the licensed mtapi SDK cannot read; only the SDK vendor can fix that, and every MT5 release will reopen it.
- **The terminal bridge** (`connectors/mt5-bridge/corei_mt5_bridge.py`) runs next to the official MT5 terminal (Windows VPS, or Wine) and uses MetaQuotes' own MetaTrader5 Python package. COREI connects over TLS (certificate pinned by fingerprint, shared token), sends the login once per session, and streams the same line protocol as the direct reader: *Log in and read now*, plans, journal, quality, bars, backfill and archive are unchanged.
- **Add MT5 account** offers *How COREI reaches it*: terminal bridge (default) with host, port, fingerprint and token, or the direct SDK. Existing connections switch under *Connection method*.
- The broker clock is measured from the first live tick when the source cannot state it (CLOCK_MEASURED event); stale tick times are never taken as the clock.
- Partitions are ensured on every write (a cached list went stale when a partition was dropped).

### MT5 build ladder (0.22.1)

Rebuild the reader: `bash scripts/setup-broker-accounts.sh`.

- When the broker refuses the SDK's own MT5 build (OLD_VERSION), the reader now climbs from the **oldest** client build (4000, 4500, 5000, 5370 … 6241) instead of jumping to the newest. Claiming 6241 got VTMarkets-Live 3 to accept the login and then answer in a protocol the SDK cannot read (`accept authorized, get login id` → IndexOutOfRangeException); the lowest accepted build is the closest to the SDK's own protocol.
- If even that build fails after the login was accepted, the error is **MT5 SDK too old for this server (login accepted)**: the password is right, the SDK must be updated. COREI stops at the first address (no repeated logins) and retries every 5 minutes with the build that was accepted, so each retry is one login.

## One set-up path — release 0.22.0

DEC-53. No migration. Nothing removed: every record list, form, control and option is still there, reached in a fixed order.

- **Setup reads top to bottom in the order you do it:** Overview → **1 Connect** (Brokers & accounts, Account groups) → **2 Instruments** (Instruments, Symbol mappings) → **3 Strategies** (Strategies, Deployments) → **4 Controls** (Order sending, Approvals, Alerts) → **5 Administration** (Users, Audit log). The same list is in the side bar and, when the side bar is hidden, as one strip above the screen.
- **Brokers & accounts is one screen:** a tree *broker › server or API › account* with environment, connection state, readiness and the reason it is not ready, plus *Add MT5 account*, *Add Upstox account*, *New broker*. The three record lists (Brokers, Servers & APIs, Accounts) are tabs of that screen for editing details; clicking a tree row opens the record.
- **Overview** keeps the counters, controls and the 8-step checklist; each step now says in one line how to do it. **Home** shows the set-up progress bar and the next step with an *Open →* link until all steps are done.

## The broker's answer, verbatim — release 0.21.0

Migration `017_broker_login_errors.sql` (two nullable columns; applied by `configure-workstation-database`).

- A refused Upstox login now shows **what the broker said**: the Upstox error code and message (e.g. `UDAPI100069 Check your client_id and client_secret`), or — when Upstox's edge network (Cloudflare) blocks the server's IP before the request reaches the broker — exactly that, with the outbound IP. The refusal is audited (`BROKER_LOGIN_FAILED`) and kept on the account (*Last login refused …*).
- **Check broker API from this server**: a button under the login blocker (and `POST /api/config/connections/{id}/api-check`) sends an unauthenticated request to the broker API and reports *reachable* (the API asked for a login), *blocked* (edge block of this IP) or *no route*. No secret is sent.
- Collector status no longer lists archived accounts or day-old stopped workers.

### Simpler set-up (0.20.1)

- Setup lists **Broker accounts → Brokers → Servers and APIs**, in the order you use them.
- An account that needs today's Upstox login shows **Log in at Upstox now** right in its readiness list.
- Read-only accounts no longer list order-sending blockers or order warnings (static IP, rate limit): "Order sending · not used".
- New collection plans default to **Collect (research)**; choosing an instrument makes it a trading feed. The error for a trading feed without an instrument now says what to do.

## One way in for every account; approvals that cannot deadlock — release 0.20.0

DEC-51. No migration.

- **Setup starts at Accounts.** *Add MT5 account* (login, password, server) and *Add Upstox account* (client ID, app key and secret) each create the broker connection for you; Upstox also creates the broker. *New connection / New account* are marked advanced; Connections is where you adjust clock, limits and addresses afterwards.
- **Approvals (four-eyes).** A LIVE change — a new LIVE server or account, a LIVE address, a LIVE password, re-activating a LIVE record — waits for a second person with the Risk approver role. DEMO never waits. If you are the only Risk approver, you can approve your own request with a written reason; it is marked self-approved in the audit log and alerted as critical. A second approver ends this automatically.
- `POST /api/config/upstox/account-setup`: broker (created if missing), Upstox API connection (reused), app key and secret (saved once, encrypted), account — all or nothing.

## Upstox market data and a clear set-up order — release 0.19.0

DEC-50. No migration.

- **Upstox market data.** The collector now streams Upstox accounts too (`md/upstox_reader.py`, same line protocol as the MT5 reader): best bid/ask, last price and volume once a second for up to 500 instrument keys, history bars from Upstox's candle API, journal → PostgreSQL → Parquet as for MT5. It needs today's broker login (valid until 03:30 IST). A plan's broker symbol is the Upstox instrument key, e.g. `NSE_INDEX|Nifty 50` or `NSE_EQ|INE002A01018`.
- **Set-up order** on Setup › Account readiness: 1 broker → 2 account (MT5: Add MT5 account; Upstox: connection with API key + secret, then account) → 3 credentials → 4 first login → 5 plan → 6 instruments → 7 strategy → 8 alerts.
- **Approvals explained.** A LIVE change (new LIVE server, LIVE address, LIVE password) needs a second person with the Risk approver role. When you are the only user the checklist says so: add a second user or turn four-eyes off. Demo servers need no approval.

## Audit fixes — release 0.18.0

DEC-49; gap register section E lists every finding and its status. No migration.

- CI fixed: backend tests run with pytest (they had been failing in CI since 10 Nov); npm audit and pip-audit added.
- Stored paper runs and assessments need a session; demo routes need sign-in.
- Home and the top bar warn when order sending is ON but no order adapter exists ("Orders ON · … · no adapter").
- `/api/workstation` reports adapters truthfully (MT5 data yes, orders none).
- Account readiness counters are one dense strip.

### MT5 login codes (0.17.1)

Login refusals are explained by number (1001 wrong login/password, 1012 IP not allowed, 1020 unknown account, 1026 password must change, 1027/1028 OTP on, …). A code above 1030 means the server uses a login step newer than the installed MT5 SDK (the SDK names codes up to 1030): update mt5api.dll, or use a broker's own demo server, which is less strict than MetaQuotes-Demo.

## Safe login and detail windows — release 0.17.0

DEC-48. No migration. Rebuild the reader (`bash scripts/setup-broker-accounts.sh`) and redeploy.

- **One login at a time.** A refused login stops at the first address (no more 12 addresses × 9 builds); refusals are retried every 5 minutes. The error now shows the refusal code (e.g. `ServerException: 1035 [Code=1035]`) and the MT5 build that got it.
- The collector no longer reports "TimeoutExpired … --stream" after a failed login (the reader exits at once).
- **Backups** use the newest installed `pg_dump` (fixes "server version mismatch" with PostgreSQL 18).
- **New MT5 connection / Add MT5 account:** the environment follows the server name (MetaQuotes-Demo → Demo).
- **Detail windows** float: drag the title bar to move, any edge or corner to resize, double-click the title to maximise, ⇥ to dock right. Labels sit beside fields, MT5-style. Readiness no longer repeats market-data blockers under order sending.

## Trading flow — release 0.16.0

DEC-47. No migration.

- The rail now follows a trade: **1 Market data · 2 Intelligence · 3 Signals · 4 Strategies · 5 Risk · 6 Portfolio · 7 Execution · 8 Post-trade** (number under each icon). Each opens a screen with the stage's components, what each does, build state, and *open →* to what already works. *Build plan* shows all components by build step; the side bar's *Trading flow* shows progress per stage.
- Account readiness: market data and order sending listed separately; MT5 market data is no longer reported as "adapter not implemented"; the clock measured at login counts; a LIVE connection pointing at a demo server is a warning; a failed connection shows the reason.
- Detail panels (accounts, connections, plans) dock on the right with a draggable left edge, instead of a floating window.
- Bold text reduced to labels that matter; field labels are muted.

## Dense workstation — release 0.15.0

DEC-46. No migration.

- Laid out like VS Code and MT5: **activity bar** (icons, badges) · **side bar** with live content (Ctrl B) · main area · **Toolbox** with Problems, Events, Journal, Activity (Ctrl J) · status bar.
- **Market:** Market Watch (bid, ask, spread; green/red on change; old prices dimmed), Feeds with state and lag, and the market-data sections.
- **Setup:** every section with its count and the order-sending state. **System:** every database table with row counts; click one to open it.
- **Overview** (Home): problems, order sending, feeds and Market Watch on one screen.
- 12px type, 22px rows, no cards. Ctrl K still goes anywhere.

### MT5 "OLD_VERSION" (broker refuses the connector's MT5 version)

The reader retries a refused login automatically with current MT5 client builds (6241 down to 5370) when the installed SDK has a build setting. To pin a build, add `COREI_MT5_CLIENT_BUILD=<build>` (the build the broker's own MT5 terminal shows under Help › About) to `/etc/corei-os-mini/workstation.env` and redeploy. `bash scripts/describe-mt5-api.sh` lists the SDK's build settings under `client_build_settings`; if that list is empty, the SDK itself must be updated.

## Test setup values and MT5 through the official terminal — release 0.31.0

- `scripts/apply-test-setup.sh` writes the correct provider details (VT Markets, MetaQuotes, Upstox), account names, six instruments
  (XAUUSD, EURUSD, GBPUSD, USDJPY, NIFTY50, NIFTYBANK), groups (SRC_FX_METALS, SRC_INDIA, TEST_DEMO), symbol mappings and collection
  plans. Dry run by default; `--apply` writes through COREI's rules (audit log, approvals for LIVE changes). Running it again changes nothing.
- `scripts/setup-mt5-terminal.sh <instance> <port> [installer-url]` installs the official MetaTrader 5 terminal under Wine with the COREI
  terminal bridge as a systemd service on 127.0.0.1. Use it when login fails with "MT5 SDK too old for this server".
- `scripts/apply-test-setup.sh --bridge <ACCOUNT_CODE> --port <port> --state <folder> [--apply]` switches that account's server to the
  terminal: method Terminal bridge, pinned certificate fingerprint, token saved write-only (never printed).
- 0.31.1: a failed source retries within 30 seconds when its account, connection method, bridge or saved login changes
  (instead of waiting out the 5-minute / 1-hour back-off), and the "MT5 SDK too old" message names the terminal route.
- 0.31.2: `deploy-workstation.sh` applies database updates (migrations) before deploying. Before this, new tables were only
  created by `configure-workstation-database.sh`, so the Market data source list failed with "Database schema out of date".

## Provider › server / session › account — release 0.32.0

- Setup › Connectivity opens grouped by **provider** (the counterparty), then its **servers / sessions** (MT5 server, FIX session,
  API endpoint), then the **accounts** on each. Every technology has the same three levels; Technology and Provider type groupings remain.
- The overview shows the six set-up steps (provider, server / session, account, login, test, collect) with counts, and a next step per provider.
- **+ Provider** adds a provider on its own (name, type, legal entity, regulator, licence). A provider's panel adds its MT5 server
  (picked from the broker's published list, or typed), FIX session or API; a server's panel adds its accounts; an account's panel
  shows steps 4–6 (login saved, login tested, collecting) and opens Market data to pick symbols.
- `apply-test-setup.sh` also names servers in the standard form ("VT Markets · MT5 live · VTMarkets-Live 2"), fixes broker names
  (Exness, IC Markets) and archives "Meta Trader" (a technology, not a provider).
- 0.32.1: the tree is **Provider › Account › Server** (DEC-62): each account shows the server it uses beneath it; an MT5 account
  picks its server from the broker's list when added and can change it; only servers in use are kept. A Broker login result is
  shown once and removed from the address, so a saved link no longer repeats an old message.

## Connectivity: one flow, no duplicates — release 0.33.0

- Tabs follow the flow: **Tree · Providers · Accounts · Servers** (provider › account › server).
- **One form per record.** A provider is entered only in the provider form; the tree's **+ Provider** and every "new provider"
  link open it, and after saving it offers step 2 (Add MT5 account / Add Upstox account / FIX session or other API).
  Accounts use the Add MT5 / Add Upstox / New account forms wherever they are opened from. The tree's technology view adds an
  MT5 account or a FIX session / API for an existing provider — it no longer creates providers.
- Names are the same everywhere: Providers, Accounts, Servers. Account records read provider › account › server.
- A broker-login result banner can be dismissed and is not kept in the address.

## Add account: provider › technology › account › server › login — release 0.34.0

- **One Add account form** (Accounts tab, tree overview, provider, technology and server panels): choose the provider,
  then the **technology** — MetaTrader 5, MetaTrader 4, cTrader, FIX 4.4 / 4.2 or a broker / exchange API (Upstox,
  Angel One SmartAPI, Zerodha Kite Connect, Dhan, Fyers, other) — then the account, its server and its login.
  MT5 and Upstox continue in their dedicated one-step forms.
- **MT4 and cTrader** accounts and servers can be recorded now (database update 022); they collect once their connectors are built.
- **Providers** record the technologies they offer; Add account lists those first. `apply-test-setup.sh` sets them for your providers.
- Overview checklist follows the same steps. Full audit: `docs/SETUP_AUDIT_2026-11-15.md`.
- 0.34.1: **Deleting an account** says exactly what keeps it (logins in the audit trail, deployments, collected prices or
  bars, broker symbol specs) and offers **Archive** in the same step. An account with nothing collected is deleted with
  its collection plans. Archiving an account also archives its collection plans, so collection stops.
- 0.34.2: **Delete permanently…** on every provider, server and account (beside Delete and Archive): removes it with
  everything attached — collected prices and bars, plans, catalogue, broker reads, deployments, logins; a server's or
  provider's accounts too. Type the code and a reason; the audit log keeps one entry listing what was removed.

## Database editor — release 0.35.0

Setup › Administration › **Database** (Administrator role): every table of COREI's database.
- **Rows:** search all columns, sort, page (50–1000), double-click a cell to edit (∅ sets it empty), **+ Row**, delete a row.
- **Columns:** **+ Column** (name, type, empty allowed, default); **⋯** on a column to rename, change type, allow/require
  empty, set or remove default, or **Drop column…** (type `table.column`).
- **Tables:** **+ Table**, **Drop table…** (type the name).
- Every change: a reason (audit log) and your password re-entered. Secrets stay hidden and are changed from their own screens.
- Structure changes made here are not part of COREI's migration files.
- 0.35.1: Database › delete a row: rows in other tables that point at it are listed first (e.g. a provider's servers,
  their accounts and collection plans) and deleted together with it after one confirmation; references that are set
  empty on delete are listed as such. One audit-log entry records the row and what went with it.

## MT5 in Docker — release 0.36.0

`sudo bash scripts/use-mt5-docker.sh <ACCOUNT_CODE> [broker's MT5 installer link]` — the official MT5 terminal in a
container (Debian + WineHQ, so the host's packages never conflict) with COREI's TLS bridge; one container per account,
127.0.0.1 only, restarts on boot; then the account is switched to it. View the terminal in a browser through an SSH
tunnel (printed at the end). Replaces the host-Wine route (`use-mt5-terminal.sh`, kept as a fallback).

## MT5 terminal push (COREI EA), an optional method — release 0.37.0

Kept as one option beside Direct SDK and Terminal bridge / Docker (the main route).

Set an MT5 server's method to **Terminal push (COREI EA in MT5)**, create its push token, and attach the read-only
**COREI_Push** EA in any MT5 terminal logged in to the account (your PC, a Windows VPS or the Docker container). Prices,
bars and the broker's symbol list arrive over HTTPS; no SDK version, Wine or password in COREI is involved. Database
update 023. See `docs/MT5_TERMINAL_PUSH.md`. `use-mt5-docker.sh` now retries registry refusals.

## MT5 routes with automatic failover — release 0.38.0

Every MT5 account can have several **routes** (Setup › Connectivity › the account › *Connection routes · failover*),
tried in order: Terminal bridge on a **Windows VPS**, Terminal bridge on a **Windows PC** (SSH tunnel out to COREI, no
open port), Terminal bridge in **Docker** on the COREI server, **EA push** (optional) and the **Direct SDK**. When the
route in use fails twice, the collector moves to the next one (event *feed switch*, alert *route fallback*) and returns
to the first after 5 minutes at the next reconnect. Each route has its own encrypted, write-only token, its last
success and last error, and a **Test** button (one login through that route). An account without routes keeps its
server's method.

Windows set-up (VPS or PC) in one run, as Administrator, with the two files downloaded from the routes panel:

    powershell -ExecutionPolicy Bypass -File setup-corei-bridge.ps1 -Name <account> -Mode Direct -CoreiIp <COREI server IP>
    powershell -ExecutionPolicy Bypass -File setup-corei-bridge.ps1 -Name <account> -Mode Tunnel -SshTarget <user>@<COREI server> -TunnelPort 9450

It installs Python and MetaQuotes' MetaTrader5 package, sets up the bridge, its certificate and token, a scheduled task
that keeps it running, and either a firewall rule open to COREI's IP only or the SSH tunnel; it prints the host, port,
fingerprint and token for *Add route*. See docs/MT5_TERMINAL_BRIDGE.md. Migration 024.

- 0.38.1: one Windows bridge serves every account (portable terminal copy + worker per login, made on demand);
  the bridge stamps receipts in milliseconds and polls every 100 ms; the collector measures the reader machine's
  clock skew from heartbeats, corrects latency by it and warns above 0.5 s (migration 026; the Windows set-up also
  turns on time sync); the bridge task is restarted every 5 minutes if it stops.

## Stage 2 · Market intelligence — release 0.39.0

Intelligence (second icon in the left bar) now opens on the **Screener**: every collected instrument ranked for
attention per timeframe (M1…D1) with its regime (trend up/down, range, transition), volatility state, ADX, RSI, ATR%,
EMA trend, 20-bar return, range position, spread against ATR and feed quality; filters, sorting, hover for the
reasons. **Instrument** shows one instrument across every timeframe with the reasons, a chart (close, EMA 20/50,
Bollinger bands, regime strip, RSI) and a CSV of its feature history. **Sessions** shows which centres are open,
overlaps, rollover, the weekend close and the next changes. The feature store recomputes every minute from closed
bars only (migration 027; health board: *Market intelligence*). Higher timeframes need history: backfill the source.

## Market intelligence workspace — release 0.40.0

Intelligence now has: Dashboard (coverage, regime mix, needs history, top attention, biggest moves, high-impact events
next 24 h, rules fired, engine controls incl. history retention and event window) · Screener (filters by broker
account, asset class and watchlist; timeframe alignment and event-risk columns) · Instrument · **Sources** (the same
instrument from every broker account: deviation, spread, latency, quality, best source) · **Correlation** · **Strength**
(FX) · **Watchlists** · **Rules & alerts** (conditions on any feature, checked on each closed bar; alert event
INTEL_RULE for Setup › Alerts channels) · **Calendar** (add or import CSV) · Sessions · Select & compare. Feature
version 2 adds MACD, stochastic, Donchian and z-score. Migration 029. The COREI EA 1.01 (terminal push) works as an
account route with its own token; quotes are stamped with COREI's receipt time.

## Market intelligence v3 and the terminal pool — release 0.42.0

**Market intelligence** is grouped as Overview (Dashboard, Screener, Instrument) · Analyse (Sources, Correlation,
Currency strength, Sessions, Select & compare) · Monitor (Watchlists, Rules & alerts, Calendar) · Configure
(Indicators, Engine settings, Stage map), in the top line and in the side bar. Feature version 3 adds **volume**: tick
and real volume, relative volume, VWAP 20 and deviation from it, OBV, and the bar's average spread (the chart has a
volume panel). **Indicators** keeps the standard set fixed and adds custom indicators per strategy (EMA 9/21,
Bollinger 20/2.5, MACD 8/21/5…) per timeframe; rules can compare a feature with another (ema_9 crosses above ema_21)
and start from templates. The **Screener** has a column chooser with presets and CSV export. **Get history** needs no
typed reason and has *Get history for all*. Plans have a *Collect everything* button. Migration 030.

**Many accounts** (DEC-73): market data is collected once per broker server; account reads rotate through the bridge's
terminal pool (`--max-terminals`, default 20, least-recently-used idle terminal closed first, streaming terminals never
closed); dedicated terminals only for trading accounts; more bridge nodes for more capacity.

## Indian markets and bridge nodes — release 0.43.0

**Indian markets** (DEC-74): instruments record exchange, market hours, ISIN, lot, expiry, strike, CE/PE, underlying
and aliases (BANKNIFTY, FINNIFTY, MIDCPNIFTY…). An Upstox source loads its instrument list by itself; *Available*
offers the main indices (NIFTY 50, NIFTY BANK, FIN NIFTY, MIDCAP SELECT, NEXT 50, SENSEX, BANKEX, INDIA VIX) and
each one's nearest future in one click. The plan form searches the source's list ("banknifty", "reliance", "gold")
and creates a missing instrument from it. Plans on *Market hours* follow NSE/BSE (09:15–15:30 IST), MCX and FX;
no stale alarm while a market is closed; INR HOLIDAY events in the calendar close Indian markets. Sessions shows
every exchange. Migration 031.

**Bridge nodes** (DEC-75): Setup › Bridge nodes › *Connect a Windows machine* gives two lines for PowerShell on the
VPS or PC; the machine installs the bridge (0.42.0) and registers itself, COREI checks it, and accounts use it with
one click (or automatically, as the default node). Adding an MT5 account offers the node instead of a fingerprint and
token. Tunnel-mode nodes: run `sudo bash scripts/enable-bridge-tunnels.sh` once on the COREI server. Migration 032.

## Trading core on paper and the system map — release 0.44.0

**How COREI works** (DEC-77): Home shows the eight stages a trade passes through, each with a live figure, and the path
a new user follows. Every stage screen has the same frame: Overview · Work · Monitor · Configure, plus its stage map.
See docs/ARCHITECTURE.md.

**Stages 3–7 run end to end on paper** (DEC-76, migration 033):
- **Strategies**: six algorithms (SMA and EMA cross, RSI and Bollinger reversion, Donchian breakout, rule builder on
  any feature); runtime on every closed bar; backtest with costs and no look-ahead; performance per strategy.
- **Signals**: board with reasons and history, manual signals, decisions, signal quality (hit rate).
- **Risk**: kill switches per scope (automatic on loss limits), limits per scope, decision log, exposure.
- **Portfolio**: accounts (equity, today, drawdown), positions at live prices with Flatten, performance, exposure.
- **Orders**: blotter with every step, paper order ticket, fills with slippage, UNKNOWN resolution.

Deployments now have a mode (Paper, the default, or Broker), an order size and a signal validity. Broker orders stay
refused until the MT5 order adapter is built and Setup › Order sending is on.

## Real execution, post-trade and the advanced controls — release 0.45.0

**Real orders on demo and live accounts** (DEC-78 … DEC-80): MT5 through the COREI terminal bridge (0.43.0: pre-check,
send, cancel, modify, SL/TP, close; net on hedging accounts; a tag is never sent twice) and Upstox through its order
API. The dispatcher records SUBMITTED before anything leaves COREI; no answer = UNKNOWN, looked up at the broker by
tag every few seconds and never re-sent. Execution gains *Waiting* (four-eyes on LIVE), *Unknown*, *Broker
connections* (which account can take real orders and why not) and *Routing*; the ticket takes paper or real, market,
limit or stop, SL/TP, and routing across accounts (best price, lowest cost, first that can, split).

**Post-trade, Stage 8** (DEC-81): dashboard, breaks (apply fill, adopt position, accept, resolve), reconciliation
runs, broker book, execution quality (TCA), trade journal, daily statements, order audit trail, CSV exports.

**Risk** (DEC-82): limits on order value, margin %, leverage, VaR % and price deviation; VaR and expected shortfall per
account; stress scenarios. **Strategies** (DEC-83): optimisation with walk-forward; drift against the backtest.
**Portfolio** (DEC-84): capital partitions and rebalancing, deployment weights, copy trading, attribution, broker
positions. Migration 034.

**To start real orders on a demo account**
1. Update each Windows bridge node to 0.43.0 (below).
2. Setup › the account: trading permission *Trading*, master password saved, a bridge route (or the node).
3. Setup › Instruments › mapping: the account's broker symbol for each instrument.
4. Setup › Order sending: turn DEMO on (Risk approver, password re-entered).
5. Execution › Broker connections: the account shows *MT5 BRIDGE*. Place a small real ticket; watch it in Orders and
   in Post-trade › Broker book.
LIVE stays off until the demo run is checked (docs/DECISIONS.md, "Still required before the first live order").

**Update a bridge node to 0.43.0** (each Windows VPS or PC, PowerShell as Administrator):
```
powershell -ExecutionPolicy Bypass -File C:\corei-setup\install-corei-bridge.ps1 -Corei https://corei-os-mini.coreeye.in -Update -Name vps-nys
```
(use each node's own name, e.g. `my-laptop`). Then Setup › Bridge nodes › *Check*: the node shows version 0.43.0. In each terminal the bridge uses, *Algo Trading*
must be on (Tools › Options › Expert Advisors › Allow algorithmic trading) — the bridge reports
TERMINAL_ALGO_TRADING_OFF otherwise.

**0.45.1 — the stages were hard to use when empty.** With the side bar open, a stage's own sections (Signals,
Decisions, Kill switches, Limits, Optimise, Breaks…) were hidden; the side bar now lists them under the stage's name.
While no strategy runs, every trading stage shows *Start here*: pick a collected instrument, a starter rule set and a
bar, and *Start on paper* creates the strategy, approves it (Risk approver), maps the symbol and activates a paper
deployment in one step (POST /api/trading/quickstart; never real orders).

**0.45.2 — end-to-end audit** (DEC-85, migration 035, bridge 0.43.1): unexecuted strategy decisions are retried on the
next bar (Realign); a new decision waits for the deployment's working or unknown order; deployments get stop-loss and
take-profit in ATRs (sent to MT5, watched by COREI on paper and Upstox); stops and targets the broker executes are
booked as fills of the right strategy; orders on a stale price are refused. Update bridge nodes to 0.43.1 (same
command as above) so each deal reports why it happened.

**0.45.3 — live audit fixes** (DEC-86, bridge 0.43.2): two bridges on one VPS port are resolved by the update script;
certificate mismatches name the bridge that answered; orders and broker reads refuse a bridge older than 0.43 instead of
waiting for it; paper-only accounts reconcile against their own fills; bridge nodes are checked every 5 minutes.

**0.45.4 — everything on one page** (DEC-87): Market data, Market intelligence and every trading stage open on
*Workspace (all)*: every panel — dashboards, work screens, monitors, settings — on one page, each closable, with an index.

## Every calculation on the workspace; Stage 3 owns the signal maths — release 0.46.0

The flow stays **1 Market data → 2 Features → 3 Signals → 4 Strategies → 5 Risk → 6 Portfolio → 7 Execution → 8 Post-trade**,
and every formula is on the workspace with live numbers (DEC-88, migration 036).

- **Stage 3 · Signal library.** Each signal is a formula that turns features into a score from −100 (strong short) to +100
  (strong long) for every collected instrument and closed bar: EMA/SMA trend, MACD momentum, RSI reversion and momentum,
  Bollinger and z-score reversion, Donchian breakout, momentum t-stat, stochastic, ADX direction, VWAP reversion. Twelve
  built-ins, tunable and pausable; the desk adds its own (Risk approver, reason, versioned, audited).
  - *Live signal board*: instrument × signal; click a score for its equation filled in with that bar's numbers.
  - *Explain an instrument*: every signal's formula, inputs, arithmetic, side and last 60 scores.
  - *Signal research*: IC (Spearman), its t-stat, hit rate, quintile returns, turnover, coverage, cost; PREDICTIVE / WEAK / NO_EDGE.
- **Stage 4 · Signal blend.** A strategy algorithm that combines library signals: combined = Σ wᵢ·scoreᵢ ÷ Σ|wᵢ|, LONG at
  ≥ entry, SHORT at ≤ −entry, flat inside ±exit. The recipe editor (Backtest and Setup › Strategies) works the combined
  score out on the last bar before saving. Every algorithm now lists its rule and strength equations.
- **Calculations & formulas** panel on Market data, Market intelligence (with each feature's value on a chosen instrument)
  and Stages 3–8: bars and the quality score; every feature and the regime; signals, gates and decision; algorithms,
  sizing, protective exits, backtest metrics, walk-forward and drift; limits, margin, VaR/ES and stress; average cost,
  P&L, equity, allocation and copy size; client order ID, paper fills, slippage, routing; reconciliation and TCA. One
  registry (`formulas.py`) beside the code; a test checks every algorithm and signal is listed.
- API: `/api/trading/formulas`, `/api/trading/signal-library/{catalogue,definitions,board,explain,research,blend-preview}`.
  Scores are computed by the intelligence engine after each run (`sig_live`); windows end at the newest stored bar.

## Signals → strategies → execution, end to end — release 0.47.0

The order stays **3 Signals → 4 Strategies**, on purpose: Stage 3 produces forecasts (scores) for every instrument;
Stage 4 combines them into decisions. What a running strategy raises — its signals, their checks and its decisions —
now lives in Stage 4, where it is made (DEC-89, migration 037).

- **Stage 3 · Signals** — Dashboard (long / short / flat now, strongest scores, the composite of each instrument,
  latest flips); Live board with a composite column, filters by family and side, sorting and a colour legend; one
  instrument in focus for every panel (a click on the board focuses it); *Explain* with the composite and a chart of any
  signal against price; *Across timeframes* (every signal × M5…D1 with the composite of each); *Rank every signal*
  (all researched on the same bars, ranked by |IC t|); *Signal correlation* (redundant pairs |r| ≥ 0.8); *Signal flips*.
- **Stage 4 · Strategies** — Dashboard: one card per strategy with position, open and closed P&L (per currency, never
  added across currencies), target, live trades, win rate, profit factor, drift, last signal and last decision; pause,
  resume and the execution handoff from the card. Strategy signals, Decisions, Signal quality and Checks and decision
  moved here. *Compare backtests*: up to four side by side with equity curves.
- **Stage 7 · Execution** — *From strategies*: every decision with its chain signal → decision → risk → order → fill,
  outcome and reasons. *Decision queue*: decisions waiting for a trader — execute (full or smaller, market or limit) or
  decline, one or all, before the window closes. *Execution policy* per strategy:
  - handoff **Automatic** (default, as before) · **Trader approves** · **Signal only**;
  - order **market** or **limit** (marketable = price cap at ask/bid ± n ticks, passive = waits at bid/ask ∓ n ticks);
  - time in force GTC / DAY / IOC; approval window.
  A waiting decision is executed as target − position *now*, through the normal risk check, with the same client order
  ID as an automatic order — it can never be sent twice; a newer decision replaces an older one; an expired, declined or
  waiting decision is never retried by realign.
- API: `/api/trading/strategies/monitor`, `/execution/{flow,policies}`, `/execution/decisions/{id}/{execute,decline}`,
  `/signal-library/{overview,instrument,events,correlation,research-all}`.

## Workspaces as in VS Code — release 0.48.0

- **Each screen is its own workspace tab.** Market data, Market intelligence, Signals, Strategies, Risk, Portfolio,
  Execution, Post-trade, Setup, Monitor and System open as tabs (rail, links, Ctrl+K); an open one is focused, not
  opened twice; each keeps its state. A workspace in a background tab stops polling and refreshes when shown.
- **Actions top right of every workspace:** *Split* (another group beside it, up to four), *New tab* (same browser),
  *New window* (a separate browser window, e.g. a second monitor), and maximise when several groups are open.
- **Sub-workspaces inside a stage:** 1–4 panes side by side (by purpose: dashboards, work, monitors, settings), each
  scrolling on its own and resizable; drag a panel title to another pane or use ◀ ▶ (move) and ◫ (split right); add a
  panel to a pane or close a pane (its panels return to pane 1). Remembered per screen and viewer. Every panel opens
  alone (⤢), in a new tab (↗) or a new window (⧉) — `?view=…&section=…&detached=1`.
- **One header on every stage:** the trading flow 1 → 8 as steps (click to go, the current stage marked), then what is
  shown, what the stage answers and what it hands on. Workspace tabs carry the stage number and icon.

## Main workspaces and sub-workspaces — release 0.49.0

Two levels, as in VS Code (DEC-91):

1. **Main workspace** — one tab per domain: Market data, Market intelligence, Signals, Strategies, Risk, Portfolio,
   Execution, Post-trade, Setup, Infrastructure (Split, New tab, New window top right).
2. **Sub-workspace** — inside each domain, its own items as sub-tabs: e.g. Market data › Dashboard, Table, Collection
   plans, Quality, Events, Backfill, Controls, Archive, Calculations & formulas; Market intelligence › Dashboard,
   Screener, Instrument, Sources, Correlation …; Setup › Overview, Connectivity, Bridge nodes, Instruments, Strategies,
   Deployments, Order sending … The side bar, links and Ctrl+K open an item as a sub-tab (focused if open); **+ Open…**
   adds any item; × (or middle-click) closes; **Split** puts the item in a pane on the right (up to four, resizable);
   drag a sub-tab to another pane, ◀ ▶ move it; ↗ ⧉ open that one item in a new browser tab or window. A hidden sub-tab
   keeps its state, stops polling and reloads when shown. *All panels* stays as the first sub-tab (every item on one
   page). The 0.48 "panes by purpose" inside All panels are replaced by this.

### 0.49.1 — navigation audit: nothing repeated, one order everywhere

- Main workspaces (stages, Setup, System) live only in the rail and the main tabs; they are no longer repeated inside
  a workspace (the 1 → 8 row and the side bar's *Trading flow* and *Problems* are gone from domain screens; Home keeps
  them). A small breadcrumb (*Stage 3 · Signal intelligence ›*) says where you are.
- A domain's side bar lists only its own items, with the same names and order as its sub-tabs (Market data, System
  and every stage now publish one list; Market data's order fixed). *All panels* no longer repeats the items as chips.
- Market data's saved board views are labelled *Views* (not another "workspace"). The Execution tab is named
  *Execution*. Sub-tab layouts start fresh once (new storage key) so every domain opens in the aligned order.
- Tests check, for Market data and every stage, that the side bar equals the sub-tabs.


## Calculation Graphs, phase 1: the engine — release 0.50.0

Every calculation becomes a graph of typed nodes you can see with live numbers on every step (DEC-93; the n8n pattern
in our own code). This release is the foundation; the screens adopt it from 0.51. Details:
[docs/CALCULATION_GRAPHS.md](docs/CALCULATION_GRAPHS.md); what moves when: [docs/CALCULATION_INVENTORY.md](docs/CALCULATION_INVENTORY.md).

- **Node catalogue** (69 nodes): sources (bars, any feature, any library signal, quote, position, account, instrument,
  setting, constants), the platform indicators (wrapped, not re-implemented), series (shift, rolling, returns,
  cumulative, normalise by ATR), maths and the **Expression** node, logic (compare, crosses, hysteresis…), combine
  (weighted sum, vote, consensus, rank), convert (last value, **To decimal**) and one output per purpose (feature,
  signal, rule, sizing, protective, risk check, allocation, measure) that enforces its contract.
- **Expression language**: one line such as `100 * (fast - slow) / (scale * atr)`; our own parser, no eval, no code,
  `x[t-k]` for the past and no way to write the future.
- **Executor**: complete checks before running (types name both ports; money only through To decimal; cycles; one
  output), deterministic order, per-node errors that stop only what depends on them, a trace of every node on any bar
  (inputs, outputs, the formula with the numbers, time). 600 bars ≈ 1.3 ms per signal graph.
- **Proof**: the built-in graph *EMA trend* equals the signal library's EMA trend to 1e-9 on every bar.
- **Storage** (migration 038): graphs, versions (approved versions frozen by the database), bindings, and an
  insert-only record of every run.
- **API**: `GET /api/calc/nodes`, `/graphs`, `/graphs/{code}`, `/graphs/{code}/versions/{n}`, `POST /api/calc/validate`,
  `POST /api/calc/preview`, `GET /api/calc/runs`.
- Nothing changes on the trading path: no stage runs a graph yet, the code stays the reference until parity is
  proven per formula, and live trading stays off by default.

## Calculation graphs on screen, execution algorithms, amend — release 0.51.0

Every calculation can now be opened as nodes and wires (n8n-style), and execution works orders over time.

- **Graph canvas** (Signals › Signal graphs, and *Calculation graphs* in Market intelligence, Strategies, Risk,
  Portfolio, Execution and Post-trade): the library on the left, the canvas with each node's value on the bar you
  choose (bar slider), the selected node's formula with its numbers on the right, the result and a sparkline below;
  *Plain* lists the same steps in the order they run; *Wide canvas* hides the side panels. Edit mode: node palette,
  drag wires (impossible wires refused at once, named), parameters (fixed or `{{graph parameter}}`), check, save
  draft, propose; a second person approves (four-eyes); *Put to use* scores a signal definition with the approved
  graph; *Check parity* compares a graph with the code it draws on stored bars.
- **Built-in graphs**, each equal to its code on every bar (tests): the 12 library signals (1e-9), 6 features
  (ATR %, Bollinger width %, 1- and 20-bar returns, distance from VWAP, relative volume) and 2 strategy rules (EMA and
  SMA crossover — the same target and strength on every bar).
- **Strategies can run a graph**: Setup › Strategies › algorithm *Calculation graph* pins an approved strategy-rule
  graph version inside the strategy version; the runtime and the backtest run it (backtest equal to the code's to
  the cent).
- **Graph-bound signals**: a signal definition bound to an approved graph is scored by it on the board, flips,
  explain, correlation and research; the binding is audited and can be stopped at any time.
- **Execution algorithms** (Execution › Algorithms, and *Worked by an algorithm* when executing a decision from the
  queue): TWAP, VWAP (weights from the usual volume at that time of day), POV (a share of the market volume) and
  iceberg (one visible clip), with a price cap and market or limit children. Each child is an ordinary order with its
  own risk check and client order ID; a refused or unknown child pauses the parent until a person resumes it.
  Progress, average price against arrival in bp, plan, children and events are on screen; pause, resume, cancel.
- **Amend a working order** (order detail › Amend): limit / stop price, stop-loss / take-profit, a smaller paper
  quantity; new prices pass the price-deviation and kill-switch checks again; a real MT5 order is amended at the
  broker first (by its ticket) and recorded only when the broker accepts.
- The Execution workspace is named *Execution* in its tab and side bar (it said "Orders").
- Migration 039 (execution algorithms). Live trading stays off by default; order sending per environment unchanged.

## Calculation flow: every calculation as nodes and wires — release 0.52.0

A new main workspace, **Calculation flow** (rail, after Post-trade), where every calculation, formula and equation of
the platform is present n8n-style (DEC-96):

- **Platform flow** — the whole platform as one workflow: Market data → Intelligence → Signals → Strategies → Risk →
  Portfolio → Execution → Post-trade. Each stage is a box holding every calculation it makes (101 in all), each card
  with its last steps written out; lines show where one calculation feeds another. Choose an instrument and the
  features and signal scores show their live value on its newest bar. Search lights up matches; double-click a stage
  to fold it (Expand / Collapse all); click a card for its steps, where it comes from and where it goes, and a small
  working graph of it.
- **Every formula** — each of the 101 calculations as its own graph: input boxes you type into → a node per step
  showing its expression, the expression with your numbers put in and its value → the result. The server works the
  steps out in the platform's own expression language. *Calculations & formulas* in every stage links each row here
  ("as a graph ↗").
- **Bar-by-bar graphs** — the executable graphs of 0.51 (every node's value on any stored bar; copy, change, approve,
  put to use).

Steadier screens:
- Graph canvases draw only while on screen, fit once per graph (not on every update), never animate wires, keep a
  fixed height and a one-line status, so nothing around them moves; nodes have a fixed width.
- *All panels* shows a light summary of the graphs with *Open* instead of a full canvas in a panel.
- "Nothing is running yet" shows only on *All panels*, not above every item.
- A link that opens a domain at an item now opens that item even while the domain is still loading.

## Financial integrity and the pre-LIVE policy (Wave 0) — release 0.53.0

Closes the safety and money defects found by the 23 Nov 2026 audits (Claude and ChatGPT, commit 673ccf3). Migration
`044_wave0_financial_integrity.sql` (additive). Tests: `backend/tests/test_wave0.py` (13 cases, PostgreSQL).

- **Paper never moves a broker decision.** Equity, daily P&L, exposure, margin, VaR and drawdown are taken from the
  order's own book (PAPER or BROKER). Account-wide figures are labelled *ALL* and used only for reporting. Equity
  snapshots are kept per book; older snapshots stay as the account-wide series. A paper loss stops paper
  deployments, never the account's real trading.
- **Unknown is not zero.** A holding with no live price has unknown unrealised P&L (it was counted as 0); one with no
  FX conversion is listed instead of silently dropped from exposure. A risk-increasing order under a money or loss
  limit is refused with `VALUATION_INCOMPLETE` and the holdings named; reducing orders still pass.
- **Sizing states its capital.** A BROKER deployment sizes from the broker's equity only when it is present, positive
  and recent; zero, missing, old or margin-only (Upstox) replies refuse with `CAPITAL_BASIS_UNAVAILABLE` instead of
  falling back to the paper record. Fixed-quantity and fixed-notional sizing need no equity.
- **Working orders are reserved.** Position, instrument and gross-exposure limits count every working order's unfilled
  remainder in the same book (UNKNOWN and cancel-pending included), and the check per account book is serialised, so
  two orders that each fit cannot together pass a limit.
- **Reconciliation keeps its evidence.** Broker deals are read from the account's last verified point (not a fixed 3
  days; MT5 up to 90 days) and every stored deal of a COREI order is checked for its fill, whatever its age. A break
  closes only when a later run examined the same item and found it matched — never because it fell outside a window
  or the broker could not be read. Fills with no position row are now a break. Each run records what it compared
  (positions, deals, working orders, cash) and any history gap; the empty-breaks text no longer claims full agreement.
- **Manual fills cannot exceed the order.** Resolving an UNKNOWN order is capped at what remains; a broker fill above
  the order quantity is booked (the exposure is real) and kept as an open break with its evidence.
- **One protected database editor policy.** Setup › Database and System › Tables share `db_policy.py`: the trading
  ledger (orders, fills, positions, risk, reconciliation, strategy runtime), logs and secrets are never written, and
  their structure never changed, from an editor; configuration tables follow `infra_edit_controlled_tables` in both;
  a delete that would cascade into protected records is refused.
- **Order rate per connection.** Every send, amend and cancel takes a slot of the connection's
  `max_orders_per_second` (strict rolling second). Over the rate an order waits APPROVED for the next dispatch;
  cancels wait for a slot. Upstox connections can carry an exchange-registered Algo Name, sent as `X-Algo-Name` on
  place, modify and cancel (COREI's tag stays for correlation).
- **Pre-LIVE policy.** LIVE order sending cannot be turned on until: every LIVE trading account is covered by
  `MAX_ORDER_NOTIONAL`, `MAX_DAILY_LOSS` and `MAX_POSITION_QTY` or `MAX_GROSS_EXPOSURE`; the four-eyes rule holds every
  LIVE order (`ALL`); two active risk approvers exist; an alert channel is active. The last three can be waived only
  by a recorded exception of 1–30 days (Setup API `/api/config/live-readiness`). If LIVE is already on, the
  dispatcher refuses every new LIVE order that adds risk while the policy is unmet (REJECTED, reason LIVE_POLICY_UNMET;
  reducing orders still go), and
  Home shows each unmet rule as CRITICAL. **Existing installs: LIVE stays as it is switched; new LIVE exposure waits
  for the policy (they are refused until it is met).**
- **Release identity.** One `VERSION` file; `/api/workstation` and the status bar show release, commit and schema.

Update: `git pull --ff-only origin main`, `bash scripts/configure-workstation-database.sh`, `bash scripts/deploy-workstation.sh`.

## Optional market research API

Corei OS Mini can supply collected bars to Core AI or other authorized research
clients. In `/etc/corei-os-mini/workstation.env`, set a dedicated private
`COREI_RESEARCH_API_TOKEN` of at least 32 characters and list allowed account
UUIDs in `COREI_RESEARCH_API_ACCOUNTS` (comma separated). Restart
`corei-os-mini-workstation` after editing the service environment.

Use `Authorization: Bearer <token>` for GET
`/api/research/v1/capabilities` and GET `/api/research/v1/bars`. Bars accepts
`account_id`, `symbol`, `timeframe`, and `limit` (1–200). Symbols are broker
symbols already collected by the authorized account. The credential authorizes
read-only requests to these two endpoints. Without an explicit token and account
scope, the optional interface is unavailable.

<<<<<<< HEAD
## Market data means what it says (Stage 1) — release 0.54.0

Closes the live defects of the 23 Nov 2026 Market Data audit. Migration `045_market_data_integrity.sql` (additive).
Tests: `backend/tests/test_market_data_stage1.py`.

- **Plan health in words.** Every active plan shows one state: live, market closed, waiting, delayed, unreliable,
  error, wrong symbol, source offline or paused — with the reason and next step. "Collecting" no longer hides a plan
  that receives nothing; the header and the dashboard count plan problems, and Home lists them.
- **Wrong symbols are caught.** Every minute each plan's symbol is checked against its source's symbol list. A symbol
  the list lacks (e.g. `Nifty 50` instead of `NSE_INDEX|Nifty 50`) is marked *wrong symbol* with suggestions, left out
  of the subscription and of automatic backfill, and raised once as a CRITICAL event. On Upstox one rejected key no
  longer stops the prices of the others.
- **Bars match the exchange.** New bars use the exchange time of the price (when within 2 minutes of receipt), align
  NSE/BSE/MCX bars to the session open (NSE H1 = 09:15, 10:15 … 15:15) and D1 to the IST day, use the last traded
  price for exchange instruments and the increase of Upstox's day volume. FX/CFD bars are unchanged (bid, UTC). Each
  bar records how it was made. Older bars are kept as they are.
- **Quality reaches trading.** The risk check refuses a risk-increasing order when its feed is wrong-symbol, error,
  offline or delayed, or scored below 40 in the last minute (Setup › Policies `md_block_states`,
  `md_min_quality_score`). Reducing orders still pass.
- **Calmer failures.** Automatic backfill waits after 3 failures in a row (15 min, doubling to 6 h); every event names
  its symbol; latency is measured only on new trades (Upstox) and a negative value shows as *clock?*; an old quality
  score is no longer shown as today's.
- **Replay check.** `python -m corei_os_mini.md.replay --account CODE --day YYYY-MM-DD` rebuilds a day's bars from the
  journal and lists any difference with the database (read-only).

Update: `git pull --ff-only origin main`, `bash scripts/configure-workstation-database.sh`, `bash scripts/deploy-workstation.sh`.
=======
## Upstox V3 order execution

1. In Setup, use an Upstox REST/OAuth connection with base
   `https://api.upstox.com`. Configure the exact registered OAuth callback
   and sign in to the broker for the account. Verify the account and map each
   trading instrument to its Upstox instrument key.
2. Verify the server's egress IP and app registration with Upstox. When your
   approved algo app requires it, put the registered **Algo Name** in the
   connection's **Registered algo name**. Corei sends it as `X-Algo-Name`.
3. In **Execution → Order ticket**, test in **Paper**. Live broker orders
   require a LIVE Upstox connection, trading permission, platform risk and
   execution controls, and any configured release approval.
4. Upstox live place, cancel, and price-only amendments use V3 on the official
   HFT hostname. Read and reconciliation calls use the account API domain.
   Market and stop-market orders send automatic market protection; the broker
   can still reject them. Upstox recognizes DAY and IOC; the Corei default GTC
   is sent as DAY. This integration uses one broker ticket per Corei order, so
   provider auto-slicing is disabled. Corei Paper remains the local simulation
   route. A DEMO connection does not dispatch to the live Upstox host.
5. Review the broker order ID, fills, and audit timeline in **Execution**.
   For a timeout or uncertain broker response, use **Look up at the broker**
   and review the broker's order before any further action.

- [Upstox Place Order V3](https://upstox.com/developer/api-documentation/v3/place-order/)
- [Upstox Modify Order V3](https://upstox.com/developer/api-documentation/v3/modify-order/)
- [Upstox Cancel Order V3](https://upstox.com/developer/api-documentation/v3/cancel-order/)
>>>>>>> origin/main

## Market intelligence describes the market it comes from (Stage 2) — release 0.55.0

Closes the defects of the 23 Nov 2026 Market Intelligence audit. Migration `046_market_intelligence_integrity.sql`
(additive). Tests: `backend/tests/test_intelligence_stage2.py`.

- **No more week-old daily trends.** When the broker's own bars stop (a backfilled D1, say), their history is continued
  with bars built from M1 on the broker's own grid (shown as *broker, then M1*); the screener, strategies and backtests
  use the same bars.
- **Fresh means fresh.** Every row says whether its bar is the latest the market can have closed: *fresh*, *market
  closed*, *late* or *stale*, from the market's hours and holidays; the Dashboard counts late and stale features.
- **Indian markets read like Indian charts.** NSE, BSE and MCX features use the Stage 1 session bars (H1 09:15 … 15:15,
  the last ending 15:30) and the IST day; realised volatility uses each market's trading year; a session VWAP is added;
  bars are labelled with their exchange session. Feature version 4 starts a new history; older versions are kept.
- **Regime can guide strategies.** Each strategy has optional *allowed regimes* and *blocked volatility* (off by
  default). Entries outside them wait; exits always pass; backtests apply the same filter.
- **Exchange holidays and event risk for every market.** Intelligence › Calendar imports this year's NSE, BSE and MCX
  trading holidays from Upstox; Stage 1 market hours and event risk use them. Events can name an exchange (NSE, BSE,
  MCX) as well as a currency; signals use the event window from Engine settings.
- **Rules you can trust.** *Preview (30 days)* shows how often a rule would have fired and warns when it never would,
  or always would. Currency strength warns when it rests on fewer than 3 currencies.
- **Steadier engine.** One run at a time (a manual refresh waits for the cycle); a timeframe with no newly closed bar
  only refreshes its freshness; the screener shows each source's feed health from Stage 1.

Update: merge the PR, then `git pull --ff-only origin main` and `bash scripts/deploy-workstation.sh`.

## Signals you can believe (Stage 3) — release 0.56.0

Closes the defects of the 24 Nov 2026 Signal Intelligence audit. Migration `047_signal_intelligence_integrity.sql`
(additive). Tests: `backend/tests/test_signal_stage3.py`.

- **Fewer false flips.** Each library signal can have an *exit threshold*: a side, once taken, is held until the score
  falls back below it (e.g. enter at ±20, hold until ±10). Empty keeps the old behaviour. The dashboard warns when a
  timeframe flips too often and says what to do.
- **Honest research.** The t-statistic counts overlapping h-bar returns as n ÷ h observations; *PREDICTIVE* now also needs
  the edge to hold on the last 30% of bars (out of sample) and to beat the spread (*net edge*). Ranking every signal
  states how many would look good by chance.
- **Stale scores don't vote.** A score whose bar is late for its market (Market intelligence freshness) is shown with a
  *stale bar* label and left out of the composite.
- **Strategy signals net of cost.** Signal quality shows the spread paid, the net move and the hit rate after cost;
  manual signals are measured too.
- **Traceable flips.** Every flip records the definition version that produced it.

Update: merge the PR, then `git pull --ff-only origin main` and `bash scripts/deploy-workstation.sh`.

## My Workspace control desk — release 0.57.0

A workspace now runs. Migration `048_workspace_control.sql` (additive). Tests: `backend/tests/test_workspace_control.py`.

- **One desk, three columns.** Input (data sources, instruments, timeframes), Processing (approved strategies, sizing,
  stop and target, decision expiry) and Output (Observe / Paper / Broker DEMO / LIVE, what happens to a decision,
  accounts, order type, what Stop does). The readiness line checks unsaved settings as you change them.
- **Validate, Test run, Start, Pause, Stop.** Validate shows every check with why and a fix. A test run is a read-only
  scan. Start creates runs the workspace owns, and adopts existing deployments only when you tick them; the rest are
  watched. Stop pauses first, then keeps, cancels working orders, or closes positions (asked again). An order with an
  unknown outcome is never cancelled blind. Emergency stop puts a kill switch on each of the desk's runs.
- **Shared, with an owner.** Every operator sees shared desks; one desk controls a run, so two desks can never send
  duplicate orders. Changes to a running desk are staged, reviewed and applied.
- **Nothing lost.** The saved layouts, their scan and the deployment mapping are under *Saved layouts*; a layout can be
  imported into a control desk.
- **Safety unchanged.** Every order passes the risk check. A desk reads the DEMO / LIVE order-sending switches and never
  turns them on. Starting needs the Risk approver role; on LIVE, a second person starts.

Update: merge the PR, then `git pull --ff-only origin main` and `bash scripts/deploy-workstation.sh`.

### 0.57.1 · the desk runs from what is on screen

- Validate, Test run and Start save your changes first. In 0.57.0 they were greyed out until you pressed Save, with no
  hint, so a desk looked as if it could not run.
- The readiness line shows the next step at once (for example, "Choose at least one data source" with its fix), without
  waiting for Validate.
- A too-short reason for Pause, Stop or Emergency stop now says so; before, nothing happened.
- Draft strategies are listed after approved ones and say they cannot run until a version is approved in Strategies.
- After a save, Start always sends the current workspace version, so there are no spurious "changed by someone else" errors.- **Adopt across modes.** An existing deployment in another mode, such as Paper while the desk is Broker DEMO, can now be
  adopted. You tick it in the Start window, which says it will move (for example "moves it from paper to broker demo (real
  demo orders)"). A broker run with an open broker position or working order is never moved to paper.
- **No empty start.** "Nothing to run" names each combination and its exact reason. Start refuses when nothing would run;
  before, the desk could start while controlling nothing.

## Strategy evidence and risk minimums — release 0.58.0

Migration `049_strategy_risk_governance.sql` (additive). Tests: `backend/tests/test_governance.py`.

- **Profiles.** Choose *None* (sandbox), *Retail*, *Professional* (default) or *Institutional* for strategy evidence and
  for risk minimums (Risk › Limits). Single values can be overridden; every change needs a reason.
- **Approve on evidence.** A version is approved only when a backtest of its exact parameters shows enough trades and
  history, includes costs, is net positive and holds on the last 30% of trades. Strategies › Versions › *Evidence &
  approve* shows each check with how to fix it. Where the profile allows it, a risk approver can approve by exception
  with a recorded reason; Institutional allows none.
- **Paper before LIVE.** A version runs on LIVE only after its paper record (days and closed trades) meets the profile.
- **Limits before orders.** DEMO order sending and a DEMO workspace Start need the profile's minimum limits. *Size
  limits from a profile* previews limits from the account's equity and applies them; a tighter limit you already set
  is kept.
- **Loss stops per run.** A daily-loss limit on a strategy or a run stops only those runs.
- **Timeframes.** *Copy to timeframe…* makes a new draft strategy on another timeframe; it needs its own evidence.
- **Workspace checks.** A desk shows, before Start, missing limits, the pre-LIVE policy, missing LIVE evidence and an
  order size that every order would breach.

Update: merge the PR, then `git pull --ff-only origin main` and `bash scripts/deploy-workstation.sh`.

