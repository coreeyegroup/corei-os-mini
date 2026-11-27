/* One way to go anywhere: goto(view, section) opens a screen at a section, from the
 * rail, the search (Ctrl+K), Home's attention list, status-bar chips or any link.
 * Screens read their section with useSection(); the requested section survives the
 * screen mounting after the request. */
import { useEffect, useState } from 'react';
import type { View } from './layout';
import { launchContext, usePublishRoute } from './workspaceLinks';

const pending: Partial<Record<View, string>> = {};
const pendingAt: Partial<Record<View, number>> = {};
/* The section each screen is showing now (for the side bar's highlight). */
export const current: Partial<Record<View, string>> = {};

/* Each stage screen's sections, published for the side bar (which replaces the section bar when it is open). */
export type StageSection = { id: string; label: string; group: string };
export const stageSections: Partial<Record<View, StageSection[]>> = {};
export function publishSections(view: View, list: StageSection[]) {
  stageSections[view] = list;
  window.dispatchEvent(new CustomEvent('corei-stage-sections', { detail: { view } }));
}

export function goto(view: View, section?: string) {
  if (section) { pending[view] = section; pendingAt[view] = Date.now(); }
  window.dispatchEvent(new CustomEvent('corei-goto', { detail: { view, section } }));
}

type AccountDestination = 'positions' | 'broker' | 'breaks';
const accountContext: Partial<Record<AccountDestination, string>> = {};

export function openAccountView(destination: AccountDestination, accountId: string) {
  accountContext[destination] = accountId;
  window.dispatchEvent(new CustomEvent('corei-account-context', { detail: destination }));
  goto(destination === 'breaks' ? 'Post-trade' : 'Portfolio', destination);
}

export function useAccountView(destination: AccountDestination) {
  const [account, setAccount] = useState(() => accountContext[destination] ?? '');
  useEffect(() => {
    const update = (event: Event) => {
      if ((event as CustomEvent).detail === destination) setAccount(accountContext[destination] ?? '');
    };
    window.addEventListener('corei-account-context', update);
    return () => window.removeEventListener('corei-account-context', update);
  }, [destination]);
  return [account, setAccount] as const;
}

type DeploymentDestination = 'runtime' | 'signals' | 'intents' | 'queue';
type DeploymentFocus = { id: string; label: string; intentId?: string };
const deploymentFocus: Partial<Record<DeploymentDestination, DeploymentFocus>> = {};

export function openDeployment(destination: DeploymentDestination, focus: DeploymentFocus) {
  deploymentFocus[destination] = focus;
  window.dispatchEvent(new Event('corei-deployment-focus'));
  goto(destination === 'queue' ? 'Execution' : 'Strategies', destination);
}

export function useDeploymentFocus(destination: DeploymentDestination, enabled = true) {
  const [focus, setFocus] = useState(() => deploymentFocus[destination]);
  useEffect(() => {
    const update = () => setFocus(deploymentFocus[destination]);
    update();
    window.addEventListener('corei-deployment-focus', update);
    return () => window.removeEventListener('corei-deployment-focus', update);
  }, [destination]);
  const clear = () => {
    delete deploymentFocus[destination];
    window.dispatchEvent(new Event('corei-deployment-focus'));
  };
  return [enabled ? focus : undefined, clear] as const;
}

let pendingRiskOrder: { id: string; label: string } | null = null;

export function openOrderRisk(id: string, label: string) {
  pendingRiskOrder = { id, label };
  goto('Risk', 'decisions');
  window.dispatchEvent(new Event('corei-risk-order'));
}

export function readRiskOrder() {
  return pendingRiskOrder;
}

export function clearRiskOrder() {
  pendingRiskOrder = null;
  window.dispatchEvent(new Event('corei-risk-order'));
}

let pendingOrder: string | null = null;

export function openOrder(id: string) {
  pendingOrder = id;
  goto('Execution', 'orders');
  window.dispatchEvent(new Event('corei-order-focus'));
}

export function takeOrder() {
  const id = pendingOrder;
  pendingOrder = null;
  return id;
}

type TicketScope = { account_id: string; instrument_id: string; mode: 'PAPER' | 'BROKER' };
let pendingTicket: TicketScope | null = null;

export function openScopedTicket(scope: TicketScope) {
  pendingTicket = scope;
  goto('Execution', 'ticket');
  window.dispatchEvent(new Event('corei-ticket-scope'));
}

