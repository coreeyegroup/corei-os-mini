"""Pre-send order checks against the account's own symbol rules (audit blocker 2).

Owns: one pure function that every order passes before it is sent, so an order the
broker would reject (wrong lot step, unsupported filling mode, stop too close, market
closed by trade mode) is stopped inside COREI with a clear reason. The specification
comes from the account's latest snapshot (read at login, or typed and marked MANUAL).

MT5 note: the symbol's filling flags (SYMBOL_FILLING_FOK=1, SYMBOL_FILLING_IOC=2) are
not the same numbers as the order's filling types (ORDER_FILLING_FOK=0, IOC=1,
RETURN=2). Mixing them is a known cause of error 10030; this module keeps them apart.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Positive = Annotated[Decimal, Field(gt=0, max_digits=38, decimal_places=12)]


class SymbolSpec(BaseModel):
    model_config = ConfigDict(extra='allow')
    digits: Annotated[int, Field(ge=0, le=12)]
    volume_min: Positive
    volume_max: Positive
    volume_step: Positive
    # Filling types the symbol allows for market/instant orders: FOK, IOC, RETURN.
    filling_modes: Annotated[list[Literal['FOK', 'IOC', 'RETURN', 'BOC']], Field(min_length=1)]
    # Minimum distance of stop loss / take profit from the price, in points.
    stops_level_points: Annotated[int, Field(ge=0)] = 0
    # Inside this distance (points) open orders and positions cannot be changed.
    freeze_level_points: Annotated[int, Field(ge=0)] = 0
    trade_mode: Literal['FULL', 'LONG_ONLY', 'SHORT_ONLY', 'CLOSE_ONLY', 'DISABLED'] = 'FULL'
    contract_size: Positive | None = None
    margin_currency: str | None = None


class OrderDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    broker_symbol: Annotated[str, Field(min_length=1, max_length=64)]
    side: Literal['BUY', 'SELL']
    volume: Positive
    order_type: Literal['MARKET', 'LIMIT', 'STOP'] = 'MARKET'
    filling: Literal['FOK', 'IOC', 'RETURN', 'BOC'] | None = None
    price: Positive | None = None
    reference_price: Positive | None = None  # current bid/ask, for distance checks
    stop_loss: Positive | None = None
    take_profit: Positive | None = None
    closes_position: bool = False


# MT5 SYMBOL_FILLING_* bit flags → names (for adapters converting broker data).
SYMBOL_FILLING_FLAGS = {1: 'FOK', 2: 'IOC', 4: 'BOC'}


def filling_modes_from_flags(flags: int, execution_mode_market: bool) -> list[str]:
    modes = [name for bit, name in SYMBOL_FILLING_FLAGS.items() if flags & bit]
    # RETURN is allowed for exchange/market execution except when only FOK is offered.
    if not execution_mode_market or not modes:
        modes.append('RETURN')
    return modes or ['RETURN']


def choose_filling(spec: SymbolSpec, preferred: str | None) -> str:
    if preferred and preferred in spec.filling_modes:
        return preferred
    for name in ('IOC', 'FOK', 'RETURN'):
        if name in spec.filling_modes:
            return name
    return spec.filling_modes[0]


def check_order(draft: OrderDraft, spec_row: dict | None, account: dict | None = None) -> dict:
    """Returns {'ok', 'problems': [...], 'filling'} — never sends anything."""
    problems: list[str] = []
    if account is not None:
        if account.get('status') != 'ACTIVE':
            problems.append('Account is not active.')
        if account.get('trading_blockers'):
            problems.append('Account is not ready to trade: ' + ', '.join(account['trading_blockers']))
    if not spec_row:
        problems.append(f'No symbol specification is known for {draft.broker_symbol} on this account. '
                        'Read it from the broker (Test login) or enter it before trading.')
        return {'ok': False, 'problems': problems, 'filling': None, 'spec_read_at': None}
    spec = SymbolSpec.model_validate(spec_row['spec'] if 'spec' in spec_row else spec_row)
    v = draft.volume
    if v < spec.volume_min:
        problems.append(f'Volume {v} is below the minimum {spec.volume_min}.')
    if v > spec.volume_max:
        problems.append(f'Volume {v} is above the maximum {spec.volume_max}.')
    if (v - spec.volume_min) % spec.volume_step != 0:
        problems.append(f'Volume {v} is not a multiple of the step {spec.volume_step} (from {spec.volume_min}).')
    if spec.trade_mode == 'DISABLED':
        problems.append('Trading is disabled for this symbol on this account.')
    elif spec.trade_mode == 'CLOSE_ONLY' and not draft.closes_position:
        problems.append('This symbol is close-only on this account: new positions cannot be opened.')
    elif spec.trade_mode == 'LONG_ONLY' and draft.side == 'SELL' and not draft.closes_position:
        problems.append('This symbol is long-only on this account.')
    elif spec.trade_mode == 'SHORT_ONLY' and draft.side == 'BUY' and not draft.closes_position:
        problems.append('This symbol is short-only on this account.')
    filling = None
    if draft.order_type == 'MARKET':
        if draft.filling and draft.filling not in spec.filling_modes:
            problems.append(f"Filling {draft.filling} is not allowed for this symbol on this account "
                            f"(allowed: {', '.join(spec.filling_modes)}).")
        filling = choose_filling(spec, draft.filling)
    point = Decimal(1).scaleb(-spec.digits)
    base = draft.price if draft.order_type != 'MARKET' else draft.reference_price
    for label, level in (('Stop loss', draft.stop_loss), ('Take profit', draft.take_profit)):
        if level is None:
            continue
        if _places(level) > spec.digits:
            problems.append(f'{label} {level} has more than {spec.digits} decimals.')
        if base is not None and spec.stops_level_points and abs(level - base) < spec.stops_level_points * point:
            problems.append(f'{label} is closer than the broker minimum of {spec.stops_level_points} points from the price.')
    for label, price in (('Price', draft.price),):
        if price is not None and _places(price) > spec.digits:
            problems.append(f'{label} {price} has more than {spec.digits} decimals.')
    if draft.order_type != 'MARKET' and draft.price is None:
        problems.append('A limit or stop order needs a price.')
    return {'ok': not problems, 'problems': problems, 'filling': filling, 'spec_read_at': spec_row.get('read_at'),
            'spec_source': spec_row.get('source')}


def _places(value: Decimal) -> int:
    exponent = value.normalize().as_tuple().exponent
    return max(0, -exponent) if isinstance(exponent, int) else 0
