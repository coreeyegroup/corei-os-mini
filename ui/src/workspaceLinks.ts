import { defaults, restore, views, type Layout, type View } from './layout';
export const registryResources = ['providers','connections','connector-profiles','accounts','instruments','mappings'] as const;
export type RegistryResource = typeof registryResources[number];
export const registryTitles:Record<RegistryResource,string> = {
  providers:'Broker & provider directory', connections:'Access connections',
  'connector-profiles':'Connector settings', accounts:'Trading accounts',
  instruments:'Instrument directory', mappings:'Symbol mappings',
};
export function launchContext(search: string) {
  const p = new URLSearchParams(search);
  const view = p.get('view') as View;
  const resource = p.get('registry') as RegistryResource;
  return { view: views.includes(view) ? view : null,
    resource: registryResources.includes(resource) ? resource : undefined,
    detached: p.get('detached') === '1' };
}
export function workspaceUrl(view: View, resource?: RegistryResource) {
  const url = new URL(window.location.origin + '/');
  url.searchParams.set('view', view);
  url.searchParams.set('detached', '1');
  if (resource) url.searchParams.set('registry', resource);
  return url.href;
}
export function initialLayout(raw: string | null): Layout {
  const context = launchContext(window.location.search);
  if (!context.detached || !context.view) return restore(raw);
  return {...defaults(), groups:[[context.view]], active:[context.view], activity:context.view,
    left:false, right:false, bottom:false};
}
