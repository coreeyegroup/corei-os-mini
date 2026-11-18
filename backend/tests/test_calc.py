"""Calculation Graphs, phase 1 (DEC-93): the expression language, every node in the catalogue, the executor's checks
and trace, determinism, point in time (every node, by truncating the bars), the float/decimal boundary, safety (no
eval, no clock, no I/O inside graphs), parity of the built-in graph with the code it draws, the time budgets, and the
/api/calc endpoints with the insert-only run record and frozen approved versions."""
from __future__ import annotations

import importlib
import importlib.util
import math
import re
import statistics
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from corei_os_mini.calc import expr as E, types as T
from corei_os_mini.calc.builtin import BUILTINS
from corei_os_mini.calc.model import LIMITS
from corei_os_mini.calc.nodes import REGISTRY, Ctx, _load, check_params
from corei_os_mini.calc.run import GraphInvalid, compile_graph, definition_hash, execute, run, run_many
from corei_os_mini.trading import signal_library

_load()
tt = importlib.import_module('test_trading') if importlib.util.find_spec('test_trading') else importlib.import_module('tests.test_trading')
NOW = tt.NOW


def wave(n: int = 600, volume: bool = False) -> list[dict]:
    out = []
    for i in range(n):
        c = 1.10 + 0.01 * math.sin(i / 9) + 0.004 * math.sin(i / 3.1) + 0.00002 * i
        out.append({'bar_time': NOW + timedelta(minutes=5 * i), 'open': c - 0.0001, 'high': c + 0.0006 + 0.0002 * math.sin(i),
                    'low': c - 0.0006, 'close': c, 'ticks': 10 + i % 7, 'volume': (100 + i % 13) if volume else 0, 'spread_avg': 0.00008})
    return out


SCALARS = {'quote': {'bid': Decimal('1.1000'), 'ask': Decimal('1.1002'), 'mid': Decimal('1.1001'), 'spread': Decimal('0.0002')},
           'position': {'quantity': Decimal('0.10'), 'side': 'LONG', 'average_price': Decimal('1.0950'), 'unrealised': Decimal('51.00')},
           'account': {'equity': Decimal('10000'), 'balance': Decimal('9950'), 'free_margin': Decimal('9000'), 'currency': 'USD'},
           'instrument': {'tick_size': Decimal('0.00001'), 'contract_size': Decimal('100000'), 'min_quantity': Decimal('0.01'),
                          'quantity_step': Decimal('0.01')},
           'settings': {'max_spread_points': 30}}


# ---------------------------------------------------------------- expression language

def ev(text: str, **series):
    names = {k: 'num' for k in series}
    x = E.parse(text, names)
    return x.at({k: v if isinstance(v, list) else [v] for k, v in series.items()}, len(next(iter(series.values()), [0])) - 1
                if series and isinstance(next(iter(series.values())), list) else 0)


def test_expression_arithmetic_precedence_and_functions():
    assert ev('2 + 3 * 4') == 14
    assert ev('(2 + 3) * 4') == 20
    assert ev('-2 ^ 2') == -4 and ev('2 ^ 3 ^ 2') == 512
    assert ev('10 / 4') == 2.5 and ev('1 / 0') is None
    assert ev('sqrt(-1)') is None and ev('log(0)') is None and ev('sqrt(16)') == 4
    assert ev('clamp(150, -100, 100)') == 100 and ev('min(3, 1, 2)') == 1 and ev('max(3, 1, 2)') == 3
    assert ev('round(2.345, 2)') == 2.35 or ev('round(2.345, 2)') == 2.34  # half to even on the binary value
    assert ev('sign(-3)') == -1 and ev('abs(-3)') == 3 and ev('exp(0)') == 1
    assert ev('1 < 2 and not (3 < 2)') is True and ev('1 > 2 or false') is False
    assert ev('if(1 < 2, 10, 1 / 0)') == 10
    assert ev('a + 1', a=[None]) is None, 'a missing value makes the result missing'
    assert ev('if(a > 0, 1, 2)', a=[None]) is None


