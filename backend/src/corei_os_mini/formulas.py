"""Every calculation COREI makes, by stage, with its equation and where it lives in the code.

Why it exists: a desk must be able to check every number it trades on. Each stage workspace shows its formulas next to
its live numbers; this registry is the single place those formulas are written, beside the code that computes them,
so the text and the code are reviewed together.
Purpose: FORMULAS[stage] = [{name, equation, variables, notes, code}] for stages 1–8; stage 2 also lists every feature
of the feature store, stage 3 the signal library (from trading.signal_library), consensus, flips, correlation and
research, stage 4 every strategy algorithm (from trading.algorithms), the checks and decision of a strategy signal, and
the sizing and protective-exit formulas; stage 7 the execution handoff and order terms.
Must never: drift from the code — when a calculation changes, its entry here changes in the same commit (the test suite
checks that every algorithm, signal formula and feature has an entry).
Dependencies: trading.algorithms, trading.signal_library, intel.indicators (names only). Consumers: GET /api/formulas.
Architectural position: documentation served at run time.
"""
from __future__ import annotations

F = dict

STAGE1 = [
    F(name='Bar (OHLC) from ticks', equation='open = first bid in the bar · high = max(bid) · low = min(bid) · close = last bid · ticks = count · spread_avg = mean(ask − bid)',
      variables='bid, ask of every tick whose broker time falls in [bar start, bar start + period)', notes='Bid bars (the MT5 convention); exchange feeds use the last trade price.', code='md/bars.py'),
    F(name='Latency', equation='latency = receive time (UTC) − broker time converted to UTC;  p95 over the minute',
      variables='broker time offset measured per server (Setup › servers › clock)', code='md/quality.py'),
    F(name='Feed quality score (0–100)', equation='100 − min(30, 15 + 15·(p95 ÷ limit − 1)) [if p95 > limit] − 25 [if longest gap > limit] − 30·min(1, frozen ms ÷ 60 000) − 15 [if spread > limit] − min(20, 10·spikes) − 15 [if |deviation| > limit]',
      variables='limits: latency 1 500 ms, gap 60 s, stale 10 s, deviation 10 bp (Market data › Controls)', code='md/quality.py: score'),
    F(name='Spike', equation='|mid move| > k · σ of recent moves and reverses on the next update', code='md/quality.py'),
    F(name='Deviation from other sources', equation='deviation_bp = 10 000 × (mid − median mid of the other sources) ÷ median mid', code='md/collector.py'),
]

