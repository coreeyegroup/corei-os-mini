/* Stage 4 · Strategies: the strategy desk.
 * Why: Stage 4 combines Stage 3 signals into decisions. The desk must see every running strategy at once — what it
 * holds, what it last decided and how that decision reaches execution — and control it from the same row.
 * Purpose: StrategyDashboard (KPIs; one card per deployment: status, mode, handoff, timeframe and last bar, position and
 * P&L, live statistics, drift, the last signal and the last decision; pause / resume, change handoff, open its signals,
 * decisions or execution queue), BacktestCompare (up to four saved backtests side by side: metrics and equity curves).
 * Must never: change a deployment without its version and a reason (the backend audits every change). */
import { useEffect, useState, type ReactNode } from 'react';
import { api } from './Configuration';
import { Kpis, Msg, num, pill, useData, when, type Ctx, type Row } from './StageShell';
import { goto } from './nav';

const HAND: Record<string, string> = { AUTO: 'automatic', APPROVE: 'trader approves', SIGNAL_ONLY: 'signal only' };
const fmt = (v: any, d = 2) => v == null ? '–' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
const signed = (v: any) => <b className={Number(v) > 0 ? 'up' : Number(v) < 0 ? 'down' : ''}>{Number(v) > 0 ? '+' : ''}{fmt(v)}</b>;

