/* Stage 7 · Execution algorithms (DEC-94): work an order over time as child orders — TWAP, VWAP, POV, iceberg.
 * Why: a desk does not send a large or patient order at once; it watches it fill against the price when it started.
 * Purpose: AlgoFields (the settings of each algorithm, shared with the decision queue), AlgoTicket (a parent order),
 * AlgoBlotter (working, paused and finished parents with progress, average price against arrival in bp, the next
 * step and the equation; pause, resume, cancel), AlgoDetail (plan, child orders, events).
 * Must never: size, check risk or send anything itself — the backend works the parent; every child goes through the
 * normal order engine and its pre-trade check, and a refused or unknown child pauses the parent. */
import { useState, type FormEvent } from 'react';
import { api, Choice, Field } from './Configuration';
import { key, Kpis, Msg, num, pill, useData, when, type Ctx, type Row } from './StageShell';

const ALGO_HELP: Record<string, string> = {
  TWAP: 'Even slices over the duration: a slice every duration ÷ slices.',
  VWAP: 'Slices weighted by the instrument\'s usual volume at that time of day (last days of stored M1 bars).',
  POV: 'A share of the market volume traded since the start; finishes the rest at the end time if asked.',
  ICEBERG: 'Only a small clip shows; the next clip goes when the last one is filled.',
};
export const ALGO_DEFAULTS: Row = { algo: 'TWAP', duration_minutes: 30, slices: 10, lookback_days: 20, participation_pct: 10, interval_seconds: 60, finish: true,
  display_qty: '', price_cap: '', child_type: 'MARKET' };

export function algoBody(f: Row): Row {
  const b: Row = { algo: f.algo, duration_minutes: Number(f.duration_minutes), child_type: f.child_type };
  if (f.algo === 'TWAP' || f.algo === 'VWAP') b.slices = Number(f.slices);
  if (f.algo === 'VWAP') b.lookback_days = Number(f.lookback_days);
  if (f.algo === 'POV') Object.assign(b, { participation_pct: Number(f.participation_pct), interval_seconds: Number(f.interval_seconds), finish: Boolean(f.finish) });
  if (f.algo === 'ICEBERG') b.display_qty = String(f.display_qty);
  if (f.price_cap) b.price_cap = String(f.price_cap);
  return b;
}

export function AlgoFields({ f, set, prefix = 'Algo' }: { f: Row; set: (p: Row) => void; prefix?: string }) {
  return <>
    <div className="cfg-pair"><Field label="Algorithm" hint={ALGO_HELP[f.algo]}><Choice label={`${prefix} algorithm`} value={f.algo} options={[['TWAP', 'TWAP — evenly over time'], ['VWAP', 'VWAP — with the usual volume'],
      ['POV', 'POV — a share of what trades'], ['ICEBERG', 'Iceberg — one visible clip']]} onChange={v => set({ algo: v })} /></Field>
      <Field label="Duration (minutes)"><input aria-label={`${prefix} duration`} inputMode="numeric" value={f.duration_minutes} onChange={e => set({ duration_minutes: e.target.value })} /></Field></div>
    <div className="cfg-pair">
      {(f.algo === 'TWAP' || f.algo === 'VWAP') && <Field label="Slices"><input aria-label={`${prefix} slices`} inputMode="numeric" value={f.slices} onChange={e => set({ slices: e.target.value })} /></Field>}
      {f.algo === 'VWAP' && <Field label="Volume history (days)"><input aria-label={`${prefix} lookback`} inputMode="numeric" value={f.lookback_days} onChange={e => set({ lookback_days: e.target.value })} /></Field>}
      {f.algo === 'POV' && <><Field label="Participation %" hint="Of the market volume since the start"><input aria-label={`${prefix} participation`} inputMode="decimal" value={f.participation_pct} onChange={e => set({ participation_pct: e.target.value })} /></Field>
        <label className="intel-check"><input type="checkbox" checked={Boolean(f.finish)} onChange={e => set({ finish: e.target.checked })} /> finish the rest at the end time</label></>}
      {f.algo === 'ICEBERG' && <Field label="Visible clip" hint="Quantity shown at a time"><input aria-label={`${prefix} clip`} inputMode="decimal" required value={f.display_qty} onChange={e => set({ display_qty: e.target.value })} /></Field>}
    </div>
    <div className="cfg-pair"><Field label="Price cap" hint="Optional: no child buys above / sells below it (a slice waits)"><input aria-label={`${prefix} price cap`} inputMode="decimal" value={f.price_cap} onChange={e => set({ price_cap: e.target.value })} /></Field>
      <Field label="Children as"><Choice label={`${prefix} child type`} value={f.child_type} options={[['MARKET', 'Market orders'], ['LIMIT', 'Limits at the cap (IOC)']]} onChange={v => set({ child_type: v })} /></Field></div>
  </>;
}

