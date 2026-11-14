/*
 * Market dashboard: live prices in workspaces.
 *   Workspaces (tabs): "All sources", one per account that has data or a collector, and your own (any set of
 *   accounts and symbols). Your workspaces follow you to other devices (operator preference md.workspaces).
 *   Each workspace: health strip · quote board (bid/ask/spread/day change/range/age/quality/5-minute trend) ·
 *   chart of the selected symbol (candles by timeframe, or ticks) · time & sales · the same instrument from
 *   every source (the quality-source comparison) · collectors and events of the workspace's accounts.
 * Data: md/latest (1 s), md/collectors (5 s), md/plans (15 s), md/events (10 s), md/bars and md/ticks for the
 * selected symbol. Polling pauses while the window is hidden.
 */
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api } from './Configuration';
import { eventSummary } from './MarketData';
import { digitsOf, goto } from './nav';

type Row = Record<string, any>;
type Workspace = { id: string; name: string; accounts: string[]; symbols: string[]; custom?: boolean };
const PREF = 'md.workspaces';
const FRAMES = ['TICK', 'S1', 'M1', 'M5', 'M15', 'H1', 'H4', 'D1'];
export const baseSymbol = (s: string) => { const u = String(s).toUpperCase().split(/[^A-Z0-9]/)[0]; return /^[A-Z]{6}[A-Z]{1,2}$/.test(u) && /^(XAU|XAG|EUR|GBP|USD|AUD|NZD|CAD|CHF|JPY)/.test(u) ? u.slice(0, 6) : u; };
const keyOf = (r: Row) => `${r.account_id}|${r.broker_symbol}`;
const fmt = (v: any, d: number) => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(d);
const ago = (s?: number | null) => s == null || !Number.isFinite(Number(s)) ? '—' : s < 1 ? 'now' : s < 60 ? `${Number(s).toFixed(s < 10 ? 1 : 0)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
const clock = (v?: string) => v ? new Date(v).toLocaleTimeString([], { hour12: false }) + '.' + String(new Date(v).getMilliseconds()).padStart(3, '0') : '—';

function useVisible() {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden);
  useEffect(() => { const on = () => setVisible(!document.hidden); document.addEventListener('visibilitychange', on); return () => document.removeEventListener('visibilitychange', on); }, []);
  return visible;
}

function usePoll<T = Row>(path: string | null, every: number): [T | null, string] {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState('');
  const visible = useVisible();
  useEffect(() => {
    if (!path) { setData(null); return; }
    let live = true;
    const load = async () => { try { const d = await api(path); if (live) { setData(d); setError(''); } } catch (e) { if (live) setError((e as Error).message); } };
    void load();
    if (!visible || !every) return () => { live = false; };
    const t = window.setInterval(() => void load(), every);
    return () => { live = false; window.clearInterval(t); };
  }, [path, every, visible]);
  return [data, error];
}

function loadWorkspaces(): Workspace[] {
  try { const x = JSON.parse(localStorage.getItem('corei-os-mini.' + PREF) || '[]'); return Array.isArray(x) ? x : []; } catch { return []; }
}

export default function MarketBoard() {
  const [latest, error] = usePoll<Row>('md/latest', 1000);
  const [collectors] = usePoll<Row>('md/collectors', 5000);
  const [plans] = usePoll<Row>('md/plans', 15000);
  const [events] = usePoll<Row>('md/events?limit=200', 10000);
  const [own, setOwn] = useState<Workspace[]>(loadWorkspaces);
  const [active, setActive] = useState<string>(() => { try { return localStorage.getItem('corei-os-mini.md.workspace') || 'all'; } catch { return 'all'; } });
  const [editing, setEditing] = useState<Workspace | null>(null);
  const [selected, setSelected] = useState<string>('');
  // Your workspaces follow you: load from the server once if this browser has none; save on change.
  useEffect(() => { if (own.length) return; void api('operator/prefs').then((p: Row) => { if (Array.isArray(p?.[PREF]) && p[PREF].length) setOwn(p[PREF]); }).catch(() => undefined); }, []);
  const saveOwn = (next: Workspace[]) => {
    setOwn(next);
    try { localStorage.setItem('corei-os-mini.' + PREF, JSON.stringify(next)); } catch { /* preference only */ }
    void api(`operator/prefs/${encodeURIComponent(PREF)}`, 'PUT', { value: next }).catch(() => undefined);
  };
  useEffect(() => { try { localStorage.setItem('corei-os-mini.md.workspace', active); } catch { /* preference only */ } }, [active]);

  const rows: Row[] = latest?.items ?? [];
  const accounts = useMemo(() => {
    const m = new Map<string, Row>();
    for (const c of collectors?.items ?? []) m.set(c.account_id, { id: c.account_id, code: c.account_code, name: c.account_name });
    for (const r of rows) if (!m.has(r.account_id)) m.set(r.account_id, { id: r.account_id, code: r.account_code, name: r.account_name });
    return [...m.values()].sort((a, b) => String(a.name || a.code).localeCompare(String(b.name || b.code)));
  }, [rows, collectors]);
  const workspaces: Workspace[] = [{ id: 'all', name: 'All sources', accounts: [], symbols: [] },
    ...accounts.map(a => ({ id: 'acct:' + a.id, name: a.name || a.code, accounts: [a.id], symbols: [] })), ...own.map(w => ({ ...w, custom: true }))];
  const ws = workspaces.find(w => w.id === active) ?? workspaces[0];
  const inWs = (r: Row) => (!ws.accounts.length || ws.accounts.includes(r.account_id)) && (!ws.symbols.length || ws.symbols.some(s => s.toUpperCase() === String(r.broker_symbol).toUpperCase()));
  const board = rows.filter(inWs);
  const planOf = useMemo(() => Object.fromEntries((plans?.items ?? []).map((p: Row) => [keyOf(p), p])), [plans]);
  const sel = board.find(r => keyOf(r) === selected) ?? board[0];

  // A short client-side history of mid prices per row, for the trend column and price flashes.
  const hist = useRef(new Map<string, number[]>()), prev = useRef(new Map<string, Row>()), [flash, setFlash] = useState<Record<string, string>>({});
  useEffect(() => {
    const f: Record<string, string> = {};
    for (const r of rows) {
      const k = keyOf(r), p = prev.current.get(k);
      if (r.bid != null && r.ask != null) { const h = hist.current.get(k) ?? []; if (!p || p.seq !== r.seq) h.push((Number(r.bid) + Number(r.ask)) / 2); if (h.length > 300) h.splice(0, h.length - 300); hist.current.set(k, h); }
      if (p && p.seq !== r.seq) { f[k + '|bid'] = Number(r.bid) > Number(p.bid) ? 'up' : Number(r.bid) < Number(p.bid) ? 'down' : ''; f[k + '|ask'] = Number(r.ask) > Number(p.ask) ? 'up' : Number(r.ask) < Number(p.ask) ? 'down' : ''; }
      prev.current.set(k, r);
    }
    setFlash(f);
  }, [latest]);

  const wsCollectors = (collectors?.items ?? []).filter((c: Row) => !ws.accounts.length || ws.accounts.includes(c.account_id));
  const wsEvents = (events?.items ?? []).filter((e: Row) => !ws.accounts.length || ws.accounts.includes(e.account_id)).slice(0, 40);
  const live = wsCollectors.filter((c: Row) => c.state === 'CONNECTED').length;
  const stale = board.filter(r => Number(r.age_seconds) > (planOf[keyOf(r)]?.stale_seconds ?? 10)).length;
  const scores = board.filter(r => r.score != null).map(r => Number(r.score)).filter(Number.isFinite);
  const problems = wsCollectors.filter((c: Row) => c.problem);

  return <div className="mb">
    <div className="mb-tabs" role="tablist" aria-label="Workspaces">
      {workspaces.map(w => <button key={w.id} role="tab" aria-selected={w.id === ws.id} onClick={() => setActive(w.id)} onDoubleClick={() => w.custom && setEditing(w)}
        title={w.custom ? 'Double-click to edit' : w.accounts.length ? 'Everything from this account' : 'Every account and symbol'}>
        {w.custom ? '★ ' : ''}{w.name}<small>{rows.filter(r => (!w.accounts.length || w.accounts.includes(r.account_id)) && (!w.symbols.length || w.symbols.some(s => s.toUpperCase() === String(r.broker_symbol).toUpperCase()))).length}</small></button>)}
      <button className="mb-new" onClick={() => setEditing({ id: 'ws' + Date.now().toString(36), name: '', accounts: ws.accounts, symbols: [], custom: true })} title="New workspace: choose accounts and symbols">+ Workspace</button>
      {ws.custom && <button className="mb-new" onClick={() => setEditing(ws)}>Edit</button>}
    </div>
    {editing && <WorkspaceForm ws={editing} accounts={accounts} symbols={[...new Set(rows.map(r => String(r.broker_symbol)))].sort()} close={() => setEditing(null)}
      save={w => { const exists = own.some(o => o.id === w.id); saveOwn(exists ? own.map(o => o.id === w.id ? w : o) : [...own, w]); setActive(w.id); setEditing(null); }}
      remove={own.some(o => o.id === editing.id) ? () => { saveOwn(own.filter(o => o.id !== editing.id)); setActive('all'); setEditing(null); } : undefined} />}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="mb-kpis">
      <Kpi label="Feeds live" value={`${live}/${wsCollectors.length}`} tone={wsCollectors.length && live === wsCollectors.length ? 'ok' : live ? 'warn' : 'bad'} />
      <Kpi label="Symbols" value={String(board.length)} />
      <Kpi label="Stale" value={String(stale)} tone={stale ? 'warn' : 'ok'} />
      <Kpi label="Avg quality" value={scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length).toString() : '—'} tone={!scores.length ? '' : Math.min(...scores) >= 80 ? 'ok' : 'warn'} />
      <Kpi label="Prices collected" value={wsCollectors.reduce((n: number, c: Row) => n + Number(c.ticks_total || 0), 0).toLocaleString()} />
      <Kpi label="Events (shown)" value={String(wsEvents.length)} tone={wsEvents.some((e: Row) => e.severity === 'CRITICAL') ? 'bad' : ''} />
    </div>
    {problems.map((c: Row) => <div key={c.account_id} className="mb-problem" role="alert"><b>{c.account_name || c.account_code}: {c.problem.title}</b> {c.problem.detail}
      <button className="link" onClick={() => goto('Configuration', 'brokers')}>Open Connectivity</button></div>)}
    <div className="mb-grid">
      <section className="mb-panel mb-board" aria-label="Quote board">
        <header><b>Quotes</b><span>{ws.name}</span></header>
        <div className="mb-scroll"><table className="mb-table"><thead><tr><th>Symbol</th><th>Source</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spr</th>
          <th className="num">Chg %</th><th>Day range</th><th className="num">Age</th><th className="num">Q</th><th>Trend</th></tr></thead>
          <tbody>{board.map(r => { const k = keyOf(r), d = digitsOf(r), p = planOf[k] ?? {}, mid = (Number(r.bid) + Number(r.ask)) / 2;
            const chg = r.day_open ? (mid / Number(r.day_open) - 1) * 100 : null, isStale = Number(r.age_seconds) > (p.stale_seconds ?? 10);
            return <tr key={k} className={k === keyOf(sel ?? {}) ? 'sel' : ''} onClick={() => setSelected(k)} tabIndex={0} onKeyDown={e => e.key === 'Enter' && setSelected(k)}>
              <td><b>{r.broker_symbol}</b>{p.feed_role && <small className={`mb-role ${p.active_feed ? 'on' : ''}`}>{p.feed_role === 'PRIMARY' ? 'P' : p.feed_role === 'BACKUP' ? 'B' : ''}</small>}</td>
              <td className="mb-src" title={r.account_code}>{r.account_name || r.account_code}</td>
              <td className={`num mono ${flash[k + '|bid'] ?? ''}`}>{fmt(r.bid, d)}</td><td className={`num mono ${flash[k + '|ask'] ?? ''}`}>{fmt(r.ask, d)}</td>
              <td className="num mono">{r.bid != null && r.ask != null ? Math.round((Number(r.ask) - Number(r.bid)) * 10 ** d) : '—'}</td>
              <td className={`num mono ${chg == null ? '' : chg >= 0 ? 'pos' : 'neg'}`}>{chg == null ? '—' : `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}`}</td>
              <td><Range low={r.day_low} high={r.day_high} value={mid} /></td>
              <td className={`num mono ${isStale ? 'neg' : ''}`}>{ago(r.age_seconds)}</td>
              <td className="num"><span className={`mb-q ${Number(r.score) >= 80 ? 'ok' : Number(r.score) >= 50 ? 'warn' : r.score == null ? '' : 'bad'}`}>{r.score ?? '—'}</span></td>
              <td><Spark points={hist.current.get(k) ?? []} /></td></tr>; })}
            {latest && !board.length && <tr><td colSpan={10} className="mb-empty">No prices in this workspace yet. Start a collection plan under Market data › Collection plans{problems.length ? ' — and see the problem above' : ''}.</td></tr>}</tbody></table></div>
      </section>
      <section className="mb-panel mb-chart" aria-label="Chart">{sel ? <Chart row={sel} /> : <p className="mb-empty">Select a symbol.</p>}</section>
      <section className="mb-panel" aria-label="Time and sales">{sel ? <Tape row={sel} /> : null}</section>
      <section className="mb-panel" aria-label="Sources">{sel ? <Sources row={sel} rows={rows} plans={planOf} /> : null}</section>
      <section className="mb-panel" aria-label="Feeds and events">
        <header><b>Feeds</b><span>{wsCollectors.length}</span></header>
        <div className="mb-scroll mb-feeds">{wsCollectors.map((c: Row) => <div key={c.account_id} className="mb-feed">
          <i className={`mb-dot ${c.state === 'CONNECTED' ? 'ok' : c.state === 'RECONNECTING' || c.state === 'STARTING' ? 'warn' : c.state === 'FAILED' ? 'bad' : ''}`} />
          <b>{c.account_name || c.account_code}</b><span>{c.state.toLowerCase()}</span>
          <small>{c.host ? `${c.host}:${c.port} · ` : ''}{c.lag_ms != null ? `lag ${Math.round(Number(c.lag_ms))} ms · ` : ''}{c.subscribed ?? 0} sym · hb {ago(c.heartbeat_age_seconds)}</small></div>)}
          {!wsCollectors.length && <p className="mb-empty">No collector for these accounts.</p>}
          <div className="mb-events">{wsEvents.map((e: Row) => <div key={e.id} className={`mb-ev ${String(e.severity).toLowerCase()}`}>
            <time>{new Date(e.at).toLocaleTimeString([], { hour12: false })}</time><b>{e.kind}</b><span>{[e.account_code, e.broker_symbol].filter(Boolean).join(' · ')} {eventSummary(e)}</span></div>)}</div>
        </div>
      </section>
    </div>
  </div>;
}

function Kpi({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className={`mb-kpi ${tone}`}><small>{label}</small><b>{value}</b></div>;
}

function Range({ low, high, value }: { low?: number; high?: number; value: number }) {
  if (low == null || high == null || !Number.isFinite(value) || high <= low) return <span className="mb-range empty" />;
  const pos = Math.max(0, Math.min(1, (value - low) / (high - low)));
  return <span className="mb-range" title={`Low ${low} · High ${high}`}><i style={{ left: `${pos * 100}%` }} /></span>;
}

function Spark({ points }: { points: number[] }) {
  if (points.length < 2) return <svg className="mb-spark" width="80" height="18" aria-hidden="true" />;
  const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1) * 80).toFixed(1)},${(17 - (p - lo) / span * 16).toFixed(1)}`).join('');
  return <svg className={`mb-spark ${points.at(-1)! >= points[0] ? 'up' : 'down'}`} width="80" height="18" viewBox="0 0 80 18" aria-hidden="true"><path d={d} /></svg>;
}

