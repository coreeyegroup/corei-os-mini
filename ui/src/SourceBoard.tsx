/*
 * Stage 1 · Market data dashboard: workspaces › sources › instruments. Collect and watch; no analysis here
 * (selection, comparison and charts across sources are Stage 2 · Market intelligence).
 *
 *   Workspace (tabs): an ordered list of sources, or "all sources". New, duplicate, one per source, rename, close.
 *     Controls: add/remove sources, columns, status filter, minimum quality, asset class, search, collapse/expand,
 *     refresh rate, local/UTC, pause display, CSV export. Saved for the user (operator preference md.sources.v1).
 *   Source section: provider · technology · server · environment · collector state, address, lag, prices/min,
 *     quality; problem in plain words. Actions: + Instrument (every collection-plan option), pause/resume the
 *     source's collection, backfill history, details, open in Connectivity, move up/down, remove, collapse.
 *   Instrument rows: live quote and health columns (choose, sort), plan status, what is stored; actions: chart +
 *     time & sales, settings (full plan editor), pause/resume, remove.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { api } from './Configuration';
import { BLANK, PlanWindow, eventSummary } from './MarketData';
import { Chart, COLS, Kpi, Picker, Seg, Tape, ago, classOf, CLASS_LABEL, instOf, isStale, keyOf, mid, setTz, usePoll, type Col, type Ctx, type Row } from './MarketBoard';
import { goto } from './nav';

type SourceWs = {
  id: string; name: string; all: boolean; sources: string[]; collapsed: string[];
  columns: string[]; sort: { col: string; dir: 1 | -1 }; status: '' | 'live' | 'stale' | 'nodata' | 'paused'; minQuality: number;
  classes: string[]; search: string; rate: number; tz: 'local' | 'utc';
};
const PREF = 'md.sources.v1';
const planCol = (id: string, label: string, title: string, value: (r: Row) => any, cell: (r: Row) => React.ReactNode): Col => ({ id, label, title, value, cell: r => cell(r) });
const PLAN_STATUS: Record<string, string> = { ACTIVE: 'collecting', PAUSED: 'paused', DRAFT: 'draft', ARCHIVED: 'removed' };
const EXTRA: Col[] = [
  planCol('plan', 'Plan', 'Collection plan status', r => r.plan_status ?? 'NONE', r => r.plan_status ? <span className={`sb-plan ${r.plan_status}`}>{PLAN_STATUS[r.plan_status] ?? r.plan_status}{r.pending_changes > 0 ? ' · approval' : ''}</span> : <span className="mb-dim">no plan</span>),
  planCol('purpose', 'Purpose', 'Purpose and feed role', r => `${r.plan_purpose ?? ''}${r.feed_role ?? ''}`, r => r.plan_purpose ? <span className="mb-dim">{r.plan_purpose.replace('_', ' ').toLowerCase()}{r.plan_purpose === 'TRADING_FEED' ? ` · ${String(r.feed_role).toLowerCase()}${r.active_feed ? ' (used)' : ''}` : ''}</span> : '—'),
  planCol('stored', 'Stored', 'What is kept: quotes, bars (timeframes), depth', r => (r.data_types ?? []).join(), r => r.data_types ? <span className="mb-dim">{r.data_types.map((d: string) => d === 'BARS' ? `bars ${r.bar_timeframes?.join(',')}` : d === 'DEPTH' ? `depth ${r.depth_levels}` : 'quotes').join(' · ')}</span> : '—'),
  planCol('keep', 'Keep', 'Days kept in the database, then archive', r => r.hot_days ?? -1, r => r.hot_days ? <span className="mb-dim">{r.hot_days} d{r.archive ? ' → archive' : ''}</span> : '—'),
  planCol('schedule', 'When', 'Collection schedule', r => r.schedule ?? '', r => r.schedule ? <span className="mb-dim">{String(r.schedule).replace('_', ' ').toLowerCase()}</span> : '—'),
];
const TABLE_COLS: Col[] = [...COLS.filter(c => !['source', 'provider', 'tech', 'env'].includes(c.id)), ...EXTRA];
const DEFAULT_COLS = ['symbol', 'instrument', 'plan', 'bid', 'ask', 'spread', 'chg', 'range', 'age', 'latency', 'ticks', 'quality', 'stored', 'trend'];
const blank = (patch: Partial<SourceWs> = {}): SourceWs => ({ id: 'sw' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), name: 'All sources', all: true, sources: [], collapsed: [],
  columns: DEFAULT_COLS, sort: { col: 'symbol', dir: 1 }, status: '', minQuality: 0, classes: [], search: '', rate: 1000, tz: 'local', ...patch });
function load(): SourceWs[] {
  try { const x = JSON.parse(localStorage.getItem('corei-os-mini.' + PREF) || 'null'); if (Array.isArray(x) && x.length) return x.map(w => blank(w)); } catch { /* none */ }
  return [blank({ id: 'all' })];
}

