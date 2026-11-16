/* Stage 2 · Market intelligence workspace screens.
 * Why: the multi-broker, multi-account, multi-instrument views of the module: Sources (one instrument from every
 * broker account side by side), Correlation (how collected instruments move together), Strength (FX currencies),
 * Watchlists (named groups across accounts), Rules (conditions on features that raise alerts) and Calendar
 * (economic events and holidays: manual or CSV, used for the screener's event-risk flag).
 * Must never: change market data or orders; every change here is audited by the API. */
import { FormEvent, useEffect, useState } from 'react';
import { api } from './Configuration';

type Row = Record<string, any>;
const HTFS = ['M5', 'M15', 'H1', 'H4', 'D1'];
const num = (v: any, d = 2) => v == null || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d);
const when = (t?: string) => t ? new Date(t).toLocaleString() : '–';
const Err = ({ e }: { e: string }) => e ? <p role="alert" className="cfg-error">{e}</p> : null;
function useLoad<T>(path: string, deps: unknown[] = [], every = 30_000): [T | null, string, () => void] {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState('');
  const load = () => { void api(path).then(d => { setData(d); setError(''); }).catch(e => setError((e as Error).message)); };
  useEffect(() => { load(); const t = every ? window.setInterval(load, every) : 0; return () => window.clearInterval(t); }, [path, ...deps]);
  return [data, error, load];
}
const Tfs = ({ tf, set, list = HTFS }: { tf: string; set: (t: string) => void; list?: string[] }) =>
  <span className="intel-tfs" role="group" aria-label="Timeframe">{list.map(t => <button key={t} aria-pressed={t === tf} onClick={() => set(t)}>{t}</button>)}</span>;

/* ---------- Sources: one instrument from every broker account ---------- */
export function SourcesView() {
  const [list] = useLoad<Row>('intel/instruments', [], 0);
  const [id, setId] = useState('');
  useEffect(() => { if (!id && list?.items?.length) setId(list.items[0].id); }, [list]);
  const [data, error] = useLoad<Row>(id ? `intel/sources/${id}` : 'intel/instruments', [id], 10_000);
  const src = id && data?.items && data.instrument ? data : null;
  return <section className="intel">
    <div className="intel-bar">
      <select aria-label="Instrument" value={id} onChange={e => setId(e.target.value)}>
        {(list?.items ?? []).map((i: Row) => <option key={i.id} value={i.id}>{i.symbol} · {i.sources} source{i.sources === 1 ? '' : 's'}</option>)}</select>
      {src && <small className="cfg-note">Reference = median mid of the sources {num(src.reference_mid, 5)} · cheapest {src.best.cheapest ?? '–'} · fastest {src.best.fastest ?? '–'} · cleanest {src.best.cleanest ?? '–'}</small>}
    </div>
    <Err e={error} />
    {list && !list.items.length && <p className="cfg-note">No canonical instruments are being collected yet. Plans linked to an instrument (Market data › plans) appear here, one row per broker account.</p>}
    {src && <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Broker</th><th>Account</th><th>Symbol</th><th>Env.</th><th>Bid</th><th>Ask</th>
      <th>Spread</th><th title="Mid against the median of all sources, in basis points">Deviation bp</th><th>Quality 60m</th><th>Latency p50 / p95</th><th>Ticks 60m</th><th>Last price</th><th>H1 regime</th></tr></thead><tbody>
      {src.items.map((r: Row) => <tr key={r.account_id + r.broker_symbol}>
        <td>{r.provider_name}</td><td>{r.account_code}</td><td>{r.broker_symbol}</td><td>{r.environment}</td><td>{num(r.bid, 5)}</td><td>{num(r.ask, 5)}</td>
        <td>{num(r.spread, 5)}{src.best.cheapest === r.account_code ? ' ★' : ''}</td>
        <td className={Math.abs(r.deviation_bp ?? 0) > 5 ? 'down' : ''}>{num(r.deviation_bp, 2)}</td><td>{num(r.quality, 0)}{src.best.cleanest === r.account_code ? ' ★' : ''}</td>
        <td>{num(r.latency_p50_ms, 0)} / {num(r.latency_p95_ms, 0)} ms{src.best.fastest === r.account_code ? ' ★' : ''}</td><td>{r.ticks ?? '–'}</td><td><small>{when(r.received_at)}</small></td><td>{r.regime ?? '–'}</td></tr>)}
    </tbody></table></div>}
    <p className="cfg-note">★ best on that measure. A deviation above 5 bp is shown in red: one source's price differs from the others.</p>
  </section>;
}

