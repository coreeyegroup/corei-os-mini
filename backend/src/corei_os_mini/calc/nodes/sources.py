"""Source nodes: where a graph's numbers come from — the bars, the platform features, the signal library's scores,
constants, and the single values the caller supplies (quote, position, account, instrument, settings).

Why it exists: a graph must say where every number comes from, and must not be able to fetch anything itself. Source
nodes only read what the run was given (calc.nodes.Ctx); a value that was not supplied is an honest error that names
the node, never a guess or a zero.
Purpose: Bars (open, high, low, close, volume, ticks, spread), Feature (one column of intel.engine.features), Signal
score (one signal_library formula), Constant number / true-false / side, Quote, Position, Account, Instrument, Settings.
Must never: read the database, the clock or the network; turn a missing value into a number.
Dependencies: intel.engine (features), trading.signal_library (scores), calc.nodes. Consumers: calc.run.
Architectural position: the edge of a graph where data enters (Stage 1 and 2 outputs, Stage 5 account state).
"""
from __future__ import annotations

import json
from decimal import Decimal

from fastapi import HTTPException

from .. import types as T
from . import Ctx, NodeType, Param, Port, fmt, register

BAR_FIELDS = ('open', 'high', 'low', 'close', 'volume', 'ticks', 'spread')


def _bars(ctx: Ctx, ins: dict, p: dict) -> dict:
    if not ctx.bars:
        raise ValueError('No bars were supplied to this run.')
    b = ctx.bars
    ticks = [float(x.get('ticks') or 0) for x in b]
    real = [float(x.get('volume') or 0) for x in b]
    return {'open': [float(x['open']) for x in b], 'high': [float(x['high']) for x in b], 'low': [float(x['low']) for x in b],
            'close': [float(x['close']) for x in b], 'volume': real if any(real) else ticks, 'ticks': ticks,
            'spread': [None if x.get('spread_avg') is None else float(x['spread_avg']) for x in b]}


register(NodeType(
    'source.bars', 'sources', 'Bars', 'The closed bars of the instrument and timeframe being computed (oldest first). '
    'Volume is the real traded volume where the source has it, otherwise the tick count — as the platform features do.',
    [], [Port(f, T.SERIES_FLOAT, d) for f, d in (('open', 'opening price'), ('high', 'highest price'), ('low', 'lowest price'),
                                                  ('close', 'closing price'), ('volume', 'real volume, or ticks where there is none'),
                                                  ('ticks', 'tick count'), ('spread', 'average spread over the bar'))],
    [], _bars, lambda p: 'OHLC, volume, ticks and spread of each closed bar'))

FEATURES = ('close', 'ema20', 'ema50', 'ema200', 'rsi14', 'atr14', 'atr_pct', 'atr_pct_rank100', 'bb_mid', 'bb_upper', 'bb_lower',
            'bb_width_pct', 'adx14', 'plus_di', 'minus_di', 'er20', 'rv20_pct', 'range_pos20', 'ret_1_pct', 'ret_20_pct', 'macd',
            'macd_signal', 'macd_hist', 'stoch_k', 'stoch_d', 'donchian_high20', 'donchian_low20', 'zscore20', 'tick_volume', 'volume',
            'rel_volume20', 'vwap20', 'vwap_dev_pct', 'obv', 'spread_bar')


def _features(ctx: Ctx) -> list[dict]:
    if 'features' not in ctx.cache:
        from ...intel import engine
        ctx.cache['features'] = engine.features(ctx.bars, ctx.timeframe)
    return ctx.cache['features']


def _feature(ctx: Ctx, ins: dict, p: dict) -> dict:
    if not ctx.bars:
        raise ValueError('No bars were supplied to this run.')
    rows = _features(ctx)
    return {'value': [None if r.get(p['name']) is None else float(r[p['name']]) for r in rows]}


register(NodeType(
    'source.feature', 'sources', 'Feature', 'One column of the platform features (Stage 2, intel.engine.features), computed from the same bars.',
    [], [Port('value', T.SERIES_FLOAT, 'the feature on each bar')],
    [Param('name', 'choice', 'atr14', choices=FEATURES, doc='which feature')],
    _feature, lambda p: f"feature {p.get('name')}", lambda p: 200))


def _library():
    from ...trading import signal_library
    return signal_library


def _sig_check(p: dict) -> None:
    lib = _library()
    if p['kind'] not in lib.KINDS:
        raise ValueError(f"unknown signal formula {p['kind']}")
    try:
        settings = json.loads(p.get('settings') or '{}')
        if not isinstance(settings, dict):
            raise ValueError
    except ValueError:
        raise ValueError('settings must be a JSON object such as {"fast": 9, "slow": 21}') from None
    try:
        lib.params_of(p['kind'], settings)
    except HTTPException as exc:  # signal_library refuses settings with HTTP 422 and the reason
        raise ValueError(str(exc.detail)) from None


def _signal(ctx: Ctx, ins: dict, p: dict) -> dict:
    lib = _library()
    key = ('signal', p['kind'], p.get('settings') or '{}')
    if key not in ctx.cache:
        ctx.cache[key] = lib.compute(p['kind'], json.loads(p.get('settings') or '{}'), ctx.bars)
    return {'score': [None if x is None else x[0] for x in ctx.cache[key]]}


