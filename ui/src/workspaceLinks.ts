/* The browser address records the active view, its section, and open panels in My Workspaces. */
import { useEffect, useLayoutEffect, useRef } from 'react';
import { defaults, restore, views, type Layout, type View } from './layout';
import { useViewFocused } from './visibility';

type Route = { view: View; section?: string; panels?: string };
const panelPattern = /^[a-z-]+(?:,[a-z-]+){0,7}$/;
const routeEvent = 'corei-address';
const sectionViews = new Set<View>(['Market data', 'Intelligence', 'Signals', 'Strategies', 'Strategy Studio', 'Risk',
  'Portfolio', 'Execution', 'Post-trade', 'Configuration', 'Infrastructure', 'Flow', 'My Workspaces']);

export function launchContext(search: string) {
  const p = new URLSearchParams(search);
  const view = p.get('view') as View;
  const section = p.get('section') ?? '';
  const panels = p.get('panels') ?? '';
  return {
    view: views.includes(view) ? view : null,
    detached: p.get('detached') === '1',
    section: /^[a-z0-9-]{1,40}$/.test(section) ? section : undefined,
    panels: view === 'My Workspaces' && panels.length <= 120 && panelPattern.test(panels) ? panels : undefined,
  };
}

function setRoute(url: URL, route: Route, detached: boolean) {
  for (const field of ['view', 'section', 'panels', 'detached']) url.searchParams.delete(field);
  url.searchParams.set('view', route.view);
  if (route.section) url.searchParams.set('section', route.section);
  if (route.panels) url.searchParams.set('panels', route.panels);
  if (detached) url.searchParams.set('detached', '1');
}

export function usePublishRoute(view: View, section?: string, panels?: string, ready = true) {
  const focused = useViewFocused();
  useEffect(() => {
    if (focused && ready) window.dispatchEvent(new CustomEvent<Route>(routeEvent, { detail: { view, section, panels } }));
  }, [view, section, panels, focused, ready]);
}

export function useAddressBar(active: View, detached: boolean, navigate: (view: View, section?: string) => void) {
  const current = useRef(active);
  const onNavigate = useRef(navigate);
  const replace = useRef(true);
  current.current = active;
  onNavigate.current = navigate;

  useLayoutEffect(() => {
    let timeout: number | undefined;
    const update = (route: Route) => {
      window.clearTimeout(timeout);
      timeout = window.setTimeout(() => {
        if (route.view !== current.current) return;
        const url = new URL(window.location.href);
        const prior = url.href;
        setRoute(url, route, detached);
        if (url.href !== prior) {
          if (replace.current) window.history.replaceState(window.history.state, '', url);
          else window.history.pushState(window.history.state, '', url);
        }
        replace.current = false;
      }, 0);
    };
    const publish = (event: Event) => {
      const route = (event as CustomEvent<Route>).detail;
      if (route.view === current.current) update(route);
    };
    const pop = () => {
      window.clearTimeout(timeout);
      const { view, section } = launchContext(window.location.search);
      replace.current = !view || (!section && sectionViews.has(view));
      onNavigate.current(view ?? 'Overview', section);
    };
    window.addEventListener(routeEvent, publish);
    window.addEventListener('popstate', pop);
    return () => {
      window.clearTimeout(timeout);
      window.removeEventListener(routeEvent, publish);
      window.removeEventListener('popstate', pop);
    };
  }, [detached]);

  usePublishRoute(active, undefined, undefined, !sectionViews.has(active));
}

export function workspaceUrl(view: View, section?: string) {
  const url = new URL('/', window.location.origin);
  const current = launchContext(window.location.search);
  setRoute(url, { view, section: section ?? (current.view === view ? current.section : undefined),
    panels: !section && current.view === view ? current.panels : undefined }, true);
  return url.href;
}

export function openWorkspace(view: View, newWindow: boolean, section?: string) {
  const name = newWindow ? `corei-${view}-${section ?? 'all'}-${Date.now()}` : '_blank';
  window.open(workspaceUrl(view, section), name, newWindow ? 'popup=yes,width=1440,height=960,left=60,top=40,noopener,noreferrer' : 'noopener,noreferrer');
}

export function initialLayout(raw: string | null): Layout {
  const { view, detached } = launchContext(window.location.search);
  const layout = restore(raw);
  if (!view) return layout;
  if (detached) return { ...defaults(), groups: [[view]], active: [view], activity: view, left: false, right: false, bottom: false };
  const at = layout.groups.findIndex(group => group.includes(view));
  const focus = at < 0 ? layout.focus : at;
  return { ...layout, activity: view, focus,
    groups: layout.groups.map((group, i) => i === focus && at < 0 ? [...group, view] : group),
    active: layout.active.map((prior, i) => i === focus ? view : prior) };
}
