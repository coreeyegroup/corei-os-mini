/* User settings: the preferences that follow a person (saved to their account) and the formatters that apply them.
 *
 * Why it exists: Settings held only appearance. A trader in India and one in London need different time zones, date
 * order and number grouping (12,34,567.89 vs 1,234,567.89); people want to choose what the top bar shows and how
 * orders are confirmed. These settings are stored per user on the server (operator/prefs, key "settings.v1") with a
 * browser copy so the screen never waits for them.
 * Purpose: the schema (every setting with its label, description, category, scope and allowed values), get / set /
 * reset, a hook that re-renders on change, and formatters (time, numbers, money) used across the app.
 * Must never: hold a secret; change trading state (only how things are shown and confirmed); format money through
 * binary float maths for anything but display of a value already computed exactly on the server.
 * Dependencies: Configuration.api. Consumers: SettingsView, App (top bar, landing), StageShell (when), PaperAccounts.
 * Architectural position: UI preferences.
 */
import { useEffect, useState } from 'react';
import { api } from './Configuration';

export type SettingDef = {
  id: string; category: string; label: string; description: string; scope: 'ACCOUNT';
  type: 'select' | 'boolean' | 'text'; options?: [string, string][]; default: string | boolean; pattern?: string;
};

export const TOPBAR_ITEMS: [string, string][] = [
  ['feeds', 'Feeds'], ['orders', 'Order sending'], ['kill', 'Kill switches'], ['setup', 'Setup progress'], ['approvals', 'Approvals'],
  ['desks', 'Running workspaces'], ['clock', 'Market clock'], ['layout', 'Layout controls'],
];

export const SETTINGS: SettingDef[] = [
  { id: 'workbench.help', category: 'Appearance', label: 'Help text', description: 'Explanations of each page and panel: behind an ⓘ button (compact), or always shown (for training and new users).',
    scope: 'ACCOUNT', type: 'select', default: 'ICON', options: [['ICON', 'Behind ⓘ (compact)'], ['INLINE', 'Always shown']] },
  { id: 'workbench.menu', category: 'Layout', label: 'Menu bar', description: 'Show the menus (View, Go, Window, Help) or fold them into one ☰ button, as VS Code "compact" does.',
    scope: 'ACCOUNT', type: 'select', default: 'CLASSIC', options: [['CLASSIC', 'Menus'], ['COMPACT', 'One ☰ button']] },
  { id: 'region.timezone', category: 'Time & region', label: 'Time zone', description: 'Times on every screen are shown in this zone. Trading logic always uses market event time.',
    scope: 'ACCOUNT', type: 'select', default: 'LOCAL',
    options: [['LOCAL', 'This computer'], ['Asia/Kolkata', 'India (IST)'], ['UTC', 'UTC'], ['Europe/London', 'London'], ['America/New_York', 'New York'],
      ['Asia/Singapore', 'Singapore'], ['Asia/Dubai', 'Dubai'], ['Asia/Tokyo', 'Tokyo'], ['Australia/Sydney', 'Sydney']] },
  { id: 'region.clock', category: 'Time & region', label: 'Clock', description: '24-hour or 12-hour times.', scope: 'ACCOUNT', type: 'select', default: '24',
    options: [['24', '24-hour (14:30)'], ['12', '12-hour (2:30 PM)']] },
  { id: 'region.date', category: 'Time & region', label: 'Date order', description: 'How dates are written.', scope: 'ACCOUNT', type: 'select', default: 'DMY',
    options: [['DMY', '26/11/2026'], ['MDY', '11/26/2026'], ['ISO', '2026-11-26']] },
  { id: 'region.numbers', category: 'Time & region', label: 'Number grouping', description: 'Indian grouping (lakh, crore) or international grouping for amounts.',
    scope: 'ACCOUNT', type: 'select', default: 'INTERNATIONAL', options: [['INTERNATIONAL', '1,234,567.89'], ['INDIAN', '12,34,567.89']] },
  { id: 'trading.confirm', category: 'Trading', label: 'Confirm orders', description: 'Ask before an order from a ticket is sent. Broker orders on LIVE are always confirmed.',
    scope: 'ACCOUNT', type: 'select', default: 'ALWAYS', options: [['ALWAYS', 'Always'], ['BROKER', 'Broker orders only (not paper)'], ['NEVER', 'Never (LIVE still asks)']] },
  { id: 'trading.quantity', category: 'Trading', label: 'Default order quantity', description: 'Quantity a new ticket starts with (lots or units).', scope: 'ACCOUNT',
    type: 'text', default: '0.01', pattern: '^\\d+(\\.\\d{1,8})?$' },
  { id: 'trading.paperAccount', category: 'Trading', label: 'Default paper account', description: 'Code of the paper account listed first and marked default on the Paper accounts page.',
    scope: 'ACCOUNT', type: 'text', default: '', pattern: '^([A-Z][A-Z0-9_]{1,31})?$' },
  { id: 'workbench.start', category: 'Startup', label: 'Start page', description: 'What opens when the workstation starts without a chosen page.', scope: 'ACCOUNT',
    type: 'select', default: 'SETUP_UNTIL_DONE', options: [['SETUP_UNTIL_DONE', 'Setup until it is complete, then Home'], ['HOME', 'Home'], ['DESKS', 'My workspaces'], ['LAST', 'Where I left off']] },
  ...TOPBAR_ITEMS.map(([id, label]) => ({ id: `topbar.${id}`, category: 'Top bar', label, description: topbarHelp(id), scope: 'ACCOUNT' as const,
    type: 'boolean' as const, default: id !== 'desks' })),
];

