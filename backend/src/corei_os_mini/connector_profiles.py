"""Connector configuration only. No credentials, sessions or execution adapters."""
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
OptionalLabel = Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)]

class ConnectorProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: UUID
    name: Label
    connector_type: Literal['MT4_EA','MT5_EA','MT5_DIRECT','CTRADER_OPEN_API','BROKER_API','EXCHANGE_API','FIX','DATA_FEED','CUSTOM']
    adapter_key: Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_.-]{0,63}$')]
    environment: Literal['PAPER','SANDBOX','LIVE']
    runtime: Literal['WINDOWS_TERMINAL','UBUNTU_WINE_TERMINAL','SERVER_API']
    server_name: OptionalLabel = ''
    terminal_host: OptionalLabel = ''
    terminal_instance: OptionalLabel = ''
    enabled: bool = False

    @model_validator(mode='after')
    def compatible_runtime(self):
        terminal = self.connector_type in ('MT4_EA','MT5_EA')
        if terminal and self.runtime == 'SERVER_API':
            raise ValueError('MetaTrader EA profiles require a terminal runtime')
        if not terminal and self.runtime != 'SERVER_API':
            raise ValueError('This connector family requires SERVER_API')
        direct = self.connector_type == 'MT5_DIRECT'
        if direct and (self.terminal_host or self.terminal_instance):
            raise ValueError('Direct MT5 connections do not use terminal inventory labels')
        if (terminal or direct) and not self.server_name:
            raise ValueError('MetaTrader profiles require the broker server name')
        return self


def profile_view(row):
    # These are server-derived facts, never editable configuration flags.
    return {**row, 'adapter_status': 'NOT_IMPLEMENTED',
            'session_status': 'NOT_CONNECTED', 'account_verified': False,
            'data_health': 'NOT_OBSERVED', 'execution_available': False}