export function AlgoTicket({ ctx, accounts, instruments }: { ctx: Ctx; accounts: Row[]; instruments: Row[] }) {
  const blank = { mode: 'PAPER', account_id: '', instrument_id: '', side: 'BUY', quantity: '', stop_loss: '', take_profit: '', reason: '', ...ALGO_DEFAULTS };
  const [f, setF] = useState<Row>(blank), [k, setK] = useState(key()), [out, setOut] = useState<Row | null>(null), [err, setErr] = useState(''), [confirm, setConfirm] = useState(false);
  const real = f.mode === 'BROKER';
  const set = (p: Row) => { setF({ ...f, ...p }); setConfirm(false); };
  const send = async (e: FormEvent) => {
    e.preventDefault(); setErr('');
    if (real && !confirm) { setConfirm(true); return; }
    setConfirm(false);
    try {
      const p = await api('trading/execution/algos', 'POST', { ...algoBody(f), account_id: f.account_id, instrument_id: f.instrument_id, side: f.side, quantity: String(f.quantity), mode: f.mode,
        ...(f.stop_loss ? { stop_loss: String(f.stop_loss) } : {}), ...(f.take_profit ? { take_profit: String(f.take_profit) } : {}), ticket_key: k, reason: f.reason });
      setOut(p); setK(key());
    } catch (x) { setErr((x as Error).message); }
  };
  if (!ctx.canOps && !ctx.canRisk) return <p className="cfg-note">Trading operations place algorithm orders.</p>;
  return <form className="cfg-form" onSubmit={send}>
    <Field label="Order"><Choice label="Algo mode" value={f.mode} options={[['PAPER', 'Paper (COREI fills each child)'], ['BROKER', 'Real (each child sent to the broker)']]} onChange={v => set({ mode: v })} /></Field>
    <div className="cfg-pair"><Field label="Account"><select aria-label="Algo account" required value={f.account_id} onChange={e => set({ account_id: e.target.value })}><option value="">Choose…</option>
      {accounts.map((a: Row) => <option key={a.id} value={a.id}>{a.code} · {a.environment}</option>)}</select></Field>
      <Field label="Instrument"><select aria-label="Algo instrument" required value={f.instrument_id} onChange={e => set({ instrument_id: e.target.value })}><option value="">Choose…</option>
        {instruments.map((i: Row) => <option key={i.id} value={i.id}>{i.symbol} · {i.name}</option>)}</select></Field></div>
    <div className="cfg-pair"><Field label="Side"><Choice label="Algo side" value={f.side} options={[['BUY', 'Buy'], ['SELL', 'Sell']]} onChange={v => set({ side: v })} /></Field>
      <Field label="Whole quantity"><input aria-label="Algo quantity" required inputMode="decimal" value={f.quantity} onChange={e => set({ quantity: e.target.value })} /></Field></div>
    <AlgoFields f={f} set={set} />
    {real && <div className="cfg-pair"><Field label="Stop-loss (each child)"><input aria-label="Algo stop loss" inputMode="decimal" value={f.stop_loss} onChange={e => set({ stop_loss: e.target.value })} /></Field>
      <Field label="Take-profit (each child)"><input aria-label="Algo take profit" inputMode="decimal" value={f.take_profit} onChange={e => set({ take_profit: e.target.value })} /></Field></div>}
    <Field label="Reason (audit log)"><input aria-label="Algo reason" value={f.reason} onChange={e => set({ reason: e.target.value })} /></Field>
    {confirm && <p className="cfg-error" role="alert">Work {f.side} {f.quantity} with {f.algo} at the broker — every child is a real order. Press again to confirm.</p>}
    <div className="cfg-actions"><button className={real ? 'danger' : 'primary'}>{real ? (confirm ? 'Confirm: start the real algorithm' : 'Start real algorithm') : `Start ${f.algo} on paper`}</button></div>
    <Msg error={err} notice={out ? `${out.algo} ${out.side} ${num(out.quantity, 2)} started: ${out.children} child order${out.children === 1 ? '' : 's'} so far, ${num(out.filled_qty, 2)} done. ${out.last_note ?? ''}` : ''} />
    <p className="cfg-note">Each child is an ordinary order: its own risk check, its own client order ID (never sent twice), its own fills. A refused or unknown child pauses the algorithm until a person looks.</p>
  </form>;
}