/* ---------- Correlation ---------- */
export function CorrelationView() {
  const [tf, setTf] = useState('H1'), [win, setWin] = useState(100);
  const [data, error] = useLoad<Row>(`intel/correlation?timeframe=${tf}&window=${win}`, [], 60_000);
  const colour = (r: number | null) => r == null ? 'transparent' : r >= 0 ? `rgba(46,160,67,${Math.abs(r) * 0.8})` : `rgba(229,83,75,${Math.abs(r) * 0.8})`;
  return <section className="intel">
    <div className="intel-bar"><Tfs tf={tf} set={setTf} />
      <label>Bars <select aria-label="Window" value={win} onChange={e => setWin(Number(e.target.value))}>{[50, 100, 200, 500].map(n => <option key={n} value={n}>{n}</option>)}</select></label>
      <small className="cfg-note">Pearson correlation of log returns, one source per instrument (the cleanest), over the bars each pair has in common (at least 20).</small></div>
    <Err e={error} />
    {data && !data.labels.length && <p className="cfg-note">Not enough {tf} history yet (each instrument needs 21 bars). Get history on the Dashboard.</p>}
    {data && data.labels?.length > 0 && <div className="cfg-scroll"><table className="cfg-table compact intel-corr"><thead><tr><th />{data.labels.map((l: Row) => <th key={l.broker_symbol + l.account_id}>{l.name}</th>)}</tr></thead>
      <tbody>{data.labels.map((l: Row, i: number) => <tr key={l.broker_symbol + l.account_id}><th>{l.name}</th>{data.matrix[i].map((c: Row, j: number) =>
        <td key={j} style={{ background: colour(c.r) }} title={`${c.n} common bars`}>{c.r == null ? '–' : c.r.toFixed(2)}</td>)}</tr>)}</tbody></table></div>}
    {data && data.skipped?.length > 0 && <p className="cfg-note">Not enough bars yet: {data.skipped.map((s: Row) => `${s.broker_symbol} (${s.bars})`).join(', ')}.</p>}
  </section>;
}

/* ---------- Currency strength ---------- */
export function StrengthView() {
  const [tf, setTf] = useState('H1'), [bars, setBars] = useState(20);
  const [data, error] = useLoad<Row>(`intel/strength?timeframe=${tf}&bars=${bars}`, [], 60_000);
  const max = Math.max(0.0001, ...(data?.currencies ?? []).map((c: Row) => Math.abs(c.strength_pct)));
  return <section className="intel">
    <div className="intel-bar"><Tfs tf={tf} set={setTf} />
      <label>Over <select aria-label="Bars" value={bars} onChange={e => setBars(Number(e.target.value))}>{[5, 10, 20, 50, 100].map(n => <option key={n} value={n}>{n} bars</option>)}</select></label>
      <small className="cfg-note">Each currency's average move against the others, from every FX pair collected.</small></div>
    <Err e={error} />{data?.note && <p className="cfg-note">{data.note}</p>}
    <div className="intel-strength">{(data?.currencies ?? []).map((c: Row) => <div key={c.currency} className="intel-strength-row">
      <b>{c.currency}</b><span className="bar"><i className={c.strength_pct >= 0 ? 'up' : 'down'} style={{ width: `${50 * Math.abs(c.strength_pct) / max}%`, [c.strength_pct >= 0 ? 'left' : 'right']: '50%' } as any} /></span>
      <span className={c.strength_pct >= 0 ? 'up' : 'down'}>{c.strength_pct >= 0 ? '+' : ''}{num(c.strength_pct, 3)} %</span><small className="cfg-note">{c.pairs} pairs</small></div>)}</div>
  </section>;
}