export default function SourceBoard({ canEdit }: { canEdit: boolean }) {
  const [list, setList] = useState<SourceWs[]>(load);
  const [active, setActive] = useState<string>(() => { try { return localStorage.getItem('corei-os-mini.md.sources.active') || 'all'; } catch { return 'all'; } });
  const ws = list.find(w => w.id === active) ?? list[0];
  setTz(ws.tz);
  const [frozen, setFrozen] = useState(false), [renaming, setRenaming] = useState(false);
  const [latest, error] = usePoll<Row>('md/latest', frozen ? 0 : ws.rate);
  const [srcData] = usePoll<Row>('md/sources', 10000);
  const [collectors] = usePoll<Row>('md/collectors', 3000);
  const [plansData, , ] = usePoll<Row>('md/plans', 5000);
  const [controls] = usePoll<Row>('md/controls', 10000);
  const [events] = usePoll<Row>('md/events?limit=300', 10000);
  const [instruments] = usePoll<Row>('config/instruments', 0);
  const [accounts] = usePoll<Row>('config/accounts', 0);
  const [nonce, setNonce] = useState(0);
  const [plansFresh] = usePoll<Row>(nonce ? `md/plans?n=${nonce}` : null, 0);
  const plans: Row[] = (plansFresh ?? plansData)?.items ?? [];
  const [editing, setEditing] = useState<Row | null>(null), [expanded, setExpanded] = useState(''), [notice, setNotice] = useState(''), [actionError, setActionError] = useState('');
  useEffect(() => { if (list.length > 1) return; void api('operator/prefs').then((p: Row) => { if (Array.isArray(p?.[PREF]) && p[PREF].length) setList(p[PREF].map((w: Row) => blank(w))); }).catch(() => undefined); }, []);
  const timer = useRef<number | undefined>(undefined);
  const commit = (next: SourceWs[]) => {
    setList(next);
    try { localStorage.setItem('corei-os-mini.' + PREF, JSON.stringify(next)); } catch { /* preference only */ }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => void api(`operator/prefs/${encodeURIComponent(PREF)}`, 'PUT', { value: next }).catch(() => undefined), 800);
  };
  const update = (patch: Partial<SourceWs>) => commit(list.map(w => w.id === ws.id ? { ...w, ...patch } : w));
  useEffect(() => { try { localStorage.setItem('corei-os-mini.md.sources.active', ws.id); } catch { /* preference only */ } }, [ws.id]);
  const refresh = () => setNonce(n => n + 1);

  const sources: Row[] = srcData?.items ?? [];
  const byId = useMemo(() => Object.fromEntries(sources.map(s => [s.account_id, s])), [srcData]);
  const coll = useMemo(() => Object.fromEntries((collectors?.items ?? []).map((c: Row) => [c.account_id, c])), [collectors]);
  const ctl = useMemo(() => Object.fromEntries((controls?.items ?? []).map((c: Row) => [c.scope, c])), [controls]);
  const shown: Row[] = ws.all ? sources : ws.sources.map(id => byId[id]).filter(Boolean);
  const priced: Row[] = latest?.items ?? [];

  // Rows per source: every plan (with its live price if any) and any priced symbol without a plan.
  const rowsBySource = useMemo(() => {
    const out: Record<string, Row[]> = {};
    const priceOf = Object.fromEntries(priced.map(r => [keyOf(r), r]));
    for (const p of plans.filter(p => p.status !== 'ARCHIVED')) {
      const l = priceOf[keyOf(p)];
      (out[p.account_id] ??= []).push({ ...(l ?? {}), account_id: p.account_id, broker_symbol: p.broker_symbol, noData: !l, plan: p, plan_id: p.id, plan_status: p.status,
        plan_purpose: p.purpose, feed_role: p.feed_role, active_feed: p.active_feed, instrument_id: p.instrument_id, instrument_symbol: p.instrument_symbol ?? l?.instrument_symbol,
        data_types: p.data_types, bar_timeframes: p.bar_timeframes, depth_levels: p.depth_levels, hot_days: p.hot_days, archive: p.archive, schedule: p.schedule,
        stale_seconds: p.stale_seconds, pending_changes: p.pending_changes, digits: l?.digits ?? p.digits });
    }
    const planned = new Set(plans.filter(p => p.status !== 'ARCHIVED').map(keyOf));
    for (const r of priced) if (!planned.has(keyOf(r))) (out[r.account_id] ??= []).push(r);
    return out;
  }, [latest, plansData, plansFresh]);

  // Trend history and change flashes.
  const hist = useRef(new Map<string, number[]>()), prev = useRef(new Map<string, Row>()), [flash, setFlash] = useState<Record<string, string>>({});
  useEffect(() => {
    const f: Record<string, string> = {};
    for (const r of priced) {
      const k = keyOf(r), p = prev.current.get(k);
      if (Number.isFinite(mid(r))) { const h = hist.current.get(k) ?? []; if (!p || p.seq !== r.seq) h.push(mid(r)); if (h.length > 300) h.splice(0, h.length - 300); hist.current.set(k, h); }
      if (p && p.seq !== r.seq) { f[k + '|bid'] = Number(r.bid) > Number(p.bid) ? 'up' : Number(r.bid) < Number(p.bid) ? 'down' : ''; f[k + '|ask'] = Number(r.ask) > Number(p.ask) ? 'up' : Number(r.ask) < Number(p.ask) ? 'down' : ''; }
      prev.current.set(k, r);
    }
    setFlash(f);
  }, [latest]);
  const ctx: Ctx = { flash, hist: hist.current, sources: byId };

  const q = ws.search.trim().toLowerCase();
  const keep = (r: Row) => {
    if (ws.status === 'live' && (r.noData || isStale(r))) return false;
    if (ws.status === 'stale' && !isStale(r)) return false;
    if (ws.status === 'nodata' && !r.noData) return false;
    if (ws.status === 'paused' && r.plan_status !== 'PAUSED') return false;
    if (ws.minQuality && !(Number(r.score) >= ws.minQuality)) return false;
    if (ws.classes.length && !ws.classes.includes(classOf(instOf(r), r.asset_class))) return false;
    if (q && !`${r.broker_symbol} ${instOf(r)}`.toLowerCase().includes(q)) return false;
    return true;
  };
  const col = TABLE_COLS.find(c => c.id === ws.sort.col) ?? TABLE_COLS[0];
  const sortRows = (rows: Row[]) => [...rows].sort((a, b) => { const x = col.value(a, ctx), y = col.value(b, ctx);
    const nx = x == null || (typeof x === 'number' && !Number.isFinite(x)), ny = y == null || (typeof y === 'number' && !Number.isFinite(y));
    if (nx || ny) return nx === ny ? String(a.broker_symbol).localeCompare(String(b.broker_symbol)) : nx ? 1 : -1;
    return ((typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * ws.sort.dir) || String(a.broker_symbol).localeCompare(String(b.broker_symbol)); });
  const cols = TABLE_COLS.filter(c => ws.columns.includes(c.id));
  const allRows = shown.flatMap(s => rowsBySource[s.account_id] ?? []);
  const visibleRows = allRows.filter(keep);
  const live = shown.filter(s => coll[s.account_id]?.state === 'CONNECTED').length;
  const scores = visibleRows.filter(r => r.score != null).map(r => Number(r.score));
  const lat = visibleRows.map(r => Number(r.latency_ms)).filter(Number.isFinite);
  const inWs = (id?: string) => !!id && (ws.all || ws.sources.includes(id));
  const wsEvents = (events?.items ?? []).filter((e: Row) => inWs(e.account_id) || !e.account_id).slice(0, 80);

  async function act(label: string, fn: () => Promise<string | void>) {
    setActionError(''); setNotice('');
    try { const m = await fn(); setNotice(m || `${label}: done.`); refresh(); } catch (e) { setActionError(`${label}: ${(e as Error).message}`); }
  }
  const setPlanStatus = (r: Row, status: string, reason: string) => act(`${r.broker_symbol} ${status.toLowerCase()}`, async () => {
    const res = await api(`md/plans/${r.plan_id}/status`, 'POST', { status, version: r.plan.version, reason });
    return res?.change_request ? `Sent for approval: ${res.change_request.summary}` : undefined;
  });
  const setSourcePaused = (s: Row, paused: boolean) => act(`${s.account_name || s.account_code} ${paused ? 'paused' : 'resumed'}`, async () => {
    const scope = `account:${s.account_id}`;
    await api(`md/controls/${encodeURIComponent(scope)}`, 'PUT', { paused, version: ctl[scope]?.version ?? 0, reason: paused ? 'Paused from the market data dashboard' : 'Resumed from the market data dashboard' });
  });
  const move = (id: string, d: number) => { const order = ws.all ? sources.map(s => s.account_id) : [...ws.sources]; const i = order.indexOf(id), j = i + d;
    if (i < 0 || j < 0 || j >= order.length) return; [order[i], order[j]] = [order[j], order[i]]; update({ all: false, sources: order }); };

  function exportCsv() {
    const esc = (v: any) => { const t = v == null ? '' : String(v); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const use = cols.filter(c => c.id !== 'trend' && c.id !== 'range');
    const lines = [['Source', 'Provider', ...use.map(c => c.label)].join(','), ...shown.flatMap(s => sortRows((rowsBySource[s.account_id] ?? []).filter(keep))
      .map(r => [esc(s.account_name || s.account_code), esc(s.provider_name), ...use.map(c => esc(c.value(r, ctx)))].join(',')))];
    const url = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })), a = document.createElement('a');
    a.href = url; a.download = `${ws.name.replace(/\W+/g, '_')}_${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  const sourceItems = sources.map(s => ({ id: s.account_id, label: s.account_name || s.account_code, group: `${s.provider_name} · ${s.platform}`,
    meta: `${s.environment === 'LIVE' ? 'LIVE' : 'demo'} · ${s.active_plans} plans`, dot: s.collector_state === 'CONNECTED' ? 'ok' : s.collector_state === 'FAILED' ? 'bad' : s.collector_state ? 'warn' : '' }));
  const classItems = Object.entries(CLASS_LABEL).map(([id, label]) => ({ id, label, group: '' }));

  return <div className="mb sb">
    <div className="mb-tabs" role="tablist" aria-label="Workspaces">
      {list.map(w => <button key={w.id} role="tab" aria-selected={w.id === ws.id} onClick={() => { setActive(w.id); setRenaming(false); }} onDoubleClick={() => setRenaming(true)} title="Double-click to rename">{w.name}</button>)}
      <button className="mb-new" onClick={() => { const w = blank({ name: `Workspace ${list.length + 1}`, all: false }); commit([...list, w]); setActive(w.id); }} title="New empty workspace: add the sources you want">+ New</button>
      <button className="mb-new" onClick={() => { const w = { ...ws, id: blank().id, name: ws.name + ' copy' }; commit([...list, w]); setActive(w.id); }}>Duplicate</button>
      <button className="mb-new" disabled={!sources.length} title="One workspace for each source in this workspace"
        onClick={() => { const made = shown.filter(s => !list.some(w => !w.all && w.sources.length === 1 && w.sources[0] === s.account_id))
          .map(s => blank({ name: s.account_name || s.account_code, all: false, sources: [s.account_id], columns: ws.columns })); if (made.length) { commit([...list, ...made]); setActive(made[0].id); } }}>One per source</button>
      {list.length > 1 && <button className="mb-new danger" onClick={() => { const next = list.filter(w => w.id !== ws.id); commit(next); setActive(next[0].id); }}>Close workspace</button>}
      {renaming && <input className="mb-rename" aria-label="Workspace name" autoFocus maxLength={40} defaultValue={ws.name} onBlur={e => { update({ name: e.target.value.trim() || ws.name }); setRenaming(false); }}
        onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setRenaming(false); }} />}
    </div>
    <div className="mb-controls" role="toolbar" aria-label="Workspace controls">
      <Picker label="Sources in workspace" items={sourceItems} selected={ws.all ? [] : ws.sources} allMeansAll={ws.all}
        change={v => update(ws.all && !v.length ? { all: true, sources: [] } : { all: false, sources: [...ws.sources.filter(id => v.includes(id)), ...v.filter(id => !ws.sources.includes(id))] })}
        empty="No source yet: add accounts under Setup › Connectivity." />
      <button className="mb-btn" onClick={() => update({ all: !ws.all, sources: ws.all ? sources.map(s => s.account_id) : ws.sources })} title={ws.all ? 'Fix the list (then add, remove and reorder)' : 'Follow every source, including new ones'}>{ws.all ? 'All sources ✓' : 'Use all sources'}</button>
      <span className="mb-sep" />
      <Seg label="Status" value={ws.status} options={[['', 'All'], ['live', 'Live'], ['stale', 'Stale'], ['nodata', 'No price'], ['paused', 'Paused']]} change={v => update({ status: v as SourceWs['status'] })} />
      <label className="mb-ctl">Min Q<select aria-label="Minimum quality" value={ws.minQuality} onChange={e => update({ minQuality: Number(e.target.value) })}>{[0, 50, 70, 80, 90].map(v => <option key={v} value={v}>{v || 'any'}</option>)}</select></label>
      <Picker label="Class" items={classItems} selected={ws.classes} change={v => update({ classes: v })} />
      <input className="mb-search" aria-label="Search instruments" placeholder="Search instrument" value={ws.search} onChange={e => update({ search: e.target.value })} />
      <Picker label="Columns" items={TABLE_COLS.map(c => ({ id: c.id, label: c.label, group: '', meta: c.title }))} selected={ws.columns} change={v => update({ columns: v.length ? v : DEFAULT_COLS })} allMeansAll={false} />
      <span className="mb-sep" />
      <button className="mb-btn" onClick={() => update({ collapsed: [] })}>Expand all</button>
      <button className="mb-btn" onClick={() => update({ collapsed: shown.map(s => s.account_id) })}>Collapse all</button>
      <label className="mb-ctl">Refresh<select aria-label="Refresh rate" value={ws.rate} onChange={e => update({ rate: Number(e.target.value) })}>{[[500, '0.5 s'], [1000, '1 s'], [2000, '2 s'], [5000, '5 s']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <Seg label="Time zone" value={ws.tz} options={[['local', 'Local'], ['utc', 'UTC']]} change={v => update({ tz: v as SourceWs['tz'] })} />
      <button className={`mb-btn ${frozen ? 'on' : ''}`} aria-pressed={frozen} onClick={() => setFrozen(f => !f)} title="Freeze the screen (collection continues)">{frozen ? '▶ Resume display' : '❚❚ Freeze display'}</button>
      <button className="mb-btn" onClick={exportCsv} disabled={!visibleRows.length}>Export CSV</button>
      <button className="mb-btn" onClick={() => goto('Intelligence')} title="Stage 2: select and compare data across sources, charts">Analyse → Market intelligence</button>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {actionError && <p role="alert" className="cfg-error">{actionError}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="mb-kpis">
      <Kpi label="Sources live" value={`${live}/${shown.length}`} tone={shown.length && live === shown.length ? 'ok' : live ? 'warn' : 'bad'} />
      <Kpi label="Instruments live" value={`${allRows.filter(r => !r.noData && !isStale(r)).length}/${allRows.length}`} />
      <Kpi label="Stale" value={String(allRows.filter(isStale).length)} tone={allRows.some(isStale) ? 'warn' : 'ok'} />
      <Kpi label="No price" value={String(allRows.filter(r => r.noData).length)} tone={allRows.some(r => r.noData) ? 'warn' : 'ok'} />
      <Kpi label="Paused" value={String(allRows.filter(r => r.plan_status === 'PAUSED').length + shown.filter(s => ctl[`account:${s.account_id}`]?.paused).length)} />
      <Kpi label="Errors" value={String(shown.filter(s => coll[s.account_id]?.problem).length)} tone={shown.some(s => coll[s.account_id]?.problem) ? 'bad' : 'ok'} />
      <Kpi label="Avg quality" value={scores.length ? String(Math.round(scores.reduce((a, b) => a + b, 0) / scores.length)) : '—'} />
      <Kpi label="Latency p95" value={lat.length ? `${Math.round([...lat].sort((a, b) => a - b)[Math.floor(lat.length * 0.95)] ?? lat[0])} ms` : '—'} />
      <Kpi label="Prices / min" value={String(visibleRows.reduce((n, r) => n + Number(r.ticks_last_minute || 0), 0))} />
    </div>
    {!shown.length && <div className="sb-empty">{ws.all ? 'No source yet. Add accounts under Setup › Connectivity.' : 'This workspace has no sources yet. Use “Sources in workspace” to add them.'}</div>}
    <div className="sb-sources">{shown.map((s, i) => {
      const c = coll[s.account_id], paused = ctl[`account:${s.account_id}`]?.paused, rows = sortRows((rowsBySource[s.account_id] ?? []).filter(keep));
      const all = rowsBySource[s.account_id] ?? [], collapsed = ws.collapsed.includes(s.account_id);
      const q2 = all.filter(r => r.score != null).map(r => Number(r.score));
      return <SourceSection key={s.account_id} s={s} c={c} paused={!!paused} rows={rows} total={all.length} collapsed={collapsed} cols={cols} ctx={ctx} ws={ws} canEdit={canEdit}
        avgQ={q2.length ? Math.round(q2.reduce((a, b) => a + b, 0) / q2.length) : null} first={i === 0} last={i === shown.length - 1}
        expanded={expanded} setExpanded={setExpanded} allRows={allRows}
        toggle={() => update({ collapsed: collapsed ? ws.collapsed.filter(x => x !== s.account_id) : [...ws.collapsed, s.account_id] })}
        sort={id => update({ sort: { col: id, dir: ws.sort.col === id ? (-ws.sort.dir as 1 | -1) : (TABLE_COLS.find(x => x.id === id)?.num ? -1 : 1) } })}
        move={d => move(s.account_id, d)} remove={() => update({ all: false, sources: (ws.all ? sources.map(x => x.account_id) : ws.sources).filter(x => x !== s.account_id) })}
        addInstrument={() => setEditing({ ...BLANK, account_id: s.account_id })} edit={r => r.plan ? setEditing(r.plan) : setEditing({ ...BLANK, account_id: s.account_id, broker_symbol: r.broker_symbol })}
        setPaused={p => void setSourcePaused(s, p)} planStatus={(r, st, reason) => void setPlanStatus(r, st, reason)}
        backfill={(planIds, form) => void act(`Backfill ${s.account_name || s.account_code}`, async () => {
          for (const id of planIds) await api('md/jobs', 'POST', { plan_id: id, ...form });
          return `Backfill queued for ${planIds.length} instrument${planIds.length === 1 ? '' : 's'} (Market data › Backfill shows progress).`; })} />;
    })}</div>
    <section className="mb-panel sb-events" aria-label="Events">
      <header><b>Events</b><span>{wsEvents.length} for this workspace's sources</span></header>
      <div className="mb-scroll">{wsEvents.map((e: Row) => <div key={e.id} className={`mb-ev ${String(e.severity).toLowerCase()}`}>
        <time>{new Date(e.at).toLocaleTimeString([], { hour12: false })}</time><b>{e.kind}</b><span>{[e.account_code, e.broker_symbol].filter(Boolean).join(' · ')} {eventSummary(e)}</span></div>)}</div>
    </section>
    {editing && <PlanWindow plan={editing} canEdit={canEdit} accounts={accounts?.items ?? []} instruments={instruments?.items ?? []}
      close={() => { setEditing(null); refresh(); }} saved={p => { setEditing(p); refresh(); }} />}
  </div>;
}

/* ---------- one source: header, details, instrument table ---------- */

function SourceSection({ s, c, paused, rows, total, collapsed, cols, ctx, ws, canEdit, avgQ, first, last, expanded, setExpanded, allRows, toggle, sort, move, remove, addInstrument, edit, setPaused, planStatus, backfill }: {
  s: Row; c?: Row; paused: boolean; rows: Row[]; total: number; collapsed: boolean; cols: Col[]; ctx: Ctx; ws: SourceWs; canEdit: boolean; avgQ: number | null; first: boolean; last: boolean;
  expanded: string; setExpanded: (k: string) => void; allRows: Row[]; toggle: () => void; sort: (id: string) => void; move: (d: number) => void; remove: () => void;
  addInstrument: () => void; edit: (r: Row) => void; setPaused: (p: boolean) => void; planStatus: (r: Row, status: string, reason: string) => void;
  backfill: (planIds: string[], form: Row) => void }) {
  const [panel, setPanel] = useState<'' | 'details' | 'backfill'>(''), [confirm, setConfirm] = useState('');
  const state = paused ? 'paused' : c?.state ? String(c.state).toLowerCase() : 'collector not running';
  const dot = paused ? 'warn' : c?.state === 'CONNECTED' ? 'ok' : c?.state === 'FAILED' ? 'bad' : c?.state ? 'warn' : '';
  const perMin = rows.reduce((n, r) => n + Number(r.ticks_last_minute || 0), 0);
  return <section className={`sb-source${collapsed ? ' collapsed' : ''}`} aria-label={`Source ${s.account_name || s.account_code}`}>
    <header className="sb-head">
      <button className="sb-caret" onClick={toggle} aria-expanded={!collapsed} aria-label={collapsed ? 'Expand source' : 'Collapse source'}>{collapsed ? '▸' : '▾'}</button>
      <i className={`mb-dot ${dot}`} />
      <b className="sb-name">{s.account_name || s.account_code}</b>
      <span className="sb-meta">{s.provider_name} · {s.platform}{s.server_name ? ` · ${s.server_name}` : ''}</span>
      <span className={`mb-env ${s.environment}`}>{s.environment === 'LIVE' ? 'LIVE' : 'demo'}</span>
      <span className={`sb-state ${dot}`}>{state}</span>
      {c?.host && <span className="sb-meta mono">{c.host}:{c.port}</span>}
      {c?.lag_ms != null && <span className="sb-meta">lag {Math.round(Number(c.lag_ms))} ms</span>}
      <span className="sb-meta">{total} instr · {perMin}/min{avgQ != null ? ` · Q ${avgQ}` : ''}</span>
      <span className="sb-actions">
        {canEdit && <button className="primary" onClick={addInstrument} title="Add an instrument to collect from this source (every plan option)">+ Instrument</button>}
        {canEdit && <button onClick={() => setPaused(!paused)} title="Pause or resume all collection from this source">{paused ? '▶ Resume source' : '❚❚ Pause source'}</button>}
        {canEdit && <button aria-pressed={panel === 'backfill'} onClick={() => setPanel(panel === 'backfill' ? '' : 'backfill')} title="Fetch history from the broker">Backfill</button>}
        <button aria-pressed={panel === 'details'} onClick={() => setPanel(panel === 'details' ? '' : 'details')}>Details</button>
        <button onClick={() => goto('Configuration', 'brokers')} title="Account, server, route and credentials">Connectivity</button>
        <button onClick={() => move(-1)} disabled={first} aria-label="Move source up">↑</button>
        <button onClick={() => move(1)} disabled={last} aria-label="Move source down">↓</button>
        <button onClick={remove} aria-label="Remove source from this workspace" title="Remove from this workspace (collection continues)">✕</button>
      </span>
    </header>
    {c?.problem && <div className="mb-problem" role="alert"><b>{c.problem.title}</b> {c.problem.detail}</div>}
    {panel === 'details' && <dl className="sb-details">
      {([['Account', `${s.account_code} · login ${s.external_account_id ?? '—'}`], ['Provider', `${s.provider_name} (${String(s.provider_kind ?? '').toLowerCase().replace('_', ' ')})`], ['Technology', s.platform],
        ['Server / session', s.server_name ?? s.connection_code], ['Environment', s.environment], ['Role', String(s.purpose ?? '').replace('_', ' ').toLowerCase()],
        ['Collector', c ? `${String(c.state).toLowerCase()}${c.started_at ? ` since ${new Date(c.started_at).toLocaleString()}` : ''}` : 'not running'],
        ['Heartbeat', c ? `${ago(c.heartbeat_age_seconds)} ago` : '—'], ['Subscribed', c ? `${c.subscribed} symbols` : '—'], ['Prices collected', c ? Number(c.ticks_total).toLocaleString() : '—'],
        ['Backlog', c ? String(c.backlog) : '—'], ['Server clock', c?.server_offset_minutes != null ? `UTC${c.server_offset_minutes >= 0 ? '+' : ''}${c.server_offset_minutes / 60}` : '—'],
        ['Active plans', String(s.active_plans)], ['Last error', c?.last_error ?? '—']] as [string, string][]).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}
    </dl>}
    {panel === 'backfill' && <BackfillForm rows={rows.filter(r => r.plan_id)} done={(ids, form) => { backfill(ids, form); setPanel(''); }} cancel={() => setPanel('')} />}
    {!collapsed && !total && <p className="sb-none">Nothing collected from this source yet.{canEdit ? ' Use + Instrument.' : ''}</p>}
    {!collapsed && total > 0 && <div className="sb-table"><table className="mb-table">
      <thead><tr>{cols.map(col => <th key={col.id} className={col.num ? 'num' : ''} title={col.title} aria-sort={ws.sort.col === col.id ? (ws.sort.dir === 1 ? 'ascending' : 'descending') : undefined}
        onClick={() => col.id !== 'trend' && sort(col.id)}>{col.label}{ws.sort.col === col.id ? (ws.sort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>)}<th className="sb-rowact">Actions</th></tr></thead>
      <tbody>{rows.map(r => { const k = keyOf(r), open = expanded === k;
        return <Fragment key={k}>
          <tr className={`${open ? 'sel' : ''}${r.noData ? ' nodata' : ''}`} onClick={() => setExpanded(open ? '' : k)}>
            {cols.map(col => <td key={col.id} className={col.num ? 'num mono' : ''}>{col.cell(r, ctx)}</td>)}
            <td className="sb-rowact" onClick={e => e.stopPropagation()}>
              <button onClick={() => setExpanded(open ? '' : k)} aria-label={`Chart ${r.broker_symbol}`} title="Chart and time & sales">{open ? '▴' : '▾'}</button>
              {canEdit && <button onClick={() => edit(r)} aria-label={`Settings ${r.broker_symbol}`} title="Every collection option for this instrument">⚙</button>}
              {canEdit && r.plan_id && (r.plan_status === 'ACTIVE' ? <button onClick={() => planStatus(r, 'PAUSED', 'Paused from the market data dashboard')} aria-label={`Pause ${r.broker_symbol}`} title="Pause collecting this instrument">❚❚</button>
                : r.plan_status !== 'ARCHIVED' && <button onClick={() => planStatus(r, 'ACTIVE', 'Started from the market data dashboard')} aria-label={`Start ${r.broker_symbol}`} title="Start collecting">▶</button>)}
              {canEdit && r.plan_id && (confirm === k ? <><button className="danger" onClick={() => { planStatus(r, 'ARCHIVED', 'Removed from the market data dashboard'); setConfirm(''); }}>Remove</button><button onClick={() => setConfirm('')}>Keep</button></>
                : <button onClick={() => setConfirm(k)} aria-label={`Remove ${r.broker_symbol}`} title="Stop and remove this instrument (history is kept)">✕</button>)}
            </td></tr>
          {open && <tr className="sb-expand"><td colSpan={cols.length + 1}>
            {r.noData ? <p className="mb-empty">No price received yet{r.plan_status !== 'ACTIVE' ? ` — the plan is ${PLAN_STATUS[r.plan_status] ?? 'not started'}` : c?.problem ? ` — ${c.problem.title}` : ''}.</p>
              : <div className="sb-drill"><div className="mb-panel"><Chart row={r} rows={allRows} pane={9} /></div><div className="mb-panel"><Tape row={r} /></div></div>}
          </td></tr>}
        </Fragment>; })}
        {!rows.length && <tr><td colSpan={cols.length + 1} className="mb-empty">No instrument matches the filters.</td></tr>}
      </tbody></table></div>}
  </section>;
}

function BackfillForm({ rows, done, cancel }: { rows: Row[]; done: (ids: string[], form: Row) => void; cancel: () => void }) {
  const day = (d: number) => new Date(Date.now() - d * 86400000).toISOString().slice(0, 10);
  const [f, setF] = useState({ timeframe: 'M1', from: day(7), to: day(0), reason: 'History for analysis', ids: rows.map(r => r.plan_id) as string[] });
  return <form className="sb-backfill" aria-label="Backfill" onSubmit={(e: FormEvent) => { e.preventDefault(); done(f.ids, { timeframe: f.timeframe, range_from: `${f.from}T00:00:00Z`, range_to: `${f.to}T23:59:59Z`, reason: f.reason }); }}>
    <b>Backfill history</b>
    <label>Timeframe<select aria-label="Backfill timeframe" value={f.timeframe} onChange={e => setF({ ...f, timeframe: e.target.value })}>{['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'].map(t => <option key={t}>{t}</option>)}</select></label>
    <label>From<input aria-label="Backfill from" type="date" value={f.from} onChange={e => setF({ ...f, from: e.target.value })} /></label>
    <label>To<input aria-label="Backfill to" type="date" value={f.to} onChange={e => setF({ ...f, to: e.target.value })} /></label>
    <label>Reason<input aria-label="Backfill reason" value={f.reason} minLength={3} onChange={e => setF({ ...f, reason: e.target.value })} /></label>
    <span className="sb-pick">{rows.map(r => <label key={r.plan_id} className="mb-chip"><input type="checkbox" checked={f.ids.includes(r.plan_id)} onChange={() => setF({ ...f, ids: f.ids.includes(r.plan_id) ? f.ids.filter(x => x !== r.plan_id) : [...f.ids, r.plan_id] })} />{r.broker_symbol}</label>)}
      {!rows.length && <small className="mb-dim">Add instruments first.</small>}</span>
    <button type="button" onClick={cancel}>Cancel</button><button className="primary" disabled={!f.ids.length || f.reason.trim().length < 3}>Queue backfill</button>
  </form>;
}
