/* The controls added in 0.45 to Stages 4–7 and the whole of Stage 8, in the same StageShell frame.
 * Why: real execution (demo and live), post-trade and the advanced risk, strategy and portfolio controls need screens
 * that read the same way as the rest: what it is, the numbers, what needs a person, and the action with its reason.
 * Purpose: Risk › VaR and margin, Stress tests; Strategies › Optimise, Drift; Portfolio › Allocation, Copy trading,
 * Attribution, Broker book; Execution › Waiting for approval, Unknown, Connections, Routing; Post-trade (Stage 8):
 * dashboard, breaks, reconciliation runs, broker book, TCA, trade journal, statements, order audit trail, exports.
 * Must never: decide anything itself (the backend decides; these screens show and ask). */
import GraphWorkbench from './calc/GraphWorkbench';
import { useState, type FormEvent, type ReactNode } from 'react';
import { api, Choice, Field } from './Configuration';
import { Formulas } from './SignalLibrary';
import { useAccountView } from './nav';
import StageShell, { ago, Kpis, money, Msg, num, pill, Reasons, statePill, useData, when, type Ctx, type Row, type Section } from './StageShell';

export function Table({ cols, rows, empty, rowKey }: { cols: [string, (r: Row) => ReactNode][]; rows: Row[]; empty: string; rowKey?: (r: Row, i: number) => string }) {
  return <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr>{cols.map(([h]) => <th key={h}>{h}</th>)}</tr></thead><tbody>
    {rows.map((r, i) => <tr key={rowKey ? rowKey(r, i) : r.id ?? i}>{cols.map(([h, f]) => <td key={h}>{f(r)}</td>)}</tr>)}
    {!rows.length && <tr><td colSpan={cols.length} className="cfg-note">{empty}</td></tr>}</tbody></table></div>;
}
const signed = (v: any, ccy?: string) => <span className={Number(v) >= 0 ? 'up' : 'down'}>{money(v, ccy)}</span>;
const useAccounts = () => { const [a] = useData<Row>('config/accounts', 0); return (a?.items ?? []) as Row[]; };
function AccountPick({ value, change, label = 'Account', all = false }: { value: string; change: (v: string) => void; label?: string; all?: boolean }) {
  const accounts = useAccounts();
  return <select aria-label={label} value={value} onChange={e => change(e.target.value)}><option value="">{all ? 'All accounts' : 'Choose an account…'}</option>
    {accounts.map(a => <option key={a.id} value={a.id}>{a.code} · {a.environment}</option>)}</select>;
}
const days = (n: number) => <>{[1, 7, 30, 90, 365].map(d => <option key={d} value={d}>{d === 1 ? 'Today' : `Last ${d} days`}</option>)}{n ? null : null}</>;

/* ---------- Stage 5 · Risk: VaR, margin, stress ---------- */

export function VarMargin() {
  const [data, error] = useData<Row>('trading/risk/var', 60_000);
  return <section className="intel"><Msg error={error} />
    {(data?.items ?? []).map((v: Row) => <div key={v.account_id} className="st-card">
      <b>{v.account_code}</b> <small className="cfg-note">equity {money(v.equity, v.currency)}</small>
      {v.error ? <p className="cfg-error">VaR not available: {v.error}</p> : <Kpis items={[
        [`VaR ${v.confidence}% (1 ${v.timeframe} bar)`, money(v.var, v.currency), `Loss not exceeded on ${v.confidence}% of the ${v.bars} past bars`],
        ['VaR % of equity', `${v.var_pct ?? '–'}%`], ['Expected shortfall', money(v.es, v.currency), 'Average loss beyond the VaR'], ['Worst bar', money(v.worst, v.currency)],
        ['Margin in use', money(v.margin?.used, v.currency), `Position value ÷ leverage ${v.margin?.leverage}`],
        ['Broker margin', v.margin?.broker_margin != null ? money(v.margin.broker_margin, v.currency) : '–', 'From the latest broker book (real accounts)']]} />}
      {!v.error && <Table cols={[['Instrument', r => r.symbol], ['Position value', r => money(r.value, v.currency)], ['Average loss in the worst bars', r => money(r.tail_loss, v.currency)]]}
        rows={v.positions ?? []} empty="No positions." rowKey={r => r.instrument_id} />}
    </div>)}
    {data && !(data.items ?? []).length && <p className="cfg-note">No open positions: nothing at risk.</p>}
    <p className="cfg-note">Historical simulation: today's positions × each past bar's price change, on the bars every held instrument shares (Execution settings › VaR sets the confidence,
      timeframe and look-back). Use the MAX_VAR_PCT, MAX_MARGIN_PCT and MAX_LEVERAGE limits to refuse orders that would go past them.</p></section>;
}

