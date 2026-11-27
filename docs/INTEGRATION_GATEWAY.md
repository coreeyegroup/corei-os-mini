# COREI Integration Gateway (`/api/v1/automation`)

Release 0.65.0 · Decision DEC-109 · Gaps: GAP register section S.

The gateway lets CORE-AI, n8n and other applications read COREI's data, run analysis jobs, propose signals and place
**paper (and, if granted, DEMO) orders** with their own key and only the permissions an administrator gave them.
It is a thin, versioned facade over the services the workstation already uses: the same risk checks, setup gates,
kill switches, order sending switches and audit apply. COREI keeps working when CORE-AI or n8n is offline.

**Never through the gateway:** LIVE orders, approvals of held orders, releasing orders to a broker, amending orders,
broker credentials. These permissions are listed as UNAVAILABLE and the server refuses them even if requested.

## 1. Architecture

```
CORE-AI / n8n / script ──HTTPS + Bearer key──▶ Caddy (/api/v1/automation*, 256 KB body limit)
     ▲                                            │
     │ signed webhooks (HMAC-SHA256)              ▼
     └──────────── webhook sender ◀── uvicorn 127.0.0.1:18786 ── integration/gateway.py
                                                  │  auth.py: key → connection → permission → resource scope → rate limit
                                                  ▼
                 existing services: market data, intelligence, scan runs (jobs), risk.check, OMS ticket, portfolio, audit
```

| Module | Job |
|---|---|
| `integration/catalog.py` | Permissions (with AVAILABLE / PARTIAL / UNAVAILABLE), events, profiles |
| `integration/auth.py` | Bearer keys (SHA-256 hash only), refusal codes, permission and scope checks, rate limit, usage log |
| `integration/gateway.py` | The `/api/v1/automation` endpoints and the filtered OpenAPI document |
| `integration/snapshot.py` | Market snapshots: datasets, quality, formats, size budget |
| `integration/webhooks.py` | Destination checks (SSRF), signing, queue, retries, job notices |
| `integration/admin.py` | Administration API behind the workstation login (`/api/config/integrations`) |
| `sql/052_integration_gateway.sql` | Additive tables `int_*` |
| `ui/src/Integrations.tsx` | Setup › 8 · Integrate › Integrations |

## 2. Connections, keys and permissions

