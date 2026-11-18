# Calculation inventory

**Purpose.** Every calculation the platform makes for a decision, where it lives in the code today, and which phase of
Calculation Graphs (DEC-93) turns it into a graph. The tables below are generated from the formula registry
(`backend/src/corei_os_mini/formulas.py`, which is checked against the code by `tests/test_signal_library.py`) and
were regenerated for release 0.50.0; the code locations were checked by reading each module.

**Rule while migrating.** The code stays the reference until a test proves the graph equals it — to 1e-9 for scores and
features, to the cent for backtests. Only then does the graph replace the formula text on a screen, and only then may a
stage run the graph instead of the code. Money stays exact decimal in both.

## Status by phase

| Phase | Release | What becomes a graph | Status |
|---|---|---|---|
| 1 Foundation | 0.50 | Engine, node catalogue (sources, indicators, series, maths, logic, combine, convert, outputs), expression language, checks, trace, migration 038, `/api/calc` read + validate + preview. One built-in drawn as proof: **EMA trend**, equal to `signal_library.compute('EMA_TREND')` to 1e-9. | Done |
| 2 Signals | 0.51 | The 12 library signals as built-in SIGNAL graphs (parity ≤ 1e-9); bindings; the engine scores graph-bound signals; the UI shows the trace. | Done |
| 3 Editor | 0.51 | Canvas editor (React Flow), plain view, library, versions DRAFT → PROPOSED → APPROVED → RETIRED with four-eyes. | Done |
| 4 Strategies | 0.51–0.53 | Strategy algorithm GRAPH (pinned approved version) in runtime and backtest — done, backtest equal to the cent; EMA and SMA crossover drawn (same target and strength every bar) — done; RSI, Donchian, Bollinger, rules, signal blend drawn — next. | Partly done |
| 5 Risk, sizing, portfolio, execution, post-trade | 0.54 | SIZING, PROTECTIVE, RISK_CHECK (extra refusals only; the built-in limits always run), ALLOCATION, MEASURE. | Planned |
| 6 Features and market data | 0.51–0.55 | 6 platform features drawn (equal to the feature engine every bar) — done; the rest of the features, custom indicators and market-data measures — next. | Partly done |

## What stays code (and why)

* **Order lifecycle, client order IDs, UNKNOWN handling, reconciliation matching** — these are state machines and
  safety rules, not formulas; a graph must never be able to change them (guardrails 4 and 8).
* **The built-in risk limits and kill switches** (`trading/risk.py check()`) — always run; a risk graph can only add
  a refusal (guardrail 5).
* **Indicator primitives** (`intel/indicators.py`) — graphs wrap them; there is one implementation.

## The calculations

### Market data — 5 calculations

Code: `md/ (bars from ticks, latency, feed quality)` · graph purpose: **FEATURE / MEASURE** · phase 0.55

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Bar (OHLC) from ticks | open = first bid in the bar · high = max(bid) · low = min(bid) · close = last bid · ticks = count · spread_avg = mean(ask − bid) |
| Latency | latency = receive time (UTC) − broker time converted to UTC;  p95 over the minute |
| Feed quality score (0–100) | 100 − min(30, 15 + 15·(p95 ÷ limit − 1)) [if p95 > limit] − 25 [if longest gap > limit] − 30·min(1, frozen ms ÷ 60 000) − 15 [if spread > limit] − min(20, 10·spikes) − 15 [if \|deviation\| > limit] |
| Spike | \|mid move\| > k · σ of recent moves and reverses on the next update |
| Deviation from other sources | deviation_bp = 10 000 × (mid − median mid of the other sources) ÷ median mid |

### Intelligence — 22 calculations

