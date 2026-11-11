/* The side bar: live content for the chosen area, as collapsible views (VS Code
 * "views", MT5 "Market Watch" / "Navigator"). Market: Market Watch + Feeds + sections.
 * Setup: sections with counts + order-sending state. System: sections + every table.
 * Home / Monitor: problems + feeds. Dense rows; a click goes straight to the item. */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api } from './Configuration';
import type { View } from './layout';
import { current, digitsOf, DESTINATIONS, goto, gotoTable, type Overview } from './nav';

type Row = Record<string, any>;

function usePoll<T>(path: string | null, every: number): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    const load = () => { if (document.visibilityState !== 'hidden') api(path).then(d => { if (alive) setData(d); }).catch(() => {}); };
    load();
    const t = every ? window.setInterval(load, every) : 0;
    return () => { alive = false; if (t) window.clearInterval(t); };
  }, [path, every]);
  return data;
}

function useCurrent(view: View) {
  const [section, setSection] = useState(current[view]);
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent).detail; if (d.view === view) setSection(d.section); };
    window.addEventListener('corei-section', on);
    return () => window.removeEventListener('corei-section', on);
  }, [view]);
  return section;
}

function Pane({ title, count, actions, children, initiallyOpen = true }: { title: string; count?: ReactNode; actions?: ReactNode; children: ReactNode; initiallyOpen?: boolean }) {
  const key = 'corei-os-mini.pane.' + title;
  const [open, setOpen] = useState(() => { try { const v = localStorage.getItem(key); return v == null ? initiallyOpen : v === '1'; } catch { return initiallyOpen; } });
  return <section className="sb-pane">
    <header onClick={() => { setOpen(!open); try { localStorage.setItem(key, open ? '0' : '1'); } catch { /* convenience only */ } }}>
      <span className="sb-caret">{open ? '▾' : '▸'}</span><span className="sb-title">{title}</span>{count != null && <span className="sb-count">{count}</span>}
      <span className="sb-actions" onClick={e => e.stopPropagation()}>{actions}</span>
    </header>
    {open && <div className="sb-body">{children}</div>}
  </section>;
}

function Sections({ view }: { view: View }) {
  const section = useCurrent(view);
  return <>{DESTINATIONS.filter(d => d.view === view && d.section).map(d =>
    <button key={d.label} className={`sb-row ${section === d.section ? 'selected' : ''}`} onClick={() => goto(view, d.section)}>{d.label}</button>)}</>;
}

/* MT5-style Market Watch: symbol, bid, ask, spread; the price flashes up/down; old prices dim. */
export function MarketWatch() {
  const data = usePoll<{ items: Row[] }>('md/latest', 1500);
  const prev = useRef<Record<string, number>>({});
  const items = data?.items ?? [];
  const rows = items.map(l => {
    const key = l.account_id + l.broker_symbol, bid = Number(l.bid), before = prev.current[key];
    const dir = before == null || bid === before ? '' : bid > before ? 'up' : 'down';
    return { l, key, dir };
  });
  useEffect(() => { for (const r of rows) prev.current[r.key] = Number(r.l.bid); });
  return <table className="sb-table">
    <thead><tr><th>Symbol</th><th className="num">Bid</th><th className="num">Ask</th><th className="num">Spr</th></tr></thead>
    <tbody>{rows.map(({ l, key, dir }) => {
      const digits = digitsOf(l), stale = Number(l.age_seconds) > 10;
      return <tr key={key} className={stale ? 'stale' : ''} title={`${l.account_name || l.account_code} · ${Number(l.age_seconds).toFixed(1)} s old`} onClick={() => goto('Market data', 'live')}>
        <td>{l.broker_symbol}</td><td className={`num ${dir}`}>{l.bid != null ? Number(l.bid).toFixed(digits) : '—'}</td>
        <td className={`num ${dir}`}>{l.ask != null ? Number(l.ask).toFixed(digits) : '—'}</td>
        <td className="num">{l.bid != null && l.ask != null ? Math.round((Number(l.ask) - Number(l.bid)) * 10 ** digits) : ''}</td></tr>;
    })}
    {data && !items.length && <tr><td colSpan={4} className="sb-empty">No prices</td></tr>}</tbody></table>;
}

export function Feeds() {
  const data = usePoll<{ items: Row[] }>('md/collectors', 5000);
  const items = data?.items ?? [];
  return <>{items.map(c => <button key={c.account_id} className="sb-row sb-feed" title={c.problem ? `${c.problem.title}: ${c.problem.raw}` : c.host ?? ''} onClick={() => goto('Market data', 'live')}>
    <span className={`sb-dot ${c.state === 'CONNECTED' ? 'ok' : c.state === 'FAILED' ? 'bad' : c.state === 'RECONNECTING' ? 'warn' : ''}`} />
    <span className="sb-grow">{c.account_name || c.account_code}</span>
    <span className="sb-meta">{c.state === 'CONNECTED' ? `${Math.round(Number(c.lag_ms ?? 0))} ms` : c.problem?.title ?? c.state.toLowerCase()}</span></button>)}
    {data && !items.length && <div className="sb-empty">No collector running</div>}</>;
}

