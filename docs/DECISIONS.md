# COREI-OS-MINI — Owner Decisions after Document Index v1.1

These decisions were made by the project owner after the v1.1 baseline. Where they conflict with
the baseline documents, these decisions govern. Blueprint decisions DEC-01 … DEC-17 remain in force
except where amended below.

| ID | Date | Decision | Supersedes / amends |
|---|---|---|---|
| DEC-18 | 10 Nov 2026 | **COREI-OS-MINI is a production trading platform, not a paper or demo prototype.** Live accounts may be registered and used. | Amends DEC-09 and the "paper-only" statements in Index v1.1, Master Work Prompt v1.1, Technology Inventory v1.3 and Process Flow v1.4 §5.7. |
| DEC-19 | 10 Nov 2026 | **Live order sending is governed by a firm-wide switch per environment (DEMO, LIVE), off by default.** Turning it on requires a recorded reason and typed confirmation of the environment name; every change is audited. An account sends orders only if its connection and provider are active, its permission is TRADING, its credential allows trading, a provider adapter exists, and the switch for its environment is on. | Implements Blueprint §13.3 kill controls at firm scope and principle 13 (explicit authorisation before live activity). |
| DEC-20 | 10 Nov 2026 | **The earlier configuration registries are removed and replaced** by one Configuration domain: providers, connections, accounts, instruments, symbol mappings, credentials, execution controls and an append-only audit log (migration 009). Existing configuration records were deleted, not migrated. | Replaces migrations 003–008 behaviour and the separate broker-account, connector-profile and terminal-installation screens. |
| DEC-21 | 10 Nov 2026 | **Launch platforms: MT5 and broker REST/WebSocket APIs.** MT5 logins are stored per account; REST API keys or OAuth clients are stored per connection. Other platforms are added as new adapter types. | Narrows Technology Inventory v1.3 §2 "Provider connection". |
| DEC-22 | 10 Nov 2026 | **Environment belongs to the connection** and is fixed after creation; every account inherits it. A connection's platform, provider and code are also fixed; changing them means creating a new connection. | Implements Blueprint §8.2 "an account cannot be silently rebound". |
| DEC-23 | 10 Nov 2026 | **Four roles, held in the database:** Administrator (users only), Trading operations (configuration and credentials; may turn order sending off), Risk approver (turns order sending on; approves strategy versions; activates deployments), Observer (read only). A user may hold several. Administrators cannot change their own roles, and at least one active administrator must remain. The existing server login becomes the first administrator on its first sign-in. | Implements Process Flow v1.4 §7 and Blueprint §15 role separation. |
| DEC-24 | 10 Nov 2026 | **Four-eyes on LIVE activation:** a deployment on a LIVE account must be activated by a different person from the one who proposed it. Strategy-version approval needs the Risk approver role but not a second person, so a single approver can work on DEMO. | Blueprint §15 "must not approve own … live activation change". |
| DEC-25 | 10 Nov 2026 | **Deployment matrix:** a strategy trades only through an explicit, ACTIVE deployment row of strategy version × instrument × account, which names its market-data source (any connection mapping that instrument for data). Approved strategy versions are immutable, enforced in the database. Any edit returns a deployment to PROPOSED. | Implements Blueprint §8.5 and DEC-12/DEC-13. |

## Still required before the first live order

DEC-18 removes the paper-only restriction; it does not by itself make live trading ready. The
blueprint's acceptance criteria (§18) still apply. Before the LIVE switch is turned on, the
following must exist and be verified: a provider adapter, the order lifecycle with UNKNOWN-outcome
handling, signals from live data, risk limits with reservations, and reconciliation (see
docs/GAP_REGISTER.md). Roles and four-eyes activation are in place (DEC-23, DEC-24).
