"""Validated input contracts for the configuration domain (Blueprint v1.2 §8).

Owns: field-level rules for providers, connections, accounts, instruments, symbol
mappings, credentials and execution controls. Rules that need other records (for
example "TRADING usage requires a trading connection") live in config_store.
Financial quantities are Decimal (exact), never float.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import ipaddress
import re
from typing import Annotated, Literal, Union
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr, StringConstraints, field_validator, model_validator

def _upper(value: object) -> object:
    return value.strip().upper() if isinstance(value, str) else value


Upper = BeforeValidator(_upper)
Code = Annotated[str, Upper, StringConstraints(pattern=r'^[A-Z][A-Z0-9_]{1,31}$')]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Currency = Annotated[str, Upper, StringConstraints(pattern=r'^[A-Z0-9]{2,10}$')]
Status = Literal['ACTIVE', 'INACTIVE']
# Providers, connections and accounts are archived (kept, read-only) once they have history.
RecordStatus = Literal['ACTIVE', 'INACTIVE', 'ARCHIVED']
Text = lambda n: Annotated[str, StringConstraints(strip_whitespace=True, max_length=n)]  # noqa: E731
Purpose = Literal['MARKET_DATA', 'TRADING', 'BOTH']
Amount = Annotated[Decimal, Field(gt=0, max_digits=38, decimal_places=12)]
Secret = Annotated[SecretStr, Field(min_length=1, max_length=4096)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Holiday(_Strict):
    date: Annotated[str, StringConstraints(pattern=r'^\d{4}-\d{2}-\d{2}$')]
    name: Text(120) = ''


class ProviderIn(_Strict):
    code: Code
    name: Name
    kind: Literal['BROKER', 'EXCHANGE', 'ECN', 'PRIME_BROKER', 'LIQUIDITY_PROVIDER', 'FAST_FEED', 'DATA_VENDOR', 'DATA_CENTRE']
    status: RecordStatus = 'ACTIVE'
    # The firm behind the brand: the same brand often has several legal entities.
    legal_entity: Text(200) = ''
    regulator: Text(120) = ''
    licence_number: Text(80) = ''
    jurisdiction: Text(80) = ''
    execution_model: Literal['UNKNOWN', 'MARKET_MAKER', 'STP', 'ECN', 'EXCHANGE', 'MIXED'] = 'UNKNOWN'
    website: Text(200) = ''
    support_contact: Text(300) = ''
    dealing_contact: Text(300) = ''
    # Most capital COREI may hold at this firm across all its accounts.
    exposure_limit: Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=2)] | None = None
    exposure_currency: Annotated[str, Upper, StringConstraints(pattern=r'^([A-Z]{3})?$')] = ''
    holidays: Annotated[list[Holiday], Field(max_length=400)] = Field(default_factory=list)

    @model_validator(mode='after')
    def exposure_has_currency(self) -> 'ProviderIn':
        if self.exposure_limit is not None and not self.exposure_currency:
            raise ValueError('exposure_currency is required with exposure_limit')
        return self


# ---------- Connection settings, one shape per platform ----------

_HOST = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$')


class Mt5AccessPoint(_Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    host: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)]
    # Disabled access points stay on record but the adapter does not try them.
    enabled: bool = True
    # Where this address came from. MANUAL addresses on LIVE need a second person
    # (four-eyes), because the login password is sent to whatever address is used.
    source: Literal['DIRECTORY', 'SERVERS_DAT', 'BROKER', 'MANUAL', 'UNKNOWN'] = 'UNKNOWN'
    confirmed_at: Annotated[str, StringConstraints(max_length=40)] | None = None

    @field_validator('host')
    @classmethod
    def valid_host(cls, value: str) -> str:
        try:
            return str(ipaddress.ip_address(value.strip('[]')))
        except ValueError:
            if not _HOST.fullmatch(value):
                raise ValueError('host must be a hostname or IP address') from None
            return value


MAX_ACCESS_POINTS = 256


class BridgeSettings(_Strict):
    """Where the COREI MT5 terminal bridge listens (connectors/mt5-bridge). Its self-signed certificate is pinned
    by fingerprint; the token is a write-only credential (BRIDGE_TOKEN) on the connection."""
    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=253, pattern=r'^[A-Za-z0-9.\-:\[\]]+$')]
    port: Annotated[int, Field(ge=1, le=65535)] = 9443
    fingerprint: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')]


class Mt5Settings(_Strict):
    server_name: Name
    # Set when the connection method is TERMINAL_BRIDGE: the official MT5 terminal logs in; COREI reads from it.
    bridge: BridgeSettings | None = None
    # Company and every other field the broker's servers.dat states for this server,
    # kept as read so operators can confirm they picked the right server.
    company_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None
    server_details: dict[str, str | int | float | bool] = Field(default_factory=dict)
    # Ordered by preference; the adapter tries enabled ones in this order. Large broker
    # servers publish dozens (MetaQuotes-Demo lists 50), so allow up to 256.
    access_points: Annotated[list[Mt5AccessPoint], Field(min_length=1, max_length=MAX_ACCESS_POINTS)]

    @field_validator('server_details', mode='before')
    @classmethod
    def valid_details(cls, value: object) -> dict:
        from .broker_catalog import server_details
        try:
            return server_details(value if value is not None else {})
        except ValueError as exc:
            raise ValueError(f'server_details: {exc}') from None

    @model_validator(mode='after')
    def unique_endpoints(self) -> 'Mt5Settings':
        endpoints = [(p.host, p.port) for p in self.access_points]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError('access points must be unique by host and port')
        if not any(p.enabled for p in self.access_points):
            raise ValueError('at least one access point must be selected')
        return self


def _url(value: str, schemes: tuple[str, ...], local_http: bool = False) -> str:
    parsed = urlparse(value)
    local = local_http and parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')
    if (parsed.scheme not in schemes and not local) or not parsed.hostname or parsed.username or parsed.password:
        allowed = ' or '.join(schemes) + (' (or http://localhost for local testing)' if local_http else '')
        raise ValueError(f'must be a {allowed} URL without embedded credentials')
    return value.rstrip('/')


class RestApiSettings(_Strict):
    api_base_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
    stream_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    auth_method: Literal['API_KEY', 'OAUTH2']
    # OAuth: the exact redirect URL registered on the broker app (Upstox requires one).
    oauth_redirect_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    # Optional endpoint the broker posts tokens or order updates to (Upstox notifier webhook).
    notifier_webhook_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None

    @field_validator('api_base_url', 'notifier_webhook_url')
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        return None if not value else _url(value, ('https',))

    @field_validator('oauth_redirect_url')
    @classmethod
    def redirect_url(cls, value: str | None) -> str | None:
        return None if not value else _url(value, ('https',), local_http=True)

    @field_validator('stream_url')
    @classmethod
    def wss_only(cls, value: str | None) -> str | None:
        return None if value is None else _url(value, ('wss',))

    @model_validator(mode='after')
    def oauth_needs_redirect(self) -> 'RestApiSettings':
        if self.auth_method == 'OAUTH2' and not self.oauth_redirect_url:
            raise ValueError('OAUTH2 requires oauth_redirect_url')
        if self.auth_method == 'API_KEY' and self.oauth_redirect_url:
            raise ValueError('oauth_redirect_url is only used with OAUTH2')
        return self


class FixSettings(_Strict):
    """A FIX 4.4 session (broker, prime broker, ECN, fast feed): where it is and who we are on it."""
    host: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)]
    tls: bool = True
    tls_verify: bool = True
    begin_string: Literal['FIX.4.4', 'FIX.4.2'] = 'FIX.4.4'
    sender_comp_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[!-~]{1,64}$')]
    target_comp_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[!-~]{1,64}$')]
    sender_sub_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[!-~]{0,64}$')] = ''
    target_sub_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[!-~]{0,64}$')] = ''
    heartbeat_seconds: Annotated[int, Field(ge=5, le=120)] = 30
    reset_seq_on_logon: bool = True
    # Market data: how deep to subscribe (1 = top of book) and whether updates come as full snapshots.
    md_depth: Annotated[int, Field(ge=1, le=10)] = 1
    md_snapshots_only: bool = False

    @field_validator('host')
    @classmethod
    def valid_host(cls, value: str) -> str:
        return Mt5AccessPoint.valid_host(value)


SETTINGS_BY_PLATFORM = {'MT5': Mt5Settings, 'REST_API': RestApiSettings, 'FIX': FixSettings}


class MaintenanceWindow(_Strict):
    weekday: Annotated[int, Field(ge=0, le=6)]  # 0 = Monday
    start: Annotated[str, StringConstraints(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')]
    end: Annotated[str, StringConstraints(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')]
    timezone: Literal['UTC', 'SERVER'] = 'SERVER'
    note: Text(120) = ''


ADAPTERS = {'MT5': {'DEFAULT', 'MTAPI_DIRECT', 'TERMINAL_BRIDGE'}, 'REST_API': {'DEFAULT', 'NATIVE'}, 'FIX': {'DEFAULT', 'NATIVE'}}


class ConnectionIn(_Strict):
    provider_id: UUID
    code: Code
    name: Name
    platform: Literal['MT5', 'REST_API', 'FIX']
    environment: Literal['DEMO', 'LIVE']
    purpose: Purpose
    settings: dict
    status: RecordStatus = 'ACTIVE'
    # How COREI reaches the platform. Both MT5 methods stay available (DEC-29).
    adapter: Literal['DEFAULT', 'MTAPI_DIRECT', 'TERMINAL_BRIDGE', 'NATIVE'] = 'DEFAULT'
    # Broker server clock: base offset from UTC (minutes) and daylight-saving rule.
    server_utc_offset_minutes: Annotated[int, Field(ge=-720, le=840)] | None = None
    dst_rule: Literal['UNKNOWN', 'NONE', 'US', 'EU'] = 'UNKNOWN'
    clock_source: Literal['UNKNOWN', 'MANUAL', 'SERVER_FILE', 'BROKER_LOGIN'] = 'UNKNOWN'
    max_orders_per_second: Annotated[Decimal, Field(gt=0, max_digits=8, decimal_places=2)] | None = None
    max_order_burst: Annotated[int, Field(ge=1, le=1000)] | None = None
    # API apps that require IP whitelisting (SEBI framework): the IP registered with the broker.
    registered_egress_ip: str | None = None
    maintenance: Annotated[list[MaintenanceWindow], Field(max_length=50)] = Field(default_factory=list)

    @field_validator('registered_egress_ip')
    @classmethod
    def valid_ip(cls, value: str | None) -> str | None:
        if not value:
            return None
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError:
            raise ValueError('registered_egress_ip must be an IP address') from None

    @model_validator(mode='after')
    def settings_match_platform(self) -> 'ConnectionIn':
        model = SETTINGS_BY_PLATFORM[self.platform]
        self.settings = model.model_validate(self.settings).model_dump(mode='json')
        if self.adapter not in ADAPTERS[self.platform]:
            raise ValueError(f'adapter {self.adapter} does not apply to {self.platform}')
        return self


class AddressRef(_Strict):
    """One address (host and port) of an MT5 server, referenced by an account."""
    host: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)]

    @field_validator('host')
    @classmethod
    def valid_host(cls, value: str) -> str:
        return Mt5AccessPoint.valid_host(value)


AccessMode = Literal['SERVER', 'FASTEST', 'ACCOUNT']


class AccountIn(_Strict):
    connection_id: UUID
    code: Code
    name: Name
    external_account_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    # Person or legal entity that owns the account at the broker.
    holder_name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ''
    base_currency: Currency
    trading_permission: Literal['DISABLED', 'READ_ONLY', 'TRADING'] = 'DISABLED'
    status: RecordStatus = 'ACTIVE'
    # Facts the broker decides. Typed values are marked MANUAL; the adapter overwrites
    # them with what it reads at login (marked BROKER, with the time read).
    margin_mode: Literal['UNKNOWN', 'HEDGING', 'NETTING', 'EXCHANGE'] = 'UNKNOWN'
    broker_group: Text(120) = ''
    leverage: Annotated[int, Field(ge=1, le=10000)] | None = None
    stop_out_level: Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=2)] | None = None
    max_open_orders: Annotated[int, Field(ge=1)] | None = None
    max_open_positions: Annotated[int, Field(ge=1)] | None = None
    expires_on: date | None = None
    password_changed_on: date | None = None
    # Whose money: OWN, FIRM (Coreeye Group) or CLIENT (needs a mandate reference).
    relationship: Literal['OWN', 'FIRM', 'CLIENT'] = 'OWN'
    mandate_reference: Text(200) = ''
    mandate_expires_on: date | None = None
    # Other systems known to use this login (MT5 terminal, EA, copier).
    other_users: Text(300) = ''
    group_ids: Annotated[list[UUID], Field(max_length=50)] = Field(default_factory=list)
    # Which of the server's addresses this account uses (MT5 only). See mt5_routing.
    access_mode: AccessMode = 'SERVER'
    # The account's own ordered choice; kept when switching modes so nothing is lost.
    access_points: Annotated[list[AddressRef], Field(max_length=MAX_ACCESS_POINTS)] = Field(default_factory=list)

    @model_validator(mode='after')
    def own_addresses(self) -> 'AccountIn':
        keys = [(p.host, p.port) for p in self.access_points]
        if len(keys) != len(set(keys)):
            raise ValueError('access_points must not repeat an address')
        if self.access_mode == 'ACCOUNT' and not keys:
            raise ValueError('choose at least one address for this account, or use the server\'s addresses')
        return self


def _places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0


class InstrumentIn(_Strict):
    symbol: Annotated[str, Upper, StringConstraints(pattern=r'^[A-Z0-9][A-Z0-9._/-]{0,31}$')]
    name: Name
    asset_class: Literal['FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION']
    base_currency: Currency
    quote_currency: Currency
    price_precision: Annotated[int, Field(ge=0, le=12)]
    quantity_precision: Annotated[int, Field(ge=0, le=12)]
    tick_size: Amount
    min_quantity: Amount
    quantity_step: Amount
    contract_size: Amount
    contract_unit: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32)]
    trading_session: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ''
    status: Status = 'ACTIVE'

    @model_validator(mode='after')
    def precision_is_consistent(self) -> 'InstrumentIn':
        if _places(self.tick_size) > self.price_precision:
            raise ValueError('tick_size has more decimal places than price_precision')
        for field in ('min_quantity', 'quantity_step'):
            if _places(getattr(self, field)) > self.quantity_precision:
                raise ValueError(f'{field} has more decimal places than quantity_precision')
        if self.min_quantity % self.quantity_step != 0:
            raise ValueError('min_quantity must be a whole multiple of quantity_step')
        return self


class MappingIn(_Strict):
    connection_id: UUID
    # Set for a mapping that applies to one account only (e.g. XAUUSD.r on a raw account);
    # it overrides the connection-wide mapping for that account.
    account_id: UUID | None = None
    instrument_id: UUID
    broker_symbol: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    usage: Purpose
    broker_contract_size: Amount | None = None
    broker_price_digits: Annotated[int, Field(ge=0, le=12)] | None = None
    status: Status = 'ACTIVE'


# ---------- Credentials (write-only) ----------

class ApiKeyCredential(_Strict):
    kind: Literal['API_KEY']
    api_key: Secret
    api_secret: Secret


class OAuthClientCredential(_Strict):
    kind: Literal['OAUTH_CLIENT']
    client_id: Secret
    client_secret: Secret


class BridgeTokenCredential(_Strict):
    kind: Literal['BRIDGE_TOKEN']
    token: Secret


class FixLoginCredential(_Strict):
    """FIX session login (Logon tags 553/554). Some venues authenticate by IP and CompIDs only: then none is saved."""
    kind: Literal['FIX_LOGIN']
    username: Secret
    password: Secret


class Mt5LoginCredential(_Strict):
    kind: Literal['MT5_LOGIN']
    password: Secret
    # FULL = master password (can trade); READ_ONLY = investor password (data/state only).
    access_level: Literal['FULL', 'READ_ONLY'] = 'FULL'


CredentialIn = Annotated[Union[ApiKeyCredential, OAuthClientCredential, Mt5LoginCredential, BridgeTokenCredential, FixLoginCredential], Field(discriminator='kind')]


class CredentialEnvelope(_Strict):
    credential: CredentialIn


# ---------- Execution control ----------

class ExecutionControlIn(_Strict):
    trading_enabled: bool
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
    version: Annotated[int, Field(ge=1)]
    # Must repeat the environment name to turn sending ON (guards against misclicks).
    confirm_environment: Literal['DEMO', 'LIVE'] | None = None


class VersionIn(_Strict):
    version: Annotated[int, Field(ge=1)]


# ---------- MT5 one-step setup and latency ----------

class Mt5AccountSetupIn(_Strict):
    """Add an MT5 account the way MT5 does it: login, password, server. The server's
    connection is reused when one already exists for this provider, environment and
    server name, otherwise it is created in the same transaction."""
    provider_id: UUID
    environment: Literal['DEMO', 'LIVE']
    server: Mt5Settings
    # Optional explicit connection; must match provider, environment and server name.
    connection_id: UUID | None = None
    # Addresses picked here that the existing connection lacks are added to it (unticked
    # for the server, so other accounts are unaffected).
    add_missing_addresses: bool = True
    account: dict
    credential: Mt5LoginCredential | None = None
    # How COREI reaches the server: through the official MT5 terminal (server.bridge set, bridge_token given)
    # or the direct SDK. Applies when the connection is created; an existing connection keeps its method.
    adapter: Literal['TERMINAL_BRIDGE', 'MTAPI_DIRECT', 'DEFAULT'] = 'DEFAULT'
    bridge_token: Secret | None = None

    @model_validator(mode='after')
    def bridge_complete(self) -> 'Mt5AccountSetupIn':
        if self.adapter == 'TERMINAL_BRIDGE' and self.server.bridge is None:
            raise ValueError('bridge: the terminal bridge needs its host, port and certificate fingerprint')
        return self


class LatencyIn(_Strict):
    addresses: Annotated[list[AddressRef], Field(min_length=1, max_length=64)]
    # When given, results are recorded against this connection's history.
    connection_id: UUID | None = None


# ---------- Account groups, policies, approvals, alerts ----------

class AccountGroupIn(_Strict):
    code: Code
    name: Name
    description: Text(500) = ''
    status: RecordStatus = 'ACTIVE'


class PolicyIn(_Strict):
    value: bool | int
    version: Annotated[int, Field(ge=1)]
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class DecisionIn(_Strict):
    approve: bool
    note: Text(500) = ''


ALERT_EVENTS = ('LIVE_CHANGE', 'APPROVAL_NEEDED', 'ROUTE_FALLBACK', 'LOGIN_FAILED', 'EXPIRY', 'ORDER_SENDING', 'MARKET_DATA', 'DATA_INTEGRITY', 'TEST')


class AlertChannelIn(_Strict):
    kind: Literal['EMAIL', 'TELEGRAM', 'WEBHOOK']
    name: Name
    # EMAIL: address; TELEGRAM: chat id; WEBHOOK: https URL.
    target: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    events: Annotated[list[Literal['LIVE_CHANGE', 'APPROVAL_NEEDED', 'ROUTE_FALLBACK', 'LOGIN_FAILED', 'EXPIRY', 'ORDER_SENDING', 'MARKET_DATA', 'DATA_INTEGRITY']],
                      Field(min_length=1)]
    status: Status = 'ACTIVE'
    # TELEGRAM bot token or WEBHOOK signing secret; write-only, stored encrypted.
    secret: Secret | None = None

    @model_validator(mode='after')
    def target_matches_kind(self) -> 'AlertChannelIn':
        if self.kind == 'EMAIL' and not re.fullmatch(r'[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}', self.target):
            raise ValueError('EMAIL target must be an email address')
        if self.kind == 'TELEGRAM' and not re.fullmatch(r'-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,31}', self.target):
            raise ValueError('TELEGRAM target must be a chat id or @channel')
        if self.kind == 'WEBHOOK':
            self.target = _url(self.target, ('https',))
        return self


class VenueSetupIn(_Strict):
    """One step: who (an existing provider, or a new one) plus its server / session / API connection."""
    provider_id: UUID | None = None
    provider: ProviderIn | None = None
    connection: dict

    @model_validator(mode='after')
    def one_provider(self) -> 'VenueSetupIn':
        if (self.provider_id is None) == (self.provider is None):
            raise ValueError('Choose an existing provider or describe a new one (not both).')
        return self