* One **connection** per application (CORE-AI, n8n, a script…). It has a profile, an environment (PAPER, or PAPER and
  DEMO), permissions, a resource scope (accounts / instruments / strategies: *all* or *only these*; an empty "only
  these" list grants nothing), a requests-per-minute limit, a concurrent-jobs limit and optional allowed networks (CIDR).
* **Keys** look like `corei_<10-character prefix>_<secret>`. COREI stores only a SHA-256 hash; the key is shown once.
  Keys can expire (30 / 90 / 365 days or never), be **rotated** with a grace period for the old key, and be revoked.
  Keys in the query string are refused; send `Authorization: Bearer <key>`.
* Every change (connection, key, webhook) is written to the audit log in the same transaction, with a reason.
* Profiles start read-only or signal-only. No profile includes order permissions beyond `orders.read`; add
  `orders.preview`, `orders.paper`, `orders.demo`, `orders.cancel` explicitly.

### Permission matrix

| Group | Permission | Meaning | Status |
|---|---|---|---|
| System | `system.read` | Platform health and version | AVAILABLE |
| System | `capabilities.read` | Capability discovery | AVAILABLE |
| Accounts | `accounts.read` | Accounts in scope: environment, permission, balance, equity — No credentials are ever returned. | AVAILABLE |
| Instruments | `instruments.read` | Instrument catalogue, collection and tradability | AVAILABLE |
| Market data | `market_data.read` | Latest quotes with age and source — Quotes are polled observations, not an exchange tick stream. | AVAILABLE |
| Market data | `market_data.history` | Historical candles (closed bars) | AVAILABLE |
| Market data | `market_data.snapshot` | Multi-instrument, multi-timeframe analysis snapshot | AVAILABLE |
| Market data | `market_data.stream` | Real-time push stream (WebSocket/SSE) — Not built; use webhooks or polling. | UNAVAILABLE |
| Market intelligence | `market_intelligence.read` | Indicators, regime, volatility, sessions, events | AVAILABLE |
| Market intelligence | `market_intelligence.screen` | Screener ranking across instruments | AVAILABLE |
| Strategies | `strategies.read` | Strategy catalogue, versions, parameters, status | AVAILABLE |
| Strategies | `strategies.evaluate` | Evaluate one strategy on one instrument (job) | AVAILABLE |
| Strategies | `strategies.scan` | Instrument- or strategy-centric scans (job) | AVAILABLE |
| Strategies | `strategies.backtest` | Backtests through the gateway — Backtests run from the workstation; gateway access is a later release. | UNAVAILABLE |
| Signals | `signals.read` | Read candidate signals submitted by this connection | AVAILABLE |
| Signals | `signals.propose` | Submit candidate signals with provenance (never an order) | AVAILABLE |
| Signals | `signals.validate` | Run a submitted signal through the risk checks | AVAILABLE |
| Risk | `risk.read` | Limits, kill switches, recent decisions | AVAILABLE |
| Risk | `risk.assess` | Risk assessment of a candidate order (no order is created) | AVAILABLE |
| Portfolio | `portfolio.read` | Equity, positions, exposure, P&L | AVAILABLE |
| Portfolio | `portfolio.analyse` | Portfolio analysis endpoints — Read data is available; proposals are kept by the caller. | PARTIAL |
| Orders | `orders.read` | Orders and fills of accounts in scope | AVAILABLE |
| Orders | `orders.preview` | Validate and risk-check an order without placing it | AVAILABLE |
| Orders | `orders.paper` | Place paper orders (simulated; never reaches a broker) | AVAILABLE |
| Orders | `orders.demo` | Place orders on DEMO broker accounts — Same risk checks, setup gates and order sending switch as the workstation. | AVAILABLE |
| Orders | `orders.cancel` | Cancel orders this connection placed | AVAILABLE |
| Orders | `orders.live_request` | Request LIVE orders — LIVE orders through integrations are not enabled in this release. | UNAVAILABLE |
| Orders | `orders.approve` | Approve held orders (four-eyes) — Approvals stay with people in the workstation. | UNAVAILABLE |
| Orders | `orders.execute` | Release orders to a broker — Not offered to integrations. | UNAVAILABLE |
| Orders | `orders.modify` | Amend working orders — Later release. | UNAVAILABLE |
| Post-trade | `post_trade.read` | Fills, costs, realised P&L | AVAILABLE |
| Post-trade | `reports.read` | Reports — Use post-trade and portfolio data. | PARTIAL |
| Jobs | `jobs.read` | Read job status and results | AVAILABLE |
| Jobs | `jobs.cancel` | Cancel own jobs | AVAILABLE |
| Events | `events.subscribe` | Receive event webhooks | AVAILABLE |
| Events | `webhooks.manage` | Create and delete own webhooks | AVAILABLE |

### Profile defaults

| Profile | Permissions |
|---|---|
| CORE-AI | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read`, `market_intelligence.screen`, `strategies.evaluate`, `strategies.scan`, `signals.read`, `signals.propose`, `signals.validate`, `risk.assess`, `portfolio.read`, `jobs.cancel` |
| n8n | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read`, `market_intelligence.screen`, `strategies.scan`, `signals.read`, `portfolio.read`, `orders.read`, `post_trade.read`, `jobs.cancel`, `events.subscribe`, `webhooks.manage` |
| External AI agent | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read`, `signals.read`, `signals.propose` |
| Python application | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read` |
| JavaScript application | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read` |
| Research platform | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `market_data.history`, `market_data.snapshot`, `market_intelligence.read`, `strategies.read`, `risk.read`, `jobs.read` |
| External dashboard | `system.read`, `capabilities.read`, `instruments.read`, `market_data.read`, `portfolio.read`, `orders.read`, `post_trade.read`, `risk.read` |
| Custom integration | `system.read`, `capabilities.read` |

## 3. Endpoints

Base URL: `https://corei-os-mini.coreeye.in/api/v1/automation`. Machine-readable: `GET /openapi.json` (no key needed;
each operation has `x-required-scopes`). Every response carries `X-Request-ID` and `X-COREI-API-Version: 1.0`.

