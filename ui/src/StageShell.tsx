/* The frame every trading stage uses, so every stage looks and reads the same.
 * Why: 1000+ users move between eight stages; the same layout everywhere means learning it once.
 * Purpose: sign-in, the stage header (number, title, the question it answers, what comes in and goes out, with
 * links to the neighbouring stages), the grouped section bar (Overview · Work · Monitor · Configure, plus the stage
 * map), the section's title and one-line explanation, and the re-authentication dialog.
 * Must never: hold stage data (each stage view does). */
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import { openWorkspace } from './workspaceLinks';
import { SubWorkspace } from './SubWorkspace';
import { useViewVisible } from './visibility';
import { addReauthHandler, api } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { SignIn } from './Infrastructure';
import Stage from './Pipeline';
import { PIPELINE, stageOf } from './flow';
import type { View } from './layout';
import { goto, publishSections, useSection } from './nav';
import { StartHere } from './QuickStart';
import ErrorBoundary from './ErrorBoundary';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

export type Row = Record<string, any>;
export type Section = { id: string; label: string; group: 'Overview' | 'Work' | 'Monitor' | 'Configure'; hint: string; render: (ctx: Ctx) => ReactNode };
export type Ctx = { user: Row; canOps: boolean; canRisk: boolean; open: (section: string) => void };
const GROUPS = ['Overview', 'Work', 'Monitor', 'Configure'] as const;

/* "All panels": every item of the domain on one page, each a panel that opens and closes (remembered per viewer), with
 * an index. Each panel opens as its own sub-tab (⤢), in a new browser tab (↗) or window (⧉). */
export type Panel = { id: string; label: string; group: string; hint: string; render: () => ReactNode };
function readJson<T>(k: string, fallback: T): T { try { const v = JSON.parse(localStorage.getItem(k) ?? 'null'); return v ?? fallback; } catch { return fallback; } }
function writeJson(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window */ } }
export function Workspace({ view, sections, alone, groups = GROUPS }: { view: View; sections: Panel[]; alone: (id: string) => void; groups?: readonly string[] }) {
  const storeKey = `corei.workspace.closed.${view}`;
  const [closed, setClosed] = useState<string[]>(() => readJson(storeKey, []));
  const save = (next: string[]) => { setClosed(next); writeJson(storeKey, next); };
  const toggle = (id: string) => save(closed.includes(id) ? closed.filter(x => x !== id) : [...closed, id]);
  return <div className="ws">
    <nav className="ws-index ws-index-slim" aria-label="All panels">
      <small className="cfg-note">Every item below, each opens and closes · open one as its own sub-tab with ⤢</small>
      <span className="ws-index-all"><button onClick={() => save([])}>Open all</button><button onClick={() => save(sections.map(x => x.id))}>Close all</button></span>
    </nav>
    {sections.map(s => <section key={s.id} id={`ws-${view}-${s.id}`} className={`ws-panel ${closed.includes(s.id) ? 'closed' : ''}`} aria-label={s.label}>
      <header className="ws-head" onClick={() => toggle(s.id)} title="Click to open or close">
        <span className="ws-caret">{closed.includes(s.id) ? '▸' : '▾'}</span><b>{s.label}</b><small className="cfg-pill">{s.group}</small><small className="cfg-note">{s.hint}</small>
        <span className="ws-tools" onClick={e => e.stopPropagation()}>
          <button className="ws-ico" aria-label={`Open ${s.label} alone`} title="Open as its own sub-tab" onClick={() => alone(s.id)}>⤢</button>
          <button className="ws-ico" aria-label={`Open ${s.label} in new tab`} title="Open this item in a new browser tab" onClick={() => openWorkspace(view, false, s.id)}>↗</button>
          <button className="ws-ico" aria-label={`Open ${s.label} in new window`} title="Open this item in a new browser window" onClick={() => openWorkspace(view, true, s.id)}>⧉</button>
        </span>
      </header>
      {!closed.includes(s.id) && <div className="ws-body"><ErrorBoundary name={s.label}>{s.render()}</ErrorBoundary></div>}
    </section>)}
  </div>;
}

/* The same header on every stage workspace: the item shown and what it is for, then what this stage answers and what
 * it hands on. The stages themselves are the main workspaces (rail and main tabs), so they are not repeated here. */
