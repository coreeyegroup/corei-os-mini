# Configuration naming guide

## The four records and what identifies each

| Record | What it is | Permanent ID (suggested automatically) | Name | Uniqueness |
|---|---|---|---|---|
| **Provider** | The broker brand / legal entity | `ICMARKETS`, `EXNESS`, `UPSTOX` | Brand and legal entity, e.g. *IC Markets (Raw Trading Ltd)* | ID unique |
| **Connection** | One way in to that provider: one MT5 trade server, or one broker API app, in one environment | `<PROVIDER>_<MT5\|API>_<LIVE\|DEMO>[_n]`, e.g. `ICMARKETS_MT5_LIVE`, `ICMARKETS_MT5_LIVE_2` | Plain description incl. server, e.g. *IC Markets MT5 live · ICMarketsSC-MT5-2* | ID unique |
| **Account** | One broker account reached through that connection | `<PROVIDER>_<ENV>_<BROKER LOGIN>`, e.g. `ICMARKETS_LIVE_51234567` | What it is for, e.g. *Gold main*, *Hedge* | Broker login unique per connection |
| **Account holder** (field on the account) | The person or company that owns the account at the broker | – | *Yatheesh N S*, *Coreeye Group Pvt Ltd* | One holder can have many accounts at many brokers |

IDs never change after creation and appear in the audit log, deployments and orders. Names can be edited.

**Users vs account holders:** *Users* (Configuration › Users) are people who operate COREI and have roles. *Account holders* own broker accounts. They can be the same person but are recorded separately.

## Example 1 — MT5: several brokers, several holders, several accounts each

| Provider | Connection (MT5 server · env) | Account ID | Holder | Broker login | Name |
|---|---|---|---|---|---|
| ICMARKETS | ICMARKETS_MT5_LIVE (ICMarketsSC-MT5-2 · LIVE) | ICMARKETS_LIVE_51234567 | Yatheesh N S | 51234567 | Gold main |
| ICMARKETS | ICMARKETS_MT5_LIVE | ICMARKETS_LIVE_51234890 | Yatheesh N S | 51234890 | Hedge |
| ICMARKETS | ICMARKETS_MT5_LIVE | ICMARKETS_LIVE_51990011 | Coreeye Group Pvt Ltd | 51990011 | Firm gold |
| ICMARKETS | ICMARKETS_MT5_DEMO (ICMarketsSC-Demo · DEMO) | ICMARKETS_DEMO_61002233 | Yatheesh N S | 61002233 | Strategy test |
| EXNESS | EXNESS_MT5_LIVE (Exness-MT5Real8 · LIVE) | EXNESS_LIVE_81112233 | Yatheesh N S | 81112233 | Gold main |
| EXNESS | EXNESS_MT5_LIVE_2 (Exness-MT5Real15 · LIVE) | EXNESS_LIVE_93334455 | Coreeye Group Pvt Ltd | 93334455 | Firm gold |
| METAQUOTES | METAQUOTES_MT5_DEMO (MetaQuotes-Demo · DEMO) | METAQUOTES_DEMO_5000000001 | Yatheesh N S | 5000000001 | Platform test |

Rules this follows:
- **One connection per broker trade server and environment.** Accounts on the same server share it; a broker with two live servers has two connections.
- Each MT5 account saves its own password (master = can trade, investor = read only).
- Filter Accounts by holder (search box) to see everything one person or company owns across brokers.

## Adding MT5 accounts (Accounts › Add MT5 account)

One window, in the order MT5 uses: **provider and environment → login, holder, password → server → this account's addresses → name and permission**.

- **Server:** *Find servers* asks the MT5 broker directory live (current servers and addresses); *Open servers.dat* reads a terminal's file; *Enter manually* is the fallback.
- If COREI already has a connection for that provider, environment and server, the account is attached to it. Otherwise one is created. Nothing is duplicated.
- **Addresses for this account** — switchable later on the account, nothing is lost by switching:

| Choice | Uses | When |
|---|---|---|
| Use the server's addresses (default) | The connection's ticked addresses, in its order | Most accounts; one place to maintain |
| Fastest first | The connection's ticked addresses, fastest by the latest latency test | When latency matters and you test regularly |
| Choose for this account | Only the addresses you tick, in your order (from that server's list) | A specific account must use a specific route |

- **Test latency** measures TCP connect time from the COREI server (not your PC), best of three, without logging in. Private addresses (10.x, 192.168.x, …) are not tested; they only work inside the broker's network.
- An MT5 login belongs to one server; it cannot be moved to another server. What differs per route is the address. Slippage is mostly the broker's execution; it will be recorded per fill once the MT5 adapter exists.

## Example 2 — Indian broker with an OAuth API (Upstox)

Upstox facts this relies on (checked 10 Nov 2026, Upstox developer documentation):
- An Upstox **app** has an API key, API secret and a **mandatory redirect URL**; `http://localhost` is accepted for local testing.
- Each Upstox account holder logs in through the app and receives an **access token that expires at 3:30 AM the next day**; the login code is single-use.
- `client_id` in the API is the app's API key, not the customer's UCC.
- The **sandbox** allows one app per user, issues 30-day tokens and currently supports order APIs only.

| Record | Value |
|---|---|
| Provider | ID `UPSTOX`, name *Upstox (RKSV Securities India Pvt Ltd)*, type Broker |
| Connection (live app) | ID `UPSTOX_API_LIVE`, platform REST/WebSocket API, LIVE, purpose Market data and trading, base URL `https://api.upstox.com`, authentication OAuth 2, redirect URL `https://corei-os-mini.coreeye.in/api/oauth/upstox/callback` (must match the app exactly), streaming URL empty (issued after login), credential = API key + API secret |
| Connection (sandbox) | ID `UPSTOX_API_DEMO`, DEMO, purpose Trading only (sandbox has no market data) |
| Account | ID `UPSTOX_LIVE_<UCC>`, broker client ID = the holder's UCC, holder *Yatheesh N S*, currency INR |
| Instrument | e.g. `RELIANCE`, asset class Equity, INR, contract size 1 |
| Symbol mapping | broker symbol = Upstox instrument key, e.g. `NSE_EQ|<ISIN>` |

Several Upstox holders: each holder logs in once a day and gets their own token. Whether one app may serve several holders depends on Upstox's terms for your app type; if each holder needs their own app, create one connection per app (`UPSTOX_API_LIVE`, `UPSTOX_API_LIVE_2`, …).

**Not built yet:** the daily OAuth login (redirect callback, per-account token store with the 3:30 AM expiry) is part of the Upstox adapter. Until then accounts show *Provider adapter for this platform is not implemented yet*.
