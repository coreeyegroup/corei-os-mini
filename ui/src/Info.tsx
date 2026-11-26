/* Info: explanatory text behind an ⓘ button instead of always on screen.
 *
 * Why it exists: every stage, setup page and panel repeated its own explanation (what the page is, what it answers,
 * what it hands on) on every visit, costing two to four lines of a screen used for 10+ hours a day. Experienced users
 * need the space; new users still need the text. As in VS Code (hover / ⓘ for descriptions), the text is one click away.
 * Purpose: <Info> renders an ⓘ button that toggles a small popover with the text; Escape or a click outside closes it.
 * Settings › Workbench › "Help text" can show every explanation inline again (for training or new users).
 * Also: long static notes (p.cfg-note / div.cfg-note) are shown as one line with ⓘ and expand on click
 * (installNoteToggle), unless help text is set to inline.
 * Must never: hide a warning, an error or a blocking reason (those use cfg-error / role="alert" and stay visible).
 * Dependencies: userSettings. Consumers: StageShell, Configuration, Infrastructure, App (home), PaperAccounts.
 * Architectural position: UI shell.
 */
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { setting, useSettings } from './userSettings';

export const helpInline = () => setting<string>('workbench.help') === 'INLINE';

export function Info({ children, label = 'About this page' }: { children: ReactNode; label?: string }) {
  useSettings();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => { if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  if (helpInline()) return <span className="info-inline">{children}</span>;
  return <span className="info" ref={ref}>
    <button type="button" className="info-btn" aria-label={label} title={label} aria-expanded={open} aria-controls={id} onClick={e => { e.stopPropagation(); setOpen(o => !o); }}>i</button>
    {open && <span className="info-pop" id={id} role="note">{children}</span>}
  </span>;
}

/** One delegated listener: a clamped note opens on click (not when the click is on a link or button inside it). */
let installed = false;
export function installNoteToggle() {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  document.addEventListener('click', e => {
    const t = e.target as HTMLElement | null;
    if (!t || t.closest('button, a, input, select, label, textarea')) return;
    const note = t.closest('p.cfg-note, div.cfg-note') as HTMLElement | null;
    if (note) note.classList.toggle('note-open');
  });
}