function Problems({ overview }: { overview: Overview | null }) {
  const items = overview?.attention ?? [];
  return <>{items.map((a, i) => <button key={i} className="sb-row sb-problem" title={a.detail} onClick={() => goto(a.goto.view, a.goto.section)}>
    <span className={`sb-sev ${a.severity}`}>{a.severity === 'CRITICAL' ? '⛔' : '⚠'}</span><span className="sb-grow">{a.title}</span></button>)}
    {overview && !items.length && <div className="sb-empty">No problems</div>}</>;
}

function SetupTree() {
  const summary = usePoll<Row>('config/summary', 15000);
  const section = useCurrent('Configuration');
  const counts = summary?.counts ?? {};
  const groups: [string, [string, string][]][] = [
    ['Brokers & accounts', [['providers', 'Providers'], ['connections', 'Connections'], ['accounts', 'Accounts'], ['groups', 'Account groups']]],
    ['Instruments', [['instruments', 'Instruments'], ['mappings', 'Symbol mappings']]],
    ['Strategies', [['strategies', 'Strategies'], ['deployments', 'Deployments']]],
    ['Controls', [['approvals', 'Approvals'], ['controls', 'Order sending'], ['alerts', 'Alerts']]],
    ['Administration', [['users', 'Users'], ['audit', 'Audit log']]],
  ];
  return <>
    <button className={`sb-row ${section === 'health' ? 'selected' : ''}`} onClick={() => goto('Configuration', 'health')}>Account readiness</button>
    {groups.map(([g, items]) => <div key={g}><div className="sb-group">{g}</div>
      {items.map(([id, label]) => <button key={id} className={`sb-row sb-indent ${section === id ? 'selected' : ''}`} onClick={() => goto('Configuration', id)}>
        <span className="sb-grow">{label}</span>{counts[id] != null && <span className={`sb-meta ${id === 'approvals' && counts[id] ? 'warn' : ''}`}>{counts[id]}</span>}
        {id === 'controls' && summary && <span className="sb-meta">{(summary.execution_controls ?? []).map((c: Row) => `${c.environment} ${c.trading_enabled ? 'ON' : 'off'}`).join(' · ')}</span>}
      </button>)}</div>)}
  </>;
}

function TablesList() {
  const data = usePoll<{ tables: Row[] }>('infra/database', 60000);
  const [filter, setFilter] = useState('');
  const tables = (data?.tables ?? []).filter(t => t.name.includes(filter.toLowerCase()));
  return <>
    <input className="sb-filter" placeholder="Filter tables" aria-label="Filter tables" value={filter} onChange={e => setFilter(e.target.value)} />
    {tables.map(t => <button key={t.name} className="sb-row" title={t.why} onClick={() => gotoTable(t.name)}>
      <span className="sb-grow sb-mono">{t.mode === 'READ_ONLY' ? '🔒 ' : ''}{t.name}</span><span className="sb-meta">{Number(t.rows).toLocaleString()}</span></button>)}
    {!data && <div className="sb-empty">Administrators only</div>}</>;
}

export default function Sidebar({ view, overview }: { view: View; overview: Overview | null }) {
  const problems = overview?.attention.length ?? 0;
  if (view === 'Market data') return <>
    <Pane title="Market watch"><MarketWatch /></Pane>
    <Pane title="Feeds" count={overview ? `${overview.feeds.live}/${overview.feeds.collectors}` : undefined}><Feeds /></Pane>
    <Pane title="Market data"><Sections view="Market data" /></Pane>
  </>;
  if (view === 'Configuration') return <Pane title="Setup"><SetupTree /></Pane>;
  if (view === 'Infrastructure') return <>
    <Pane title="System"><Sections view="Infrastructure" /></Pane>
    <Pane title="Tables"><TablesList /></Pane>
  </>;
  if (view === 'Overview' || view === 'Operations') return <>
    <Pane title="Problems" count={problems || undefined}><Problems overview={overview} /></Pane>
    <Pane title="Feeds" count={overview ? `${overview.feeds.live}/${overview.feeds.collectors}` : undefined}><Feeds /></Pane>
    <Pane title="Market watch"><MarketWatch /></Pane>
  </>;
  return <Pane title="Go to">{DESTINATIONS.filter(d => d.group !== 'Home').slice(0, 12).map(d =>
    <button key={d.label} className="sb-row" onClick={() => goto(d.view, d.section)}>{d.group} › {d.label}</button>)}</Pane>;
}
