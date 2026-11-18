/* Stage 3 signal library and the "Calculations & formulas" panel of every stage.
 * Why: a desk must see how every number it trades on is made. The formula belongs to the signal (Stage 3); strategies
 * (Stage 4) combine signals; every stage shows its own equations next to its live numbers.
 * Purpose: Formulas (every calculation of a stage, searchable; Intelligence shows each feature's value on the chosen
 * instrument), SignalLibrary (the formulas with parameters, threshold, timeframes; add, edit, pause, delete),
 * SignalBoard (instrument × signal scores, click a cell for the equation with that bar's numbers), SignalExplain (one
 * instrument: every signal's equation, inputs, worked arithmetic and its last 60 scores), SignalResearch (does it
 * predict? IC, t-stat, hit rate, quintiles, turnover, cost, verdict, chart), BlendEditor (the SIGNAL_BLEND recipe of a
 * strategy: components, weights, entry/exit, with the combined score worked out live on a chosen instrument).
 * Must never: compute a score itself (the backend computes; these screens show and ask). */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api, Field } from './Configuration';
import { Kpis, Msg, num, pill, useData, when, type Ctx, type Row } from './StageShell';

const TFS = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const scoreCls = (s: any) => s == null ? 'sig-none' : s >= 60 ? 'sig-l3' : s >= 20 ? 'sig-l2' : s > 0 ? 'sig-l1' : s <= -60 ? 'sig-s3' : s <= -20 ? 'sig-s2' : s < 0 ? 'sig-s1' : 'sig-zero';
const fmtScore = (s: any) => s == null ? '–' : `${Number(s) > 0 ? '+' : ''}${Number(s).toFixed(1)}`;
const fmtVal = (v: any) => v == null ? '–' : typeof v === 'number' ? (Math.abs(v) >= 1000 ? v.toFixed(2) : Number(v.toPrecision(6)).toString()) : String(v);

function SourcePick({ value, change, label = 'Instrument' }: { value: string; change: (v: string) => void; label?: string }) {
  const [sources] = useData<Row>('trading/sources', 0);
  return <select aria-label={label} value={value} onChange={e => change(e.target.value)}><option value="">Choose an instrument…</option>
    {(sources?.items ?? []).map((s: Row) => <option key={s.account_id + s.broker_symbol} value={`${s.account_id}|${s.broker_symbol}`}>{s.account_code} · {s.broker_symbol}</option>)}</select>;
}
const TfPick = ({ value, change, label = 'Timeframe' }: { value: string; change: (v: string) => void; label?: string }) =>
  <select aria-label={label} value={value} onChange={e => change(e.target.value)}>{TFS.map(t => <option key={t}>{t}</option>)}</select>;

function MiniSpark({ values, threshold }: { values: (number | null)[]; threshold?: number }) {
  const pts = values.map((v, i) => [i, v] as const).filter(([, v]) => v != null) as [number, number][];
  if (pts.length < 2) return null;
  const W = 140, H = 34, x = (i: number) => (i / Math.max(1, values.length - 1)) * W, y = (v: number) => H / 2 - (v / 100) * (H / 2 - 2);
  return <svg className="sig-spark" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Recent scores">
    <line x1="0" x2={W} y1={H / 2} y2={H / 2} className="sig-axis" />
    {threshold != null && <><line x1="0" x2={W} y1={y(threshold)} y2={y(threshold)} className="sig-thr" /><line x1="0" x2={W} y1={y(-threshold)} y2={y(-threshold)} className="sig-thr" /></>}
    <polyline fill="none" strokeWidth="1.4" className="sig-line" points={pts.map(([i, v]) => `${x(i)},${y(v)}`).join(' ')} /></svg>;
}

/* ---------- every stage: Calculations & formulas ---------- */

