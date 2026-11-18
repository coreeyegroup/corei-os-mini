"""Formula graphs: every calculation the platform makes — all of them, stage by stage — as a small n8n graph of
inputs → steps → result that can be tried with numbers.

Why it exists: the owner wants every calculation, formula and equation present as nodes and wires, not only the ones
that run on bar series. Each entry here is one calculation of the formula registry (formulas.py), written as named
inputs (with a realistic example value and what it means) and ordered steps in the platform's own expression
language; the screen draws inputs → steps → result as a graph and works out every step for the numbers entered.
Where the platform also has an executable bar-by-bar graph (calc.builtin), the entry links to it.
Purpose: `FORMULAS` (stage → list), `get(fid)`, `evaluate(fid, values)` → every step's value (safe: calc.expr only),
`flow()` → the platform flow: stages in order with every calculation and the wires between calculations ("feeds").
Must never: execute anything but calc.expr; claim to be the code — these are the formulas as the code computes them,
with money exact in the code (Decimal) while the illustration here works in numbers; drift from formulas.py (a test
checks every registry entry has a formula graph here and that every graph evaluates on its examples).
Dependencies: calc.expr. Consumers: calc_api (/api/calc/formulas, /api/calc/flow), tests.
Architectural position: the "every formula" layer of Calculation Graphs (DEC-96).
"""
from __future__ import annotations

import re

from . import expr as E

STAGES = ['Market data', 'Intelligence', 'Signals', 'Strategies', 'Risk', 'Portfolio', 'Execution', 'Post-trade']
STAGE_NO = {s: i + 1 for i, s in enumerate(STAGES)}


def F(fid: str, name: str, inputs: list[str], steps: list[str], *, feeds: tuple = (), graph: str | None = None, code: str = '', note: str = '') -> dict:
    ins = []
    for raw in inputs:
        head, _, doc = raw.partition(':')
        n, _, ex = head.partition('=')
        ins.append({'name': n.strip(), 'example': float(ex) if ex.strip() not in ('true', 'false') else ex.strip() == 'true',
                    'kind': 'bool' if ex.strip() in ('true', 'false') else 'num', 'doc': doc.strip()})
    st = []
    for raw in steps:
        body, _, label = raw.partition('#')
        n, _, ex = body.partition('=')
        st.append({'name': n.strip(), 'expr': ex.strip(), 'label': label.strip() or n.strip()})
    return {'id': fid, 'name': name, 'inputs': ins, 'steps': st, 'feeds': list(feeds), 'graph': graph, 'code': code, 'note': note}