| Method | Path | Required permission |
|---|---|---|
| GET | `/system` | system.read |
| GET | `/auth/test` | any valid key |
| GET | `/capabilities` | capabilities.read |
| GET | `/accounts` | accounts.read |
| GET | `/instruments` | instruments.read |
| GET | `/market-data/quotes` | market_data.read |
| GET | `/market-data/candles` | market_data.history |
| POST | `/market-data/snapshot` | market_data.snapshot, market_intelligence.read (for indicators, regime, levels, events) |
| GET | `/market-intelligence` | market_intelligence.read |
| GET | `/market-intelligence/screener` | market_intelligence.screen |
| GET | `/strategies` | strategies.read |
| POST | `/strategies/scan` | strategies.scan |
| POST | `/strategies/evaluate` | strategies.evaluate |
| GET | `/jobs` | jobs.read |
| GET | `/jobs/{job_id}` | jobs.read |
| GET | `/jobs/{job_id}/results` | jobs.read |
| POST | `/jobs/{job_id}/cancel` | jobs.cancel |
| POST | `/signals` | signals.propose |
| GET | `/signals` | signals.read |
| GET | `/signals/{signal_id}` | signals.read |
| POST | `/risk/assess` | risk.assess, signals.validate (with signal_id) |
| GET | `/risk` | risk.read |
| GET | `/portfolio` | portfolio.read |
| GET | `/orders` | orders.read |
| POST | `/orders/preview` | orders.preview |
| POST | `/orders` | orders.paper (mode PAPER), orders.demo (mode DEMO) |
| POST | `/orders/{order_id}/cancel` | orders.cancel |
| GET | `/post-trade/fills` | post_trade.read |
| GET | `/usage` | system.read |
| GET | `/events` | capabilities.read |
| GET | `/webhooks` | webhooks.manage |
| POST | `/webhooks` | webhooks.manage, events.subscribe |
| DELETE | `/webhooks/{webhook_id}` | webhooks.manage |
| POST | `/webhooks/{webhook_id}/test` | webhooks.manage |
| GET | `/webhooks/deliveries` | webhooks.manage |
| POST | `/webhooks/deliveries/{delivery_id}/replay` | webhooks.manage |

Lists take `limit` and `offset` and return `total`. Prices, quantities and money are strings (exact decimals).

### Errors

One shape everywhere: `{"error": {"code": "...", "message": "...", "request_id": "..."}}`. Validation errors add the
field and the problem, never the submitted value.

| HTTP | Code |
|---|---|
| 401 | `EXPIRED_CREDENTIAL` |
| 401 | `INVALID_CREDENTIAL` |
| 401 | `MISSING_CREDENTIAL` |
| 401 | `REVOKED_CREDENTIAL` |
| 403 | `CONNECTION_DISABLED` |
| 403 | `ENVIRONMENT_NOT_ALLOWED` |
| 403 | `LIVE_NOT_ENABLED` |
| 403 | `NETWORK_NOT_ALLOWED` |
| 403 | `OUT_OF_SCOPE` |
| 403 | `PERMISSION_DENIED` |
| 404 | `DATA_NOT_COLLECTED` |
| 404 | `DELIVERY_NOT_FOUND` |
| 404 | `INVALID_ACCOUNT` |
| 404 | `INVALID_INSTRUMENT` |
| 404 | `INVALID_STRATEGY` |
| 404 | `JOB_NOT_FOUND` |
| 404 | `ORDER_NOT_FOUND` |
| 404 | `SIGNAL_NOT_FOUND` |
| 404 | `WEBHOOK_NOT_FOUND` |
| 409 | `TOO_MANY_WEBHOOKS` |
| 413 | `REQUEST_SIZE_EXCEEDED` |
| 422 | `INVALID_DATASET` |
| 422 | `INVALID_EVENT` |
| 422 | `INVALID_FEED` |
| 422 | `INVALID_IDEMPOTENCY_KEY` |
| 422 | `INVALID_REQUEST` |
| 422 | `INVALID_SNAPSHOT` |
| 422 | `MISSING_PROVENANCE` |
| 422 | `NOTHING_TO_SCAN` |
| 422 | `NOT_ASSESSABLE` |
| 422 | `UNSAFE_DESTINATION` |
| 429 | `RATE_LIMITED` |
| 429 | `TOO_MANY_JOBS` |
| 501 | `CAPABILITY_UNAVAILABLE` |

## 4. Market snapshot (`POST /market-data/snapshot`, schema `corei.snapshot.v1`)

```json
{"request_id": "analysis-001", "instruments": ["EURUSD", "NIFTY"], "timeframes": ["H4", "H1", "M15"],
 "include": ["instrument_metadata", "latest_quote", "candles", "indicators", "market_regime", "levels", "events", "data_quality"],
 "feed_selection": "ACTIVE_REFERENCE", "closed_bars_only": true, "format": "AI_COMPACT",
 "candles_per_timeframe": 100, "max_output_chars": 16000}
```

* Formats: `RAW`, `NORMALIZED`, `COMPACT`, `AI_COMPACT`, `SUMMARY`, `FEATURE_VECTOR`.
* **Never silently truncated:** if the result exceeds `max_output_chars`, candles are halved (down to 10 per timeframe)
  and the response says so under `coverage`; if it still does not fit, `413 REQUEST_SIZE_EXCEEDED`.