export function Formulas({ stage, features = false }: { stage: string; features?: boolean }) {
  const [data, error] = useData<Row>('trading/formulas', 0);
  const [q, setQ] = useState(''), [src, setSrc] = useState(''), [tf, setTf] = useState('H1'), [live, setLive] = useState<Row | null>(null), [liveErr, setLiveErr] = useState('');
  useEffect(() => {
    if (!features || !src) { setLive(null); return; }
    const [a, s] = src.split('|');
    void api(`intel/instrument/${a}/${encodeURIComponent(s)}?timeframe=${tf}&limit=10`).then(d => { setLive(d); setLiveErr(''); }).catch(e => { setLive(null); setLiveErr((e as Error).message); });
  }, [features, src, tf]);
  const rows: Row[] = (data?.stages?.[stage] ?? []).filter((f: Row) => !q || `${f.name} ${f.equation} ${f.notes ?? ''} ${f.variables ?? ''}`.toLowerCase().includes(q.toLowerCase()));
  const last: Row = live?.history?.length ? live.history[live.history.length - 1] : null;
  const valuesOf = (name: string) => {
    if (!last?.features) return null;
    const m = name.match(/—\s*(.+)$/);
    const keys = m ? m[1].split(',').map(k => k.trim()).filter(k => /^[a-z0-9_]+$/.test(k)) : [];
    return keys.length ? keys.map(k => <span key={k} className="sig-kv"><code>{k}</code> {fmtVal(last.features[k])}</span>) : null;
  };
  return <section className="intel">
    <div className="intel-bar">
      <input aria-label={`Search ${stage} formulas`} placeholder="Search formulas…" value={q} onChange={e => setQ(e.target.value)} />
      {features && <><SourcePick value={src} change={setSrc} label="Formula values for instrument" /><TfPick value={tf} change={setTf} label="Formula values timeframe" />
        {last && <small className="cfg-note">Values on the bar closed {when(last.bar_time)} · regime {last.regime ?? '—'}</small>}</>}
    </div>
    <Msg error={error || liveErr} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table sig-formulas"><thead><tr><th>Calculation</th><th>Formula</th>{features && <th>This bar</th>}<th>Inputs, notes</th><th>Code</th></tr></thead><tbody>
      {rows.map((f, i) => <tr key={i}><td><b>{f.name}</b></td><td><code className="sig-eq">{f.equation}</code></td>{features && <td>{valuesOf(f.name) ?? <small className="cfg-note">—</small>}</td>}
        <td><small>{[f.variables, f.notes].filter(Boolean).join(' · ')}</small></td><td><small className="cfg-note">{f.code}</small></td></tr>)}
      {data && !rows.length && <tr><td colSpan={5} className="cfg-note">No formula matches.</td></tr>}</tbody></table></div>
    <p className="cfg-note">These are the formulas the code uses, kept in one registry beside the code (a change to a calculation changes its entry in the same release). Numbers are worked out on live data in each panel.</p>
  </section>;
}

/* ---------- Stage 3: the library ---------- */

type Def = Row & { code: string; kind: string; parameters: Row; threshold: number; timeframes: string[]; status: string };

