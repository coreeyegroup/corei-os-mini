/* The Toolbox (MT5) / Panel (VS Code): always at hand under the main area.
 * Problems: what needs attention now · Events: market-data events as they happen ·
 * Journal: every configuration and control change · Activity: this session. */
import { useEffect, useState } from 'react';
import { api } from './Configuration';
import { goto, type Overview } from './nav';

type Row = Record<string, any>;
const time = (v?: string) => v ? new Date(v).toLocaleTimeString([], { hour12: false }) : '';
const day = (v?: string) => v ? new Date(v).toLocaleDateString([], { month: 'short', day: '2-digit' }) : '';

function useList(path: string, active: boolean, every: number) {
  const [items, setItems] = useState<Row[] | null>(null), [error, setError] = useState('');
  useEffect(() => {
    if (!active) return;
    let alive = true;
    const load = () => api(path).then(d => { if (alive) { setItems(d.items ?? []); setError(''); } }).catch(e => { if (alive) setError((e as Error).message); });
    load();
    const t = window.setInterval(load, every);
    return () => { alive = false; window.clearInterval(t); };
  }, [path, active, every]);
  return [items, error] as const;
}

function summary(e: Row): string {
  const d = e.detail ?? {};
  const v = d.message ?? d.reason ?? d.error ?? d.warning ?? (d.from && d.to ? `${d.from} → ${d.to}` : '');
  return typeof v === 'string' ? v : JSON.stringify(v);
}

export default function Toolbox({ overview, activity, close }: { overview: Overview | null; activity: string[]; close: () => void }) {
  const [tab, setTab] = useState(() => { try { return localStorage.getItem('corei-os-mini.toolbox') || 'problems'; } catch { return 'problems'; } });
  useEffect(() => { try { localStorage.setItem('corei-os-mini.toolbox', tab); } catch { /* convenience only */ } }, [tab]);
  const [events, eventsError] = useList('md/events?limit=200', tab === 'events', 4000);
  const [journal, journalError] = useList('config/audit', tab === 'journal', 15000);
  const problems = overview?.attention ?? [];
  const tabs: [string, string, number | null][] = [['problems', 'Problems', problems.length], ['events', 'Events', null], ['journal', 'Journal', null], ['activity', 'Activity', null]];
  return <>
    <div className="bottom-tabs tb-tabs" role="tablist" aria-label="Toolbox">
      {tabs.map(([id, label, n]) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>
        {label.toUpperCase()}{n ? <span className={`tb-count ${problems.some(p => p.severity === 'CRITICAL') ? 'bad' : ''}`}>{n}</span> : null}</button>)}
      <button className="push" title="Hide the toolbox (Ctrl+J)" aria-label="Hide toolbox" onClick={close}>×</button>
    </div>
    <div className="tb-body">
      {tab === 'problems' && <table className="tb-table"><tbody>
        {problems.map((a, i) => <tr key={i} onClick={() => goto(a.goto.view, a.goto.section)} title={`${a.title}\n${a.detail}`}>
          <td className={`tb-sev ${a.severity}`}>{a.severity === 'CRITICAL' ? '⛔' : '⚠'}</td><td className="tb-area">{a.area}</td><td>{a.title}</td><td className="tb-muted">{a.detail}</td></tr>)}
        {overview && !problems.length && <tr><td className="tb-muted">No problems.</td></tr>}
        {!overview && <tr><td className="tb-muted">Sign in to see problems.</td></tr>}</tbody></table>}
      {tab === 'events' && <table className="tb-table"><tbody>
        {(events ?? []).map(e => <tr key={e.id} onClick={() => goto('Market data', 'events')} className={`tb-${e.severity}`}>
          <td className="tb-time">{day(e.at)} {time(e.at)}</td><td className={`tb-sev ${e.severity}`}>{e.severity === 'CRITICAL' ? '⛔' : e.severity === 'WARNING' ? '⚠' : '·'}</td>
          <td className="tb-area">{e.kind.replace(/_/g, ' ').toLowerCase()}</td><td>{e.account_code ?? ''}{e.broker_symbol ? ` ${e.broker_symbol}` : ''}</td><td className="tb-muted">{summary(e)}</td></tr>)}
        {events && !events.length && <tr><td className="tb-muted">No events.</td></tr>}
        {eventsError && <tr><td className="tb-muted">{eventsError}</td></tr>}</tbody></table>}
      {tab === 'journal' && <table className="tb-table"><tbody>
        {(journal ?? []).map((a, i) => <tr key={a.id ?? i} onClick={() => goto('Configuration', 'audit')}><td className="tb-time">{day(a.occurred_at)} {time(a.occurred_at)}</td><td className="tb-area">{a.actor}</td>
          <td>{String(a.action ?? '').replace(/_/g, ' ').toLowerCase()}</td><td>{String(a.entity ?? '').replace(/_/g, ' ')}</td><td className="tb-muted">{a.reason ?? ''}</td></tr>)}
        {journal && !journal.length && <tr><td className="tb-muted">No changes recorded.</td></tr>}
        {journalError && <tr><td className="tb-muted">{journalError}</td></tr>}</tbody></table>}
      {tab === 'activity' && <div className="tb-log">{activity.length ? activity.map((e, i) => <div key={i}>{e}</div>) : <span className="tb-muted">No activity in this session.</span>}</div>}
    </div>
  </>;
}