const TONE: Record<string, string> = { WORKING: 'ok', PAUSED: 'warn', COMPLETED: 'demo', CANCELLED: 'off', EXPIRED: 'off' };

export function AlgoBlotter({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/execution/algos', 5_000);
  const [open, setOpen] = useState<string | null>(null), [msg, setMsg] = useState(''), [err, setErr] = useState('');
  const items: Row[] = data?.items ?? [];
  const act = async (p: Row, a: string) => {
    const reason = window.prompt(`${a[0].toUpperCase()}${a.slice(1)} ${p.algo} ${p.symbol}: reason (audit log)`) ?? '';
    if (reason.trim().length < 5) return;
    try { await api(`trading/execution/algos/${p.id}/${a}`, 'POST', { reason }); setMsg(`${p.algo} ${p.symbol}: ${a}d.`); setErr(''); reload(); } catch (x) { setErr((x as Error).message); }
  };
  const run = async () => { try { const r = await api('trading/execution/algos-tick', 'POST'); setMsg(`Looked at ${r.parents} working algorithm(s); ${r.children} child order(s) sent.`); reload(); } catch (x) { setErr((x as Error).message); } };
  const working = items.filter(p => p.state === 'WORKING').length, paused = items.filter(p => p.state === 'PAUSED').length;
  return <section className="intel">
    <Kpis items={[['Working', working, undefined, working ? 'up' : ''], ['Paused', paused, 'A refused or unknown child, or paused by a person', paused ? 'warn' : ''],
      ['Finished', items.filter(p => p.state === 'COMPLETED').length], ['Cancelled / expired', items.filter(p => ['CANCELLED', 'EXPIRED'].includes(p.state)).length]]} />
    <div className="intel-bar">{(ctx.canOps || ctx.canRisk) && <button onClick={() => void run()}>Run now</button>}<small className="cfg-note">COREI looks at every working algorithm every 2 seconds and every trading cycle.</small></div>
    <Msg error={error || err} notice={msg} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Started</th><th>Algorithm</th><th>Order</th><th>Done</th><th>Average vs arrival</th><th>Next</th><th>State</th><th /></tr></thead><tbody>
      {items.map(p => <tr key={p.id}>
        <td><small>{when(p.created_at)}</small><br /><small className="cfg-note">until {when(p.end_at)}</small></td>
        <td><b>{p.algo}</b><br /><small className="cfg-note" title={p.equation}>{p.equation}</small></td>
        <td>{p.side} {num(p.quantity, 2)} {p.symbol}<br /><small>{p.account_code} · {p.mode === 'BROKER' ? pill('real', 'live') : pill('paper', 'demo')}{p.deployment_id ? ' · from a strategy' : ''}</small></td>
        <td><div className="ex-bar" title={`${num(p.filled_qty, 2)} of ${num(p.quantity, 2)}`}><i style={{ width: `${Math.min(100, p.done_pct ?? 0)}%` }} /></div>
          <small>{num(p.filled_qty, 2)} / {num(p.quantity, 2)} · {p.children} child{p.children === 1 ? '' : 'ren'}</small></td>
        <td>{p.avg_price ? <>{num(p.avg_price, 5)} vs {num(p.arrival, 5)}<br /><small className={Number(p.slippage_bp) > 0 ? 'down' : 'up'}>{p.slippage_bp != null ? `${Number(p.slippage_bp) > 0 ? '+' : ''}${p.slippage_bp} bp` : ''}</small></> : <small className="cfg-note">—</small>}</td>
        <td><small>{p.last_note ?? '—'}</small></td>
        <td>{pill(p.state.toLowerCase(), TONE[p.state] ?? '')}</td>
        <td><span className="ex-acts"><button onClick={() => setOpen(p.id)}>Detail</button>
          {(ctx.canOps || ctx.canRisk) && p.state === 'WORKING' && <button onClick={() => void act(p, 'pause')}>Pause</button>}
          {(ctx.canOps || ctx.canRisk) && p.state === 'PAUSED' && <button className="primary" onClick={() => void act(p, 'resume')}>Resume</button>}
          {(ctx.canOps || ctx.canRisk) && ['WORKING', 'PAUSED'].includes(p.state) && <button className="danger" onClick={() => void act(p, 'cancel')}>Cancel</button>}</span></td></tr>)}
      {data && !items.length && <tr><td colSpan={8} className="cfg-note">No algorithm orders yet. Start one with the ticket, or choose an algorithm when executing a decision from the queue.</td></tr>}
    </tbody></table></div>
    {open && <AlgoDetail id={open} close={() => setOpen(null)} />}
  </section>;
}

function AlgoDetail({ id, close }: { id: string; close: () => void }) {
  const [d, error] = useData<Row>(`trading/execution/algos/${id}`, 5_000, [id]);
  return <div className="ex-dialog" role="dialog" aria-label="Algorithm detail"><div className="cfg-form">
    <h4>{d ? `${d.algo} · ${d.side} ${num(d.quantity, 2)} ${d.symbol} · ${d.account_code}` : 'Algorithm'}</h4>
    <Msg error={error} />
    {d && <>
      <p className="cfg-note"><code>{d.equation}</code></p>
      <p>Arrival {num(d.arrival, 5)} · average {d.avg_price ? num(d.avg_price, 5) : '—'}{d.slippage_bp != null ? ` · ${d.slippage_bp} bp` : ''} · {num(d.filled_qty, 2)} of {num(d.quantity, 2)} done{d.price_cap ? ` · cap ${num(d.price_cap, 5)}` : ''}</p>
      {d.plan?.length > 0 && <><h5>Plan</h5><table className="cfg-table compact"><thead><tr><th>Slice</th><th>At minute</th><th>Share</th><th>Done by then</th></tr></thead><tbody>
        {d.plan.map((s: Row) => <tr key={s.slice}><td>{s.slice}</td><td>{s.at_minute}</td><td>{(s.share * 100).toFixed(1)}%</td><td>{(s.cumulative * 100).toFixed(1)}%</td></tr>)}</tbody></table></>}
      <h5>Child orders</h5><table className="cfg-table compact"><thead><tr><th>Sent</th><th>Order</th><th>Quantity</th><th>Filled</th><th>Price</th><th>State</th></tr></thead><tbody>
        {(d.children_orders ?? []).map((k: Row) => <tr key={k.id}><td><small>{when(k.created_at)}</small></td><td><code>{String(k.client_order_id).slice(0, 12)}…</code></td><td>{num(k.quantity, 2)}</td>
          <td>{num(k.filled_qty, 2)}</td><td>{k.avg_fill_price ? num(k.avg_fill_price, 5) : '—'}</td><td>{k.state.toLowerCase()}{k.reasons?.length ? <small className="cfg-error-text"> {Array.isArray(k.reasons) ? k.reasons[0] : String(k.reasons)}</small> : null}</td></tr>)}
        {!(d.children_orders ?? []).length && <tr><td colSpan={6} className="cfg-note">No child yet.</td></tr>}</tbody></table>
      <h5>Events</h5><ul className="ex-events">{(d.events ?? []).map((e: Row) => <li key={e.id}><small>{when(e.at)}</small> <b>{e.kind.toLowerCase()}</b> <small>{e.actor}</small>
        {e.detail?.why && <small className="cfg-note"> · {e.detail.why}</small>}{e.detail?.reason && <small className="cfg-note"> · {e.detail.reason}</small>}</li>)}</ul></>}
    <div className="cfg-actions"><button type="button" onClick={close}>Close</button></div>
  </div></div>;
}
