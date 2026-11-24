/* Stage 7 · Execution: what arrives from the strategies, and how it is executed.
 * Why: every decision a strategy makes must be visible in Execution with its signal, size, price terms, risk result
 * and order — and a desk must be able to choose, per strategy, whether decisions go straight to the market, wait for a
 * trader, or are only recorded.
 * Purpose: DecisionFlow (the chain signal → decision → risk → order → fill for every decision, filterable),
 * DecisionQueue (decisions waiting for execution: execute — full or smaller size, market or limit — or decline, one or
 * all), ExecutionPolicies (per deployment: handoff AUTO / APPROVE / SIGNAL_ONLY, order type, limit mode and offset,
 * time in force, approval window; versioned, with a reason).
 * Must never: size or check risk itself (the backend does), or offer to execute a decision that is not waiting. */
import { ALGO_DEFAULTS, AlgoFields, algoBody } from './ExecutionAlgos';
import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { api, Field } from './Configuration';
import { DeploymentFilter, Kpis, Msg, Reasons, num, pill, useData, when, type Ctx, type Row } from './StageShell';
import { goto, openOrder, useDeploymentFocus } from './nav';

const OUT_CLS: Record<string, string> = { PENDING: 'warn', ORDERED: 'ok', NO_CHANGE: 'demo', REFUSED: 'live', DECLINED: 'off', EXPIRED: 'off', SUPERSEDED: 'off', RECORDED: 'demo' };
const OUT_LABEL: Record<string, string> = { PENDING: 'waiting for execution', ORDERED: 'ordered', NO_CHANGE: 'no change needed', REFUSED: 'refused', DECLINED: 'declined',
  EXPIRED: 'expired', SUPERSEDED: 'replaced', RECORDED: 'recorded only' };
const HANDOFF: Record<string, [string, string]> = { AUTO: ['Automatic', 'The order is placed as soon as the strategy decides (risk check first).'],
  APPROVE: ['Trader approves', 'The decision waits here; a trader executes, changes or declines it before the window closes.'],
  SIGNAL_ONLY: ['Signal only', 'The decision and its size are recorded; nothing is ordered (research, shadow trading).'] };
const outcome = (o: string) => <span className={`cfg-pill ${OUT_CLS[o] ?? ''}`}>{OUT_LABEL[o] ?? o.toLowerCase()}</span>;
const sideB = (s?: string) => s ? <b className={s === 'BUY' ? 'up' : 'down'}>{s}</b> : <>—</>;
const left = (t?: string) => { if (!t) return '—'; const s = (new Date(t).getTime() - Date.now()) / 1000; return s <= 0 ? 'due' : s < 120 ? `${Math.round(s)} s` : `${Math.round(s / 60)} min`; };
const terms = (r: Row) => `${(r.order_type ?? 'MARKET').toLowerCase()}${r.limit_price ? ` @ ${num(r.limit_price, 5)}` : ''}${r.time_in_force && r.time_in_force !== 'GTC' ? ` · ${r.time_in_force}` : ''}`;