* Each instrument carries `data_quality.state` = PASS / WARNING / FAIL / UNAVAILABLE with reasons, the source feed,
  the quote age, and `quote_kind: POLLED_OBSERVATION` (quotes are polled, not an exchange tick stream).
* Datasets COREI does not collect — `open_interest`, `option_chain`, `greeks`, `market_depth` — are listed under
  `missing_datasets` and are never invented.
* Times are market event time from the source, not the server clock.

## 5. Jobs, signals, risk and orders

* `POST /strategies/scan` (`mode`: `instrument` or `strategy`) and `POST /strategies/evaluate` return **202** with a
  `job_id`. Poll `GET /jobs/{id}` (QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED), then `GET /jobs/{id}/results`,
  or subscribe to `job.completed` / `job.failed`. The same `request_id` returns the existing job instead of a new one.
* `POST /signals` records a **candidate** signal with mandatory `provenance` (application, agent, model, input snapshot…).
  A signal is never an order.
* `POST /risk/assess` runs the real pre-trade checks and returns APPROVED / REJECTED / REQUIRES_APPROVAL / UNAVAILABLE.
  Nothing is created: the check runs in a transaction that is rolled back.
* `POST /orders` needs an `Idempotency-Key` header; repeating it returns the same order. `mode` PAPER (default) or DEMO
  (needs `orders.demo`, a DEMO-environment connection and a DEMO account). `mode: LIVE` → `403 LIVE_NOT_ENABLED`.
  Orders go through the same order management as the workstation, so risk, gates, kill switches and order sending apply.

## 6. Webhooks

* Destinations must be `https`, carry no credentials and must not resolve to private or local addresses
  (`COREI_WEBHOOK_ALLOW_PRIVATE=true` relaxes the last rule for a private network). Redirects are not followed.
* Headers: `X-COREI-Event`, `X-COREI-Event-Id`, `X-COREI-Timestamp`, `X-COREI-Signature`.
* Delivery is **at least once**: retries back off (15 s doubling, max 1 h) up to the webhook's attempt limit, then
  FAILED (replay with `POST /webhooks/deliveries/{id}/replay`). De-duplicate on `X-COREI-Event-Id`.
* Events: `signal.created`, `signal.validated`, `signal.rejected`, `job.completed`, `job.failed`, `order.submitted`, `order.rejected`, `order.event`, `risk.kill_switch`, `approval.needed`, `system.order_sending`, `market.data.alert`, `connection.failed`, `system.alert`, `webhook.test`.

Verify the signature (reject if the timestamp is more than 5 minutes old):

```python
import hmac, hashlib, time
def verify(secret: str, ts: str, raw_body: bytes, signature: str) -> bool:
    if abs(time.time() - int(ts)) > 300:
        return False
    want = 'sha256=' + hmac.new(secret.encode(), ts.encode() + b'.' + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature)
```

## 7. n8n

1. In COREI: Setup › 8 · Integrate › Integrations › **+ New connection**, profile **n8n**, save, then **Keys › + Issue key**
   and copy the key (shown once).
2. In n8n: Credentials › New › **HTTP Bearer Auth** (or in an HTTP Request node: Authentication **Generic Credential
   Type** › Generic Auth Type **Bearer Auth**). Name it **COREI Gateway** and paste the **raw key only** — no `Bearer `.
3. Import the workflows in `integrations/n8n/` (Workflows › Import from file). They reference the credential by name and
   contain no keys. Optional: set the n8n environment variable `COREI_BASE_URL`.

| File | What it does | Permissions |
|---|---|---|
| `A-connection-test.json` | Auth test and capability list | any valid key |
| `B-market-snapshot.json` | AI_COMPACT snapshot | `market_data.snapshot` |
| `C-strategy-scan.json` | Queue a scan, poll, read results | `strategies.scan`, `jobs.read` |
| `D-signal-and-risk.json` | Propose a signal, assess risk (no order) | `signals.propose`, `signals.validate`, `risk.assess` |
| `E-webhook-receiver.json` | Receive events and verify the signature | `events.subscribe` (on the connection) |
| `F-paper-order.json` | Preview then place a **paper** order with an idempotency key | `orders.preview`, `orders.paper` |

Workflow E reads the signing secret from the n8n environment variable `COREI_WEBHOOK_SECRET` and needs
`NODE_FUNCTION_ALLOW_BUILTIN=crypto`. Replace `<PAPER_ACCOUNT_ID>` in D and F with a paper account id from `GET /accounts`.