/* ---------- chart: candles by timeframe, or ticks ---------- */

function Chart({ row }: { row: Row }) {
  const [frame, setFrame] = useState<string>(() => { try { return localStorage.getItem('corei-os-mini.md.frame') || 'M1'; } catch { return 'M1'; } });
  useEffect(() => { try { localStorage.setItem('corei-os-mini.md.frame', frame); } catch { /* preference only */ } }, [frame]);
  const base = `${row.account_id}/${encodeURIComponent(row.broker_symbol)}`;
  const [bars] = usePoll<Row>(frame === 'TICK' ? null : `md/bars/${base}?timeframe=${frame}&limit=150`, frame === 'S1' ? 2000 : 10000);
  const [ticks] = usePoll<Row>(frame === 'TICK' ? `md/ticks/${base}?seconds=300&limit=1500` : null, 2000);
  const [hover, setHover] = useState<number | null>(null);
  const d = digitsOf(row);
  const W = 640, H = 260, PAD = 56;
  const candles: Row[] = frame === 'TICK' ? [] : [...(bars?.items ?? [])].reverse();
  const tks: Row[] = frame === 'TICK' ? (ticks?.items ?? []) : [];
  const values = frame === 'TICK' ? tks.flatMap(t => [t.bid, t.ask].filter(v => v != null).map(Number)) : candles.flatMap(c => [Number(c.high), Number(c.low)]);
  const lo = values.length ? Math.min(...values) : 0, hi = values.length ? Math.max(...values) : 1, span = hi - lo || Math.abs(hi) * 1e-4 || 1;
  const y = (v: number) => 8 + (1 - (v - lo) / span) * (H - 28);
  const n = frame === 'TICK' ? tks.length : candles.length;
  const x = (i: number) => 4 + (i + 0.5) * ((W - PAD - 8) / Math.max(n, 1));
  const cw = Math.max(1, (W - PAD - 8) / Math.max(n, 1) * 0.7);
  const grid = Array.from({ length: 5 }, (_, i) => lo + span * i / 4);
  const last = Number(row.bid);
  const pointAt = (i: number | null) => i == null ? null : frame === 'TICK' ? tks[i] : candles[i];
  const h = pointAt(hover);
  return <>
    <header><b>{row.broker_symbol}</b><span>{row.account_name || row.account_code}</span>
      <span className="mb-frames" role="group" aria-label="Timeframe">{FRAMES.map(f => <button key={f} aria-pressed={frame === f} onClick={() => setFrame(f)}>{f === 'TICK' ? 'Ticks' : f}</button>)}</span></header>
    <div className="mb-readout">{h ? frame === 'TICK' ? <>{clock(h.received_at)} · bid <b>{fmt(h.bid, d)}</b> ask <b>{fmt(h.ask, d)}</b></>
      : <>{new Date(h.bar_time).toLocaleString([], { hour12: false })} · O <b>{fmt(h.open, d)}</b> H <b>{fmt(h.high, d)}</b> L <b>{fmt(h.low, d)}</b> C <b>{fmt(h.close, d)}</b> · {h.ticks} ticks{h.source === 'BROKER' ? ' · broker history' : ''}</>
      : <>{n ? `${n} ${frame === 'TICK' ? 'prices, last 5 min' : 'bars'}` : 'No data for this timeframe yet'}</>}</div>
    <svg className="mb-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`${row.broker_symbol} chart`}
      onMouseMove={e => { const r = (e.currentTarget as SVGSVGElement).getBoundingClientRect(); const px = (e.clientX - r.left) / r.width * W; const i = Math.floor((px - 4) / ((W - PAD - 8) / Math.max(n, 1))); setHover(i >= 0 && i < n ? i : null); }}
      onMouseLeave={() => setHover(null)}>
      {grid.map(g => <g key={g}><line className="mb-gridline" x1={0} x2={W - PAD} y1={y(g)} y2={y(g)} /><text className="mb-axis" x={W - PAD + 4} y={y(g) + 3}>{g.toFixed(d)}</text></g>)}
      {frame === 'TICK' ? <>
        <path className="mb-line bid" d={tks.filter(t => t.bid != null).map((t, i) => `${i ? 'L' : 'M'}${x(tks.indexOf(t)).toFixed(1)},${y(Number(t.bid)).toFixed(1)}`).join('')} />
        <path className="mb-line ask" d={tks.filter(t => t.ask != null).map((t, i) => `${i ? 'L' : 'M'}${x(tks.indexOf(t)).toFixed(1)},${y(Number(t.ask)).toFixed(1)}`).join('')} />
      </> : candles.map((c, i) => { const up = Number(c.close) >= Number(c.open);
        return <g key={c.bar_time} className={up ? 'up' : 'down'}><line className="mb-wick" x1={x(i)} x2={x(i)} y1={y(Number(c.high))} y2={y(Number(c.low))} />
          <rect className="mb-body" x={x(i) - cw / 2} width={cw} y={y(Math.max(Number(c.open), Number(c.close)))} height={Math.max(1, Math.abs(y(Number(c.open)) - y(Number(c.close))))} /></g>; })}
      {Number.isFinite(last) && values.length > 0 && last >= lo && last <= hi && <g><line className="mb-last" x1={0} x2={W - PAD} y1={y(last)} y2={y(last)} />
        <rect className="mb-lastbox" x={W - PAD} y={y(last) - 7} width={PAD} height={14} /><text className="mb-lasttext" x={W - PAD + 4} y={y(last) + 4}>{last.toFixed(d)}</text></g>}
      {hover != null && <line className="mb-cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} />}
    </svg>
  </>;
}

/* ---------- time & sales ---------- */

function Tape({ row }: { row: Row }) {
  const [data] = usePoll<Row>(`md/ticks/${row.account_id}/${encodeURIComponent(row.broker_symbol)}?seconds=120&limit=200`, 2000);
  const d = digitsOf(row);
  const items: Row[] = [...(data?.items ?? [])].reverse();
  return <><header><b>Time &amp; sales</b><span>{row.broker_symbol} · last 2 min</span></header>
    <div className="mb-scroll"><table className="mb-table mb-tape"><thead><tr><th>Time</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spr</th><th className="num">Lat</th></tr></thead>
      <tbody>{items.map((t, i) => { const p = items[i + 1]; const dir = p ? Number(t.bid) > Number(p.bid) ? 'pos' : Number(t.bid) < Number(p.bid) ? 'neg' : '' : '';
        const lat = t.source_time ? new Date(t.received_at).getTime() - new Date(t.source_time).getTime() : null;
        return <tr key={t.received_at + i}><td className="mono">{clock(t.received_at)}</td><td className={`num mono ${dir}`}>{fmt(t.bid, d)}</td><td className="num mono">{fmt(t.ask, d)}</td>
          <td className="num mono">{t.bid != null && t.ask != null ? Math.round((Number(t.ask) - Number(t.bid)) * 10 ** d) : '—'}</td>
          <td className="num mono">{lat == null ? '—' : `${lat} ms`}</td></tr>; })}
        {data && !items.length && <tr><td colSpan={5} className="mb-empty">No prices in the last 2 minutes.</td></tr>}</tbody></table></div></>;
}

/* ---------- the same instrument from every source ---------- */

function Sources({ row, rows, plans }: { row: Row; rows: Row[]; plans: Record<string, Row> }) {
  const inst = plans[keyOf(row)]?.instrument_id;
  // Same instrument: linked by a plan, or the same base symbol once broker suffixes are dropped (XAUUSD.r, XAUUSDm, EURUSD-ECN).
  const same = rows.filter(r => (inst && plans[keyOf(r)]?.instrument_id === inst) || baseSymbol(r.broker_symbol) === baseSymbol(row.broker_symbol));
  const d = digitsOf(row), ref = same.find(r => plans[keyOf(r)]?.active_feed) ?? row, refMid = (Number(ref.bid) + Number(ref.ask)) / 2;
  return <><header><b>Sources</b><span>{plans[keyOf(row)]?.instrument_symbol ?? row.broker_symbol} · {same.length} source{same.length === 1 ? '' : 's'}</span></header>
    <div className="mb-scroll"><table className="mb-table"><thead><tr><th>Source</th><th>Role</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spr</th><th className="num">Δ pts</th><th className="num">Age</th><th className="num">Q</th></tr></thead>
      <tbody>{same.map(r => { const p = plans[keyOf(r)] ?? {}, mid = (Number(r.bid) + Number(r.ask)) / 2;
        return <tr key={keyOf(r)} className={keyOf(r) === keyOf(row) ? 'sel' : ''}><td className="mb-src">{r.account_name || r.account_code}</td>
          <td>{p.feed_role ? <span className={`mb-role ${p.active_feed ? 'on' : ''}`}>{p.feed_role.toLowerCase()}{p.active_feed ? ' · used' : ''}</span> : <span className="mb-dim">{(p.purpose ?? '').toLowerCase().replace('_', ' ') || '—'}</span>}</td>
          <td className="num mono">{fmt(r.bid, d)}</td><td className="num mono">{fmt(r.ask, d)}</td>
          <td className="num mono">{r.bid != null && r.ask != null ? Math.round((Number(r.ask) - Number(r.bid)) * 10 ** d) : '—'}</td>
          <td className="num mono">{r === ref || !Number.isFinite(mid - refMid) ? '—' : Math.round((mid - refMid) * 10 ** d)}</td>
          <td className="num mono">{ago(r.age_seconds)}</td><td className="num">{r.score ?? '—'}</td></tr>; })}</tbody></table>
      {same.length < 2 && <p className="mb-empty">One source only. Add a backup plan for this instrument on another account to compare and switch.</p>}</div></>;
}

/* ---------- workspace editor ---------- */

function WorkspaceForm({ ws, accounts, symbols, close, save, remove }: { ws: Workspace; accounts: Row[]; symbols: string[]; close: () => void; save: (w: Workspace) => void; remove?: () => void }) {
  const [f, setF] = useState<Workspace>({ ...ws, name: ws.name || 'My workspace' });
  const toggle = (list: string[], v: string) => list.includes(v) ? list.filter(x => x !== v) : [...list, v];
  return <form className="mb-wsform" onSubmit={(e: FormEvent) => { e.preventDefault(); save({ ...f, name: f.name.trim() || 'Workspace' }); }} aria-label="Workspace">
    <label>Name <input aria-label="Workspace name" maxLength={40} value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></label>
    <fieldset><legend>Accounts <small>(none = all)</small></legend>{accounts.map(a => <label key={a.id} className="mb-chip"><input type="checkbox" checked={f.accounts.includes(a.id)} onChange={() => setF({ ...f, accounts: toggle(f.accounts, a.id) })} />{a.name || a.code}</label>)}</fieldset>
    <fieldset><legend>Symbols <small>(none = all)</small></legend>{symbols.map(s => <label key={s} className="mb-chip"><input type="checkbox" checked={f.symbols.includes(s)} onChange={() => setF({ ...f, symbols: toggle(f.symbols, s) })} />{s}</label>)}
      {!symbols.length && <small className="mb-dim">Symbols appear once prices arrive.</small>}</fieldset>
    <div className="mb-wsactions">{remove && <button type="button" onClick={remove}>Delete</button>}<button type="button" onClick={close}>Cancel</button><button className="primary">Save</button></div>
  </form>;
}