/* The chain of one decision, as steps. */
function Chain({ r }: { r: Row }) {
  const step = (label: string, state: 'ok' | 'warn' | 'bad' | 'off' | 'idle', body: ReactNode) =>
    <span className={`ex-step ex-${state}`}><small>{label}</small><span>{body}</span></span>;
  const risk = r.outcome === 'REFUSED' ? 'bad' : r.order_id ? 'ok' : r.outcome === 'PENDING' ? 'idle' : 'off';
  const ord = r.order_state === 'FILLED' ? 'ok' : r.order_state && ['REJECTED', 'CANCELLED', 'EXPIRED', 'UNKNOWN'].includes(r.order_state) ? 'bad' : r.order_state ? 'warn' : 'off';
  return <span className="ex-chain">
    {step('Signal', 'ok', <>{sideB(r.signal_side)} {r.strength != null && <small>{r.strength}</small>}</>)}
    {step('Decision', r.outcome === 'PENDING' ? 'warn' : ['DECLINED', 'EXPIRED', 'SUPERSEDED'].includes(r.outcome) ? 'off' : 'ok', <>{r.target?.toLowerCase()} {r.quantity != null && <small>{sideB(r.side)} {num(r.quantity, 2)}</small>}</>)}
    {step('Risk', risk, risk === 'ok' ? 'passed' : risk === 'bad' ? 'refused' : risk === 'idle' ? 'at execution' : '—')}
    {step('Order', ord, r.order_state ? <>{r.order_state.toLowerCase().replace(/_/g, ' ')} <small>{terms(r)}</small></> : r.outcome === 'RECORDED' ? 'not sent' : '—')}
    {step('Fill', r.filled_qty && Number(r.filled_qty) > 0 ? 'ok' : 'off', r.avg_fill_price ? <>{num(r.avg_fill_price, 5)}{r.slippage != null && <small> slip {num(r.slippage, 5)}</small>}</> : '—')}
  </span>;
}

export function DecisionFlow({ ctx, compact = false }: { ctx: Ctx; compact?: boolean }) {
  const [focus, clear] = useDeploymentFocus('intents', compact);
  const [filter, setFilter] = useState(''), [q, setQ] = useState('');
  const [data, error, reload] = useData<Row>(`trading/execution/flow?limit=${compact ? 30 : 300}${filter ? `&outcome=${filter}` : ''}${focus ? `&deployment_id=${encodeURIComponent(focus.id)}` : ''}`, 10_000);
  const [act, setAct] = useState<Row | null>(null);
  const sum = data?.summary ?? {}, today = sum.today ?? {};
  const rows: Row[] = (data?.items ?? []).filter((r: Row) => !q || `${r.strategy_code} ${r.symbol} ${r.account_code}`.toLowerCase().includes(q.toLowerCase()));
  return <section className="intel">
    {focus && <DeploymentFilter label={focus.label} clear={clear} />}
    {!compact && <Kpis items={[['Waiting for execution', sum.pending ?? 0, sum.next_expiry ? `First window closes in ${left(sum.next_expiry)}` : 'Nothing waiting', sum.pending ? 'warn' : ''],
      ['Ordered today', today.ORDERED ?? 0, 'Decisions that became orders (risk passed)', 'up'], ['Refused today', today.REFUSED ?? 0, 'By risk or the order checks: see the reasons', today.REFUSED ? 'warn' : ''],
      ['No change', today.NO_CHANGE ?? 0, 'Already at the target'], ['Recorded only', today.RECORDED ?? 0, 'Signal-only strategies'],
      ['Declined / expired', (today.DECLINED ?? 0) + (today.EXPIRED ?? 0) + (today.SUPERSEDED ?? 0)],
      ['Auto · approve · signal', ['AUTO', 'APPROVE', 'SIGNAL_ONLY'].map(k => sum.active_by_handoff?.[k] ?? 0).join(' · '), 'Running strategies by handoff: automatic · trader approves · signal only (Execution policy)']]} />}
    <div className="intel-bar">
      <select aria-label="Decision outcome" value={filter} onChange={e => setFilter(e.target.value)}>
        <option value="">Every decision</option><option value="PENDING">Waiting for execution</option><option value="ORDERED">Ordered</option><option value="REFUSED">Refused</option>
        <option value="RECORDED">Recorded only</option><option value="DECLINED,EXPIRED,SUPERSEDED">Declined, expired, replaced</option><option value="NO_CHANGE">No change</option></select>
      <input aria-label="Find a strategy or instrument" placeholder="Strategy, instrument, account…" value={q} onChange={e => setQ(e.target.value)} />
      <button onClick={reload}>Reload</button>
      <small className="cfg-note">Strategy → signal → decision (target − position) → risk check → order → fill. Decisions arrive here by each strategy's handoff (Execution policy).</small>
    </div>
    <Msg error={error} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table ex-flow"><thead><tr><th>Decided</th><th>Strategy</th><th>Instrument · account</th><th>Handoff</th><th>Chain</th><th>Outcome</th><th /></tr></thead><tbody>
      {rows.map(r => <tr key={r.id} className={r.outcome === 'PENDING' ? 'ex-pending' : ''}>
        <td><small>{when(r.created_at)}</small>{r.bar_time && <><br /><small className="cfg-note">bar {new Date(r.bar_time).toISOString().slice(11, 16)} {r.timeframe}</small></>}</td>
        <td><b>{r.strategy_code ?? 'manual'}</b>{r.version_number ? <small> v{r.version_number}</small> : null}<br /><small className="cfg-note">{r.algorithm ?? ''}</small></td>
        <td>{r.symbol}<br /><small>{r.account_code} · {(r.mode ?? 'PAPER') === 'BROKER' ? pill('real', 'live') : pill('paper', 'demo')}</small></td>
        <td><small>{HANDOFF[r.handoff]?.[0] ?? r.handoff}</small></td>
        <td><Chain r={r} />{r.reasons?.length > 0 && <Reasons list={r.reasons} />}{r.explanation?.[0] && <small className="cfg-note ex-why">{r.explanation[0]}</small>}</td>
        <td>{outcome(r.outcome)}{r.outcome === 'PENDING' && <><br /><small>closes in {left(r.expires_at)}</small></>}
          {r.decided_by && r.decided_by !== 'runtime' && <><br /><small className="cfg-note">by {r.decided_by}{r.decision_note ? `: ${r.decision_note}` : ''}</small></>}</td>
        <td>{r.outcome === 'PENDING' && (ctx.canOps || ctx.canRisk) ? <span className="ex-acts"><button className="primary" onClick={() => setAct(r)}>Execute…</button>
          <button onClick={() => setAct({ ...r, declining: true })}>Decline</button></span>
          : r.order_id ? <button className="link" onClick={() => openOrder(r.order_id)}>Inspect order</button> : null}</td>
      </tr>)}
      {data && !rows.length && <tr><td colSpan={7} className="cfg-note">No decisions {filter ? 'with this outcome ' : ''}yet. They appear when a running strategy decides a new target (Strategies › Dashboard).</td></tr>}
    </tbody></table></div>
    {act && (!focus || act.deployment_id === focus.id) && <ActDialog r={act} done={() => { setAct(null); reload(); }} />}
  </section>;
}

