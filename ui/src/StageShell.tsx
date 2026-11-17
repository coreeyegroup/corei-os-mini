/* The frame every trading stage uses, so every stage looks and reads the same.
 * Why: 1000+ users move between eight stages; the same layout everywhere means learning it once.
 * Purpose: sign-in, the stage header (number, title, the question it answers, what comes in and goes out, with
 * links to the neighbouring stages), the grouped section bar (Overview · Work · Monitor · Configure, plus the stage
 * map), the section's title and one-line explanation, and the re-authentication dialog.
 * Must never: hold stage data (each stage view does). */
import { Fragment, useEffect, useState, type ReactNode } from 'react';
import { addReauthHandler, api } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { SignIn } from './Infrastructure';
import Stage from './Pipeline';
import { PIPELINE, stageOf } from './flow';
import type { View } from './layout';
import { goto, publishSections, useSection } from './nav';
import { StartHere } from './QuickStart';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

export type Row = Record<string, any>;
export type Section = { id: string; label: string; group: 'Overview' | 'Work' | 'Monitor' | 'Configure'; hint: string; render: (ctx: Ctx) => ReactNode };
export type Ctx = { user: Row; canOps: boolean; canRisk: boolean; open: (section: string) => void };
const GROUPS = ['Overview', 'Work', 'Monitor', 'Configure'] as const;

export default function StageShell({ view, sections }: { view: View; sections: Section[] }) {
  const all = [...sections, { id: 'stage', label: 'Stage map', group: 'Configure' as const, hint: 'What this stage does, its components and how far each is built.', render: () => null }];
  const [tab, setTab] = useSection<string>(view, sections[0].id, all.map(s => s.id));
  useEffect(() => { publishSections(view, all.map(s => ({ id: s.id, label: s.label, group: s.group }))); }, [view, all.map(s => s.id).join()]);
  const [user, setUser] = useState<Row | null | undefined>(undefined), [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check(); window.addEventListener('corei-session', check); return () => window.removeEventListener('corei-session', check);
  }, []);
  const stage = stageOf(view)!;
  const i = PIPELINE.indexOf(stage), prev = PIPELINE[i - 1], next = PIPELINE[i + 1];
  const current = all.find(s => s.id === tab) ?? all[0];
  const nav = <nav className="cfg-nav" aria-label={`${stage.title} sections`}>
    {GROUPS.map(g => <Fragment key={g}><span className="cfg-nav-group">{g}</span>
      {all.filter(s => s.group === g).map(s => <button key={s.id} aria-current={tab === s.id ? 'page' : undefined} onClick={() => setTab(s.id)}><span>{s.label}</span></button>)}</Fragment>)}
  </nav>;
  if (tab === 'stage') return <div className="cfg md">{nav}<Stage view={view} open={v => goto(v)} /></div>;
  if (user === undefined) return <div className="cfg md">{nav}<div className="content"><p>Checking sign-in…</p></div></div>;
  if (user === null) return <div className="cfg md">{nav}<SignIn title={stage.title.toUpperCase()} done={s => setUser(s)} /></div>;
  const roles: string[] = user.roles ?? [];
  const ctx: Ctx = { user, canOps: roles.includes('TRADING_OPS') || roles.includes('ADMIN'), canRisk: roles.includes('RISK_APPROVER') || roles.includes('ADMIN'), open: setTab };
  return <div className="cfg md">
    {nav}
    <section className="cfg-main">
      <header className="cfg-head st-head"><div>
        <p className="st-flow">
          {prev ? <button className="link" onClick={() => goto(prev.view)}>← {prev.n} · {prev.title}</button> : <span />}
          <b>Stage {stage.n} · {stage.title}</b>
          {next ? <button className="link" onClick={() => goto(next.view)}>{next.n} · {next.title} →</button> : <span />}
        </p>
        <h1>{current.label}</h1><p>{current.hint}</p>
        <p className="st-question"><b>This stage answers:</b> {stage.question} <b>It hands on:</b> {stage.feeds}.</p>
      </div></header>
      <div className="cfg-pane"><StartHere ctx={ctx} />{current.render(ctx)}</div>
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

/* Small shared pieces for the stage views. */
export function useData<T = Row>(path: string | null, every = 15_000, deps: unknown[] = []): [T | null, string, () => void] {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [n, setN] = useState(0);
  useEffect(() => {
    if (!path) return;
    let live = true;
    const load = () => void api(path).then(d => { if (live) { setData(d); setError(''); } }).catch(e => { if (live) setError((e as Error).message); });
    load();
    const t = every ? window.setInterval(load, every) : 0;
    return () => { live = false; if (t) window.clearInterval(t); };
  }, [path, n, ...deps]);
  return [data, error, () => setN(x => x + 1)];
}

export const when = (v?: string | null) => v ? new Date(v).toLocaleString() : '—';
export const ago = (v?: string | null) => { if (!v) return '—'; const s = (Date.now() - new Date(v).getTime()) / 1000; return s < 90 ? `${Math.round(s)} s ago` : s < 5400 ? `${Math.round(s / 60)} min ago` : when(v); };
export const num = (v: any, d = 2) => v == null || v === '' ? '–' : Number(v).toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
export const money = (v: any, ccy?: string) => v == null ? '–' : `${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}${ccy ? ' ' + ccy : ''}`;
export const pill = (v: string, cls = '') => <span className={`cfg-pill ${cls}`}>{String(v).toLowerCase().replace(/_/g, ' ')}</span>;
export const STATE_CLS: Record<string, string> = { FILLED: 'ok', ACTED: 'ok', VALID: 'ok', ACTIVE: 'ok', APPROVED: 'ok', ACKNOWLEDGED: 'demo', SUBMITTED: 'demo', NEW: 'demo',
  PARTIALLY_FILLED: 'demo', REJECTED: 'live', UNKNOWN: 'live', CANCELLED: 'off', EXPIRED: 'off', CANCEL_PENDING: 'warn', REFUSED: 'live', NO_CHANGE: 'off', ORDERED: 'ok' };
export const statePill = (v: string) => pill(v, STATE_CLS[v] ?? '');
export function Kpis({ items }: { items: [string, ReactNode, string?, string?][] }) {
  return <div className="intel-kpis">{items.map(([l, v, hint, cls]) => <div key={l} className={`intel-kpi ${cls ?? ''}`} title={hint}><small>{l}</small><b>{v}</b></div>)}</div>;
}
export function Msg({ error, notice }: { error?: string; notice?: string }) {
  return <>{error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}</>;
}
export function Reasons({ list }: { list?: string[] }) {
  if (!list?.length) return null;
  return <ul className="st-reasons">{list.map((r, i) => { const [code, ...rest] = String(r).split(': '); return <li key={i}><code>{code}</code>{rest.length ? ` ${rest.join(': ')}` : ''}</li>; })}</ul>;
}
export const key = () => 'k' + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