export function Stress({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/risk/stress', 60_000);
  const [f, setF] = useState<Row | null>(null), [err, setErr] = useState('');
  const save = (e: FormEvent) => { e.preventDefault(); setErr('');
    void api(f!.id ? `trading/risk/scenarios/${f!.id}` : 'trading/risk/scenarios', f!.id ? 'PUT' : 'POST',
      { name: f!.name, description: f!.description, shocks: f!.shocks, reason: f!.reason }).then(() => { setF(null); reload(); }).catch(x => setErr((x as Error).message)); };
  const del = (s: Row) => { const reason = window.prompt(`Delete scenario ${s.name}? Reason:`); if (reason) void api(`trading/risk/scenarios/${s.id}?reason=${encodeURIComponent(reason)}`, 'DELETE').then(reload).catch(x => setErr((x as Error).message)); };
  return <section className="intel">
    {ctx.canRisk && <div className="intel-bar"><button className="primary" onClick={() => setF({ name: '', description: '', shocks: [{ match: 'ALL', value: '', pct: '-5' }], reason: '' })}>Add a scenario</button></div>}
    {f && <form className="intel-edit" onSubmit={save}>
      <div className="cfg-pair"><Field label="Name"><input aria-label="Scenario name" required value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></Field>
        <Field label="Description"><input aria-label="Scenario description" value={f.description} onChange={e => setF({ ...f, description: e.target.value })} /></Field></div>
      {f.shocks.map((s: Row, i: number) => <div key={i} className="cfg-pair">
        <Field label="Applies to"><Choice label={`Shock ${i + 1} match`} value={s.match} options={[['ALL', 'Everything'], ['ASSET_CLASS', 'Asset class'], ['SYMBOL', 'Symbol'], ['CURRENCY', 'Quote currency']]}
          onChange={v => setF({ ...f, shocks: f.shocks.map((x: Row, j: number) => j === i ? { ...x, match: v } : x) })} /></Field>
        {s.match !== 'ALL' && <Field label="Which"><input aria-label={`Shock ${i + 1} value`} value={s.value} placeholder="FX, METAL, XAUUSD, INR…" onChange={e => setF({ ...f, shocks: f.shocks.map((x: Row, j: number) => j === i ? { ...x, value: e.target.value } : x) })} /></Field>}
        <Field label="Price change %"><input aria-label={`Shock ${i + 1} pct`} value={s.pct} onChange={e => setF({ ...f, shocks: f.shocks.map((x: Row, j: number) => j === i ? { ...x, pct: e.target.value } : x) })} /></Field></div>)}
      <button type="button" onClick={() => setF({ ...f, shocks: [...f.shocks, { match: 'SYMBOL', value: '', pct: '-3' }] })}>Add a shock</button>
      <Field label="Reason (audit log)"><input aria-label="Scenario reason" required minLength={5} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Save</button><button type="button" onClick={() => setF(null)}>Cancel</button></div></form>}
    <Msg error={error || err} />
    <Table cols={[['Scenario', s => <><b>{s.name}</b><br /><small className="cfg-note">{s.description}</small></>],
      ['Shocks', s => <small>{s.shocks.map((x: Row) => `${x.match === 'ALL' ? 'all' : x.value} ${Number(x.pct) > 0 ? '+' : ''}${x.pct}%`).join(' · ')}</small>],
      ['P&L if it happened', s => signed(s.total)], ['By account', s => <small>{s.by_account.map((a: Row) => `${a.account} ${Number(a.pnl).toFixed(2)}`).join(' · ') || '–'}</small>],
      ['', s => ctx.canRisk && <>{<button onClick={() => setF({ ...s, reason: '' })}>Edit</button>}{!s.builtin && <button onClick={() => del(s)}>Delete</button>}</>]]}
      rows={data?.scenarios ?? []} empty="No scenarios." />
    {(data?.missing?.length ?? 0) > 0 && <p className="cfg-error">Not valued (no price or FX rate): {data!.missing.join('; ')}</p>}
    <p className="cfg-note">Each position takes the most specific shock that matches it (symbol, then asset class, then quote currency, then everything). Built-in scenarios can be edited but not deleted.</p></section>;
}

/* ---------- Stage 4 · Strategies: optimisation, drift ---------- */

export function Optimise({ ctx }: { ctx: Ctx }) {
  const [algos] = useData<Row>('trading/algorithms', 0), [sources] = useData<Row>('trading/sources', 0), [list, , reload] = useData<Row>('trading/optimisations', 0);
  const now = new Date(), from = new Date(now.getTime() - 30 * 864e5);
  const [f, setF] = useState<Row>({ algorithm: 'EMA_CROSSOVER', base: '{"timeframe": "H1", "fast": 9, "slow": 21}', grid: '{"fast": [5, 9, 13], "slow": [21, 34, 55]}', objective: 'SHARPE',
    source: '', from: from.toISOString().slice(0, 10), to: now.toISOString().slice(0, 10), folds: '4', in_sample_pct: '70', spread_ticks: '0', min_trades: '5' });
  const [out, setOut] = useState<Row | null>(null), [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const run = async (e: FormEvent) => { e.preventDefault(); setErr(''); setBusy(true);
    try { const [account_id, broker_symbol] = f.source.split('|');
      setOut(await api('trading/optimisations', 'POST', { algorithm: f.algorithm, base_parameters: JSON.parse(f.base), grid: JSON.parse(f.grid), objective: f.objective, account_id, broker_symbol,
        range_from: `${f.from}T00:00:00Z`, range_to: `${f.to}T23:59:59Z`, walk_forward_folds: Number(f.folds), in_sample_pct: Number(f.in_sample_pct), spread_ticks: Number(f.spread_ticks), min_trades: Number(f.min_trades) }));
      reload(); } catch (x) { setErr((x as Error).message); } finally { setBusy(false); } };
  return <section className="intel">
    {ctx.canOps || ctx.canRisk ? <form className="cfg-form" onSubmit={run}>
      <div className="cfg-pair"><Field label="Algorithm"><select aria-label="Optimise algorithm" value={f.algorithm} onChange={e => setF({ ...f, algorithm: e.target.value })}>{(algos?.items ?? []).map((a: Row) => <option key={a.id} value={a.id}>{a.label}</option>)}</select></Field>
        <Field label="History"><select aria-label="Optimise source" required value={f.source} onChange={e => setF({ ...f, source: e.target.value })}><option value="">Choose…</option>
          {(sources?.items ?? []).map((s: Row) => <option key={s.account_id + s.broker_symbol} value={`${s.account_id}|${s.broker_symbol}`}>{s.broker_symbol} · {s.account_code}</option>)}</select></Field></div>
      <div className="cfg-pair"><Field label="Fixed parameters (JSON)" hint="Timeframe and anything not swept"><input aria-label="Optimise base" value={f.base} onChange={e => setF({ ...f, base: e.target.value })} /></Field>
        <Field label="Values to try (JSON)" hint="Each parameter with its list; at most 400 combinations"><input aria-label="Optimise grid" value={f.grid} onChange={e => setF({ ...f, grid: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Best by"><Choice label="Optimise objective" value={f.objective} options={[['SHARPE', 'Sharpe ratio'], ['NET_PNL', 'Net P&L'], ['PROFIT_FACTOR', 'Profit factor'], ['RETURN_OVER_DD', 'Return ÷ drawdown']]} onChange={v => setF({ ...f, objective: v })} /></Field>
        <Field label="From"><input aria-label="Optimise from" type="date" value={f.from} onChange={e => setF({ ...f, from: e.target.value })} /></Field>
        <Field label="To"><input aria-label="Optimise to" type="date" value={f.to} onChange={e => setF({ ...f, to: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Walk-forward folds" hint="0 = one period only"><input aria-label="Optimise folds" value={f.folds} onChange={e => setF({ ...f, folds: e.target.value })} /></Field>
        <Field label="In-sample %"><input aria-label="Optimise in-sample" value={f.in_sample_pct} onChange={e => setF({ ...f, in_sample_pct: e.target.value })} /></Field>
        <Field label="Spread (ticks)"><input aria-label="Optimise spread" value={f.spread_ticks} onChange={e => setF({ ...f, spread_ticks: e.target.value })} /></Field>
        <Field label="Minimum trades"><input aria-label="Optimise min trades" value={f.min_trades} onChange={e => setF({ ...f, min_trades: e.target.value })} /></Field></div>
      <div className="cfg-actions"><button className="primary" disabled={busy}>{busy ? 'Running…' : 'Run the optimisation'}</button></div></form>
      : <p className="cfg-note">Trading operations and risk approvers run optimisations.</p>}
    <Msg error={err} />
    {out && <>
      {out.robustness && <Kpis items={[['Verdict', out.robustness.verdict, 'ROBUST: out-of-sample keeps at least half of in-sample', out.robustness.verdict === 'ROBUST' ? 'up' : 'warn'],
        ['Efficiency', `${out.robustness.efficiency_pct ?? '–'}%`, 'Out-of-sample ÷ in-sample score'], ['Profitable folds', `${out.robustness.oos_profitable_folds}/${out.robustness.folds}`],
        ['Out-of-sample P&L', out.robustness.oos_net_pnl], ['Most chosen', JSON.stringify(out.robustness.most_chosen), `${out.robustness.stability_pct}% of folds`]]} />}
      {out.folds?.length > 0 && <><h4>Walk-forward</h4><Table cols={[['Fold', r => r.fold], ['Chosen on', r => <small>{r.in_sample?.map(when).join(' → ')}</small>], ['Parameters', r => <code>{JSON.stringify(r.parameters ?? {})}</code>],
        ['In-sample', r => r.in_sample_score?.toFixed?.(3) ?? r.note], ['Out-of-sample', r => r.out_of_sample_score?.toFixed?.(3) ?? '–'], ['OOS P&L', r => r.out_of_sample?.net_pnl ?? '–'], ['OOS trades', r => r.out_of_sample?.trades ?? '–']]}
        rows={out.folds} empty="" rowKey={r => String(r.fold)} /></>}
      <h4>Every combination ({out.combinations}, {out.bars} bars)</h4>
      <Table cols={[['Parameters', r => <code>{JSON.stringify(r.parameters)}</code>], ['Score', r => r.score?.toFixed?.(3) ?? 'too few trades'], ['Net P&L', r => r.metrics.net_pnl], ['Trades', r => r.metrics.trades],
        ['Win %', r => r.metrics.win_rate_pct ?? '–'], ['PF', r => r.metrics.profit_factor ?? '–'], ['Max DD %', r => r.metrics.max_drawdown_pct], ['Sharpe', r => r.metrics.sharpe ?? '–']]}
        rows={out.results.slice(0, 100)} empty="" rowKey={r => JSON.stringify(r.parameters)} />
      <p className="cfg-note">Results are advice: a person makes a new strategy version with the chosen parameters (Setup › Strategies), which is then approved and backtested as usual.</p></>}
    <h4>Earlier runs</h4>
    <Table cols={[['When', r => <small>{when(r.created_at)}</small>], ['Algorithm', r => r.algorithm], ['History', r => `${r.broker_symbol} ${r.timeframe} · ${r.account_code}`], ['Combinations', r => r.combinations],
      ['Best', r => <code>{JSON.stringify(r.best?.parameters ?? {})}</code>], ['Walk-forward', r => r.robustness?.verdict ?? '—'], ['By', r => r.created_by]]} rows={list?.items ?? []} empty="No optimisation yet." />
  </section>;
}

export function Drift() {
  const [data, error] = useData<Row>('trading/drift', 60_000);
  const cls: Record<string, string> = { DRIFT: 'live', OK: 'ok', NOT_ENOUGH_TRADES: 'off', NO_BACKTEST: 'warn', NOT_CHECKED: 'off' };
  return <section className="intel"><Msg error={error} />
    <Table cols={[['Deployment', r => `${r.strategy} v${r.version} · ${r.symbol} · ${r.account_code}`], ['Mode', r => r.mode?.toLowerCase()], ['State', r => pill(r.drift.status, cls[r.drift.status])],
      ['Live', r => r.drift.live ? `${r.drift.live.trades} trades · win ${r.drift.live.win_rate_pct ?? '–'}% · PF ${r.drift.live.profit_factor ?? '–'} · avg ${r.drift.live.avg_trade ?? '–'}` : '–'],
      ['Backtest', r => r.drift.backtest ? `win ${r.drift.backtest.win_rate_pct ?? '–'}% · PF ${r.drift.backtest.profit_factor ?? '–'} · avg ${r.drift.backtest.avg_trade ?? '–'}` : '–'],
      ['Why', r => <small>{(r.drift.reasons ?? []).join('; ')}</small>]]} rows={data?.items ?? []} empty="No active deployment." rowKey={r => r.deployment_id} />
    <p className="cfg-note">Checked every trading cycle against the latest backtest of the same version. DRIFT raises an alert once; stopping a strategy is a person's decision (Risk › Kill switches).
      Minimum trades and tolerance: Execution settings.</p></section>;
}

/* ---------- Stage 6 · Portfolio: attribution, allocation, copy trading, broker book ---------- */

export function Attribution() {
  const [by, setBy] = useState('strategy'), [d, setD] = useState(30), [data, error] = useData<Row>(`trading/portfolio/attribution?by=${by}&days=${d}`, 60_000, [by, d]);
  return <section className="intel">
    <div className="intel-bar"><Choice label="Attribution by" value={by} options={[['strategy', 'Strategy'], ['deployment', 'Deployment'], ['instrument', 'Instrument'], ['account', 'Account'], ['source', 'Order source'], ['mode', 'Paper / broker']]} onChange={setBy} />
      <select aria-label="Attribution period" value={d} onChange={e => setD(Number(e.target.value))}>{days(d)}</select>
      <a className="link" href={`/api/trading/posttrade/export/attribution.csv?by=${by}&days=${d}`}>CSV</a></div>
    <Msg error={error} />
    {data?.rows && <Kpis items={[['Realised P&L', signed(data.total, data.currency)], ['Groups', data.rows.length]]} />}
    <Table cols={[['', r => r.label], ['Realised', r => signed(r.realised, data?.currency)], ['Share of the result', r => <div className="st-bar"><span style={{ width: `${Math.min(100, Math.abs(Number(r.share_pct)))}%` }} className={Number(r.realised) >= 0 ? 'up' : 'down'} /> {r.share_pct}%</div>],
      ['Fills', r => r.fills], ['Win rate', r => r.win_rate_pct != null ? `${r.win_rate_pct}%` : '–']]} rows={data?.rows ?? []} empty="No fills in this period." rowKey={r => String(r.label)} />
    {(data?.missing_fx?.length ?? 0) > 0 && <p className="cfg-error">No FX rate for {data!.missing_fx.join(', ')}: those fills are counted but not converted.</p>}</section>;
}

export function Allocation({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/portfolio/partitions', 30_000), [deps] = useData<Row>('config/deployments', 0);
  const [f, setF] = useState<Row | null>(null), [alloc, setAlloc] = useState<Row>({ account_id: '', method: 'WEIGHT', total: '' }), [plan, setPlan] = useState<Row | null>(null);
  const [allocating, setAllocating] = useState(false);
  const [assign, setAssign] = useState<Row | null>(null), [err, setErr] = useState(''), [msg, setMsg] = useState('');
  const fail = (x: unknown) => setErr((x as Error).message);
  const save = (e: FormEvent) => { e.preventDefault(); setErr('');
    void api(f!.id ? `trading/portfolio/partitions/${f!.id}?version=${f!.version}` : 'trading/portfolio/partitions', f!.id ? 'PUT' : 'POST',
      { account_id: f!.account_id, name: f!.name, capital: f!.capital, weight: f!.weight || '1', max_drawdown_pct: f!.max_drawdown_pct || null, status: f!.status, note: f!.note ?? '', reason: f!.reason })
      .then(() => { setF(null); reload(); }).catch(fail); };
  const changeAllocation = (patch: Row) => {
    setAlloc({ ...alloc, ...patch });
    setPlan(null);
  };
  const preview = async (apply: boolean) => {
    if (allocating || (apply && (!plan || plan.applied))) return;
    const reason = apply ? window.prompt('Reason for the new allocation (audit log)') : '';
    if (apply && (!reason || reason.trim().length < 5)) return;
    setAllocating(true); setErr(''); setPlan(null);
    try {
      setPlan(await api('trading/portfolio/allocate', 'POST', { ...alloc, apply, reason }));
      if (apply) { setMsg('Allocation applied.'); reload(); }
    } catch (error) { fail(error); }
    finally { setAllocating(false); }
  };
  const accountParts = (aid: string) => (data?.items ?? []).filter((p: Row) => p.account_id === aid);
  return <section className="intel">
    {ctx.canRisk && <div className="intel-bar"><button className="primary" onClick={() => setF({ account_id: '', name: '', capital: '', weight: '1', max_drawdown_pct: '', status: 'ACTIVE', reason: '' })}>Add a partition</button></div>}
    {f && <form className="intel-edit" onSubmit={save}>
      <div className="cfg-pair"><Field label="Account"><AccountPick value={f.account_id} change={v => setF({ ...f, account_id: v })} label="Partition account" /></Field>
        <Field label="Name"><input aria-label="Partition name" required value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></Field></div>
      <div className="cfg-pair"><Field label="Capital (account currency)"><input aria-label="Partition capital" required value={f.capital} onChange={e => setF({ ...f, capital: e.target.value })} /></Field>
        <Field label="Weight" hint="Used by allocation by weight"><input aria-label="Partition weight" value={f.weight} onChange={e => setF({ ...f, weight: e.target.value })} /></Field>
        <Field label="Own drawdown limit %" hint="Blank: none. Past it, new entries are refused"><input aria-label="Partition drawdown" value={f.max_drawdown_pct ?? ''} onChange={e => setF({ ...f, max_drawdown_pct: e.target.value })} /></Field>
        <Field label="State"><Choice label="Partition status" value={f.status} options={[['ACTIVE', 'Active'], ['PAUSED', 'Paused (no new entries)']]} onChange={v => setF({ ...f, status: v })} /></Field></div>
      <Field label="Reason (audit log)"><input aria-label="Partition reason" required minLength={5} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Save</button><button type="button" onClick={() => setF(null)}>Cancel</button></div></form>}
    <Msg error={error || err} notice={msg} />
    <Table cols={[['Account', r => r.account_code], ['Partition', r => <><b>{r.name}</b> {r.status === 'PAUSED' && pill('paused', 'warn')}</>], ['Capital', r => money(r.capital)], ['Equity', r => money(r.equity)],
      ['Drawdown', r => `${r.drawdown_pct ?? 0}%${r.max_drawdown_pct ? ` / ${r.max_drawdown_pct}%` : ''}`], ['Weight', r => r.weight],
      ['Deployments', r => <small>{r.deployments.map((d: Row) => `${d.strategy} ${d.symbol} ×${Number(d.weight)}`).join(' · ') || '–'}</small>],
      ['', r => ctx.canRisk && <><button onClick={() => setF({ ...r, reason: '' })}>Edit</button><button onClick={() => { const reason = window.prompt(`Delete ${r.name}? Reason:`); if (reason) void api(`trading/portfolio/partitions/${r.id}?reason=${encodeURIComponent(reason)}`, 'DELETE').then(reload).catch(fail); }}>Delete</button></>]]}
      rows={data?.items ?? []} empty="No partitions: deployments size from the whole account." />
    {ctx.canRisk && <>
      <h4>Assign a deployment</h4>
      <div className="intel-bar"><select aria-label="Assign deployment" value={assign?.id ?? ''} onChange={e => { const d = (deps?.items ?? []).find((x: Row) => x.id === e.target.value); setAssign(d ? { id: d.id, account_id: d.account_id, partition_id: d.partition_id ?? '', weight: d.weight ?? '1', reason: '' } : null); }}>
        <option value="">Choose a deployment…</option>{(deps?.items ?? []).map((d: Row) => <option key={d.id} value={d.id}>{d.strategy_code} v{d.version_number} · {d.instrument_symbol} · {d.account_code}</option>)}</select>
        {assign && <><select aria-label="Assign partition" value={assign.partition_id} onChange={e => setAssign({ ...assign, partition_id: e.target.value })}><option value="">Whole account</option>
          {accountParts(assign.account_id).map((p: Row) => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
          <input aria-label="Assign weight" title="Size × weight; 0 stops new entries" value={assign.weight} onChange={e => setAssign({ ...assign, weight: e.target.value })} style={{ width: '5em' }} />
          <input aria-label="Assign reason" placeholder="Reason" value={assign.reason} onChange={e => setAssign({ ...assign, reason: e.target.value })} />
          <button disabled={assign.reason.trim().length < 5} onClick={() => void api(`trading/portfolio/deployments/${assign.id}/allocation`, 'PUT', { partition_id: assign.partition_id || null, weight: assign.weight, reason: assign.reason })
            .then(() => { setAssign(null); setMsg('Deployment assigned.'); reload(); }).catch(fail)}>Save</button></>}</div>
      <h4>Rebalance capital</h4>
      <fieldset className="intel-bar" disabled={allocating}><AccountPick value={alloc.account_id} change={v => changeAllocation({ account_id: v })} label="Allocate account" />
        <Choice label="Allocation method" value={alloc.method} options={[['EQUAL', 'Equal shares'], ['WEIGHT', 'By weight'], ['RISK_PARITY', 'Risk parity (inverse volatility)']]} onChange={v => changeAllocation({ method: v })} />
        <input aria-label="Allocation total" placeholder="Total capital" value={alloc.total} onChange={e => changeAllocation({ total: e.target.value })} />
        <button disabled={!alloc.account_id || !alloc.total} onClick={() => void preview(false)}>Preview</button>
        <button className="primary" disabled={!plan || plan.applied} onClick={() => void preview(true)}>Apply</button></fieldset>
      <p className="cfg-note">Changing the account, method or capital clears the preview. Apply recalculates from current partition data.</p>
      {plan && <Table cols={[['Partition', r => r.name], ['Now', r => money(r.before)], ['After', r => <b>{money(r.after)}</b>], ['Share', r => `${r.share_pct}%`]]} rows={plan.plan} empty="" rowKey={r => r.partition_id} />}
    </>}
    <p className="cfg-note">Partitions are COREI's own book-keeping: they size orders and carry their own drawdown limit; no money moves at the broker.</p></section>;
}

export function CopyTrading({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/portfolio/copy-links', 30_000);
  const [f, setF] = useState<Row | null>(null), [err, setErr] = useState('');
  const save = (e: FormEvent) => { e.preventDefault(); setErr('');
    const body = { master_account_id: f!.master_account_id, follower_account_id: f!.follower_account_id, method: f!.method, ratio: f!.ratio || '1', fixed_qty: f!.fixed_qty || null,
      mode: f!.mode, reverse: !!f!.reverse, max_qty: f!.max_qty || null, symbols: String(f!.symbols ?? '').split(',').map((s: string) => s.trim()).filter(Boolean), enabled: f!.enabled, reason: f!.reason };
    void api(f!.id ? `trading/portfolio/copy-links/${f!.id}?version=${f!.version}` : 'trading/portfolio/copy-links', f!.id ? 'PUT' : 'POST', body).then(() => { setF(null); reload(); }).catch(x => setErr((x as Error).message)); };
  return <section className="intel">
    {ctx.canRisk && <div className="intel-bar"><button className="primary" onClick={() => setF({ master_account_id: '', follower_account_id: '', method: 'FIXED_RATIO', ratio: '1', mode: 'PAPER', reverse: false, max_qty: '', symbols: '', enabled: true, reason: '' })}>Add a follower</button></div>}
    {f && <form className="intel-edit" onSubmit={save}>
      <div className="cfg-pair"><Field label="Master (copied)"><AccountPick value={f.master_account_id} change={v => setF({ ...f, master_account_id: v })} label="Copy master" /></Field>
        <Field label="Follower"><AccountPick value={f.follower_account_id} change={v => setF({ ...f, follower_account_id: v })} label="Copy follower" /></Field></div>
      <div className="cfg-pair"><Field label="Size"><Choice label="Copy method" value={f.method} options={[['FIXED_RATIO', 'Master × ratio'], ['EQUITY_RATIO', 'By equity × ratio'], ['FIXED_QTY', 'Fixed quantity']]} onChange={v => setF({ ...f, method: v })} /></Field>
        {f.method === 'FIXED_QTY' ? <Field label="Quantity"><input aria-label="Copy quantity" value={f.fixed_qty ?? ''} onChange={e => setF({ ...f, fixed_qty: e.target.value })} /></Field>
          : <Field label="Ratio"><input aria-label="Copy ratio" value={f.ratio} onChange={e => setF({ ...f, ratio: e.target.value })} /></Field>}
        <Field label="Largest copy"><input aria-label="Copy max" value={f.max_qty ?? ''} onChange={e => setF({ ...f, max_qty: e.target.value })} /></Field>
        <Field label="Follower orders"><Choice label="Copy mode" value={f.mode} options={[['PAPER', 'Paper'], ['BROKER', 'Real (broker)']]} onChange={v => setF({ ...f, mode: v })} /></Field></div>
      <div className="cfg-pair"><Field label="Only these symbols" hint="Comma-separated; blank = all"><input aria-label="Copy symbols" value={Array.isArray(f.symbols) ? f.symbols.join(', ') : f.symbols} onChange={e => setF({ ...f, symbols: e.target.value })} /></Field>
        <label className="intel-check"><input type="checkbox" checked={!!f.reverse} onChange={e => setF({ ...f, reverse: e.target.checked })} /> Reverse (opposite side)</label>
        <label className="intel-check"><input type="checkbox" checked={!!f.enabled} onChange={e => setF({ ...f, enabled: e.target.checked })} /> On</label></div>
      <Field label="Reason (audit log)"><input aria-label="Copy reason" required minLength={5} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Save</button><button type="button" onClick={() => setF(null)}>Cancel</button></div></form>}
    <Msg error={error || err} />
    <Table cols={[['Master → follower', r => <b>{r.master_code} → {r.follower_code}</b>], ['Size', r => r.method === 'FIXED_QTY' ? `fixed ${r.fixed_qty}` : `${r.method === 'EQUITY_RATIO' ? 'equity × ' : '× '}${r.ratio}`],
      ['Mode', r => r.mode.toLowerCase()], ['Options', r => <small>{[r.reverse && 'reverse', r.max_qty && `max ${r.max_qty}`, r.symbols?.length && r.symbols.join(',')].filter(Boolean).join(' · ') || '–'}</small>],
      ['State', r => r.enabled ? pill('on', 'ok') : pill('off', 'off')], ['Copies', r => r.copies],
      ['', r => ctx.canRisk && <><button onClick={() => setF({ ...r, symbols: (r.symbols ?? []).join(', '), reason: '' })}>Edit</button><button onClick={() => { const reason = window.prompt('Remove this link? Reason:'); if (reason) void api(`trading/portfolio/copy-links/${r.id}?reason=${encodeURIComponent(reason)}`, 'DELETE').then(reload).catch(x => setErr((x as Error).message)); }}>Remove</button></>]]}
      rows={data?.items ?? []} empty="No copy links." />
    <p className="cfg-note">Each master fill becomes one follower order in the same moment, through the follower's own risk check, kill switches and order sending. Copies are never copied again; two accounts cannot copy each other.</p></section>;
}

export function BrokerBook({ ctx }: { ctx: Ctx }) {
  const [account, setAccount] = useAccountView('broker'), [data, error, reload] = useData<Row>(account ? `trading/posttrade/broker/${account}` : null, 30_000, [account]);
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const snap = data?.snapshot;
  return <section className="intel">
    <div className="intel-bar"><AccountPick value={account} change={setAccount} label="Broker book account" />
      {account && (ctx.canOps || ctx.canRisk) && <button disabled={busy} onClick={() => { setBusy(true); setErr(''); void api(`trading/posttrade/broker/${account}/read`, 'POST').then(reload).catch(x => setErr((x as Error).message)).finally(() => setBusy(false)); }}>{busy ? 'Reading…' : 'Read from the broker now'}</button>}</div>
    <Msg error={error || err} />
    {account && !data && !error && <p role="status">Loading broker snapshot…</p>}
    {data && !snap && <p className="cfg-note">No broker snapshot has been recorded for this account.</p>}
    {snap && !snap.ok && <p role="alert" className="cfg-error">Broker read failed {ago(snap.at)} · {snap.source} · {snap.error}</p>}
    {snap?.ok && <>
      <p className="cfg-note">Snapshot {when(snap.at)} · {snap.source}</p>
      {snap.ok && <Kpis items={Object.entries(snap.money ?? {}).filter(([, v]) => v !== '').map(([k, v]) => [k.replace(/_/g, ' '), num(v)] as [string, string])} />}
      <h4>Positions at the broker</h4>
      <Table cols={[['Ticket', r => r.ticket], ['Symbol', r => r.symbol], ['Side', r => r.side], ['Volume', r => r.volume], ['Open', r => r.price_open], ['Now', r => r.price_current ?? '–'], ['Profit', r => signed(r.profit)],
        ['Placed by', r => r.magic == null ? '–' : r.magic === 7770001 || r.magic === Number(r.magic) && r.comment?.startsWith?.('C') ? 'COREI' : `other (${r.magic})`]]} rows={snap.positions ?? []} empty="No positions at the broker." rowKey={r => String(r.ticket)} />
      {(snap.orders ?? []).length > 0 && <><h4>Working orders at the broker</h4><Table cols={[['Ticket', r => r.ticket], ['Symbol', r => r.symbol], ['Type', r => r.type], ['Volume', r => r.volume_current], ['Price', r => r.price_open], ['Tag', r => r.comment]]} rows={snap.orders} empty="" rowKey={r => String(r.ticket)} /></>}
    </>}
    {data?.deals && <><h4>Deals</h4><Table cols={[['Time (broker)', r => <small>{when(r.at)}</small>], ['Ticket', r => r.ticket], ['Symbol', r => r.symbol], ['Side', r => r.side], ['Entry', r => r.entry], ['Volume', r => r.volume], ['Price', r => r.price],
      ['Commission', r => r.commission], ['Profit', r => signed(r.profit)], ['COREI order', r => r.client_order_id ? <code>{r.client_order_id.slice(0, 12)}…</code> : pill('outside COREI', 'warn')]]} rows={data.deals ?? []} empty="No deals stored yet." rowKey={r => String(r.ticket)} /></>}
    {!account && <p className="cfg-note">Choose an account that takes real orders (MT5 through a bridge, or Upstox).</p>}</section>;
}

/* ---------- Stage 7 · Execution: waiting, unknown, connections, routing ---------- */

export function Waiting({ ctx, open }: { ctx: Ctx; open: (id: string) => void }) {
  const [data, error, reload] = useData<Row>('trading/orders?state=APPROVED&limit=200', 10_000);
  const [err, setErr] = useState('');
  const held = (data?.items ?? []).filter((o: Row) => o.mode === 'BROKER');
  const release = (o: Row) => { const reason = window.prompt(`Send ${o.side} ${o.quantity} ${o.symbol} on ${o.account_code} to the broker? Reason:`); if (reason) void api(`trading/orders/${o.id}/release`, 'POST', { reason }).then(reload).catch(x => setErr((x as Error).message)); };
  return <section className="intel"><Msg error={error || err} />
    <Table cols={[['Placed', o => <small>{when(o.created_at)} · {o.created_by}</small>], ['Account', o => o.account_code], ['Order', o => <b className={o.side === 'BUY' ? 'up' : 'down'}>{o.side} {o.quantity} {o.symbol}</b>],
      ['Type', o => `${o.order_type.toLowerCase()}${o.limit_price ? ' ' + o.limit_price : ''}${o.stop_price ? ' stop ' + o.stop_price : ''}`], ['Waiting for', o => o.hold_for_approval ? pill('second person', 'warn') : pill('dispatcher', 'demo')],
      ['', o => <>{o.hold_for_approval && ctx.canRisk && <button className="primary" onClick={() => release(o)}>Release to broker</button>}<button onClick={() => open(o.id)}>Open</button></>]]}
      rows={held} empty="Nothing is waiting." />
    <p className="cfg-note">Four-eyes on LIVE accounts (Execution settings): an order waits here until a Risk approver who did not place it releases it. Cancelling it here is safe — nothing has reached the broker.</p></section>;
}

export function Unknown({ ctx, open }: { ctx: Ctx; open: (id: string) => void }) {
  const [data, error, reload] = useData<Row>('trading/orders?state=UNKNOWN&limit=200', 10_000);
  const [err, setErr] = useState(''), [msg, setMsg] = useState('');
  const look = (o: Row) => void api(`trading/orders/${o.id}/check-broker`, 'POST').then(r => { setMsg(`${o.symbol}: ${r.state.toLowerCase()}.`); reload(); }).catch(x => setErr((x as Error).message));
  return <section className="intel"><Msg error={error || err} notice={msg} />
    <Table cols={[['Sent', o => <small>{when(o.submitted_at ?? o.created_at)}</small>], ['Account', o => o.account_code], ['Order', o => `${o.side} ${o.quantity} ${o.symbol}`], ['Tag at the broker', o => <code>{o.broker_tag ?? '–'}</code>],
      ['Look-ups', o => `${o.checks ?? 0}${o.last_check_at ? ` · last ${ago(o.last_check_at)}` : ''}${o.last_check?.nothing_at_broker ? ' · nothing found' : ''}`],
      ['', o => <>{(ctx.canOps || ctx.canRisk) && <button onClick={() => look(o)}>Look up now</button>}<button onClick={() => open(o.id)}>Open / resolve</button></>]]}
      rows={data?.items ?? []} empty="No order with an unknown outcome." />
    <p className="cfg-note">The order was sent and no answer came back. COREI looks it up at the broker by its tag every few seconds (open orders, positions and 7 days of deals) and applies what it finds.
      It is never sent again. If the broker shows nothing, check the terminal and resolve the order by hand (Risk approver).</p></section>;
}

export function Connections() {
  const [data, error, reload] = useData<Row>('trading/execution/status', 10_000);
  const [msg, setMsg] = useState(''), [err, setErr] = useState('');
  return <section className="intel"><Msg error={error || err} notice={msg} />
    {data && <Kpis items={[['Dispatcher', data.running ? 'running' : 'stopped', 'Sends approved real orders and looks orders up every 2 s', data.running ? 'up' : 'warn'],
      ['Last send', ago(data.last_dispatch_at)], ['Last look-up', ago(data.last_sync_at)], ['Waiting for approval', data.held_for_approval ?? 0, undefined, data.held_for_approval ? 'warn' : ''],
      ['Outcome unknown', data.open?.UNKNOWN ?? 0, undefined, data.open?.UNKNOWN ? 'warn' : ''],
      ...((data.order_sending ?? []).map((o: Row) => [`Order sending ${o.environment}`, o.enabled ? 'on' : 'off', 'Setup › Order sending', o.enabled ? (o.environment === 'LIVE' ? 'warn' : 'up') : '']) as [string, string, string, string][])]} />}
    <div className="intel-bar"><button onClick={() => void api('trading/execution/dispatch', 'POST').then(r => { setMsg(`Sent ${r.dispatch.sent}, refused ${r.dispatch.rejected}, unknown ${r.dispatch.unknown}; looked up ${r.sync.checked}.`); reload(); }).catch(x => setErr((x as Error).message))}>Send and look up now</button></div>
    {(data?.errors ?? []).length > 0 && <><h4>Recent dispatcher errors</h4><ul>{data!.errors.map((e: string, i: number) => <li key={i}><small>{e}</small></li>)}</ul></>}
    <h4>Which accounts can take real orders</h4>
    <Table cols={[['Account', r => <b>{r.account_code}</b>], ['Environment', r => pill(r.environment, r.environment === 'LIVE' ? 'live' : 'demo')], ['Platform', r => r.platform],
      ['Adapter', r => r.adapter ? pill(r.adapter.replace('_', ' '), 'ok') : pill('none', 'off')], ['Why not', r => <Reasons list={r.reasons} />]]} rows={data?.accounts ?? []} empty="No accounts." rowKey={r => r.account_id} />
    <p className="cfg-note">MT5 (demo and live): the COREI terminal bridge on a Windows node executes with the official terminal, using the master password (bridge 0.43 or later; Setup › Bridge nodes,
      then the account's route). Upstox: the order API with today's login. Every real order also needs: trading permission on the account, the instrument mapped to the broker symbol, order sending on for the environment.</p></section>;
}

export function Routing() {
  const accounts = useAccounts(), [ins] = useData<Row>('config/instruments', 0);
  const [f, setF] = useState<Row>({ instrument_id: '', side: 'BUY', quantity: '', mode: 'PAPER', accounts: [] as string[] });
  const ready = f.instrument_id && Number(f.quantity) > 0 && f.accounts.length > 0;
  const [data, error] = useData<Row>(ready ? `trading/routing/preview?instrument_id=${f.instrument_id}&side=${f.side}&quantity=${f.quantity}&mode=${f.mode}&accounts=${f.accounts.join(',')}` : null, 5_000,
    [f.instrument_id, f.side, f.quantity, f.mode, f.accounts.join()]);
  return <section className="intel">
    <div className="cfg-pair"><Field label="Instrument"><select aria-label="Routing instrument" value={f.instrument_id} onChange={e => setF({ ...f, instrument_id: e.target.value })}><option value="">Choose…</option>{(ins?.items ?? []).map((i: Row) => <option key={i.id} value={i.id}>{i.symbol}</option>)}</select></Field>
      <Field label="Side"><Choice label="Routing side" value={f.side} options={[['BUY', 'Buy'], ['SELL', 'Sell']]} onChange={v => setF({ ...f, side: v })} /></Field>
      <Field label="Quantity"><input aria-label="Routing quantity" value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} /></Field>
      <Field label="Orders"><Choice label="Routing mode" value={f.mode} options={[['PAPER', 'Paper'], ['BROKER', 'Real']]} onChange={v => setF({ ...f, mode: v })} /></Field></div>
    <Field label="Accounts to compare"><div className="intel-checks">{accounts.map(a => <label key={a.id} className="intel-check"><input type="checkbox" checked={f.accounts.includes(a.id)}
      onChange={e => setF({ ...f, accounts: e.target.checked ? [...f.accounts, a.id] : f.accounts.filter((x: string) => x !== a.id) })} /> {a.code}</label>)}</div></Field>
    <Msg error={error} />
    {data && <Table cols={[['Account', r => <b>{r.account_code}</b>], ['Bid', r => r.bid ?? '–'], ['Ask', r => r.ask ?? '–'], ['Spread', r => r.spread ?? '–'], ['Avg slippage (30 d)', r => r.slippage ?? '–'],
      ['Price source', r => r.source?.toLowerCase().replace('_', ' ') ?? '–'], ['Can take it', r => r.eligible ? pill('yes', 'ok') : <small className="cfg-error">{r.why.join(', ')}</small>]]} rows={data.items} empty="" rowKey={r => r.account_id} />}
    <p className="cfg-note">The order ticket's routing (Best price, Lowest cost, Priority, Split equally) uses exactly this comparison when the order is placed, and records it on the order.</p></section>;
}

/* ---------- Stage 8 · Post-trade ---------- */

function PtOverview({ ctx }: { ctx: Ctx }) {
  const [ov, error, reload] = useData<Row>('trading/posttrade/overview', 20_000);
  const [msg, setMsg] = useState(''), [err, setErr] = useState('');
  return <section className="intel"><Msg error={error || err} notice={msg} />
    {ov?.open_breaks && <Kpis items={[['Open breaks', ov.open_total, 'Differences between COREI and the broker waiting for a person', ov.open_total ? 'warn' : 'up'], ['Outcome unknown', ov.unknown_orders, undefined, ov.unknown_orders ? 'warn' : ''],
      ['Fills today', ov.fills_today], ['Average latency today', ov.avg_latency_today_ms != null ? `${ov.avg_latency_today_ms} ms` : '–', 'Submitted → filled']]} />}
    {ov && Object.keys(ov.open_breaks ?? {}).length > 0 && <p>{Object.entries(ov.open_breaks ?? {}).map(([k, n]) => <span key={k} className="cfg-pill warn">{k.toLowerCase().replace(/_/g, ' ')} {String(n)}</span>)}</p>}
    <h4>Last reconciliation per account</h4>
    <Table cols={[['Account', r => r.code], ['When', r => ago(r.at)], ['Against', r => r.kind === 'BROKER' ? 'broker' : 'COREI fills only'], ['Result', r => pill(r.status, r.status === 'MATCHED' ? 'ok' : r.status === 'FAILED' ? 'live' : 'warn')],
      ['Open breaks', r => r.breaks], ['', r => (ctx.canOps || ctx.canRisk) && <button onClick={() => void api(`trading/posttrade/reconcile/${r.account_id}`, 'POST').then(x => { setMsg(`${r.code}: ${x.status.toLowerCase()}, ${x.checked} checked.`); reload(); }).catch(x => setErr((x as Error).message))}>Reconcile now</button>]]}
      rows={ov?.accounts ?? []} empty="No reconciliation yet: it runs every few minutes for accounts with real orders or positions." rowKey={r => r.account_id} />
    <p className="cfg-note">Every few minutes (Execution settings) COREI reads each real account's positions, working orders and deals and compares them with its own record; every account's positions are also
      checked against the fills behind them. A difference is a break and stays open until the next run matches or a person accepts or resolves it.</p></section>;
}

function Breaks({ ctx }: { ctx: Ctx }) {
  const [state, setState] = useState('OPEN'), [account, setAccount] = useAccountView('breaks');
  const [data, error, reload] = useData<Row>(`trading/posttrade/breaks?state=${state}${account ? `&account_id=${account}` : ''}`, 20_000, [state, account]);
  const [err, setErr] = useState('');
  const act = (b: Row, action: string, text: string) => { const note = window.prompt(`${text} — note (audit log):`); if (note) void api(`trading/posttrade/breaks/${b.id}`, 'POST', { action, note }).then(reload).catch(x => setErr((x as Error).message)); };
  return <section className="intel">
    <div className="intel-bar"><Choice label="Break state" value={state} options={[['OPEN', 'Open'], ['ACCEPTED', 'Accepted'], ['RESOLVED', 'Resolved'], ['', 'All']]} onChange={setState} />
      <AccountPick value={account} change={setAccount} label="Break account" all /><a className="link" href="/api/trading/posttrade/export/breaks.csv">CSV</a></div>
    <Msg error={error || err} />
    <Table cols={[['Account', b => b.account_code], ['Kind', b => pill(b.kind, ['POSITION_MISMATCH', 'MISSING_AT_BROKER', 'NOT_IN_COREI'].includes(b.kind) ? 'live' : 'warn')], ['About', b => b.symbol ?? b.broker_symbol ?? b.key],
      ['COREI', b => b.corei_value ?? '–'], ['Broker', b => b.broker_value ?? '–'], ['Difference', b => b.difference ?? '–'], ['Seen', b => <small>{when(b.first_seen)} → {ago(b.last_seen)}</small>],
      ['State', b => <>{pill(b.state, b.state === 'OPEN' ? 'warn' : 'off')}{b.resolution && <><br /><small className="cfg-note">{b.resolution} · {b.resolved_by}</small></>}</>],
      ['', b => b.state === 'OPEN' && ctx.canRisk && <>
        {b.kind === 'FILL_MISSING' && <button className="primary" onClick={() => act(b, 'APPLY_FILL', 'Apply the broker deal to its COREI order')}>Apply fill</button>}
        {['NOT_IN_COREI', 'POSITION_MISMATCH'].includes(b.kind) && <button onClick={() => act(b, 'ADOPT_POSITION', 'Adopt the broker position into COREI (nothing is sent)')}>Adopt</button>}
        <button onClick={() => act(b, 'ACCEPT', 'Accept as known')}>Accept</button><button onClick={() => act(b, 'RESOLVE', 'Mark resolved')}>Resolved</button></>]]}
      rows={data?.items ?? []} empty={state === 'OPEN' ? 'No open breaks in this view. See reconciliation runs for comparison status.' : 'None.'} />
    <p className="cfg-note"><b>Position mismatch / missing at broker / not in COREI</b>: net positions differ (COREI's magic number on MT5). <b>Fill missing</b>: the broker has a deal for a COREI order that COREI has not applied.
      <b> External trade</b>: a deal or position placed outside COREI. <b>Internal</b>: a COREI position differs from its own fills. Accepted breaks are not raised again while their values stay the same.</p></section>;
}

function Runs() {
  const [data, error] = useData<Row>('trading/posttrade/runs', 30_000);
  return <section className="intel"><Msg error={error} />
    <Table cols={[['When', r => <small>{when(r.at)}</small>], ['Account', r => r.account_code], ['Against', r => r.kind.toLowerCase()], ['Result', r => pill(r.status, r.status === 'MATCHED' ? 'ok' : r.status === 'FAILED' ? 'live' : 'warn')],
      ['Checked', r => r.checked], ['Open breaks', r => r.breaks], ['New', r => <small>{(r.detail?.new ?? []).join('; ') || (r.detail?.error ?? '')}</small>], ['By', r => r.actor]]} rows={data?.items ?? []} empty="No runs yet." /></section>;
}

function Tca() {
  const [by, setBy] = useState('account'), [d, setD] = useState(30), [mode, setMode] = useState('');
  const [data, error] = useData<Row>(`trading/posttrade/tca?by=${by}&days=${d}${mode ? `&mode=${mode}` : ''}`, 60_000, [by, d, mode]);
  return <section className="intel">
    <div className="intel-bar"><Choice label="TCA by" value={by} options={[['account', 'Account'], ['broker', 'Broker'], ['instrument', 'Instrument'], ['strategy', 'Strategy'], ['hour', 'Hour (UTC)'], ['day', 'Day'], ['side', 'Side'], ['adapter', 'Adapter'], ['source', 'Order source'], ['mode', 'Paper / real']]} onChange={setBy} />
      <select aria-label="TCA period" value={d} onChange={e => setD(Number(e.target.value))}>{days(d)}</select>
      <Choice label="TCA mode" value={mode} options={[['', 'Paper and real'], ['BROKER', 'Real only'], ['PAPER', 'Paper only']]} onChange={setMode} />
      <a className="link" href={`/api/trading/posttrade/export/tca.csv?by=${by}&days=${d}`}>CSV</a></div>
    <Msg error={error} />
    {data && <Kpis items={[['Slippage cost', money(data.slippage_cost_total, data.currency), 'Positive = paid versus the arrival price'], ['Commission and fees', money(data.commission_total, data.currency)]]} />}
    <Table cols={[['', r => <b>{r.label}</b>], ['Orders', r => r.orders ?? '–'], ['Fill rate', r => r.fill_rate_pct != null ? `${r.fill_rate_pct}%` : '–'], ['Refused', r => r.reject_rate_pct != null ? `${r.reject_rate_pct}%` : '–'],
      ['Fills', r => r.fills], ['Avg slippage', r => r.avg_slippage_bps != null ? `${r.avg_slippage_bps} bp` : '–'], ['Slippage cost', r => money(r.slippage_cost)], ['Better / worse', r => `${r.price_improved} / ${r.worse_than_arrival}`],
      ['Latency avg · p95 · max', r => r.latency_avg_ms != null ? `${r.latency_avg_ms} · ${r.latency_p95_ms} · ${r.latency_max_ms} ms` : '–'], ['Commission', r => money(r.commission)]]}
      rows={data?.rows ?? []} empty="No fills in this period." rowKey={r => String(r.label)} />
    <p className="cfg-note">{data?.note} Latency is from SUBMITTED to the fill. Commission comes from the broker's deals (real MT5 accounts).</p></section>;
}

function Journal() {
  const [account, setAccount] = useState(''), [d, setD] = useState(30);
  const [data, error] = useData<Row>(`trading/posttrade/journal?days=${d}${account ? `&account_id=${account}` : ''}`, 60_000, [account, d]);
  const s = data?.summary;
  return <section className="intel">
    <div className="intel-bar"><AccountPick value={account} change={setAccount} label="Journal account" all /><select aria-label="Journal period" value={d} onChange={e => setD(Number(e.target.value))}>{days(d)}</select>
      <a className="link" href={`/api/trading/posttrade/export/trades.csv?days=${d}${account ? `&account_id=${account}` : ''}`}>CSV</a></div>
    <Msg error={error} />
    {s && <Kpis items={[['Closed trades', s.closed], ['Open', s.open], ['Win rate', s.win_rate_pct != null ? `${s.win_rate_pct}%` : '–'], ['Net after costs', signed(s.net)], ['Average hold', s.avg_minutes != null ? `${s.avg_minutes} min` : '–']]} />}
    <Table cols={[['Opened', t => <small>{when(t.opened_at)}</small>], ['Closed', t => t.open ? pill('open', 'demo') : <small>{when(t.closed_at)}</small>], ['Account', t => t.account], ['Strategy', t => t.strategy],
      ['Instrument', t => <>{t.symbol} <small className="cfg-note">{t.mode.toLowerCase()}</small></>], ['Side', t => <b className={t.side === 'LONG' ? 'up' : 'down'}>{t.side}</b>], ['Size', t => t.max_qty],
      ['Entry → exit', t => `${num(t.avg_entry, 5)} → ${t.avg_exit != null ? num(t.avg_exit, 5) : '…'}`], ['P&L', t => signed(t.realised, t.currency)], ['Costs', t => num(t.costs)],
      ['Best / worst move', t => t.mfe != null ? `+${num(t.mfe, 5)} / −${num(t.mae, 5)}` : '–'], ['Held', t => `${t.duration_minutes} min`]]}
      rows={data?.trips ?? []} empty="No trades in this period." rowKey={(t, i) => `${t.account}${t.symbol}${t.opened_at}${i}`} />
    <p className="cfg-note">A trade runs from flat to flat in one book (account × instrument × strategy × paper/real); a reversal closes it and opens the next. Best and worst move are the price's excursion while open (M1 bars).</p></section>;
}

function Statements() {
  const today = new Date().toISOString().slice(0, 10), month = new Date(Date.now() - 30 * 864e5).toISOString().slice(0, 10);
  const [f, setF] = useState({ account: '', from: month, to: today });
  const [data, error] = useData<Row>(f.account ? `trading/posttrade/statement/${f.account}?day_from=${f.from}&day_to=${f.to}` : null, 0, [f.account, f.from, f.to]);
  return <section className="intel">
    <div className="intel-bar"><AccountPick value={f.account} change={v => setF({ ...f, account: v })} label="Statement account" />
      <input aria-label="Statement from" type="date" value={f.from} onChange={e => setF({ ...f, from: e.target.value })} /><input aria-label="Statement to" type="date" value={f.to} onChange={e => setF({ ...f, to: e.target.value })} />
      {f.account && <a className="link" href={`/api/trading/posttrade/export/statement.csv?account_id=${f.account}&day_from=${f.from}&day_to=${f.to}`}>CSV</a>}
      {data && <button onClick={() => window.print()}>Print</button>}</div>
    <Msg error={error} />
    {data?.days && <>
      <h4>{data.account} · {data.from} to {data.to} · {data.currency}</h4>
      <Table cols={[['Day', r => r.day], ['Fills', r => r.fills], ['Quantity', r => num(r.quantity)], ['Turnover', r => money(r.turnover)], ['Realised', r => signed(r.realised)], ['Commission', r => num(r.commission)],
        ['Swap', r => num(r.swap)], ['Net', r => signed(r.net)], ['COREI equity', r => r.corei_equity != null ? money(r.corei_equity) : '–'], ['Broker balance · equity', r => r.broker_balance ? `${num(r.broker_balance)} · ${num(r.broker_equity)}` : '–']]}
        rows={data.days} empty="Nothing in this period." rowKey={r => r.day} />
      <p><b>Totals</b>: {data.totals.fills} fills · turnover {money(data.totals.turnover)} · realised {money(data.totals.realised)} · costs {money(Number(data.totals.commission) + Number(data.totals.swap))} · net {money(data.totals.net)}</p>
      <p className="cfg-note">{data.note}{data.missing_fx?.length ? ` No FX rate for ${data.missing_fx.join(', ')}: those amounts are not converted.` : ''}</p></>}
    {!f.account && <p className="cfg-note">Choose an account for its daily statement.</p>}</section>;
}

function AuditTrail() {
  const [f, setF] = useState({ days: 7, search: '', state: '', actor: '', account: '' });
  const q = `days=${f.days}${f.search ? `&search=${encodeURIComponent(f.search)}` : ''}${f.state ? `&state=${f.state}` : ''}${f.actor ? `&actor=${encodeURIComponent(f.actor)}` : ''}${f.account ? `&account_id=${f.account}` : ''}`;
  const [data, error] = useData<Row>(`trading/posttrade/events?${q}`, 30_000, [q]);
  return <section className="intel">
    <div className="intel-bar"><input aria-label="Audit search" placeholder="Order ID, client ID, broker tag or number" value={f.search} onChange={e => setF({ ...f, search: e.target.value })} />
      <select aria-label="Audit state" value={f.state} onChange={e => setF({ ...f, state: e.target.value })}><option value="">Every state</option>{['NEW', 'APPROVED', 'REJECTED', 'SUBMITTED', 'ACKNOWLEDGED', 'PARTIALLY_FILLED', 'FILLED', 'CANCEL_PENDING', 'CANCELLED', 'EXPIRED', 'UNKNOWN'].map(s => <option key={s}>{s}</option>)}</select>
      <input aria-label="Audit actor" placeholder="Who (user, risk, broker…)" value={f.actor} onChange={e => setF({ ...f, actor: e.target.value })} />
      <AccountPick value={f.account} change={v => setF({ ...f, account: v })} label="Audit account" all />
      <select aria-label="Audit period" value={f.days} onChange={e => setF({ ...f, days: Number(e.target.value) })}>{days(f.days)}</select>
      <a className="link" href={`/api/trading/posttrade/export/events.csv?days=${f.days}`}>CSV</a></div>
    <Msg error={error} />
    <Table cols={[['Time', e => <small>{when(e.at)}</small>], ['Order', e => <><code>{e.client_order_id.slice(0, 14)}…</code><br /><small className="cfg-note">{e.side} {e.quantity} {e.symbol} · {e.account_code} · {e.mode.toLowerCase()}</small></>],
      ['Change', e => <>{e.state_from ? <small>{e.state_from.toLowerCase()} → </small> : null}{statePill(e.state_to)}</>], ['By', e => e.actor], ['Detail', e => <small className="st-detail">{JSON.stringify(e.detail)}</small>]]}
      rows={data?.items ?? []} empty="No order events match." />
    <p className="cfg-note">Order events are append-only (the database refuses changes). Settings, limits, kill switches, partitions, copy links, releases and break decisions are in Setup › Audit log.</p></section>;
}

function Exports() {
  const [d, setD] = useState(30);
  const items: [string, string, string][] = [['fills', 'Fills', 'Every fill with arrival price, slippage, latency, broker deal'], ['orders', 'Orders', 'Every order with its state'], ['trades', 'Trade journal', 'Round trips'],
    ['events', 'Order audit trail', 'Every order state change'], ['breaks', 'Reconciliation breaks', 'Open and closed'], ['tca', 'TCA', 'By account'], ['attribution', 'Attribution', 'By strategy']];
  return <section className="intel">
    <div className="intel-bar"><select aria-label="Export period" value={d} onChange={e => setD(Number(e.target.value))}>{days(d)}</select></div>
    <Table cols={[['Report', r => <b>{r[1]}</b>], ['Contains', r => r[2]], ['', r => <a className="link" href={`/api/trading/posttrade/export/${r[0]}.csv?days=${d}`}>Download CSV</a>]]} rows={items as unknown as Row[]} empty="" rowKey={r => r[0]} />
    <p className="cfg-note">Exact decimals and ISO times (UTC). Daily statements: Work › Statements.</p></section>;
}

export function PostTradeView() {
  const sections: Section[] = [
    { id: 'overview', label: 'Dashboard', group: 'Overview', hint: 'Open breaks, unknown orders, today\'s fills and each account\'s last reconciliation.', render: ctx => <PtOverview ctx={ctx} /> },
    { id: 'breaks', label: 'Breaks', group: 'Work', hint: 'Every difference between COREI and the broker, and what to do about it.', render: ctx => <Breaks ctx={ctx} /> },
    { id: 'journal', label: 'Trade journal', group: 'Work', hint: 'Round trips with entry, exit, P&L, costs and how far the price went for and against.', render: () => <Journal /> },
    { id: 'statements', label: 'Statements', group: 'Work', hint: 'Per account and day: fills, turnover, P&L, costs, COREI and broker equity.', render: () => <Statements /> },
    { id: 'tca', label: 'Execution quality', group: 'Monitor', hint: 'Slippage against the arrival price, latency, fill and refusal rates, commission — by any dimension.', render: () => <Tca /> },
    { id: 'broker', label: 'Broker book', group: 'Monitor', hint: 'What the broker holds: money, positions, working orders and deals.', render: ctx => <BrokerBook ctx={ctx} /> },
    { id: 'runs', label: 'Reconciliation runs', group: 'Monitor', hint: 'Every run, what it checked and what it found.', render: () => <Runs /> },
    { id: 'audit', label: 'Order audit trail', group: 'Monitor', hint: 'Every order event, searchable by order, tag, state, person or account.', render: () => <AuditTrail /> },
    { id: 'exports', label: 'Exports', group: 'Configure', hint: 'CSV of every post-trade report.', render: () => <Exports /> },
    { id: 'formulas', label: 'Calculations & formulas', group: 'Configure', hint: 'Reconciliation, TCA slippage in bp and money, fill and refusal rates, latency and the trade journal.', render: () => <Formulas stage="Post-trade" /> },
    { id: 'graphs', label: 'Calculation graphs', group: 'Configure', hint: 'Post-trade measures as graphs.', render: ctx => <GraphWorkbench ctx={ctx} purposes={['MEASURE']} /> },
  ];
  return <StageShell view="Post-trade" sections={sections} />;
}

export { statePill };
