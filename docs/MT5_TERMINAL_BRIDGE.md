# MT5 terminal bridge — the stable way to reach MetaTrader 5

**Why.** COREI's first MT5 route (`connectors/mt5-direct-check`, the licensed mtapi SDK) speaks MetaQuotes'
private server protocol itself. MetaQuotes changes that protocol with every MT5 build, and brokers move to
new builds within weeks. On 12–13 Nov 2026 both MetaQuotes-Demo and VTMarkets-Live 3 accepted the login and
then answered in a protocol the SDK could not read. That will happen again after every MT5 release, and only
the SDK vendor can fix it.

The official MT5 **terminal** never has this problem: MetaQuotes updates it with the protocol. MetaQuotes also
publishes the **MetaTrader5 Python package**, which talks to a running terminal and gives ticks, bars, symbols,
account state — and, later, orders. The bridge (`connectors/mt5-bridge/corei_mt5_bridge.py`) sits next to the
terminal and lets COREI use it over one TLS connection. COREI's collector, journal, quality scoring, bars and
archive are unchanged: the bridge speaks the same line protocol as the direct reader.

```
COREI (Ubuntu) ──TLS, pinned cert + token──▶ corei_mt5_bridge.py ──MetaTrader5 package──▶ MT5 terminal ──▶ broker
```

The password travels once per session, over TLS, from COREI's encrypted store to the terminal. The bridge
machine stores no password and no token in COREI's name: its own token file is the shared secret.

## Where to run the terminal

| Option | When | Notes |
| --- | --- | --- |
| **A. Windows VPS** (recommended) | Production | Any small Windows Server/Windows 11 VPS; many brokers give one free for active accounts. Nothing else runs there. |
| **B. Wine on the COREI Ubuntu server** | Test, single account | MetaQuotes supports MT5 under Wine; the Python package needs a Windows Python inside Wine. Works, fiddlier to keep up. |

One terminal holds one broker login at a time. One bridge serves one terminal. For several accounts streaming
at once, install the terminal in *portable mode* in several folders and run one bridge per folder on its own
port (`--terminal <folder>\terminal64.exe --port 9444`, and so on).

## A. Windows VPS, step by step

1. **Install the broker's MT5 terminal** (download from the broker; e.g. VT Markets, IC Markets) and log in
   once by hand with the account's login, password and server. Confirm it shows prices. Leave it installed;
   the bridge starts it when needed.
2. **Install Python 3.11 or 3.12, 64-bit** from python.org (tick *Add python.exe to PATH*).
3. In PowerShell:
   ```
   pip install MetaTrader5
   mkdir C:\corei-bridge
   ```
   Copy `connectors/mt5-bridge/corei_mt5_bridge.py` from the repository into `C:\corei-bridge\`
   (and `openssl.exe` must be on the PATH — Git for Windows provides it, or install OpenSSL).
4. **First run**, in `C:\corei-bridge`:
   ```
   python corei_mt5_bridge.py --terminal "C:\Program Files\MetaTrader 5\terminal64.exe" --port 9443
   ```
   It prints:
   - `Certificate fingerprint (SHA-256): AB:CD:…` — copy it;
   - `Token file: C:\corei-bridge\corei-bridge.token` — open the file and copy its contents.
   Keep the window open for now.
5. **Firewall:** allow inbound TCP 9443 **from COREI's server IP only** (Windows Defender Firewall › Inbound
   rules › New rule › Port 9443 › Scope: remote IP = COREI's outbound IP, shown on Setup › Overview).
6. **Keep it running:** Task Scheduler › Create task › *Run only when user is logged on* (the terminal needs a
   desktop session) › Trigger *At log on* › Action `python C:\corei-bridge\corei_mt5_bridge.py --terminal "…\terminal64.exe" --port 9443`
   › Settings: *If the task fails, restart every 1 minute*. Set the VPS to log the user on automatically.

## B. Wine on the COREI server (test)

```
sudo dpkg --add-architecture i386 && sudo apt update && sudo apt install -y wine64 wine32 winetricks
wget https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5ubuntu.sh && bash mt5ubuntu.sh   # MetaQuotes' installer
```
Then install a **Windows** Python inside Wine (`wine python-3.12.x-amd64.exe /quiet InstallAllUsers=1 PrependPath=1`),
`wine pip install MetaTrader5`, log the terminal in once (`wine ~/.mt5/drive_c/Program\ Files/MetaTrader\ 5/terminal64.exe`),
and run the bridge with `wine python corei_mt5_bridge.py --bind 127.0.0.1 --port 9443 --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe"`.
Bridge host in COREI is then `127.0.0.1`. The terminal needs an X session (Xvfb is enough): `xvfb-run -a wine …`.

## C. In COREI

**New account:** Setup › Brokers & accounts › **Add MT5 account**. Fill in broker, login, password, server as
usual. Under *How COREI reaches it* keep **MT5 terminal bridge (recommended)** and enter:

| Field | Value |
| --- | --- |
| Bridge host | the VPS's DNS name or IP (`127.0.0.1` for Wine on the same server) |
| Bridge port | `9443` |
| Certificate fingerprint | as printed by the bridge (32 pairs, colon-separated) |
| Bridge token | contents of `corei-bridge.token` |

**Existing connection:** open it (Setup › Brokers & accounts › the server), set *Connection method* to
**MT5 terminal bridge**, fill in host, port and fingerprint, save; then *Credential* › **Terminal bridge token**.

Then open the account and click **Log in and read now**. Margin mode, leverage, group, symbol specifications
and the server clock come from the terminal. Add a plan under Market data › Plans; the collector streams
through the bridge and measures the broker's clock from the first tick.

## What each error means

| Shown on the account | Meaning | Fix |
| --- | --- | --- |
| MT5 terminal bridge not reachable | TCP connection to host:port failed | bridge not running, firewall, wrong host |
| MT5 terminal bridge certificate changed | the certificate at that address is not the pinned one | reinstalled bridge → copy the new fingerprint; otherwise investigate |
| MT5 terminal bridge token refused | token on the connection ≠ `corei-bridge.token` | save the current token |
| MT5 terminal could not log in | the terminal rejected login/password/server | log in by hand in the terminal; it shows the broker's message |
| Broker refused the login (…) | the broker rejected the account | wrong password, disabled account, wrong server |

## Security

- TLS 1.2+ with the bridge's self-signed certificate pinned by fingerprint: no CA, no MITM.
- A 256-bit random token in `corei-bridge.token` (file mode 600); COREI stores it encrypted, write-only.
- The firewall rule limits the port to COREI's IP. Never expose the bridge to the whole internet.
- The bridge logs connection times and peers only; it never prints a password or token.

## Limits today

- Market data and account reads. Order sending through the terminal (`order_send`) is the planned order
  adapter for stage 7; nothing sends yet.
- One login per terminal at a time; `symbol_info_tick` polling every 200 ms gives every tick for liquid
  symbols (the package returns all ticks since the last one, not just the latest).
