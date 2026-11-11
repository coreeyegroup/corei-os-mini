/* One way to go anywhere: goto(view, section) opens a screen at a section, from the
 * rail, the search (Ctrl+K), Home's attention list, status-bar chips or any link.
 * Screens read their section with useSection(); the requested section survives the
 * screen mounting after the request. */
import { useEffect, useState } from 'react';
import type { View } from './layout';

const pending: Partial<Record<View, string>> = {};
/* The section each screen is showing now (for the side bar's highlight). */
export const current: Partial<Record<View, string>> = {};

export function goto(view: View, section?: string) {
  if (section) pending[view] = section;
  window.dispatchEvent(new CustomEvent('corei-goto', { detail: { view, section } }));
}

export function useSection<T extends string>(view: View, initial: T, valid: readonly T[]): [T, (s: T) => void] {
  const take = () => {
    const s = pending[view] as T | undefined;
    delete pending[view];
    return s && valid.includes(s) ? s : undefined;
  };
  const [section, setSection] = useState<T>(() => take() ?? initial);
  useEffect(() => {
    current[view] = section;
    window.dispatchEvent(new CustomEvent('corei-section', { detail: { view, section } }));
  }, [view, section]);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent).detail as { view: View; section?: string };
      if (d.view !== view || !d.section) return;
      const s = take();
      if (s) setSection(s);
    };
    window.addEventListener('corei-goto', on);
    return () => window.removeEventListener('corei-goto', on);
  }, [view]);
  return [section, setSection];
}

/* Every destination, for the search, the navigator and links. */
export const DESTINATIONS: { view: View; section?: string; label: string; group: string; keywords?: string }[] = [
  { view: 'Overview', label: 'Home', group: 'Home', keywords: 'today attention dashboard' },
  { view: 'Market data', section: 'live', label: 'Live prices', group: 'Market data', keywords: 'quotes bid ask feeds collectors' },
  { view: 'Market data', section: 'plans', label: 'Collection plans', group: 'Market data', keywords: 'collect subscribe symbols' },
  { view: 'Market data', section: 'quality', label: 'Feed quality & broker comparison', group: 'Market data', keywords: 'latency spread gaps score' },
  { view: 'Market data', section: 'events', label: 'Market-data events', group: 'Market data', keywords: 'outage disconnect switch' },
  { view: 'Market data', section: 'backfill', label: 'Backfill history', group: 'Market data', keywords: 'history bars download' },
  { view: 'Market data', section: 'controls', label: 'Collection controls', group: 'Market data', keywords: 'pause resume stop' },
  { view: 'Market data', section: 'archive', label: 'Archive & backups', group: 'Market data', keywords: 'parquet fingerprint backup' },
  { view: 'Configuration', section: 'health', label: 'Account readiness', group: 'Setup', keywords: 'can trade blockers health' },
  { view: 'Configuration', section: 'providers', label: 'Providers', group: 'Setup', keywords: 'broker vendor' },
  { view: 'Configuration', section: 'connections', label: 'Connections', group: 'Setup', keywords: 'servers addresses mt5 server' },
  { view: 'Configuration', section: 'accounts', label: 'Accounts', group: 'Setup', keywords: 'login password mt5 account' },
  { view: 'Configuration', section: 'groups', label: 'Account groups', group: 'Setup' },
  { view: 'Configuration', section: 'instruments', label: 'Instruments', group: 'Setup', keywords: 'symbols contracts' },
  { view: 'Configuration', section: 'mappings', label: 'Symbol mappings', group: 'Setup' },
  { view: 'Configuration', section: 'strategies', label: 'Strategies (setup)', group: 'Setup' },
  { view: 'Configuration', section: 'deployments', label: 'Deployments', group: 'Setup' },
  { view: 'Configuration', section: 'approvals', label: 'Approvals', group: 'Controls', keywords: 'four eyes review' },
  { view: 'Configuration', section: 'controls', label: 'Order sending & policies', group: 'Controls', keywords: 'kill switch trading on off' },
  { view: 'Configuration', section: 'alerts', label: 'Alert channels', group: 'Controls', keywords: 'email telegram webhook' },
  { view: 'Configuration', section: 'users', label: 'Users & roles', group: 'Controls' },
  { view: 'Configuration', section: 'audit', label: 'Audit log', group: 'Controls', keywords: 'history changes' },
  { view: 'Operations', label: 'Monitor', group: 'System', keywords: 'status health attention' },
  { view: 'Infrastructure', section: 'health', label: 'System health', group: 'System', keywords: 'server postgres disk services' },
  { view: 'Infrastructure', section: 'tables', label: 'Database tables', group: 'System', keywords: 'postgres excel edit rows' },
  { view: 'Infrastructure', section: 'history', label: 'History query', group: 'System', keywords: 'duckdb parquet' },
  { view: 'Infrastructure', section: 'sql', label: 'SQL console', group: 'System' },
  { view: 'Infrastructure', section: 'database', label: 'Database sessions', group: 'System' },
  { view: 'Infrastructure', section: 'edits', label: 'Table edit log', group: 'System' },
  { view: 'Strategies', label: 'Strategies', group: 'Trading' },
  { view: 'Execution', label: 'Orders', group: 'Trading' },
  { view: 'Portfolio', label: 'Portfolio', group: 'Trading' },
  { view: 'Settings', label: 'Appearance & layout', group: 'Settings', keywords: 'theme font density' },
];

export type Overview = {
  user: { username: string; roles: string[] };
  feeds: { active_plans: number; collectors: number; live: number; problems: number; paused: boolean };
  order_sending: { environment: string; enabled: boolean }[];
  approvals: number;
  attention: { severity: 'CRITICAL' | 'WARNING' | 'INFO'; area: string; title: string; detail: string; goto: { view: View; section?: string } }[];
};

/* Open a database table in System › Tables. */
let pendingTable: string | null = null;
export function gotoTable(name: string) {
  pendingTable = name;
  goto('Infrastructure', 'tables');
  window.dispatchEvent(new CustomEvent('corei-table', { detail: name }));
}
export function takeTable(): string | null { const t = pendingTable; pendingTable = null; return t; }

/* Decimals for a symbol: the broker's digits when read, else the decimals its prices carry. */
export function digitsOf(row: { digits?: number | null; bid?: number | string | null; ask?: number | string | null }): number {
  if (row.digits != null && Number.isFinite(Number(row.digits))) return Number(row.digits);
  // Round to 6 places first so binary noise (67210.45999999) does not count as decimals.
  const dec = (v: unknown) => { if (v == null || !Number.isFinite(Number(v))) return 0; const t = Number(v).toFixed(6).replace(/0+$/, ''); const i = t.indexOf('.'); return i < 0 ? 0 : t.length - i - 1; };
  return Math.max(dec(row.bid), dec(row.ask));
}
export const ago = (v?: number | string | null) => { const s = v == null || v === '' ? null : Number(v);
  return s == null || !Number.isFinite(s) ? '—' : s < 1 ? 'now' : s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };
