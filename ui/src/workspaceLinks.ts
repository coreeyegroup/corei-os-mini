import { defaults, restore, views, type Layout, type View } from './layout';
export function launchContext(search: string) {
  const p = new URLSearchParams(search);
  const view = p.get('view') as View;
  return { view: views.includes(view) ? view : null, detached: p.get('detached') === '1' };
}
export function workspaceUrl(view: View) {
  const url = new URL(window.location.origin + '/');
  url.searchParams.set('view', view);
  url.searchParams.set('detached', '1');
  return url.href;
}
export function initialLayout(raw: string | null): Layout {
  const context = launchContext(window.location.search);
  if (!context.detached || !context.view) return restore(raw);
  return {...defaults(), groups:[[context.view]], active:[context.view], activity:context.view,
    left:false, right:false, bottom:false};
}
