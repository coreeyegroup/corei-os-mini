/* Stages 3–7 screens: Signals, Strategies, Risk, Portfolio, Orders — each in the same StageShell. Panels live beside:
 * Stage 3 in SignalLibrary.tsx, the Stage 4 desk in StrategyDesk.tsx, the decision flow and execution policy in
 * ExecutionFlow.tsx, Stage 8 and the 0.45 additions in TradingExtras.tsx.
 * Why: the trading core (signals → risk → orders → portfolio) is visible and controllable step by step, with the
 * reason for every refusal on screen.
 * Purpose: each stage's Overview / Work / Monitor / Configure sections over /api/trading.
 * Must never: decide anything itself (the backend decides; these screens show and ask). */
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, Choice, Field } from './Configuration';
import FloatingWindow from './FloatingWindow';
import StageShell, { ago, key, Kpis, money, Msg, num, pill, Reasons, statePill, useData, when, type Ctx, type Row, type Section } from './StageShell';
import { goto, useScanEvidence } from './nav';
import GraphWorkbench from './calc/GraphWorkbench';
import { AlgoBlotter, AlgoTicket } from './ExecutionAlgos';
import { AlgorithmEquations, BlendEditor, Formulas, ResearchAll, SignalBoardLive, SignalCorrelation, SignalDashboard, SignalExplain, SignalFlips, SignalLibrary, SignalResearch, SignalTimeframes } from './SignalLibrary';
import { DecisionFlow, DecisionQueue, ExecutionPolicies } from './ExecutionFlow';
import { BacktestCompare, StrategyDashboard } from './StrategyDesk';
import { Allocation, Attribution, BrokerBook, Connections, CopyTrading, Drift, Optimise, Routing, Stress, Unknown, VarMargin, Waiting } from './TradingExtras';

/* ---------- shared ---------- */

function Lists() {
  const [accounts] = useData<Row>('config/accounts', 0), [instruments] = useData<Row>('config/instruments', 0);
  const [strategies] = useData<Row>('config/strategies', 0), [deployments] = useData<Row>('config/deployments', 0);
  return { accounts: accounts?.items ?? [], instruments: instruments?.items ?? [], strategies: strategies?.items ?? [], deployments: deployments?.items ?? [] };
}
const depName = (d: Row) => `${d.strategy_code} v${d.version_number} · ${d.instrument_symbol} · ${d.account_code}`;

const SETTING_LABEL: Record<string, [string, string]> = {
  runtime_enabled: ['Strategy runtime on', 'Off: deployments are not evaluated (Run now still works)'],
  max_quote_age_seconds: ['Max price age (seconds)', 'A signal is refused when the last price is older'],
  max_spread_atr_pct: ['Max spread (% of ATR)', 'A signal is refused when the spread is wider than this share of the ATR'],
  block_on_event: ['Refuse near high-impact events', 'Events in the calendar for the instrument\'s currencies, ±30 minutes'],
  min_strength: ['Minimum signal strength (0–100)', 'Weaker signals are refused'],
  decision_mode: ['Decision', 'Latest signal wins, or several agreeing signals are needed'],
  agree_count: ['Agreeing signals needed', 'With "agree": how many in a row'],
  paper_slippage_ticks: ['Paper slippage (ticks)', 'Paper fills pay this many ticks beyond the bid / ask'],
  default_capital: ['Default paper capital', 'Starting equity of an account without its own (account currency)'],
  pre_check_orders: ['Pre-check real orders at the broker', 'MT5 order_check (margin, volume, price, trading rules) before every real order'],
  live_four_eyes: ['Four-eyes on LIVE accounts', 'A second Risk approver releases the order before it goes to the broker'],
  broker_magic: ['MT5 magic number', 'Marks COREI\'s positions and deals in the terminal (reconciliation counts only these)'],
  max_deviation_points: ['Max price deviation (points)', 'MT5 market orders may fill this far from the requested price'],
  broker_timeout_seconds: ['Broker answer timeout (seconds)', 'No answer in this time → the order is UNKNOWN and looked up by its tag'],
  unknown_check_seconds: ['Look-up interval (seconds)', 'How often working and UNKNOWN orders are looked up at the broker'],
  upstox_product: ['Upstox product', 'D delivery, I intraday, MTF margin trading'],
  recon_interval_minutes: ['Reconcile every (minutes)', 'Broker positions and deals against COREI\'s, per account with real orders or positions'],
  recon_qty_tolerance: ['Position tolerance', 'Differences up to this quantity are not a break'],
  recon_kill_on_break: ['Stop the account on a position break', 'Engage the account kill switch when a position differs from the broker'],
  drift_min_trades: ['Drift: minimum trades', 'Closed trades before live results are compared with the backtest'],
  drift_tolerance_pct: ['Drift: tolerance %', 'Live worse than the backtest by more than this → DRIFT'],
  var_confidence: ['VaR confidence %', '90, 95, 97.5, 99 or 99.5'],
  var_timeframe: ['VaR horizon (bar)', 'D1 = 1-day VaR'],
  var_lookback_bars: ['VaR look-back (bars)', 'Past bars used for the historical simulation'],
  realign_max_attempts: ['Realign attempts', 'Times in a row a deployment retries a decision that was not executed (0 = never)'],
};
const SETTING_CHOICES: Record<string, [string, string][]> = {
  decision_mode: [['LATEST', 'Latest signal wins'], ['AGREE', 'Agreeing signals needed']],
  live_four_eyes: [['MANUAL', 'Manual orders on LIVE'], ['ALL', 'Every LIVE order'], ['OFF', 'Off']],
  upstox_product: [['D', 'Delivery (D)'], ['I', 'Intraday (I)'], ['MTF', 'Margin trading (MTF)']],
  var_confidence: [['90', '90%'], ['95', '95%'], ['97.5', '97.5%'], ['99', '99%'], ['99.5', '99.5%']],
  var_timeframe: [['D1', 'One day'], ['H4', 'Four hours'], ['H1', 'One hour'], ['M30', '30 minutes'], ['M15', '15 minutes']],
};

function SettingsForm({ fields, canEdit, intro }: { fields: string[]; canEdit: boolean; intro?: string }) {
  const [data, error, reload] = useData<Row>('trading/settings', 0);
  const [f, setF] = useState<Row | null>(null), [reason, setReason] = useState(''), [msg, setMsg] = useState(''), [err, setErr] = useState('');
  useEffect(() => { if (data) setF(data); }, [data]);
  if (!f) return <Msg error={error} />;
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { await api('trading/settings', 'PUT', { ...Object.fromEntries(Object.keys(SETTING_LABEL).filter(k => f[k] !== undefined).map(k => [k, f[k]])), version: f.version, reason }); setMsg('Saved.'); setErr(''); setReason(''); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  return <form className="cfg-form" onSubmit={save}>
    {intro && <p className="cfg-note">{intro}</p>}
    {fields.map(k => { const [label, hint] = SETTING_LABEL[k]; const v = f[k];
      return typeof v === 'boolean' ? <label key={k} title={hint}><input type="checkbox" disabled={!canEdit} checked={v} onChange={e => setF({ ...f, [k]: e.target.checked })} /> {label} <small className="cfg-note">{hint}</small></label>
        : SETTING_CHOICES[k] ? <Field key={k} label={label} hint={hint}><Choice label={label} value={String(v)} options={SETTING_CHOICES[k]} onChange={x => setF({ ...f, [k]: x })} /></Field>
        : <Field key={k} label={label} hint={hint}><input aria-label={label} disabled={!canEdit} value={v ?? ''} onChange={e => setF({ ...f, [k]: e.target.value })} /></Field>; })}
    {canEdit ? <><Field label="Reason (kept in the audit log)"><input aria-label="Settings reason" value={reason} onChange={e => setReason(e.target.value)} /></Field>
      <div className="cfg-actions"><button className="primary" disabled={reason.trim().length < 5}>Save</button></div></>
      : <p className="cfg-note">Risk approvers change these settings.</p>}
    <p className="cfg-note">Last changed {when(f.updated_at)} by {f.updated_by}.</p>
    <Msg error={err} notice={msg} />
  </form>;
}

function RuntimeLine({ canRun }: { canRun: boolean }) {
  const [ov, , reload] = useData<Row>('trading/overview', 15_000);
  const [busy, setBusy] = useState(false), [msg, setMsg] = useState('');
  const r = ov?.runtime ?? {};
  return <div className="intel-bar">
    <span className={`cfg-pill ${r.enabled === false ? 'off' : 'ok'}`}>{r.enabled === false ? 'runtime paused' : 'runtime on'}</span>
    <small className="cfg-note">Runs after every feature refresh (about a minute). Last cycle {ago(r.last_run_at)} · {r.deployments ?? 0} deployments · {r.signals ?? 0} signals · {r.orders ?? 0} orders
      {r.duration_ms != null ? ` · ${r.duration_ms} ms` : ''}{r.deployments_with_problems ? ` · ${r.deployments_with_problems} deployments need attention` : ''}</small>
    {canRun && <button disabled={busy} onClick={() => { setBusy(true); void api('trading/runtime/run', 'POST').then(x => { setMsg(`Cycle done: ${x.signals} signals, ${x.orders} orders${x.errors?.length ? `, ${x.errors.length} notes` : ''}.`); reload(); })
      .catch(e => setMsg((e as Error).message)).finally(() => setBusy(false)); }}>{busy ? 'Running…' : 'Run a cycle now'}</button>}
    {msg && <small role="status">{msg}</small>}
  </div>;
}

/* ---------- Stage 4 output: strategy signals and their quality (Stage 3 lives in SignalLibrary.tsx) ---------- */

function SignalTable({ rows, open }: { rows: Row[]; open?: (r: Row) => void }) {
  if (!rows.length) return <p className="cfg-note">No signals yet. They appear when an active deployment's strategy changes its target on a closed bar (Strategies › Runtime shows each deployment's state).</p>;
  return <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Bar (UTC)</th><th>Strategy</th><th>Instrument</th><th>Account</th><th>Side</th><th>Strength</th><th>State</th><th>Why</th></tr></thead><tbody>
    {rows.map(r => <tr key={r.id} className={open ? 'clickable' : ''} onClick={() => open?.(r)}>
      <td><small>{new Date(r.bar_time).toISOString().slice(5, 16).replace('T', ' ')}</small></td><td>{r.strategy_code ?? '—'}{r.version_number ? ` v${r.version_number}` : ''} <small className="cfg-note">{r.source === 'MANUAL' ? 'manual' : r.timeframe}</small></td>
      <td>{r.symbol}</td><td>{r.account_code} <small className="cfg-note">{(r.mode ?? 'PAPER').toLowerCase()}</small></td>
      <td><b className={r.side === 'BUY' ? 'up' : r.side === 'SELL' ? 'down' : ''}>{r.side}</b></td><td>{r.strength}</td><td>{statePill(r.state)}</td>
      <td><small>{r.reasons?.length ? r.reasons[0] : (r.explanation ?? [])[0]}</small></td></tr>)}</tbody></table></div>;
}