Code: `intel/indicators.py, intel/engine.py features() and custom_values()` · graph purpose: **FEATURE** · phase 0.55

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| SMA(n) | SMA = (close₁ + … + closeₙ) ÷ n |
| EMA(n) — ema20, ema50, ema200 | EMAₜ = EMAₜ₋₁ + α·(closeₜ − EMAₜ₋₁),  α = 2 ÷ (n + 1); seeded with the SMA of the first n closes |
| RSI(14) — rsi14 | RSI = 100 − 100 ÷ (1 + RS),  RS = Wilder avg gain ÷ Wilder avg loss;  Wilderₜ = (Wilderₜ₋₁·(n − 1) + xₜ) ÷ n |
| ATR(14) — atr14, atr_pct | TR = max(high − low, \|high − close₋₁\|, \|low − close₋₁\|);  ATR = Wilder(TR, 14);  atr_pct = 100 × ATR ÷ close |
| ATR% rank — atr_pct_rank100 | percentile of today's atr_pct among the last 100 bars (0–100) |
| Bollinger(20, 2) — bb_mid, bb_upper, bb_lower, bb_width_pct | mid = SMA(20);  bands = mid ± 2·σ(20) (population σ);  width% = 100 × (upper − lower) ÷ mid |
| ADX(14), +DI, −DI | +DM = high − high₋₁ if larger than low₋₁ − low and > 0;  −DM likewise;  +DI = 100·Wilder(+DM) ÷ ATR;  DX = 100·\|+DI − −DI\| ÷ (+DI + −DI);  ADX = Wilder(DX) |
| Efficiency ratio — er20 | ER = \|closeₜ − closeₜ₋₂₀\| ÷ Σ\|closeᵢ − closeᵢ₋₁\| over 20 bars (1 = straight line, 0 = noise) |
| Realised volatility — rv20_pct | 100 × stdev(ln(closeᵢ ÷ closeᵢ₋₁), 20 bars) × √(bars per year) |
| Range position — range_pos20 | 100 × (close − low₂₀) ÷ (high₂₀ − low₂₀) |
| Returns — ret_1_pct, ret_20_pct | 100 × (closeₜ ÷ closeₜ₋ₖ − 1) |
| MACD(12, 26, 9) — macd, macd_signal, macd_hist | line = EMA(12) − EMA(26);  signal = EMA(9) of line;  hist = line − signal |
| Stochastic(14, 3, 3) — stoch_k, stoch_d | raw = 100 × (close − low₁₄) ÷ (high₁₄ − low₁₄);  %K = SMA₃(raw);  %D = SMA₃(%K) |
| Donchian(20) — donchian_high20, donchian_low20 | high₂₀ = max(high, 20 bars);  low₂₀ = min(low, 20 bars) |
| Z-score(20) — zscore20 | z = (close − mean₂₀) ÷ σ₂₀ |
| Volume — tick_volume, volume, rel_volume20 | rel_volume = volume ÷ SMA₂₀(volume) (real volume where the source has it, else ticks) |
| VWAP(20) — vwap20, vwap_dev_pct | VWAP = Σ((H + L + C) ÷ 3 · volume) ÷ Σ volume over 20 bars;  dev% = 100 × (close ÷ VWAP − 1) |
| OBV | OBVₜ = OBVₜ₋₁ + volume if close rose, − volume if it fell |
| Regime | TREND_UP/DOWN if ADX ≥ 25 and ER ≥ 0.30 and EMA20 vs EMA50 agrees with +DI vs −DI;  RANGE if ADX < 20;  else TRANSITION.  Confidence (trend) = 0.6·clamp((ADX − 20) ÷ 20) + 0.4·clamp(ER ÷ 0.6) |
| Volatility state | HIGH if ATR% rank ≥ 80, LOW if ≤ 20, else NORMAL |
| Screener attention (0–100) | 100 × quality × cost × (0.5·movement + 0.5·direction);  cost = clamp(1 − spread ÷ (0.2·ATR));  movement = ATR% rank ÷ 100;  direction = clamp(ADX ÷ 50) |
| Timeframes | M5…D1 bars = the broker's own bars when as complete as M1-built ones, else M1 resampled (open = first, high = max, low = min, close = last) |

### Signals — 19 calculations