STAGE2 = [
    F(name='SMA(n)', equation='SMA = (close₁ + … + closeₙ) ÷ n', code='intel/indicators.py: sma'),
    F(name='EMA(n) — ema20, ema50, ema200', equation='EMAₜ = EMAₜ₋₁ + α·(closeₜ − EMAₜ₋₁),  α = 2 ÷ (n + 1); seeded with the SMA of the first n closes', code='intel/indicators.py: ema'),
    F(name='RSI(14) — rsi14', equation='RSI = 100 − 100 ÷ (1 + RS),  RS = Wilder avg gain ÷ Wilder avg loss;  Wilderₜ = (Wilderₜ₋₁·(n − 1) + xₜ) ÷ n', code='intel/indicators.py: rsi'),
    F(name='ATR(14) — atr14, atr_pct', equation='TR = max(high − low, |high − close₋₁|, |low − close₋₁|);  ATR = Wilder(TR, 14);  atr_pct = 100 × ATR ÷ close', code='intel/indicators.py: atr'),
    F(name='ATR% rank — atr_pct_rank100', equation='percentile of today\'s atr_pct among the last 100 bars (0–100)', code='intel/engine.py: features'),
    F(name='Bollinger(20, 2) — bb_mid, bb_upper, bb_lower, bb_width_pct', equation='mid = SMA(20);  bands = mid ± 2·σ(20) (population σ);  width% = 100 × (upper − lower) ÷ mid', code='intel/indicators.py: bollinger'),
    F(name='ADX(14), +DI, −DI', equation='+DM = high − high₋₁ if larger than low₋₁ − low and > 0;  −DM likewise;  +DI = 100·Wilder(+DM) ÷ ATR;  DX = 100·|+DI − −DI| ÷ (+DI + −DI);  ADX = Wilder(DX)', code='intel/indicators.py: adx'),
    F(name='Efficiency ratio — er20', equation='ER = |closeₜ − closeₜ₋₂₀| ÷ Σ|closeᵢ − closeᵢ₋₁| over 20 bars (1 = straight line, 0 = noise)', code='intel/indicators.py: efficiency_ratio'),
    F(name='Realised volatility — rv20_pct', equation='100 × stdev(ln(closeᵢ ÷ closeᵢ₋₁), 20 bars) × √(bars per year)', code='intel/indicators.py: realised_vol'),
    F(name='Range position — range_pos20', equation='100 × (close − low₂₀) ÷ (high₂₀ − low₂₀)', code='intel/indicators.py: range_position'),
    F(name='Returns — ret_1_pct, ret_20_pct', equation='100 × (closeₜ ÷ closeₜ₋ₖ − 1)', code='intel/engine.py'),
    F(name='MACD(12, 26, 9) — macd, macd_signal, macd_hist', equation='line = EMA(12) − EMA(26);  signal = EMA(9) of line;  hist = line − signal', code='intel/indicators.py: macd'),
    F(name='Stochastic(14, 3, 3) — stoch_k, stoch_d', equation='raw = 100 × (close − low₁₄) ÷ (high₁₄ − low₁₄);  %K = SMA₃(raw);  %D = SMA₃(%K)', code='intel/indicators.py: stochastic'),
    F(name='Donchian(20) — donchian_high20, donchian_low20', equation='high₂₀ = max(high, 20 bars);  low₂₀ = min(low, 20 bars)', code='intel/indicators.py: donchian'),
    F(name='Z-score(20) — zscore20', equation='z = (close − mean₂₀) ÷ σ₂₀', code='intel/indicators.py: zscore'),
    F(name='Volume — tick_volume, volume, rel_volume20', equation='rel_volume = volume ÷ SMA₂₀(volume) (real volume where the source has it, else ticks)', code='intel/engine.py'),
    F(name='VWAP(20) — vwap20, vwap_dev_pct', equation='VWAP = Σ((H + L + C) ÷ 3 · volume) ÷ Σ volume over 20 bars;  dev% = 100 × (close ÷ VWAP − 1)', code='intel/indicators.py: vwap'),
    F(name='OBV', equation='OBVₜ = OBVₜ₋₁ + volume if close rose, − volume if it fell', code='intel/indicators.py: obv'),
    F(name='Regime', equation='TREND_UP/DOWN if ADX ≥ 25 and ER ≥ 0.30 and EMA20 vs EMA50 agrees with +DI vs −DI;  RANGE if ADX < 20;  else TRANSITION.  '
                              'Confidence (trend) = 0.6·clamp((ADX − 20) ÷ 20) + 0.4·clamp(ER ÷ 0.6)', code='intel/regime.py: classify'),
    F(name='Volatility state', equation='HIGH if ATR% rank ≥ 80, LOW if ≤ 20, else NORMAL', code='intel/regime.py'),
    F(name='Screener attention (0–100)', equation='100 × quality × cost × (0.5·movement + 0.5·direction);  cost = clamp(1 − spread ÷ (0.2·ATR));  movement = ATR% rank ÷ 100;  direction = clamp(ADX ÷ 50)',
      code='intel/regime.py: attention'),
    F(name='Timeframes', equation='M5…D1 bars = the broker\'s own bars when as complete as M1-built ones, else M1 resampled (open = first, high = max, low = min, close = last)', code='intel/engine.py: resample'),
]

