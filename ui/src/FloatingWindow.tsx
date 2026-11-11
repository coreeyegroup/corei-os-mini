/*
 * Floating, non-modal window: drag by the title bar, resize from any edge or corner,
 * maximise/restore (button or double-click on the title), close (button or Esc), and
 * move/resize from the keyboard (arrow keys on the title bar; Shift+arrows resize).
 * The window always stays inside the browser viewport; its last position and size
 * are remembered per storageKey in this browser.
 */
import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react';

type Box = { x: number; y: number; w: number; h: number };
type Edge = 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
const MIN_W = 340, MIN_H = 220, MARGIN = 8;

function fit(b: Box): Box {
  const w = Math.min(Math.max(MIN_W, b.w), window.innerWidth - 2 * MARGIN);
  const h = Math.min(Math.max(MIN_H, b.h), window.innerHeight - 2 * MARGIN);
  return { w, h, x: Math.min(Math.max(MARGIN, b.x), window.innerWidth - w - MARGIN), y: Math.min(Math.max(MARGIN, b.y), window.innerHeight - h - MARGIN) };
}

function initial(storageKey: string, width = 560): Box {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (saved && ['x', 'y', 'w', 'h'].every(k => Number.isFinite(saved[k]))) return fit(saved);
  } catch { /* fall back to default placement */ }
  const w = Math.min(width, window.innerWidth - 48), h = Math.min(760, window.innerHeight - 120);
  return fit({ w, h, x: window.innerWidth - w - 24, y: 90 });
}

export default function FloatingWindow({ title, subtitle, onClose, children, footer, width, storageKey = 'corei-os-mini.window.v1' }:
  { title: string; subtitle?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number; storageKey?: string }) {
  const [box, setBox] = useState<Box>(() => initial(storageKey, width));
  const [maximized, setMaximized] = useState(false);
  const drag = useRef<{ x: number; y: number; start: Box; edge: Edge | 'move' } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(box)); } catch { /* preference only */ }
    // Also kept per user on the server (Configuration listens and saves it, debounced).
    window.dispatchEvent(new CustomEvent('corei-pref', { detail: { key: storageKey, value: box } }));
  }, [box, storageKey]);
  useEffect(() => { const onResize = () => setBox(b => fit(b)); window.addEventListener('resize', onResize); return () => window.removeEventListener('resize', onResize); }, []);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('.fw-title')?.focus(); }, []);

  function start(e: PointerEvent<HTMLElement>, edge: Edge | 'move') {
    if (e.button !== 0 || maximized || (edge === 'move' && (e.target as HTMLElement).closest('button'))) return;
    e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { x: e.clientX, y: e.clientY, start: box, edge };
  }
  function move(e: PointerEvent<HTMLElement>) {
    const d = drag.current; if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y, s = d.start;
    if (d.edge === 'move') { setBox(fit({ ...s, x: s.x + dx, y: s.y + dy })); return; }
    let { x, y, w, h } = s;
    if (d.edge.includes('e')) w = s.w + dx;
    if (d.edge.includes('s')) h = s.h + dy;
    if (d.edge.includes('w')) { w = Math.max(MIN_W, s.w - dx); x = s.x + s.w - w; }
    if (d.edge.includes('n')) { h = Math.max(MIN_H, s.h - dy); y = s.y + s.h - h; }
    setBox(fit({ x, y, w, h }));
  }
  const end = () => { drag.current = null; };
  function keys(e: KeyboardEvent<HTMLElement>) {
    if (e.target !== e.currentTarget || maximized || !e.key.startsWith('Arrow')) return;
    e.preventDefault();
    const step = 16, dx = e.key === 'ArrowRight' ? step : e.key === 'ArrowLeft' ? -step : 0, dy = e.key === 'ArrowDown' ? step : e.key === 'ArrowUp' ? -step : 0;
    setBox(b => fit(e.shiftKey ? { ...b, w: b.w + dx, h: b.h + dy } : { ...b, x: b.x + dx, y: b.y + dy }));
  }
  const style = maximized ? { left: MARGIN, top: MARGIN, width: `calc(100vw - ${2 * MARGIN}px)`, height: `calc(100vh - ${2 * MARGIN}px)` }
    : { left: box.x, top: box.y, width: box.w, height: box.h };
  const handle = (edge: Edge) => <div key={edge} className={`fw-edge fw-${edge}`} onPointerDown={e => start(e, edge)} onPointerMove={move}
    onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} aria-hidden="true" />;

  return <div ref={ref} className={`fw ${maximized ? 'fw-max' : ''}`} style={style} role="dialog" aria-modal="false" aria-label={title}
    onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
    <div className="fw-title" tabIndex={0} aria-label={`${title} · drag or use arrow keys to move, Shift+arrows to resize`}
      onPointerDown={e => start(e, 'move')} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end}
      onDoubleClick={e => { if (!(e.target as HTMLElement).closest('button')) setMaximized(m => !m); }} onKeyDown={keys}>
      <div className="fw-caption"><strong>{title}</strong>{subtitle && <small>{subtitle}</small>}</div>
      <button type="button" title="Reset size and position" aria-label="Reset window" onClick={() => { setMaximized(false); localStorage.removeItem(storageKey); setBox(initial(storageKey, width)); }}>⟲</button>
      <button type="button" title={maximized ? 'Restore' : 'Maximise'} aria-label={maximized ? 'Restore window' : 'Maximise window'} onClick={() => setMaximized(m => !m)}>{maximized ? '❐' : '□'}</button>
      <button type="button" title="Close (Esc)" aria-label="Close window" onClick={onClose}>✕</button>
    </div>
    <div className="fw-body">{children}</div>
    {footer && <div className="fw-footer">{footer}</div>}
    {!maximized && (['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'] as Edge[]).map(handle)}
  </div>;
}
