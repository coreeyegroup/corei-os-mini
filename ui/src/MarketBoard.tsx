/*
 * Market dashboard: live prices for many sources and instruments, in workspaces.
 *   Workspaces (tabs): each keeps its own selection (sources, instruments), filters (environment, feed role, status,
 *   minimum quality, search), grouping, sort, columns, chart panes, refresh rate and time zone; saved for the user
 *   (operator preference md.workspaces.v2) so they follow them to other devices. New, duplicate, one per source, rename, close.
 *   Panels: health strip · quote board (26 columns to choose from, sort by any, group by instrument/source/provider/class)
 *   · 1, 2 or 4 charts (candles S1…D1 or ticks, compare with another source) · time & sales · the same instrument from
 *   every source · feeds and events. Pause, CSV export, keyboard (/ search, ↑↓ rows).
 * Data: md/latest (rate per workspace), md/sources, md/instruments, md/collectors, md/plans, md/events, md/bars, md/ticks.
 */
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import { api } from './Configuration';
import { eventSummary } from './MarketData';
import { digitsOf, goto } from './nav';

export type Row = Record<string, any>;
type Workspace = {
  id: string; name: string;
  sources: string[]; instruments: string[];          // empty = all
  env: '' | 'DEMO' | 'LIVE'; roles: string[]; status: '' | 'live' | 'stale' | 'nodata'; minQuality: number; search: string;
  group: 'none' | 'instrument' | 'source' | 'provider' | 'class'; sort: { col: string; dir: 1 | -1 }; columns: string[];
  panes: 1 | 2 | 4; charts: string[]; rate: number; tz: 'local' | 'utc';
};
const PREF = 'md.workspaces.v2';
const FRAMES = ['TICK', 'S1', 'M1', 'M5', 'M15', 'H1', 'H4', 'D1'];
export const baseSymbol = (s: string) => { const u = String(s).toUpperCase().split(/[^A-Z0-9]/)[0]; return /^[A-Z]{6}[A-Z]{1,2}$/.test(u) && /^(XAU|XAG|EUR|GBP|USD|AUD|NZD|CAD|CHF|JPY)/.test(u) ? u.slice(0, 6) : u; };
export const keyOf = (r: Row) => `${r.account_id}|${r.broker_symbol}`;
export const instOf = (r: Row) => String(r.instrument_symbol || r.broker_symbol);
const CCY = /^(EUR|GBP|USD|AUD|NZD|CAD|CHF|JPY|SGD|HKD|NOK|SEK|ZAR|MXN|TRY|PLN|CNH|INR)/;
export const CLASS_LABEL: Record<string, string> = { FX: 'FX', METAL: 'Metals', INDEX: 'Indices', COMMODITY: 'Commodities', CRYPTO: 'Crypto', EQUITY: 'Equities', FUTURE: 'Futures', OPTION: 'Options', OTHER: 'Other' };
export function classOf(symbol: string, known?: string | null): string {
  if (known) return known;
  const u = baseSymbol(symbol);
  if (/^(XAU|XAG|XPT|XPD|GOLD|SILVER)/.test(u)) return 'METAL';
  if (/^(BTC|ETH|XRP|SOL|LTC|DOGE|ADA)/.test(u)) return 'CRYPTO';
  if (/NIFTY|SENSEX|BANKEX|US30|US500|NAS|SPX|NDX|DJ|GER|DAX|UK100|FTSE|JP225|HK50|AUS200|FRA40|STOXX/.test(u)) return 'INDEX';
  if (/^(WTI|BRENT|USOIL|UKOIL|NGAS|XNG|CRUDE|COPPER)/.test(u)) return 'COMMODITY';
  if (u.length === 6 && CCY.test(u) && CCY.test(u.slice(3))) return 'FX';
  return 'OTHER';
}
export const fmt = (v: any, d: number) => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(d);
export const ago = (s?: number | null) => s == null || !Number.isFinite(Number(s)) ? '—' : s < 1 ? 'now' : s < 60 ? `${Number(s).toFixed(s < 10 ? 1 : 0)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`;
let TZ: 'local' | 'utc' = 'local';
export const setTz = (tz: 'local' | 'utc') => { TZ = tz; };
export const clock = (v?: string) => { if (!v) return '—'; const t = new Date(v); const ms = String(t.getUTCMilliseconds()).padStart(3, '0');
  return (TZ === 'utc' ? t.toISOString().slice(11, 19) : t.toLocaleTimeString([], { hour12: false })) + '.' + ms; };
export const stamp = (v?: string) => !v ? '—' : TZ === 'utc' ? new Date(v).toISOString().replace('T', ' ').slice(0, 16) + 'Z' : new Date(v).toLocaleString([], { hour12: false });

function useVisible() {
  const [visible, setVisible] = useState(() => typeof document === 'undefined' || !document.hidden);
  useEffect(() => { const on = () => setVisible(!document.hidden); document.addEventListener('visibilitychange', on); return () => document.removeEventListener('visibilitychange', on); }, []);
  return visible;
}