export function takeTicketScope() {
  const scope = pendingTicket;
  pendingTicket = null;
  return scope;
}

export type StrategyRule = { code: string; version: number; name: string };
let pendingStrategyRule: StrategyRule | null = null;

export function setupStrategyFromRule(rule: StrategyRule) {
  pendingStrategyRule = rule;
  goto('Configuration', 'strategies');
  window.dispatchEvent(new Event('corei-strategy-rule'));
}

export function takeStrategyRule(): StrategyRule | null {
  const rule = pendingStrategyRule;
  pendingStrategyRule = null;
  return rule;
}

export type ScanEvidence = {
  instrument_symbol: string; strategy_code: string; strategy_version_id?: string;
  account_id: string; broker_symbol: string; timeframe: string;
  account_code?: string; bar_time?: string;
};
type EvidenceTarget = 'chart' | 'backtest';
const pendingEvidence: Partial<Record<EvidenceTarget, ScanEvidence>> = {};

export function openScanEvidence(target: EvidenceTarget, evidence: ScanEvidence) {
  pendingEvidence[target] = evidence;
  if (target === 'chart') goto('Intelligence', 'instrument');
  else goto('Strategies', 'backtest');
  window.dispatchEvent(new CustomEvent('corei-scan-evidence', { detail: target }));
}

export function useScanEvidence(target: EvidenceTarget): ScanEvidence | null {
  const [selection, setSelection] = useState<ScanEvidence | null>(null);
  useEffect(() => {
    const take = () => {
      const value = pendingEvidence[target];
      if (value) { delete pendingEvidence[target]; setSelection(value); }
    };
    take();
    const onEvidence = (event: Event) => { if ((event as CustomEvent).detail === target) take(); };
    window.addEventListener('corei-scan-evidence', onEvidence);
    return () => window.removeEventListener('corei-scan-evidence', onEvidence);
  }, [target]);
  return selection;
}

export function useSection<T extends string>(view: View, initial: T, valid: readonly T[]): [T, (s: T) => void] {
  // A requested section stays available for a few seconds, so a screen that mounts twice while it opens (sign-in check,
  // layout change) still opens at it; the event below consumes it for a screen that is already open.
  const peek = () => { const s = pending[view] as T | undefined; return s && valid.includes(s) && Date.now() - (pendingAt[view] ?? 0) < 4000 ? s : undefined; };
  const [section, setSection] = useState<T>(() => {
    const address = launchContext(window.location.search);
    const linked = address.view === view ? address.section as T | undefined : undefined;
    return peek() ?? (linked && valid.includes(linked) ? linked : initial);
  });
  usePublishRoute(view, section);
  useEffect(() => {
    current[view] = section;
    window.dispatchEvent(new CustomEvent('corei-section', { detail: { view, section } }));
  }, [view, section]);
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent).detail as { view: View; section?: string };
      if (d.view !== view || !d.section) return;
      const s = d.section as T;
      if (valid.includes(s)) setSection(s);
    };
    window.addEventListener('corei-goto', on);
    return () => window.removeEventListener('corei-goto', on);
  }, [view]);
  return [section, setSection];
}

