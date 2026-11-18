/* Sub-workspace: the items of one domain (Market data, Market intelligence, Signals, Setup …) as tabs, as editor tabs
 * in VS Code — inside the main workspace tab of that domain.
 * Why: a domain has many items (dashboards, boards, settings); each opens as its own sub-tab beside the others, keeps
 * its state while hidden, and two to four items can be seen side by side (split), each pane with its own sub-tabs.
 * Purpose: the sub-tab bar (open, close, drag to another pane, split right, open in a new browser tab or window, add
 * any item), up to four panes side by side with resizable widths, the item in focus mirrored to the domain's own
 * section (side bar, links and the search open items here). Remembered per domain in this browser.
 * Must never: show an item twice, or poll from a hidden sub-tab (ViewVisible is false there). */
import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react';
import ErrorBoundary from './ErrorBoundary';
import type { View } from './layout';
import { useViewVisible, ViewVisible } from './visibility';
import { openWorkspace } from './workspaceLinks';

export type SubItem = { id: string; label: string; group?: string; hint?: string };
type Pane = { tabs: string[]; active: string };
type State = { panes: Pane[]; focus: number; widths: number[] };
const MAX = 4;

function read(key: string): unknown { try { return JSON.parse(localStorage.getItem(key) ?? 'null'); } catch { return null; } }
function write(key: string, v: unknown) { try { localStorage.setItem(key, JSON.stringify(v)); } catch { /* private window */ } }

/* Saved state made valid for today's items: unknown ids dropped, no item twice, never empty. */
export function normaliseSub(saved: unknown, ids: string[], initial: string[], first: string): State {
  const raw = saved as Partial<State> | null;
  const seen = new Set<string>();
  let panes: Pane[] = Array.isArray(raw?.panes) ? raw!.panes.slice(0, MAX).map(p => {
    const tabs = (Array.isArray(p?.tabs) ? p.tabs : []).filter(x => typeof x === 'string' && ids.includes(x) && !seen.has(x) && (seen.add(x), true));
    return { tabs, active: tabs.includes(p?.active) ? p.active : tabs[0] };
  }).filter(p => p.tabs.length) : [];
  if (!panes.length) {
    const tabs = initial.filter(x => ids.includes(x));
    panes = [{ tabs: tabs.length ? tabs : [ids[0]], active: ids.includes(first) && tabs.includes(first) ? first : (tabs[0] ?? ids[0]) }];
  }
  const focus = Math.min(Math.max(0, Number(raw?.focus) || 0), panes.length - 1);
  const widths = Array.isArray(raw?.widths) && raw!.widths.length === panes.length ? raw!.widths.map(Number) : panes.map(() => 100 / panes.length);
  return { panes, focus, widths };
}

