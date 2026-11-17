/* The trading flow, end to end, as the stages COREI is built in. Taken from the corei
 * blueprint (Phase A trading logic → B constraints → D execution pipeline → portfolio)
 * without its infrastructure, and aligned with how institutional platforms split the
 * work (OMS/EMS, pre-trade compliance, TCA). Each stage lists its components, what each
 * does and its build state; the left bar shows one entry per stage. */
import type { View } from './layout';

export type State = 'BUILT' | 'PARTIAL' | 'NEXT' | 'PLANNED';
export type Component = { name: string; does: string; state: State; where?: [View, string?]; step: number };
export type Stage = { view: View; n: number; title: string; short?: string; question: string; feeds: string; components: Component[] };

export const PIPELINE: Stage[] = [
  { view: 'Market data', n: 1, title: 'Market data', question: 'What is the market doing, from which source, and can we trust it?', feeds: 'Prices, bars and quality to every later stage',
    components: [
      { step: 1, name: 'Broker connectivity', does: 'MT5 login (read-only), addresses, routing, latency; REST/WS and FIX later', state: 'PARTIAL', where: ['Configuration', 'connections'] },
      { step: 1, name: 'Collection plans', does: 'What to collect, from where, when, how long to keep', state: 'BUILT', where: ['Market data', 'plans'] },
      { step: 1, name: 'Tick and bar store', does: 'Journal → PostgreSQL by day → fingerprinted Parquet history', state: 'BUILT', where: ['Market data', 'archive'] },
      { step: 1, name: 'Feed quality', does: 'Latency, gaps, frozen prices, spikes, cross-source deviation, feed switching', state: 'BUILT', where: ['Market data', 'quality'] },
      { step: 1, name: 'History backfill', does: 'Bars from broker history, gaps only', state: 'BUILT', where: ['Market data', 'backfill'] },
      { step: 2, name: 'Symbol normalisation', does: 'One instrument across brokers (XAUUSD.r, GOLD → XAUUSD), contract specs', state: 'PARTIAL', where: ['Configuration', 'mappings'] },
      { step: 3, name: 'Order book depth', does: 'Level-2 where the broker publishes it', state: 'PLANNED' },
      { step: 3, name: 'Upstox / Indian markets', does: 'NSE, BSE, MCX indices, stocks, futures and options through the same plans; exchange hours and holidays; the main indices and nearest futures in one click', state: 'BUILT', where: ['Market data', 'dashboard'] },
      { step: 2, name: 'Bridge nodes', does: 'Windows machines running the MT5 bridge, registered with a one-time code and shared by many accounts', state: 'BUILT', where: ['Configuration', 'bridges'] },
    ] },
  { view: 'Intelligence', n: 2, short: 'Intelligence', title: 'Market intelligence', question: 'What state is each market in, and which instruments are worth attention?', feeds: 'Features and the ranked universe to signals and strategies',
    components: [
      { step: 2, name: 'Feature store', does: 'Standard set (EMA 20/50/200, RSI, ATR, Bollinger, ADX ±DI, efficiency, realised vol, MACD, stochastic, Donchian, z-score) plus volume (tick and real, relative volume, VWAP, OBV, bar spread) — computed once from closed stored bars (M1…D1), kept per bar, shared by every strategy', state: 'BUILT', where: ['Intelligence', 'instrument'] },
      { step: 2, name: 'Custom indicators', does: 'Per-strategy lengths beside the standard set (EMA 9/21, Bollinger 20/2.5, MACD 8/21/5, VWAP 50…) per timeframe, usable in rules and screener columns', state: 'BUILT', where: ['Intelligence', 'indicators'] },
      { step: 2, name: 'Sessions and calendar', does: 'Sessions, overlaps, rollover, weekend close; economic events and holidays (added or imported as CSV) with event-risk flags per instrument. Automatic calendar feed: next', state: 'BUILT', where: ['Intelligence', 'calendar'] },
      { step: 2, name: 'Volatility and regime', does: 'Trend up/down, range, transition and high/normal/low volatility per instrument and timeframe, with reasons', state: 'BUILT', where: ['Intelligence', 'screener'] },
      { step: 3, name: 'Instrument screening', does: 'Rank the universe across brokers and accounts: attention (quality, spread vs ATR, movement, direction), timeframe alignment, event risk; filters by broker, asset class, watchlist; watchlists and rules on features with alerts', state: 'BUILT', where: ['Intelligence', 'screener'] },
      { step: 3, name: 'Correlation and cross-asset', does: 'Correlation matrix of collected instruments, currency strength, the same instrument compared across sources (deviation, spread, latency, quality)', state: 'BUILT', where: ['Intelligence', 'correlation'] },
      { step: 4, name: 'News and sentiment', does: 'Headlines and scheduled events tagged to instruments', state: 'PLANNED' },
    ] },
  { view: 'Signals', n: 3, short: 'Signals', title: 'Signal intelligence', question: 'Is there a trade, how strong, and does it agree with the others?', feeds: 'Trade intents (instrument, side, strength, horizon) to risk',
    components: [
      { step: 3, name: 'Signal model', does: 'One shape for every signal: instrument, side, strength, horizon, expiry, source, the features and reasons behind it', state: 'BUILT', where: ['Signals', 'signals'] },
      { step: 3, name: 'Signal lifecycle', does: 'New → valid → acted, or refused / expired / cancelled, each step kept', state: 'BUILT', where: ['Signals', 'signals'] },
      { step: 3, name: 'Validation', does: 'Stale price, spread vs ATR, market closed, event risk, duplicates, minimum strength', state: 'BUILT', where: ['Signals', 'checks'] },
      { step: 4, name: 'Decision engine', does: 'Target position per deployment (latest signal, or N agreeing), compared with the position held', state: 'BUILT', where: ['Signals', 'intents'] },
      { step: 4, name: 'Signal quality', does: 'Forward move and hit rate per strategy and instrument', state: 'BUILT', where: ['Signals', 'quality'] },
      { step: 5, name: 'Signal contribution', does: 'Weight each source by its measured quality', state: 'PLANNED' },
    ] },
  { view: 'Strategies', n: 4, short: 'Strategies', title: 'Strategy intelligence', question: 'Which strategies run, on what, and are they still working?', feeds: 'Approved strategy versions and their parameters to signals',
    components: [
      { step: 1, name: 'Strategy registry and versions', does: 'Rules, parameters, approval of each version', state: 'BUILT', where: ['Configuration', 'strategies'] },
      { step: 1, name: 'Deployments', does: 'Version × instrument × account × data source; paper or broker; sizing', state: 'BUILT', where: ['Configuration', 'deployments'] },
      { step: 3, name: 'Algorithms', does: 'SMA and EMA cross, RSI and Bollinger reversion, Donchian breakout, rule builder on any feature', state: 'BUILT', where: ['Strategies', 'algorithms'] },
      { step: 3, name: 'Strategy runtime', does: 'Runs each active deployment on every newly closed bar, emits signals', state: 'BUILT', where: ['Strategies', 'runtime'] },
      { step: 3, name: 'Backtest', does: 'Same code on stored history, point in time, with costs; trades, equity, metrics', state: 'BUILT', where: ['Strategies', 'backtest'] },
      { step: 4, name: 'Paper and forward test', does: 'Live data, simulated fills, full risk and order path', state: 'BUILT', where: ['Strategies', 'performance'] },
      { step: 5, name: 'Performance attribution', does: 'P&L, win rate, profit factor per strategy, instrument, account', state: 'BUILT', where: ['Strategies', 'performance'] },
      { step: 6, name: 'Optimisation and drift', does: 'Parameter sweeps, walk-forward, alerts when live leaves the backtest range', state: 'PLANNED' },
    ] },
  { view: 'Risk', n: 5, short: 'Risk', title: 'Risk intelligence', question: 'Is this trade allowed, how big may it be, and what is our exposure?', feeds: 'Approved, sized orders to execution — or a refusal with the reason',
    components: [
      { step: 1, name: 'Broker limits per symbol', does: 'Min/step/max lot, stops level, filling modes, trade mode; pre-send check', state: 'BUILT', where: ['Configuration', 'accounts'] },
      { step: 4, name: 'Kill switches', does: 'Firm, account, strategy, instrument, deployment; automatic on a loss limit; order sending per environment', state: 'BUILT', where: ['Risk', 'kill'] },
      { step: 4, name: 'Constraint engine', does: 'Limits per firm, account, strategy, instrument, deployment; tightest applies', state: 'BUILT', where: ['Risk', 'limits'] },
      { step: 4, name: 'Position sizing', does: 'Fixed, % of equity at risk with an ATR stop, fixed value; rounded to the lot step', state: 'BUILT', where: ['Configuration', 'deployments'] },
      { step: 4, name: 'Loss and drawdown limits', does: 'Daily loss and drawdown per account; breach stops new orders', state: 'BUILT', where: ['Risk', 'limits'] },
      { step: 5, name: 'Exposure', does: 'Net and gross by instrument and account, in the account currency', state: 'BUILT', where: ['Risk', 'exposure'] },
      { step: 5, name: 'Margin model', does: 'Required margin from leverage and broker rules', state: 'PLANNED' },
      { step: 6, name: 'VaR and stress', does: 'Value at risk, scenario and stress tests', state: 'PLANNED' },
    ] },
  { view: 'Portfolio', n: 6, short: 'Portfolio', title: 'Portfolio intelligence', question: 'What do we hold, what is it worth, and how is capital allocated?', feeds: 'Positions, P&L and exposure back to risk and strategies',
    components: [
      { step: 5, name: 'Positions', does: 'Per account, instrument and deployment, from fills; flatten in a click', state: 'BUILT', where: ['Portfolio', 'positions'] },
      { step: 5, name: 'P&L', does: 'Realised and unrealised at the live price, per account, strategy, instrument', state: 'BUILT', where: ['Portfolio', 'attribution'] },
      { step: 5, name: 'Equity and drawdown', does: 'Paper equity per account, peak and drawdown, snapshots', state: 'BUILT', where: ['Portfolio', 'accounts'] },
      { step: 5, name: 'Broker positions', does: 'What the broker reports, beside COREI\'s own', state: 'NEXT' },
      { step: 6, name: 'Capital partitions', does: 'Capital per account, strategy and client; isolation between them', state: 'PLANNED' },
      { step: 6, name: 'Allocation and copy', does: 'One decision across many accounts, scaled per account (master-follower)', state: 'PLANNED' },
    ] },
  { view: 'Execution', n: 7, title: 'Execution', question: 'How does an approved order reach the broker, and what happened to it?', feeds: 'Fills to portfolio and post-trade',
    components: [
      { step: 5, name: 'Order engine (OMS)', does: 'State machine with UNKNOWN, cancel, expiry; idempotent client order IDs; append-only history', state: 'BUILT', where: ['Execution', 'orders'] },
      { step: 5, name: 'Paper adapter', does: 'Fills at the live bid/ask plus slippage; limit orders when the price trades through', state: 'BUILT', where: ['Execution', 'execution'] },
      { step: 5, name: 'Order ticket', does: 'Manual paper orders with the same risk check', state: 'BUILT', where: ['Execution', 'ticket'] },
      { step: 5, name: 'Fill handling', does: 'Fills, partials, slippage capture', state: 'BUILT', where: ['Execution', 'fills'] },
      { step: 5, name: 'MT5 order adapter', does: 'Send, modify, cancel through the bridge (master password, four-eyes on LIVE)', state: 'NEXT' },
      { step: 6, name: 'Smart routing (EMS)', does: 'Choose broker/account by price, spread, latency and fill quality', state: 'PLANNED' },
      { step: 1, name: 'Rate limits and pre-send checks', does: 'Orders per minute, lot rounding, working orders', state: 'BUILT', where: ['Risk', 'limits'] },
    ] },
  { view: 'Post-trade', n: 8, title: 'Post-trade', question: 'Does our record match the broker’s, and how good was the execution?', feeds: 'Corrections to portfolio; evidence to compliance; learning to strategies',
    components: [
      { step: 6, name: 'Reconciliation', does: 'Broker positions, orders and deals vs COREI’s; breaks raised and resolved', state: 'PLANNED' },
      { step: 6, name: 'Transaction cost analysis', does: 'Slippage, latency, fill rate per broker × address × hour', state: 'PLANNED' },
      { step: 6, name: 'Trade journal and reports', does: 'Daily statements, per client / account reports', state: 'PLANNED' },
      { step: 1, name: 'Audit trail', does: 'Every change, password use, approval and edit — append-only', state: 'BUILT', where: ['Configuration', 'audit'] },
      { step: 7, name: 'Compliance', does: 'Mandates, restricted lists, regulatory records (SEBI / ESMA as applicable)', state: 'PARTIAL', where: ['Configuration', 'accounts'] },
    ] },
];

