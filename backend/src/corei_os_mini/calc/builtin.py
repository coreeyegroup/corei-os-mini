"""Built-in calculation graphs: the platform's own formulas drawn as graphs, read-only and APPROVED, each with the
code it must equal (its parity reference).

Why it exists: built-ins show every desk exactly how the platform computes — node by node, with live numbers — and
are the starting point for a copy the desk can change. A built-in graph replaces formula text only because a test
proves it equals the code it draws, to 1e-9, on every bar (tests/test_calc.py, DEC-93).
Purpose: `BUILTINS` — code → {name, purpose, family, description, parity_ref, kind, definition}. The 12 signal-library
formulas (SIG_<KIND>), each with the same parameters and defaults as the library and the same values shown with
the score.
Must never: change without its parity test; be edited in place (a change is a copy, a new graph or a new version).
Dependencies: none (plain data). Consumers: calc.store, calc_api, the signal engine (graph-bound signals), tests.
Architectural position: reference graphs of Calculation Graphs.
"""
from __future__ import annotations


class _G:
    """A small builder so each built-in reads like its formula."""

    def __init__(self, notes: str, params: list[tuple]):
        self.d = {'purpose': 'SIGNAL', 'nodes': [], 'edges': [], 'notes': notes,
                  'parameters': [{'name': n, 'kind': k, 'default': d, 'min': lo, 'max': hi, 'doc': doc} for n, k, d, lo, hi, doc in params]}

    def node(self, id_: str, type_: str, col: int, row: int, label: str = '', **params) -> '_G':
        self.d['nodes'].append({'id': id_, 'type': type_, 'params': params, 'position': (col * 250, row * 120), 'label': label})
        return self

    def wire(self, *pairs: str) -> '_G':
        for p in pairs:
            a, b = p.split('->')
            self.d['edges'].append({'from': a.strip(), 'to': b.strip()})
        return self


def _bars(g: _G) -> _G:
    return g.node('bars', 'source.bars', 0, 2, 'Bars')


def _atr(g: _G, col: int = 1, row: int = 4) -> _G:
    return g.node('atr', 'ind.atr', col, row, 'ATR 14', n=14).wire('bars.high -> atr.high', 'bars.low -> atr.low', 'bars.close -> atr.close')


def _const(g: _G, id_: str, param: str, col: int, row: int) -> _G:
    return g.node(id_, 'source.constant', col, row, param, value='{{' + param + '}}')


def _out(g: _G, show: list[tuple[str, str]], col: int = 4) -> _G:
    """Clamp → Signal output, with the values the library shows beside the score."""
    g.node('clamp', 'math.clamp', col - 1, 2, 'clamp ±100', lo=-100, hi=100).wire('raw.out -> clamp.x')
    g.node('score', 'output.signal', col, 2, 'Signal score', show=', '.join(n for n, _ in show)).wire('clamp.out -> score.score')
    g.wire(*[f'{src} -> score.{name}' for name, src in show])
    return g


def _trend(kind: str, ma: str, fast: int, slow: int) -> dict:
    g = _bars(_G(f'score = clamp(100 × ({ma.upper()}(fast) − {ma.upper()}(slow)) ÷ (scale × ATR14), −100, +100)',
                 [('fast', 'int', fast, 1, 500, f'fast {ma.upper()}, bars'), ('slow', 'int', slow, 2, 1000, f'slow {ma.upper()}, bars'),
                  ('scale', 'float', 1.0, 0.01, 100, 'ATRs for a full score')]))
    g.node('fast', f'ind.{ma}', 1, 0, f'{ma.upper()} fast', n='{{fast}}').node('slow', f'ind.{ma}', 1, 2, f'{ma.upper()} slow', n='{{slow}}')
    _const(_atr(g), 'scale', 'scale', 1, 6)
    g.node('gap', 'math.sub', 2, 1, 'gap')
    g.node('raw', 'math.expression', 3, 2, 'gap in ATRs', inputs='fast, slow, atr, scale', expression='100 * (fast - slow) / (scale * atr)')
    g.wire('bars.close -> fast.x', 'bars.close -> slow.x', 'fast.out -> gap.a', 'slow.out -> gap.b',
           'fast.out -> raw.fast', 'slow.out -> raw.slow', 'atr.out -> raw.atr', 'scale.value -> raw.scale')
    return _out(g, [('close', 'bars.close'), (f'{ma}_fast', 'fast.out'), (f'{ma}_slow', 'slow.out'), ('gap', 'gap.out'), ('atr14', 'atr.out')], 5).d