export function StrategyDashboard({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/strategies/monitor', 15_000);
  const [q, setQ] = useState(''), [show, setShow] = useState('ACTIVE'), [msg, setMsg] = useState(''), [err, setErr] = useState('');
  const s = data?.summary ?? {};
  const rows: Row[] = (data?.items ?? []).filter((r: Row) => (show === 'ALL' || r.status === show) && (!q || `${r.strategy_code} ${r.symbol} ${r.account_code} ${r.algorithm}`.toLowerCase().includes(q.toLowerCase())));
  const transition = async (r: Row, action: 'pause' | 'activate') => {
    const reason = window.prompt(`${action === 'pause' ? 'Pause' : 'Resume'} ${r.strategy_code} on ${r.symbol}: reason (kept in the audit log)`) ?? '';
    if (reason.trim().length < 5) return;
    try { await api(`config/deployments/${r.id}/${action}`, 'POST', { version: r.version, reason }); setMsg(`${r.strategy_code} · ${r.symbol} ${action === 'pause' ? 'paused' : 'resumed'}.`); setErr(''); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  const handoff = async (r: Row, h: string) => {
    const reason = window.prompt(`${r.strategy_code} on ${r.symbol}: decisions will be "${HAND[h]}". Reason (kept in the audit log)`) ?? '';
    if (reason.trim().length < 5) return;
    try { await api(`trading/execution/policies/${r.id}`, 'PUT', { order_type: 'MARKET', limit_mode: 'MARKETABLE', limit_offset_ticks: 0, time_in_force: 'GTC', approval_minutes: 10,
      ...(r.execution ?? {}), handoff: h, version: r.version, reason }); setMsg(`${r.strategy_code} · ${r.symbol}: ${HAND[h]}.`); setErr(''); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  const pnl = Object.entries(s.pnl_by_currency ?? {}) as [string, Row][];
  return <section className="intel">
    <Kpis items={[['Running', s.active ?? 0, 'Active deployments evaluated on every closed bar', 'up'], ['Paused', s.paused ?? 0], ['Proposed', s.proposed ?? 0, 'Waiting for activation (Setup › Deployments)'],
      ['Paper · real', `${s.paper ?? 0} · ${s.real ?? 0}`], ['Long · short', `${s.long ?? 0} · ${s.short ?? 0}`, 'Running strategies holding a position'],
      ['Signals · orders today', `${s.signals_today ?? 0} · ${s.orders_today ?? 0}`], ['Waiting for execution', s.pending ?? 0, 'Decisions of "trader approves" strategies (Execution › Decision queue)', s.pending ? 'warn' : ''],
      ['Drifting', s.drifting ?? 0, 'Live results worse than the backtest', s.drifting ? 'warn' : ''], ['Need attention', s.errors ?? 0, 'Last run ended with a problem', s.errors ? 'warn' : ''],
      ...pnl.map(([c, v]) => [`Open · closed P&L ${c}`, <span className="sd-pnl">{signed(v.unrealised)} · {signed(v.realised)}</span>, 'Unrealised at the live price · realised from fills, in the account currency'] as [string, ReactNode, string])]} />
    <div className="intel-bar">
      <select aria-label="Strategy status" value={show} onChange={e => setShow(e.target.value)}><option value="ACTIVE">Running</option><option value="PAUSED">Paused</option><option value="PROPOSED">Proposed</option><option value="ALL">All</option></select>
      <input aria-label="Find a strategy" placeholder="Strategy, instrument, account, algorithm…" value={q} onChange={e => setQ(e.target.value)} />
      <button onClick={() => goto('Configuration', 'deployments')}>+ Deploy a strategy (Setup)</button>
      <button onClick={reload}>Reload</button>
    </div>
    <Msg error={error || err} notice={msg} />
    {data && !rows.length && <p className="cfg-note ex-empty">No {show === 'ALL' ? '' : show.toLowerCase() + ' '}strategies. Quick start (Home) deploys one in a click; Setup › Strategies and Deployments give every option.</p>}
    <div className="sd-cards">{rows.map(r => { const pos = r.position ?? {}, qty = Number(pos.quantity ?? 0), d = r.drift?.status, ls = r.last_signal, ld = r.last_decision;
      return <article key={r.id} className={`sd-card ${r.status !== 'ACTIVE' ? 'sd-off' : ''} ${r.last_error ? 'sd-err' : ''}`}>
        <header><b>{r.strategy_code}</b><small> v{r.version_number}</small>
          <span className="sd-tags">{pill(r.status === 'ACTIVE' ? 'running' : r.status, r.status === 'ACTIVE' ? 'ok' : 'off')}{r.mode === 'BROKER' ? pill('real', 'live') : pill('paper', 'demo')}
            <span className={`cfg-pill ${r.handoff === 'APPROVE' ? 'warn' : r.handoff === 'SIGNAL_ONLY' ? 'demo' : ''}`}>{HAND[r.handoff]}</span></span></header>
        <p className="sd-sub">{r.symbol} · {r.account_code} · <code>{r.algorithm}</code> · {r.timeframe ?? r.parameters?.timeframe ?? '—'}</p>
        <div className="sd-grid">
          <span><small>Position</small><b className={qty > 0 ? 'up' : qty < 0 ? 'down' : ''}>{qty > 0 ? 'long' : qty < 0 ? 'short' : 'flat'} {qty ? fmt(Math.abs(qty)) : ''}</b></span>
          <span><small>Open P&L</small>{signed(pos.unrealised ?? 0)}</span>
          <span><small>Closed P&L</small>{signed(pos.realised ?? 0)}</span>
          <span><small>Target</small><b>{r.last_target?.toLowerCase() ?? '—'}</b></span>
          <span><small>Trades · win</small><b>{r.live?.trades ?? 0} · {r.live?.win_rate_pct == null ? '–' : `${r.live.win_rate_pct}%`}</b></span>
          <span><small>Profit f.</small><b>{r.live?.profit_factor ?? '–'}</b></span>
          <span><small>Drift</small><b className={d === 'DRIFT' ? 'down' : d === 'OK' ? 'up' : ''}>{(d ?? 'not checked').toLowerCase().replace(/_/g, ' ')}</b></span>
          <span><small>Today</small><b>{r.signals_today} sig · {r.orders_today} ord</b></span>
        </div>
        <p className="sd-line"><small className="sd-k">Last signal</small>{ls ? <>{<b className={ls.side === 'BUY' ? 'up' : ls.side === 'SELL' ? 'down' : ''}>{ls.side}</b>} {ls.strength} · {ls.state.toLowerCase()} · {new Date(ls.bar_time).toISOString().slice(5, 16).replace('T', ' ')} <small className="cfg-note">{ls.why}</small></> : <small className="cfg-note">none yet</small>}</p>
        <p className="sd-line"><small className="sd-k">Last decision</small>{ld ? <>{ld.outcome.toLowerCase().replace(/_/g, ' ')}{ld.side ? ` · ${ld.side.toLowerCase()} ${fmt(ld.quantity)}` : ''} · {when(ld.created_at)}</> : <small className="cfg-note">none yet</small>}
          {r.pending > 0 && <button className="link" onClick={() => goto('Execution', 'queue')}> {r.pending} waiting →</button>}</p>
        {r.last_error && <p className="cfg-error-text"><small>{r.last_error}</small></p>}
        <footer>
          {(ctx.canOps || ctx.canRisk) && r.status === 'ACTIVE' && <button onClick={() => void transition(r, 'pause')}>Pause</button>}
          {ctx.canRisk && ['PAUSED', 'PROPOSED'].includes(r.status) && <button className="primary" onClick={() => void transition(r, 'activate')}>{r.status === 'PAUSED' ? 'Resume' : 'Activate'}</button>}
          {ctx.canRisk && <select aria-label={`Handoff ${r.strategy_code} ${r.symbol}`} value={r.handoff} onChange={e => void handoff(r, e.target.value)}>
            {Object.entries(HAND).map(([k, l]) => <option key={k} value={k}>→ execution: {l}</option>)}</select>}
          <button className="link" onClick={() => goto('Strategies', 'signals')}>signals</button>
          <button className="link" onClick={() => goto('Execution', 'flow')}>decisions →</button>
        </footer>
      </article>; })}</div>
  </section>;
}

export function BacktestCompare() {
  const [list] = useData<Row>('trading/backtests', 60_000);
  const [pick, setPick] = useState<string[]>([]), [runs, setRuns] = useState<Row[]>([]), [err, setErr] = useState('');
  useEffect(() => {
    if (!pick.length) { setRuns([]); return; }
    void Promise.all(pick.map(id => api(`trading/backtests/${id}`))).then(r => { setRuns(r); setErr(''); }).catch(e => setErr((e as Error).message));
  }, [pick.join()]);
  const items: Row[] = list?.items ?? [];
  const label = (b: Row) => `${b.strategy_code ? `${b.strategy_code} v${b.version_number}` : b.algorithm} · ${b.broker_symbol} ${b.timeframe} · ${new Date(b.created_at).toISOString().slice(0, 10)}`;
  const M: [string, string, (v: any) => string][] = [['Net P&L', 'net_pnl', v => fmt(v)], ['Return %', 'return_pct', v => fmt(v)], ['Trades', 'trades', v => String(v ?? '–')],
    ['Win rate %', 'win_rate_pct', v => v == null ? '–' : String(v)], ['Profit factor', 'profit_factor', v => v == null ? '–' : String(v)], ['Average trade', 'avg_trade', v => fmt(v)],
    ['Max drawdown %', 'max_drawdown_pct', v => fmt(v)], ['Sharpe', 'sharpe', v => v == null ? '–' : String(v)], ['Time in market %', 'exposure_pct', v => v == null ? '–' : String(v)], ['Bars', 'bars', v => String(v ?? '–')]];
  const best = (k: string) => { const vs = runs.map(r => Number(r.metrics?.[k])); if (vs.some(isNaN)) return -1; const want = k === 'max_drawdown_pct' ? Math.min(...vs) : Math.max(...vs); return vs.indexOf(want); };
  const colors = ['var(--accent)', 'var(--ready)', 'var(--warn, #c80)', 'var(--danger)'];
  return <section className="intel">
    <div className="intel-bar"><select aria-label="Add a backtest to compare" value="" onChange={e => e.target.value && pick.length < 4 && !pick.includes(e.target.value) && setPick([...pick, e.target.value])}>
      <option value="">{pick.length >= 4 ? 'Four chosen (remove one to add)' : 'Add a backtest…'}</option>{items.map(b => <option key={b.id} value={b.id}>{label(b)}</option>)}</select>
      {pick.length > 0 && <button onClick={() => setPick([])}>Clear</button>}
      <small className="cfg-note">Up to four saved backtests side by side — the best value of each row is marked.</small></div>
    <Msg error={err} />
    {!items.length && list && <p className="cfg-note">No saved backtests yet (Backtest panel above).</p>}
    {runs.length > 0 && <>
      <div className="cfg-scroll"><table className="cfg-table compact intel-table bt-cmp"><thead><tr><th>Metric</th>{runs.map((r, i) => <th key={r.id}><span className="bt-dot" style={{ background: colors[i] }} />{label({ ...r, ...(items.find(b => b.id === r.id) ?? {}) })}
        <button className="link" onClick={() => setPick(pick.filter(x => x !== r.id))}>remove</button><br /><small className="cfg-note">{r.algorithm} {JSON.stringify(r.parameters ?? {}).slice(0, 80)}</small></th>)}</tr></thead><tbody>
        {M.map(([l, k, f]) => { const b = runs.length > 1 ? best(k) : -1; return <tr key={k}><td>{l}</td>{runs.map((r, i) => <td key={r.id} className={i === b ? 'bt-best' : ''}>{f(r.metrics?.[k])}</td>)}</tr>; })}
      </tbody></table></div>
      <EquityOverlay runs={runs} colors={colors} />
    </>}
  </section>;
}

function EquityOverlay({ runs, colors }: { runs: Row[]; colors: string[] }) {
  const series: number[][] = runs.map(r => (r.equity ?? []).map((p: Row) => Number(p.equity)));
  const all = series.flat(); if (all.length < 2) return null;
  const lo = Math.min(...all), hi = Math.max(...all), W = 760, H = 180;
  return <svg className="sig-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Equity curves">
    {series.map((s, k) => s.length > 1 && <polyline key={k} fill="none" strokeWidth="1.6" stroke={colors[k]} points={s.map((v, i) => `${(i / (s.length - 1)) * W},${H - 6 - ((v - lo) / ((hi - lo) || 1)) * (H - 12)}`).join(' ')} />)}
    <text x="4" y="12" className="sig-label">equity, each run from its own start to its end</text></svg>;
}
