/* Workspace links: a screen (and optionally one of its panels) opened on its own, in a new browser tab or window.
 * ?view=Signals&section=board&detached=1 opens Signals with only that panel; a detached page never overwrites the
 * main layout. */
import { defaults, restore, views, type Layout, type View } from './layout';
export function launchContext(search: string) {
  const p = new URLSearchParams(search);
  const view = p.get('view') as View;
  const section = p.get('section');
  return { view: views.includes(view) ? view : null, detached: p.get('detached') === '1', section: section && /^[a-z0-9-]{1,40}$/.test(section) ? section : null };
}
export function workspaceUrl(view: View, section?: string) {
  const url = new URL(window.location.origin + '/');
  url.searchParams.set('view', view);
  if (section) url.searchParams.set('section', section);
  url.searchParams.set('detached', '1');
  return url.href;
}
/* A new browser tab (same window), or a separate window sized for a second monitor. */
export function openWorkspace(view: View, newWindow: boolean, section?: string) {
  const name = newWindow ? `corei-${view}-${section ?? 'all'}-${Date.now()}` : '_blank';
  window.open(workspaceUrl(view, section), name, newWindow ? 'popup=yes,width=1440,height=960,left=60,top=40,noopener,noreferrer' : 'noopener,noreferrer');
}
export function initialLayout(raw: string | null): Layout {
  const context = launchContext(window.location.search);
  if (!context.detached || !context.view) return restore(raw);
  return {...defaults(), groups:[[context.view]], active:[context.view], activity:context.view,
    left:false, right:false, bottom:false};
}