export function usePoll<T = Row>(path: string | null, every: number): [T | null, string] {
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

/* ---------- columns: every one can be shown, hidden and sorted ---------- */

export type Col = { id: string; label: string; title: string; num?: boolean; value: (r: Row, x: Ctx) => any; cell: (r: Row, x: Ctx) => React.ReactNode };
export type Ctx = { flash: Record<string, string>; hist: Map<string, number[]>; sources: Record<string, Row> };
export const mid = (r: Row) => r.bid != null && r.ask != null ? (Number(r.bid) + Number(r.ask)) / 2 : r.last != null ? Number(r.last) : NaN;
export const spreadPts = (r: Row) => r.bid != null && r.ask != null ? Math.round((Number(r.ask) - Number(r.bid)) * 10 ** digitsOf(r)) : null;
export const chgPct = (r: Row) => r.day_open && Number.isFinite(mid(r)) ? (mid(r) / Number(r.day_open) - 1) * 100 : null;
export const isStale = (r: Row) => !r.noData && Number(r.age_seconds) > (r.stale_seconds ?? 10);
export const COLS: Col[] = [
  { id: 'symbol', label: 'Symbol', title: 'Broker symbol', value: r => r.broker_symbol, cell: r => <><b>{r.broker_symbol}</b>{r.feed_role && <small className={`mb-role ${r.active_feed ? 'on' : ''}`}>{r.feed_role === 'PRIMARY' ? 'P' : r.feed_role === 'BACKUP' ? 'B' : ''}</small>}</> },
  { id: 'instrument', label: 'Instr', title: 'Instrument (COREI name)', value: r => instOf(r), cell: r => r.instrument_symbol ?? <span className="mb-dim">{baseSymbol(r.broker_symbol)}</span> },
  { id: 'class', label: 'Class', title: 'Asset class', value: r => classOf(instOf(r), r.asset_class), cell: r => CLASS_LABEL[classOf(instOf(r), r.asset_class)] },
  { id: 'source', label: 'Source', title: 'Account (source)', value: (r, x) => x.sources[r.account_id]?.account_name || r.account_name || r.account_code,
    cell: (r, x) => <span className="mb-src" title={`${r.account_code} · ${r.connection_code ?? ''}`}>{x.sources[r.account_id]?.account_name || r.account_name || r.account_code}</span> },
  { id: 'provider', label: 'Provider', title: 'Provider (who)', value: (r, x) => x.sources[r.account_id]?.provider_name ?? r.provider_name, cell: (r, x) => <span className="mb-src">{x.sources[r.account_id]?.provider_name ?? r.provider_name}</span> },
  { id: 'tech', label: 'Tech', title: 'Technology', value: (r, x) => x.sources[r.account_id]?.platform ?? r.platform, cell: (r, x) => <span className="mb-dim">{x.sources[r.account_id]?.platform ?? r.platform}</span> },
  { id: 'env', label: 'Env', title: 'Environment', value: (r, x) => x.sources[r.account_id]?.environment ?? r.environment, cell: (r, x) => { const e = x.sources[r.account_id]?.environment ?? r.environment; return <span className={`mb-env ${e}`}>{e === 'LIVE' ? 'LIVE' : 'demo'}</span>; } },
  { id: 'bid', label: 'Bid', title: 'Best bid', num: true, value: r => Number(r.bid), cell: (r, x) => <span className={x.flash[keyOf(r) + '|bid'] ?? ''}>{fmt(r.bid, digitsOf(r))}</span> },
  { id: 'ask', label: 'Ask', title: 'Best ask', num: true, value: r => Number(r.ask), cell: (r, x) => <span className={x.flash[keyOf(r) + '|ask'] ?? ''}>{fmt(r.ask, digitsOf(r))}</span> },
  { id: 'mid', label: 'Mid', title: 'Mid price', num: true, value: r => mid(r), cell: r => fmt(mid(r), digitsOf(r) + 1) },
  { id: 'last', label: 'Last', title: 'Last trade (exchanges)', num: true, value: r => Number(r.last), cell: r => fmt(r.last, digitsOf(r)) },
  { id: 'spread', label: 'Spr', title: 'Spread now, points', num: true, value: r => spreadPts(r), cell: r => spreadPts(r) ?? '—' },
  { id: 'spreadAvg', label: 'Spr avg', title: 'Average spread last minute, points', num: true, value: r => Number(r.spread_avg_points), cell: r => r.spread_avg_points == null ? '—' : Number(r.spread_avg_points).toFixed(1) },
  { id: 'chg', label: 'Chg %', title: 'Change since the day open', num: true, value: r => chgPct(r), cell: r => { const c = chgPct(r); return c == null ? '—' : <span className={c >= 0 ? 'pos' : 'neg'}>{c >= 0 ? '+' : ''}{c.toFixed(2)}</span>; } },
  { id: 'chgPts', label: 'Chg pts', title: 'Change since the day open, points', num: true, value: r => r.day_open ? (mid(r) - Number(r.day_open)) * 10 ** digitsOf(r) : null, cell: r => r.day_open && Number.isFinite(mid(r)) ? Math.round((mid(r) - Number(r.day_open)) * 10 ** digitsOf(r)) : '—' },
  { id: 'open', label: 'Open', title: 'Day open', num: true, value: r => Number(r.day_open), cell: r => fmt(r.day_open, digitsOf(r)) },
  { id: 'high', label: 'High', title: 'Day high', num: true, value: r => Number(r.day_high), cell: r => fmt(r.day_high, digitsOf(r)) },
  { id: 'low', label: 'Low', title: 'Day low', num: true, value: r => Number(r.day_low), cell: r => fmt(r.day_low, digitsOf(r)) },
  { id: 'range', label: 'Day range', title: 'Where the price sits in the day range', value: r => r.day_high > r.day_low ? (mid(r) - r.day_low) / (r.day_high - r.day_low) : null, cell: r => <Range low={r.day_low} high={r.day_high} value={mid(r)} /> },
  { id: 'age', label: 'Age', title: 'Seconds since the last price', num: true, value: r => r.noData ? Infinity : Number(r.age_seconds), cell: r => r.noData ? <span className="mb-dim">no price</span> : <span className={isStale(r) ? 'neg' : ''}>{ago(r.age_seconds)}</span> },
  { id: 'latency', label: 'Lat', title: 'Broker time to COREI, ms (last price)', num: true, value: r => Number(r.latency_ms), cell: r => r.latency_ms == null ? '—' : Math.round(Number(r.latency_ms)) },
  { id: 'latency50', label: 'Lat p50', title: 'Median latency last minute, ms', num: true, value: r => Number(r.latency_p50_ms), cell: r => r.latency_p50_ms == null ? '—' : Math.round(Number(r.latency_p50_ms)) },
  { id: 'ticks', label: 'Ticks/m', title: 'Prices received in the last minute', num: true, value: r => Number(r.ticks_last_minute), cell: r => r.ticks_last_minute ?? '—' },
  { id: 'quality', label: 'Q', title: 'Quality score (0–100)', num: true, value: r => r.score == null ? -1 : Number(r.score), cell: r => <span className={`mb-q ${Number(r.score) >= 80 ? 'ok' : Number(r.score) >= 50 ? 'warn' : r.score == null ? '' : 'bad'}`}>{r.score ?? '—'}</span> },
  { id: 'time', label: 'Broker time', title: 'Time of the last price at the source', value: r => r.source_time, cell: r => <span className="mb-dim">{r.source_time ? clock(r.source_time) : '—'}</span> },
  { id: 'trend', label: 'Trend', title: 'Mid price, last 5 minutes on this screen', value: () => 0, cell: (r, x) => <Spark points={x.hist.get(keyOf(r)) ?? []} /> },
];
export const DEFAULT_COLS = ['symbol', 'source', 'env', 'bid', 'ask', 'spread', 'chg', 'range', 'age', 'latency', 'ticks', 'quality', 'trend'];
const blankWs = (patch: Partial<Workspace> = {}): Workspace => ({ id: 'ws' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5), name: 'All sources', sources: [], instruments: [],
  env: '', roles: [], status: '', minQuality: 0, search: '', group: 'none', sort: { col: 'symbol', dir: 1 }, columns: DEFAULT_COLS, panes: 1, charts: [], rate: 1000, tz: 'local', ...patch });