function topbarHelp(id: string) {
  return ({ feeds: 'Live market-data feeds and problems.', orders: 'Whether order sending is on for DEMO and LIVE.', kill: 'Engaged kill switches (always shown when any is engaged).',
    setup: 'Setup progress of the target track and active overrides.', approvals: 'Changes and decisions waiting for a second person.',
    desks: 'Workspaces running and paused.', clock: 'Server time with the forex and Indian market sessions.',
    layout: 'Buttons at the top right to show or hide the side bar, panel and inspector, and focus mode.' } as Record<string, string>)[id] ?? '';
}

const KEY = 'settings.v1';
const LOCAL = 'corei-os-mini.settings.v1';
let values: Record<string, string | boolean> = (() => { try { return JSON.parse(localStorage.getItem(LOCAL) || '{}'); } catch { return {}; } })();
let loaded = false;
const EVENT = 'corei-settings';

export function setting<T extends string | boolean>(id: string): T {
  const def = SETTINGS.find(s => s.id === id);
  return (id in values ? values[id] : def?.default) as T;
}
export function isModified(id: string) { return id in values && values[id] !== SETTINGS.find(s => s.id === id)?.default; }

let timer: number | undefined;
function persist() {
  try { localStorage.setItem(LOCAL, JSON.stringify(values)); } catch { /* storage may be unavailable */ }
  window.dispatchEvent(new Event(EVENT));
  window.clearTimeout(timer);
  timer = window.setTimeout(() => { void api(`operator/prefs/${KEY}`, 'PUT', { value: values }).catch(() => undefined); }, 600);
}
export function setSetting(id: string, v: string | boolean) { values = { ...values, [id]: v }; persist(); }
export function resetSetting(id: string) { const { [id]: _, ...rest } = values; values = rest; persist(); }
export function resetAll(category?: string) {
  values = category ? Object.fromEntries(Object.entries(values).filter(([k]) => SETTINGS.find(s => s.id === k)?.category !== category)) : {};
  persist();
}

/** Re-render on any settings change; loads the server copy once per page. */
export function useSettings(): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const on = () => setN(x => x + 1);
    window.addEventListener(EVENT, on);
    if (!loaded) {
      loaded = true;
      void api('operator/prefs').then((p: Record<string, unknown>) => {
        const server = p?.[KEY];
        if (server && typeof server === 'object') { values = { ...(server as Record<string, string | boolean>) }; try { localStorage.setItem(LOCAL, JSON.stringify(values)); } catch { /* ignore */ } window.dispatchEvent(new Event(EVENT)); }
      }).catch(() => undefined);
    }
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return n;
}

const tz = () => { const z = setting<string>('region.timezone'); return z === 'LOCAL' ? undefined : z; };

/** A timestamp in the chosen zone, clock and date order. */
export function fmtTime(v?: string | number | Date | null, withDate = true): string {
  if (v === null || v === undefined || v === '') return '—';
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v);
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { timeZone: tz(), year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: setting('region.clock') === '12' }).formatToParts(d).map(p => [p.type, p.value]));
  const time = `${parts.hour}:${parts.minute}:${parts.second}${parts.dayPeriod ? ' ' + parts.dayPeriod.toUpperCase() : ''}`;
  if (!withDate) return time;
  const order = setting<string>('region.date');
  const date = order === 'ISO' ? `${parts.year}-${parts.month}-${parts.day}` : order === 'MDY' ? `${parts.month}/${parts.day}/${parts.year}` : `${parts.day}/${parts.month}/${parts.year}`;
  return `${date} ${time}`;
}

/** Round a decimal string half away from zero to `d` places using integer (BigInt) arithmetic — never binary float. */
export function roundDec(s: string, d: number): string {
  const neg = s.startsWith('-'), [i, f = ''] = s.replace('-', '').split('.');
  if (f.length <= d) return `${neg ? '-' : ''}${i}${d ? '.' + f.padEnd(d, '0') : ''}`;
  let n = BigInt(i + f.slice(0, d));
  if (Number(f[d]) >= 5) n += 1n;
  const t = n.toString().padStart(d + 1, '0'), out = d ? `${t.slice(0, -d)}.${t.slice(-d)}` : t;
  return `${neg && /[1-9]/.test(out) ? '-' : ''}${out}`;
}

/** A decimal string grouped by the chosen system (display only; values stay exact strings). With `decimals`, rounded half-up. */
export function fmtNum(v?: string | number | null, decimals?: number): string {
  if (v === null || v === undefined || v === '') return '—';
  const raw = String(v);
  if (!/^-?\d+(\.\d+)?$/.test(raw)) return raw;
  const s = decimals === undefined ? raw : roundDec(raw, decimals);
  const [int, frac] = s.replace('-', '').split('.');
  const f = frac;
  const indian = setting('region.numbers') === 'INDIAN';
  const grouped = indian && int.length > 3 ? int.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',') + ',' + int.slice(-3) : int.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${s.startsWith('-') ? '-' : ''}${grouped}${f ? '.' + f : ''}`;
}
export const fmtMoney = (v?: string | number | null, ccy?: string) => v === null || v === undefined || v === '' ? '—' : `${fmtNum(v, 2)}${ccy ? ' ' + ccy : ''}`;
