# Setup audit — 15 Nov 2026 (release 0.34.0)

Scope: Setup › Connectivity (Tree, Providers, Accounts, Servers), the Overview checklist and every place a provider,
account or server can be created. Rule from the owner: keep every feature, option and control; change only the flow,
remove duplicates and confusion.

## Target structure

    Provider ──► Account ──► Server            (the tree, the tabs and every form follow this order)
       │           │            │
       │           │            └ MT5/MT4 server picked for the account (only servers in use are kept),
       │           │              FIX session, cTrader endpoint, API endpoint — addresses, clock, limits, server login
       │           └ technology chosen here: MT5 · MT4 · cTrader · FIX · broker/exchange API (Upstox, Angel One, Zerodha,
       │             Dhan, Fyers, other) — account number / client ID, permission, groups, own login (MT5/MT4 password)
       └ who: legal entity, regulator, contacts, exposure, calendar, **technologies offered**

One form per record:

| Record | The only form | Opened from |
|---|---|---|
| Provider | Providers › New provider (full record) | Tree + Provider, provider-type panel, Add account's "+ New provider" |
| Account | **Add account**: provider › technology › account › server › login. MT5 and Upstox continue in their dedicated forms (server list, addresses, method; app key, daily login) | Accounts tab, tree overview, provider panel, technology panel, server panel, provider record's "next step" |
| Server | Created inside Add account (new server of that provider), or Servers › New server (advanced) | Edited from the Servers tab or the tree |

## Findings and fixes

| # | Finding | Fix |
|---|---|---|
| 1 | Technology was never chosen: account creation was hard-wired to two shortcuts (Add MT5 account, Add Upstox account). An Angel One, MT4 or cTrader account had no path. | One **Add account** form; step 2 lists every technology (offered by the provider first, then those usual for its type, then the rest). |
| 2 | MT4 and cTrader could not be stored at all (database allowed MT5, FIX, API only). | Migration 022: MT4 and cTrader servers and accounts are recorded with their login; the readiness check shows "connector not built" until the adapters exist. |
| 3 | Indian broker APIs other than Upstox had no starting values. | API presets (Upstox — built; Angel One SmartAPI, Zerodha Kite Connect, Dhan, Fyers, other — recorded, data planned) fill the endpoint and login method; all editable. |
| 4 | A provider did not say which technologies it offers. | Provider form: **Technologies offered** (MT5, MT4, cTrader, FIX, API, feed, exchange). Add account lists these first. |
| 5 | Three different provider forms (tree short form, tree "Add who", Providers form). | Only the Providers form; every link opens it (0.33.0). |
| 6 | Servers could be added from the tree without an account (Add who / Add FIX session / API), a second path to the same thing. | Servers are added with their account in Add account; Servers › New server (advanced) remains for exceptions. |
| 7 | Tabs and names disagreed (Brokers, Broker accounts, Servers and APIs, Servers & sessions, Connections). | Providers · Accounts · Servers everywhere; tabs Tree · Providers · Accounts · Servers. |
| 8 | Tree order changed twice. | Provider › Account › Server, fixed (DEC-62). |
| 9 | Overview checklist named the old steps ("1 Broker … Add who"). | 1 Provider · 2 Account (technology, server, login in one form) · 3 Login saved · 4 First login · 5 Market data … |
| 10 | After creating a provider there was no next step. | The provider record offers "Add account at <provider>" with the technologies it offers. |
| 11 | The server-credential panel picked the wrong kind for non-API servers. | MT4 login on the account; cTrader app client ID/secret, FIX logon, API key on the server. |
| 12 | Old broker-login message stuck in a saved link. | Shown once, dismissible, removed from the address (0.32.1/0.33.0). |

## Kept (nothing removed)

Tree with Provider / Technology / Provider-type groupings, filter, expand/collapse, keyboard; provider record (all
fields); MT5 form (server list search, servers.dat, addresses per account, fastest/server/pinned routing, latency,
refresh from broker, change server, direct SDK or terminal bridge); Upstox form (client ID, app key/secret, daily login,
TOTP login); FIX session fields; API settings; credentials write-only; four-eyes approvals; CSV import/export; account
readiness and blockers; account record (advanced) and server record (advanced).

## Not built yet (recorded, collects when built)

MT4 and cTrader connectors; Angel One, Zerodha, Dhan and Fyers market-data readers; feed and exchange-native protocols
(use FIX 4.4 where the provider offers it).