export const STATE_LABEL: Record<State, string> = { BUILT: 'built', PARTIAL: 'partly built', NEXT: 'next', PLANNED: 'planned' };
export const stageOf = (view: View) => PIPELINE.find(s => s.view === view);

/* The build order: each step makes the next one possible. */
export const STEPS: [number, string, string][] = [
  [1, 'Foundation', 'Brokers, accounts, instruments, market data collection and storage, audit'],
  [2, 'Market intelligence', 'Features, sessions, regimes, symbol normalisation'],
  [3, 'Signals and strategy runtime', 'Signal model, strategies running on live data, backtest on history'],
  [4, 'Risk and decisions', 'Constraint engine, sizing, loss limits, decision engine, paper trading'],
  [5, 'Orders and portfolio', 'OMS, MT5 order adapter, fills, positions, P&L, exposure'],
  [6, 'Post-trade and routing', 'Reconciliation, TCA, smart routing, capital partitions, VaR'],
  [7, 'Governance', 'Compliance, mandates, regulatory records'],
];
export const progress = (s: Stage) => {
  const c = s.components, built = c.filter(x => x.state === 'BUILT').length, part = c.filter(x => x.state === 'PARTIAL').length;
  return { built, part, total: c.length, pct: Math.round(100 * (built + part / 2) / c.length) };
};
