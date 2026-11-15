# MT5 terminal push — the COREI EA (DEC-68)

The COREI EA (`backend/src/corei_os_mini/md/ea/COREI_Push.mq5`, downloadable from the server's panel) runs inside any
MT5 terminal and sends what that terminal sees to COREI over HTTPS. Read-only: it never trades.

Why: the terminal always speaks its broker's protocol (no SDK can fall behind the server build); it only makes
outgoing HTTPS calls (no inbound port on the PC, VPS or container); COREI stores no MT5 password for these accounts.
Idea taken from how MT5-centred data APIs use an EA with WebRequest; the code here is COREI's own.

| Step | Where |
|---|---|
| 1. The account's server: Method = **Terminal push (COREI EA in MT5)** | Setup › Connectivity › Servers › the server |
| 2. **Create push token** (shown once) | same panel |
| 3. Download COREI_Push.mq5, compile in MetaEditor (F7) | same panel |
| 4. MT5 Tools › Options › Expert Advisors › allow WebRequest for the COREI address | MT5 |
| 5. Terminal logged in to the account; attach COREI_Push; AccountCode + PushToken | MT5 |

Data path: EA → `POST /api/md/push` (Bearer token + X-COREI-Account; the terminal's login must match the account) →
`md_push_inbox` → the collector's push session (same handling as every source: quality, bars, journal, failover) →
commands (`hello`, `subscribe`, `unsubscribe`, `history`, `catalog`) through `md_push_outbox`, returned as text lines.
The EA's symbol list fills the symbol catalogue. The panel lists each terminal's login, server, build, last push and IP.

Where to run the terminal: your Windows PC (while it is on), a Windows VPS (production), or the COREI Docker container
(`scripts/use-mt5-docker.sh`, attach the EA through the browser view).