function ActDialog({ r, done }: { r: Row; done: () => void }) {
  const [f, setF] = useState<Row>({ quantity: '', order_type: r.order_type ?? 'MARKET', limit_price: r.limit_price ?? '', note: '', reason: '', how: 'ONCE', ...ALGO_DEFAULTS });
  const [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('');
    try {
      if (r.declining) await api(`trading/execution/decisions/${r.id}/decline`, 'POST', { reason: f.reason });
      else if (f.how === 'ALGO') await api(`trading/execution/decisions/${r.id}/execute`, 'POST', { ...(f.quantity ? { quantity: f.quantity } : {}), algo: algoBody(f), note: f.note });
      else await api(`trading/execution/decisions/${r.id}/execute`, 'POST', { ...(f.quantity ? { quantity: f.quantity } : {}), order_type: f.order_type,
        ...(f.order_type === 'LIMIT' && f.limit_price ? { limit_price: String(f.limit_price) } : {}), note: f.note });
      done();
    } catch (x) { setErr((x as Error).message); } finally { setBusy(false); }
  };
  return <div className="ex-dialog" role="dialog" aria-label={r.declining ? 'Decline decision' : 'Execute decision'}>
    <form className="cfg-form" onSubmit={submit}>
      <h4>{r.declining ? 'Decline' : 'Execute'}: {r.strategy_code} · {r.symbol} · {r.account_code}</h4>
      <p>{sideB(r.side)} {num(r.quantity, 2)} to go {r.target?.toLowerCase()} (held {num(r.current_qty, 2)} → wanted {num(r.target_qty, 2)}) · decided at bid {num(r.decision_bid, 5)} / ask {num(r.decision_ask, 5)}
        {r.stop_loss && <> · stop {num(r.stop_loss, 5)}</>}{r.take_profit && <> · target {num(r.take_profit, 5)}</>}</p>
      {r.sizing_note && <p className="cfg-note">Size: {r.sizing_note}</p>}
      {r.declining ? <Field label="Reason (kept in the audit log)"><input aria-label="Decline reason" value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
        : <>
          <p className="cfg-note">The order is the target minus the position <b>now</b>; it goes through the same risk check as an automatic order and can never be sent twice.</p>
          <Field label="How"><select aria-label="Execute how" value={f.how} onChange={e => setF({ ...f, how: e.target.value })}><option value="ONCE">At once — one order</option><option value="ALGO">Worked by an algorithm (TWAP, VWAP, POV, iceberg)</option></select></Field>
          {f.how === 'ALGO' ? <><Field label="Quantity" hint={`Up to ${num(r.quantity, 2)}; empty = all`}><input aria-label="Execute quantity" inputMode="decimal" placeholder={String(Number(r.quantity))} value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} /></Field>
            <AlgoFields f={f} set={p => setF({ ...f, ...p })} prefix="Execute" /></> : <>
          <div className="cfg-pair"><Field label="Quantity" hint={`Up to ${num(r.quantity, 2)}; empty = all`}><input aria-label="Execute quantity" inputMode="decimal" placeholder={String(Number(r.quantity))} value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} /></Field>
            <Field label="Order type"><select aria-label="Execute order type" value={f.order_type} onChange={e => setF({ ...f, order_type: e.target.value })}><option value="MARKET">Market</option><option value="LIMIT">Limit</option></select></Field></div>
          {f.order_type === 'LIMIT' && <Field label="Limit price" hint="Empty = by the strategy's policy (offset from the live price)"><input aria-label="Execute limit price" inputMode="decimal" value={f.limit_price ?? ''} onChange={e => setF({ ...f, limit_price: e.target.value })} /></Field>}</>}
          <Field label="Note (optional)"><input aria-label="Execute note" value={f.note} onChange={e => setF({ ...f, note: e.target.value })} /></Field></>}
      <Msg error={err} />
      <div className="cfg-actions"><button className="primary" disabled={busy || (r.declining && f.reason.trim().length < 5)}>{busy ? 'Working…' : r.declining ? 'Decline' : 'Execute now'}</button>
        <button type="button" onClick={done}>Cancel</button></div>
    </form>
  </div>;
}

