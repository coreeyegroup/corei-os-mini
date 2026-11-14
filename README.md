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
