/* Tab strips (workspace tabs, sub-workspace tabs): thin, see-through position bar drawn over the bottom edge, and the
 * mouse wheel scrolls the tabs sideways.
 *
 * Why it exists: a native scrollbar in a tab strip takes height and covers the tab names (owner, 28 Nov 2026: "scroll
 * bar is blocking the tab words"). In Chrome `scrollbar-width` overrides the ::-webkit-scrollbar styling, so even a
 * "thin" bar took ~8–11px. Now the native bar never shows; a 2px overlay shows position on hover, VS Code style.
 * Purpose: installStripScroll() once (App): delegated wheel/scroll/hover listeners on .tabs and .sw-tabs set
 * --strip-x / --strip-w on the strip; CSS draws the overlay with ::after.
 * Must never: steal the wheel when the strip does not overflow, or when the user scrolls sideways already.
 * Consumers: App.tsx. Architectural position: UI behaviour, no data.
 */
const STRIPS = '.tabs, .sw-tabs';

function update(el: HTMLElement) {
  const over = el.scrollWidth > el.clientWidth + 1;
  el.classList.toggle('strip-over', over);
  if (!over) return;
  const w = Math.max(24, el.clientWidth * el.clientWidth / el.scrollWidth);
  const x = el.scrollLeft + (el.clientWidth - w) * (el.scrollLeft / Math.max(1, el.scrollWidth - el.clientWidth));
  el.style.setProperty('--strip-x', `${x}px`);
  el.style.setProperty('--strip-w', `${w}px`);
}

let installed = false;
export function installStripScroll(): void {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  const strip = (t: EventTarget | null) => (t instanceof Element ? t.closest(STRIPS) as HTMLElement | null : null);
  document.addEventListener('wheel', e => {
    const el = strip(e.target);
    if (!el || e.deltaX !== 0 || el.scrollWidth <= el.clientWidth + 1) return;
    el.scrollLeft += e.deltaY;
    e.preventDefault();
    update(el);
  }, { passive: false });
  document.addEventListener('scroll', e => { const el = e.target instanceof HTMLElement && e.target.matches(STRIPS) ? e.target : null; if (el) update(el); }, true);
  document.addEventListener('mouseover', e => { const el = strip(e.target); if (el) update(el); });
}