register(NodeType(
    'source.signal_score', 'sources', 'Signal score', 'A score from the signal library (Stage 3), −100…+100 per bar, with its own settings.',
    [], [Port('score', T.SERIES_FLOAT, 'the score on each bar')],
    [Param('kind', 'choice', 'EMA_TREND', choices=('EMA_TREND', 'SMA_TREND', 'MACD_MOMENTUM', 'RSI_REVERSION', 'RSI_MOMENTUM', 'BOLLINGER_REVERSION',
                                                    'ZSCORE_REVERSION', 'DONCHIAN_BREAKOUT', 'MOMENTUM_TSTAT', 'STOCHASTIC_REVERSION', 'ADX_DIRECTION',
                                                    'VWAP_REVERSION'), doc='the library formula'),
     Param('settings', 'text', '{}', doc='its settings as JSON, e.g. {"fast": 9, "slow": 21}; empty = the defaults')],
    _signal, lambda p: f"signal {p.get('kind')} {p.get('settings') or '{}'}", lambda p: 200, check=_sig_check))


def _const_number(ctx, ins, p):
    return {'value': float(p['value'])}


register(NodeType('source.constant', 'sources', 'Number', 'A fixed number. Type {{name}} to use a graph parameter instead.',
                  [], [Port('value', T.FLOAT)], [Param('value', 'float', 0.0, doc='the number, or {{parameter}}')],
                  _const_number, lambda p: fmt(p.get('value'))))
register(NodeType('source.constant_bool', 'sources', 'True / false', 'A fixed true or false.',
                  [], [Port('value', T.BOOL)], [Param('value', 'bool', True)], lambda c, i, p: {'value': p['value']}, lambda p: fmt(p.get('value'))))
register(NodeType('source.constant_side', 'sources', 'Side', 'A fixed side: LONG, SHORT, FLAT or HOLD.',
                  [], [Port('value', T.SIDE)], [Param('value', 'choice', 'FLAT', choices=T.SIDES)], lambda c, i, p: {'value': p['value']},
                  lambda p: str(p.get('value'))))


def _scalar(source: str, fields: list[tuple[str, str, str]], label: str, doc: str):
    def run(ctx: Ctx, ins: dict, p: dict) -> dict:
        data = ctx.scalars.get(source)
        if data is None:
            raise ValueError(f'This run was not given the {label.lower()} — preview it with a {label.lower()} or bind it where one exists.')
        out = {}
        for name, typ, _d in fields:
            v = data.get(name)
            if v is None:
                raise ValueError(f'The {label.lower()} has no {name}.')
            if typ in T.DECIMALS:
                if isinstance(v, float):
                    raise ValueError(f'The {label.lower()} {name} arrived as a binary float; money, prices and quantities must be exact decimals.')
                v = Decimal(str(v))
            elif typ == T.FLOAT:
                v = float(v)
            out[name] = v
        return out
    register(NodeType(f'source.{source}', 'sources', label, doc, [], [Port(n, t, d) for n, t, d in fields], [], run,
                      lambda p: f'{label.lower()}: ' + ', '.join(n for n, _t, _d in fields)))


_scalar('quote', [('bid', T.PRICE, 'best bid'), ('ask', T.PRICE, 'best offer'), ('mid', T.PRICE, '(bid + ask) ÷ 2'), ('spread', T.PRICE, 'ask − bid')],
        'Quote', 'The latest quote of the instrument, as exact decimals.')
_scalar('position', [('quantity', T.QUANTITY, 'signed: + long, − short'), ('side', T.SIDE, 'LONG, SHORT or FLAT'),
                     ('average_price', T.PRICE, 'average entry price'), ('unrealised', T.MONEY, 'open profit or loss in the account currency')],
        'Position', 'The current position of the deployment or account in this instrument.')
_scalar('account', [('equity', T.MONEY, 'equity'), ('balance', T.MONEY, 'balance'), ('free_margin', T.MONEY, 'free margin'), ('currency', T.TEXT, 'account currency')],
        'Account', 'The account the calculation is for.')
_scalar('instrument', [('tick_size', T.PRICE, 'smallest price step'), ('contract_size', T.QUANTITY, 'units per lot'),
                       ('min_quantity', T.QUANTITY, 'smallest order'), ('quantity_step', T.QUANTITY, 'order size step')],
        'Instrument', 'The instrument definition (Setup › Instruments).')


def _settings(ctx: Ctx, ins: dict, p: dict) -> dict:
    data = ctx.scalars.get('settings')
    if data is None or data.get(p['key']) is None:
        raise ValueError(f"The platform setting {p['key']} was not supplied to this run.")
    v = data[p['key']]
    if isinstance(v, bool) or not isinstance(v, (int, float, Decimal, str)):
        raise ValueError(f"The platform setting {p['key']} is not a number.")
    return {'value': float(v)}


register(NodeType('source.settings', 'sources', 'Setting', 'One numeric platform setting (Setup › Settings), as a number for analytics.',
                  [], [Port('value', T.FLOAT)], [Param('key', 'text', 'max_spread_points', doc='the setting name')], _settings,
                  lambda p: f"setting {p.get('key')}"))