export function SubWorkspace({ view, items, tab, setTab, render, initial, label }: {
  view: View; items: SubItem[]; tab: string; setTab: (id: string) => void; render: (id: string) => ReactNode; initial?: string[]; label: string;
}) {
  const ids = items.map(i => i.id), key = `corei.subworkspace.v2.${view}`;
  const [st, setSt] = useState<State>(() => normaliseSub(read(key), ids, initial ?? ids, tab));
  const [drag, setDrag] = useState<string | null>(null), [over, setOver] = useState<number | null>(null);
  const mounted = useRef(new Set<string>());
  const box = useRef<HTMLDivElement>(null);
  const visible = useViewVisible();
  const byId = new Map(items.map(i => [i.id, i]));
  const commit = (next: State) => { setSt(next); write(key, next); };
  useEffect(() => { setSt(s => normaliseSub(s, ids, initial ?? ids, tab)); }, [ids.join()]); // eslint-disable-line react-hooks/exhaustive-deps

  /* Open an item: focus it where it is open, otherwise add it to the pane in focus, right of its active tab. */
  const open = (id: string, s: State = st): State => {
    if (!ids.includes(id)) return s;
    const at = s.panes.findIndex(p => p.tabs.includes(id));
    const panes = s.panes.map(p => ({ ...p, tabs: [...p.tabs] }));
    if (at >= 0) { panes[at].active = id; return { ...s, panes, focus: at }; }
    const p = panes[s.focus] ?? panes[0], i = p.tabs.indexOf(p.active);
    p.tabs.splice(i + 1, 0, id); p.active = id;
    return { ...s, panes };
  };
  // The domain's own section (side bar, links, search) opens or focuses the item here.
  useEffect(() => { if (ids.includes(tab)) { const n = open(tab); if (JSON.stringify(n) !== JSON.stringify(st)) commit(n); } }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const on = (e: Event) => { const d = (e as CustomEvent).detail as { view: View; section?: string }; if (d.view === view && d.section && ids.includes(d.section)) setSt(s => { const n = open(d.section!, s); write(key, n); return n; }); };
    window.addEventListener('corei-goto', on); return () => window.removeEventListener('corei-goto', on);
  }, [view, ids.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  const focusedActive = st.panes[st.focus]?.active;
  // The sub-tab in focus tells the domain which item is shown — but only when it changes, never on the first render:
  // a link that opened this domain at an item (goto) must not be overridden by the remembered layout's active tab.
  const lastActive = useRef(focusedActive);
  useEffect(() => {
    if (lastActive.current === focusedActive) return;
    lastActive.current = focusedActive;
    if (focusedActive && focusedActive !== tab) setTab(focusedActive);
  }, [focusedActive]); // eslint-disable-line react-hooks/exhaustive-deps

  const activate = (k: number, id: string) => commit({ ...st, focus: k, panes: st.panes.map((p, j) => j === k ? { ...p, active: id } : p) });
  const close = (k: number, id: string) => {
    let panes = st.panes.map(p => ({ ...p, tabs: [...p.tabs] }));
    const p = panes[k], i = p.tabs.indexOf(id);
    p.tabs.splice(i, 1);
    if (p.active === id) p.active = p.tabs[Math.min(i, p.tabs.length - 1)];
    mounted.current.delete(id);
    let focus = st.focus;
    if (!p.tabs.length) {
      if (panes.length > 1) { panes = panes.filter((_, j) => j !== k); focus = Math.max(0, Math.min(focus === k ? k - 1 : focus > k ? focus - 1 : focus, panes.length - 1)); }
      else { p.tabs = [ids[0]]; p.active = ids[0]; }
    }
    commit({ panes, focus, widths: panes.map(() => 100 / panes.length) });
  };
  const moveTo = (id: string, to: number) => {
    const from = st.panes.findIndex(p => p.tabs.includes(id));
    let panes = st.panes.map(p => ({ ...p, tabs: p.tabs.filter(x => x !== id) }));
    if (to >= panes.length) panes.push({ tabs: [], active: id });
    panes[to].tabs.push(id); panes[to].active = id;
    if (from >= 0 && panes[from].active === id) panes[from].active = panes[from].tabs[0];
    const before = panes.length;
    panes = panes.filter(p => p.tabs.length);
    const focus = Math.min(to - (before !== panes.length && from < to ? 1 : 0), panes.length - 1);
    commit({ panes, focus, widths: panes.map(() => 100 / panes.length) });
  };
  /* Split right: the active item of a pane moves to a new pane on its right (with one tab only, the next item opens). */
  const split = (k: number) => {
    if (st.panes.length >= MAX) return;
    const p = st.panes[k];
    if (p.tabs.length > 1) { moveTo(p.active, st.panes.length); return; }
    const next = ids.find(x => !st.panes.some(q => q.tabs.includes(x)));
    if (!next) return;
    const panes = [...st.panes, { tabs: [next], active: next }];
    commit({ panes, focus: panes.length - 1, widths: panes.map(() => 100 / panes.length) });
  };
  const resize = (k: number, dx: number) => setSt(s => {
    const total = box.current?.clientWidth || 1000, w = s.widths.length === s.panes.length ? [...s.widths] : s.panes.map(() => 100 / s.panes.length);
    const pair = w[k] + w[k + 1], a = Math.min(Math.max(12, w[k] + (dx / total) * 100), pair - 12);
    w[k] = a; w[k + 1] = pair - a; const n = { ...s, widths: w }; write(key, n); return n;
  });
  const unopened = items.filter(i => !st.panes.some(p => p.tabs.includes(i.id)));
  const multi = st.panes.length > 1;

  return <div className={`sw ${multi ? 'sw-multi' : ''}`} ref={box} aria-label={`${label} sub-workspace`}>
    {st.panes.map((p, k) => <Fragment key={k}>
      <section className={`sw-pane ${st.focus === k ? 'focused' : ''} ${over === k && drag ? 'drop' : ''}`} style={multi ? { flexBasis: `${st.widths[k] ?? 100 / st.panes.length}%` } : undefined}
        onMouseDown={() => { if (st.focus !== k) commit({ ...st, focus: k }); }} aria-label={`${label} pane ${k + 1}`}>
        <div className="sw-bar" role="tablist" aria-label={`${label} items${multi ? `, pane ${k + 1}` : ''}`}
          onDragOver={e => { if (drag) { e.preventDefault(); setOver(k); } }} onDragLeave={() => setOver(o => (o === k ? null : o))}
          onDrop={e => { if (drag) { e.preventDefault(); moveTo(drag, k); setDrag(null); setOver(null); } }}>
          <span className="sw-tabs">{p.tabs.map(id => { const it = byId.get(id); if (!it) return null;
            return <span key={id} className={`sw-tab ${p.active === id ? 'active' : ''}`} draggable onDragStart={e => { e.dataTransfer.setData('text/plain', id); e.dataTransfer.effectAllowed = 'move'; setDrag(id); }}
              onDragEnd={() => { setDrag(null); setOver(null); }}>
              <button role="tab" aria-selected={p.active === id} title={it.hint ?? it.label} onClick={() => activate(k, id)}
                onAuxClick={e => { if (e.button === 1) { e.preventDefault(); close(k, id); } }}>{it.label}</button>
              <button className="sw-x" aria-label={`Close ${it.label}`} title="Close (middle-click also closes)" onClick={() => close(k, id)}>×</button>
            </span>; })}</span>
          <span className="sw-tools">
            {unopened.length > 0 && <select aria-label={`Open a ${label} item${multi ? ` in pane ${k + 1}` : ''}`} value="" title="Open another item here"
              onChange={e => { const id = e.target.value; if (!id) return; const s = { ...st, focus: k }; commit(open(id, s)); }}>
              <option value="">+ Open…</option>{unopened.map(i => <option key={i.id} value={i.id}>{i.group ? `${i.group} › ` : ''}{i.label}</option>)}</select>}
            {k > 0 && <button aria-label={`Move ${byId.get(p.active)?.label} to the pane on the left`} title="Move this item to the pane on the left" onClick={() => moveTo(p.active, k - 1)}>◀</button>}
            {k < st.panes.length - 1 && <button aria-label={`Move ${byId.get(p.active)?.label} to the pane on the right`} title="Move this item to the pane on the right" onClick={() => moveTo(p.active, k + 1)}>▶</button>}
            {st.panes.length < MAX && <button aria-label={`Split ${byId.get(p.active)?.label} to the right`} title="Split right: see two items side by side" onClick={() => split(k)}><span aria-hidden="true">◫</span> Split</button>}
            <button aria-label={`Open ${byId.get(p.active)?.label} in new tab`} title="Open this item in a new browser tab" onClick={() => openWorkspace(view, false, p.active)}>↗</button>
            <button aria-label={`Open ${byId.get(p.active)?.label} in new window`} title="Open this item in a new browser window" onClick={() => openWorkspace(view, true, p.active)}>⧉</button>
          </span>
        </div>
        <div className="sw-body">
          {p.tabs.filter(id => id === p.active || mounted.current.has(id)).map(id => { mounted.current.add(id);
            return <div key={id} className="sw-item" hidden={id !== p.active} role="tabpanel" aria-label={byId.get(id)?.label}>
              <ViewVisible.Provider value={visible && id === p.active}><ErrorBoundary name={byId.get(id)?.label ?? id}>{render(id)}</ErrorBoundary></ViewVisible.Provider></div>; })}
        </div>
      </section>
      {multi && k < st.panes.length - 1 && <Handle onMove={dx => resize(k, dx)} />}
    </Fragment>)}
  </div>;
}

function Handle({ onMove }: { onMove: (dx: number) => void }) {
  const start = (e: React.PointerEvent) => {
    let x = e.clientX;
    const mv = (ev: PointerEvent) => { onMove(ev.clientX - x); x = ev.clientX; };
    const up = () => { window.removeEventListener('pointermove', mv); window.removeEventListener('pointerup', up); };
    window.addEventListener('pointermove', mv); window.addEventListener('pointerup', up);
  };
  return <div className="sw-handle" role="separator" aria-orientation="vertical" aria-label="Resize panes" tabIndex={0} onPointerDown={start}
    onKeyDown={e => { if (e.key === 'ArrowLeft') onMove(-24); if (e.key === 'ArrowRight') onMove(24); }} />;
}