export function StageBar({ view, title, hint }: { view: View; title: string; hint: string }) {
  const stage = stageOf(view);
  if (!stage) return <header className="cfg-head"><div><h1>{title}</h1><p>{hint}</p></div></header>;
  return <header className="cfg-head st-head st-bar">
    <div className="st-title"><span className="st-crumb">Stage {stage.n} · {stage.title} ›</span><h1>{title}</h1><p>{hint}</p></div>
    <p className="st-question" title={`${stage.question} It hands on: ${stage.feeds}.`}><b>Answers:</b> {stage.question} <b>Hands on:</b> {stage.feeds}.</p>
  </header>;
}

export default function StageShell({ view, sections, title, allPanels = true }: { view: View; sections: Section[]; title?: string; allPanels?: boolean }) {
  const isStage = Boolean(stageOf(view));
  const all = [...(allPanels ? [{ id: 'workspace', label: 'All panels', group: 'Overview' as const, hint: 'Every item of this stage on one page — dashboards, work, monitors and settings — each opens and closes.', render: () => null }] : []),
    ...sections, ...(isStage ? [{ id: 'stage', label: 'Stage map', group: 'Configure' as const, hint: 'What this stage does, its components and how far each is built.', render: () => null }] : [])];
  // One order everywhere: by group (Overview, Work, Monitor, Configure), as the side bar shows them.
  all.sort((a, b) => GROUPS.indexOf(a.group as typeof GROUPS[number]) - GROUPS.indexOf(b.group as typeof GROUPS[number]));
  const [tab, setTab] = useSection<string>(view, all[0].id, all.map(s => s.id));
  useEffect(() => { publishSections(view, all.map(s => ({ id: s.id, label: s.label, group: s.group }))); }, [view, all.map(s => s.id).join()]);
  const [user, setUser] = useState<Row | null | undefined>(undefined), [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check(); window.addEventListener('corei-session', check); return () => window.removeEventListener('corei-session', check);
  }, []);
  const stage = stageOf(view) ?? { title: title ?? String(view) };
  const current = all.find(s => s.id === tab) ?? all[0];
  const nav = <nav className="cfg-nav" aria-label={`${stage.title} sections`}>
    {GROUPS.map(g => <Fragment key={g}><span className="cfg-nav-group">{g}</span>
      {all.filter(s => s.group === g).map(s => <button key={s.id} aria-current={tab === s.id ? 'page' : undefined} onClick={() => setTab(s.id)}><span>{s.label}</span></button>)}</Fragment>)}
  </nav>;
  if (tab === 'stage' && !user) return <div className="cfg md">{nav}<Stage view={view} open={v => goto(v)} /></div>;
  if (user === undefined) return <div className="cfg md">{nav}<div className="content"><p>Checking sign-in…</p></div></div>;
  if (user === null) return <div className="cfg md">{nav}<SignIn title={stage.title.toUpperCase()} done={s => setUser(s)} /></div>;
  const roles: string[] = user.roles ?? [];
  const ctx: Ctx = { user, canOps: roles.includes('TRADING_OPS') || roles.includes('ADMIN'), canRisk: roles.includes('RISK_APPROVER') || roles.includes('ADMIN'), open: setTab };
  return <div className="cfg md">
    {nav}
    <section className="cfg-main">
      <StageBar view={view} title={current.label} hint={current.hint} />
      {isStage && tab === 'workspace' && <StartHere ctx={ctx} />}
      <SubWorkspace view={view} label={stage.title} tab={tab} setTab={setTab} initial={all.map(x => x.id)}
        items={all.map(x => ({ id: x.id, label: x.label, group: x.group, hint: x.hint }))}
        render={id => <div className="cfg-pane">{id === 'workspace' ? <Workspace view={view} sections={sections.map(x => ({ ...x, render: () => x.render(ctx) }))} alone={setTab} />
          : id === 'stage' ? <Stage view={view} open={v => goto(v)} /> : (all.find(x => x.id === id) ?? all[0]).render(ctx)}</div>} />
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

/* Small shared pieces for the stage views. */
export function useData<T = Row>(path: string | null, every = 15_000, deps: unknown[] = []): [T | null, string, () => void] {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState(''), [n, setN] = useState(0);
  const visible = useViewVisible(), loaded = useRef(false);
  useEffect(() => {
    if (!path) return;
    if (!visible && loaded.current) return; // a background workspace keeps what it has and polls again when shown
    loaded.current = true;
    let live = true;
    const load = () => void api(path).then(d => { if (live) { setData(d); setError(''); } }).catch(e => { if (live) setError((e as Error).message); });
    load();
    const t = every ? window.setInterval(load, every) : 0;
    return () => { live = false; if (t) window.clearInterval(t); };
  }, [path, n, visible, ...deps]);
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