/* ---------- Watchlists ---------- */
export function WatchlistsView({ canEdit }: { canEdit: boolean }) {
  const [data, error, reload] = useLoad<Row>('intel/watchlists', [], 0);
  const [universe] = useLoad<Row>('intel/screener?timeframe=H1', [], 0);
  const [edit, setEdit] = useState<Row | null>(null), [err, setErr] = useState('');
  const items: Row[] = universe?.items ?? [];
  const save = async (e: FormEvent) => {
    e.preventDefault(); if (!edit) return;
    try { const body = { name: edit.name, items: edit.items, notes: edit.notes ?? '' };
      if (edit.id) await api(`intel/watchlists/${edit.id}`, 'PUT', { ...body, version: edit.version }); else await api('intel/watchlists', 'POST', body);
      setEdit(null); setErr(''); reload(); } catch (x) { setErr((x as Error).message); }
  };
  const has = (r: Row) => (edit?.items ?? []).some((x: Row) => x.account_id === r.account_id && x.broker_symbol === r.broker_symbol);
  return <section className="intel">
    <div className="intel-bar">{canEdit && <button className="primary" onClick={() => setEdit({ name: '', items: [], notes: '' })}>New watchlist</button>}
      <small className="cfg-note">A watchlist holds a broker account's symbol, so the same instrument from two brokers can be watched separately. Use it to filter the Screener and to scope Rules.</small></div>
    <Err e={error || err} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Name</th><th>Instruments</th><th>Notes</th><th>Changed</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((w: Row) => <tr key={w.id}><td><strong>{w.name}</strong></td><td>{w.items.map((x: Row) => x.broker_symbol).join(', ') || '–'}</td><td>{w.notes}</td>
        <td><small>{when(w.updated_at)} · {w.updated_by}</small></td>
        <td>{canEdit && <><button onClick={() => setEdit({ ...w })}>Edit</button> <button onClick={() => { if (window.confirm(`Delete watchlist ${w.name}?`)) void api(`intel/watchlists/${w.id}`, 'DELETE').then(reload).catch(x => setErr((x as Error).message)); }}>Delete</button></>}</td></tr>)}
      {data && !data.items.length && <tr><td colSpan={5} className="cfg-note">No watchlists yet.</td></tr>}</tbody></table>
    {edit && <form className="cfg-form intel-edit" onSubmit={save}>
      <label>Name <input aria-label="Watchlist name" required maxLength={80} value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></label>
      <div className="intel-checks">{items.map(r => <label key={r.account_id + r.broker_symbol} className="intel-check"><input type="checkbox" checked={has(r)}
        onChange={e => setEdit({ ...edit, items: e.target.checked ? [...edit.items, { account_id: r.account_id, broker_symbol: r.broker_symbol }]
          : edit.items.filter((x: Row) => !(x.account_id === r.account_id && x.broker_symbol === r.broker_symbol)) })} /> {r.broker_symbol} <small className="cfg-note">{r.provider_name} · {r.account_code}</small></label>)}</div>
      <label>Notes <input aria-label="Watchlist notes" maxLength={500} value={edit.notes ?? ''} onChange={e => setEdit({ ...edit, notes: e.target.value })} /></label>
      <div className="cfg-actions"><button className="primary">Save watchlist</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
    </form>}
  </section>;
}

