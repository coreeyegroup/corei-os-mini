/*
 * Detail window (account, connection, plan editors). A standard desktop window: drag the
 * title bar to move it, drag any edge or corner to resize, double-click the title (or ▢)
 * to maximise, ⇥ to dock it to the right edge, Esc or ✕ to close. Position, size and
 * mode are remembered per storageKey and kept inside the screen. Keyboard: on the title
 * bar Alt+arrows move by 16 px and Shift+arrows resize. It never blocks the workstation.
 */
import { useEffect, useRef, useState, type ReactNode, type PointerEvent as RPE } from 'react';

type Mode = 'float' | 'max' | 'dock';
type Geo = { x: number; y: number; w: number; h: number; mode: Mode };
const TOP = 32, BOTTOM = 22, MIN_W = 420, MIN_H = 240;
const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as const;
type Edge = typeof EDGES[number] | 'move';

function fit(g: Geo): Geo {
  const W = window.innerWidth, H = window.innerHeight - TOP - BOTTOM;
  const w = Math.min(Math.max(MIN_W, Math.round(g.w)), W), h = Math.min(Math.max(MIN_H, Math.round(g.h)), H);
  const x = Math.min(Math.max(0, Math.round(g.x)), W - w), y = Math.min(Math.max(TOP, Math.round(g.y)), TOP + H - h);
  return { ...g, x, y, w, h };
}

function initial(key: string, width = 720): Geo {
  try {
    const s = JSON.parse(localStorage.getItem(key) || 'null');
    if (s && ['x', 'y', 'w', 'h'].every(k => Number.isFinite(s[k]))) return fit({ ...s, mode: ['float', 'max', 'dock'].includes(s.mode) ? s.mode : 'float' });
  } catch { /* defaults */ }
  const w = Math.min(width, window.innerWidth - 40), h = Math.min(640, window.innerHeight - TOP - BOTTOM - 40);
  return fit({ x: (window.innerWidth - w) / 2, y: TOP + 24, w, h, mode: 'float' });
}

export default function FloatingWindow({ title, subtitle, onClose, children, footer, width, storageKey = 'corei-os-mini.window.v2' }:
  { title: string; subtitle?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number; storageKey?: string }) {
  const key = storageKey.replace(/\.v1$/, '.v2');
  const [g, setG] = useState<Geo>(() => initial(key, width));
  const drag = useRef<{ edge: Edge; px: number; py: number; g: Geo } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(g)); } catch { /* preference only */ }
    window.dispatchEvent(new CustomEvent('corei-pref', { detail: { key, value: g } }));
  }, [g, key]);
  useEffect(() => { const r = () => setG(x => fit(x)); window.addEventListener('resize', r); return () => window.removeEventListener('resize', r); }, []);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('.fw-title')?.focus(); }, []);

  const start = (edge: Edge) => (e: RPE<HTMLElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest('button')) return;
    if (g.mode !== 'float' && edge !== 'w') { if (edge !== 'move') return; }
    e.currentTarget.setPointerCapture(e.pointerId);
    // Dragging a maximised or docked window by its title restores it under the pointer.
    const base = g.mode === 'float' ? g : (() => { const r = initial('', width); return fit({ ...r, x: e.clientX - r.w / 2, y: e.clientY - 12, mode: 'float' as Mode }); })();
    if (g.mode !== 'float' && edge === 'move') setG(base);
    drag.current = { edge, px: e.clientX, py: e.clientY, g: edge === 'w' && g.mode === 'dock' ? geoOfDock(g) : base };
    e.preventDefault();
  };
  const move = (e: RPE<HTMLElement>) => {
    const d = drag.current; if (!d) return;
    const dx = e.clientX - d.px, dy = e.clientY - d.py, o = d.g;
    if (d.edge === 'move') return setG(fit({ ...o, x: o.x + dx, y: o.y + dy }));
    if (o.mode === 'dock') return setG(x => ({ ...x, w: Math.min(window.innerWidth - 80, Math.max(MIN_W, o.w - dx)) }));
    let { x, y, w, h } = o;
    if (d.edge.includes('e')) w = o.w + dx;
    if (d.edge.includes('s')) h = o.h + dy;
    if (d.edge.includes('w')) { w = Math.max(MIN_W, o.w - dx); x = o.x + o.w - w; }
    if (d.edge.includes('n')) { h = Math.max(MIN_H, o.h - dy); y = o.y + o.h - h; }
    setG(fit({ ...o, x, y, w, h }));
  };
  const end = () => { drag.current = null; };
  const toggle = (m: Mode) => setG(x => ({ ...x, mode: x.mode === m ? 'float' : m }));

  const style = g.mode === 'max' ? { left: 0, top: TOP, width: '100vw', height: `calc(100vh - ${TOP + BOTTOM}px)` }
    : g.mode === 'dock' ? { right: 0, top: TOP, width: g.w, height: `calc(100vh - ${TOP + BOTTOM}px)` }
    : { left: g.x, top: g.y, width: g.w, height: g.h };

  return <div ref={ref} className={`fw fw-${g.mode}${g.mode === 'max' ? ' fw-max' : ''}`} style={style} role="dialog" aria-modal="false" aria-label={title}
    onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
    {g.mode === 'float' && EDGES.map(edge => <div key={edge} className={`fw-edge fw-${edge}`} aria-hidden="true"
      onPointerDown={start(edge)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} />)}
    {g.mode === 'dock' && <div className="fw-edge fw-w" aria-hidden="true" onPointerDown={start('w')} onPointerMove={move} onPointerUp={end} onPointerCancel={end} />}
    <div className="fw-title" tabIndex={0} aria-label={`${title} — drag to move; Alt+arrows move, Shift+arrows resize`}
      onPointerDown={start('move')} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
      onDoubleClick={e => { if (!(e.target as HTMLElement).closest('button')) toggle('max'); }}
      onKeyDown={e => {
        const k = e.key, step = 16; if (!k.startsWith('Arrow') || !(e.altKey || e.shiftKey)) return;
        e.preventDefault();
        const dx = k === 'ArrowLeft' ? -step : k === 'ArrowRight' ? step : 0, dy = k === 'ArrowUp' ? -step : k === 'ArrowDown' ? step : 0;
        setG(x => fit(e.shiftKey ? { ...x, mode: 'float', w: x.w + dx, h: x.h + dy } : { ...x, mode: 'float', x: x.x + dx, y: x.y + dy }));
      }}>
      <span className="fw-caption"><strong>{title}</strong>{subtitle && <small>{subtitle}</small>}</span>
      <button type="button" title={g.mode === 'dock' ? 'Float' : 'Dock right'} aria-label={g.mode === 'dock' ? 'Float window' : 'Dock window right'} aria-pressed={g.mode === 'dock'} onClick={() => toggle('dock')}>{g.mode === 'dock' ? '⧉' : '⇥'}</button>
      <button type="button" title={g.mode === 'max' ? 'Restore' : 'Maximise'} aria-label={g.mode === 'max' ? 'Restore window' : 'Maximise window'} aria-pressed={g.mode === 'max'} onClick={() => toggle('max')}>{g.mode === 'max' ? '❐' : '▢'}</button>
      <button type="button" title="Close (Esc)" aria-label="Close window" onClick={onClose}>✕</button>
    </div>
    <div className="fw-body">{children}</div>
    {footer && <div className="fw-footer">{footer}</div>}
  </div>;
}

function geoOfDock(g: Geo): Geo { return { ...g, x: window.innerWidth - g.w }; }