def _macd() -> dict:
    g = _bars(_G('score = clamp(100 × histogram ÷ (scale × ATR14)), histogram = (EMA(fast) − EMA(slow)) − EMA(signal) of that line',
                 [('fast', 'int', 12, 1, 500, 'fast EMA'), ('slow', 'int', 26, 2, 1000, 'slow EMA'), ('signal', 'int', 9, 1, 500, 'signal EMA'),
                  ('scale', 'float', 0.5, 0.01, 100, 'ATRs for a full score')]))
    g.node('macd', 'ind.macd', 1, 1, 'MACD', fast='{{fast}}', slow='{{slow}}', signal='{{signal}}').wire('bars.close -> macd.close')
    _const(_atr(g), 'scale', 'scale', 1, 6)
    g.node('raw', 'math.expression', 2, 2, 'histogram in ATRs', inputs='hist, atr, scale', expression='100 * hist / (scale * atr)')
    g.wire('macd.hist -> raw.hist', 'atr.out -> raw.atr', 'scale.value -> raw.scale')
    return _out(g, [('macd', 'macd.line'), ('signal_line', 'macd.signal'), ('histogram', 'macd.hist'), ('atr14', 'atr.out')]).d


def _rsi(sign: int) -> dict:
    word = 'reversion: high RSI scores short' if sign < 0 else 'momentum: high RSI scores long'
    g = _bars(_G(f'score = clamp({"−" if sign < 0 else ""}2 × (RSI − 50)) — {word}', [('period', 'int', 14, 2, 500, 'RSI bars')]))
    g.node('rsi', 'ind.rsi', 1, 2, 'RSI', n='{{period}}').wire('bars.close -> rsi.close')
    g.node('raw', 'math.expression', 2, 2, 'RSI to score', inputs='rsi', expression=f'{sign} * 2 * (rsi - 50)').wire('rsi.out -> raw.rsi')
    return _out(g, [('rsi', 'rsi.out')]).d


def _bollinger() -> dict:
    g = _bars(_G('σ = (upper − middle) ÷ deviations; z = (close − middle) ÷ σ; score = clamp(−100 × z ÷ deviations)',
                 [('period', 'int', 20, 2, 1000, 'bars'), ('deviations', 'float', 2.0, 0.1, 10, 'band width in σ')]))
    g.node('bb', 'ind.bollinger', 1, 1, 'Bollinger', n='{{period}}', k='{{deviations}}').wire('bars.close -> bb.close')
    _const(g, 'dev', 'deviations', 1, 4)
    g.node('sigma', 'math.expression', 2, 0, 'σ', inputs='up, mid, dev', expression='(up - mid) / dev')
    g.node('z', 'math.expression', 2, 1, 'z', inputs='c, mid, sigma', expression='(c - mid) / sigma')
    g.node('raw', 'math.expression', 3, 2, 'z to score', inputs='z, dev', expression='-100 * z / dev')
    g.wire('bb.upper -> sigma.up', 'bb.mid -> sigma.mid', 'dev.value -> sigma.dev', 'bars.close -> z.c', 'bb.mid -> z.mid', 'sigma.out -> z.sigma',
           'z.out -> raw.z', 'dev.value -> raw.dev')
    return _out(g, [('close', 'bars.close'), ('middle', 'bb.mid'), ('sigma', 'sigma.out'), ('upper', 'bb.upper'), ('lower', 'bb.lower'), ('z', 'z.out')], 5).d