/* ---------- Rules & alerts ---------- */
const OPS: [string, string][] = [['>', 'is above'], ['<', 'is below'], ['>=', 'is at or above'], ['<=', 'is at or below'], ['CROSSES_ABOVE', 'crosses above'], ['CROSSES_BELOW', 'crosses below'], ['BECOMES', 'becomes']];
export function RulesView({ canEdit }: { canEdit: boolean }) {
  const [data, error, reload] = useLoad<Row>('intel/rules', [], 0);
  const [fires] = useLoad<Row>('intel/fires?limit=50', [], 30_000);
  const [lists] = useLoad<Row>('intel/watchlists', [], 0);
  const [edit, setEdit] = useState<Row | null>(null), [err, setErr] = useState('');
  const blank = { name: '', enabled: true, timeframe: 'H1', field: 'rsi14', op: 'CROSSES_BELOW', value: '30', severity: 'INFO', cooldown_minutes: 60, scope: {}, notes: '' };
  const save = async (e: FormEvent) => {
    e.preventDefault(); if (!edit) return;
    const body = { ...edit }; delete body.id; delete body.version; delete body.fires; delete body.last_fired_at;
    for (const k of ['created_at', 'created_by', 'updated_at', 'updated_by']) delete body[k];
    try { if (edit.id) await api(`intel/rules/${edit.id}`, 'PUT', { ...body, version: edit.version }); else await api('intel/rules', 'POST', body);
      setEdit(null); setErr(''); reload(); } catch (x) { setErr((x as Error).message); }
  };
  const cat = edit && ['regime', 'volatility'].includes(edit.field);
  return <section className="intel">
    <div className="intel-bar">{canEdit && <button className="primary" onClick={() => setEdit({ ...blank })}>New rule</button>}
      <small className="cfg-note">A rule is checked on every newly closed bar of its timeframe and fires once per bar (cooldown per instrument). Fired rules are listed below and queued to the alert channels subscribed to INTEL_RULE (Setup › Alerts).</small></div>
    <Err e={error || err} />
    <table className="cfg-table compact intel-table"><thead><tr><th>Rule</th><th>Condition</th><th>Scope</th><th>Severity</th><th>Cooldown</th><th>Fired</th><th>On</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((r: Row) => <tr key={r.id}><td><strong>{r.name}</strong></td>
        <td>{r.timeframe} · {r.field} {(OPS.find(o => o[0] === r.op) ?? [r.op, r.op])[1]} {r.value}</td>
        <td>{r.scope?.watchlist_id ? `watchlist ${(lists?.items ?? []).find((w: Row) => w.id === r.scope.watchlist_id)?.name ?? '?'}` : r.scope?.broker_symbol ?? 'every instrument'}</td>
        <td>{r.severity.toLowerCase()}</td><td>{r.cooldown_minutes} min</td><td>{r.fires}{r.last_fired_at ? <small className="cfg-note"> · {when(r.last_fired_at)}</small> : null}</td>
        <td>{r.enabled ? 'on' : 'off'}</td>
        <td>{canEdit && <><button onClick={() => setEdit({ ...r })}>Edit</button> <button onClick={() => { if (window.confirm(`Delete rule ${r.name}?`)) void api(`intel/rules/${r.id}`, 'DELETE').then(reload).catch(x => setErr((x as Error).message)); }}>Delete</button></>}</td></tr>)}
      {data && !data.items.length && <tr><td colSpan={8} className="cfg-note">No rules yet. Examples: RSI 14 on H1 crosses below 30 · regime on H4 becomes TREND_UP · ATR% rank on M15 is above 90 · z-score on H1 is above 2.</td></tr>}</tbody></table>
    {edit && <form className="cfg-form intel-edit" onSubmit={save}>
      <label>Name <input aria-label="Rule name" required maxLength={80} value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></label>
      <label>Timeframe <select aria-label="Rule timeframe" value={edit.timeframe} onChange={e => setEdit({ ...edit, timeframe: e.target.value })}>{['M1', ...HTFS].map(t => <option key={t}>{t}</option>)}</select></label>
      <label>Feature <select aria-label="Rule feature" value={edit.field} onChange={e => setEdit({ ...edit, field: e.target.value, op: ['regime', 'volatility'].includes(e.target.value) ? 'BECOMES' : edit.op === 'BECOMES' ? '>' : edit.op })}>
        {(data?.fields ?? []).map((f: string) => <option key={f}>{f}</option>)}</select></label>
      <label>Condition <select aria-label="Rule condition" value={edit.op} onChange={e => setEdit({ ...edit, op: e.target.value })}>{OPS.filter(o => cat ? o[0] === 'BECOMES' : o[0] !== 'BECOMES').map(o => <option key={o[0]} value={o[0]}>{o[1]}</option>)}</select></label>
      <label>Value {cat ? <select aria-label="Rule value" value={edit.value} onChange={e => setEdit({ ...edit, value: e.target.value })}>
        {(edit.field === 'regime' ? ['TREND_UP', 'TREND_DOWN', 'RANGE', 'TRANSITION'] : ['HIGH', 'NORMAL', 'LOW']).map(v => <option key={v}>{v}</option>)}</select>
        : <input aria-label="Rule value" required value={edit.value} onChange={e => setEdit({ ...edit, value: e.target.value })} />}</label>
      <label>Scope <select aria-label="Rule scope" value={edit.scope?.watchlist_id ?? ''} onChange={e => setEdit({ ...edit, scope: e.target.value ? { watchlist_id: e.target.value } : {} })}>
        <option value="">Every instrument</option>{(lists?.items ?? []).map((w: Row) => <option key={w.id} value={w.id}>Watchlist: {w.name}</option>)}</select></label>
      <label>Severity <select aria-label="Rule severity" value={edit.severity} onChange={e => setEdit({ ...edit, severity: e.target.value })}>{['INFO', 'WARNING', 'CRITICAL'].map(v => <option key={v}>{v}</option>)}</select></label>
      <label>Cooldown (minutes) <input aria-label="Rule cooldown" type="number" min={0} max={10080} value={edit.cooldown_minutes} onChange={e => setEdit({ ...edit, cooldown_minutes: Number(e.target.value) })} /></label>
      <label><input type="checkbox" checked={!!edit.enabled} onChange={e => setEdit({ ...edit, enabled: e.target.checked })} /> On</label>
      <div className="cfg-actions"><button className="primary">Save rule</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
    </form>}
    <h4>Fired</h4>
    <table className="cfg-table compact intel-table"><thead><tr><th>When</th><th>Rule</th><th>Instrument</th><th>Bar (UTC)</th><th>Message</th></tr></thead><tbody>
      {(fires?.items ?? []).map((f: Row) => <tr key={f.id}><td><small>{when(f.fired_at)}</small></td><td>{f.rule_name}</td><td>{f.broker_symbol} <small className="cfg-note">{f.account_code}</small></td>
        <td><small>{new Date(f.bar_time).toISOString().slice(5, 16).replace('T', ' ')}</small></td><td>{f.message}</td></tr>)}
      {fires && !fires.items.length && <tr><td colSpan={5} className="cfg-note">Nothing fired yet.</td></tr>}</tbody></table>
  </section>;
}