Code: `trading/signal_library.py KINDS` · graph purpose: **SIGNAL** · phase 0.51 (EMA trend drawn in 0.50)

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| EMA trend (EMA_TREND) | score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100) |
| SMA trend (SMA_TREND) | score = clamp(100 × (SMA(fast) − SMA(slow)) ÷ (scale × ATR14), −100, +100) |
| MACD momentum (MACD_MOMENTUM) | score = clamp(100 × histogram ÷ (scale × ATR14)),  histogram = (EMA(fast) − EMA(slow)) − EMA(signal) of that line |
| RSI reversion (RSI_REVERSION) | score = clamp(2 × (50 − RSI), −100, +100) |
| RSI momentum (RSI_MOMENTUM) | score = clamp(2 × (RSI − 50), −100, +100) |
| Bollinger reversion (BOLLINGER_REVERSION) | score = clamp(−100 × z ÷ k),  z = (close − SMA(n)) ÷ σ(n) |
| Z-score reversion (ZSCORE_REVERSION) | score = clamp(−100 × z ÷ zmax),  z = (close − mean(n)) ÷ stdev(n) |
| Donchian breakout (DONCHIAN_BREAKOUT) | score = clamp(200 × (position − 0.5)),  position = (close − low(n)) ÷ (high(n) − low(n)) of the previous n bars |
| Momentum (t-stat) (MOMENTUM_TSTAT) | score = clamp(100 × t ÷ scale),  t = ln(closeₜ ÷ closeₜ₋ₙ) ÷ (σ₁ × √n) |
| Stochastic reversion (STOCHASTIC_REVERSION) | score = clamp(−2 × (%K − 50)),  %K = SMA(smooth) of 100 × (close − low(n)) ÷ (high(n) − low(n)) |
| ADX direction (ADX_DIRECTION) | score = clamp(100 × (+DI − −DI) ÷ (+DI + −DI) × min(1, ADX ÷ full_at)) |
| VWAP reversion (VWAP_REVERSION) | score = clamp(−100 × (close − VWAP(n)) ÷ (scale × ATR14)),  VWAP = Σ(typical price × volume) ÷ Σ volume |
| Signal side (library) | LONG if score ≥ +threshold;  SHORT if score ≤ −threshold;  else FLAT (threshold per definition, default 20) |
| Consensus (composite of an instrument) | composite = mean(score of every active signal);  agreement = share of signals with the composite's sign |
| Flip | recorded when direction(scoreₜ) ≠ direction(scoreₜ₋₁) on a closed bar (once per bar) |
| Signal correlation | r(a, b) = Pearson correlation of the two score series over the same bars;  \|r\| ≥ 0.8 = redundant (a blend counts it twice) |
| Research: information coefficient | IC(h) = Spearman rank correlation(scoreₜ, closeₜ₊ₕ ÷ closeₜ − 1);  t = IC·√((n − 2) ÷ (1 − IC²)) |
| Research: hit rate, quintiles, turnover, cost | hit = share of bars with \|score\| ≥ threshold whose h-bar return has the score's sign;  quintile = average h-bar return of each fifth of scores;  turnover = mean \|Δscore\| ÷ 200 per bar;  cost = average spread ÷ average close (bp) |
| Research: verdict | PREDICTIVE if \|IC\| ≥ 0.03 and \|t\| ≥ 2 and edge > cost at the best horizon;  WEAK if \|t\| ≥ 1;  else NO_EDGE |

### Strategies — 22 calculations