/* Every destination, for the search, the navigator and links. */
export const DESTINATIONS: { view: View; section?: string; label: string; group: string; keywords?: string }[] = [
  { view: 'My Workspaces', label: 'My Workspaces', group: 'Home', keywords: 'saved custom desk layout market trade lifecycle' },
  { view: 'Overview', label: 'Home', group: 'Home', keywords: 'today attention dashboard' },
  { view: 'Market data', section: 'live', label: 'Live prices', group: 'Market data', keywords: 'quotes bid ask feeds collectors' },
  { view: 'Market data', section: 'plans', label: 'Collection plans', group: 'Market data', keywords: 'collect subscribe symbols' },
  { view: 'Market data', section: 'quality', label: 'Feed quality & broker comparison', group: 'Market data', keywords: 'latency spread gaps score' },
  { view: 'Market data', section: 'events', label: 'Market-data events', group: 'Market data', keywords: 'outage disconnect switch' },
  { view: 'Market data', section: 'backfill', label: 'Backfill history', group: 'Market data', keywords: 'history bars download' },
  { view: 'Market data', section: 'controls', label: 'Collection controls', group: 'Market data', keywords: 'pause resume stop' },
  { view: 'Market data', section: 'archive', label: 'Archive & backups', group: 'Market data', keywords: 'parquet fingerprint backup' },
  { view: 'Configuration', section: 'health', label: 'Overview (readiness)', group: 'Setup', keywords: 'can trade blockers health checklist' },
  { view: 'Configuration', section: 'brokers', label: 'Connectivity', group: 'Setup', keywords: 'technology provider who broker exchange ecn feed data centre server session fix api account login tree add mt5 mt4 ctrader upstox' },
  { view: 'Configuration', section: 'providers', label: 'Providers (list)', group: 'Setup', keywords: 'broker vendor provider exchange ecn feed' },
  { view: 'Configuration', section: 'connections', label: 'Servers (list)', group: 'Setup', keywords: 'servers addresses mt5 server connection fix session api' },
  { view: 'Configuration', section: 'accounts', label: 'Accounts (list)', group: 'Setup', keywords: 'login password mt5 account' },
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
  { view: 'Configuration', section: 'database', label: 'Database', group: 'Controls', keywords: 'postgres postgresql table row column sql edit delete' },
  { view: 'Operations', label: 'Monitor', group: 'System', keywords: 'status health attention' },
  { view: 'Infrastructure', section: 'health', label: 'System health', group: 'System', keywords: 'server postgres disk services' },
  { view: 'Infrastructure', section: 'tables', label: 'Database tables', group: 'System', keywords: 'postgres excel edit rows' },
  { view: 'Infrastructure', section: 'history', label: 'History query', group: 'System', keywords: 'duckdb parquet' },
  { view: 'Infrastructure', section: 'sql', label: 'SQL console', group: 'System' },
  { view: 'Infrastructure', section: 'database', label: 'Database sessions', group: 'System' },
  { view: 'Infrastructure', section: 'edits', label: 'Table edit log', group: 'System' },
  { view: 'Intelligence', label: '2 Market intelligence', group: 'Trading flow', keywords: 'features indicators regime screening' },
  { view: 'Intelligence', section: 'screener', label: 'Intelligence › Screener', group: 'Trading flow', keywords: 'rank attention regime trend range volatility adx rsi atr' },
  { view: 'Intelligence', section: 'instrument', label: 'Intelligence › Instrument', group: 'Trading flow', keywords: 'features timeframes chart ema bollinger csv' },
  { view: 'Intelligence', section: 'sessions', label: 'Intelligence › Sessions', group: 'Trading flow', keywords: 'london new york tokyo sydney overlap rollover' },
  { view: 'Intelligence', section: 'dashboard', label: 'Intelligence › Dashboard', group: 'Trading flow', keywords: 'coverage regime mix warming history engine controls' },
  { view: 'Intelligence', section: 'sources', label: 'Intelligence › Sources', group: 'Trading flow', keywords: 'brokers accounts compare deviation spread latency best' },
  { view: 'Intelligence', section: 'correlation', label: 'Intelligence › Correlation', group: 'Trading flow', keywords: 'correlation matrix returns' },
  { view: 'Intelligence', section: 'strength', label: 'Intelligence › Strength', group: 'Trading flow', keywords: 'currency strength meter fx' },
  { view: 'Intelligence', section: 'watchlists', label: 'Intelligence › Watchlists', group: 'Trading flow', keywords: 'lists groups' },
  { view: 'Intelligence', section: 'rules', label: 'Intelligence › Rules & alerts', group: 'Trading flow', keywords: 'conditions alerts rsi cross regime' },
  { view: 'Configuration', section: 'bridges', label: 'Setup › Bridge nodes', group: 'Setup', keywords: 'windows vps pc bridge terminal mt5 fingerprint token install enrol tunnel' },
  { view: 'Intelligence', section: 'indicators', label: 'Intelligence › Indicators', group: 'Trading flow', keywords: 'ema sma custom period bollinger macd vwap volume strategy' },
  { view: 'Intelligence', section: 'settings', label: 'Intelligence › Engine settings', group: 'Trading flow', keywords: 'engine cycle pause timeframes retention exclude' },
  { view: 'Intelligence', section: 'calendar', label: 'Intelligence › Calendar', group: 'Trading flow', keywords: 'economic events news holidays cpi nfp csv' },
  { view: 'Signals', label: '3 Signals', group: 'Trading flow', keywords: 'signal decision intent' },
  { view: 'Strategies', label: '4 Strategies', group: 'Trading flow' },
  { view: 'Risk', label: '5 Risk', group: 'Trading flow', keywords: 'limits sizing drawdown margin exposure var' },
  { view: 'Execution', label: '7 Orders (execution)', group: 'Trading flow', keywords: 'oms ems order fill routing' },
  { view: 'Portfolio', label: '6 Portfolio', group: 'Trading flow', keywords: 'positions pnl exposure capital' },
  { view: 'Post-trade', label: '8 Post-trade', group: 'Trading flow', keywords: 'reconciliation tca reports compliance' },
  { view: 'Flow', label: 'Calculation flow (n8n)', group: 'Trading flow', keywords: 'n8n graph nodes formula equation calculation workflow canvas' },
  { view: 'Flow', section: 'formulas', label: 'Flow › Every formula', group: 'Trading flow', keywords: 'formula equation calculation try numbers' },
  { view: 'Settings', label: 'Appearance & layout', group: 'Settings', keywords: 'theme font density' },
];