export function SignalLibrary({ ctx }: { ctx: Ctx }) {
  const [cat] = useData<Row>('trading/signal-library/catalogue', 0), [defs, error, reload] = useData<Row>('trading/signal-library/definitions', 30_000);
  const kinds: Row[] = cat?.items ?? [];
  const [edit, setEdit] = useState<Row | null>(null), [err, setErr] = useState(''), [msg, setMsg] = useState('');
  const kind = kinds.find(k => k.kind === edit?.kind);
  const start = (d?: Def) => { setErr(''); setMsg(''); setEdit(d ? { ...d, parameters: { ...d.parameters }, reason: '' }
    : { code: '', name: '', kind: kinds[0]?.kind ?? 'EMA_TREND', parameters: { ...(kinds[0]?.defaults ?? {}) }, threshold: 20, timeframes: ['M5', 'M15', 'H1', 'H4', 'D1'], status: 'ACTIVE', notes: '', reason: '' }); };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    const body = { code: edit!.code, name: edit!.name, kind: edit!.kind, parameters: edit!.parameters, threshold: Number(edit!.threshold), timeframes: edit!.timeframes,
      status: edit!.status, notes: edit!.notes ?? '', reason: edit!.reason };
    try { if (edit!.id) await api(`trading/signal-library/definitions/${edit!.id}`, 'PUT', body); else await api('trading/signal-library/definitions', 'POST', body);
      setMsg(`${body.code} saved — scores are recomputed at the next engine run.`); setEdit(null); reload(); } catch (x) { setErr((x as Error).message); }
  };
  const act = async (d: Def, what: 'pause' | 'delete') => {
    const reason = window.prompt(`${what === 'delete' ? 'Delete' : d.status === 'ACTIVE' ? 'Pause' : 'Resume'} ${d.code}: reason (kept in the audit log)`) ?? '';
    if (reason.trim().length < 5) return;
    try {
      if (what === 'delete') await api(`trading/signal-library/definitions/${d.id}?reason=${encodeURIComponent(reason)}`, 'DELETE');
      else await api(`trading/signal-library/definitions/${d.id}`, 'PUT', { code: d.code, name: d.name, kind: d.kind, parameters: d.parameters, threshold: Number(d.threshold), timeframes: d.timeframes,
        status: d.status === 'ACTIVE' ? 'PAUSED' : 'ACTIVE', notes: d.notes ?? '', reason });
      reload();
    } catch (x) { setErr((x as Error).message); }
  };
  return <section className="intel">
    <div className="intel-bar">{ctx.canRisk && <button className="primary" onClick={() => start()}>+ New signal</button>}
      <small className="cfg-note">Every signal is a score from −100 (strong short) to +100 (strong long) per instrument and closed bar. LONG at ≥ +threshold, SHORT at ≤ −threshold, else FLAT.</small></div>
    <Msg error={error || err} notice={msg} />
    {edit && <form className="cfg-form sig-edit" onSubmit={save}>
      <div className="cfg-pair"><Field label="Code" hint="Capitals, digits and _"><input aria-label="Signal code" required value={edit.code} onChange={e => setEdit({ ...edit, code: e.target.value.toUpperCase() })} /></Field>
        <Field label="Name"><input aria-label="Signal name" required value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></Field></div>
      <Field label="Formula" hint={kind?.detail}><select aria-label="Signal formula" value={edit.kind} onChange={e => { const k = kinds.find(x => x.kind === e.target.value); setEdit({ ...edit, kind: e.target.value, parameters: { ...(k?.defaults ?? {}) } }); }}>
        {kinds.map(k => <option key={k.kind} value={k.kind}>{k.label} · {k.family}</option>)}</select></Field>
      {kind && <p className="sig-eqline"><code className="sig-eq">{kind.equation}</code></p>}
      <div className="st-params">{Object.entries(kind?.defaults ?? {}).map(([p, dflt]) => <Field key={p} label={p.replace(/_/g, ' ')} hint={`default ${dflt}`}>
        <input aria-label={`Signal parameter ${p}`} inputMode="decimal" value={edit.parameters[p] ?? ''} onChange={e => setEdit({ ...edit, parameters: { ...edit.parameters, [p]: e.target.value === '' ? undefined : Number(e.target.value) } })} /></Field>)}
        <Field label="Threshold" hint="|score| that counts as a side"><input aria-label="Signal threshold" type="number" min={1} max={99} value={edit.threshold} onChange={e => setEdit({ ...edit, threshold: e.target.value })} /></Field>
        <Field label="Status"><select aria-label="Signal status" value={edit.status} onChange={e => setEdit({ ...edit, status: e.target.value })}><option>ACTIVE</option><option>PAUSED</option></select></Field></div>
      <Field label="Timeframes scored"><span className="sig-tfs">{TFS.map(t => <label key={t}><input type="checkbox" checked={edit.timeframes.includes(t)}
        onChange={e => setEdit({ ...edit, timeframes: e.target.checked ? [...edit.timeframes, t] : edit.timeframes.filter((x: string) => x !== t) })} /> {t}</label>)}</span></Field>
      <Field label="Notes"><input aria-label="Signal notes" value={edit.notes ?? ''} onChange={e => setEdit({ ...edit, notes: e.target.value })} /></Field>
      <Field label="Reason (kept in the audit log)"><input aria-label="Signal reason" value={edit.reason} onChange={e => setEdit({ ...edit, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary" disabled={(edit.reason ?? '').trim().length < 5}>Save</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
    </form>}
    <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Signal</th><th>Family</th><th>Formula</th><th>Parameters</th><th>Threshold</th><th>Timeframes</th><th>Status</th><th /></tr></thead><tbody>
      {(defs?.items ?? []).map((d: Def) => <tr key={d.id}><td><b>{d.code}</b><br /><small>{d.name}</small>{d.builtin && <> {pill('built-in', 'demo')}</>}</td><td><small>{d.family}</small></td>
        <td><code className="sig-eq">{d.equation}</code></td><td><small>{Object.entries(d.parameters ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')}</small></td><td>±{num(d.threshold, 0)}</td>
        <td><small>{(d.timeframes ?? []).join(' ')}</small></td><td>{pill(d.status, d.status === 'ACTIVE' ? 'ok' : 'off')}</td>
        <td>{ctx.canRisk && <span className="sig-acts"><button onClick={() => start(d)}>Edit</button><button onClick={() => void act(d, 'pause')}>{d.status === 'ACTIVE' ? 'Pause' : 'Resume'}</button>
          {!d.builtin && <button onClick={() => void act(d, 'delete')}>Delete</button>}</span>}</td></tr>)}
      {defs && !(defs.items ?? []).length && <tr><td colSpan={8} className="cfg-note">No signals in the library.</td></tr>}</tbody></table></div>
    <p className="cfg-note">Built-in signals can be tuned or paused, not deleted. A change starts a new version and clears the stored scores of that signal; Risk approvers change the library.</p>
  </section>;
}

export function SignalBoardLive() {
  const [tf, setTf] = useState('H1'), [data, error] = useData<Row>(`trading/signal-library/board?timeframe=${tf}`, 30_000, [tf]);
  const [cell, setCell] = useState<Row | null>(null);
  const items: Row[] = data?.items ?? [];
  const codes = useMemo(() => [...new Set(items.map(r => r.code as string))], [items]);
  const rows = useMemo(() => { const m = new Map<string, Row>(); items.forEach(r => { const k = `${r.account_code} · ${r.broker_symbol}`; const x = m.get(k) ?? { key: k, cells: {} as Row }; x.cells[r.code] = r; m.set(k, x); }); return [...m.values()]; }, [items]);
  return <section className="intel">
    <div className="intel-bar"><TfPick value={tf} change={v => { setTf(v); setCell(null); }} label="Signal board timeframe" />
      <small className="cfg-note">{rows.length} instruments × {codes.length} signals · recomputed after every closed bar · click a score for its working</small></div>
    <Msg error={error} />
    <div className="cfg-scroll"><table className="cfg-table compact intel-table sig-board"><thead><tr><th>Instrument</th>{codes.map(c => <th key={c} title={c}><small>{c.replace(/_/g, ' ')}</small></th>)}</tr></thead><tbody>
      {rows.map(r => <tr key={r.key}><td><b>{r.key}</b></td>{codes.map(c => { const x = r.cells[c];
        return <td key={c} className={`sig-cell ${scoreCls(x?.score)} ${cell && cell === x ? 'sel' : ''}`} onClick={() => x && setCell(x)} title={x?.worked}>{x ? fmtScore(x.score) : ''}</td>; })}</tr>)}
      {data && !rows.length && <tr><td colSpan={codes.length + 1} className="cfg-note">No scores on {tf} yet. They are written by the intelligence engine after each run for every collected instrument (Market intelligence › Engine settings: timeframes).</td></tr>}
    </tbody></table></div>
    {cell && <div className="sig-detail"><h4>{cell.code} · {cell.account_code} · {cell.broker_symbol} · {tf} <small className="cfg-note">bar {when(cell.bar_time)}</small></h4>
      <p><b className={Number(cell.score) > 0 ? 'up' : Number(cell.score) < 0 ? 'down' : ''}>{fmtScore(cell.score)}</b> → {pill(cell.direction, cell.direction === 'LONG' ? 'ok' : cell.direction === 'SHORT' ? 'live' : 'off')}
        <small className="cfg-note"> threshold ±{num(cell.threshold, 0)} · previous {fmtScore(cell.previous_score)}</small></p>
      <p className="sig-eqline"><code className="sig-eq">{cell.worked}</code></p>
      <Inputs inputs={cell.inputs} /></div>}
  </section>;
}

function Inputs({ inputs }: { inputs?: Row | null }) {
  if (!inputs) return null;
  return <div className="sig-inputs">{Object.entries(inputs).map(([k, v]) => <span key={k} className="sig-kv"><code>{k}</code> {fmtVal(v)}</span>)}</div>;
}

export function SignalExplain() {
  const [src, setSrc] = useState(''), [tf, setTf] = useState('H1');
  const [account, symbol] = src.split('|');
  const [data, error] = useData<Row>(src ? `trading/signal-library/explain?account_id=${account}&symbol=${encodeURIComponent(symbol)}&timeframe=${tf}` : null, 60_000, [src, tf]);
  return <section className="intel">
    <div className="intel-bar"><SourcePick value={src} change={setSrc} label="Explain instrument" /><TfPick value={tf} change={setTf} label="Explain timeframe" />
      {data && <small className="cfg-note">{data.bars} bars ({String(data.bar_source).toLowerCase().replace(/_/g, ' ')}) · last close {fmtVal(Number(data.last_close))} at {when(data.last_bar)}</small>}</div>
    <Msg error={error} />
    {!src && <p className="cfg-note">Choose an instrument: every active signal is computed from its stored bars, with the formula, the inputs on the last closed bar, the arithmetic and the last 60 scores.</p>}
    {data && <div className="sig-cards">{(data.items ?? []).map((r: Row) => <div key={r.code} className="sig-card">
      <header><b>{r.code}</b> <small className="cfg-note">{r.family}</small><span className={`sig-badge ${scoreCls(r.score)}`}>{fmtScore(r.score)}</span>{pill(r.direction, r.direction === 'LONG' ? 'ok' : r.direction === 'SHORT' ? 'live' : 'off')}</header>
      <p className="sig-eqline"><small>Formula</small><code className="sig-eq">{r.equation}</code></p>
      <p className="sig-eqline"><small>This bar</small><code className="sig-eq">{r.worked}</code></p>
      <Inputs inputs={r.inputs} />
      <MiniSpark values={r.recent ?? []} threshold={Number(r.threshold)} />
    </div>)}</div>}
  </section>;
}

export function SignalResearch() {
  const [cat] = useData<Row>('trading/signal-library/catalogue', 0);
  const kinds: Row[] = cat?.items ?? [];
  const [f, setF] = useState<Row>({ src: '', timeframe: 'H1', kind: 'EMA_TREND', parameters: {}, threshold: 20, days: 180, horizons: '1, 2, 5, 10, 20' });
  const [res, setRes] = useState<Row | null>(null), [err, setErr] = useState(''), [busy, setBusy] = useState(false);
  const kind = kinds.find(k => k.kind === f.kind);
  const run = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr('');
    const [account_id, symbol] = String(f.src).split('|');
    try { setRes(await api('trading/signal-library/research', 'POST', { account_id, symbol, timeframe: f.timeframe, kind: f.kind, parameters: f.parameters, threshold: Number(f.threshold),
      days: Number(f.days), horizons: String(f.horizons).split(/[ ,]+/).filter(Boolean).map(Number) })); } catch (x) { setErr((x as Error).message); setRes(null); } finally { setBusy(false); }
  };
  const best = res?.horizons?.find((h: Row) => h.horizon === res.best_horizon);
  return <section className="intel">
    <form className="cfg-form" onSubmit={run}>
      <div className="cfg-pair"><Field label="Instrument (collected bars)"><SourcePick value={f.src} change={v => setF({ ...f, src: v })} label="Research instrument" /></Field>
        <Field label="Timeframe"><TfPick value={f.timeframe} change={v => setF({ ...f, timeframe: v })} label="Research timeframe" /></Field></div>
      <Field label="Signal formula" hint={kind?.detail}><select aria-label="Research formula" value={f.kind} onChange={e => setF({ ...f, kind: e.target.value, parameters: {} })}>
        {kinds.map(k => <option key={k.kind} value={k.kind}>{k.label}</option>)}</select></Field>
      {kind && <p className="sig-eqline"><code className="sig-eq">{kind.equation}</code></p>}
      <div className="st-params">{Object.entries(kind?.defaults ?? {}).map(([p, d]) => <Field key={p} label={p.replace(/_/g, ' ')} hint={`default ${d}`}>
        <input aria-label={`Research parameter ${p}`} inputMode="decimal" placeholder={String(d)} value={f.parameters[p] ?? ''} onChange={e => setF({ ...f, parameters: { ...f.parameters, [p]: e.target.value === '' ? undefined : Number(e.target.value) } })} /></Field>)}
        <Field label="Threshold"><input aria-label="Research threshold" type="number" min={1} max={99} value={f.threshold} onChange={e => setF({ ...f, threshold: e.target.value })} /></Field>
        <Field label="Days of history"><input aria-label="Research days" type="number" min={1} max={3650} value={f.days} onChange={e => setF({ ...f, days: e.target.value })} /></Field>
        <Field label="Horizons (bars ahead)"><input aria-label="Research horizons" value={f.horizons} onChange={e => setF({ ...f, horizons: e.target.value })} /></Field></div>
      <div className="cfg-actions"><button className="primary" disabled={busy || !f.src}>{busy ? 'Researching…' : 'Research this signal'}</button></div>
    </form>
    <Msg error={err} />
    {res && <>
      <Kpis items={[['Verdict', res.verdict ?? '–', 'PREDICTIVE: |IC| ≥ 0.03, |t| ≥ 2 and the edge beats the spread', res.verdict === 'PREDICTIVE' ? 'up' : res.verdict === 'NO_EDGE' ? 'down' : 'warn'],
        ['Best horizon', res.best_horizon ? `${res.best_horizon} bars` : '–'], ['IC (best)', best?.ic ?? '–', 'Spearman rank correlation of score and forward return'], ['IC t-stat', best?.ic_t ?? '–'],
        ['Hit rate', best?.hit_rate_pct == null ? '–' : `${best.hit_rate_pct}%`], ['Edge / spread', `${best?.avg_signed_return_bp ?? '–'} / ${res.avg_spread_bp ?? '–'} bp`],
        ['Coverage', res.coverage_pct == null ? '–' : `${res.coverage_pct}%`, '|score| ≥ threshold'], ['Turnover / bar', res.turnover_per_bar ?? '–'], ['Bars', `${res.scored_bars} / ${res.bars}`]]} />
      <h4>By horizon</h4>
      <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Bars ahead</th><th>n</th><th>IC</th><th>t</th><th>Hit rate</th><th>Active bars</th><th>Avg return with the score (bp)</th><th>Avg |move| (bp)</th><th>Quintiles: avg return (bp), low score → high</th></tr></thead><tbody>
        {(res.horizons ?? []).map((h: Row) => <tr key={h.horizon} className={h.horizon === res.best_horizon ? 'sel' : ''}><td>{h.horizon}</td><td>{h.n}</td><td>{h.ic ?? h.note ?? '–'}</td><td>{h.ic_t ?? '–'}</td>
          <td>{h.hit_rate_pct == null ? '–' : `${h.hit_rate_pct}%`}</td><td>{h.active_bars ?? '–'}</td><td>{h.avg_signed_return_bp ?? '–'}</td><td>{h.avg_abs_move_bp ?? '–'}</td>
          <td><Quintiles q={h.quintiles ?? []} /></td></tr>)}</tbody></table></div>
      <ScoreChart series={res.series ?? []} threshold={Number(res.threshold)} />
      <p className="cfg-note">{res.how_to_read}</p>
      <p className="cfg-note">IC = ρ(rank(scoreₜ), rank(closeₜ₊ₕ ÷ closeₜ − 1)) · t = IC·√((n − 2) ÷ (1 − IC²)) · cost = average spread ÷ average close. Forward returns use later bars only here — never inside a score.</p>
    </>}
  </section>;
}

function Quintiles({ q }: { q: Row[] }) {
  if (!q.length) return <small className="cfg-note">–</small>;
  const m = Math.max(...q.map(x => Math.abs(Number(x.avg_return_bp))), 0.0001);
  return <span className="sig-quint">{q.map(x => <span key={x.quintile} title={`Q${x.quintile}: score ${x.score_from} → ${x.score_to}, ${x.n} bars, ${x.avg_return_bp} bp`}>
    <i className={Number(x.avg_return_bp) >= 0 ? 'up' : 'down'} style={{ height: `${Math.max(2, 18 * Math.abs(Number(x.avg_return_bp)) / m)}px` }} /><small>{x.avg_return_bp}</small></span>)}</span>;
}

function ScoreChart({ series, threshold }: { series: Row[]; threshold: number }) {
  if (series.length < 2) return null;
  const W = 760, H = 180, cs = series.map(s => Number(s.close)), lo = Math.min(...cs), hi = Math.max(...cs);
  const x = (i: number) => (i / (series.length - 1)) * W, yc = (v: number) => 8 + (1 - (v - lo) / ((hi - lo) || 1)) * (H / 2 - 16), ys = (v: number) => H * 0.75 - (v / 100) * (H / 4 - 6);
  const scored = series.map((s, i) => [i, s.score] as const).filter(([, v]) => v != null) as [number, number][];
  return <svg className="sig-chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Price and score">
    <polyline fill="none" strokeWidth="1.2" className="sig-price" points={cs.map((c, i) => `${x(i)},${yc(c)}`).join(' ')} />
    <line x1="0" x2={W} y1={ys(0)} y2={ys(0)} className="sig-axis" /><line x1="0" x2={W} y1={ys(threshold)} y2={ys(threshold)} className="sig-thr" /><line x1="0" x2={W} y1={ys(-threshold)} y2={ys(-threshold)} className="sig-thr" />
    <polyline fill="none" strokeWidth="1.2" className="sig-line" points={scored.map(([i, v]) => `${x(i)},${ys(v)}`).join(' ')} />
    <text x="4" y="12" className="sig-label">close</text><text x="4" y={H / 2 + 12} className="sig-label">score (−100…+100), threshold ±{threshold}</text></svg>;
}

/* ---------- Stage 4: the SIGNAL_BLEND recipe ---------- */

export function BlendEditor({ value, change }: { value: Row; change: (v: Row) => void }) {
  const [cat] = useData<Row>('trading/signal-library/catalogue', 0);
  const kinds: Row[] = cat?.items ?? [];
  const comps: Row[] = Array.isArray(value.components) && value.components.length ? value.components : [{ kind: 'EMA_TREND', parameters: {}, weight: 1 }];
  const entry = value.entry ?? 40, exit = value.exit ?? 10;
  const set = (c: Row[]) => change({ ...value, components: c, entry, exit });
  const [src, setSrc] = useState(''), [prev, setPrev] = useState<Row | null>(null), [err, setErr] = useState('');
  const totalW = comps.reduce((a, c) => a + Math.abs(Number(c.weight ?? 1)), 0);
  const recipe = `combined = (${comps.map(c => `${c.weight ?? 1} × ${c.kind}`).join(' + ')}) ÷ ${totalW || '…'}`;
  const sig = JSON.stringify([comps, entry, exit, value.timeframe]);
  useEffect(() => { setPrev(null); }, [sig]);
  const preview = async () => {
    const [account_id, symbol] = src.split('|');
    try { setPrev(await api('trading/signal-library/blend-preview', 'POST', { account_id, symbol, timeframe: value.timeframe ?? 'H1', components: comps.map(c => ({ ...c, weight: Number(c.weight ?? 1) })), entry: Number(entry), exit: Number(exit) })); setErr(''); }
    catch (x) { setErr((x as Error).message); setPrev(null); }
  };
  useEffect(() => { if (!value.components) set(comps); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="sig-blend">
    <p className="cfg-note">A strategy built from Stage 3 signals: each component is a library formula with its own parameters and a weight (negative inverts it). LONG when the combined score ≥ entry, SHORT when ≤ −entry, FLAT when it falls back inside ±exit.</p>
    <table className="cfg-table compact intel-table"><thead><tr><th>Signal formula</th><th>Parameters</th><th>Weight</th><th /></tr></thead><tbody>
      {comps.map((c, i) => { const k = kinds.find(x => x.kind === c.kind);
        return <tr key={i}><td><select aria-label={`Blend component ${i + 1}`} value={c.kind} onChange={e => set(comps.map((x, j) => j === i ? { ...x, kind: e.target.value, parameters: {} } : x))}>
          {kinds.map(x => <option key={x.kind} value={x.kind}>{x.label}</option>)}</select>{k && <><br /><code className="sig-eq">{k.equation}</code></>}</td>
          <td><span className="sig-params">{Object.entries(k?.defaults ?? {}).map(([p, d]) => <label key={p}><small>{p}</small><input aria-label={`Blend ${i + 1} ${p}`} inputMode="decimal" placeholder={String(d)} value={c.parameters?.[p] ?? ''}
            onChange={e => set(comps.map((x, j) => j === i ? { ...x, parameters: { ...(x.parameters ?? {}), [p]: e.target.value === '' ? undefined : Number(e.target.value) } } : x))} /></label>)}</span></td>
          <td><input aria-label={`Blend weight ${i + 1}`} type="number" step="0.1" min={-10} max={10} value={c.weight ?? 1} onChange={e => set(comps.map((x, j) => j === i ? { ...x, weight: e.target.value === '' ? 1 : Number(e.target.value) } : x))} /></td>
          <td>{comps.length > 1 && <button type="button" onClick={() => set(comps.filter((_, j) => j !== i))}>Remove</button>}</td></tr>; })}</tbody></table>
    <div className="intel-bar"><button type="button" onClick={() => set([...comps, { kind: kinds[0]?.kind ?? 'RSI_REVERSION', parameters: {}, weight: 1 }])}>+ Add signal</button>
      <label><small>Entry</small> <input aria-label="Blend entry" type="number" min={1} max={100} value={entry} onChange={e => change({ ...value, components: comps, entry: Number(e.target.value), exit })} /></label>
      <label><small>Exit</small> <input aria-label="Blend exit" type="number" min={0} max={99} value={exit} onChange={e => change({ ...value, components: comps, entry, exit: Number(e.target.value) })} /></label></div>
    <p className="sig-eqline"><small>Recipe</small><code className="sig-eq">{recipe}</code></p>
    <div className="intel-bar"><SourcePick value={src} change={setSrc} label="Blend preview instrument" /><button type="button" disabled={!src} onClick={() => void preview()}>Work it out on the last bar</button></div>
    <Msg error={err} />
    {prev && <div className="sig-detail">
      {(prev.components ?? []).map((c: Row, i: number) => <p key={i} className="sig-eqline"><small>{c.kind} × {c.weight}</small><code className="sig-eq">{c.worked}</code></p>)}
      <p className="sig-eqline"><small>Combined</small><code className="sig-eq">{prev.worked}</code></p>
      <p>{prev.combined == null ? 'Not enough bars yet.' : <>Combined <b className={prev.combined > 0 ? 'up' : prev.combined < 0 ? 'down' : ''}>{fmtScore(prev.combined)}</b> → {prev.side} <small className="cfg-note">(entry ±{prev.entry}, exit ±{prev.exit}; {prev.symbol} {prev.timeframe}, bar {when(prev.last_bar)})</small></>}</p>
    </div>}
  </div>;
}

/* Algorithm list with each rule's equation and strength formula (Stage 4 › Algorithms). */
export function AlgorithmEquations() {
  const [data] = useData<Row>('trading/algorithms', 0);
  return <section className="intel"><div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Algorithm</th><th>What it does</th><th>Rule (equation)</th><th>Strength</th><th>Parameters</th></tr></thead><tbody>
    {(data?.items ?? []).map((a: Row) => <tr key={a.id}><td><b>{a.label}</b><br /><code>{a.id}</code></td><td><small>{a.description}</small></td><td><code className="sig-eq">{a.equation ?? '—'}</code></td>
      <td><code className="sig-eq">{a.strength_equation ?? '—'}</code></td>
      <td><small>{Object.entries(a.schema?.properties ?? {}).map(([k, p]: [string, any]) => `${k}${p.default != null && typeof p.default !== 'object' ? ` (${p.default})` : ''}`).join(', ')}</small></td></tr>)}</tbody></table></div>
    <p className="cfg-note">Each version of a strategy names one algorithm and its parameters (Setup › Strategies). SIGNAL_BLEND combines Stage 3 library signals with weights; the rule builder takes conditions on any feature.</p></section>;
}