def _zscore() -> dict:
    g = _bars(_G('score = clamp(−100 × z ÷ zmax), z = (close − SMA) ÷ σ (population) over the period',
                 [('period', 'int', 20, 2, 1000, 'bars'), ('zmax', 'float', 2.0, 0.1, 10, 'z for a full score')]))
    g.node('zs', 'ind.zscore', 1, 1, 'Z-score', n='{{period}}').wire('bars.close -> zs.close')
    _const(g, 'zmax', 'zmax', 1, 3)
    g.node('raw', 'math.expression', 2, 2, 'z to score', inputs='z, zmax', expression='-100 * z / zmax').wire('zs.out -> raw.z', 'zmax.value -> raw.zmax')
    return _out(g, [('close', 'bars.close'), ('z', 'zs.out')]).d


def _donchian() -> dict:
    g = _bars(_G('channel = highest high and lowest low of the previous n bars (this bar excluded); position = (close − low) ÷ (high − low), '
                 '0.5 when flat; score = clamp(200 × (position − 0.5))', [('period', 'int', 20, 2, 1000, 'bars')]))
    g.node('hi', 'series.rolling', 1, 0, 'channel high', fn='max', n='{{period}}', exclude_current=True)
    g.node('lo', 'series.rolling', 1, 3, 'channel low', fn='min', n='{{period}}', exclude_current=True)
    g.node('pos', 'math.expression', 2, 1, 'position', inputs='c, hi, lo', expression='if(hi == lo, 0.5, (c - lo) / (hi - lo))')
    g.node('raw', 'math.expression', 3, 2, 'position to score', inputs='pos', expression='200 * (pos - 0.5)')
    g.wire('bars.high -> hi.x', 'bars.low -> lo.x', 'bars.close -> pos.c', 'hi.out -> pos.hi', 'lo.out -> pos.lo', 'pos.out -> raw.pos')
    return _out(g, [('close', 'bars.close'), ('channel_high', 'hi.out'), ('channel_low', 'lo.out'), ('position', 'pos.out')], 5).d


def _momentum() -> dict:
    g = _bars(_G('t = ln(close ÷ close n bars ago) ÷ (σ₁ × √n), σ₁ = sample σ of the last n one-bar log returns; score = clamp(100 × t ÷ scale)',
                 [('period', 'int', 20, 2, 1000, 'bars'), ('scale', 'float', 2.0, 0.01, 100, 't for a full score')]))
    g.node('lr', 'math.expression', 1, 0, 'one-bar log return', inputs='c', expression='if(c > 0 and c[t-1] > 0, log(c / c[t-1]), 0)')
    g.node('sd', 'series.rolling', 2, 0, 'σ₁ (sample)', fn='stdev', n='{{period}}')
    g.node('ret', 'series.log_return', 1, 2, 'n-bar log return', k='{{period}}')
    g.node('ago', 'series.shift', 1, 4, 'close n ago', k='{{period}}')
    _const(g, 'n', 'period', 2, 3)
    _const(g, 'scale', 'scale', 2, 4)
    g.node('tstat', 'math.expression', 3, 1, 't', inputs='ret, sd, n', expression='if(sd == 0, 0, ret / (sd * sqrt(n)))')
    g.node('raw', 'math.expression', 4, 2, 't to score', inputs='tstat, scale', expression='100 * tstat / scale')
    g.wire('bars.close -> lr.c', 'lr.out -> sd.x', 'bars.close -> ret.x', 'bars.close -> ago.x', 'ret.out -> tstat.ret', 'sd.out -> tstat.sd',
           'n.value -> tstat.n', 'tstat.out -> raw.tstat', 'scale.value -> raw.scale')
    return _out(g, [('close', 'bars.close'), ('close_n_ago', 'ago.out'), ('log_return', 'ret.out'), ('sigma_1bar', 'sd.out'), ('t', 'tstat.out')], 6).d