/* ---------- Calendar ---------- */
export function CalendarView({ canEdit }: { canEdit: boolean }) {
  const [ahead, setAhead] = useState(7);
  const [data, error, reload] = useLoad<Row>(`intel/events?days_back=1&days_ahead=${ahead}`, [], 60_000);
  const [form, setForm] = useState<Row>({ at: '', currency: 'USD', impact: 'HIGH', title: '', forecast: '', previous: '' });
  const [csv, setCsv] = useState(''), [err, setErr] = useState(''), [notice, setNotice] = useState('');
  const add = async (e: FormEvent) => {
    e.preventDefault();
    try { await api('intel/events', 'POST', { ...form, at: new Date(form.at).toISOString() }); setNotice('Event added.'); setErr(''); setForm({ ...form, title: '' }); reload(); }
    catch (x) { setErr((x as Error).message); }
  };
  const importCsv = async () => { try { const r = await api('intel/events/import', 'POST', { csv }); setNotice(`${r.added} events imported.`); setErr(''); setCsv(''); reload(); } catch (x) { setErr((x as Error).message); } };
  const readFile = (f?: File) => { if (f) void f.text().then(setCsv); };
  return <section className="intel">
    <div className="intel-bar"><label>Show next <select aria-label="Days ahead" value={ahead} onChange={e => setAhead(Number(e.target.value))}>{[1, 3, 7, 14, 30].map(n => <option key={n} value={n}>{n} days</option>)}</select></label>
      <small className="cfg-note">High-impact events and holidays mark instruments in those currencies with event risk (Screener) within the window set on the Dashboard. Times are UTC; shown in your local time.</small></div>
    <Err e={error || err} />{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <table className="cfg-table compact intel-table"><thead><tr><th>When</th><th>Currency</th><th>Impact</th><th>Event</th><th>Forecast</th><th>Previous</th><th>Actual</th><th>Source</th><th /></tr></thead><tbody>
      {(data?.items ?? []).map((e: Row) => <tr key={e.id} className={new Date(e.at).getTime() < Date.now() ? 'muted' : ''}><td>{when(e.at)}</td><td>{e.currency}</td>
        <td><span className={`cfg-pill ${e.impact === 'HIGH' ? 'live' : e.impact === 'MEDIUM' ? 'warn' : e.impact === 'HOLIDAY' ? 'demo' : 'off'}`}>{e.impact.toLowerCase()}</span></td>
        <td>{e.title}</td><td>{e.forecast}</td><td>{e.previous}</td><td>{e.actual}</td><td><small>{e.source.toLowerCase()}</small></td>
        <td>{canEdit && <button onClick={() => { if (window.confirm(`Delete "${e.title}"?`)) void api(`intel/events/${e.id}`, 'DELETE').then(reload).catch(x => setErr((x as Error).message)); }}>Delete</button>}</td></tr>)}
      {data && !data.items.length && <tr><td colSpan={9} className="cfg-note">No events in this period. Add them below or import a CSV.</td></tr>}</tbody></table>
    {canEdit && <div className="intel-cols">
      <form className="cfg-form" onSubmit={add}><h4>Add an event</h4>
        <label>When (your local time) <input aria-label="Event time" type="datetime-local" required value={form.at} onChange={e => setForm({ ...form, at: e.target.value })} /></label>
        <label>Currency <input aria-label="Event currency" required pattern="[A-Z]{3}|ALL" maxLength={3} value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value.toUpperCase() })} /></label>
        <label>Impact <select aria-label="Event impact" value={form.impact} onChange={e => setForm({ ...form, impact: e.target.value })}>{['HIGH', 'MEDIUM', 'LOW', 'HOLIDAY'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label>Event <input aria-label="Event title" required maxLength={200} value={form.title} onChange={e => setForm({ ...form, title: e.target.value })} /></label>
        <label>Forecast <input aria-label="Event forecast" maxLength={40} value={form.forecast} onChange={e => setForm({ ...form, forecast: e.target.value })} /></label>
        <label>Previous <input aria-label="Event previous" maxLength={40} value={form.previous} onChange={e => setForm({ ...form, previous: e.target.value })} /></label>
        <div className="cfg-actions"><button className="primary">Add event</button></div></form>
      <div className="cfg-form"><h4>Import a CSV</h4>
        <p className="cfg-note">First line: <code>datetime_utc,currency,impact,title,forecast,previous,actual</code>. Example: <code>2026-11-20 13:30,USD,HIGH,CPI y/y,3.1%,3.0%,</code>. Every line must be valid or nothing is imported (the errors are listed). Up to about 600 lines per file.</p>
        <input aria-label="CSV file" type="file" accept=".csv,text/csv" onChange={e => readFile(e.target.files?.[0])} />
        <textarea aria-label="CSV text" rows={6} value={csv} onChange={e => setCsv(e.target.value)} placeholder="or paste the CSV here" />
        <div className="cfg-actions"><button className="primary" disabled={!csv.trim()} onClick={() => void importCsv()}>Import</button></div></div>
    </div>}
  </section>;
}