## 8. CORE-AI

Inbound (CORE-AI calls COREI): create a connection with the **CORE-AI** profile and give CORE-AI the key. CORE-AI reads
snapshots and intelligence, runs scans and proposes signals; it cannot place orders unless an administrator adds order
permissions.

Outbound (COREI calls CORE-AI) is **not built** in this release. The CORE-AI contract was checked (commit `ca0cf7a`):
`POST /api/v1/automation/jobs` with `{request_id, prompt, model?}` → `{id}`; `GET /api/v1/automation/jobs/{id}`
→ status QUEUED / RUNNING / CANCEL_REQUESTED / SUCCEEDED / FAILED / CANCELLED; keys `Bearer coreai_…`; webhooks signed
`sha256=HMAC(secret, ts.payload)` with `X-Core-AI-Timestamp` / `X-Core-AI-Signature`. Until outbound is built, use n8n
to pass a COREI snapshot to CORE-AI and post the result back as a signal.

## 9. Examples

```bash
KEY=corei_…   # from Keys › Issue key; keep it out of scripts and history (e.g. read -s KEY)
curl -s https://corei-os-mini.coreeye.in/api/v1/automation/auth/test -H "Authorization: Bearer $KEY"
```

```python
import os, requests
BASE = "https://corei-os-mini.coreeye.in/api/v1/automation"
H = {"Authorization": f"Bearer {os.environ['COREI_API_KEY']}"}
job = requests.post(f"{BASE}/strategies/scan", headers=H, json={"request_id": "scan-1", "mode": "strategy"}, timeout=30).json()
print(requests.get(f"{BASE}/jobs/{job['job_id']}", headers=H, timeout=30).json()["status"])
```

```javascript
const H = { Authorization: `Bearer ${process.env.COREI_API_KEY}`, "Content-Type": "application/json" };
const r = await fetch("https://corei-os-mini.coreeye.in/api/v1/automation/orders", { method: "POST",
  headers: { ...H, "Idempotency-Key": "order-001" },
  body: JSON.stringify({ account_id: "<PAPER_ACCOUNT_ID>", instrument: "EURUSD", side: "BUY", quantity: "1000", mode: "PAPER" }) });
```

## 10. Deployment, settings and rollback

| Setting | Default | Meaning |
|---|---|---|
| `COREI_INTEGRATION_WEBHOOKS` | `on` | `off` stops the webhook sender (deliveries stay queued) |
| `COREI_WEBHOOK_ALLOW_PRIVATE` | `false` | Allow webhook destinations on private addresses |
| `COREI_PASSWORD_MIN_LENGTH` | `2` | Password minimum in Development mode; Production mode uses 6 or this value if higher (DEC-110) |

* Migration `052_integration_gateway.sql` only adds `int_*` tables; it changes no existing table.
* Caddy exposes `/api/v1/automation*` (256 KB body limit) in `deploy/workstation.Caddyfile`.
* **Switch off without a rollback:** disable each connection (or revoke its keys) in the Integrations screen; set
  `COREI_INTEGRATION_WEBHOOKS=off`.
* **Roll back the release:** check out the previous tag and redeploy. The `int_*` tables can stay (unused) or be dropped
  later with owner approval; no other data depends on them.

## 11. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `401 MISSING_CREDENTIAL` | No `Authorization: Bearer` header. In n8n the credential must be attached to the node. |
| `401 INVALID_CREDENTIAL` | Wrong key, or the key was pasted with `Bearer ` inside the n8n credential. |
| `401 REVOKED_CREDENTIAL` / `EXPIRED_CREDENTIAL` | Issue or rotate a key. |
| `403 PERMISSION_DENIED` | The connection lacks the permission named in the message; edit its permissions. |
| `403 OUT_OF_SCOPE` | The account / instrument / strategy is not in the connection's resource scope. |
| `403 NETWORK_NOT_ALLOWED` | The caller's address is outside the allowed networks. |
| `429 RATE_LIMITED` | Over the per-minute limit; wait (`Retry-After`) or raise the limit. |
| `501 CAPABILITY_UNAVAILABLE` | The feature is not built (streams, backtests, LIVE, approvals). |
| `413 REQUEST_SIZE_EXCEEDED` | Reduce instruments, timeframes or candles, or raise `max_output_chars`. |
| Webhook never arrives | Connection needs `events.subscribe`; check Webhooks › Recent deliveries for the HTTP status and error. |

The Integrations screen's **Test & setup** tab sends a real request with the key from the browser **without** the
workstation session — the same path an external application uses.