def test_expression_indexing_reads_only_the_past():
    x = E.parse('c - c[t-1]', {'c': 'num'})
    env = {'c': [1.0, 3.0, 6.0]}
    assert [x.at(env, i) for i in range(3)] == [None, 2.0, 3.0]
    assert x.lookback == 1
    assert E.parse('c[t]', {'c': 'num'}).lookback == 0
    with pytest.raises(E.ExprError, match='future'):
        E.parse('c[t+1]', {'c': 'num'})
    with pytest.raises(E.ExprError):
        E.parse('c[1]', {'c': 'num'})


@pytest.mark.parametrize('text', [
    '__import__("os")', 'a.real', '"text"', "'text'", 'lambda: 1', 'a; b', 'open(1)', 'eval(1)', 'x = 1', '[1, 2]', 'a if b else c',
    '1 +', '(1', 'min(1)', 'clamp(1, 2)', 'a and 1', 'not 3', '1 + true', 'if(1, 2, 3)', 'if(1 < 2, 1, true)', 'unknown + 1', 't + 1',
])
def test_expression_refuses_everything_outside_the_grammar(text):
    with pytest.raises(E.ExprError):
        E.parse(text, {'a': 'num', 'b': 'num', 'c': 'num'})


def test_expression_limits():
    with pytest.raises(E.ExprError, match='longer'):
        E.parse('1 + ' * 200 + '1', {})
    with pytest.raises(E.ExprError, match='parts'):
        E.parse('+'.join(['1'] * 120), {})
    with pytest.raises(E.ExprError, match='nested'):
        E.parse('(' * 60 + '1' + ')' * 60, {})


# ---------------------------------------------------------------- the node catalogue

