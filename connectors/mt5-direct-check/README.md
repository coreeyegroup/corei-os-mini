# MT5 server-file reader

Used by the authenticated **Accounts → Add broker account** form. Parses
`servers.dat` locally and returns server names and named access points. It never
connects to an account, accepts a password or calls a trading method.

The broker-provided server name is saved separately from the selected access
point. The backend validates membership against its imported catalogue; the
browser cannot supply an arbitrary host/port in an account save request.

The reader uses the vendor-linked `mtapi.be.mt5net` 5.4850.0 package. Its checksum
and the .NET SDK checksum are pinned in `scripts/setup-broker-accounts.sh`.
The SDK is fetched from NuGet during installation; no proprietary binary or
uploaded server file is committed. The vendor trial is not a production trading
licence. Unsupported file formats, including extended access records without a
readable endpoint in this SDK, fail explicitly without saving an account.

## Ubuntu update (normal workstation deployment user)

```bash
cd /home/coreeye/corei-os-mini
git pull --ff-only origin main
bash scripts/setup-broker-accounts.sh
bash scripts/configure-workstation-database.sh
bash scripts/deploy-workstation.sh
```

Setup requires outbound HTTPS to Microsoft's .NET distribution and NuGet,
Ubuntu x86_64, curl, tar, python3 and sudo. It publishes a self-contained parser
under `/opt/corei-os-mini/` without changing system .NET installations.

The encryption key is `/etc/corei-os-mini/broker-credentials.key`, readable only
by the workstation service user (0600). Setup preserves an existing key and
checks its saved fingerprint. Keep a secure separate backup of this key together
with database backups. Do not delete or regenerate it during an update. Losing
the key makes saved passwords unrecoverable. Key rotation is not implemented.

## Operator flow

Open Accounts, sign in, choose Add broker account. Enter the account label,
broker name, Demo/Live, login, password and exact broker-provided server name.
Browse `servers.dat`, select an access point from the matching server, and save.
The account is saved as disabled configuration and not connected. Enable/disable
changes configuration only. Edit allows label/access-point changes and optional
password replacement; an empty password field retains the stored password.
Broker, login, environment and server identity remain fixed; add a new account
for a different identity. Earlier registry-only records remain accessible under
Earlier account records. They are not automatically rebound to credentials.

One transaction creates the canonical provider/connection/account/profile and
secret-reference records. A duplicate server/login rolls the transaction back.
Optimistic versions reject stale edits. Passwords use AES-256-GCM with a random
nonce and secret-ID-bound authenticated data. HTTP responses never expose the
password or ciphertext; there is no read-secret endpoint.

## Validation

```bash
python -m unittest discover -s backend/tests -v
npm test --prefix ui
```

Set `COREI_TEST_DATABASE_URL` to a disposable PostgreSQL database to run the
transaction/migration tests. They create and remove a uniquely named schema.
CI runs those tests against PostgreSQL 18. For local parser development:

```bash
dotnet publish connectors/mt5-direct-check/ServerCatalog.csproj -c Release \
  -r linux-x64 --self-contained true -p:Mt5SdkPath=/absolute/path/to/mt5api.dll
```

Override `COREI_MT5_CATALOG_PARSER` and `COREI_BROKER_KEY_FILE` only for local tests
or an explicitly configured deployment. The uploaded fixture was verified as
one `MetaQuotes-Demo` server with 50 access points. No login/session/trading tests
are part of this storage-only increment.
