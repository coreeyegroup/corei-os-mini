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
      { step: 3, name: 'Upstox / other API feeds', does: 'Indian brokers and vendors through the same plans', state: 'PLANNED' },
    ] },
  { view: 'Intelligence', n: 2, short: 'Intelligence', title: 'Market intelligence', question: 'What state is each market in, and which instruments are worth attention?', feeds: 'Features and the ranked universe to signals and strategies',
    components: [
      { step: 2, name: 'Feature store', does: 'ATR, RSI, EMA, VWAP, Bollinger, ADX computed once from stored bars, shared by every strategy', state: 'NEXT' },
      { step: 2, name: 'Sessions and calendar', does: 'Trading sessions, holidays, economic events, rollover windows', state: 'PLANNED' },
      { step: 2, name: 'Volatility and regime', does: 'Trend / range / high-vol classification per instrument and timeframe', state: 'PLANNED' },
      { step: 3, name: 'Instrument screening', does: 'Filter and rank the universe: liquidity, spread, volatility, correlation', state: 'PLANNED' },
      { step: 3, name: 'Correlation and cross-asset', does: 'DXY, gold, indices, yields relationships', state: 'PLANNED' },
      { step: 4, name: 'News and sentiment', does: 'Headlines and scheduled events tagged to instruments', state: 'PLANNED' },
    ] },
  { view: 'Signals', n: 3, short: 'Signals', title: 'Signal intelligence', question: 'Is there a trade, how strong, and does it agree with the others?', feeds: 'Trade intents (instrument, side, strength, horizon) to risk',
    components: [
      { step: 3, name: 'Signal model', does: 'One shape for every signal: instrument, side, strength, confidence, horizon, source, expiry', state: 'NEXT' },
      { step: 3, name: 'Signal lifecycle', does: 'New → confirmed → acted / expired / cancelled, with the market state that caused it', state: 'PLANNED' },
      { step: 3, name: 'Validation', does: 'Stale data, spread, session and duplicate checks before a signal counts', state: 'PLANNED' },
      { step: 4, name: 'Decision engine', does: 'Aggregates signals per instrument into one trade intent; conflicts resolved by rules', state: 'PLANNED' },
      { step: 4, name: 'Signal quality', does: 'Hit rate, decay and contribution of each signal source', state: 'PLANNED' },
    ] },
  { view: 'Strategies', n: 4, short: 'Strategies', title: 'Strategy intelligence', question: 'Which strategies run, on what, and are they still working?', feeds: 'Approved strategy versions and their parameters to signals',
    components: [
      { step: 1, name: 'Strategy registry and versions', does: 'Rules, parameters, approval of each version', state: 'BUILT', where: ['Configuration', 'strategies'] },
      { step: 1, name: 'Deployments', does: 'Strategy version × instrument × account × data source', state: 'BUILT', where: ['Configuration', 'deployments'] },
      { step: 3, name: 'Strategy runtime', does: 'Runs each deployed version on live bars/features, emits signals', state: 'NEXT' },
      { step: 3, name: 'Backtest and replay', does: 'Same code on stored history (Parquet), point-in-time correct', state: 'PLANNED' },
      { step: 4, name: 'Paper and forward test', does: 'Live data, simulated fills, before any real order', state: 'PLANNED' },
      { step: 5, name: 'Performance attribution', does: 'P&L, drawdown, Sharpe per strategy, instrument, account; parameter drift', state: 'PLANNED' },
    ] },
  { view: 'Risk', n: 5, short: 'Risk', title: 'Risk intelligence', question: 'Is this trade allowed, how big may it be, and what is our exposure?', feeds: 'Approved, sized orders to execution — or a refusal with the reason',
    components: [
      { step: 1, name: 'Broker limits per symbol', does: 'Min/step/max lot, stops level, filling modes, trade mode; pre-send check', state: 'BUILT', where: ['Configuration', 'accounts'] },
      { step: 1, name: 'Live-price check', does: 'Refuse on stale price, abnormal spread or degraded feed', state: 'BUILT', where: ['Market data', 'live'] },
      { step: 4, name: 'Constraint engine', does: 'Versioned limits per trade, account, strategy, instrument and firm (max size, exposure, open orders)', state: 'NEXT' },
      { step: 4, name: 'Position sizing', does: 'Size from strategy, signal strength and portfolio risk (volatility-adjusted), rounded to the broker step', state: 'PLANNED' },
      { step: 4, name: 'Loss and drawdown limits', does: 'Daily / weekly loss, max drawdown, per account and strategy; automatic stop', state: 'PLANNED' },
      { step: 5, name: 'Margin and exposure', does: 'Margin use, net exposure by currency/asset, concentration', state: 'PLANNED' },
      { step: 6, name: 'VaR and stress', does: 'Value at risk, scenario and stress tests', state: 'PLANNED' },
      { step: 1, name: 'Kill switches', does: 'Order sending per environment, collection pause; per account/strategy later', state: 'PARTIAL', where: ['Configuration', 'controls'] },
    ] },
  { view: 'Portfolio', n: 6, short: 'Portfolio', title: 'Portfolio intelligence', question: 'What do we hold, what is it worth, and how is capital allocated?', feeds: 'Positions, P&L and exposure back to risk and strategies',
    components: [
      { step: 5, name: 'Positions and balances', does: 'Every account read from the broker, one view across brokers', state: 'NEXT' },
      { step: 5, name: 'P&L', does: 'Realised / unrealised, fees, swaps; per account, strategy, instrument', state: 'PLANNED' },
      { step: 5, name: 'Exposure', does: 'Net and gross by instrument, currency, asset class, account group', state: 'PLANNED' },
      { step: 6, name: 'Capital partitions', does: 'Capital per account, strategy and client; isolation between them', state: 'PLANNED' },
      { step: 6, name: 'Allocation and copy', does: 'One decision across many accounts, scaled per account (master-follower)', state: 'PLANNED' },
    ] },
  { view: 'Execution', n: 7, title: 'Execution', question: 'How does an approved order reach the broker, and what happened to it?', feeds: 'Fills to portfolio and post-trade',
    components: [
      { step: 5, name: 'Execution intent and routing', does: 'Trade intent → per-account orders using each account’s routing and mode', state: 'PLANNED' },
      { step: 5, name: 'Order engine (OMS)', does: 'Order state machine: new → sent → partial → filled / rejected / cancelled; idempotent', state: 'NEXT' },
      { step: 5, name: 'MT5 order adapter', does: 'Send, modify, cancel through the MT5 SDK (master password, four-eyes on LIVE)', state: 'NEXT' },
      { step: 6, name: 'Smart routing (EMS)', does: 'Choose broker/account by price, spread, latency and fill quality', state: 'PLANNED' },
      { step: 5, name: 'Fill handling', does: 'Fills, partials, slippage capture, broker ticket mapping', state: 'PLANNED' },
      { step: 1, name: 'Rate limits and pre-send checks', does: 'Orders per second, filling mode, lot rounding', state: 'PARTIAL', where: ['Configuration', 'controls'] },
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