def sample_input(port_type: str, n: int, k: int = 0):
    if port_type == T.SERIES_FLOAT:
        return [1.1 + 0.01 * math.sin((i + k) / 5) + 0.001 * k for i in range(n)]
    if port_type == T.SERIES_BOOL:
        return [((i + k) // 3) % 2 == 0 for i in range(n)]
    if port_type == T.SERIES_SIDE:
        return [('LONG', 'SHORT', 'FLAT')[((i + k) // 4) % 3] for i in range(n)]
    return {T.FLOAT: 1.5, T.BOOL: True, T.SIDE: 'LONG', T.TEXT: 'x'}.get(port_type, Decimal('1.5'))


def run_node(nt, params, bars):
    ctx = Ctx(bars, 'M5', SCALARS)
    p = check_params('n', nt, params)
    ins, _outs = nt.io(p)
    values = {port.name: sample_input(port.type, len(bars), k) for k, port in enumerate(ins)}
    return nt.run(ctx, values, p), p


NODE_PARAMS = {'math.expression': {'inputs': 'a, b', 'expression': '100 * (a - b[t-2]) / b'},
               'combine.weighted_sum': {'count': 3, 'w1': 2, 'w2': 1, 'w3': -1},
               'output.signal': {'show': 'x'}, 'output.risk_check': {'reason': 'too wide'},
               'source.signal_score': {'kind': 'MOMENTUM_TSTAT'}}


def test_catalogue_is_complete_and_described():
    families = {nt.family for nt in REGISTRY.values()}
    assert families == {'sources', 'indicators', 'series', 'maths', 'logic', 'combine', 'convert', 'outputs'}
    for want in ('source.bars', 'source.feature', 'source.signal_score', 'source.quote', 'source.position', 'source.account', 'source.instrument',
                 'source.settings', 'source.constant', 'ind.sma', 'ind.ema', 'ind.rsi', 'ind.atr', 'ind.bollinger', 'ind.adx', 'ind.macd',
                 'ind.stochastic', 'ind.donchian', 'ind.zscore', 'ind.vwap', 'ind.obv', 'ind.roc', 'series.shift', 'series.rolling', 'series.diff',
                 'series.pct_change', 'series.log_return', 'series.cumulative', 'series.normalise_atr', 'math.add', 'math.sub', 'math.mul',
                 'math.div', 'math.func', 'math.minmax', 'math.clamp', 'math.round', 'math.expression', 'logic.compare', 'logic.crosses',
                 'logic.and', 'logic.or', 'logic.not', 'logic.if', 'logic.between', 'logic.side_from', 'logic.hysteresis',
                 'combine.weighted_sum', 'combine.vote', 'combine.consensus', 'combine.rank', 'convert.to_decimal',
                 'output.feature', 'output.signal', 'output.rule', 'output.sizing', 'output.protective', 'output.risk_check',
                 'output.allocation', 'output.measure'):
        assert want in REGISTRY, want
    for nt in REGISTRY.values():
        assert nt.label and len(nt.doc) > 10, nt.type
        d = nt.as_dict()
        assert d['formula'], nt.type


@pytest.mark.parametrize('type_', sorted(REGISTRY))
def test_every_node_runs_and_never_looks_ahead(type_):
    """Point in time, node by node: the output on bar i is the same whether the bars after i exist or not."""
    nt = REGISTRY[type_]
    bars = wave(300, volume=True)
    full, p = run_node(nt, NODE_PARAMS.get(type_, {}), bars)
    _ins, outs = nt.io(p)
    for port in outs:
        v = full[port.name]
        if T.is_series(port.type):
            assert isinstance(v, list) and len(v) == len(bars), (type_, port.name)
        elif port.type in T.DECIMALS:
            assert isinstance(v, Decimal), (type_, port.name)
    for cut in (40, 150, 299):
        part, _ = run_node(nt, NODE_PARAMS.get(type_, {}), bars[:cut])
        for name, v in full.items():
            if isinstance(v, list):
                assert part[name] == v[:cut], f'{type_}.{name} changes on bars before {cut} when later bars are added: it looks ahead'
    # The formula and the explanation can always be shown.
    assert isinstance(nt.formula(p), str) and nt.formula(p)


def test_indicator_of_an_indicator_starts_where_its_input_starts():
    from corei_os_mini.intel import indicators as ind
    nt = REGISTRY['ind.sma']
    x = [None] * 5 + [float(i) for i in range(30)]
    out = nt.run(Ctx(wave(35)), {'x': x}, {'n': 4})['out']
    assert out == [None] * 5 + ind.sma([float(i) for i in range(30)], 4)
    with pytest.raises(ValueError, match='gap'):
        nt.run(Ctx(wave(35)), {'x': x[:10] + [None] + x[11:]}, {'n': 4})


def test_series_nodes_compute_what_they_say():
    c = [1.0, 2.0, 4.0, 7.0, 11.0]
    R = REGISTRY
    ctx = Ctx(wave(5))
    assert R['series.shift'].run(ctx, {'x': c}, {'k': 2})['out'] == [None, None, 1.0, 2.0, 4.0]
    assert R['series.diff'].run(ctx, {'x': c}, {'k': 1})['out'] == [None, 1.0, 2.0, 3.0, 4.0]
    assert R['series.rolling'].run(ctx, {'x': c}, {'fn': 'sum', 'n': 2, 'exclude_current': False})['out'] == [None, 3.0, 6.0, 11.0, 18.0]
    assert R['series.rolling'].run(ctx, {'x': c}, {'fn': 'max', 'n': 2, 'exclude_current': True})['out'] == [None, None, 2.0, 4.0, 7.0]
    sd = R['series.rolling'].run(ctx, {'x': c}, {'fn': 'stdev', 'n': 3, 'exclude_current': False})['out'][-1]
    assert sd == pytest.approx(statistics.stdev([4.0, 7.0, 11.0]))
    assert R['series.cumulative'].run(ctx, {'x': c}, {'fn': 'sum'})['out'] == [1.0, 3.0, 7.0, 14.0, 25.0]
    assert R['logic.crosses'].run(ctx, {'a': [1, 3, 2, 5, 1], 'b': [2, 2, 2, 2, 2]}, {'direction': 'either'})['out'] == [None, True, False, True, True]
    hy = R['logic.hysteresis'].run(ctx, {'score': [10, 35, 20, 4, -40]}, {'entry': 30, 'exit': 5})['out']
    assert hy == ['FLAT', 'LONG', 'LONG', 'FLAT', 'SHORT']
    ws = R['combine.weighted_sum'].run(ctx, {'in1': [10.0] * 5, 'in2': [None, 40.0, 40.0, 40.0, 40.0]},
                                       check_params('w', R['combine.weighted_sum'], {'w1': 2, 'w2': 1}))['out']
    assert ws[0] == 10.0 and ws[1] == pytest.approx(20.0)


# ---------------------------------------------------------------- the executor

def g_signal(**over) -> dict:
    d = {'purpose': 'SIGNAL', 'nodes': [{'id': 'bars', 'type': 'source.bars'}, {'id': 'r', 'type': 'ind.rsi', 'params': {'n': 14}},
                                        {'id': 'x', 'type': 'math.expression', 'params': {'inputs': 'rsi', 'expression': '2 * (rsi - 50)'}},
                                        {'id': 'out', 'type': 'output.signal'}],
         'edges': [{'from': 'bars.close', 'to': 'r.close'}, {'from': 'r.out', 'to': 'x.rsi'}, {'from': 'x.out', 'to': 'out.score'}]}
    d.update(over)
    return d


def errors_of(definition, overrides=None) -> str:
    with pytest.raises(GraphInvalid) as exc:
        compile_graph(definition, overrides)
    return ' | '.join(e['message'] for e in exc.value.errors)


def test_executor_checks_everything_before_running():
    g = g_signal()
    assert 'unknown node type' in errors_of({**g, 'nodes': [*g['nodes'], {'id': 'z', 'type': 'nope'}]})
    assert 'must be at least 1' in errors_of({**g, 'nodes': [{**n, 'params': {'n': 0}} if n['id'] == 'r' else n for n in g['nodes']]})
    assert "input 'score'" in errors_of({**g, 'edges': g['edges'][:2]})
    assert 'two wires' in errors_of({**g, 'edges': [*g['edges'], {'from': 'r.out', 'to': 'out.score'}]})
    assert "no output 'nope'" in errors_of({**g, 'edges': [*g['edges'][:2], {'from': 'x.nope', 'to': 'out.score'}]})
    assert 'ends in exactly one output' in errors_of({**g, 'nodes': g['nodes'][:3], 'edges': g['edges'][:2]})
    assert 'must end in output.signal' in errors_of({**g, 'purpose': 'SIGNAL', 'nodes': [*g['nodes'][:3], {'id': 'out', 'type': 'output.feature'}],
                                                     'edges': [*g['edges'][:2], {'from': 'x.out', 'to': 'out.value'}]})
    assert "no graph parameter 'p'" in errors_of({**g, 'nodes': [{**n, 'params': {'n': '{{p}}'}} if n['id'] == 'r' else n for n in g['nodes']]})
    assert 'expression' in errors_of({**g, 'nodes': [{**n, 'params': {'inputs': 'rsi', 'expression': 'rsi[t+1]'}} if n['id'] == 'x' else n
                                                     for n in g['nodes']]})
    loop = {'purpose': 'FEATURE', 'nodes': [{'id': 'a', 'type': 'math.add'}, {'id': 'b', 'type': 'math.add'}, {'id': 'k', 'type': 'source.constant'},
                                            {'id': 'o', 'type': 'output.feature'}],
            'edges': [{'from': 'a.out', 'to': 'b.a'}, {'from': 'b.out', 'to': 'a.a'}, {'from': 'k.value', 'to': 'a.b'}, {'from': 'k.value', 'to': 'b.b'},
                      {'from': 'b.out', 'to': 'o.value'}]}
    assert 'loop' in errors_of(loop)
    with pytest.raises(GraphInvalid):
        compile_graph({'purpose': 'SIGNAL', 'nodes': [{'id': f'n{i}', 'type': 'source.constant'} for i in range(LIMITS['nodes'] + 1)]})


def test_money_never_arrives_as_a_float():
    g = {'purpose': 'SIZING', 'nodes': [{'id': 'bars', 'type': 'source.bars'}, {'id': 'last', 'type': 'convert.last_float'},
                                        {'id': 'out', 'type': 'output.sizing'}],
         'edges': [{'from': 'bars.close', 'to': 'last.x'}, {'from': 'last.out', 'to': 'out.quantity'}]}
    msg = errors_of(g)
    assert 'float' in msg and 'decimal:quantity' in msg and 'To decimal' in msg
    g['nodes'].append({'id': 'dec', 'type': 'convert.to_decimal', 'params': {'kind': 'quantity', 'places': 2, 'rounding': 'DOWN'}})
    g['edges'] = [{'from': 'bars.close', 'to': 'last.x'}, {'from': 'last.out', 'to': 'dec.x'}, {'from': 'dec.out', 'to': 'out.quantity'}]
    res = run(g, Ctx(wave(50)))
    assert res.ok and isinstance(res.outputs['quantity'], Decimal) and res.outputs['quantity'] == Decimal(repr(wave(50)[-1]['close'])).quantize(
        Decimal('0.01'), rounding='ROUND_DOWN')
    assert 'exact quantity' in res.trace['dec']['formula']
    # A price wired into a quantity is refused too: decimal kinds do not mix.
    g2 = {'purpose': 'SIZING', 'nodes': [{'id': 'q', 'type': 'source.quote'}, {'id': 'out', 'type': 'output.sizing'}],
          'edges': [{'from': 'q.bid', 'to': 'out.quantity'}]}
    assert 'decimal:price' in errors_of(g2)


def test_trace_shows_every_step_with_its_numbers_and_errors_stay_local():
    res = run(g_signal(), Ctx(wave(100)), trace_at=60)
    assert res.ok and res.bar == 60 and set(res.trace) == {'bars', 'r', 'x', 'out'}
    t = res.trace['x']
    assert t['inputs']['rsi'] == pytest.approx(REGISTRY['ind.rsi'].run(Ctx(wave(100)), {'close': [b['close'] for b in wave(100)]}, {'n': 14})['out'][60])
    assert t['explain'].startswith('2 * (rsi - 50)  =  2 * (') and '=' in t['explain']
    # A source without its value fails with the reason; only what depends on it is skipped.
    g = {'purpose': 'RISK_CHECK', 'nodes': [{'id': 'q', 'type': 'source.quote'}, {'id': 'bars', 'type': 'source.bars'},
                                            {'id': 'f', 'type': 'math.expression', 'params': {'inputs': 'c', 'expression': 'c > 2', 'output': 'true/false'}},
                                            {'id': 'out', 'type': 'output.risk_check', 'params': {'reason': 'price above 2'}}],
         'edges': [{'from': 'bars.close', 'to': 'f.c'}, {'from': 'f.out', 'to': 'out.refuse'}]}
    res = run(g, Ctx(wave(30)))
    assert res.ok and res.outputs['refuse'] is False and 'quote' in res.errors['q']
    plan_bad = compile_graph({**g, 'edges': [{'from': 'bars.close', 'to': 'f.c'}, {'from': 'f.out', 'to': 'out.refuse'}],
                              'nodes': [*g['nodes'][:2], {'id': 'f', 'type': 'math.expression',
                                                           'params': {'inputs': 'c', 'expression': 'c > 2', 'output': 'true/false'}},
                                        g['nodes'][3]]})
    both = run_many([('one', compile_graph(g_signal())), ('two', plan_bad)], Ctx(wave(30)))
    assert both['one'].ok and both['two'].ok


def test_risk_check_fails_closed_and_can_only_refuse():
    g = {'purpose': 'RISK_CHECK', 'nodes': [{'id': 'bars', 'type': 'source.bars'}, {'id': 'r', 'type': 'ind.rsi', 'params': {'n': 14}},
                                            {'id': 'f', 'type': 'math.expression', 'params': {'inputs': 'r', 'expression': 'r > 80', 'output': 'true/false'}},
                                            {'id': 'out', 'type': 'output.risk_check', 'params': {'reason': 'overbought'}}],
         'edges': [{'from': 'bars.close', 'to': 'r.close'}, {'from': 'r.out', 'to': 'f.r'}, {'from': 'f.out', 'to': 'out.refuse'}]}
    warm = run(g, Ctx(wave(10)))          # RSI not yet available: the check cannot be computed → it refuses
    assert warm.outputs['refuse'] is True and 'fails closed' in warm.outputs['reason']
    assert set(run(g, Ctx(wave(100))).outputs) == {'refuse', 'reason'}, 'a risk graph gives a refusal or nothing — never an approval or a size'


def test_deterministic_and_hash_ignores_layout():
    g = BUILTINS['SIG_EMA_TREND']['definition']
    a = run(g, Ctx(wave()))
    b = run(g, Ctx(wave()))
    assert a.outputs['score'] == b.outputs['score'] and a.trace['raw']['explain'] == b.trace['raw']['explain']
    moved = {**g, 'nodes': [{**n, 'position': (n['position'][0] + 50, 9)} for n in g['nodes']]}
    assert definition_hash(moved) == definition_hash(g)
    assert definition_hash({**g, 'notes': 'other'}) != definition_hash(g)


@pytest.mark.parametrize('params', [{}, {'fast': 5, 'slow': 13, 'scale': 2.0}, {'fast': 20, 'slow': 50, 'scale': 0.5}])
def test_builtin_ema_trend_equals_the_signal_library(params):
    bars = wave()
    res = run(BUILTINS['SIG_EMA_TREND']['definition'], Ctx(bars, 'M5'), params)
    ref = signal_library.compute('EMA_TREND', params, bars)
    assert [x is None for x in res.outputs['score']] == [x is None for x in ref]
    for got, want in zip(res.outputs['score'], ref):
        if want is not None:
            assert abs(got - want[0]) <= 1e-9
    i = len(bars) - 1
    assert res.outputs['inputs'][i]['fast'] == pytest.approx(ref[i][1]['ema_fast'], abs=1e-5)


def test_graph_parameters_are_bounded():
    assert 'within' in errors_of(BUILTINS['SIG_EMA_TREND']['definition'], {'fast': 0})
    assert 'no parameter' in errors_of(BUILTINS['SIG_EMA_TREND']['definition'], {'nope': 1})


def test_time_budgets():
    plan = compile_graph(BUILTINS['SIG_EMA_TREND']['definition'])
    bars = wave(600)
    times = []
    for _ in range(7):
        t = time.perf_counter()
        execute(plan, Ctx(bars, 'M5'))
        times.append((time.perf_counter() - t) * 1000)
    median = statistics.median(times)
    print(f'signal graph, 600 bars: median {median:.2f} ms')
    assert median <= 20, f'a signal graph on 600 bars must run within 20 ms; it took {median:.1f} ms'
    plans = [compile_graph(BUILTINS['SIG_EMA_TREND']['definition'], {'fast': 3 + k, 'slow': 30 + k}) for k in range(15)]
    t = time.perf_counter()
    for _instrument in range(30):
        for _tf in range(5):
            ctx = Ctx(bars, 'M5')
            for p in plans:
                execute(p, ctx)
    total = time.perf_counter() - t
    print(f'30 instruments × 5 timeframes × 15 graphs: {total:.2f} s')
    assert total < 10, f'2,250 graph runs must finish within 10 s; they took {total:.1f} s'


def test_no_code_execution_clock_or_io_inside_graphs():
    root = Path(__file__).parents[1] / 'src' / 'corei_os_mini' / 'calc'
    banned = re.compile(r'\b(eval|exec|compile|__import__|open|getattr|setattr)\s*\(|\bimport\s+(os|sys|socket|subprocess|requests|httpx|urllib|ast)\b|'
                        r'datetime\.now|time\.time\(|date\.today|psycopg\.connect')
    for path in root.rglob('*.py'):
        text = path.read_text()
        if path.name == 'store.py':        # persistence takes the caller's transaction; it is not used inside graphs
            continue
        for line in text.splitlines():
            if re.search(r're\.compile\(', line):
                line = re.sub(r're\.compile\(', '', line)
            assert not banned.search(line), f'{path.name}: {line.strip()}'
    for path in root.rglob('*.py'):
        assert path.read_text().startswith('"""'), f'{path.name} needs its header'


# ---------------------------------------------------------------- the API

class CalcApi(tt.Trading):
    test_paper_trade_end_to_end = None
    test_tickets_limits_and_unknown = None
    test_broker_mode_refused_and_loss_limit_stops = None
    test_backtest_no_look_ahead = None

    def test_nodes_graphs_validate_preview_and_audit(self):
        _connection, (a, _b, _c), ins = self.setup_accounts()
        self.start(self.plan(a, ins, 'EU_CALC'))
        closes = [1.10 + 0.01 * math.sin(i / 9) for i in range(300)]
        self.bars(a['id'], closes, NOW - timedelta(minutes=5 * 300))
        self.quote(a['id'], 1.08000, 1.08010)
        cat = self.ok(self.alice.get('/api/calc/nodes'))
        self.assertEqual(len(cat['items']), len(REGISTRY))
        self.assertIn('SIGNAL', cat['purposes'])
        lib = self.ok(self.alice.get('/api/calc/graphs'))['items']
        self.assertTrue(any(g['code'] == 'SIG_EMA_TREND' and g['builtin'] and g['versions'][0]['status'] == 'APPROVED' for g in lib))
        v1 = self.ok(self.alice.get('/api/calc/graphs/SIG_EMA_TREND/versions/1'))
        self.assertEqual(v1['definition']['purpose'], 'SIGNAL')
        self.assertEqual(self.alice.get('/api/calc/graphs/NOPE').status_code, 404)
        good = self.ok(self.alice.post('/api/calc/validate', json={'definition': g_signal()}))
        self.assertTrue(good['ok'] and good['order'][-1] == 'out' and len(good['hash']) == 64)
        bad = self.ok(self.alice.post('/api/calc/validate', json={'definition': {**g_signal(), 'edges': []}}))
        self.assertFalse(bad['ok'])
        self.assertTrue(any("input 'score'" in e['message'] for e in bad['errors']))
        pv = self.ok(self.alice.post('/api/calc/preview', json={'code': 'SIG_EMA_TREND', 'account_id': a['id'], 'symbol': 'EURUSD',
                                                                 'timeframe': 'M5', 'bars': 300, 'tail': 50}))
        self.assertTrue(pv['ok'])
        self.assertEqual((pv['bars'], len(pv['outputs']['score']), len(pv['times'])), (300, 50, 50))
        self.assertIn('100 * (fast - slow) / (scale * atr)  =  100 * (', pv['trace']['raw']['explain'])
        stored = [dict(r) for r in self.connect().execute("SELECT * FROM md_bars WHERE account_id=%s AND timeframe='M5' ORDER BY bar_time",
                                                          (a['id'],)).fetchall()]
        ref = signal_library.compute('EMA_TREND', {}, stored)
        self.assertAlmostEqual(pv['outputs']['score'][-1], ref[-1][0], delta=1e-9)
        # A graph reading the quote gets the stored quote as exact decimals.
        qg = {'purpose': 'MEASURE', 'nodes': [{'id': 'q', 'type': 'source.quote'}, {'id': 'bars', 'type': 'source.bars'},
                                              {'id': 'm', 'type': 'output.measure', 'params': {'name': 'close'}}],
              'edges': [{'from': 'bars.close', 'to': 'm.value'}]}
        pq = self.ok(self.alice.post('/api/calc/preview', json={'definition': qg, 'account_id': a['id'], 'symbol': 'EURUSD', 'timeframe': 'M5', 'bars': 50}))
        self.assertEqual(Decimal(pq['trace']['q']['outputs']['bid']), Decimal('1.08'))
        self.assertEqual(Decimal(pq['trace']['q']['outputs']['spread']), Decimal('0.0001'))
        trace_mid = self.ok(self.alice.post('/api/calc/preview', json={'code': 'SIG_EMA_TREND', 'account_id': a['id'], 'symbol': 'EURUSD',
                                                                        'timeframe': 'M5', 'bars': 300, 'trace_bar': 100}))
        self.assertEqual(trace_mid['trace_bar'], 100)
        invalid = self.ok(self.alice.post('/api/calc/preview', json={'definition': {**g_signal(), 'edges': []}, 'account_id': a['id'],
                                                                      'symbol': 'EURUSD', 'timeframe': 'M5'}))
        self.assertFalse(invalid['valid'])
        self.assertEqual(self.alice.post('/api/calc/preview', json={'code': 'SIG_EMA_TREND', 'account_id': a['id'], 'symbol': 'GBPUSD'}).status_code, 409)
        runs = self.ok(self.alice.get('/api/calc/runs'))['items']
        self.assertEqual(len(runs), 3)
        self.assertTrue(all(r['kind'] == 'PREVIEW' and r['actor'] == 'alice' for r in runs))
        with self.connect() as db:
            with self.assertRaises(Exception):
                db.execute('UPDATE calc_runs SET ok = false')
        with self.connect() as db:
            with self.assertRaises(Exception):
                db.execute('DELETE FROM calc_runs')

    def test_approved_versions_are_frozen_by_the_database(self):
        with self.connect() as db:
            gid = db.execute("INSERT INTO calc_graphs(code, name, purpose, created_by) VALUES ('MY_SIG', 'Mine', 'SIGNAL', 'alice') RETURNING id").fetchone()['id']
            h = '0' * 64
            vid = db.execute("INSERT INTO calc_graph_versions(graph_id, version, status, definition, hash, created_by, approved_by, approved_at) "
                             "VALUES (%s, 1, 'APPROVED', '{}', %s, 'alice', 'bob', now()) RETURNING id", (gid, h)).fetchone()['id']
            db.execute("UPDATE calc_graph_versions SET status = 'RETIRED', retired_by = 'bob', retired_at = now() WHERE id = %s", (vid,))
        for stmt in ("UPDATE calc_graph_versions SET definition = '{\"x\": 1}' WHERE id = %s",
                     "UPDATE calc_graph_versions SET status = 'APPROVED' WHERE id = %s",
                     'DELETE FROM calc_graph_versions WHERE id = %s'):
            with self.connect() as db:
                with self.assertRaises(Exception):
                    db.execute(stmt, (vid,))
        with self.connect() as db:
            with self.assertRaises(Exception):
                db.execute("INSERT INTO calc_graph_versions(graph_id, version, status, definition, hash, created_by) "
                           "VALUES (%s, 2, 'APPROVED', '{}', %s, 'alice')", (gid, h))
        lib = self.ok(self.alice.get('/api/calc/graphs'))['items']
        self.assertTrue(any(g['code'] == 'MY_SIG' and g['versions'][0]['status'] == 'RETIRED' for g in lib))