STAGE3_EXTRA = [
    F(name='Signal side (library)', equation='LONG if score ≥ +threshold;  SHORT if score ≤ −threshold;  else FLAT (threshold per definition, default 20)', code='trading/signal_library.py: direction'),
    F(name='Consensus (composite of an instrument)', equation='composite = mean(score of every active signal);  agreement = share of signals with the composite\'s sign', code='trading/signal_library.py: consensus'),
    F(name='Flip', equation='recorded when direction(scoreₜ) ≠ direction(scoreₜ₋₁) on a closed bar (once per bar)', code='trading/signal_library.py: live_update'),
    F(name='Signal correlation', equation='r(a, b) = Pearson correlation of the two score series over the same bars;  |r| ≥ 0.8 = redundant (a blend counts it twice)', code='trading/signal_library.py: correlation'),
    F(name='Research: information coefficient', equation='IC(h) = Spearman rank correlation(scoreₜ, closeₜ₊ₕ ÷ closeₜ − 1);  t = IC·√((n − 2) ÷ (1 − IC²))', code='trading/signal_library.py: research'),
    F(name='Research: hit rate, quintiles, turnover, cost', equation='hit = share of bars with |score| ≥ threshold whose h-bar return has the score\'s sign;  quintile = average h-bar return of each fifth of scores;  '
                                                                'turnover = mean |Δscore| ÷ 200 per bar;  cost = average spread ÷ average close (bp)', code='trading/signal_library.py: research'),
    F(name='Research: verdict', equation='PREDICTIVE if |IC| ≥ 0.03 and |t| ≥ 2 and edge > cost at the best horizon;  WEAK if |t| ≥ 1;  else NO_EDGE', code='trading/signal_library.py: research'),
]

STAGE3_GATE = [
    F(name='Strategy signal born', equation='a running strategy\'s target changes on a newly closed bar (or by hand); the first bar after activation is only recorded', code='trading/runtime.py'),
    F(name='Check: price fresh', equation='now − quote received time ≤ max_quote_age_seconds', code='trading/signals.py: validate'),
    F(name='Check: spread', equation='100 × (ask − bid) ÷ ATR14 ≤ max_spread_atr_pct', code='trading/signals.py: validate'),
    F(name='Check: market open, event risk, strength, duplicate', equation='market hours (Market data › markets);  no high-impact event for either currency within ±30 min;  strength ≥ min_strength;  no live signal with the same side', code='trading/signals.py: validate'),
    F(name='Decision', equation='target = LONG / SHORT / FLAT of the latest signal (or of N agreeing signals);  wanted = +size, −size or 0;  order = wanted − current position (of this deployment)', code='trading/signals.py: process'),
    F(name='Realign', equation='retry when the latest decision was not executed and position ≠ target, nothing working, attempts < realign_max_attempts (AUTO handoff only)', code='trading/runtime.py: realign_reason'),
]

STAGE4_SIZING = [
    F(name='Sizing: fixed quantity', equation='quantity = Q × deployment weight', code='trading/risk.py: size'),
    F(name='Sizing: fixed value', equation='quantity = N × weight ÷ (price × contract size × FX rate to the account currency)', code='trading/risk.py: size'),
    F(name='Sizing: % of equity at an ATR stop', equation='quantity = equity × r% × weight ÷ (k × ATR × contract size × FX);  equity = partition equity, else broker equity (real), else paper equity',
      code='trading/risk.py: size'),
    F(name='Lot rules', equation='quantity rounded down to the volume step; below the minimum → refused', code='trading/risk.py: lot'),
    F(name='Protective exits', equation='long: stop = entry − stop_atr × ATR, target = entry + take_atr × ATR (short mirrored); RISK_PCT without its own stop uses its k', code='trading/signals.py: protective_levels'),
    F(name='Backtest fill', equation='fill at the next bar\'s open;  buy = open + spread ticks × tick size (bars are bid);  commission per lot each side', code='trading/backtest.py: simulate'),
    F(name='Backtest metrics', equation='net P&L = Σ trade P&L − commission;  max drawdown = max(peak equity − equity) ÷ peak;  Sharpe = mean(bar returns) ÷ stdev × √(bars per year);  profit factor = gross win ÷ |gross loss|',
      code='trading/backtest.py'),
    F(name='Walk-forward', equation='each fold: best combination on the first in-sample % → traded on the rest;  efficiency = mean OOS score ÷ mean IS score;  ROBUST if efficiency ≥ 50% and OOS > 0',
      code='trading/optimise.py'),
    F(name='Drift', equation='DRIFT if live win rate < backtest × (1 − tol) or live profit factor < backtest × (1 − tol) or live average trade < 0 < backtest average trade (after min trades)',
      code='trading/strategy_health.py'),
]