Code: `trading/algorithms.py, trading/signals.py checks, trading/risk.py size()` · graph purpose: **STRATEGY_RULE / SIZING / PROTECTIVE** · phase 0.53 (rules), 0.54 (sizing)

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Moving-average crossover (SMA) | LONG when SMA(fast)ₜ₋₁ ≤ SMA(slow)ₜ₋₁ and SMA(fast)ₜ > SMA(slow)ₜ; SHORT on the opposite cross |
| EMA crossover | LONG when EMA(fast)ₜ₋₁ ≤ EMA(slow)ₜ₋₁ and EMA(fast)ₜ > EMA(slow)ₜ; SHORT on the opposite cross. EMA(n)ₜ = EMA(n)ₜ₋₁ + 2÷(n+1)·(closeₜ − EMA(n)ₜ₋₁) |
| RSI mean reversion | LONG when RSIₜ₋₁ < lower ≤ RSIₜ; SHORT when RSIₜ₋₁ > upper ≥ RSIₜ; FLAT when RSI reaches exit_level. RSI = 100 − 100 ÷ (1 + avg gain ÷ avg loss), Wilder |
| Donchian channel breakout | LONG when closeₜ > max(high of the previous entry bars); SHORT when closeₜ < min(low of the previous entry bars); FLAT when the close crosses the exit channel the other way |
| Bollinger band reversion | LONG when closeₜ₋₁ < lowerₜ₋₁ and closeₜ ≥ lowerₜ; SHORT at the upper band; FLAT at the middle. Bands = SMA(n) ± k·σ(n) |
| Rule builder (any feature) | LONG when every long-entry condition holds; FLAT when any long-exit condition holds (shorts the same) |
| Signal blend (Stage 3 signals combined) | combined = Σ wᵢ · scoreᵢ ÷ Σ \|wᵢ\| (scores from the Stage 3 library); LONG when combined ≥ entry, SHORT when ≤ −entry; FLAT when it falls back inside ±exit; reverse when it crosses the opposite entry |
| Strategy signal born | a running strategy's target changes on a newly closed bar (or by hand); the first bar after activation is only recorded |
| Check: price fresh | now − quote received time ≤ max_quote_age_seconds |
| Check: spread | 100 × (ask − bid) ÷ ATR14 ≤ max_spread_atr_pct |
| Check: market open, event risk, strength, duplicate | market hours (Market data › markets);  no high-impact event for either currency within ±30 min;  strength ≥ min_strength;  no live signal with the same side |
| Decision | target = LONG / SHORT / FLAT of the latest signal (or of N agreeing signals);  wanted = +size, −size or 0;  order = wanted − current position (of this deployment) |
| Realign | retry when the latest decision was not executed and position ≠ target, nothing working, attempts < realign_max_attempts (AUTO handoff only) |
| Sizing: fixed quantity | quantity = Q × deployment weight |
| Sizing: fixed value | quantity = N × weight ÷ (price × contract size × FX rate to the account currency) |
| Sizing: % of equity at an ATR stop | quantity = equity × r% × weight ÷ (k × ATR × contract size × FX);  equity = partition equity, else broker equity (real), else paper equity |
| Lot rules | quantity rounded down to the volume step; below the minimum → refused |
| Protective exits | long: stop = entry − stop_atr × ATR, target = entry + take_atr × ATR (short mirrored); RISK_PCT without its own stop uses its k |
| Backtest fill | fill at the next bar's open;  buy = open + spread ticks × tick size (bars are bid);  commission per lot each side |
| Backtest metrics | net P&L = Σ trade P&L − commission;  max drawdown = max(peak equity − equity) ÷ peak;  Sharpe = mean(bar returns) ÷ stdev × √(bars per year);  profit factor = gross win ÷ \|gross loss\| |
| Walk-forward | each fold: best combination on the first in-sample % → traded on the rest;  efficiency = mean OOS score ÷ mean IS score;  ROBUST if efficiency ≥ 50% and OOS > 0 |
| Drift | DRIFT if live win rate < backtest × (1 − tol) or live profit factor < backtest × (1 − tol) or live average trade < 0 < backtest average trade (after min trades) |

### Risk — 9 calculations

Code: `trading/risk.py check(), margin(); trading/var.py` · graph purpose: **RISK_CHECK (extra refusals only)** · phase 0.54

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Kill switches | any engaged switch of the firm, account, strategy, instrument or deployment refuses new risk (closing orders pass) |
| Limits (tightest of firm, account, strategy, instrument, deployment) | order qty ≤ MAX_ORDER_QTY;  \|position after\| ≤ MAX_POSITION_QTY;  working orders < MAX_OPEN_ORDERS;  orders in the last 60 s < MAX_ORDERS_PER_MINUTE |
| Loss and drawdown | today's realised + unrealised ≥ −MAX_DAILY_LOSS;  drawdown = (peak equity − equity) ÷ peak ≤ MAX_DRAWDOWN_PCT |
| Exposure | value = \|qty\| × price × contract size × FX;  gross = Σ value ≤ MAX_GROSS_EXPOSURE;  per instrument ≤ MAX_INSTRUMENT_EXPOSURE;  order value ≤ MAX_ORDER_NOTIONAL |
| Margin and leverage | margin = Σ value ÷ account leverage (only the growing part of a position adds);  margin% = margin ÷ equity ≤ MAX_MARGIN_PCT;  leverage = gross ÷ equity ≤ MAX_LEVERAGE |
| Price deviation (fat finger) | 100 × \|limit or stop − mid\| ÷ mid ≤ MAX_PRICE_DEVIATION_PCT |
| Stale price | now − quote time ≤ max_quote_age_seconds (a real closing order passes) |
| Value at Risk (historical) | P&Lₜ = Σᵢ valueᵢ × returnᵢ,ₜ over the shared past bars;  VaR(α) = −(the ⌈(1 − α)·n⌉-th worst P&Lₜ);  ES = −mean of the worst ⌈(1 − α)·n⌉ |
| Stress | P&L = Σ value × shock% of the most specific matching shock (symbol › asset class › quote currency › all) |

