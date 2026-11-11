/*
 * Detail panel docked to the right edge, full height between the title bar and the
 * status bar (as the Properties pane in MT5 or a side editor in VS Code): it does not
 * float or move. Drag its left edge, or use the arrow keys on that edge, to change the
 * width; "Wide" toggles a wide layout; Esc or ✕ closes. Width is remembered per storageKey.
 * (The component keeps its old name so every editor uses it unchanged.)
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

const MIN_W = 380;
const maxWidth = () => Math.max(MIN_W, Math.round(window.innerWidth * 0.8));
const clamp = (w: number) => Math.min(maxWidth(), Math.max(MIN_W, Math.round(w)));

function initial(storageKey: string, width = 560): number {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    if (Number.isFinite(saved?.w)) return clamp(saved.w);
  } catch { /* default width */ }
  return clamp(Math.min(width, window.innerWidth * 0.5));
}

export default function FloatingWindow({ title, subtitle, onClose, children, footer, width, storageKey = 'corei-os-mini.window.v1' }:
  { title: string; subtitle?: string; onClose: () => void; children: ReactNode; footer?: ReactNode; width?: number; storageKey?: string }) {
  const [w, setW] = useState(() => initial(storageKey, width));
  const [wide, setWide] = useState(false);
  const drag = useRef<{ x: number; w: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify({ w })); } catch { /* preference only */ }
    window.dispatchEvent(new CustomEvent('corei-pref', { detail: { key: storageKey, value: { w } } }));
  }, [w, storageKey]);
  useEffect(() => { const onResize = () => setW(x => clamp(x)); window.addEventListener('resize', onResize); return () => window.removeEventListener('resize', onResize); }, []);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('.dock-head')?.focus(); }, []);
  const shown = wide ? maxWidth() : w;
  return <div ref={ref} className={`fw dock ${wide ? 'fw-max' : ''}`} style={{ width: shown }} role="dialog" aria-modal="false" aria-label={title}
    onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
    <div className="dock-edge" role="separator" aria-orientation="vertical" aria-label="Resize panel" tabIndex={0} aria-valuenow={shown} aria-valuemin={MIN_W} aria-valuemax={maxWidth()}
      onPointerDown={e => { if (e.button !== 0) return; e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, w: shown }; setWide(false); e.preventDefault(); }}
      onPointerMove={e => { if (drag.current) setW(clamp(drag.current.w + (drag.current.x - e.clientX))); }}
      onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}
      onKeyDown={e => { if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') { e.preventDefault(); setWide(false); setW(x => clamp(x + (e.key === 'ArrowLeft' ? 32 : -32))); } }} />
    <div className="fw-title dock-head" tabIndex={-1}>
      <div className="fw-caption"><strong>{title}</strong>{subtitle && <small>{subtitle}</small>}</div>
      <button type="button" title={wide ? 'Normal width' : 'Wide'} aria-label={wide ? 'Restore window' : 'Maximise window'} aria-pressed={wide} onClick={() => setWide(x => !x)}>{wide ? '⇥' : '⇤'}</button>
      <button type="button" title="Close (Esc)" aria-label="Close window" onClick={onClose}>✕</button>
    </div>
    <div className="fw-body">{children}</div>
    {footer && <div className="fw-footer">{footer}</div>}
  </div>;
}