FORMULAS: dict[str, list[dict]] = {
    'Market data': [
        F('md_bar', 'Bar (OHLC) from ticks', ['first_bid=1.08512: first bid in the bar', 'max_bid=1.08561: highest bid', 'min_bid=1.08490: lowest bid',
                                             'last_bid=1.08530: last bid', 'ticks=184: ticks in the bar', 'sum_spread=0.0147: Σ(ask − bid) of the ticks'],
          ['open = first_bid # open', 'high = max_bid # high', 'low = min_bid # low', 'close = last_bid # close', 'spread_avg = sum_spread / ticks # average spread'],
          feeds=('in_ema', 'in_rsi', 'in_atr', 'md_quality'), code='md/bars.py', note='Bid bars (MT5 convention); exchange feeds use the last trade.'),
        F('md_latency', 'Latency', ['receive_ms=1700000000250: receive time, UTC, ms', 'broker_ms=1700007200040: broker time, ms', 'offset_ms=7200000: broker clock offset to UTC, ms'],
          ['broker_utc = broker_ms - offset_ms # broker time in UTC', 'latency = receive_ms - broker_utc # latency, ms'], feeds=('md_quality',), code='md/quality.py'),
        F('md_quality', 'Feed quality score (0–100)', ['p95=1800: latency p95, ms', 'lat_limit=1500: latency limit, ms', 'gap_s=12: longest gap, s', 'gap_limit=60: gap limit, s',
                                                      'frozen_ms=0: frozen time, ms', 'spread=0.00018: spread', 'spread_limit=0.0005: spread limit', 'spikes=1: spikes',
                                                      'dev_bp=3: deviation, bp', 'dev_limit=10: deviation limit, bp'],
          ['p_lat = if(p95 > lat_limit, min(30, 15 + 15 * (p95 / lat_limit - 1)), 0) # latency penalty',
           'p_gap = if(gap_s > gap_limit, 25, 0) # gap penalty', 'p_frozen = 30 * min(1, frozen_ms / 60000) # frozen penalty',
           'p_spread = if(spread > spread_limit, 15, 0) # spread penalty', 'p_spike = min(20, 10 * spikes) # spike penalty',
           'p_dev = if(abs(dev_bp) > dev_limit, 15, 0) # deviation penalty',
           'score = max(0, 100 - p_lat - p_gap - p_frozen - p_spread - p_spike - p_dev) # quality score'], feeds=('in_attention',), code='md/quality.py: score'),
        F('md_spike', 'Spike', ['move=0.0021: mid move', 'sigma=0.00035: σ of recent moves', 'k=5: k', 'reverses=true: reverses on the next update'],
          ['big = abs(move) > k * sigma # move above k·σ', 'spike = big and reverses # spike'], feeds=('md_quality',), code='md/quality.py'),
        F('md_deviation', 'Deviation from other sources', ['mid=1.08532: this source mid', 'median_mid=1.08529: median mid of the other sources'],
          ['dev_bp = 10000 * (mid - median_mid) / median_mid # deviation, bp'], feeds=('md_quality',), code='md/collector.py'),
    ],
    'Intelligence': [
        F('in_sma', 'SMA(n)', ['sum_close=21.7062: Σ close of the last n bars', 'n=20: bars'], ['sma = sum_close / n # SMA'], feeds=('in_bollinger', 'sig_SMA_TREND'),
          code='intel/indicators.py: sma'),
        F('in_ema', 'EMA(n) — ema20, ema50, ema200', ['prev_ema=1.08480: EMA of the bar before', 'close=1.08530: close', 'n=20: bars'],
          ['alpha = 2 / (n + 1) # α', 'ema = prev_ema + alpha * (close - prev_ema) # EMA'], feeds=('in_regime', 'in_macd', 'sig_EMA_TREND', 'st_EMA_CROSSOVER'), code='intel/indicators.py: ema'),
        F('in_rsi', 'RSI(14) — rsi14', ['prev_gain=0.00041: Wilder average gain before', 'prev_loss=0.00033: Wilder average loss before', 'change=0.00020: close − close₋₁', 'n=14: bars'],
          ['gain = (prev_gain * (n - 1) + max(change, 0)) / n # average gain', 'loss = (prev_loss * (n - 1) + max(-change, 0)) / n # average loss',
           'rs = gain / loss # RS', 'rsi = 100 - 100 / (1 + rs) # RSI'], feeds=('sig_RSI_REVERSION', 'sig_RSI_MOMENTUM', 'st_RSI_REVERSION'), code='intel/indicators.py: rsi'),
        F('in_atr', 'ATR(14) — atr14, atr_pct', ['high=1.08561: high', 'low=1.08490: low', 'prev_close=1.08505: close before', 'prev_atr=0.00092: ATR before', 'close=1.08530: close', 'n=14: bars'],
          ['tr = max(high - low, abs(high - prev_close), abs(low - prev_close)) # true range', 'atr = (prev_atr * (n - 1) + tr) / n # ATR (Wilder)',
           'atr_pct = 100 * atr / close # ATR % of price'], feeds=('in_atr_rank', 'rk_size_pct', 'st_protective', 'sig_EMA_TREND', 'sg_check_spread'), graph='FEAT_ATR_PCT',
          code='intel/indicators.py: atr'),
        F('in_atr_rank', 'ATR% rank — atr_pct_rank100', ['below=63: of the last 100 bars, how many had a lower atr_pct', 'count=100: bars in the window'],
          ['rank = 100 * below / (count - 1) # percentile 0–100'], feeds=('in_volstate', 'in_attention'), code='intel/engine.py: features'),
        F('in_bollinger', 'Bollinger(20, 2) — bb_mid, bb_upper, bb_lower, bb_width_pct', ['mid=1.08480: SMA(20)', 'sigma=0.00041: population σ(20)', 'k=2: deviations'],
          ['upper = mid + k * sigma # upper band', 'lower = mid - k * sigma # lower band', 'width_pct = 100 * (upper - lower) / mid # width %'],
          feeds=('sig_BOLLINGER_REVERSION', 'st_BOLLINGER_REVERSION'), graph='FEAT_BB_WIDTH_PCT', code='intel/indicators.py: bollinger'),
        F('in_adx', 'ADX(14), +DI, −DI', ['up_move=0.00031: high − high₋₁', 'down_move=0.00012: low₋₁ − low', 'pdm_avg=0.00028: Wilder +DM before', 'mdm_avg=0.00019: Wilder −DM before',
                                         'atr=0.00092: ATR', 'prev_adx=24: ADX before', 'n=14: bars'],
          ['pdm = if(up_move > down_move and up_move > 0, up_move, 0) # +DM', 'mdm = if(down_move > up_move and down_move > 0, down_move, 0) # −DM',
           'pdm_s = (pdm_avg * (n - 1) + pdm) / n # smoothed +DM', 'mdm_s = (mdm_avg * (n - 1) + mdm) / n # smoothed −DM',
           'pdi = 100 * pdm_s / atr # +DI', 'mdi = 100 * mdm_s / atr # −DI', 'dx = 100 * abs(pdi - mdi) / (pdi + mdi) # DX', 'adx = (prev_adx * (n - 1) + dx) / n # ADX'],
          feeds=('in_regime', 'sig_ADX_DIRECTION', 'in_attention'), code='intel/indicators.py: adx'),
        F('in_er', 'Efficiency ratio — er20', ['net=0.00210: |closeₜ − closeₜ₋₂₀|', 'path=0.00590: Σ|closeᵢ − closeᵢ₋₁| over 20 bars'],
          ['er = if(path == 0, 0, net / path) # efficiency ratio'], feeds=('in_regime',), code='intel/indicators.py: efficiency_ratio'),
        F('in_rv', 'Realised volatility — rv20_pct', ['var=0.0000000121: sample variance of 1-bar log returns, 20 bars', 'bars_per_year=6048: bars per year (H1, FX)'],
          ['rv = 100 * sqrt(var * bars_per_year) # annualised %'], code='intel/indicators.py: realised_vol'),
        F('in_range_pos', 'Range position — range_pos20', ['close=1.08530: close', 'low20=1.08300: lowest low, 20 bars', 'high20=1.08700: highest high, 20 bars'],
          ['pos = if(high20 == low20, 50, 100 * (close - low20) / (high20 - low20)) # position 0–100'], code='intel/indicators.py: range_position'),
        F('in_returns', 'Returns — ret_1_pct, ret_20_pct', ['close=1.08530: close now', 'close_k=1.08210: close k bars ago'],
          ['ret_pct = 100 * (close / close_k - 1) # return %'], graph='FEAT_RET_20_PCT', code='intel/engine.py'),
        F('in_macd', 'MACD(12, 26, 9) — macd, macd_signal, macd_hist', ['ema12=1.08520: EMA(12)', 'ema26=1.08470: EMA(26)', 'prev_signal=0.00041: signal line before', 'n_signal=9: signal EMA'],
          ['line = ema12 - ema26 # MACD line', 'signal = prev_signal + 2 / (n_signal + 1) * (line - prev_signal) # signal line', 'hist = line - signal # histogram'],
          feeds=('sig_MACD_MOMENTUM',), code='intel/indicators.py: macd'),
        F('in_stoch', 'Stochastic(14, 3, 3) — stoch_k, stoch_d', ['raw1=72: raw %K now', 'raw2=64: raw %K one bar ago', 'raw3=58: raw %K two bars ago', 'k1=64.7: %K one bar ago', 'k2=60.1: %K two bars ago'],
          ['k = (raw1 + raw2 + raw3) / 3 # %K = SMA₃(raw)', 'd = (k + k1 + k2) / 3 # %D = SMA₃(%K)'], feeds=('sig_STOCHASTIC_REVERSION',), code='intel/indicators.py: stochastic'),
        F('in_donchian', 'Donchian(20) — donchian_high20, donchian_low20', ['highest=1.08700: max(high, 20 bars)', 'lowest=1.08300: min(low, 20 bars)'],
          ['upper = highest # Donchian high', 'lower = lowest # Donchian low', 'mid = (upper + lower) / 2 # middle'], feeds=('sig_DONCHIAN_BREAKOUT', 'st_DONCHIAN_BREAKOUT'), code='intel/indicators.py: donchian'),
        F('in_zscore', 'Z-score(20) — zscore20', ['close=1.08530: close', 'mean=1.08480: mean of 20 closes', 'sigma=0.00041: population σ'],
          ['z = if(sigma == 0, 0, (close - mean) / sigma) # z-score'], feeds=('sig_ZSCORE_REVERSION',), code='intel/indicators.py: zscore'),
        F('in_volume', 'Volume — tick_volume, volume, rel_volume20', ['volume=1520: volume of the bar (real, else ticks)', 'avg20=1180: SMA(volume, 20)'],
          ['rel = volume / avg20 # relative volume'], graph='FEAT_REL_VOLUME20', code='intel/engine.py'),
        F('in_vwap', 'VWAP(20) — vwap20, vwap_dev_pct', ['sum_pv=25.6512: Σ typical price × volume, 20 bars', 'sum_v=23.64: Σ volume, 20 bars', 'close=1.08530: close'],
          ['vwap = sum_pv / sum_v # VWAP', 'dev_pct = 100 * (close / vwap - 1) # distance %'], feeds=('sig_VWAP_REVERSION',), graph='FEAT_VWAP_DEV_PCT', code='intel/indicators.py: vwap'),
        F('in_obv', 'OBV', ['prev_obv=15200: OBV before', 'volume=1520: volume', 'change=0.0002: close − close₋₁'],
          ['obv = prev_obv + volume * sign(change) # OBV'], code='intel/indicators.py: obv'),
        F('in_regime', 'Regime', ['adx=27: ADX', 'er=0.36: efficiency ratio', 'ema20=1.08520: EMA20', 'ema50=1.08440: EMA50', 'pdi=26: +DI', 'mdi=17: −DI'],
          ['up_agree = ema20 > ema50 and pdi > mdi # EMAs and DIs agree up', 'down_agree = ema20 < ema50 and pdi < mdi # EMAs and DIs agree down',
           'trend = adx >= 25 and er >= 0.30 # strong and efficient', 'trend_up = trend and up_agree # TREND_UP', 'trend_down = trend and down_agree # TREND_DOWN',
           'range = adx < 20 # RANGE', 'confidence = 0.6 * clamp((adx - 20) / 20, 0, 1) + 0.4 * clamp(er / 0.6, 0, 1) # trend confidence'],
          feeds=('in_attention',), code='intel/regime.py: classify'),
        F('in_volstate', 'Volatility state', ['rank=63: ATR% rank'], ['high = rank >= 80 # HIGH', 'low = rank <= 20 # LOW', 'normal = not high and not low # NORMAL'], code='intel/regime.py'),
        F('in_attention', 'Screener attention (0–100)', ['quality=0.95: feed quality ÷ 100', 'spread=0.00012: spread', 'atr=0.00092: ATR', 'rank=63: ATR% rank', 'adx=27: ADX'],
          ['cost = clamp(1 - spread / (0.2 * atr), 0, 1) # cost', 'movement = rank / 100 # movement', 'direction = clamp(adx / 50, 0, 1) # direction',
           'attention = 100 * quality * cost * (0.5 * movement + 0.5 * direction) # attention'], code='intel/regime.py: attention'),
        F('in_timeframes', 'Timeframes', ['m1_bars=60: M1 bars in the H1 bucket', 'broker_bars=1: broker H1 bars (complete)', 'needed=60: M1 bars for a complete H1 bar'],
          ['broker_complete = broker_bars >= 1 # broker bar available', 'built_complete = m1_bars >= needed # M1-built bar complete',
           'use_broker = broker_complete # broker bar used when as complete'], code='intel/engine.py: resample'),
    ],
}