STAGE5 = [
    F(name='Kill switches', equation='any engaged switch of the firm, account, strategy, instrument or deployment refuses new risk (closing orders pass)', code='trading/risk.py: check'),
    F(name='Limits (tightest of firm, account, strategy, instrument, deployment)', equation='order qty ≤ MAX_ORDER_QTY;  |position after| ≤ MAX_POSITION_QTY;  working orders < MAX_OPEN_ORDERS;  orders in the last 60 s < MAX_ORDERS_PER_MINUTE',
      code='trading/risk.py: check'),
    F(name='Loss and drawdown', equation='today\'s realised + unrealised ≥ −MAX_DAILY_LOSS;  drawdown = (peak equity − equity) ÷ peak ≤ MAX_DRAWDOWN_PCT', code='trading/risk.py'),
    F(name='Exposure', equation='value = |qty| × price × contract size × FX;  gross = Σ value ≤ MAX_GROSS_EXPOSURE;  per instrument ≤ MAX_INSTRUMENT_EXPOSURE;  order value ≤ MAX_ORDER_NOTIONAL', code='trading/risk.py'),
    F(name='Margin and leverage', equation='margin = Σ value ÷ account leverage (only the growing part of a position adds);  margin% = margin ÷ equity ≤ MAX_MARGIN_PCT;  leverage = gross ÷ equity ≤ MAX_LEVERAGE',
      code='trading/risk.py: margin'),
    F(name='Price deviation (fat finger)', equation='100 × |limit or stop − mid| ÷ mid ≤ MAX_PRICE_DEVIATION_PCT', code='trading/risk.py'),
    F(name='Stale price', equation='now − quote time ≤ max_quote_age_seconds (a real closing order passes)', code='trading/risk.py'),
    F(name='Value at Risk (historical)', equation='P&Lₜ = Σᵢ valueᵢ × returnᵢ,ₜ over the shared past bars;  VaR(α) = −(the ⌈(1 − α)·n⌉-th worst P&Lₜ);  ES = −mean of the worst ⌈(1 − α)·n⌉',
      variables='α = var_confidence, bars = var_lookback_bars of var_timeframe', code='trading/var.py: account_var'),
    F(name='Stress', equation='P&L = Σ value × shock% of the most specific matching shock (symbol › asset class › quote currency › all)', code='trading/var.py: stress'),
]

STAGE6 = [
    F(name='Average cost', equation='adding: avg = (qty₀·avg₀ + q·price) ÷ (qty₀ + q);  reducing: avg unchanged;  reversing: avg = fill price', code='trading/portfolio.py: apply_fill'),
    F(name='Realised P&L', equation='closed qty × (fill − avg) × sign × contract size (instrument currency)', code='trading/portfolio.py'),
    F(name='Unrealised P&L', equation='qty × (mid − avg) × contract size × FX to the account currency', code='trading/portfolio.py: positions'),
    F(name='Equity and drawdown', equation='equity = capital + Σ realised (converted) + Σ unrealised;  drawdown = (peak − equity) ÷ peak', code='trading/portfolio.py: equity'),
    F(name='Partition equity', equation='capital + realised + unrealised of the deployments assigned to it', code='trading/portfolio.py: partition_equity'),
    F(name='Allocation', equation='EQUAL: total ÷ n;  WEIGHT: total × wᵢ ÷ Σw;  RISK_PARITY: total × (1 ÷ σᵢ) ÷ Σ(1 ÷ σ), σᵢ = stdev of daily returns of the partition\'s instruments (60 days)', code='trading/partitions.py'),
    F(name='Copy size', equation='FIXED_RATIO: master fill × ratio;  EQUITY_RATIO: fill × follower equity ÷ master equity × ratio;  FIXED_QTY: Q × fill ÷ master order;  capped, rounded to the lot step', code='trading/copytrade.py'),
    F(name='Attribution', equation='share = realised of the group ÷ Σ|realised of all groups|', code='trading/strategy_health.py: attribution'),
]