export function DecisionQueue({ ctx }: { ctx: Ctx }) {
  const [focus, clear] = useDeploymentFocus('queue');
  const [data, error, reload] = useData<Row>(`trading/execution/flow?outcome=PENDING&limit=200${focus ? `&deployment_id=${encodeURIComponent(focus.id)}` : ''}`, 5_000);
  const [act, setAct] = useState<Row | null>(null), [msg, setMsg] = useState(''), [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const rows: Row[] = data?.items ?? [];
  const all = async () => {
    if (!window.confirm(`Execute all ${rows.length} displayed decisions at full size using their saved order terms?`)) return;
    setBusy(true); let ok = 0; const bad: string[] = [];
    for (const r of rows) { try { await api(`trading/execution/decisions/${r.id}/execute`, 'POST', { note: 'executed with all waiting' }); ok += 1; } catch (x) { bad.push(`${r.strategy_code} ${r.symbol}: ${(x as Error).message}`); } }
    setMsg(`${ok} requests processed. Inspect order states for acceptance and fills.`); setErr(bad.join(' · ')); setBusy(false); reload();
  };
  return <section className="intel">
    {focus && <DeploymentFilter label={focus.label} clear={clear} />}
    <div className="intel-bar">{(ctx.canOps || ctx.canRisk) && rows.length > 1 && <button className="primary" disabled={busy} onClick={() => void all()}>Execute all {rows.length}</button>}
      <small className="cfg-note">Decisions from strategies whose handoff is <b>Trader approves</b>. Refreshes every 5 s. A window that closes expires the decision; a newer decision of the same strategy replaces an older one.</small></div>
    <Msg error={error || err} notice={msg} />
    {!data ? !error && <p role="status">Loading decision queue…</p> : !rows.length ? <p className="cfg-note ex-empty">Nothing is waiting. Set a strategy's handoff to <b>Trader approves</b> under Execution policy to review its decisions here before they are sent.</p>
      : <div className="ex-cards">{rows.map(r => <div key={r.id} className="ex-card">
        <header><b>{r.strategy_code}</b> <small className="cfg-note">v{r.version_number} · {r.algorithm}</small><span className="ex-timer">closes in {left(r.expires_at)}</span></header>
        <p className="ex-big">{sideB(r.side)} {num(r.quantity, 2)} <span>{r.symbol}</span> <small>{r.account_code} · {(r.mode ?? 'PAPER') === 'BROKER' ? pill('real', 'live') : pill('paper', 'demo')}</small></p>
        <p><small>Target {r.target?.toLowerCase()} · held {num(r.current_qty, 2)} → {num(r.target_qty, 2)} · {terms(r)}{r.stop_loss ? ` · stop ${num(r.stop_loss, 5)}` : ''}{r.take_profit ? ` · target ${num(r.take_profit, 5)}` : ''}</small></p>
        <p><small className="cfg-note">Signal strength {r.strength} on the {r.timeframe} bar {r.bar_time ? new Date(r.bar_time).toISOString().slice(5, 16).replace('T', ' ') : ''} · {r.explanation?.[0]}</small></p>
        {(ctx.canOps || ctx.canRisk) && <div className="cfg-actions"><button className="primary" onClick={() => setAct(r)}>Execute…</button><button onClick={() => setAct({ ...r, declining: true })}>Decline…</button></div>}
      </div>)}</div>}
    {act && (!focus || act.deployment_id === focus.id) && <ActDialog r={act} done={() => { setAct(null); reload(); }} />}
  </section>;
}

export function ExecutionPolicies({ ctx }: { ctx: Ctx }) {
  const [data, error, reload] = useData<Row>('trading/execution/policies', 30_000);
  const [edit, setEdit] = useState<Row | null>(null), [err, setErr] = useState(''), [msg, setMsg] = useState('');
  useEffect(() => { setErr(''); }, [edit?.id]);
  const save = async (e: FormEvent) => {
    e.preventDefault();
    try { await api(`trading/execution/policies/${edit!.id}`, 'PUT', { ...edit!.policy, version: edit!.version, reason: edit!.reason });
      setMsg(`${edit!.strategy_code} · ${edit!.symbol}: ${HANDOFF[edit!.policy.handoff][0].toLowerCase()}, ${edit!.policy.order_type.toLowerCase()}.`); setEdit(null); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  const p = edit?.policy;
  const set = (k: string, v: unknown) => setEdit({ ...edit!, policy: { ...edit!.policy, [k]: v } });
  return <section className="intel">
    <div className="ex-handoffs">{Object.entries(HANDOFF).map(([k, [l, d]]) => <div key={k}><b>{l}</b><small>{d}</small></div>)}</div>
    <Msg error={error || err} notice={msg} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Strategy</th><th>Instrument · account</th><th>Status</th><th>Handoff</th><th>Order</th><th>Time in force</th><th>Approval window</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((r: Row) => <tr key={r.id}><td><b>{r.strategy_code}</b> <small>v{r.version_number}</small><br /><small className="cfg-note">{r.algorithm}</small></td>
        <td>{r.symbol}<br /><small>{r.account_code} · {r.environment?.toLowerCase()} · {(r.mode ?? 'PAPER') === 'BROKER' ? 'real orders' : 'paper'}</small></td><td>{pill(r.status, r.status === 'ACTIVE' ? 'ok' : 'off')}</td>
        <td><span className={`cfg-pill ${r.policy.handoff === 'AUTO' ? 'ok' : r.policy.handoff === 'APPROVE' ? 'warn' : 'demo'}`}>{HANDOFF[r.policy.handoff]?.[0]}</span></td>
        <td>{r.policy.order_type === 'LIMIT' ? `limit · ${r.policy.limit_mode.toLowerCase()} ${r.policy.limit_offset_ticks} ticks` : 'market'}</td><td>{r.policy.time_in_force}</td>
        <td>{r.policy.handoff === 'APPROVE' ? `${r.policy.approval_minutes} min` : '—'}</td>
        <td>{ctx.canRisk && <button onClick={() => setEdit({ ...r, policy: { ...r.policy }, reason: '' })}>Change</button>}</td></tr>)}
      {data && !(data.items ?? []).length && <tr><td colSpan={8} className="cfg-note">No deployments yet (Setup › Deployments).</td></tr>}</tbody></table></div>
    {edit && p && <form className="cfg-form ex-policy" onSubmit={save}>
      <h4>Execution policy · {edit.strategy_code} · {edit.symbol} · {edit.account_code}</h4>
      <Field label="Handoff"><span className="ex-radio">{Object.entries(HANDOFF).map(([k, [l, d]]) => <label key={k} title={d}><input type="radio" name="handoff" checked={p.handoff === k} onChange={() => set('handoff', k)} /> {l}</label>)}</span></Field>
      <div className="cfg-pair"><Field label="Order type"><select aria-label="Policy order type" value={p.order_type} onChange={e => set('order_type', e.target.value)}><option value="MARKET">Market</option><option value="LIMIT">Limit</option></select></Field>
        <Field label="Time in force" hint="GTC rests until filled or cancelled; DAY until the market closes; IOC fills now or cancels"><select aria-label="Policy time in force" value={p.time_in_force} onChange={e => set('time_in_force', e.target.value)}>{['GTC', 'DAY', 'IOC'].map(x => <option key={x}>{x}</option>)}</select></Field></div>
      {p.order_type === 'LIMIT' && <div className="cfg-pair"><Field label="Limit price" hint="Marketable: buy at ask + n ticks (a price cap). Passive: buy at bid − n ticks (waits for the price)">
        <select aria-label="Policy limit mode" value={p.limit_mode} onChange={e => set('limit_mode', e.target.value)}><option value="MARKETABLE">Marketable (cap the price)</option><option value="PASSIVE">Passive (wait at the touch)</option></select></Field>
        <Field label="Offset (ticks)"><input aria-label="Policy offset" type="number" min={0} value={p.limit_offset_ticks} onChange={e => set('limit_offset_ticks', Number(e.target.value))} /></Field></div>}
      {p.handoff === 'APPROVE' && <Field label="Approval window (minutes)" hint="After this the decision expires and is not sent"><input aria-label="Policy approval minutes" type="number" min={1} max={1440} value={p.approval_minutes} onChange={e => set('approval_minutes', Number(e.target.value))} /></Field>}
      <Field label="Reason (kept in the audit log)"><input aria-label="Policy reason" value={edit.reason} onChange={e => setEdit({ ...edit, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary" disabled={edit.reason.trim().length < 5}>Save policy</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
    </form>}
    <p className="cfg-note">Risk approvers change policies. Changing one does not stop the strategy; it changes how its next decision reaches execution. A paused strategy decides nothing.</p>
  </section>;
}