### Portfolio — 8 calculations

Code: `trading/portfolio.py, partitions.py, copytrade.py, strategy_health.py` · graph purpose: **ALLOCATION / MEASURE** · phase 0.54

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Average cost | adding: avg = (qty₀·avg₀ + q·price) ÷ (qty₀ + q);  reducing: avg unchanged;  reversing: avg = fill price |
| Realised P&L | closed qty × (fill − avg) × sign × contract size (instrument currency) |
| Unrealised P&L | qty × (mid − avg) × contract size × FX to the account currency |
| Equity and drawdown | equity = capital + Σ realised (converted) + Σ unrealised;  drawdown = (peak − equity) ÷ peak |
| Partition equity | capital + realised + unrealised of the deployments assigned to it |
| Allocation | EQUAL: total ÷ n;  WEIGHT: total × wᵢ ÷ Σw;  RISK_PARITY: total × (1 ÷ σᵢ) ÷ Σ(1 ÷ σ), σᵢ = stdev of daily returns of the partition's instruments (60 days) |
| Copy size | FIXED_RATIO: master fill × ratio;  EQUITY_RATIO: fill × follower equity ÷ master equity × ratio;  FIXED_QTY: Q × fill ÷ master order;  capped, rounded to the lot step |
| Attribution | share = realised of the group ÷ Σ\|realised of all groups\| |

### Execution — 9 calculations

Code: `trading/handoff.py, execution.py, oms.py, routing.py` · graph purpose: **MEASURE (terms stay code)** · phase 0.54

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Handoff (per deployment) | AUTO: order now;  APPROVE: waits in the execution queue until approval_minutes, then EXPIRED;  SIGNAL_ONLY: recorded with its size, never ordered |
| Executing a waiting decision | order = target − position now (not at decision time);  a smaller quantity may be chosen, rounded down to the lot step;  same client order ID as automatic |
| Order terms | MARKET;  or LIMIT — MARKETABLE: buy = ask + n·tick, sell = bid − n·tick (a price cap);  PASSIVE: buy = bid − n·tick, sell = ask + n·tick;  time in force GTC / DAY / IOC |
| Client order ID | "C" + first 31 hex of SHA-256(seed) — the same intent always gives the same ID;  broker tag = first 20 characters |
| Paper fill price | buy = ask + slippage ticks × tick size;  sell = bid − slippage ticks × tick size;  limit at the limit price when reached;  stop at market when passed |
| Slippage | buy: fill − arrival ask;  sell: arrival bid − fill (positive = worse than the price when decided) |
| Latency | fill time − SUBMITTED time |
| Routing | BEST_PRICE: lowest ask to buy / highest bid to sell, tighter spread breaks ties;  LOWEST_COST: price ± average 30-day slippage;  SPLIT_EQUAL: qty ÷ n rounded to the lot step, remainder to the best |
| UNKNOWN look-up | every unknown_check_seconds: open orders, positions and 7 days of deals carrying the tag → fills, working, cancelled, or still unknown |

### Post-trade — 5 calculations

Code: `trading/posttrade.py` · graph purpose: **MEASURE** · phase 0.54

| Calculation | Equation (as the formula registry shows it today) |
|---|---|
| Position reconciliation | break if \|Σ COREI real position − broker net position (COREI magic)\| > tolerance, per broker symbol |
| Internal reconciliation | break if position row ≠ Σ signed fills of the same account, instrument, deployment and mode |
| TCA slippage | bp = 10 000 × slippage ÷ arrival price;  cost = slippage × qty × contract size × FX |
| Fill rate, refusal rate, latency | fill rate = Σ filled ÷ Σ ordered;  refusal = rejected orders ÷ orders;  p95 = 95th percentile of latency |
| Trade (round trip) | opens when a book goes flat → non-flat, closes when flat again (a reversal closes one and opens the next);  net = realised + costs;  MFE/MAE = best/worst price move while open (M1 bars) |