function SignalBoard({ ctx }: { ctx: Ctx }) {
  const [state, setState] = useState(''), [data, error, reload] = useData<Row>(`trading/signals?limit=500${state ? `&state=${state}` : ''}`, 20_000, [state]);
  const [detail, setDetail] = useState<Row | null>(null), [manual, setManual] = useState<Row | null>(null), [msg, setMsg] = useState(''), [err, setErr] = useState('');
  const { deployments } = Lists();
  const show = (r: Row) => void api(`trading/signals/${r.id}`).then(setDetail).catch(e => setErr((e as Error).message));
  const send = async (e: FormEvent) => {
    e.preventDefault(); if (!manual) return;
    try { const r = await api('trading/signals', 'POST', manual); setManual(null); setMsg(`Manual signal ${r.signal.state.toLowerCase()}${r.signal.reasons?.length ? ': ' + r.signal.reasons.join('; ') : ''}.`); setErr(''); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  return <section className="intel">
    <div className="intel-bar">
      <select aria-label="Signal state" value={state} onChange={e => setState(e.target.value)}><option value="">All states</option>{['NEW', 'VALID', 'ACTED', 'REJECTED', 'EXPIRED', 'CANCELLED'].map(s => <option key={s}>{s}</option>)}</select>
      {ctx.canOps && <button className="primary" onClick={() => setManual({ deployment_id: deployments.find((d: Row) => d.status === 'ACTIVE')?.id ?? '', side: 'BUY', strength: 60, reason: '' })}>Manual signal</button>}
      <button onClick={reload}>Reload</button>
    </div>
    <Msg error={error || err} notice={msg} />
    <SignalTable rows={data?.items ?? []} open={show} />
    {detail && <FloatingWindow storageKey="corei.signal.v1" title={`Signal · ${detail.side} · ${detail.state.toLowerCase()}`} onClose={() => setDetail(null)}
      footer={<div className="cfg-actions">{(detail.state === 'NEW' || detail.state === 'VALID') && (ctx.canOps || ctx.canRisk) && <button onClick={() => {
        const reason = window.prompt('Reason for cancelling this signal'); if (reason) void api(`trading/signals/${detail.id}/cancel`, 'POST', { reason }).then(() => { setDetail(null); reload(); }).catch(e => setErr((e as Error).message)); }}>Cancel signal</button>}
        <button onClick={() => setDetail(null)}>Close</button></div>}>
      <div className="cfg-form">
        <p><b>Why it was raised:</b> {(detail.explanation ?? []).join('; ')}</p>
        {detail.reasons?.length > 0 && <><b>Why it was refused:</b><Reasons list={detail.reasons} /></>}
        {detail.intent && <p><b>Decision:</b> target {detail.intent.target.toLowerCase()} · held {num(detail.intent.current_qty, 2)} → wanted {num(detail.intent.target_qty, 2)} · {statePill(detail.intent.outcome)}
          {detail.intent.order_id && <> · <button className="link" onClick={() => goto('Execution', 'orders')}>see the order →</button></>}</p>}
        <h4>History</h4><table className="cfg-table compact"><tbody>{detail.events.map((e: Row) => <tr key={e.id}><td><small>{when(e.at)}</small></td><td>{statePill(e.state)}</td><td><small>{e.actor}</small></td><td><small>{e.detail}</small></td></tr>)}</tbody></table>
        <h4>Features on the bar</h4><p className="cfg-note">{Object.entries(detail.features ?? {}).map(([k, v]) => `${k} ${num(v, 4)}`).join(' · ') || '—'}</p>
      </div>
    </FloatingWindow>}
    {manual && <FloatingWindow storageKey="corei.signal-manual.v1" title="Manual signal" onClose={() => setManual(null)}
      footer={<div className="cfg-actions"><button className="primary" form="st-manual">Raise signal</button><button onClick={() => setManual(null)}>Cancel</button></div>}>
      <form id="st-manual" className="cfg-form" onSubmit={send}>
        <p className="cfg-note">It goes through the same checks, decision, risk and execution as a strategy's signal.</p>
        <Field label="Deployment"><select aria-label="Signal deployment" required value={manual.deployment_id} onChange={e => setManual({ ...manual, deployment_id: e.target.value })}>
          <option value="">Choose…</option>{deployments.filter((d: Row) => d.status === 'ACTIVE').map((d: Row) => <option key={d.id} value={d.id}>{depName(d)} · {(d.mode ?? 'PAPER').toLowerCase()}</option>)}</select></Field>
        <Field label="Side"><Choice label="Signal side" value={manual.side} options={[['BUY', 'Buy (go long)'], ['SELL', 'Sell (go short)'], ['FLAT', 'Flat (close)']]} onChange={v => setManual({ ...manual, side: v })} /></Field>
        <Field label="Strength (0–100)"><input aria-label="Signal strength" type="number" min={0} max={100} value={manual.strength} onChange={e => setManual({ ...manual, strength: Number(e.target.value) })} /></Field>
        <Field label="Reason"><input aria-label="Signal reason" required minLength={5} value={manual.reason} onChange={e => setManual({ ...manual, reason: e.target.value })} /></Field>
      </form>
    </FloatingWindow>}
  </section>;
}

function Quality() {
  const [data, error] = useData<Row>('trading/signals/quality', 60_000);
  return <section className="intel"><Msg error={error} />
    <p className="cfg-note">Each signal's move over its horizon (10 bars), signed so that + means the market went the signal's way. Hit rate = share of measured signals that were right.</p>
    <table className="cfg-table compact intel-table"><thead><tr><th>Strategy</th><th>Instrument</th><th>Timeframe</th><th>Signals</th><th>Acted</th><th>Refused</th><th>Measured</th><th>Hit rate</th><th>Average move %</th></tr></thead><tbody>
      {(data?.items ?? []).map((r: Row) => <tr key={r.strategy + r.symbol + r.timeframe}><td>{r.strategy}</td><td>{r.symbol}</td><td>{r.timeframe}</td><td>{r.signals}</td><td>{r.acted}</td><td>{r.rejected}</td>
        <td>{r.measured}</td><td>{r.hit_rate_pct == null ? '–' : `${r.hit_rate_pct}%`}</td><td className={Number(r.avg_return_pct) > 0 ? 'up' : 'down'}>{num(r.avg_return_pct, 3)}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={9} className="cfg-note">No signals measured yet.</td></tr>}</tbody></table></section>;
}

export function SignalsView() {
  const sections: Section[] = [
    { id: 'overview', label: 'Dashboard', group: 'Overview', hint: 'What the library says now: long, short and flat scores, the strongest scores, the composite of each instrument and the latest flips.', render: () => <SignalDashboard /> },
    { id: 'board', label: 'Live signal board', group: 'Overview', hint: 'Every collected instrument × every library signal with its composite: filter, sort, click a score for its equation with this bar’s numbers.', render: () => <SignalBoardLive /> },
    { id: 'graphs', label: 'Signal graphs', group: 'Work', hint: 'Every signal as nodes and wires (n8n-style) with each node’s value on the bar you choose: inspect, copy, change, check, propose, approve, put to use.', render: ctx => <GraphWorkbench ctx={ctx} purposes={['SIGNAL']} intro="Every library signal is drawn here as a graph and is equal to its code on every bar. Copy one to make your own; once approved it can score a signal definition instead of the library formula." /> },
    { id: 'explain', label: 'Explain an instrument', group: 'Work', hint: 'One instrument: each signal’s formula, its inputs on the last bar, the arithmetic, the side, and a chart against price.', render: () => <SignalExplain /> },
    { id: 'timeframes', label: 'Across timeframes', group: 'Work', hint: 'One instrument: every signal on every timeframe and the composite of each — do the horizons agree?', render: () => <SignalTimeframes /> },
    { id: 'research', label: 'Signal research', group: 'Work', hint: 'Does one signal predict? Information coefficient, t-stat, hit rate, quintiles, turnover and cost over several horizons.', render: () => <SignalResearch /> },
    { id: 'rank', label: 'Rank every signal', group: 'Work', hint: 'Every active signal researched on the same bars and ranked: the candidates for a strategy’s signal blend.', render: () => <ResearchAll /> },
    { id: 'correlation', label: 'Signal correlation', group: 'Monitor', hint: 'How alike the signals are on one instrument: redundant pairs count twice in a blend.', render: () => <SignalCorrelation /> },
    { id: 'flips', label: 'Signal flips', group: 'Monitor', hint: 'Every time a signal changed side on a closed bar.', render: () => <SignalFlips /> },
    { id: 'library', label: 'Signal library', group: 'Configure', hint: 'Every signal formula with its parameters, threshold and timeframes: tune, pause or add your own.', render: ctx => <SignalLibrary ctx={ctx} /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Every signal formula, the side rule, the composite, flips, correlation and how research measures prediction.', render: () => <Formulas stage="Signals" /> },
  ];
  return <StageShell view="Signals" sections={sections} />;
}

/* ---------- Stage 4 · Strategies ---------- */

function Runtime({ ctx }: { ctx: Ctx }) {
  const [data, error] = useData<Row>('trading/runtime', 15_000);
  return <section className="intel">
    <RuntimeLine canRun={ctx.canOps || ctx.canRisk} />
    <div className="intel-bar"><button onClick={() => goto('Configuration', 'strategies')}>Strategies and versions (Setup)</button><button onClick={() => goto('Configuration', 'deployments')}>Deployments (Setup)</button></div>
    <Msg error={error} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Strategy</th><th>Algorithm</th><th>Instrument</th><th>Account</th><th>Mode</th><th>Timeframe</th><th>Data from</th><th>Last bar</th><th>Target</th><th>State</th></tr></thead><tbody>
      {(data?.items ?? []).map((r: Row) => <tr key={r.deployment_id}><td>{r.strategy_code} v{r.version_number}</td><td><small>{r.algorithm}</small></td><td>{r.symbol}</td><td>{r.account_code}</td>
        <td>{(r.mode ?? 'PAPER').toLowerCase()}</td><td>{r.timeframe}</td><td><small>{r.source_symbol ?? '—'}</small></td><td><small>{r.last_bar_time ? new Date(r.last_bar_time).toISOString().slice(5, 16).replace('T', ' ') : '—'}</small></td>
        <td>{r.last_target ?? '—'}</td><td>{r.status !== 'ACTIVE' ? pill(r.status, 'off') : r.last_error ? <small className="cfg-error-text">{r.last_error}</small> : pill('running', 'ok')}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={10} className="cfg-note">No deployment has run yet. Setup › Strategies (add a version, approve it) › Deployments (instrument, account, Paper) › activate. The runtime picks it up at the next cycle.</td></tr>}
    </tbody></table></div>
    <p className="cfg-note">Each active deployment is evaluated once per newly closed bar of its timeframe, from its data source's bars and the same features as Market intelligence. The first bar after activation is only recorded; signals start from the next one.</p>
  </section>;
}

function ParamInputs({ schema, value, change }: { schema: Row; value: Row; change: (v: Row) => void }) {
  const all: Row = schema?.properties ?? {}, blend = 'components' in all;
  const props: Row = blend ? Object.fromEntries(Object.entries(all).filter(([k]) => !['components', 'entry', 'exit'].includes(k))) : all;
  return <><div className="st-params">{Object.entries(props).map(([k, p]: [string, any]) => {
    const enumOf = p.enum ?? p.anyOf?.find((x: Row) => x.enum)?.enum ?? (p.$ref ? undefined : undefined);
    const choices: string[] | undefined = p.type === 'array' ? p.items?.enum ?? p.items?.anyOf?.find((x: Row) => x.enum)?.enum : undefined;
    if (choices) return <Field key={k} label={k.replace(/_/g, ' ')} hint={p.description}><span className="st-choices">{choices.map(c => {
      const on: string[] = value[k] ?? [];   // e.g. allowed_regimes: tick the market states this strategy may enter in (none = any)
      return <label key={c}><input type="checkbox" aria-label={`${k} ${c}`} checked={on.includes(c)}
        onChange={e => change({ ...value, [k]: e.target.checked ? [...on, c] : on.filter(x => x !== c) })} /> {c.toLowerCase().replace(/_/g, ' ')}</label>; })}</span></Field>;
    if (p.type === 'array') return <Field key={k} label={k.replace(/_/g, ' ')} hint="Conditions as JSON: [{&quot;field&quot;:&quot;rsi14&quot;,&quot;op&quot;:&quot;<&quot;,&quot;value&quot;:&quot;30&quot;}]">
      <textarea aria-label={`Parameter ${k}`} rows={2} value={JSON.stringify(value[k] ?? [])} onChange={e => { try { change({ ...value, [k]: JSON.parse(e.target.value) }); } catch { /* keep typing */ } }} /></Field>;
    const opts = enumOf ?? (k === 'timeframe' ? ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'] : k === 'direction' ? ['BOTH', 'LONG_ONLY', 'SHORT_ONLY'] : null);
    return <Field key={k} label={k.replace(/_/g, ' ')} hint={p.description}>{opts ? <select aria-label={`Parameter ${k}`} value={value[k] ?? p.default ?? opts[0]} onChange={e => change({ ...value, [k]: e.target.value })}>
      {opts.map((o: string) => <option key={o}>{o}</option>)}</select>
      : <input aria-label={`Parameter ${k}`} inputMode="decimal" value={value[k] ?? p.default ?? ''} onChange={e => change({ ...value, [k]: e.target.value === '' ? undefined : Number(e.target.value) })} />}</Field>;
  })}</div>{blend && <BlendEditor value={value} change={change} />}</>;
}

function Sparkline({ points }: { points: { t: string; equity: string }[] }) {
  if (points.length < 2) return null;
  const ys = points.map(p => Number(p.equity)), lo = Math.min(...ys), hi = Math.max(...ys), W = 700, H = 120;
  const x = (i: number) => (i / (points.length - 1)) * W, y = (v: number) => H - ((v - lo) / ((hi - lo) || 1)) * (H - 10) - 5;
  return <svg className="st-spark" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Equity curve"><polyline fill="none" stroke="var(--ready)" strokeWidth="1.5" points={points.map((p, i) => `${x(i)},${y(Number(p.equity))}`).join(' ')} /></svg>;
}

function Backtest({ ctx }: { ctx: Ctx }) {
  const [algos] = useData<Row>('trading/algorithms', 0), [sources] = useData<Row>('trading/sources', 0), [runs, , reloadRuns] = useData<Row>('trading/backtests', 0);
  const { strategies } = Lists();
  const [versions, setVersions] = useState<Row[]>([]), [strategyId, setStrategyId] = useState('');
  const today = new Date(), monthAgo = new Date(Date.now() - 30 * 86400_000);
  const [f, setF] = useState<Row>({ use: 'VERSION', strategy_version_id: '', algorithm: 'EMA_CROSSOVER', parameters: { timeframe: 'H1', fast: 9, slow: 21 }, source: '',
    range_from: monthAgo.toISOString().slice(0, 10), range_to: today.toISOString().slice(0, 10), quantity: '1', spread_ticks: 0, commission_per_lot: '0', starting_capital: '100000' });
  const [result, setResult] = useState<Row | null>(null), [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const evidence = useScanEvidence('backtest');
  const evidenceStrategyId = strategies.find((row: Row) => row.code === evidence?.strategy_code)?.id;
  useEffect(() => {
    if (!evidence || !evidenceStrategyId || !evidence.strategy_version_id) return;
    setStrategyId(String(evidenceStrategyId));
    setF(value => ({ ...value, use: 'VERSION', strategy_version_id: evidence.strategy_version_id,
      source: evidence.account_id + '|' + evidence.broker_symbol }));
  }, [evidence, evidenceStrategyId]);
  useEffect(() => {
    let live = true;
    setVersions([]);
    if (strategyId) void api(`config/strategies/${strategyId}/versions`)
      .then(data => { if (live) setVersions(data.items); }).catch(() => undefined);
    return () => { live = false; };
  }, [strategyId]);
  const sourceOptions = sources?.items ?? [];
  const evidenceSource = evidence && evidence.account_id + '|' + evidence.broker_symbol;
  const algo = (algos?.items ?? []).find((a: Row) => a.id === f.algorithm);
  const run = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('');
    const [account_id, broker_symbol] = String(f.source).split('|');
    try {
      const body: Row = { account_id, broker_symbol, range_from: new Date(f.range_from).toISOString(), range_to: new Date(f.range_to + 'T23:59:59Z').toISOString(),
        quantity: String(f.quantity), spread_ticks: Number(f.spread_ticks), commission_per_lot: String(f.commission_per_lot), starting_capital: String(f.starting_capital) };
      if (f.use === 'VERSION') body.strategy_version_id = f.strategy_version_id; else { body.algorithm = f.algorithm; body.parameters = f.parameters; }
      setResult(await api('trading/backtests', 'POST', body)); reloadRuns();
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  const m = result?.metrics;
  return <section className="intel">
    <form className="cfg-form" onSubmit={run}>
      {evidence && <p className="cfg-note">Scan selection · {evidence.instrument_symbol} · {evidence.strategy_code} · {evidence.timeframe} · {evidence.broker_symbol}. Review the date range and costs before running.</p>}
      <Field label="Rules to test"><Choice label="Backtest rules" value={f.use} options={[['VERSION', 'A strategy version'], ['IDEA', 'An algorithm and parameters (idea)']]} onChange={v => setF({ ...f, use: v })} /></Field>
      {f.use === 'VERSION' ? <div className="cfg-pair">
        <Field label="Strategy"><select aria-label="Backtest strategy" value={strategyId} onChange={e => { setStrategyId(e.target.value); setF({ ...f, strategy_version_id: '' }); }}><option value="">Choose…</option>{strategies.map((s: Row) => <option key={s.id} value={s.id}>{s.code}</option>)}</select></Field>
        <Field label="Version"><select aria-label="Backtest version" required value={f.strategy_version_id} onChange={e => setF({ ...f, strategy_version_id: e.target.value })}><option value="">Choose…</option>
          {versions.map(v => <option key={v.id} value={v.id}>v{v.number} · {v.state.toLowerCase()} · {v.algorithm}</option>)}</select></Field></div>
        : <><Field label="Algorithm" hint={algo?.description}><select aria-label="Backtest algorithm" value={f.algorithm} onChange={e => setF({ ...f, algorithm: e.target.value, parameters: { timeframe: f.parameters.timeframe } })}>
            {(algos?.items ?? []).map((a: Row) => <option key={a.id} value={a.id}>{a.label}</option>)}</select></Field>
          {algo && <ParamInputs schema={algo.schema} value={f.parameters} change={p => setF({ ...f, parameters: p })} />}</>}
      <Field label="Data (collected source)"><select aria-label="Backtest source" required value={f.source} onChange={e => setF({ ...f, source: e.target.value })}><option value="">Choose…</option>
        {evidence && evidenceSource && !sourceOptions.some((s: Row) => s.account_id + '|' + s.broker_symbol === evidenceSource) &&
          <option value={evidenceSource}>{evidence.broker_symbol} · Scan source</option>}
        {sourceOptions.map((s: Row) => <option key={s.account_id + s.broker_symbol} value={`${s.account_id}|${s.broker_symbol}`}>{s.account_code} · {s.broker_symbol}{s.first_bar ? ` · bars ${s.first_bar.slice(0, 10)} → ${String(s.last_bar).slice(0, 10)}` : ' · no bars yet'}</option>)}</select></Field>
      <div className="cfg-pair"><Field label="From"><input aria-label="Backtest from" type="date" value={f.range_from} onChange={e => setF({ ...f, range_from: e.target.value })} /></Field>
        <Field label="To"><input aria-label="Backtest to" type="date" value={f.range_to} onChange={e => setF({ ...f, range_to: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Quantity (lots)"><input aria-label="Backtest quantity" value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} /></Field>
        <Field label="Spread paid on buys (ticks)" hint="Bars are bid prices"><input aria-label="Backtest spread" type="number" min={0} value={f.spread_ticks} onChange={e => setF({ ...f, spread_ticks: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Commission per lot"><input aria-label="Backtest commission" value={f.commission_per_lot} onChange={e => setF({ ...f, commission_per_lot: e.target.value })} /></Field>
        <Field label="Starting capital"><input aria-label="Backtest capital" value={f.starting_capital} onChange={e => setF({ ...f, starting_capital: e.target.value })} /></Field></div>
      {(ctx.canOps || ctx.canRisk) && <div className="cfg-actions"><button className="primary" disabled={busy}>{busy ? 'Running…' : 'Run backtest'}</button></div>}
      <p className="cfg-note">Point in time: a decision on a bar's close is filled at the next bar's open. Same code as the live runtime.</p>
    </form>
    <Msg error={err} />
    {m && <>
      <Kpis items={[['Net P&L', money(m.net_pnl, m.currency), undefined, Number(m.net_pnl) >= 0 ? 'up' : 'down'], ['Return', `${m.return_pct}%`], ['Trades', m.trades], ['Win rate', m.win_rate_pct == null ? '–' : `${m.win_rate_pct}%`],
        ['Profit factor', m.profit_factor ?? '–'], ['Largest drawdown', `${m.max_drawdown_pct}%`], ['Sharpe', m.sharpe ?? '–'], ['Time in market', `${m.exposure_pct}%`]]} />
      <Sparkline points={result!.equity} />
      {m.note && <p className="cfg-note">{m.note}</p>}
      <h4>Trades ({result!.trades.length})</h4>
      <div className="cfg-scroll st-short"><table className="cfg-table compact intel-table"><thead><tr><th>Side</th><th>Entry</th><th>Price</th><th>Exit</th><th>Price</th><th>P&L</th><th>Exit reason</th></tr></thead><tbody>
        {result!.trades.slice().reverse().map((t: Row, i: number) => <tr key={i}><td>{t.side}</td><td><small>{t.entry_time.slice(0, 16).replace('T', ' ')}</small></td><td>{t.entry}</td>
          <td><small>{t.exit_time.slice(0, 16).replace('T', ' ')}</small></td><td>{t.exit}</td><td className={Number(t.pnl) >= 0 ? 'up' : 'down'}>{t.pnl}</td><td><small>{t.reason}</small></td></tr>)}</tbody></table></div></>}
    <h4>Earlier runs</h4>
    <table className="cfg-table compact intel-table"><thead><tr><th>When</th><th>Rules</th><th>Data</th><th>Timeframe</th><th>Period</th><th>Trades</th><th>Net P&L</th><th>Drawdown</th></tr></thead><tbody>
      {(runs?.items ?? []).map((r: Row) => <tr key={r.id} className="clickable" onClick={() => void api(`trading/backtests/${r.id}`).then(setResult)}><td><small>{when(r.created_at)}</small></td>
        <td>{r.strategy_code ? `${r.strategy_code} v${r.version_number}` : r.algorithm}</td><td>{r.account_code} · {r.broker_symbol}</td><td>{r.timeframe}</td>
        <td><small>{r.range_from.slice(0, 10)} → {r.range_to.slice(0, 10)}</small></td><td>{r.metrics.trades}</td><td>{r.metrics.net_pnl}</td><td>{r.metrics.max_drawdown_pct}%</td></tr>)}</tbody></table>
  </section>;
}

function Performance() {
  const [data, error] = useData<Row>('trading/performance', 30_000);
  return <section className="intel"><Msg error={error} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Strategy</th><th>Mode</th><th>Instrument</th><th>Account</th><th>Fills</th><th>Realised</th><th>Open</th><th>Unrealised</th><th>Win rate</th><th>Profit factor</th></tr></thead><tbody>
      {(data?.items ?? []).map((r: Row, i: number) => <tr key={i}><td>{r.strategy}{r.version ? ` v${r.version}` : ''}</td><td>{(r.mode ?? '—').toLowerCase()}</td><td>{r.symbol}</td><td>{r.account_code}</td><td>{r.fills}</td>
        <td className={Number(r.realised) >= 0 ? 'up' : 'down'}>{money(r.realised, r.currency)}</td><td>{num(r.open_qty, 2)}</td><td className={Number(r.unrealised) >= 0 ? 'up' : 'down'}>{money(r.unrealised, r.currency)}</td>
        <td>{r.win_rate_pct == null ? '–' : `${r.win_rate_pct}%`}</td><td>{r.profit_factor ?? '–'}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={10} className="cfg-note">No fills yet.</td></tr>}</tbody></table>
    <p className="cfg-note">P&L in the instrument's quote currency (EURUSD in USD, NIFTY in INR). Win rate and profit factor count closing fills.</p></section>;
}

export function StrategiesView() {
  const sections: Section[] = [
    { id: 'overview', label: 'Dashboard', group: 'Overview', hint: 'Every strategy at once: position and P&L, last signal and decision, drift, and how its decisions reach execution — pause, resume, change handoff.', render: ctx => <StrategyDashboard ctx={ctx} /> },
    { id: 'runtime', label: 'Runtime', group: 'Overview', hint: 'Every deployment: is it running, on which bar, what target, and what needs attention.', render: ctx => <Runtime ctx={ctx} /> },
    { id: 'signals', label: 'Strategy signals', group: 'Work', hint: 'Signals raised by running strategies (and by hand) with their state and reasons; open one for its history; raise one by hand.', render: ctx => <SignalBoard ctx={ctx} /> },
    { id: 'intents', label: 'Decisions', group: 'Work', hint: 'What each signal decided: target, position held, the order needed, and how it went to execution.', render: ctx => <DecisionFlow ctx={ctx} compact /> },
    { id: 'backtest', label: 'Backtest', group: 'Work', hint: 'A version, or an idea, on stored history with costs; trades, equity and metrics.', render: ctx => <Backtest ctx={ctx} /> },
    { id: 'compare', label: 'Compare backtests', group: 'Work', hint: 'Up to four saved backtests side by side: every metric and the equity curves.', render: () => <BacktestCompare /> },
    { id: 'optimise', label: 'Optimise', group: 'Work', hint: 'Every combination of parameter values, and walk-forward: chosen on one part, tested on the next.', render: ctx => <Optimise ctx={ctx} /> },
    { id: 'performance', label: 'Performance', group: 'Monitor', hint: 'Live results per strategy, instrument and account (paper and real).', render: () => <Performance /> },
    { id: 'quality', label: 'Signal quality', group: 'Monitor', hint: 'Were the strategies\' signals right? Forward move and hit rate per strategy and instrument.', render: () => <Quality /> },
    { id: 'drift', label: 'Drift', group: 'Monitor', hint: 'Is each running strategy still behaving like its backtest?', render: () => <Drift /> },
    { id: 'checks', label: 'Checks and decision', group: 'Configure', hint: 'The checks every strategy signal passes, and how signals become a decision.',
      render: ctx => <SettingsForm canEdit={ctx.canRisk} fields={['max_quote_age_seconds', 'max_spread_atr_pct', 'block_on_event', 'min_strength', 'decision_mode', 'agree_count']} /> },
    { id: 'drift-settings', label: 'Drift settings', group: 'Configure', hint: 'When live results count as drift.', render: ctx => <SettingsForm canEdit={ctx.canRisk} fields={['drift_min_trades', 'drift_tolerance_pct']} /> },
    { id: 'algorithms', label: 'Algorithms', group: 'Configure', hint: 'The rule sets a version can use, each with its equation, strength formula and parameters.', render: () => <AlgorithmEquations /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Every algorithm’s rule, sizing (fixed, value, % of equity at an ATR stop), protective exits, backtest fills and metrics, walk-forward and drift.', render: () => <Formulas stage="Strategies" /> },
    { id: 'graphs', label: 'Calculation graphs', group: 'Configure', hint: 'Strategy rules, sizing and stop/target as graphs (n8n-style): draft, check on stored bars, propose and approve; an approved rule graph runs as a strategy (Setup › Strategies › algorithm Calculation graph).', render: ctx => <GraphWorkbench ctx={ctx} purposes={['STRATEGY_RULE', 'SIZING', 'PROTECTIVE']} /> },
    { id: 'runtime-settings', label: 'Runtime switch', group: 'Configure', hint: 'Pause or resume every strategy at once.',
      render: ctx => <SettingsForm canEdit={ctx.canRisk} fields={['runtime_enabled']} intro="Paused: no deployment is evaluated and no new signal is raised; orders already working stay as they are. Pause one deployment from Setup › Deployments." /> },
  ];
  return <StageShell view="Strategies" sections={sections} />;
}

/* ---------- Stage 5 · Risk ---------- */

const SCOPES: [string, string][] = [['FIRM', 'Firm (everything)'], ['ACCOUNT', 'Account'], ['STRATEGY', 'Strategy'], ['INSTRUMENT', 'Instrument'], ['DEPLOYMENT', 'Deployment']];

function TargetPicker({ scope, value, change }: { scope: string; value: string; change: (v: string) => void }) {
  const l = Lists();
  const opts: [string, string][] = scope === 'ACCOUNT' ? l.accounts.map((a: Row) => [a.id, `${a.code} · ${a.environment}`]) : scope === 'STRATEGY' ? l.strategies.map((s: Row) => [s.id, s.code])
    : scope === 'INSTRUMENT' ? l.instruments.map((i: Row) => [i.id, i.symbol]) : scope === 'DEPLOYMENT' ? l.deployments.map((d: Row) => [d.id, depName(d)]) : [];
  if (scope === 'FIRM') return null;
  return <Field label="Which"><select aria-label="Target" required value={value} onChange={e => change(e.target.value)}><option value="">Choose…</option>{opts.map(([id, n]) => <option key={id} value={id}>{n}</option>)}</select></Field>;
}

function RiskOverview() {
  const [ov] = useData<Row>('trading/overview', 15_000), [exp] = useData<Row>('trading/risk/exposure', 20_000), [kills] = useData<Row>('trading/risk/kill-switches', 15_000);
  return <section className="intel">
    <Kpis items={[['Kill switches on', ov?.kill_switches ?? 0, 'New orders stopped for these scopes', ov?.kill_switches ? 'warn' : ''], ['Limits in force', ov?.limits ?? 0],
      ['Refused today', ov?.refused_today ?? 0, 'Orders refused by risk today (Monitor › Decisions)'], ['Open positions', ov?.positions ?? 0], ['Working orders', ov?.open_orders ?? 0]]} />
    {(kills?.items ?? []).length > 0 && <><h4>Kill switches on</h4><ul>{kills!.items.map((k: Row) => <li key={k.id}><b>{k.scope.toLowerCase()} {k.target_name}</b> — {k.reason} <small className="cfg-note">({k.automatic ? 'automatic' : k.engaged_by}, {when(k.engaged_at)})</small></li>)}</ul></>}
    <h4>Accounts</h4>
    <table className="cfg-table compact intel-table"><thead><tr><th>Account</th><th>Equity</th><th>Today</th><th>Drawdown</th><th>Gross exposure</th><th>Net exposure</th></tr></thead><tbody>
      {(exp?.accounts ?? []).map((a: Row) => <tr key={a.account_id}><td>{a.account_code}</td><td>{money(a.equity, a.currency)}</td><td className={Number(a.day_pnl) >= 0 ? 'up' : 'down'}>{money(a.day_pnl, a.currency)}</td>
        <td>{a.drawdown_pct}%</td><td>{money(a.gross, a.currency)}</td><td>{money(a.net, a.currency)}</td></tr>)}
      {exp && !exp.accounts.length && <tr><td colSpan={6} className="cfg-note">No open positions.</td></tr>}</tbody></table>
    <p className="cfg-note">Order of checks for every order: kill switches → order sending (real orders) → market open and price → lot rules → limits (the tightest of firm, account, strategy, instrument, deployment). A refusal lists every reason.</p>
  </section>;
}

function KillSwitches({ ctx }: { ctx: Ctx }) {
  const [history, setHistory] = useState(false), [data, error, reload] = useData<Row>(`trading/risk/kill-switches${history ? '?history=true' : ''}`, 15_000, [history]);
  const [f, setF] = useState<Row | null>(null), [err, setErr] = useState(''), [msg, setMsg] = useState('');
  const engage = async (e: FormEvent) => { e.preventDefault(); if (!f) return;
    try { await api('trading/risk/kill-switches', 'POST', { scope: f.scope, target_id: f.scope === 'FIRM' ? null : f.target_id, reason: f.reason }); setF(null); setMsg('Kill switch on: new orders in that scope are refused.'); reload(); } catch (x) { setErr((x as Error).message); } };
  return <section className="intel">
    <div className="intel-bar">{(ctx.canOps || ctx.canRisk) && <button className="danger" onClick={() => setF({ scope: 'ACCOUNT', target_id: '', reason: '' })}>Engage a kill switch</button>}
      <label className="intel-check"><input type="checkbox" checked={history} onChange={e => setHistory(e.target.checked)} /> Show released</label></div>
    <Msg error={error || err} notice={msg} />
    {f && <form className="cfg-form intel-edit" onSubmit={engage}>
      <Field label="Scope"><Choice label="Kill scope" value={f.scope} options={SCOPES} onChange={v => setF({ ...f, scope: v, target_id: '' })} /></Field>
      <TargetPicker scope={f.scope} value={f.target_id} change={v => setF({ ...f, target_id: v })} />
      <Field label="Reason"><input aria-label="Kill reason" required minLength={5} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="danger">Stop new orders</button><button type="button" onClick={() => setF(null)}>Cancel</button></div></form>}
    <table className="cfg-table compact intel-table"><thead><tr><th>Scope</th><th>Which</th><th>Reason</th><th>By</th><th>Since</th><th>State</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((k: Row) => <tr key={k.id}><td>{k.scope.toLowerCase()}</td><td>{k.target_name}</td><td>{k.reason}</td><td>{k.automatic ? 'automatic (loss limit)' : k.engaged_by}</td><td><small>{when(k.engaged_at)}</small></td>
        <td>{k.engaged ? pill('on', 'live') : <small>released by {k.released_by}: {k.release_reason}</small>}</td>
        <td>{k.engaged && ctx.canRisk && <button onClick={() => { const r = window.prompt('Reason for releasing this kill switch'); if (r) void api(`trading/risk/kill-switches/${k.id}/release`, 'POST', { reason: r }).then(() => { setMsg('Released.'); reload(); }).catch(e => setErr((e as Error).message)); }}>Release</button>}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={7} className="cfg-note">No kill switch is on.</td></tr>}</tbody></table>
    <p className="cfg-note">A kill switch stops new orders in its scope. An operator's Flatten (Portfolio › Positions) still closes positions. Releasing needs the Risk approver role and a reason; a loss-limit stop is engaged automatically and released by a person.</p>
  </section>;
}

function Limits({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/risk/limits', 30_000);
  const [f, setF] = useState<Row | null>(null), [err, setErr] = useState(''), [msg, setMsg] = useState('');
  const kinds: Row = data?.kinds ?? {};
  const save = async (e: FormEvent) => { e.preventDefault(); if (!f) return;
    const body = { scope: f.scope, target_id: f.scope === 'FIRM' ? null : f.target_id, kind: f.kind, value: String(f.value), enabled: !!f.enabled, note: f.note ?? '', reason: f.reason };
    try { if (f.id) await api(`trading/risk/limits/${f.id}`, 'PUT', { ...body, version: f.version }); else await api('trading/risk/limits', 'POST', body); setF(null); setMsg('Saved.'); reload(); } catch (x) { setErr((x as Error).message); } };
  return <section className="intel">
    {ctx.canRisk && <div className="intel-bar"><button className="primary" onClick={() => setF({ scope: 'ACCOUNT', target_id: '', kind: 'MAX_DAILY_LOSS', value: '', enabled: true, note: '', reason: '' })}>Add a limit</button></div>}
    <Msg error={error || err} notice={msg} />
    {f && <form className="cfg-form intel-edit" onSubmit={save}>
      <div className="cfg-pair"><Field label="Scope"><Choice label="Limit scope" value={f.scope} options={SCOPES} onChange={v => !f.id && setF({ ...f, scope: v, target_id: '' })} /></Field>
        <TargetPicker scope={f.scope} value={f.target_id} change={v => setF({ ...f, target_id: v })} /></div>
      <Field label="Limit" hint={kinds[f.kind]}><select aria-label="Limit kind" disabled={!!f.id} value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })}>{Object.entries(kinds).map(([k, t]) => <option key={k} value={k}>{t as string}</option>)}</select></Field>
      <Field label="Value"><input aria-label="Limit value" required inputMode="decimal" value={f.value} onChange={e => setF({ ...f, value: e.target.value })} /></Field>
      <label><input type="checkbox" checked={!!f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} /> In force</label>
      <Field label="Note"><input aria-label="Limit note" value={f.note ?? ''} onChange={e => setF({ ...f, note: e.target.value })} /></Field>
      <Field label="Reason (audit log)"><input aria-label="Limit reason" required minLength={5} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Save limit</button><button type="button" onClick={() => setF(null)}>Cancel</button></div></form>}
    <table className="cfg-table compact intel-table"><thead><tr><th>Scope</th><th>Which</th><th>Limit</th><th>Value</th><th>State</th><th>Changed</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((l: Row) => <tr key={l.id}><td>{l.scope.toLowerCase()}</td><td>{l.target_name}</td><td>{kinds[l.kind] ?? l.kind}</td><td><b>{num(l.value, l.kind.includes('QTY') ? 2 : 2)}</b></td>
        <td>{l.enabled ? pill('in force', 'ok') : pill('off', 'off')}</td><td><small>{when(l.updated_at)} · {l.updated_by}</small></td>
        <td>{ctx.canRisk && <><button onClick={() => setF({ ...l, reason: '' })}>Edit</button> <button onClick={() => { const r = window.prompt('Reason for deleting this limit'); if (r) void api(`trading/risk/limits/${l.id}?reason=${encodeURIComponent(r)}`, 'DELETE').then(() => reload()).catch(e => setErr((e as Error).message)); }}>Delete</button></>}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={7} className="cfg-note">No limits yet. Start with a daily loss and a position limit per account.</td></tr>}</tbody></table>
    <p className="cfg-note">The tightest limit of each kind applies. Orders that reduce a position skip the position, exposure and loss limits. Breaching a daily-loss or drawdown limit engages the account's kill switch automatically.</p>
  </section>;
}

function Decisions() {
  const [filter, setFilter] = useState(''), [data, error] = useData<Row>(`trading/risk/decisions${filter ? `?approved=${filter}` : ''}`, 20_000, [filter]);
  return <section className="intel">
    <div className="intel-bar"><select aria-label="Decision filter" value={filter} onChange={e => setFilter(e.target.value)}><option value="">All</option><option value="false">Refused</option><option value="true">Approved</option></select></div>
    <Msg error={error} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Time</th><th>Strategy</th><th>Instrument</th><th>Account</th><th>Side</th><th>Quantity</th><th>Result</th><th>Reasons</th></tr></thead><tbody>
      {(data?.items ?? []).map((d: Row) => <tr key={d.id}><td><small>{when(d.at)}</small></td><td>{d.strategy_code ?? 'manual'}</td><td>{d.symbol}</td><td>{d.account_code}</td><td>{d.side}</td>
        <td>{num(d.requested_qty, 2)}</td><td>{d.approved ? pill('approved', 'ok') : pill('refused', 'live')}</td><td><Reasons list={d.reasons} /></td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={8} className="cfg-note">No risk decisions yet.</td></tr>}</tbody></table></section>;
}

function Exposure() {
  const [data, error] = useData<Row>('trading/risk/exposure', 20_000);
  return <section className="intel"><Msg error={error} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Account</th><th>Instrument</th><th>Net</th><th>Gross</th></tr></thead><tbody>
      {(data?.instruments ?? []).map((e: Row) => <tr key={e.account_id + e.instrument_id}><td>{e.account_code}</td><td>{e.symbol}</td><td className={Number(e.net) >= 0 ? 'up' : 'down'}>{money(e.net, e.currency)}</td><td>{money(e.gross, e.currency)}</td></tr>)}
      {data && !(data.instruments ?? []).length && <tr><td colSpan={4} className="cfg-note">No open positions.</td></tr>}</tbody></table>
    <p className="cfg-note">Position value at the live mid, converted to the account's currency with collected FX pairs. A position whose currency cannot be converted is listed under Portfolio with a warning and blocks exposure checks.</p></section>;
}

export function RiskView() {
  const sections: Section[] = [
    { id: 'overview', label: 'Dashboard', group: 'Overview', hint: 'Kill switches, limits, refusals and each account\'s equity, loss today and exposure.', render: () => <RiskOverview /> },
    { id: 'kill', label: 'Kill switches', group: 'Work', hint: 'Stop new orders for the firm, an account, a strategy, an instrument or one deployment.', render: ctx => <KillSwitches ctx={ctx} /> },
    { id: 'limits', label: 'Limits', group: 'Work', hint: 'Order size and value, position, working orders, orders per minute, daily loss, drawdown, exposure, margin, leverage, VaR, price deviation — per scope.', render: ctx => <Limits ctx={ctx} /> },
    { id: 'decisions', label: 'Decisions', group: 'Monitor', hint: 'Every pre-trade check with its result and reasons.', render: () => <Decisions /> },
    { id: 'exposure', label: 'Exposure', group: 'Monitor', hint: 'Net and gross position value per account and instrument.', render: () => <Exposure /> },
    { id: 'var', label: 'VaR and margin', group: 'Monitor', hint: 'How much each account could lose in a bad bar, and the margin in use.', render: () => <VarMargin /> },
    { id: 'stress', label: 'Stress tests', group: 'Monitor', hint: 'Today\'s positions under price shocks: everything, an asset class, a currency or one symbol.', render: ctx => <Stress ctx={ctx} /> },
    { id: 'var-settings', label: 'VaR settings', group: 'Configure', hint: 'Confidence, horizon and look-back of the VaR.', render: ctx => <SettingsForm canEdit={ctx.canRisk} fields={['var_confidence', 'var_timeframe', 'var_lookback_bars']} /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Every pre-trade check: limits, loss and drawdown, exposure, margin and leverage, price deviation, stale price, VaR, ES and stress.', render: () => <Formulas stage="Risk" /> },
    { id: 'graphs', label: 'Calculation graphs', group: 'Configure', hint: 'Extra risk refusals as graphs: they can only refuse, fail closed, and the built-in limits always run.', render: ctx => <GraphWorkbench ctx={ctx} purposes={['RISK_CHECK']} /> },
  ];
  return <StageShell view="Risk" sections={sections} />;
}

/* ---------- Stage 6 · Portfolio ---------- */

function Accounts({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/portfolio/accounts', 15_000);
  const set = (a: Row) => { const amount = window.prompt(`Starting paper capital for ${a.account_code} (${a.currency})`, a.starting_capital); if (!amount) return;
    const reason = window.prompt('Reason (audit log)'); if (!reason) return;
    void api(`trading/portfolio/accounts/${a.account_id}/capital`, 'PUT', { amount, reason }).then(reload).catch(e => alert((e as Error).message)); };
  return <section className="intel"><Msg error={error} />
    <div className="st-cards">{(data?.items ?? []).map((a: Row) => <div key={a.account_id} className="st-card">
      <b>{a.account_code}</b><small className="cfg-note">{a.currency}</small>
      <div className="st-big">{money(a.equity, a.currency)}</div>
      <table className="cfg-table compact"><tbody><tr><td>Starting capital</td><td>{money(a.starting_capital)}{!a.capital_set && <small className="cfg-note"> default</small>}</td></tr>
        <tr><td>Realised</td><td className={Number(a.realised) >= 0 ? 'up' : 'down'}>{money(a.realised)}</td></tr><tr><td>Unrealised</td><td className={Number(a.unrealised) >= 0 ? 'up' : 'down'}>{money(a.unrealised)}</td></tr>
        <tr><td>Today</td><td className={Number(a.day_pnl) >= 0 ? 'up' : 'down'}>{money(a.day_pnl)}</td></tr><tr><td>Drawdown from peak</td><td>{a.drawdown_pct}%</td></tr></tbody></table>
      {a.missing_fx?.length > 0 && <p className="cfg-error">No FX rate for {a.missing_fx.join(', ')} → {a.currency}: collect that pair (e.g. USDINR).</p>}
      {ctx.canRisk && <button onClick={() => set(a)}>Set starting capital</button>}</div>)}
      {data && !(data.items ?? []).length && <p className="cfg-note">No account has a deployment or a position yet.</p>}</div>
    <p className="cfg-note">Paper equity = starting capital + realised + unrealised P&L, in the account's currency. Real accounts: the broker's own balance and equity are under Monitor › Broker positions; sizing of a real deployment uses the broker's equity.</p></section>;
}

function Positions({ ctx }: { ctx: Ctx }) {
  const [closed, setClosed] = useState(false), [data, error, reload] = useData<Row>(`trading/portfolio/positions${closed ? '?include_closed=true' : ''}`, 10_000, [closed]);
  const [msg, setMsg] = useState(''), [err, setErr] = useState('');
  const flatten = (p: Row) => { const reason = window.prompt(`Close ${p.account_code} ${p.symbol} (${p.quantity}) at market? Reason:`); if (!reason) return;
    void api('trading/portfolio/flatten', 'POST', { account_id: p.account_id, instrument_id: p.instrument_id, reason, key: key() })
      .then(r => { setMsg(`Close orders: ${r.orders.map((o: Row) => o.state.toLowerCase()).join(', ')}.`); reload(); }).catch(e => setErr((e as Error).message)); };
  return <section className="intel">
    <div className="intel-bar"><label className="intel-check"><input type="checkbox" checked={closed} onChange={e => setClosed(e.target.checked)} /> Include closed</label></div>
    <Msg error={error || err} notice={msg} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Account</th><th>Instrument</th><th>Strategy</th><th>Mode</th><th>Quantity</th><th>Average</th><th>Mark</th><th>Unrealised</th><th>Realised</th><th>Value</th><th>Stop / target</th><th>Opened</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((p: Row) => <tr key={p.id}><td>{p.account_code}</td><td>{p.symbol}</td><td>{p.strategy_code ?? 'manual'}</td><td>{p.mode.toLowerCase()}</td>
        <td className={Number(p.quantity) > 0 ? 'up' : Number(p.quantity) < 0 ? 'down' : ''}><b>{p.quantity}</b></td><td>{p.avg_price ?? '–'}</td><td>{p.mark ?? '–'}</td>
        <td className={Number(p.unrealised_pnl) >= 0 ? 'up' : 'down'}>{money(p.unrealised_pnl, p.quote_currency)}</td><td>{money(p.realised_pnl, p.quote_currency)}</td><td>{money(p.notional_account, p.account_currency)}</td>
        <td><small>{p.stop_loss ?? '–'} / {p.take_profit ?? '–'}</small></td><td><small>{when(p.opened_at)}</small></td><td>{(ctx.canOps || ctx.canRisk) && Number(p.quantity) !== 0 && <button onClick={() => flatten(p)}>Flatten</button>}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={13} className="cfg-note">No open positions.</td></tr>}</tbody></table></div>
    <p className="cfg-note">Positions come only from fills (Orders). Each deployment keeps its own book; Flatten closes every book of that account in the instrument, also when a kill switch is on.</p></section>;
}

export function PortfolioView() {
  const sections: Section[] = [
    { id: 'accounts', label: 'Accounts', group: 'Overview', hint: 'Equity, P&L today and drawdown per account.', render: ctx => <Accounts ctx={ctx} /> },
    { id: 'positions', label: 'Positions', group: 'Work', hint: 'Every open position with its P&L at the live price; close one in a click.', render: ctx => <Positions ctx={ctx} /> },
    { id: 'allocation', label: 'Allocation', group: 'Work', hint: 'Capital partitions per account, deployment weights, and rebalancing.', render: ctx => <Allocation ctx={ctx} /> },
    { id: 'copy', label: 'Copy trading', group: 'Work', hint: 'Follower accounts that mirror a master account\'s fills, each with its own size and risk.', render: ctx => <CopyTrading ctx={ctx} /> },
    { id: 'attribution', label: 'Performance', group: 'Monitor', hint: 'P&L, win rate and profit factor per strategy, instrument and account.', render: () => <Performance /> },
    { id: 'contribution', label: 'Attribution', group: 'Monitor', hint: 'Where the result came from: by strategy, deployment, instrument, account, order source.', render: () => <Attribution /> },
    { id: 'exposure', label: 'Exposure', group: 'Monitor', hint: 'Net and gross value per account and instrument.', render: () => <Exposure /> },
    { id: 'broker', label: 'Broker positions', group: 'Monitor', hint: 'What the broker itself holds for a real account.', render: ctx => <BrokerBook ctx={ctx} /> },
    { id: 'capital', label: 'Paper capital', group: 'Configure', hint: 'Starting equity of accounts without their own.',
      render: ctx => <SettingsForm canEdit={ctx.canRisk} fields={['default_capital']} intro="Each account's own starting capital is set on its card (Overview › Accounts)." /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Average cost, realised and unrealised P&L, equity, drawdown, partitions, allocation, copy size and attribution.', render: () => <Formulas stage="Portfolio" /> },
    { id: 'graphs', label: 'Calculation graphs', group: 'Configure', hint: 'Allocation weights and portfolio measures as graphs.', render: ctx => <GraphWorkbench ctx={ctx} purposes={['ALLOCATION', 'MEASURE']} /> },
  ];
  return <StageShell view="Portfolio" sections={sections} />;
}

/* ---------- Stage 7 · Orders ---------- */

function OrderTable({ rows, open }: { rows: Row[]; open: (r: Row) => void }) {
  return <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Time</th><th>Source</th><th>Account</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Type</th><th>State</th><th>Filled</th><th>Average</th><th>Why</th></tr></thead><tbody>
    {rows.map(o => <tr key={o.id} className="clickable" onClick={() => open(o)}><td><small>{when(o.created_at)}</small></td><td>{o.strategy_code ?? o.source.toLowerCase()} <small className="cfg-note">{o.mode.toLowerCase()}</small></td>
      <td>{o.account_code}</td><td>{o.symbol}</td><td><b className={o.side === 'BUY' ? 'up' : 'down'}>{o.side}</b></td><td>{o.quantity}</td><td>{o.order_type.toLowerCase()}{o.limit_price ? ` ${o.limit_price}` : ''}</td>
      <td>{statePill(o.state)}</td><td>{o.filled_qty}</td><td>{o.avg_fill_price ?? '–'}</td><td><small>{(o.reasons ?? [])[0] ?? ''}</small></td></tr>)}
    {!rows.length && <tr><td colSpan={11} className="cfg-note">No orders.</td></tr>}</tbody></table></div>;
}

function OrderDetail({ id, close, ctx }: { id: string; close: () => void; ctx: Ctx }) {
  const [o, error, reload] = useData<Row>(`trading/orders/${id}`, 0, [id]);
  const [err, setErr] = useState(''), [res, setRes] = useState<Row | null>(null), [amend, setAmend] = useState<Row | null>(null), [amending,setAmending]=useState(false);
  if (!o) return <Msg error={error} />;
  const upstox = o.mode === 'BROKER' && o.adapter === 'UPSTOX';
  const working = ['ACKNOWLEDGED', 'PARTIALLY_FILLED'].includes(o.state);
  const sendAmend = async (e: FormEvent) => { e.preventDefault(); if(amending)return; setErr(''); setAmending(true);
    const body: Row = { reason: amend!.reason };
    for (const k of ['limit_price', 'stop_price', 'stop_loss', 'take_profit', 'quantity']) if (String(amend![k] ?? '').trim() !== '') body[k] = String(amend![k]).trim();
    try { await api(`trading/orders/${id}/modify`, 'POST', body); setAmend(null); reload(); } catch (x) { setErr((x as Error).message); } finally {setAmending(false);} };
  const cancel = () => { const reason = window.prompt('Reason for cancelling'); if (reason) void api(`trading/orders/${id}/cancel`, 'POST', { reason }).then(reload).catch(e => setErr((e as Error).message)); };
  return <FloatingWindow storageKey="corei.order.v1" title={`Order · ${o.symbol} ${o.side} ${o.quantity}`} subtitle={o.client_order_id} onClose={close}
    footer={<div className="cfg-actions">{(['ACKNOWLEDGED', 'PARTIALLY_FILLED'].includes(o.state) || (o.state === 'SUBMITTED' && o.mode === 'PAPER') || (o.state === 'APPROVED' && o.mode === 'BROKER')) && (ctx.canOps || ctx.canRisk) && <button onClick={cancel}>Cancel order</button>}
      {working && (!upstox || ['LIMIT','STOP'].includes(o.order_type)) && (ctx.canOps || ctx.canRisk) && !amend && <button onClick={() => setAmend({ limit_price: o.limit_price ?? '', stop_price: o.stop_price ?? '', stop_loss: upstox?'':o.stop_loss ?? '', take_profit: upstox?'':o.take_profit ?? '', quantity: '', reason: '' })}>Amend</button>}
      {o.hold_for_approval && o.state === 'APPROVED' && ctx.canRisk && <button className="primary" onClick={() => { const reason = window.prompt('Release this LIVE order to the broker? Reason:'); if (reason) void api(`trading/orders/${id}/release`, 'POST', { reason }).then(reload).catch(e => setErr((e as Error).message)); }}>Release to broker</button>}
      {o.mode === 'BROKER' && o.broker_tag && !['FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED', 'APPROVED'].includes(o.state) && (ctx.canOps || ctx.canRisk) && <button onClick={() => void api(`trading/orders/${id}/check-broker`, 'POST').then(reload).catch(e => setErr((e as Error).message))}>Look up at the broker</button>}
      {o.state === 'UNKNOWN' && ctx.canRisk && <button onClick={() => setRes({ outcome: 'FILLED', price: '', quantity: '', reason: '' })}>Resolve</button>}<button onClick={close}>Close</button></div>}>
    <div className="cfg-form">
      <p>{statePill(o.state)} · {o.mode === 'BROKER' ? 'real' : 'paper'} · {o.order_type.toLowerCase()}{o.limit_price ? ` ${o.limit_price}` : ''}{o.stop_price ? ` stop ${o.stop_price}` : ''} {o.time_in_force} · filled {o.filled_qty}{o.avg_fill_price ? ` at ${o.avg_fill_price}` : ''}
        {o.hold_for_approval && <> · {pill('waiting for a second person', 'warn')}</>}</p>
      {o.mode === 'BROKER' && <p className="cfg-note">Adapter {o.adapter ?? '–'} · broker tag <code>{o.broker_tag ?? '–'}</code>{o.broker_order_id ? <> · broker order {o.broker_order_id}</> : null}
        {o.submitted_at && <> · sent {when(o.submitted_at)}</>}{o.checks ? <> · {o.checks} look-ups, last {ago(o.last_check_at)}</> : null}
        {o.stop_loss || o.take_profit ? <> · SL {o.stop_loss ?? '–'} / TP {o.take_profit ?? '–'}</> : null}{o.released_by && <> · released by {o.released_by}</>}</p>}
      {upstox && <p className="cfg-note">Upstox V3 · {o.time_in_force === 'IOC' ? 'IOC' : 'DAY'} at the broker{o.order_type !== 'LIMIT' ? ' · automatic market protection' : ''}</p>}
      {o.routing && <p className="cfg-note">Routed ({o.routing.rule?.toLowerCase().replace('_', ' ')}): {o.routing.because}</p>}
      {o.reasons?.length > 0 && <><b>Refused because</b><Reasons list={o.reasons} /></>}
      <h4>What happened</h4>
      <ol className="st-timeline">{o.events.map((e: Row) => <li key={e.id}><b>{e.state_to.toLowerCase().replace(/_/g, ' ')}</b> <small className="cfg-note">{when(e.at)} · {e.actor}</small>
        {e.detail?.price && <small> · price {e.detail.price}{e.detail.slippage ? ` (slippage ${e.detail.slippage})` : ''}</small>}{e.detail?.reason && <small> · {e.detail.reason}</small>}{e.detail?.reasons && <small> · {e.detail.reasons.join('; ')}</small>}
        {e.detail?.modified && <small> · amended {Object.entries(e.detail.modified).map(([k, v]: [string, any]) => `${k.replace('_', ' ')} ${v[0] ?? '–'} → ${v[1] ?? '–'}`).join(', ')}</small>}
        {e.detail?.deal && <small> · deal {e.detail.deal}</small>}{e.detail?.found && <small> · {e.detail.found}</small>}{e.detail?.not_sent && <small> · not sent</small>}</li>)}</ol>
      {o.risk && <><h4>Risk check</h4><p className="cfg-note">{o.risk.approved ? 'Approved' : 'Refused'} · position {o.risk.inputs?.position_before} → {o.risk.inputs?.position_after}
        {o.risk.inputs?.limits && Object.keys(o.risk.inputs.limits).length ? ` · limits ${Object.entries(o.risk.inputs.limits).map(([k, v]) => `${k} ${v}`).join(', ')}` : ' · no limits set'}</p></>}
      {o.fills.length > 0 && <><h4>Fills</h4><table className="cfg-table compact"><tbody>{o.fills.map((f: Row) => <tr key={f.id}><td>{f.quantity} at {f.price}</td><td><small>bid {f.quote_bid ?? '–'} / ask {f.quote_ask ?? '–'}</small></td><td><small>realised {f.realised_pnl} {f.currency}</small></td><td><small>{f.broker_deal_id ? `deal ${f.broker_deal_id}` : ''}{f.latency_ms != null ? ` · ${f.latency_ms} ms` : ''}</small></td></tr>)}</tbody></table></>}
      {amend && <form className="intel-edit" onSubmit={sendAmend} aria-label="Amend order">
        <p className="cfg-note">{upstox?'Update the working limit or trigger price at Upstox. Corei records the change once Upstox accepts it.':'New prices pass the pre-trade checks. Broker amendments are recorded after acceptance.'}</p>
        <div className="cfg-pair">{o.order_type === 'LIMIT' && <Field label="Limit price"><input aria-label="Amend limit price" inputMode="decimal" value={amend.limit_price} onChange={e => setAmend({ ...amend, limit_price: e.target.value })} /></Field>}
          {o.order_type === 'STOP' && <Field label="Stop price"><input aria-label="Amend stop price" inputMode="decimal" value={amend.stop_price} onChange={e => setAmend({ ...amend, stop_price: e.target.value })} /></Field>}
          {o.mode === 'PAPER' && <Field label="Smaller quantity" hint={`Above ${o.filled_qty} filled, below ${o.quantity}`}><input aria-label="Amend quantity" inputMode="decimal" value={amend.quantity} onChange={e => setAmend({ ...amend, quantity: e.target.value })} /></Field>}</div>
        {!upstox&&<div className="cfg-pair"><Field label="Stop-loss (0 removes)"><input aria-label="Amend stop loss" inputMode="decimal" value={amend.stop_loss} onChange={e => setAmend({ ...amend, stop_loss: e.target.value })} /></Field>
          <Field label="Take-profit (0 removes)"><input aria-label="Amend take profit" inputMode="decimal" value={amend.take_profit} onChange={e => setAmend({ ...amend, take_profit: e.target.value })} /></Field></div>}
        <Field label="Reason (audit log)"><input aria-label="Amend reason" value={amend.reason} onChange={e => setAmend({ ...amend, reason: e.target.value })} /></Field>
        <div className="cfg-actions"><button className="primary" disabled={amending||amend.reason.trim().length < 5}>{amending?'Amending…':'Amend order'}</button><button type="button" disabled={amending} onClick={() => setAmend(null)}>Cancel</button></div></form>}
      {res && <form className="intel-edit" onSubmit={e => { e.preventDefault(); void api(`trading/orders/${id}/resolve`, 'POST', { outcome: res.outcome, reason: res.reason, price: res.price || null, quantity: res.quantity || null })
        .then(() => { setRes(null); reload(); }).catch(x => setErr((x as Error).message)); }}>
        <p className="cfg-note">Use "Look up at the broker" first; resolve by hand only when the broker shows nothing. COREI never sends an order with an unknown outcome again.</p>
        <Field label="Outcome"><Choice label="Resolve outcome" value={res.outcome} options={[['FILLED', 'Filled at the broker'], ['NOT_FILLED', 'Not filled']]} onChange={v => setRes({ ...res, outcome: v })} /></Field>
        {res.outcome === 'FILLED' && <div className="cfg-pair"><Field label="Price"><input aria-label="Resolve price" value={res.price} onChange={e => setRes({ ...res, price: e.target.value })} /></Field>
          <Field label="Quantity (blank = the rest)"><input aria-label="Resolve quantity" value={res.quantity} onChange={e => setRes({ ...res, quantity: e.target.value })} /></Field></div>}
        <Field label="Reason"><input aria-label="Resolve reason" required minLength={5} value={res.reason} onChange={e => setRes({ ...res, reason: e.target.value })} /></Field>
        <div className="cfg-actions"><button className="primary">Record</button></div></form>}
      <Msg error={err} />
    </div>
  </FloatingWindow>;
}

function Blotter({ ctx, openOnly = false }: { ctx: Ctx; openOnly?: boolean }) {
  const [state, setState] = useState(''), [data, error] = useData<Row>(`trading/orders?limit=500${state ? `&state=${state}` : ''}${openOnly ? '&open_only=true' : ''}`, 10_000, [state]);
  const [id, setId] = useState<string | null>(null);
  return <section className="intel">
    {!openOnly && <div className="intel-bar"><select aria-label="Order state" value={state} onChange={e => setState(e.target.value)}><option value="">All states</option>
      {['FILLED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'REJECTED', 'CANCELLED', 'EXPIRED', 'UNKNOWN'].map(s => <option key={s}>{s}</option>)}</select></div>}
    <Msg error={error} /><OrderTable rows={data?.items ?? []} open={o => setId(o.id)} />
    {id && <OrderDetail id={id} close={() => setId(null)} ctx={ctx} />}
  </section>;
}

function Ticket({ ctx }: { ctx: Ctx }) {
  const { accounts, instruments } = Lists();
  const blank = { mode: 'PAPER', account_id: '', route: 'NONE', route_accounts: [] as string[], instrument_id: '', side: 'BUY', quantity: '', order_type: 'MARKET', limit_price: '', stop_price: '',
    stop_loss: '', take_profit: '', time_in_force: 'GTC', reason: '' };
  const [f, setF] = useState<Row>(blank);
  const [k, setK] = useState(key()), [out, setOut] = useState<Row | null>(null), [err, setErr] = useState(''), [confirm, setConfirm] = useState(false);
  const acct = accounts.find((a: Row) => a.id === f.account_id);
  const real = f.mode === 'BROKER', live = real && (f.route === 'NONE' ? acct?.environment === 'LIVE' : accounts.some((a: Row) => f.route_accounts.includes(a.id) && a.environment === 'LIVE'));
  const send = async (e: FormEvent) => { e.preventDefault(); setErr('');
    if (real && !confirm) { setConfirm(true); return; }
    setConfirm(false);
    try { const o = await api('trading/orders', 'POST', { ...f, account_id: f.route === 'NONE' ? f.account_id : null, route_accounts: f.route === 'NONE' ? [] : f.route_accounts,
      limit_price: f.order_type === 'LIMIT' ? f.limit_price : null, stop_price: f.order_type === 'STOP' ? f.stop_price : null, stop_loss: f.stop_loss || null, take_profit: f.take_profit || null, ticket_key: k });
      setOut(o); setK(key()); } catch (x) { setErr((x as Error).message); } };
  if (!ctx.canOps) return <p className="cfg-note">Trading operations place orders.</p>;
  const set = (patch: Row) => { setF({ ...f, ...patch }); setConfirm(false); };
  return <section className="intel">
    <form className="cfg-form" onSubmit={send}>
      <Field label="Order"><Choice label="Ticket mode" value={f.mode} options={[['PAPER', 'Paper (COREI fills it)'], ['BROKER', 'Real (sent to the broker)']]} onChange={v => set({ mode: v })} /></Field>
      {real && <p className={live ? 'cfg-error' : 'cfg-note'}>{live ? 'LIVE account: real money. ' : 'Real order on a demo account. '}Sent through the account's broker connection after the risk check, only when order sending is on for its environment
        {live ? '; manual LIVE orders wait for a second person (four-eyes) unless switched off in Execution settings' : ''}.</p>}
      <div className="cfg-pair"><Field label="Where"><Choice label="Ticket routing" value={f.route} options={[['NONE', 'One account'], ['BEST_PRICE', 'Best price'], ['LOWEST_COST', 'Lowest cost'], ['PRIORITY', 'First that can'], ['SPLIT_EQUAL', 'Split equally']]} onChange={v => set({ route: v })} /></Field>
        {f.route === 'NONE' ? <Field label="Account"><select aria-label="Ticket account" required value={f.account_id} onChange={e => set({ account_id: e.target.value })}><option value="">Choose…</option>
          {accounts.map((a: Row) => <option key={a.id} value={a.id}>{a.code} · {a.environment}</option>)}</select></Field>
          : <Field label="Accounts it may use"><div className="intel-checks">{accounts.map((a: Row) => <label key={a.id} className="intel-check"><input type="checkbox" checked={f.route_accounts.includes(a.id)}
            onChange={e => set({ route_accounts: e.target.checked ? [...f.route_accounts, a.id] : f.route_accounts.filter((x: string) => x !== a.id) })} /> {a.code}</label>)}</div></Field>}
        <Field label="Instrument"><select aria-label="Ticket instrument" required value={f.instrument_id} onChange={e => set({ instrument_id: e.target.value })}><option value="">Choose…</option>
          {instruments.map((i: Row) => <option key={i.id} value={i.id}>{i.symbol} · {i.name}</option>)}</select></Field></div>
      <div className="cfg-pair"><Field label="Side"><Choice label="Ticket side" value={f.side} options={[['BUY', 'Buy'], ['SELL', 'Sell']]} onChange={v => set({ side: v })} /></Field>
        <Field label="Quantity (lots / contracts)"><input aria-label="Ticket quantity" required inputMode="decimal" value={f.quantity} onChange={e => set({ quantity: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Type"><Choice label="Ticket type" value={f.order_type} options={[['MARKET', 'Market'], ['LIMIT', 'Limit'], ['STOP', 'Stop']]} onChange={v => set({ order_type: v })} /></Field>
        {f.order_type === 'LIMIT' && <Field label="Limit price"><input aria-label="Ticket limit" required inputMode="decimal" value={f.limit_price} onChange={e => set({ limit_price: e.target.value })} /></Field>}
        {f.order_type === 'STOP' && <Field label="Stop price"><input aria-label="Ticket stop" required inputMode="decimal" value={f.stop_price} onChange={e => set({ stop_price: e.target.value })} /></Field>}
        <Field label="Time in force"><Choice label="Ticket TIF" value={f.time_in_force} options={[['GTC', 'Until cancelled'], ['DAY', 'Today (market close)'], ['IOC', 'Immediate or cancel']]} onChange={v => set({ time_in_force: v })} /></Field></div>
      {real && <div className="cfg-pair"><Field label="Stop-loss (at the broker)" hint="Optional; MT5 attaches it to the position"><input aria-label="Ticket stop loss" inputMode="decimal" value={f.stop_loss} onChange={e => set({ stop_loss: e.target.value })} /></Field>
        <Field label="Take-profit (at the broker)"><input aria-label="Ticket take profit" inputMode="decimal" value={f.take_profit} onChange={e => set({ take_profit: e.target.value })} /></Field></div>}
      <Field label="Reason (audit log)"><input aria-label="Ticket reason" value={f.reason} onChange={e => set({ reason: e.target.value })} /></Field>
      {confirm && <p className="cfg-error" role="alert">Send {f.side} {f.quantity} {instruments.find((i: Row) => i.id === f.instrument_id)?.symbol ?? ''} {f.order_type.toLowerCase()} to the broker{f.route === 'NONE' ? ` on ${acct?.code ?? ''}` : ` (${f.route.toLowerCase().replace('_', ' ')})`}? Press again to confirm.</p>}
      <div className="cfg-actions"><button className={real ? 'danger' : 'primary'}>{real ? (confirm ? 'Confirm: send the real order' : 'Place real order') : 'Place paper order'}</button></div>
    </form>
    <Msg error={err} />
    {out && <div className="intel-edit">{(out.legs ?? [out]).map((o: Row) => <p key={o.id}><b>{o.duplicate ? 'Already placed (same ticket): ' : ''}{o.side} {o.quantity}</b> → {statePill(o.state)}{o.avg_fill_price ? ` at ${o.avg_fill_price}` : ''}
      {o.hold_for_approval ? <> · {pill('waiting for a second person', 'warn')}</> : null}{o.routing ? <small className="cfg-note"> · {o.routing.because}</small> : null}</p>)}<Reasons list={out.reasons} /></div>}
  </section>;
}

function Fills() {
  const [data, error] = useData<Row>('trading/fills', 15_000);
  return <section className="intel"><Msg error={error} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Time</th><th>Source</th><th>Account</th><th>Instrument</th><th>Side</th><th>Quantity</th><th>Price</th><th>Bid / ask</th><th>Slippage</th><th>Latency</th><th>Realised</th></tr></thead><tbody>
      {(data?.items ?? []).map((f: Row) => <tr key={f.id}><td><small>{when(f.at)}</small></td><td>{f.strategy_code ?? 'manual'} <small className="cfg-note">{f.mode.toLowerCase()}</small></td><td>{f.account_code}</td><td>{f.symbol}</td><td>{f.side}</td>
        <td>{f.quantity}</td><td>{f.price}</td><td><small>{f.quote_bid ?? '–'} / {f.quote_ask ?? '–'}</small></td><td>{f.slippage ?? '–'}</td><td>{f.latency_ms != null ? `${f.latency_ms} ms` : '–'}</td><td className={Number(f.realised_pnl) >= 0 ? 'up' : 'down'}>{f.realised_pnl} {f.currency}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={11} className="cfg-note">No fills yet.</td></tr>}</tbody></table></section>;
}

function OrdersOverview({ ctx }: { ctx: Ctx }) {
  const [ov] = useData<Row>('trading/overview', 10_000);
  const o = ov?.orders_today ?? {};
  return <section className="intel">
    <Kpis items={[['Orders today', Object.values(o).reduce((a: number, b: any) => a + Number(b), 0)], ['Filled', o.FILLED ?? 0, undefined, 'up'], ['Working', ov?.open_orders ?? 0],
      ['Refused', o.REJECTED ?? 0, 'By risk: Risk › Decisions', o.REJECTED ? 'warn' : ''], ['Outcome unknown', ov?.unknown_orders ?? 0, 'Needs a check at the broker (Monitor › Unknown)', ov?.unknown_orders ? 'warn' : '']]} />
    <h4>Working orders</h4><Blotter ctx={ctx} openOnly />
    <p className="cfg-note">Every order: new → approved by risk (or refused with reasons) → submitted → acknowledged → filled. A repeat of the same decision or ticket is one order, never two.
      An order whose outcome is not known waits for a check at the broker; it is never sent again.</p>
  </section>;
}

function ExecutionSettings({ ctx }: { ctx: Ctx }) {
  return <section className="intel">
    <SettingsForm canEdit={ctx.canRisk} fields={['paper_slippage_ticks', 'pre_check_orders', 'live_four_eyes', 'broker_timeout_seconds', 'unknown_check_seconds', 'max_deviation_points',
      'broker_magic', 'upstox_product', 'realign_max_attempts', 'recon_interval_minutes', 'recon_qty_tolerance', 'recon_kill_on_break']} />
    <h4>Paper and real</h4>
    <p className="cfg-note">A deployment's mode is set in Setup › Deployments; a ticket chooses its own. <b>Paper</b>: COREI fills at the live bid/ask plus the slippage above. <b>Real (broker)</b>: demo or live
      accounts — MT5 through the COREI terminal bridge (master password), Upstox through its order API — only when Setup › Order sending is on for that environment. A real order is recorded as
      SUBMITTED before it leaves COREI; with no answer it becomes UNKNOWN and is looked up by its tag, never sent again.</p>
    <button onClick={() => goto('Configuration', 'controls')}>Order sending (Setup)</button>
  </section>;
}

function AlgoDesk({ ctx }: { ctx: Ctx }) {
  const { accounts, instruments } = Lists();
  return <section className="intel"><AlgoBlotter ctx={ctx} /><h4>Start an algorithm</h4><AlgoTicket ctx={ctx} accounts={accounts} instruments={instruments} /></section>;
}

function WithDetail({ ctx, body }: { ctx: Ctx; body: (open: (id: string) => void) => ReactNode }) {
  const [id, setId] = useState<string | null>(null);
  return <>{body(setId)}{id && <OrderDetail id={id} close={() => setId(null)} ctx={ctx} />}</>;
}

export function OrdersView() {
  const sections: Section[] = [
    { id: 'today', label: 'Dashboard', group: 'Overview', hint: 'Orders today, working orders and anything that needs a person.', render: ctx => <OrdersOverview ctx={ctx} /> },
    { id: 'flow', label: 'From strategies', group: 'Overview', hint: 'Every decision coming from the strategies: signal → decision → risk → order → fill, with what waits for you.', render: ctx => <DecisionFlow ctx={ctx} /> },
    { id: 'queue', label: 'Decision queue', group: 'Work', hint: 'Decisions waiting for a trader: execute (full or smaller, market or limit) or decline — one or all.', render: ctx => <DecisionQueue ctx={ctx} /> },
    { id: 'orders', label: 'Orders', group: 'Work', hint: 'Every order with its full history: open one to see each step, the risk check and the fills.', render: ctx => <Blotter ctx={ctx} /> },
    { id: 'algos', label: 'Algorithms', group: 'Work', hint: 'Work an order over time as child orders: TWAP, VWAP, POV or iceberg, with a price cap; progress, average against arrival in bp, pause, resume, cancel.', render: ctx => <AlgoDesk ctx={ctx} /> },
    { id: 'ticket', label: 'Order ticket', group: 'Work', hint: 'Place a paper or real order by hand, on one account or routed; same risk check.', render: ctx => <Ticket ctx={ctx} /> },
    { id: 'waiting', label: 'Waiting', group: 'Work', hint: 'Real orders waiting for a second person (four-eyes) or for the dispatcher.', render: ctx => <WithDetail ctx={ctx} body={open => <Waiting ctx={ctx} open={open} />} /> },
    { id: 'unknown', label: 'Unknown', group: 'Work', hint: 'Orders sent with no answer: looked up at the broker, never sent again.', render: ctx => <WithDetail ctx={ctx} body={open => <Unknown ctx={ctx} open={open} />} /> },
    { id: 'fills', label: 'Fills', group: 'Monitor', hint: 'Every fill with the price at the time, the slippage and the latency.', render: () => <Fills /> },
    { id: 'connections', label: 'Broker connections', group: 'Monitor', hint: 'The dispatcher, order sending, and which accounts can take real orders through what.', render: () => <Connections /> },
    { id: 'routing', label: 'Routing', group: 'Monitor', hint: 'Compare accounts for one order: price, spread, slippage, eligibility.', render: () => <Routing /> },
    { id: 'policy', label: 'Execution policy', group: 'Configure', hint: 'Per strategy: automatic, trader approves or signal only; market or limit, offset, time in force, approval window.', render: ctx => <ExecutionPolicies ctx={ctx} /> },
    { id: 'execution', label: 'Execution settings', group: 'Configure', hint: 'Paper fills, real orders (pre-check, four-eyes, timeouts, magic number), reconciliation.', render: ctx => <ExecutionSettings ctx={ctx} /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Client order ID, paper fill price, slippage, latency, routing scores and the UNKNOWN look-up.', render: () => <Formulas stage="Execution" /> },
    { id: 'graphs', label: 'Calculation graphs', group: 'Configure', hint: 'Execution measures as graphs (slippage, cost, fill quality) on stored bars.', render: ctx => <GraphWorkbench ctx={ctx} purposes={['MEASURE']} /> },
  ];
  return <StageShell view="Execution" sections={sections} />;
}