def _stochastic() -> dict:
    g = _bars(_G('score = clamp(−2 × (%K − 50)) — %K high (close near the top of its range) scores short',
                 [('period', 'int', 14, 2, 500, 'range bars'), ('smooth', 'int', 3, 1, 50, '%K smoothing')]))
    g.node('st', 'ind.stochastic', 1, 2, 'Stochastic', n='{{period}}', k_smooth='{{smooth}}', d=3)
    g.wire('bars.high -> st.high', 'bars.low -> st.low', 'bars.close -> st.close')
    g.node('raw', 'math.expression', 2, 2, '%K to score', inputs='k', expression='-2 * (k - 50)').wire('st.k -> raw.k')
    return _out(g, [('percent_k', 'st.k'), ('percent_d', 'st.d')]).d


def _adx() -> dict:
    g = _bars(_G('balance = (+DI − −DI) ÷ (+DI + −DI); factor = min(1, ADX ÷ full_at); score = clamp(100 × balance × factor)',
                 [('period', 'int', 14, 2, 500, 'bars'), ('full_at', 'float', 30.0, 1, 100, 'ADX for full strength')]))
    g.node('adx', 'ind.adx', 1, 2, 'ADX', n='{{period}}').wire('bars.high -> adx.high', 'bars.low -> adx.low', 'bars.close -> adx.close')
    _const(g, 'full', 'full_at', 1, 4)
    g.node('bal', 'math.expression', 2, 1, 'DI balance', inputs='p, m', expression='(p - m) / (p + m)')
    g.node('fac', 'math.expression', 2, 3, 'ADX factor', inputs='a, full', expression='min(1, a / full)')
    g.node('raw', 'math.expression', 3, 2, 'to score', inputs='bal, fac', expression='100 * bal * fac')
    g.wire('adx.plus_di -> bal.p', 'adx.minus_di -> bal.m', 'adx.adx -> fac.a', 'full.value -> fac.full', 'bal.out -> raw.bal', 'fac.out -> raw.fac')
    return _out(g, [('adx', 'adx.adx'), ('plus_di', 'adx.plus_di'), ('minus_di', 'adx.minus_di'), ('di_balance', 'bal.out'), ('adx_factor', 'fac.out')], 5).d


def _vwap() -> dict:
    g = _bars(_G('score = clamp(−100 × (close − VWAP) ÷ (scale × ATR14)); volume = real volume, or ticks where there is none',
                 [('period', 'int', 20, 2, 1000, 'bars'), ('scale', 'float', 1.0, 0.01, 100, 'ATRs for a full score')]))
    g.node('vw', 'ind.vwap', 1, 1, 'VWAP', n='{{period}}').wire('bars.high -> vw.high', 'bars.low -> vw.low', 'bars.close -> vw.close', 'bars.volume -> vw.volume')
    _const(_atr(g), 'scale', 'scale', 1, 6)
    g.node('dist', 'math.sub', 2, 0, 'distance')
    g.node('raw', 'math.expression', 3, 2, 'distance in ATRs', inputs='c, v, atr, scale', expression='-100 * (c - v) / (scale * atr)')
    g.wire('bars.close -> dist.a', 'vw.out -> dist.b', 'bars.close -> raw.c', 'vw.out -> raw.v', 'atr.out -> raw.atr', 'scale.value -> raw.scale')
    return _out(g, [('close', 'bars.close'), ('vwap', 'vw.out'), ('distance', 'dist.out'), ('atr14', 'atr.out')], 5).d


def _sig(kind: str, name: str, family: str, definition: dict) -> tuple[str, dict]:
    return f'SIG_{kind}', {'name': name, 'purpose': 'SIGNAL', 'family': family, 'kind': kind, 'definition': definition,
                           'parity_ref': f'trading.signal_library.compute("{kind}")',
                           'description': f'The signal library\'s {name} score drawn as a graph; equal to the code to 1e-9 on every bar.'}