export type Overview = {
  user: { username: string; roles: string[] };
  feeds: { active_plans: number; collectors: number; live: number; problems: number; paused: boolean };
  order_sending: { environment: string; enabled: boolean }[];
  order_adapter?: boolean;
  setup?: { done: number; total: number; next: { step: string; goto: { view: View; section?: string } } | null; steps: { step: string; done: boolean; section: string }[] };
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

/* Setup, in the order a firm sets itself up. One tree, used by the side bar and the Setup screen;
 * the three record lists behind "Connectivity" are views of that one screen. */
/** Setup in the order the work is done: set up, connect, markets, risk, strategies, run, settings, admin. An id
 * "@View:section" opens that stage's own page (one home per object; Setup links to it rather than copying it). */
export const SETUP_GROUPS: [string, [string, string][]][] = [
  ['1 · Setup', [['guide', 'Setup path'], ['health', 'Readiness per account'], ['gates', 'Gates & overrides']]],
  ['2 · Connect', [['@Portfolio:paper', 'Paper accounts ↗'], ['brokers', 'Connectivity map'], ['providers', 'Providers'], ['connections', 'Servers'], ['accounts', 'Accounts'], ['bridges', 'Bridge nodes'], ['groups', 'Account groups']]],
  ['3 · Markets', [['instruments', 'Instruments'], ['mappings', 'Symbol mappings'], ['fx', 'FX rates'], ['@Market data:plans', 'Market data plans ↗']]],
  ['4 · Risk', [['@Risk:limits', 'Profiles & limits ↗'], ['@Risk:kill', 'Kill switches ↗']]],
  ['5 · Strategies', [['strategies', 'Strategies & evidence'], ['deployments', 'Deployments']]],
  ['6 · Run', [['@My Workspaces:desks', 'Workspaces ↗'], ['controls', 'Order sending']]],
  ['7 · Settings', [['approvals', 'Approvals (four-eyes)'], ['alerts', 'Alerts'], ['users', 'Users & roles']]],
  ['8 · Integrate', [['integrations', 'Integrations']]],
  ['9 · Admin', [['audit', 'Audit log'], ['database', 'Database']]],
];
/** Open a Setup menu item: a Setup section, or another stage's page for "@View:section". */
export function openSetupItem(id: string, local: (id: string) => void) {
  if (id.startsWith('@')) { const [view, section] = id.slice(1).split(':'); goto(view as View, section); return; }
  local(id);
}
/** Market intelligence, in the order an analyst works: look, analyse, watch, configure. */
export const INTEL_GROUPS: [string, [string, string][]][] = [
  ['Overview', [['workspace', 'All panels'], ['dashboard', 'Dashboard'], ['screener', 'Screener'], ['instrument', 'Instrument']]],
  ['Analyse', [['sources', 'Sources'], ['correlation', 'Correlation'], ['strength', 'Currency strength'], ['sessions', 'Sessions'], ['compare', 'Select & compare']]],
  ['Monitor', [['watchlists', 'Watchlists'], ['rules', 'Rules & alerts'], ['calendar', 'Calendar']]],
  ['Configure', [['indicators', 'Indicators'], ['settings', 'Engine settings'], ['formulas', 'Calculations & formulas'], ['graphs', 'Calculation graphs'], ['stage', 'Stage map']]],
];
export const BROKER_VIEWS = ['brokers', 'providers', 'connections', 'accounts'] as const;
/** The side-bar row a Setup section belongs to (the record lists highlight "Connectivity"). */
export const setupRowOf = (section: string) => section;
