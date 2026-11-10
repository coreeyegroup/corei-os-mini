"""Validated input contracts for the configuration domain (Blueprint v1.2 §8).

Owns: field-level rules for providers, connections, accounts, instruments, symbol
mappings, credentials and execution controls. Rules that need other records (for
example "TRADING usage requires a trading connection") live in config_store.
Financial quantities are Decimal (exact), never float.
"""
from __future__ import annotations

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
Purpose = Literal['MARKET_DATA', 'TRADING', 'BOTH']
Amount = Annotated[Decimal, Field(gt=0, max_digits=38, decimal_places=12)]
Secret = Annotated[SecretStr, Field(min_length=1, max_length=4096)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ProviderIn(_Strict):
    code: Code
    name: Name
    kind: Literal['BROKER', 'EXCHANGE', 'DATA_VENDOR']
    status: Status = 'ACTIVE'


# ---------- Connection settings, one shape per platform ----------

_HOST = re.compile(r'^[A-Za-z0-9]([A-Za-z0-9-]{0,62})(\.[A-Za-z0-9]([A-Za-z0-9-]{0,62}))*$')


class Mt5AccessPoint(_Strict):
    name: Name
    host: Annotated[str, StringConstraints(strip_whitespace=True, to_lower=True, min_length=1, max_length=253)]
    port: Annotated[int, Field(ge=1, le=65535)]

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


class Mt5Settings(_Strict):
    server_name: Name
    # Ordered by preference; the adapter tries them in this order. Large broker
    # servers publish dozens (MetaQuotes-Demo lists 50), so allow up to 256.
    access_points: Annotated[list[Mt5AccessPoint], Field(min_length=1, max_length=MAX_ACCESS_POINTS)]

    @model_validator(mode='after')
    def unique_endpoints(self) -> 'Mt5Settings':
        endpoints = [(p.host, p.port) for p in self.access_points]
        if len(endpoints) != len(set(endpoints)):
            raise ValueError('access points must be unique by host and port')
        return self


def _url(value: str, schemes: tuple[str, ...]) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in schemes or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f'must be a {" or ".join(schemes)} URL without embedded credentials')
    return value.rstrip('/')


class RestApiSettings(_Strict):
    api_base_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
    stream_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None
    auth_method: Literal['API_KEY', 'OAUTH2']
    oauth_redirect_url: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] | None = None

    @field_validator('api_base_url', 'oauth_redirect_url')
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        return None if value is None else _url(value, ('https',))

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


SETTINGS_BY_PLATFORM = {'MT5': Mt5Settings, 'REST_API': RestApiSettings}


class ConnectionIn(_Strict):
    provider_id: UUID
    code: Code
    name: Name
    platform: Literal['MT5', 'REST_API']
    environment: Literal['DEMO', 'LIVE']
    purpose: Purpose
    settings: dict
    status: Status = 'ACTIVE'

    @model_validator(mode='after')
    def settings_match_platform(self) -> 'ConnectionIn':
        model = SETTINGS_BY_PLATFORM[self.platform]
        self.settings = model.model_validate(self.settings).model_dump(mode='json')
        return self


class AccountIn(_Strict):
    connection_id: UUID
    code: Code
    name: Name
    external_account_id: Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[A-Za-z0-9_.:-]{1,64}$')]
    base_currency: Currency
    trading_permission: Literal['DISABLED', 'READ_ONLY', 'TRADING'] = 'DISABLED'
    status: Status = 'ACTIVE'


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


class Mt5LoginCredential(_Strict):
    kind: Literal['MT5_LOGIN']
    password: Secret
    # FULL = master password (can trade); READ_ONLY = investor password (data/state only).
    access_level: Literal['FULL', 'READ_ONLY'] = 'FULL'


CredentialIn = Annotated[Union[ApiKeyCredential, OAuthClientCredential, Mt5LoginCredential], Field(discriminator='kind')]


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