def _feature(notes: str, build) -> dict:
    g = _bars(_G(notes, []))
    g.d['purpose'] = 'FEATURE'
    build(g)
    g.node('value', 'output.feature', 4, 2, 'Feature value').wire('raw.out -> value.value')
    return g.d


def _f_atr_pct(g: _G) -> None:
    _atr(g, 1, 3)
    g.node('raw', 'math.expression', 2, 2, 'ATR as % of close', inputs='atr, c', expression='100 * atr / c').wire('atr.out -> raw.atr', 'bars.close -> raw.c')


def _f_bb_width(g: _G) -> None:
    g.node('bb', 'ind.bollinger', 1, 2, 'Bollinger 20, 2', n=20, k=2.0).wire('bars.close -> bb.close')
    g.node('raw', 'math.expression', 2, 2, 'band width %', inputs='up, lo, mid', expression='100 * (up - lo) / mid').wire('bb.upper -> raw.up', 'bb.lower -> raw.lo', 'bb.mid -> raw.mid')


def _f_ret(k: int):
    def build(g: _G) -> None:
        g.node('raw', 'series.pct_change', 2, 2, f'{k}-bar return %', k=k).wire('bars.close -> raw.x')
    return build


def _f_vwap_dev(g: _G) -> None:
    g.node('vw', 'ind.vwap', 1, 2, 'VWAP 20', n=20).wire('bars.high -> vw.high', 'bars.low -> vw.low', 'bars.close -> vw.close', 'bars.volume -> vw.volume')
    g.node('raw', 'math.expression', 2, 2, 'distance from VWAP %', inputs='c, v', expression='100 * (c / v - 1)').wire('bars.close -> raw.c', 'vw.out -> raw.v')


def _f_rel_vol(g: _G) -> None:
    g.node('avg', 'ind.sma', 1, 3, 'average volume 20', n=20).wire('bars.volume -> avg.x')
    g.node('raw', 'math.div', 2, 2, 'volume ÷ average').wire('bars.volume -> raw.a', 'avg.out -> raw.b')


def _feat(key: str, name: str, notes: str, build) -> tuple[str, dict]:
    return f'FEAT_{key.upper()}', {'name': name, 'purpose': 'FEATURE', 'family': 'Feature', 'feature': key, 'definition': _feature(notes, build),
                                   'parity_ref': f'intel.engine.features()["{key}"]',
                                   'description': f'The platform feature {key} drawn as a graph; equal to the feature engine on every bar.'}


def _cross_rule(ma: str, fast: str, slow: str, fdef: int, sdef: int, label: str) -> dict:
    g = _bars(_G(f'{label}: LONG when {ma.upper()}({fast}) crosses above {ma.upper()}({slow}), SHORT when it crosses below; the target is held between '
                 f'crossings. strength = clamp(round(100 × |fast − slow| ÷ ATR14), 10, 100) on the bar it changes (50 before ATR is ready).',
                 [(fast, 'int', fdef, 1, 500, f'fast {ma.upper()}, bars'), (slow, 'int', sdef, 2, 1000, f'slow {ma.upper()}, bars')]))
    g.d['purpose'] = 'STRATEGY_RULE'
    g.node('fast', f'ind.{ma}', 1, 0, f'{ma.upper()} fast', n='{{' + fast + '}}').node('slow', f'ind.{ma}', 1, 2, f'{ma.upper()} slow', n='{{' + slow + '}}')
    _atr(g, 1, 4)
    g.node('up', 'logic.crosses', 2, 0, 'crosses above', direction='above').node('dn', 'logic.crosses', 2, 2, 'crosses below', direction='below')
    g.node('latch', 'logic.latch', 3, 1, 'target', direction='BOTH')
    g.node('strength', 'math.expression', 3, 3, 'strength', inputs='chg:bool, f, s, atr',
           expression='if(chg, if(coalesce(atr, 0) > 0, max(10, min(100, round(100 * abs(f - s) / atr))), 50), 0)')
    g.node('rule', 'output.rule', 4, 2, 'Strategy rule', reason=f'{label} crossing')
    g.wire('bars.close -> fast.x', 'bars.close -> slow.x', 'fast.out -> up.a', 'slow.out -> up.b', 'fast.out -> dn.a', 'slow.out -> dn.b',
           'up.out -> latch.long', 'dn.out -> latch.short', 'latch.changed -> strength.chg', 'fast.out -> strength.f', 'slow.out -> strength.s',
           'atr.out -> strength.atr', 'latch.target -> rule.target', 'strength.out -> rule.strength')
    return g.d