function loadWorkspaces(): Workspace[] {
  try { const x = JSON.parse(localStorage.getItem('corei-os-mini.' + PREF) || 'null'); if (Array.isArray(x) && x.length) return x.map(w => blankWs(w)); } catch { /* none saved */ }
  try { // earlier version: { accounts, symbols }
    const old = JSON.parse(localStorage.getItem('corei-os-mini.md.workspaces') || '[]');
    if (Array.isArray(old) && old.length) return [blankWs({ id: 'all' }), ...old.map((w: Row) => blankWs({ id: w.id, name: w.name, sources: w.accounts ?? [], instruments: w.symbols ?? [] }))];
  } catch { /* none */ }
  return [blankWs({ id: 'all' })];
}

/* ---------- the dashboard ---------- */

export default function MarketBoard() {
  const [list, setList] = useState<Workspace[]>(loadWorkspaces);
  const [active, setActive] = useState<string>(() => { try { return localStorage.getItem('corei-os-mini.md.workspace') || 'all'; } catch { return 'all'; } });
  const ws = list.find(w => w.id === active) ?? list[0];
  TZ = ws.tz;
  const [paused, setPaused] = useState(false);
  const [latest, error] = usePoll<Row>('md/latest', paused ? 0 : ws.rate);
  const [collectors] = usePoll<Row>('md/collectors', 5000);
  const [plans] = usePoll<Row>('md/plans', 15000);
  const [events] = usePoll<Row>('md/events?limit=300', 10000);
  const [srcData] = usePoll<Row>('md/sources', 15000);
  const [instData] = usePoll<Row>('md/instruments', 30000);
  const [renaming, setRenaming] = useState(false), [pane, setPane] = useState(0), [selected, setSelected] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  // Workspaces follow the user: load from the server when this browser has none of its own; save every change.
  useEffect(() => { if (list.length > 1) return; void api('operator/prefs').then((p: Row) => { if (Array.isArray(p?.[PREF]) && p[PREF].length) setList(p[PREF].map((w: Row) => blankWs(w))); }).catch(() => undefined); }, []);
  const saveTimer = useRef<number | undefined>(undefined);
  const commit = (next: Workspace[]) => {
    setList(next);
    try { localStorage.setItem('corei-os-mini.' + PREF, JSON.stringify(next)); } catch { /* preference only */ }
    window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => void api(`operator/prefs/${encodeURIComponent(PREF)}`, 'PUT', { value: next }).catch(() => undefined), 800);
  };
  const update = (patch: Partial<Workspace>) => commit(list.map(w => w.id === ws.id ? { ...w, ...patch } : w));
  useEffect(() => { try { localStorage.setItem('corei-os-mini.md.workspace', ws.id); } catch { /* preference only */ } }, [ws.id]);

  const sourceList: Row[] = srcData?.items ?? [];
  const sources = useMemo(() => Object.fromEntries(sourceList.map(s => [s.account_id, s])), [srcData]);
  const priced: Row[] = latest?.items ?? [];
  // Rows: every price, plus active plans that have no price yet (so a silent source is visible, not missing).
  const rows = useMemo(() => {
    const have = new Set(priced.map(keyOf));
    const waiting = (plans?.items ?? []).filter((p: Row) => p.status === 'ACTIVE' && !have.has(keyOf(p)))
      .map((p: Row) => ({ ...p, noData: true, account_name: sources[p.account_id]?.account_name, provider_name: sources[p.account_id]?.provider_name }));
    const planOf = Object.fromEntries((plans?.items ?? []).map((p: Row) => [keyOf(p), p]));
    return [...priced.map(r => ({ ...r, active_feed: planOf[keyOf(r)]?.active_feed, feed_role: r.feed_role ?? planOf[keyOf(r)]?.feed_role })), ...waiting];
  }, [latest, plans, sources]);

  // History for the trend column and change flashes.
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

  const q = ws.search.trim().toLowerCase();
  const visible = rows.filter(r => {
    const src = sources[r.account_id] ?? r;
    if (ws.sources.length && !ws.sources.includes(r.account_id)) return false;
    if (ws.instruments.length && !ws.instruments.includes(instOf(r))) return false;
    if (ws.env && (src.environment ?? r.environment) !== ws.env) return false;
    if (ws.roles.length && !ws.roles.includes(r.feed_role ?? r.plan_purpose ?? 'NONE')) return false;
    if (ws.status === 'live' && (r.noData || isStale(r))) return false;
    if (ws.status === 'stale' && !isStale(r)) return false;
    if (ws.status === 'nodata' && !r.noData) return false;
    if (ws.minQuality && !(Number(r.score) >= ws.minQuality)) return false;
    if (q && !`${r.broker_symbol} ${instOf(r)} ${r.account_name ?? ''} ${r.account_code ?? ''} ${src.provider_name ?? ''}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const ctx: Ctx = { flash, hist: hist.current, sources };
  const col = COLS.find(c => c.id === ws.sort.col) ?? COLS[0];
  const cmp = (a: Row, b: Row) => { const x = col.value(a, ctx), y = col.value(b, ctx);
    const nx = x == null || (typeof x === 'number' && !Number.isFinite(x)), ny = y == null || (typeof y === 'number' && !Number.isFinite(y));
    if (nx || ny) return nx === ny ? 0 : nx ? 1 : -1;
    return (typeof x === 'number' ? x - y : String(x).localeCompare(String(y))) * ws.sort.dir; };
  const sorted = [...visible].sort((a, b) => cmp(a, b) || String(a.broker_symbol).localeCompare(String(b.broker_symbol)));
  const groupKey = (r: Row) => ws.group === 'instrument' ? instOf(r) : ws.group === 'source' ? (sources[r.account_id]?.account_name || r.account_name || r.account_code) : ws.group === 'provider' ? (sources[r.account_id]?.provider_name ?? r.provider_name ?? '—')
    : ws.group === 'class' ? CLASS_LABEL[classOf(instOf(r), r.asset_class)] : '';
  const groups = useMemo(() => { const m = new Map<string, Row[]>(); for (const r of sorted) { const g = groupKey(r); m.set(g, [...(m.get(g) ?? []), r]); } return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0])); }, [sorted, ws.group]);
  const cols = COLS.filter(c => ws.columns.includes(c.id));
  const charts = Array.from({ length: ws.panes }, (_, i) => ws.charts[i]);
  // Pane 1 shows the first priced row until something is picked, so the screen is never blank.
  const chartRows = charts.map((k, i) => rows.find(r => keyOf(r) === k) ?? (i === 0 ? sorted.find(r => !r.noData) ?? null : null));
  const focus = rows.find(r => keyOf(r) === selected) ?? chartRows[pane] ?? sorted[0];
  const pick = (r: Row) => { const k = keyOf(r); setSelected(k); const next = [...charts]; next[pane] = k; update({ charts: next.map(x => x ?? '') }); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault(); const i = sorted.findIndex(r => keyOf(r) === keyOf(focus ?? {})); const r = sorted[Math.max(0, Math.min(sorted.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]; if (r) pick(r);
  };
  useEffect(() => { const on = (e: KeyboardEvent) => { if (e.key === '/' && !(e.target instanceof HTMLInputElement) && !(e.target instanceof HTMLTextAreaElement)) { e.preventDefault(); searchRef.current?.focus(); } };
    window.addEventListener('keydown', on); return () => window.removeEventListener('keydown', on); }, []);

  const inSources = (id: string) => !ws.sources.length || ws.sources.includes(id);
  const wsCollectors = (collectors?.items ?? []).filter((c: Row) => inSources(c.account_id));
  const wsEvents = (events?.items ?? []).filter((e: Row) => !e.account_id || inSources(e.account_id)).slice(0, 60);
  const live = wsCollectors.filter((c: Row) => c.state === 'CONNECTED').length;
  const scores = visible.filter(r => r.score != null).map(r => Number(r.score));
  const lat = visible.map(r => Number(r.latency_ms)).filter(Number.isFinite);
  const problems = wsCollectors.filter((c: Row) => c.problem);
  const sourceItems = sourceList.map(s => ({ id: s.account_id, label: s.account_name || s.account_code, group: `${s.provider_name} · ${s.platform}`,
    meta: `${s.environment === 'LIVE' ? 'LIVE' : 'demo'} · ${s.symbols_with_prices}/${s.active_plans} sym${s.collector_state ? ' · ' + s.collector_state.toLowerCase() : ''}`, dot: s.collector_state === 'CONNECTED' ? 'ok' : s.collector_state === 'FAILED' ? 'bad' : s.collector_state ? 'warn' : '' }));
  const instItems = (instData?.items ?? []).map((i: Row) => ({ id: i.symbol, label: i.symbol, group: CLASS_LABEL[classOf(i.symbol, i.asset_class)],
    meta: i.sources ? `${i.sources} source${i.sources === 1 ? '' : 's'}${i.priced ? '' : ' · no price yet'}` : 'not collected', dim: !i.sources }));

  function exportCsv() {
    const esc = (v: any) => { const t = v == null ? '' : String(v); return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t; };
    const out = [cols.filter(c => c.id !== 'trend' && c.id !== 'range').map(c => c.label).join(','), ...sorted.map(r => cols.filter(c => c.id !== 'trend' && c.id !== 'range').map(c => esc(c.value(r, ctx))).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob([out], { type: 'text/csv' })), a = document.createElement('a');
    a.href = url; a.download = `${ws.name.replace(/\W+/g, '_')}_${new Date().toISOString().slice(0, 19).replace(/:/g, '')}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  return <div className="mb">
    <div className="mb-tabs" role="tablist" aria-label="Workspaces">
      {list.map(w => <button key={w.id} role="tab" aria-selected={w.id === ws.id} onClick={() => { setActive(w.id); setRenaming(false); }} onDoubleClick={() => setRenaming(true)} title="Double-click to rename">{w.name}</button>)}
      <button className="mb-new" onClick={() => { const w = blankWs({ name: `Workspace ${list.length + 1}` }); commit([...list, w]); setActive(w.id); }} title="New workspace with every source and instrument">+ New</button>
      <button className="mb-new" onClick={() => { const w = { ...ws, id: blankWs().id, name: ws.name + ' copy' }; commit([...list, w]); setActive(w.id); }} title="Copy this workspace with its selection, filters and layout">Duplicate</button>
      <button className="mb-new" disabled={!ws.sources.length && !sourceList.length} title="One workspace for each source selected here (or every source)"
        onClick={() => { const ids = ws.sources.length ? ws.sources : sourceList.map(s => s.account_id); const made = ids.filter(id => !list.some(w => w.sources.length === 1 && w.sources[0] === id))
          .map(id => blankWs({ name: sources[id]?.account_name || sources[id]?.account_code || 'Source', sources: [id], columns: ws.columns })); if (made.length) { commit([...list, ...made]); setActive(made[0].id); } }}>One per source</button>
      {list.length > 1 && <button className="mb-new danger" onClick={() => { const next = list.filter(w => w.id !== ws.id); commit(next); setActive(next[0].id); }}>Close workspace</button>}
      {renaming && <input className="mb-rename" aria-label="Workspace name" autoFocus maxLength={40} defaultValue={ws.name} onBlur={e => { update({ name: e.target.value.trim() || ws.name }); setRenaming(false); }}
        onKeyDown={e => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); if (e.key === 'Escape') setRenaming(false); }} />}
    </div>
    <div className="mb-controls" role="toolbar" aria-label="Dashboard controls">
      <Picker label="Sources" items={sourceItems} selected={ws.sources} change={v => update({ sources: v })} empty="No source yet: add accounts under Setup › Connectivity." />
      <Picker label="Instruments" items={instItems} selected={ws.instruments} change={v => update({ instruments: v })} empty="No instrument yet: add collection plans." />
      <Seg label="Environment" value={ws.env} options={[['', 'All'], ['DEMO', 'Demo'], ['LIVE', 'Live']]} change={v => update({ env: v as Workspace['env'] })} />
      <Picker label="Role" items={[['PRIMARY', 'Primary feed'], ['BACKUP', 'Backup feed'], ['MONITORING', 'Monitoring'], ['RESEARCH', 'Research'], ['REFERENCE', 'Reference'], ['NONE', 'No plan']].map(([id, label]) => ({ id, label, group: '' }))}
        selected={ws.roles} change={v => update({ roles: v })} />
      <Seg label="Status" value={ws.status} options={[['', 'All'], ['live', 'Live'], ['stale', 'Stale'], ['nodata', 'No price']]} change={v => update({ status: v as Workspace['status'] })} />
      <label className="mb-ctl">Min Q<select aria-label="Minimum quality" value={ws.minQuality} onChange={e => update({ minQuality: Number(e.target.value) })}>{[0, 50, 70, 80, 90].map(v => <option key={v} value={v}>{v || 'any'}</option>)}</select></label>
      <input ref={searchRef} className="mb-search" aria-label="Search symbols and sources" placeholder="Search  /" value={ws.search} onChange={e => update({ search: e.target.value })} />
      <span className="mb-sep" />
      <label className="mb-ctl">Group<select aria-label="Group rows by" value={ws.group} onChange={e => update({ group: e.target.value as Workspace['group'] })}>
        {[['none', 'None'], ['instrument', 'Instrument'], ['source', 'Source'], ['provider', 'Provider'], ['class', 'Asset class']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <Picker label="Columns" items={COLS.map(c => ({ id: c.id, label: c.label, group: '', meta: c.title }))} selected={ws.columns} change={v => update({ columns: v.length ? v : DEFAULT_COLS })} allMeansAll={false} />
      <span className="mb-sep" />
      <Seg label="Charts" value={String(ws.panes)} options={[['1', '1'], ['2', '2'], ['4', '4']]} change={v => { update({ panes: Number(v) as Workspace['panes'] }); setPane(0); }} />
      <label className="mb-ctl">Refresh<select aria-label="Refresh rate" value={ws.rate} onChange={e => update({ rate: Number(e.target.value) })}>{[[500, '0.5 s'], [1000, '1 s'], [2000, '2 s'], [5000, '5 s']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <Seg label="Time zone" value={ws.tz} options={[['local', 'Local'], ['utc', 'UTC']]} change={v => update({ tz: v as Workspace['tz'] })} />
      <button className={`mb-btn ${paused ? 'on' : ''}`} aria-pressed={paused} onClick={() => setPaused(p => !p)} title="Freeze the quote board (collection continues)">{paused ? '▶ Resume' : '❚❚ Pause'}</button>
      <button className="mb-btn" onClick={exportCsv} disabled={!sorted.length} title="Download the rows and columns shown">Export CSV</button>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="mb-kpis">
      <Kpi label="Feeds live" value={`${live}/${wsCollectors.length}`} tone={wsCollectors.length && live === wsCollectors.length ? 'ok' : live ? 'warn' : 'bad'} />
      <Kpi label="Sources" value={`${ws.sources.length || sourceList.length}/${sourceList.length}`} />
      <Kpi label="Instruments" value={String(new Set(visible.map(instOf)).size)} />
      <Kpi label="Rows" value={`${visible.length}/${rows.length}`} />
      <Kpi label="Stale · no price" value={`${visible.filter(isStale).length} · ${visible.filter(r => r.noData).length}`} tone={visible.some(r => isStale(r) || r.noData) ? 'warn' : 'ok'} />
      <Kpi label="Avg quality" value={scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length).toString() : '—'} tone={!scores.length ? '' : Math.min(...scores) >= 80 ? 'ok' : 'warn'} />
      <Kpi label="Latency p95" value={lat.length ? `${Math.round([...lat].sort((a, b) => a - b)[Math.floor(lat.length * 0.95)] ?? lat[0])} ms` : '—'} />
      <Kpi label="Events" value={String(wsEvents.length)} tone={wsEvents.some((e: Row) => e.severity === 'CRITICAL') ? 'bad' : ''} />
    </div>
    {problems.map((c: Row) => <div key={c.account_id} className="mb-problem" role="alert"><b>{c.account_name || c.account_code}: {c.problem.title}</b> {c.problem.detail}
      <button className="link" onClick={() => goto('Configuration', 'brokers')}>Open Connectivity</button></div>)}
    <div className={`mb-grid panes-${ws.panes}`}>
      <section className="mb-panel mb-board" aria-label="Quote board">
        <header><b>Quotes</b><span>{ws.name}{paused ? ' · paused' : ''}</span><span className="mb-hint">click a row to chart it in the selected pane · ↑↓ to move</span></header>
        <div className="mb-scroll" tabIndex={0} onKeyDown={onKey}><table className="mb-table"><thead><tr>{cols.map(c => <th key={c.id} className={c.num ? 'num' : ''} title={c.title} aria-sort={ws.sort.col === c.id ? (ws.sort.dir === 1 ? 'ascending' : 'descending') : undefined}
          onClick={() => c.id !== 'trend' && update({ sort: { col: c.id, dir: ws.sort.col === c.id ? (-ws.sort.dir as 1 | -1) : (c.num ? -1 : 1) } })}>{c.label}{ws.sort.col === c.id ? (ws.sort.dir === 1 ? ' ▲' : ' ▼') : ''}</th>)}</tr></thead>
          <tbody>{groups.map(([g, members]) => <Fragment key={g || 'all'}>
            {ws.group !== 'none' && <tr className="mb-group"><td colSpan={cols.length}><b>{g}</b> <span>{members.length} row{members.length === 1 ? '' : 's'}</span>
              {members.some(r => !r.noData) && <span>· best spread {Math.min(...members.map(r => spreadPts(r) ?? Infinity))} pts · best Q {Math.max(...members.map(r => Number(r.score ?? -1))) < 0 ? '—' : Math.max(...members.map(r => Number(r.score ?? -1)))}</span>}</td></tr>}
            {members.map(r => <tr key={keyOf(r)} className={`${keyOf(r) === keyOf(focus ?? {}) ? 'sel' : ''}${r.noData ? ' nodata' : ''}${charts.includes(keyOf(r)) ? ' charted' : ''}`} onClick={() => pick(r)}>
              {cols.map(c => <td key={c.id} className={c.num ? 'num mono' : ''}>{c.cell(r, ctx)}</td>)}</tr>)}
          </Fragment>)}
            {latest && !visible.length && <tr><td colSpan={cols.length} className="mb-empty">{rows.length ? 'Nothing matches the selection and filters.' : 'No prices yet. Start collection plans under Market data › Collection plans.'}</td></tr>}</tbody></table></div>
      </section>
      <div className="mb-charts">{charts.map((_, i) => <section key={i} className={`mb-panel mb-chart${i === pane && ws.panes > 1 ? ' active' : ''}`} aria-label={`Chart ${i + 1}`} onMouseDown={() => setPane(i)}>
        {chartRows[i] ? <Chart key={keyOf(chartRows[i]!)} row={chartRows[i]!} rows={rows} pane={i} />
          : <><header><b>Chart {i + 1}</b></header><p className="mb-empty">{ws.panes > 1 ? 'Select this pane, then click a row.' : 'Click a row to chart it.'}</p></>}</section>)}</div>
      <section className="mb-panel mb-tapepanel" aria-label="Time and sales">{focus && !focus.noData ? <Tape row={focus} /> : <><header><b>Time &amp; sales</b></header><p className="mb-empty">Select a symbol with prices.</p></>}</section>
      <section className="mb-panel mb-sourcespanel" aria-label="Sources">{focus ? <SourcesPanel row={focus} rows={rows} sources={sources} /> : null}</section>
      <section className="mb-panel mb-feedspanel" aria-label="Feeds and events">
        <header><b>Feeds</b><span>{live}/{wsCollectors.length} live</span></header>
        <div className="mb-scroll mb-feeds">{wsCollectors.map((c: Row) => <div key={c.account_id} className="mb-feed">
          <i className={`mb-dot ${c.state === 'CONNECTED' ? 'ok' : c.state === 'RECONNECTING' || c.state === 'STARTING' ? 'warn' : c.state === 'FAILED' ? 'bad' : ''}`} />
          <b>{c.account_name || c.account_code}</b><span>{c.state.toLowerCase()}</span>
          <small>{sources[c.account_id]?.provider_name ? `${sources[c.account_id].provider_name} · ` : ''}{c.host ? `${c.host}:${c.port} · ` : ''}{c.lag_ms != null ? `lag ${Math.round(Number(c.lag_ms))} ms · ` : ''}{c.subscribed ?? 0} sym · hb {ago(c.heartbeat_age_seconds)}</small></div>)}
          {!wsCollectors.length && <p className="mb-empty">No collector for these sources.</p>}
          <div className="mb-events">{wsEvents.map((e: Row) => <div key={e.id} className={`mb-ev ${String(e.severity).toLowerCase()}`}>
            <time>{clock(e.at).slice(0, 8)}</time><b>{e.kind}</b><span>{[e.account_code, e.broker_symbol].filter(Boolean).join(' · ')} {eventSummary(e)}</span></div>)}</div>
        </div>
      </section>
    </div>
  </div>;
}

/* ---------- pickers ---------- */

type Item = { id: string; label: string; group: string; meta?: string; dot?: string; dim?: boolean };

/** Multi-select for long lists: search, groups, select a whole group, "only", all/none. Empty selection = all. */
export function Picker({ label, items, selected, change, empty, allMeansAll = true }: { label: string; items: Item[]; selected: string[]; change: (v: string[]) => void; empty?: string; allMeansAll?: boolean }) {
  const [open, setOpen] = useState(false), [q, setQ] = useState('');
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!open) { setQ(''); return; } const off = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', off); document.addEventListener('keydown', esc); return () => { document.removeEventListener('mousedown', off); document.removeEventListener('keydown', esc); }; }, [open]);
  const all = allMeansAll && !selected.length;
  const isOn = (id: string) => all || selected.includes(id);
  const shown = items.filter(i => !q || `${i.label} ${i.group} ${i.meta ?? ''}`.toLowerCase().includes(q.toLowerCase()));
  const groups = [...new Set(shown.map(i => i.group))];
  const set = (ids: string[]) => change(allMeansAll && ids.length === items.length ? [] : ids);
  const toggle = (ids: string[], on: boolean) => { const base = all ? items.map(i => i.id) : selected; set(on ? [...new Set([...base, ...ids])] : base.filter(x => !ids.includes(x))); };
  const summary = all ? `All (${items.length})` : `${selected.filter(id => items.some(i => i.id === id)).length} of ${items.length}`;
  return <div className="mb-picker" ref={box}>
    <button className={`mb-pickbtn${all ? '' : ' on'}`} aria-haspopup="listbox" aria-expanded={open} onClick={() => setOpen(o => !o)}><span>{label}</span><b>{summary}</b> ▾</button>
    {open && <div className="mb-pop" role="dialog" aria-label={label}>
      <div className="mb-poptools"><input autoFocus aria-label={`Search ${label.toLowerCase()}`} placeholder={`Search ${items.length} ${label.toLowerCase()}…`} value={q} onChange={e => setQ(e.target.value)} />
        <button onClick={() => toggle(shown.map(i => i.id), true)}>{q ? 'Add shown' : 'All'}</button>
        <button onClick={() => q ? toggle(shown.map(i => i.id), false) : set(allMeansAll ? [] : [])}>{q ? 'Remove shown' : allMeansAll ? 'Reset' : 'None'}</button></div>
      <div className="mb-poplist" role="listbox" aria-multiselectable="true">
        {!items.length && <p className="mb-empty">{empty ?? 'Nothing to choose yet.'}</p>}
        {groups.map(g => { const members = shown.filter(i => i.group === g), on = members.every(i => isOn(i.id));
          return <div key={g || 'items'}>{g && <label className="mb-popgroup"><input type="checkbox" checked={on} onChange={() => toggle(members.map(i => i.id), !on)} />{g}<small>{members.length}</small></label>}
            {members.map(i => <label key={i.id} className={`mb-popitem${i.dim ? ' dim' : ''}`} role="option" aria-selected={isOn(i.id)}>
              <input type="checkbox" checked={isOn(i.id)} onChange={() => toggle([i.id], !isOn(i.id))} />{i.dot !== undefined && <i className={`mb-dot ${i.dot}`} />}
              <span>{i.label}</span><small>{i.meta}</small><button type="button" className="mb-only" onClick={e => { e.preventDefault(); change([i.id]); }}>only</button></label>)}</div>; })}
      </div>
      <div className="mb-popfoot">{all ? (allMeansAll ? 'Showing all — new ones are included automatically.' : '') : `${selected.length} selected`}</div>
    </div>}
  </div>;
}

export function Seg({ label, value, options, change }: { label: string; value: string; options: [string, string][]; change: (v: string) => void }) {
  return <span className="mb-seg" role="group" aria-label={label}>{options.map(([v, l]) => <button key={v} aria-pressed={value === v} onClick={() => change(v)}>{l}</button>)}</span>;
}

export function Kpi({ label, value, tone = '' }: { label: string; value: string; tone?: string }) {
  return <div className={`mb-kpi ${tone}`}><small>{label}</small><b>{value}</b></div>;
}

export function Range({ low, high, value }: { low?: number; high?: number; value: number }) {
  if (low == null || high == null || !Number.isFinite(value) || high <= low) return <span className="mb-range none" />;
  const pos = Math.max(0, Math.min(1, (value - low) / (high - low)));
  return <span className="mb-range" title={`Low ${low} · High ${high}`}><i style={{ left: `${pos * 100}%` }} /></span>;
}

export function Spark({ points }: { points: number[] }) {
  if (points.length < 2) return <svg className="mb-spark" width="80" height="18" aria-hidden="true" />;
  const lo = Math.min(...points), hi = Math.max(...points), span = hi - lo || 1;
  const d = points.map((p, i) => `${i ? 'L' : 'M'}${(i / (points.length - 1) * 80).toFixed(1)},${(17 - (p - lo) / span * 16).toFixed(1)}`).join('');
  return <svg className={`mb-spark ${points.at(-1)! >= points[0] ? 'up' : 'down'}`} width="80" height="18" viewBox="0 0 80 18" aria-hidden="true"><path d={d} /></svg>;
}

/* ---------- chart: candles by timeframe, or ticks ---------- */

export function Chart({ row, rows, pane }: { row: Row; rows: Row[]; pane: number }) {
  const PK = `corei-os-mini.md.frame.${pane}`;
  const [frame, setFrame] = useState<string>(() => { try { return localStorage.getItem(PK) || 'M1'; } catch { return 'M1'; } });
  useEffect(() => { try { localStorage.setItem(PK, frame); } catch { /* preference only */ } }, [frame]);
  const base = `${row.account_id}/${encodeURIComponent(row.broker_symbol)}`;
  const [bars] = usePoll<Row>(frame === 'TICK' ? null : `md/bars/${base}?timeframe=${frame}&limit=150`, frame === 'S1' ? 2000 : 10000);
  const [ticks] = usePoll<Row>(frame === 'TICK' ? `md/ticks/${base}?seconds=300&limit=1500` : null, 2000);
  // Compare: the same instrument from another source, drawn as a line over this one.
  const peers = rows.filter(r => !r.noData && keyOf(r) !== keyOf(row) && (instOf(r) === instOf(row) || baseSymbol(r.broker_symbol) === baseSymbol(row.broker_symbol)));
  const [cmpKey, setCmpKey] = useState('');
  const peer = peers.find(r => keyOf(r) === cmpKey);
  const pbase = peer ? `${peer.account_id}/${encodeURIComponent(peer.broker_symbol)}` : '';
  const [pbars] = usePoll<Row>(peer && frame !== 'TICK' ? `md/bars/${pbase}?timeframe=${frame}&limit=150` : null, 10000);
  const [pticks] = usePoll<Row>(peer && frame === 'TICK' ? `md/ticks/${pbase}?seconds=300&limit=1500` : null, 2000);
  const [hover, setHover] = useState<number | null>(null);
  const d = digitsOf(row);
  const W = 640, H = 260, PAD = 56;
  const candles: Row[] = frame === 'TICK' ? [] : [...(bars?.items ?? [])].reverse();
  const tks: Row[] = frame === 'TICK' ? (ticks?.items ?? []) : [];
  const cmpPts: { t: number; v: number }[] = !peer ? [] : frame === 'TICK' ? (pticks?.items ?? []).filter((t: Row) => t.bid != null && t.ask != null).map((t: Row) => ({ t: Date.parse(t.received_at), v: (Number(t.bid) + Number(t.ask)) / 2 }))
    : (pbars?.items ?? []).map((b: Row) => ({ t: Date.parse(b.bar_time), v: Number(b.close) }));
  const values = [...(frame === 'TICK' ? tks.flatMap(t => [t.bid, t.ask].filter(v => v != null).map(Number)) : candles.flatMap(c => [Number(c.high), Number(c.low)])), ...cmpPts.map(p => p.v)];
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
      {peers.length > 0 && <select className="mb-cmp" aria-label="Compare with another source" value={cmpKey} onChange={e => setCmpKey(e.target.value)}>
        <option value="">Compare…</option>{peers.map(p => <option key={keyOf(p)} value={keyOf(p)}>{p.account_name || p.account_code} · {p.broker_symbol}</option>)}</select>}
      <span className="mb-frames" role="group" aria-label="Timeframe">{FRAMES.map(f => <button key={f} aria-pressed={frame === f} onClick={() => setFrame(f)}>{f === 'TICK' ? 'Ticks' : f}</button>)}</span></header>
    <div className="mb-readout">{h ? frame === 'TICK' ? <>{clock(h.received_at)} · bid <b>{fmt(h.bid, d)}</b> ask <b>{fmt(h.ask, d)}</b></>
      : <>{stamp(h.bar_time)} · O <b>{fmt(h.open, d)}</b> H <b>{fmt(h.high, d)}</b> L <b>{fmt(h.low, d)}</b> C <b>{fmt(h.close, d)}</b> · {h.ticks} ticks{h.source === 'BROKER' ? ' · broker history' : ''}</>
      : <>{n ? `${n} ${frame === 'TICK' ? 'prices, last 5 min' : 'bars'}` : 'No data for this timeframe yet'}{peer ? ` · compare: ${peer.account_name || peer.account_code} (line)` : ''}</>}</div>
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
      {cmpPts.length > 1 && n > 1 && (() => { const ts = frame === 'TICK' ? tks.map(t => Date.parse(t.received_at)) : candles.map(c => Date.parse(c.bar_time)); const t0 = ts[0], t1 = ts[ts.length - 1] || t0 + 1;
        const px = (t: number) => x(0) + (t - t0) / Math.max(1, t1 - t0) * (x(n - 1) - x(0));
        return <path className="mb-line cmp" d={cmpPts.filter(p => p.t >= t0 && p.t <= t1).sort((a, b) => a.t - b.t).map((p, i) => `${i ? 'L' : 'M'}${px(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('')} />; })()}
      {hover != null && <line className="mb-cross" x1={x(hover)} x2={x(hover)} y1={0} y2={H} />}
    </svg>
  </>;
}

/* ---------- time & sales ---------- */

export function Tape({ row }: { row: Row }) {
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

export function SourcesPanel({ row, rows, sources }: { row: Row; rows: Row[]; sources: Record<string, Row> }) {
  // Same instrument: linked by a plan, or the same base symbol once broker suffixes are dropped (XAUUSD.r, XAUUSDm, EURUSD-ECN).
  const same = rows.filter(r => (row.instrument_id && r.instrument_id === row.instrument_id) || baseSymbol(r.broker_symbol) === baseSymbol(row.broker_symbol));
  const d = digitsOf(row), ref = same.find(r => r.active_feed && !r.noData) ?? row, refMid = mid(ref);
  return <><header><b>Sources</b><span>{row.instrument_symbol ?? baseSymbol(row.broker_symbol)} · {same.length} source{same.length === 1 ? '' : 's'}</span></header>
    <div className="mb-scroll"><table className="mb-table"><thead><tr><th>Source</th><th>Provider</th><th>Role</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spr</th><th className="num">Δ pts</th><th className="num">Lat</th><th className="num">Age</th><th className="num">Q</th></tr></thead>
      <tbody>{same.map(r => <tr key={keyOf(r)} className={keyOf(r) === keyOf(row) ? 'sel' : ''}><td className="mb-src">{r.account_name || r.account_code} <small className="mb-dim">{r.broker_symbol}</small></td>
          <td className="mb-src">{sources[r.account_id]?.provider_name ?? r.provider_name}</td>
          <td>{r.feed_role ? <span className={`mb-role ${r.active_feed ? 'on' : ''}`}>{r.feed_role.toLowerCase()}{r.active_feed ? ' · used' : ''}</span> : <span className="mb-dim">{(r.plan_purpose ?? r.purpose ?? '').toLowerCase().replace('_', ' ') || '—'}</span>}</td>
          <td className="num mono">{fmt(r.bid, d)}</td><td className="num mono">{fmt(r.ask, d)}</td><td className="num mono">{spreadPts(r) ?? '—'}</td>
          <td className="num mono">{r === ref || !Number.isFinite(mid(r) - refMid) ? '—' : Math.round((mid(r) - refMid) * 10 ** d)}</td>
          <td className="num mono">{r.latency_ms == null ? '—' : Math.round(Number(r.latency_ms))}</td>
          <td className="num mono">{r.noData ? 'none' : ago(r.age_seconds)}</td><td className="num">{r.score ?? '—'}</td></tr>)}</tbody></table>
      {same.length < 2 && <p className="mb-empty">One source only. Add a backup plan for this instrument on another source to compare and switch.</p>}</div></>;
}