STAGE7 = [
    F(name='Handoff (per deployment)', equation='AUTO: order now;  APPROVE: waits in the execution queue until approval_minutes, then EXPIRED;  SIGNAL_ONLY: recorded with its size, never ordered',
      code='trading/handoff.py: policy'),
    F(name='Executing a waiting decision', equation='order = target − position now (not at decision time);  a smaller quantity may be chosen, rounded down to the lot step;  same client order ID as automatic',
      code='trading/handoff.py: execute'),
    F(name='Order terms', equation='MARKET;  or LIMIT — MARKETABLE: buy = ask + n·tick, sell = bid − n·tick (a price cap);  PASSIVE: buy = bid − n·tick, sell = ask + n·tick;  time in force GTC / DAY / IOC',
      code='trading/handoff.py: order_terms'),
    F(name='Client order ID', equation='"C" + first 31 hex of SHA-256(seed) — the same intent always gives the same ID;  broker tag = first 20 characters', code='trading/oms.py: client_order_id'),
    F(name='Paper fill price', equation='buy = ask + slippage ticks × tick size;  sell = bid − slippage ticks × tick size;  limit at the limit price when reached;  stop at market when passed', code='trading/oms.py'),
    F(name='Slippage', equation='buy: fill − arrival ask;  sell: arrival bid − fill (positive = worse than the price when decided)', code='trading/oms.py: _fill'),
    F(name='Latency', equation='fill time − SUBMITTED time', code='trading/oms.py: _fill'),
    F(name='Routing', equation='BEST_PRICE: lowest ask to buy / highest bid to sell, tighter spread breaks ties;  LOWEST_COST: price ± average 30-day slippage;  SPLIT_EQUAL: qty ÷ n rounded to the lot step, remainder to the best',
      code='trading/routing.py'),
    F(name='UNKNOWN look-up', equation='every unknown_check_seconds: open orders, positions and 7 days of deals carrying the tag → fills, working, cancelled, or still unknown', code='trading/execution.py: check_one'),
]

STAGE8 = [
    F(name='Position reconciliation', equation='break if |Σ COREI real position − broker net position (COREI magic)| > tolerance, per broker symbol', code='trading/posttrade.py: reconcile'),
    F(name='Internal reconciliation', equation='break if position row ≠ Σ signed fills of the same account, instrument, deployment and mode', code='trading/posttrade.py: _internal'),
    F(name='TCA slippage', equation='bp = 10 000 × slippage ÷ arrival price;  cost = slippage × qty × contract size × FX', code='trading/posttrade.py: tca'),
    F(name='Fill rate, refusal rate, latency', equation='fill rate = Σ filled ÷ Σ ordered;  refusal = rejected orders ÷ orders;  p95 = 95th percentile of latency', code='trading/posttrade.py: tca'),
    F(name='Trade (round trip)', equation='opens when a book goes flat → non-flat, closes when flat again (a reversal closes one and opens the next);  net = realised + costs;  MFE/MAE = best/worst price move while open (M1 bars)',
      code='trading/posttrade.py: journal'),
]


def all_formulas() -> dict:
    from .trading import algorithms, signal_library
    algos = [F(name=v['label'], equation=v.get('equation'), notes=f"strength: {v.get('strength_equation')}", code=f'trading/algorithms.py · {k}')
             for k, v in algorithms.ALGORITHMS.items()]
    sigs = [F(name=f"{v['label']} ({k})", equation=v['equation'], notes=v['detail'], variables=', '.join(f'{a} = {b}' for a, b in v['defaults'].items()),
              code=f'trading/signal_library.py · {k}') for k, v in signal_library.KINDS.items()]
    return {'Market data': STAGE1, 'Intelligence': STAGE2, 'Signals': sigs + STAGE3_EXTRA, 'Strategies': algos + STAGE3_GATE + STAGE4_SIZING, 'Risk': STAGE5,
            'Portfolio': STAGE6, 'Execution': STAGE7, 'Post-trade': STAGE8}