def _rule(algo: str, name: str, d: dict) -> tuple[str, dict]:
    return f'STRAT_{algo}', {'name': name, 'purpose': 'STRATEGY_RULE', 'family': 'Strategy', 'algorithm': algo, 'definition': d,
                              'parity_ref': f'trading.algorithms.run("{algo}")',
                              'description': f'The {name} strategy algorithm drawn as a graph: the same target and strength on every bar.'}


BUILTINS: dict[str, dict] = dict([
    _rule('EMA_CROSSOVER', 'EMA crossover', _cross_rule('ema', 'fast', 'slow', 9, 21, 'EMA crossover')),
    _rule('MA_CROSSOVER', 'Moving-average crossover', _cross_rule('sma', 'fast_window', 'slow_window', 20, 50, 'SMA crossover')),
    _feat('atr_pct', 'ATR %', 'atr_pct = 100 × ATR14 ÷ close: the typical bar range as a share of the price.', _f_atr_pct),
    _feat('bb_width_pct', 'Bollinger width %', 'bb_width_pct = 100 × (upper − lower) ÷ middle, Bollinger(20, 2).', _f_bb_width),
    _feat('ret_1_pct', 'Return 1 bar %', 'ret_1_pct = 100 × (closeₜ ÷ closeₜ₋₁ − 1).', _f_ret(1)),
    _feat('ret_20_pct', 'Return 20 bars %', 'ret_20_pct = 100 × (closeₜ ÷ closeₜ₋₂₀ − 1).', _f_ret(20)),
    _feat('vwap_dev_pct', 'Distance from VWAP %', 'vwap_dev_pct = 100 × (close ÷ VWAP20 − 1).', _f_vwap_dev),
    _feat('rel_volume20', 'Relative volume', 'rel_volume20 = volume ÷ SMA(volume, 20) — real volume where the source has it, else ticks.', _f_rel_vol),
    _sig('EMA_TREND', 'EMA trend', 'Trend', _trend('EMA_TREND', 'ema', 9, 21)),
    _sig('SMA_TREND', 'SMA trend', 'Trend', _trend('SMA_TREND', 'sma', 20, 50)),
    _sig('MACD_MOMENTUM', 'MACD momentum', 'Momentum', _macd()),
    _sig('RSI_REVERSION', 'RSI reversion', 'Mean reversion', _rsi(-1)),
    _sig('RSI_MOMENTUM', 'RSI momentum', 'Momentum', _rsi(1)),
    _sig('BOLLINGER_REVERSION', 'Bollinger reversion', 'Mean reversion', _bollinger()),
    _sig('ZSCORE_REVERSION', 'Z-score reversion', 'Mean reversion', _zscore()),
    _sig('DONCHIAN_BREAKOUT', 'Donchian breakout', 'Breakout', _donchian()),
    _sig('MOMENTUM_TSTAT', 'Momentum (t-stat)', 'Momentum', _momentum()),
    _sig('STOCHASTIC_REVERSION', 'Stochastic reversion', 'Mean reversion', _stochastic()),
    _sig('ADX_DIRECTION', 'ADX direction', 'Trend', _adx()),
    _sig('VWAP_REVERSION', 'VWAP reversion', 'Mean reversion', _vwap()),
])
EMA_TREND = BUILTINS['SIG_EMA_TREND']['definition']
