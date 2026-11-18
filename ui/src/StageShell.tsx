/* The frame every trading stage uses, so every stage looks and reads the same.
 * Why: 1000+ users move between eight stages; the same layout everywhere means learning it once.
 * Purpose: sign-in, the stage header (number, title, the question it answers, what comes in and goes out, with
 * links to the neighbouring stages), the grouped section bar (Overview · Work · Monitor · Configure, plus the stage
 * map), the section's title and one-line explanation, and the re-authentication dialog.
 * Must never: hold stage data (each stage view does). */
import { Fragment, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { openWorkspace } from './workspaceLinks';
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

/* Every section of the stage on one page, each a panel that opens and closes (remembered per viewer), with an index:
 * all features, options and controls in view at once. One section alone is still a click away.
 * Sub-workspaces (as editor groups in VS Code): the panels can be laid out in up to four panes side by side — split a
 * panel to the right, drag a panel's title to another pane, choose 1–4 columns, resize the panes. Each pane scrolls on
 * its own; the layout is remembered per screen. A panel (or the whole screen) opens in a new tab or window. */
export type Panel = { id: string; label: string; group: string; hint: string; render: () => ReactNode };
const MAX_PANES = 4;
function readJson<T>(k: string, fallback: T): T { try { const v = JSON.parse(localStorage.getItem(k) ?? 'null'); return v ?? fallback; } catch { return fallback; } }
function writeJson(k: string, v: unknown) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private window */ } }
/* Keep the saved panes valid for today's panels: unknown ids dropped, new panels added to the first pane. */
export function normalisePanes(saved: unknown, ids: string[]): string[][] {
  const panes = Array.isArray(saved) ? saved.filter(Array.isArray).slice(0, MAX_PANES).map(p => (p as unknown[]).filter((x): x is string => typeof x === 'string' && ids.includes(x))) : [];
  const seen = new Set<string>(); const out = panes.map(p => p.filter(x => (seen.has(x) ? false : (seen.add(x), true))));
  const missing = ids.filter(x => !seen.has(x));
  if (!out.length) return [ids];
  out[0] = [...out[0], ...missing];
  return out.filter((p, k) => p.length || k === 0);
}
export function Workspace({ view, sections, alone, groups = GROUPS }: { view: View; sections: Panel[]; alone: (id: string) => void; groups?: readonly string[] }) {
  const storeKey = `corei.workspace.closed.${view}`, paneKey = `corei.workspace.panes.${view}`, widthKey = `corei.workspace.widths.${view}`;
  const ids = sections.map(x => x.id);
  const [closed, setClosed] = useState<string[]>(() => readJson(storeKey, []));
  const [panes, setPanesRaw] = useState<string[][]>(() => normalisePanes(readJson(paneKey, null), ids));
  const [widths, setWidthsRaw] = useState<number[]>(() => readJson(widthKey, []));
  const [drag, setDrag] = useState<string | null>(null), [over, setOver] = useState<number | null>(null);
  const [height, setHeight] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { setPanesRaw(p => normalisePanes(p, ids)); }, [ids.join()]);
  const save = (next: string[]) => { setClosed(next); writeJson(storeKey, next); };
  const setPanes = (next: string[][]) => { const n = normalisePanes(next.filter((p, k) => p.length || k === 0), ids); setPanesRaw(n); writeJson(paneKey, n); if (n.length !== panes.length) setWidths([]); };
  const setWidths = (w: number[]) => { setWidthsRaw(w); writeJson(widthKey, w); };
  const multi = panes.length > 1;
  // Panes fill the height of the screen and scroll on their own (as editor groups do).
  useLayoutEffect(() => {
    if (!multi) return;
    const measure = () => { const el = box.current, sc = el?.closest('.view') as HTMLElement | null; if (el && sc) setHeight(Math.max(320, sc.clientHeight - 56)); };
    measure(); window.addEventListener('resize', measure); return () => window.removeEventListener('resize', measure);
  }, [multi]);
  const toggle = (id: string) => save(closed.includes(id) ? closed.filter(x => x !== id) : [...closed, id]);
  const jump = (id: string) => { if (closed.includes(id)) save(closed.filter(x => x !== id)); window.setTimeout(() => document.getElementById(`ws-${view}-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 30); };
  const paneOf = (id: string) => panes.findIndex(p => p.includes(id));
  const move = (id: string, to: number, before?: string) => {
    const next = panes.map(p => p.filter(x => x !== id));
    while (next.length <= to && next.length < MAX_PANES) next.push([]);
    const t = Math.min(to, next.length - 1), at = before ? next[t].indexOf(before) : -1;
    if (at >= 0) next[t].splice(at, 0, id); else next[t].push(id);
    setPanes(next);
  };
  const splitRight = (id: string) => { const from = paneOf(id); if (panes.length >= MAX_PANES && from === panes.length - 1) return; move(id, Math.min(from + 1 === panes.length ? panes.length : from + 1, MAX_PANES - 1)); };
  /* Columns by purpose: 1 = everything; 2 = dashboards & work | monitors & settings; 3 = dashboards | work | monitors & settings; 4 = one per group. */
  const columns = (n: number) => {
    if (n === 1) { setPanes([ids]); return; }
    const by = (gs: string[]) => sections.filter(x => gs.includes(x.group)).map(x => x.id);
    const g = [...groups];
    const plan = n === 2 ? [g.slice(0, 2), g.slice(2)] : n === 3 ? [g.slice(0, 1), g.slice(1, 2), g.slice(2)] : g.map(x => [x]);
    const next = plan.map(by).filter(p => p.length);
    const rest = ids.filter(x => !next.flat().includes(x));
    if (next.length) next[next.length - 1].push(...rest);
    setPanes(next.length ? next : [ids]);
  };
  const w = panes.map((_, k) => widths.length === panes.length ? widths[k] : 100 / panes.length);
  const resize = (k: number, dx: number) => {
    const total = box.current?.clientWidth || 1000, d = (dx / total) * 100;
    setWidthsRaw(prev => {
      const cur = prev.length === panes.length ? [...prev] : panes.map(() => 100 / panes.length);
      const pair = cur[k] + cur[k + 1], a = Math.min(Math.max(12, cur[k] + d), pair - 12);
      cur[k] = a; cur[k + 1] = pair - a; writeJson(widthKey, cur); return cur;
    });
  };
  const byId = new Map(sections.map(x => [x.id, x]));
  const panel = (s: Panel, k: number) => <section key={s.id} id={`ws-${view}-${s.id}`} className={`ws-panel ${closed.includes(s.id) ? 'closed' : ''} ${drag === s.id ? 'dragging' : ''}`} aria-label={s.label}
    onDragOver={e => { if (drag && drag !== s.id) { e.preventDefault(); setOver(k); } }} onDrop={e => { if (drag && drag !== s.id) { e.preventDefault(); e.stopPropagation(); move(drag, k, s.id); setDrag(null); setOver(null); } }}>
    <header className="ws-head" onClick={() => toggle(s.id)} draggable onDragStart={e => { e.dataTransfer.setData('text/plain', s.id); e.dataTransfer.effectAllowed = 'move'; setDrag(s.id); }} onDragEnd={() => { setDrag(null); setOver(null); }}
      title="Click to open or close · drag to another pane">
      <span className="ws-caret">{closed.includes(s.id) ? '▸' : '▾'}</span><b>{s.label}</b><small className="cfg-pill">{s.group}</small><small className="cfg-note">{s.hint}</small>
      <span className="ws-tools" onClick={e => e.stopPropagation()}>
        {multi && k > 0 && <button className="ws-ico" aria-label={`Move ${s.label} to the pane on the left`} title="Move to the pane on the left" onClick={() => move(s.id, k - 1)}>◀</button>}
        {(k < panes.length - 1 || panes.length < MAX_PANES) && <button className="ws-ico" aria-label={`Move ${s.label} to the right`} title={k < panes.length - 1 ? 'Move to the pane on the right' : 'Split: open in a new pane on the right'} onClick={() => k < panes.length - 1 ? move(s.id, k + 1) : splitRight(s.id)}>{k < panes.length - 1 ? '▶' : '◫'}</button>}
        <button className="ws-ico" aria-label={`Open ${s.label} alone`} title="Open alone (only this panel)" onClick={() => alone(s.id)}>⤢</button>
        <button className="ws-ico" aria-label={`Open ${s.label} in new tab`} title="Open this panel in a new browser tab" onClick={() => openWorkspace(view, false, s.id)}>↗</button>
        <button className="ws-ico" aria-label={`Open ${s.label} in new window`} title="Open this panel in a new browser window" onClick={() => openWorkspace(view, true, s.id)}>⧉</button>
      </span>
    </header>
    {!closed.includes(s.id) && <div className="ws-body"><ErrorBoundary name={s.label}>{s.render()}</ErrorBoundary></div>}
  </section>;
  return <div className="ws">
    <nav className="ws-index" aria-label="Workspace index">
      {groups.map(g => <span key={g} className="ws-index-group"><b>{g}</b>{sections.filter(x => x.group === g).map(x =>
        <button key={x.id} className={closed.includes(x.id) ? 'off' : ''} onClick={() => jump(x.id)} title={multi ? `In pane ${paneOf(x.id) + 1}` : x.hint}>{multi && <i className="ws-pane-n">{paneOf(x.id) + 1}</i>}{x.label}</button>)}</span>)}
      <span className="ws-index-all">
        <span className="ws-cols" role="group" aria-label="Panes side by side">{[1, 2, 3, 4].map(n => <button key={n} aria-pressed={panes.length === n} title={n === 1 ? 'One pane: every panel' : `${n} panes side by side, panels grouped by purpose`} onClick={() => columns(n)}
          aria-label={`${n} pane${n > 1 ? 's' : ''}`}><span className={`ws-colicon c${n}`} aria-hidden="true">{Array.from({ length: n }, (_, k) => <i key={k} />)}</span></button>)}</span>
        <button onClick={() => save([])}>Open all</button><button onClick={() => save(ids)}>Close all</button>
      </span>
    </nav>
    <div ref={box} className={`ws-panes ${multi ? 'multi' : ''}`} style={multi && height ? { height } : undefined}>
      {panes.map((p, k) => <Fragment key={k}>
        <div className={`ws-pane ${over === k && drag ? 'drop' : ''}`} style={multi ? { flexBasis: `${w[k]}%` } : undefined}
          onDragOver={e => { if (drag) { e.preventDefault(); setOver(k); } }} onDragLeave={() => setOver(o => (o === k ? null : o))}
          onDrop={e => { if (drag) { e.preventDefault(); move(drag, k); setDrag(null); setOver(null); } }}>
          {multi && <div className="ws-pane-head"><b>Pane {k + 1}</b><small>{p.length} panel{p.length === 1 ? '' : 's'}</small>
            <select aria-label={`Add a panel to pane ${k + 1}`} value="" onChange={e => e.target.value && move(e.target.value, k)}>
              <option value="">+ Add panel…</option>{sections.filter(x => !p.includes(x.id)).map(x => <option key={x.id} value={x.id}>{x.label}</option>)}</select>
            {k > 0 && <button className="ws-ico" aria-label={`Close pane ${k + 1}`} title="Close this pane (its panels go to pane 1)" onClick={() => setPanes(panes.map((q, j) => j === 0 ? [...q, ...p] : q).filter((_, j) => j !== k))}>×</button>}</div>}
          {p.map(id => byId.get(id)).filter(Boolean).map(x => panel(x as Panel, k))}
          {multi && !p.length && <p className="ws-empty">Drag a panel title here, or add one above.</p>}
        </div>
        {multi && k < panes.length - 1 && <PaneHandle onMove={dx => resize(k, dx)} />}
      </Fragment>)}
    </div>
  </div>;
}

function PaneHandle({ onMove }: { onMove: (dx: number) => void }) {
  const start = (e: React.PointerEvent) => {
    let x = e.clientX; (e.target as HTMLElement).setPointerCapture(e.pointerId);
    const mv = (ev: PointerEvent) => { onMove(ev.clientX - x); x = ev.clientX; };
    const up = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up);
  };
  return <div className="ws-handle" role="separator" aria-orientation="vertical" aria-label="Resize panes" tabIndex={0} onPointerDown={start}
    onKeyDown={e => { if (e.key === 'ArrowLeft') onMove(-24); if (e.key === 'ArrowRight') onMove(24); }} />;
}

/* The same header on every stage workspace: the trading flow 1 → 8 (click to go; the current stage is marked), then
 * what is shown, what this stage answers and what it hands on. */
export function StageBar({ view, title, hint }: { view: View; title: string; hint: string }) {
  const stage = stageOf(view);
  if (!stage) return <header className="cfg-head"><div><h1>{title}</h1><p>{hint}</p></div></header>;
  return <header className="cfg-head st-head st-bar">
    <ol className="st-steps" aria-label="Trading flow">{PIPELINE.map((p, k) => <li key={p.view} className={p.view === view ? 'on' : k < PIPELINE.indexOf(stage) ? 'before' : ''}>
      <button onClick={() => goto(p.view)} aria-current={p.view === view ? 'step' : undefined} title={`${p.n} · ${p.title}\n${p.question}`}>
        <span className="st-n">{p.n}</span><span className="st-l">{p.view === view ? `Stage ${p.n} · ${p.title}` : p.short ?? p.title}</span></button></li>)}</ol>
    <div className="st-title"><h1>{title}</h1><p>{hint}</p></div>
    <p className="st-question" title={`${stage.question} It hands on: ${stage.feeds}.`}><b>Answers:</b> {stage.question} <b>Hands on:</b> {stage.feeds}.</p>
  </header>;
}

export default function StageShell({ view, sections }: { view: View; sections: Section[] }) {
  const all = [{ id: 'workspace', label: 'Workspace (all)', group: 'Overview' as const, hint: 'Every panel of this stage on one page — dashboards, work, monitors and settings — each opens and closes.', render: () => null },
    ...sections, { id: 'stage', label: 'Stage map', group: 'Configure' as const, hint: 'What this stage does, its components and how far each is built.', render: () => null }];
  const [tab, setTab] = useSection<string>(view, 'workspace', all.map(s => s.id));
  useEffect(() => { publishSections(view, all.map(s => ({ id: s.id, label: s.label, group: s.group }))); }, [view, all.map(s => s.id).join()]);
  const [user, setUser] = useState<Row | null | undefined>(undefined), [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check(); window.addEventListener('corei-session', check); return () => window.removeEventListener('corei-session', check);
  }, []);
  const stage = stageOf(view)!;
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
      <StageBar view={view} title={current.label} hint={current.hint} />
      <div className="cfg-pane"><StartHere ctx={ctx} />{tab === 'workspace' ? <Workspace view={view} sections={sections.map(x => ({ ...x, render: () => x.render(ctx) }))} alone={setTab} /> : current.render(ctx)}</div>
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