def _signal_entries() -> list[dict]:
    out = [
        F('sig_EMA_TREND', 'EMA trend (EMA_TREND)', ['ema_fast=1.08520: EMA(fast)', 'ema_slow=1.08440: EMA(slow)', 'atr14=0.00092: ATR14', 'scale=1: ATRs for a full score'],
          ['gap = ema_fast - ema_slow # gap', 'score = clamp(100 * gap / (scale * atr14), -100, 100) # score'], feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_EMA_TREND',
          code='trading/signal_library.py · EMA_TREND'),
        F('sig_SMA_TREND', 'SMA trend (SMA_TREND)', ['sma_fast=1.08500: SMA(fast)', 'sma_slow=1.08410: SMA(slow)', 'atr14=0.00092: ATR14', 'scale=1: ATRs for a full score'],
          ['gap = sma_fast - sma_slow # gap', 'score = clamp(100 * gap / (scale * atr14), -100, 100) # score'], feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_SMA_TREND',
          code='trading/signal_library.py · SMA_TREND'),
        F('sig_MACD_MOMENTUM', 'MACD momentum (MACD_MOMENTUM)', ['histogram=0.00012: MACD histogram', 'atr14=0.00092: ATR14', 'scale=0.5: ATRs for a full score'],
          ['score = clamp(100 * histogram / (scale * atr14), -100, 100) # score'], feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_MACD_MOMENTUM',
          code='trading/signal_library.py · MACD_MOMENTUM'),
        F('sig_RSI_REVERSION', 'RSI reversion (RSI_REVERSION)', ['rsi=68: RSI'], ['score = clamp(-2 * (rsi - 50), -100, 100) # score (high RSI → short)'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_RSI_REVERSION', code='trading/signal_library.py · RSI_REVERSION'),
        F('sig_RSI_MOMENTUM', 'RSI momentum (RSI_MOMENTUM)', ['rsi=68: RSI'], ['score = clamp(2 * (rsi - 50), -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_RSI_MOMENTUM', code='trading/signal_library.py · RSI_MOMENTUM'),
        F('sig_BOLLINGER_REVERSION', 'Bollinger reversion (BOLLINGER_REVERSION)', ['close=1.08530: close', 'middle=1.08480: middle band', 'upper=1.08562: upper band', 'deviations=2: deviations'],
          ['sigma = (upper - middle) / deviations # σ', 'z = (close - middle) / sigma # z', 'score = clamp(-100 * z / deviations, -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_BOLLINGER_REVERSION', code='trading/signal_library.py · BOLLINGER_REVERSION'),
        F('sig_ZSCORE_REVERSION', 'Z-score reversion (ZSCORE_REVERSION)', ['z=1.2: z-score', 'zmax=2: z for a full score'], ['score = clamp(-100 * z / zmax, -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_ZSCORE_REVERSION', code='trading/signal_library.py · ZSCORE_REVERSION'),
        F('sig_DONCHIAN_BREAKOUT', 'Donchian breakout (DONCHIAN_BREAKOUT)', ['close=1.08690: close', 'channel_high=1.08700: highest high of the previous n bars', 'channel_low=1.08300: lowest low'],
          ['position = if(channel_high == channel_low, 0.5, (close - channel_low) / (channel_high - channel_low)) # position', 'score = clamp(200 * (position - 0.5), -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_DONCHIAN_BREAKOUT', code='trading/signal_library.py · DONCHIAN_BREAKOUT'),
        F('sig_MOMENTUM_TSTAT', 'Momentum (t-stat) (MOMENTUM_TSTAT)', ['log_return=0.0029: ln(close ÷ close n ago)', 'sigma_1bar=0.00041: sample σ of 1-bar log returns', 'n=20: bars', 'scale=2: t for a full score'],
          ['tstat = if(sigma_1bar == 0, 0, log_return / (sigma_1bar * sqrt(n))) # t-statistic', 'score = clamp(100 * tstat / scale, -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_MOMENTUM_TSTAT', code='trading/signal_library.py · MOMENTUM_TSTAT'),
        F('sig_STOCHASTIC_REVERSION', 'Stochastic reversion (STOCHASTIC_REVERSION)', ['percent_k=78: %K'], ['score = clamp(-2 * (percent_k - 50), -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_STOCHASTIC_REVERSION', code='trading/signal_library.py · STOCHASTIC_REVERSION'),
        F('sig_ADX_DIRECTION', 'ADX direction (ADX_DIRECTION)', ['adx=27: ADX', 'plus_di=26: +DI', 'minus_di=17: −DI', 'full_at=30: ADX for full strength'],
          ['di_balance = (plus_di - minus_di) / (plus_di + minus_di) # DI balance', 'factor = min(1, adx / full_at) # ADX factor', 'score = clamp(100 * di_balance * factor, -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_ADX_DIRECTION', code='trading/signal_library.py · ADX_DIRECTION'),
        F('sig_VWAP_REVERSION', 'VWAP reversion (VWAP_REVERSION)', ['close=1.08530: close', 'vwap=1.08510: VWAP', 'atr14=0.00092: ATR14', 'scale=1: ATRs for a full score'],
          ['distance = close - vwap # distance', 'score = clamp(-100 * distance / (scale * atr14), -100, 100) # score'],
          feeds=('sig_side', 'sig_consensus', 'st_SIGNAL_BLEND'), graph='SIG_VWAP_REVERSION', code='trading/signal_library.py · VWAP_REVERSION'),
        F('sig_side', 'Signal side (library)', ['score=34.2: score', 'threshold=20: threshold'],
          ['long = score >= threshold # LONG', 'short = score <= -threshold # SHORT', 'flat = not long and not short # FLAT'], feeds=('sig_flip',), code='trading/signal_library.py: direction'),
        F('sig_consensus', 'Consensus (composite of an instrument)', ['sum_scores=148: Σ scores of the active signals', 'n=12: active signals', 'same_sign=8: signals with the composite\'s sign'],
          ['composite = sum_scores / n # composite', 'agreement = 100 * same_sign / n # agreement %'], code='trading/signal_library.py: consensus'),
        F('sig_flip', 'Flip', ['dir_now=1: direction now (+1 long, 0 flat, −1 short)', 'dir_before=0: direction on the bar before'], ['flip = dir_now != dir_before # flip recorded'],
          code='trading/signal_library.py: live_update'),
        F('sig_corr', 'Signal correlation', ['cov=410: covariance of the two score series', 'sd_a=28: σ of a', 'sd_b=21: σ of b'],
          ['r = cov / (sd_a * sd_b) # Pearson r', 'redundant = abs(r) >= 0.8 # counts twice in a blend'], code='trading/signal_library.py: correlation'),
        F('sig_ic', 'Research: information coefficient', ['ic=0.045: Spearman rank correlation score vs h-bar return', 'n=2400: bars'],
          ['tstat = ic * sqrt((n - 2) / (1 - ic ^ 2)) # t-stat'], feeds=('sig_verdict',), code='trading/signal_library.py: research'),
        F('sig_hit', 'Research: hit rate, quintiles, turnover, cost', ['hits=640: bars with |score| ≥ threshold whose return had the score\'s sign', 'active=1180: bars with |score| ≥ threshold',
                                                                    'sum_dscore=9600: Σ|Δscore|', 'bars=2400: bars', 'avg_spread=0.00012: average spread', 'avg_close=1.0850: average close'],
          ['hit_rate = 100 * hits / active # hit rate %', 'turnover = sum_dscore / bars / 200 # turnover per bar', 'cost_bp = 10000 * avg_spread / avg_close # cost, bp'],
          feeds=('sig_verdict',), code='trading/signal_library.py: research'),
        F('sig_verdict', 'Research: verdict', ['ic=0.045: IC at the best horizon', 'tstat=2.21: t-stat', 'edge_bp=1.6: edge, bp', 'cost_bp=1.1: cost, bp'],
          ['predictive = abs(ic) >= 0.03 and abs(tstat) >= 2 and edge_bp > cost_bp # PREDICTIVE', 'weak = not predictive and abs(tstat) >= 1 # WEAK'],
          code='trading/signal_library.py: research'),
    ]
    return out


def _strategy_entries() -> list[dict]:
    return [
        F('st_MA_CROSSOVER', 'Moving-average crossover (SMA)', ['fast_before=1.08470: SMA fast, bar before', 'slow_before=1.08480: SMA slow, bar before', 'fast=1.08500: SMA fast', 'slow=1.08482: SMA slow',
                                                                 'atr=0.00092: ATR14'],
          ['long = fast_before <= slow_before and fast > slow # crossed above → LONG', 'short = fast_before >= slow_before and fast < slow # crossed below → SHORT',
           'strength = clamp(round(100 * abs(fast - slow) / atr), 10, 100) # strength'], feeds=('sg_born',), graph='STRAT_MA_CROSSOVER', code='trading/algorithms.py · MA_CROSSOVER'),
        F('st_EMA_CROSSOVER', 'EMA crossover', ['fast_before=1.08470: EMA fast, bar before', 'slow_before=1.08480: EMA slow, bar before', 'fast=1.08500: EMA fast', 'slow=1.08482: EMA slow',
                                                'atr=0.00092: ATR14'],
          ['long = fast_before <= slow_before and fast > slow # crossed above → LONG', 'short = fast_before >= slow_before and fast < slow # crossed below → SHORT',
           'strength = clamp(round(100 * abs(fast - slow) / atr), 10, 100) # strength'], feeds=('sg_born',), graph='STRAT_EMA_CROSSOVER', code='trading/algorithms.py · EMA_CROSSOVER'),
        F('st_RSI_REVERSION', 'RSI mean reversion', ['rsi_before=28: RSI bar before', 'rsi=31: RSI', 'lower=30: lower level', 'upper=70: upper level', 'exit_level=50: exit level'],
          ['long = rsi_before < lower and rsi >= lower # crosses back above lower → LONG', 'short = rsi_before > upper and rsi <= upper # crosses back below upper → SHORT',
           'strength = clamp(2 * (exit_level - min(rsi_before, rsi)), 10, 100) # strength (long)'], feeds=('sg_born',), code='trading/algorithms.py · RSI_REVERSION'),
        F('st_DONCHIAN_BREAKOUT', 'Donchian channel breakout', ['close=1.08720: close', 'entry_high=1.08700: max high of the previous entry bars', 'entry_low=1.08300: min low', 'atr=0.00092: ATR14'],
          ['long = close > entry_high # breakout up → LONG', 'short = close < entry_low # breakout down → SHORT', 'strength = clamp(100 * (close - entry_high) / atr, 10, 100) # strength (long)'],
          feeds=('sg_born',), code='trading/algorithms.py · DONCHIAN_BREAKOUT'),
        F('st_BOLLINGER_REVERSION', 'Bollinger band reversion', ['close_before=1.08390: close bar before', 'lower_before=1.08398: lower band before', 'close=1.08405: close', 'lower=1.08399: lower band',
                                                                  'middle=1.08480: middle', 'upper=1.08562: upper'],
          ['long = close_before < lower_before and close >= lower # back inside from below → LONG', 'strength = clamp(200 * abs(close - middle) / (upper - lower), 10, 100) # strength'],
          feeds=('sg_born',), code='trading/algorithms.py · BOLLINGER_REVERSION'),
        F('st_RULES', 'Rule builder (any feature)', ['rsi=28: a feature (e.g. RSI)', 'level=30: its level', 'ema_fast=1.08500: another feature', 'ema_slow=1.08482: and another'],
          ['cond1 = rsi < level # condition 1', 'cond2 = ema_fast > ema_slow # condition 2', 'long_entry = cond1 and cond2 # every long-entry condition holds'],
          feeds=('sg_born',), code='trading/algorithms.py · RULES', note='strength = 60 on entry, 50 on exit'),
        F('st_SIGNAL_BLEND', 'Signal blend (Stage 3 signals combined)', ['s1=42: score of signal 1', 'w1=2: weight 1', 's2=-10: score of signal 2', 'w2=1: weight 2', 'entry=30: entry', 'exit=10: exit'],
          ['combined = (w1 * s1 + w2 * s2) / (abs(w1) + abs(w2)) # combined', 'long = combined >= entry # LONG', 'short = combined <= -entry # SHORT',
           'flat = abs(combined) < exit # back inside ±exit → FLAT', 'strength = clamp(abs(combined), 10, 100) # strength'], feeds=('sg_born',), code='trading/algorithms.py · SIGNAL_BLEND'),
        F('st_GRAPH', 'Calculation graph (an approved strategy-rule graph)', ['target_long=true: the graph says LONG', 'held_short=true: currently SHORT', 'graph_strength=64: the graph\'s strength'],
          ['change = target_long and held_short # target differs from the one held', 'strength = if(change, graph_strength, 0) # strength'], feeds=('sg_born',), code='trading/algorithms.py · GRAPH'),
        F('sg_born', 'Strategy signal born', ['target_now=1: target now (+1/0/−1)', 'target_before=0: target on the bar before', 'first_bar=false: first bar after activation'],
          ['changed = target_now != target_before # target changed', 'signal = changed and not first_bar # a signal is born'], feeds=('sg_check_fresh', 'sg_check_spread', 'sg_check_other'),
          code='trading/runtime.py'),
        F('sg_check_fresh', 'Check: price fresh', ['age_s=4: now − quote received, s', 'max_age=60: max_quote_age_seconds'], ['fresh = age_s <= max_age # price fresh'],
          feeds=('sg_decision',), code='trading/signals.py: validate'),
        F('sg_check_spread', 'Check: spread', ['ask=1.08542: ask', 'bid=1.08530: bid', 'atr=0.00092: ATR14', 'max_pct=25: max_spread_atr_pct'],
          ['spread_pct = 100 * (ask - bid) / atr # spread as % of ATR', 'ok = spread_pct <= max_pct # spread acceptable'], feeds=('sg_decision',), code='trading/signals.py: validate'),
        F('sg_check_other', 'Check: market open, event risk, strength, duplicate', ['market_open=true: market open', 'event_near=false: high-impact event within ±30 min',
                                                                                     'strength=64: signal strength', 'min_strength=40: min_strength', 'duplicate=false: same side already live'],
          ['ok = market_open and not event_near and strength >= min_strength and not duplicate # all checks pass'], feeds=('sg_decision',), code='trading/signals.py: validate'),
        F('sg_decision', 'Decision', ['target=1: target (+1 long, 0 flat, −1 short)', 'size=0.10: size', 'position=-0.10: position of this deployment'],
          ['wanted = target * size # wanted position', 'order = wanted - position # order quantity (+ buy, − sell)'], feeds=('rk_kill', 'rk_limits', 'ex_handoff'), code='trading/signals.py: process'),
        F('sg_realign', 'Realign', ['executed=false: latest decision executed', 'position=0: position', 'target=0.10: target', 'working=false: something working', 'attempts=1: attempts', 'max_attempts=3: realign_max_attempts'],
          ['retry = not executed and position != target and not working and attempts < max_attempts # retry'], code='trading/runtime.py: realign_reason'),
        F('rk_size_fixed', 'Sizing: fixed quantity', ['q=0.10: quantity', 'weight=1: deployment weight'], ['quantity = q * weight # quantity'], feeds=('rk_lot',), code='trading/risk.py: size'),
        F('rk_size_value', 'Sizing: fixed value', ['notional=10000: value N', 'weight=1: weight', 'price=1.0853: price', 'contract=100000: contract size', 'fx=1: FX to the account currency'],
          ['quantity = notional * weight / (price * contract * fx) # quantity'], feeds=('rk_lot',), code='trading/risk.py: size'),
        F('rk_size_pct', 'Sizing: % of equity at an ATR stop', ['equity=10000: equity', 'r_pct=1: risk %', 'weight=1: weight', 'k=2: ATR multiple', 'atr=0.00092: ATR14',
                                                               'contract=100000: contract size', 'fx=1: FX'],
          ['risk_money = equity * r_pct / 100 * weight # money at risk', 'stop_distance = k * atr # stop distance', 'quantity = risk_money / (stop_distance * contract * fx) # quantity'],
          feeds=('rk_lot',), code='trading/risk.py: size'),
        F('rk_lot', 'Lot rules', ['quantity=0.5434: quantity before rounding', 'step=0.01: volume step', 'minimum=0.01: minimum'],
          ['rounded = floor(quantity / step) * step # rounded down to the step', 'refused = rounded < minimum # below the minimum → refused'], feeds=('rk_limits',), code='trading/risk.py: lot'),
        F('st_protective', 'Protective exits', ['entry=1.08542: entry', 'atr=0.00092: ATR14', 'stop_atr=2: stop, ATRs', 'take_atr=3: target, ATRs'],
          ['stop = entry - stop_atr * atr # stop (long)', 'target = entry + take_atr * atr # target (long)'], feeds=('ex_terms',), code='trading/signals.py: protective_levels'),
        F('st_bt_fill', 'Backtest fill', ['next_open=1.08530: opening bid of the next bar', 'spread_ticks=12: spread, ticks', 'tick=0.00001: tick size', 'commission=3.5: commission per lot each side', 'lots=0.1: lots'],
          ['buy_fill = next_open + spread_ticks * tick # buy fill', 'cost = commission * lots # commission'], feeds=('st_bt_metrics',), code='trading/backtest.py: simulate'),
        F('st_bt_metrics', 'Backtest metrics', ['gross_win=1820: Σ winning trades', 'gross_loss=-1110: Σ losing trades', 'commission=96: commission', 'peak=10900: peak equity',
                                                 'trough=10450: equity at the worst point after it', 'mean_ret=0.00004: mean bar return', 'sd_ret=0.0011: σ of bar returns', 'bars_per_year=6048: bars per year'],
          ['net = gross_win + gross_loss - commission # net P&L', 'max_dd = 100 * (peak - trough) / peak # max drawdown %', 'sharpe = mean_ret / sd_ret * sqrt(bars_per_year) # Sharpe',
           'profit_factor = gross_win / abs(gross_loss) # profit factor'], code='trading/backtest.py'),
        F('st_walkforward', 'Walk-forward', ['oos=0.62: mean out-of-sample score', 'is_score=1.05: mean in-sample score'],
          ['efficiency = 100 * oos / is_score # efficiency %', 'robust = efficiency >= 50 and oos > 0 # ROBUST'], code='trading/optimise.py'),
        F('st_drift', 'Drift', ['live_win=41: live win rate %', 'bt_win=55: backtest win rate %', 'live_pf=1.05: live profit factor', 'bt_pf=1.6: backtest profit factor', 'tol=20: tolerance %',
                                 'live_avg=3: live average trade', 'bt_avg=12: backtest average trade'],
          ['f = 1 - tol / 100 # tolerance factor', 'drift = live_win < bt_win * f or live_pf < bt_pf * f or (live_avg < 0 and 0 < bt_avg) # DRIFT'], code='trading/strategy_health.py'),
    ]


FORMULAS['Signals'] = _signal_entries()
FORMULAS['Strategies'] = _strategy_entries()
FORMULAS['Risk'] = [
    F('rk_kill', 'Kill switches', ['firm=false: firm switch engaged', 'account=false: account switch', 'strategy=false: strategy switch', 'instrument=false: instrument switch',
                                   'deployment=false: deployment switch', 'closing=false: the order only closes'],
      ['engaged = firm or account or strategy or instrument or deployment # any switch engaged', 'refuse = engaged and not closing # new risk refused'], feeds=('ex_terms',),
      code='trading/risk.py: check'),
    F('rk_limits', 'Limits (tightest of firm, account, strategy, instrument, deployment)', ['qty=0.10: order quantity', 'max_order=1: MAX_ORDER_QTY', 'position_after=0.30: position after',
                                                                                          'max_position=0.5: MAX_POSITION_QTY', 'working=1: working orders', 'max_open=5: MAX_OPEN_ORDERS',
                                                                                          'last_minute=2: orders in the last 60 s', 'per_minute=10: MAX_ORDERS_PER_MINUTE'],
      ['ok = qty <= max_order and abs(position_after) <= max_position and working < max_open and last_minute < per_minute # within every limit'], feeds=('ex_terms',),
      code='trading/risk.py: check'),
    F('rk_loss', 'Loss and drawdown', ['realised=-120: realised today', 'unrealised=-60: unrealised', 'max_loss=500: MAX_DAILY_LOSS', 'peak=10500: peak equity', 'equity=10180: equity', 'max_dd=5: MAX_DRAWDOWN_PCT'],
      ['loss_ok = realised + unrealised >= -max_loss # daily loss within limit', 'drawdown = 100 * (peak - equity) / peak # drawdown %', 'dd_ok = drawdown <= max_dd # drawdown within limit'],
      feeds=('ex_terms',), code='trading/risk.py'),
    F('rk_exposure', 'Exposure', ['qty=0.3: position', 'price=1.0853: price', 'contract=100000: contract size', 'fx=1: FX', 'other_gross=40000: gross of other positions',
                                  'max_gross=200000: MAX_GROSS_EXPOSURE', 'max_instrument=60000: MAX_INSTRUMENT_EXPOSURE'],
      ['value = abs(qty) * price * contract * fx # position value', 'gross = other_gross + value # gross exposure', 'ok = gross <= max_gross and value <= max_instrument # within exposure limits'],
      feeds=('rk_margin', 'rk_var'), code='trading/risk.py'),
    F('rk_margin', 'Margin and leverage', ['gross=72559: gross value', 'leverage=30: account leverage', 'equity=10180: equity', 'max_margin_pct=50: MAX_MARGIN_PCT', 'max_leverage=10: MAX_LEVERAGE'],
      ['margin = gross / leverage # margin', 'margin_pct = 100 * margin / equity # margin %', 'lev = gross / equity # leverage used',
       'ok = margin_pct <= max_margin_pct and lev <= max_leverage # within margin and leverage'], code='trading/risk.py: margin'),
    F('rk_deviation', 'Price deviation (fat finger)', ['price=1.0950: limit or stop price', 'mid=1.0853: mid', 'max_pct=0.5: MAX_PRICE_DEVIATION_PCT'],
      ['dev_pct = 100 * abs(price - mid) / mid # distance from the market %', 'ok = dev_pct <= max_pct # within the limit'], code='trading/risk.py'),
    F('rk_stale', 'Stale price', ['age_s=4: now − quote time, s', 'max_age=60: max_quote_age_seconds', 'real_closing=false: a real closing order'],
      ['ok = age_s <= max_age or real_closing # price fresh enough'], code='trading/risk.py'),
    F('rk_var', 'Value at Risk (historical)', ['worst_k=-185: the ⌈(1 − α)·n⌉-th worst P&L of the past bars', 'mean_worst=-240: mean of the worst ⌈(1 − α)·n⌉'],
      ['var = -worst_k # VaR', 'es = -mean_worst # expected shortfall'], code='trading/var.py: account_var'),
    F('rk_stress', 'Stress', ['value=32559: position value', 'shock_pct=-5: shock of the most specific matching rule %'], ['pnl = value * shock_pct / 100 # stressed P&L'],
      code='trading/var.py: stress'),
]
FORMULAS['Portfolio'] = [
    F('pf_avg', 'Average cost', ['qty0=0.2: position', 'avg0=1.08400: average', 'q=0.1: fill quantity (same side)', 'price=1.08542: fill price'],
      ['avg = (qty0 * avg0 + q * price) / (qty0 + q) # new average (adding)'], feeds=('pf_realised', 'pf_unrealised'), code='trading/portfolio.py: apply_fill'),
    F('pf_realised', 'Realised P&L', ['closed=0.1: closed quantity', 'fill=1.08600: fill price', 'avg=1.08447: average cost', 'side=1: +1 closing a long, −1 a short', 'contract=100000: contract size'],
      ['realised = closed * (fill - avg) * side * contract # realised (instrument currency)'], feeds=('pf_equity', 'pf_attribution'), code='trading/portfolio.py'),
    F('pf_unrealised', 'Unrealised P&L', ['qty=0.3: position (signed)', 'mid=1.08560: mid', 'avg=1.08447: average', 'contract=100000: contract size', 'fx=1: FX to the account currency'],
      ['unrealised = qty * (mid - avg) * contract * fx # unrealised (account currency)'], feeds=('pf_equity',), code='trading/portfolio.py: positions'),
    F('pf_equity', 'Equity and drawdown', ['capital=10000: capital', 'realised=153: Σ realised (converted)', 'unrealised=339: Σ unrealised', 'peak=10600: peak equity'],
      ['equity = capital + realised + unrealised # equity', 'drawdown = 100 * (peak - equity) / peak # drawdown %'], feeds=('rk_loss',), code='trading/portfolio.py: equity'),
    F('pf_partition', 'Partition equity', ['capital=5000: partition capital', 'realised=80: realised of its deployments', 'unrealised=40: unrealised of its deployments'],
      ['equity = capital + realised + unrealised # partition equity'], feeds=('rk_size_pct',), code='trading/portfolio.py: partition_equity'),
    F('pf_allocation', 'Allocation', ['total=20000: total capital', 'w1=2: weight 1', 'w2=1: weight 2', 'sd1=0.006: σ of partition 1', 'sd2=0.012: σ of partition 2'],
      ['equal = total / 2 # EQUAL', 'weight1 = total * w1 / (w1 + w2) # WEIGHT (partition 1)', 'parity1 = total * (1 / sd1) / (1 / sd1 + 1 / sd2) # RISK_PARITY (partition 1)'],
      code='trading/partitions.py'),
    F('pf_copy', 'Copy size', ['fill=0.30: master fill', 'ratio=0.5: ratio', 'f_equity=5000: follower equity', 'm_equity=20000: master equity', 'step=0.01: volume step'],
      ['fixed_ratio = floor(fill * ratio / step) * step # FIXED_RATIO', 'equity_ratio = floor(fill * f_equity / m_equity * ratio / step) * step # EQUITY_RATIO'],
      code='trading/copytrade.py'),
    F('pf_attribution', 'Attribution', ['group=320: realised of the group', 'all_abs=900: Σ|realised of all groups|'], ['share = 100 * group / all_abs # share %'],
      code='trading/strategy_health.py: attribution'),
]
FORMULAS['Execution'] = [
    F('ex_handoff', 'Handoff (per deployment)', ['auto=false: policy AUTO', 'approve=true: policy APPROVE', 'minutes_waiting=4: minutes waiting', 'window=10: approval_minutes'],
      ['order_now = auto # AUTO orders at once', 'waits = approve and minutes_waiting < window # waits in the queue', 'expired = approve and minutes_waiting >= window # EXPIRED'],
      feeds=('ex_execute',), code='trading/handoff.py: policy'),
    F('ex_execute', 'Executing a waiting decision', ['target=0.10: target', 'position_now=0.04: position now', 'chosen=0.05: smaller quantity chosen (0 = all)', 'step=0.01: volume step'],
      ['order = target - position_now # order = target − position now', 'qty = if(chosen > 0, min(chosen, abs(order)), abs(order)) # quantity', 'rounded = floor(qty / step) * step # rounded down to the step'],
      feeds=('ex_terms',), code='trading/handoff.py: execute'),
    F('ex_terms', 'Order terms', ['ask=1.08542: ask', 'bid=1.08530: bid', 'n=2: offset ticks', 'tick=0.00001: tick size'],
      ['marketable_buy = ask + n * tick # LIMIT MARKETABLE buy (price cap)', 'marketable_sell = bid - n * tick # MARKETABLE sell', 'passive_buy = bid - n * tick # PASSIVE buy',
       'passive_sell = ask + n * tick # PASSIVE sell'], feeds=('ex_cid', 'ex_paper', 'ex_algo'), code='trading/handoff.py: order_terms'),
    F('ex_cid', 'Client order ID', ['seed_hash=12345678: first 31 hex of SHA-256(seed), as a number for illustration', 'same_seed=true: the same intent again'],
      ['duplicate = same_seed # the same intent gives the same ID → never a second order'], code='trading/oms.py: client_order_id',
      note='The real ID is "C" + 31 hex of SHA-256(seed); the broker tag is its first 20 characters.'),
    F('ex_paper', 'Paper fill price', ['ask=1.08542: ask', 'bid=1.08530: bid', 'slip_ticks=1: paper_slippage_ticks', 'tick=0.00001: tick size'],
      ['buy = ask + slip_ticks * tick # buy fill', 'sell = bid - slip_ticks * tick # sell fill'], feeds=('ex_slippage',), code='trading/oms.py'),
    F('ex_slippage', 'Slippage', ['fill=1.08543: fill price', 'arrival_ask=1.08542: ask when decided', 'arrival_bid=1.08530: bid when decided'],
      ['buy_slip = fill - arrival_ask # buy slippage (positive = worse)', 'sell_slip = arrival_bid - fill # sell slippage'], feeds=('pt_tca',), code='trading/oms.py: _fill'),
    F('ex_latency', 'Latency', ['fill_ms=1700000000420: fill time, ms', 'submitted_ms=1700000000180: SUBMITTED time, ms'], ['latency = fill_ms - submitted_ms # latency, ms'],
      feeds=('pt_rates',), code='trading/oms.py: _fill'),
    F('ex_routing', 'Routing', ['ask_a=1.08542: ask at A', 'ask_b=1.08545: ask at B', 'slip_a=0.00003: A average slippage', 'slip_b=0.00000: B average slippage'],
      ['best_price_a = ask_a <= ask_b # BEST_PRICE picks A', 'cost_a = ask_a + slip_a # LOWEST_COST: A', 'cost_b = ask_b + slip_b # LOWEST_COST: B', 'lowest_cost_a = cost_a <= cost_b # picks A'],
      code='trading/routing.py'),
    F('ex_unknown', 'UNKNOWN look-up', ['found_fill=false: deal with the tag found', 'found_open=false: working order with the tag found', 'found_cancel=true: cancelled order found'],
      ['filled = found_fill # → fills', 'working = not found_fill and found_open # → working', 'cancelled = not found_fill and not found_open and found_cancel # → cancelled'],
      code='trading/execution.py: check_one'),
    F('ex_algo', 'Execution algorithm child (TWAP / VWAP / POV / iceberg)', ['q=1.0: whole quantity', 'due=6: TWAP slices due', 'slices=10: slices', 'filled=0.4: filled', 'working=0: working',
                                                                              'step=0.01: volume step', 'pct=10: POV participation %', 'volume=3.2: market volume since start'],
      ['twap_target = q * due / slices # TWAP target', 'pov_target = min(q, pct / 100 * volume) # POV target',
       'child = floor(max(0, twap_target - filled - working) / step) * step # next child (TWAP)'], feeds=('ex_paper',), code='trading/algos.py'),
]
FORMULAS['Post-trade'] = [
    F('pt_recon', 'Position reconciliation', ['corei=0.30: COREI real position', 'broker=0.30: broker net position (COREI magic)', 'tolerance=0.001: tolerance'],
      ['diff = abs(corei - broker) # difference', 'break = diff > tolerance # break'], code='trading/posttrade.py: reconcile'),
    F('pt_internal', 'Internal reconciliation', ['position=0.30: position row', 'fills=0.30: Σ signed fills'], ['break = position != fills # break'], code='trading/posttrade.py: _internal'),
    F('pt_tca', 'TCA slippage', ['slippage=0.00002: slippage', 'arrival=1.08542: arrival price', 'qty=0.1: quantity', 'contract=100000: contract size', 'fx=1: FX'],
      ['bp = 10000 * slippage / arrival # slippage, bp', 'cost = slippage * qty * contract * fx # cost, money'], code='trading/posttrade.py: tca'),
    F('pt_rates', 'Fill rate, refusal rate, latency', ['filled=9.5: Σ filled quantity', 'ordered=10: Σ ordered quantity', 'rejected=2: rejected orders', 'orders=40: orders'],
      ['fill_rate = 100 * filled / ordered # fill rate %', 'refusal = 100 * rejected / orders # refusal rate %'], code='trading/posttrade.py: tca'),
    F('pt_trade', 'Trade (round trip)', ['realised=153: realised', 'costs=-7: costs', 'best=1.08710: best price while open', 'worst=1.08380: worst price while open', 'entry=1.08447: entry'],
      ['net = realised + costs # net', 'mfe = best - entry # MFE (long)', 'mae = entry - worst # MAE (long)'], code='trading/posttrade.py: journal'),
]

# The registry names each formula graph stands for (for the coverage check and the "formula" panels).
ALIASES = {'ex_algo': None, 'st_GRAPH': 'Calculation graph (an approved strategy-rule graph)'}
_BY_ID = {f['id']: {**f, 'stage': s} for s, fs in FORMULAS.items() for f in fs}


def get(fid: str) -> dict | None:
    return _BY_ID.get(fid)


def _kinds(f: dict, upto: int) -> dict[str, str]:
    k = {i['name']: i['kind'] for i in f['inputs']}
    for st in f['steps'][:upto]:
        k[st['name']] = st.get('kind') or 'num'
    return k


def compile_all() -> None:
    """Parse every step once (with the names before it); records each step's kind. Raises on any error."""
    for f in _BY_ID.values():
        for n, st in enumerate(f['steps']):
            try:
                x = E.parse(st['expr'], _kinds(f, n))
            except E.ExprError as exc:
                raise E.ExprError(f"{f['id']} step {st['name']}: {exc}") from None
            st['kind'] = x.kind
            st['uses'] = sorted(x.used)


compile_all()


def evaluate(fid: str, values: dict | None = None) -> dict:
    """Every step worked out for the given inputs (the examples where a value is not given)."""
    f = _BY_ID[fid]
    env: dict[str, list] = {}
    for i in f['inputs']:
        v = (values or {}).get(i['name'], i['example'])
        if i['kind'] == 'bool':
            v = v if isinstance(v, bool) else str(v).lower() in ('true', '1', 'yes')
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = None
        env[i['name']] = [v]
    out = {}
    for n, st in enumerate(f['steps']):
        x = E.parse(st['expr'], _kinds(f, n))
        v = x.at(env, 0)
        env[st['name']] = [v]
        out[st['name']] = v
    return {'inputs': {k: v[0] for k, v in env.items() if k in {i['name'] for i in f['inputs']}}, 'steps': out}


def worked(f: dict, values: dict) -> dict[str, str]:
    """Each step's expression with the numbers put in."""
    out = {}
    for st in f['steps']:
        text = st['expr']
        for name in sorted(st.get('uses', []), key=len, reverse=True):
            v = values.get(name)
            text = re.sub(rf'\b{name}\b', '—' if v is None else (('true' if v else 'false') if isinstance(v, bool) else f'{v:.6g}'), text)
        out[st['name']] = text
    return out


def flow() -> dict:
    """The platform as one flow: each stage a group, each formula a node, wires from 'feeds' (and stage to stage)."""
    nodes, edges = [], []
    for s in STAGES:
        for f in FORMULAS.get(s, []):
            nodes.append({'id': f['id'], 'stage': s, 'stage_no': STAGE_NO[s], 'name': f['name'], 'graph': f['graph'], 'code': f['code'],
                          'result': f['steps'][-1]['label'] if f['steps'] else '', 'equation': '; '.join(f"{st['label']} = {st['expr']}" for st in f['steps'][-2:])})
            for t in f['feeds']:
                if t in _BY_ID:
                    edges.append({'from': f['id'], 'to': t})
    return {'stages': [{'name': s, 'no': STAGE_NO[s], 'count': len(FORMULAS.get(s, []))} for s in STAGES], 'nodes': nodes, 'edges': edges}


def catalogue() -> dict:
    return {s: [{**{k: v for k, v in f.items()}, 'stage': s} for f in FORMULAS.get(s, [])] for s in STAGES}
