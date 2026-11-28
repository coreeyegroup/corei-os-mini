/*
 * Configuration workspace: providers, connections, accounts, instruments, symbol
 * mappings, order-sending controls and the audit log (Blueprint v1.2 §8, §13.3, §17.2).
 *
 * Owns: presentation and form state only. Every rule is enforced by the backend
 * (/api/config); this screen shows the backend's own validation messages.
 * Secrets are write-only: the screen never receives a stored credential back.
 */
import { VersionEvidence, copyToTimeframe } from './Governance';
import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import './configuration.css';
import type { View } from './layout';
import { goto, openSetupItem, SETUP_GROUPS, setupRowOf, takeStrategyRule, useSection, type StrategyRule } from './nav';
import { SetupGates, SetupHome } from './SetupHome';
import { FxRates } from './PaperAccounts';
import Integrations from './Integrations';
import PlatformMode from './PlatformMode';
import DatabaseEditor from './DatabaseEditor';
import FloatingWindow from './FloatingWindow';
import { BridgeNodes } from './BridgeNodes';
import ConnectTree from './ConnectTree';
import { AccountRoutes, Alerts, Approvals, BrokerLogin, BrokerRead, CredentialUse, HealthBoard, ImportAccounts, Policies, SymbolSpecs } from './ConfigControls';
import { BlendEditor } from './SignalLibrary';
import { SubWorkspace } from './SubWorkspace';
import { useViewVisible } from './visibility';
import { Info } from './Info';

export type Row = Record<string, any>;
type Section = 'guide' | 'mode' | 'gates' | 'paper' | 'integrations' | 'fx' | 'health' | 'brokers' | 'providers' | 'connections' | 'accounts' | 'groups' | 'instruments' | 'mappings' | 'strategies' | 'deployments' | 'approvals' | 'controls' | 'alerts' | 'users' | 'audit' | 'database' | 'bridges';
type Entity = Exclude<Section, 'guide' | 'mode' | 'gates' | 'paper' | 'integrations' | 'fx' | 'health' | 'brokers' | 'controls' | 'audit' | 'users' | 'strategies' | 'deployments' | 'approvals' | 'alerts' | 'database' | 'bridges'>;
export type Navigate = (section: string, id?: string) => void;
type Session = { username: string; display_name: string; roles: string[]; must_change_password: boolean; password_min?: number; platform_mode?: string };
const ROLE_LABEL: Record<string, string> = { ADMIN: 'Administrator', TRADING_OPS: 'Trading operations', RISK_APPROVER: 'Risk approver', OBSERVER: 'Observer' };
const ROLE_HINT: Record<string, string> = { ADMIN: 'Manage users and roles', TRADING_OPS: 'Change configuration and credentials; turn order sending off',
  RISK_APPROVER: 'Turn order sending on or off', OBSERVER: 'Read configuration and audit log' };

const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: 'guide', label: 'Setup path', hint: 'The steps from nothing to a first trade, per track (Paper, DEMO, LIVE): checked from your real data, with what to do next' },
  { id: 'gates', label: 'Gates & overrides', hint: 'How strictly each step holds actions (off · warn · soft · supervised · hard), scoped time-boxed overrides, the setup report' },
  { id: 'paper', label: 'Paper accounts', hint: 'Simulated accounts, each with its own currency, amount, markets and costs; any strategy; nothing reaches a broker' },
  { id: 'fx', label: 'FX rates', hint: 'Rates entered by a person, used only when no collected pair converts a currency' },
  { id: 'health', label: 'Readiness per account', hint: 'Can each account collect data and trade right now; what to set up next' },
  { id: 'brokers', label: 'Connectivity', hint: 'Provider › account › server. Select a level to add the next one' },
  { id: 'providers', label: 'Providers', hint: 'Step 1 · who you deal with: broker, exchange, ECN, feed … legal entity, regulator, contacts, exposure' },
  { id: 'accounts', label: 'Accounts', hint: 'Step 2 · the accounts you hold at each provider: login, credentials, permission, the server each uses, readiness' },
  { id: 'connections', label: 'Servers', hint: 'Step 3 · where accounts connect: MT5 servers in use, FIX sessions, APIs — environment, addresses, clock, order limits' },
  { id: 'bridges', label: 'Bridge nodes', hint: 'Windows machines (VPS, PC) running the COREI MT5 bridge: connect one with a one-time code, then use it for any number of accounts' },
  { id: 'groups', label: 'Account groups', hint: 'Named sets of accounts, e.g. a desk or a client book' },
  { id: 'instruments', label: 'Instruments', hint: 'Canonical instruments and contract specifications' },
  { id: 'mappings', label: 'Symbol mappings', hint: 'Broker symbol for each instrument on a connection' },
  { id: 'strategies', label: 'Strategies', hint: 'Strategy rules and their approved versions' },
  { id: 'deployments', label: 'Deployments', hint: 'Which strategy version trades which instrument on which account, and from which data source' },
  { id: 'approvals', label: 'Approvals', hint: 'LIVE changes waiting for a second person (four-eyes)' },
  { id: 'controls', label: 'Order sending', hint: 'Firm-wide switch per environment, and control policies' },
  { id: 'alerts', label: 'Alerts', hint: 'E-mail, Telegram and webhook notifications' },
  { id: 'users', label: 'Users', hint: 'Who can sign in and what each role may do' },
  { id: 'integrations', label: 'Integrations', hint: 'CORE-AI, n8n and other applications: connections, API keys, permissions, webhooks, usage, API explorer' },
  { id: 'mode', label: 'Platform mode', hint: 'Development (build and test) or Production (real trading): gates, LIVE, passwords, test data reset' },
  { id: 'audit', label: 'Audit log', hint: 'Every configuration and control change' },
  { id: 'database', label: 'Database', hint: 'Every table: add, edit and delete rows; add, change and drop columns; create and drop tables (Administrator; reason and password; audited)' },
];

/* Headings in the section list, so 14 sections read as five groups. */
/* The four views of the Connect screen: the tree, then the three record lists behind it. */
const BROKER_TABS: [Section, string][] = [['brokers', 'Tree'], ['providers', 'Providers'], ['accounts', 'Accounts'], ['connections', 'Servers']];

export const BLOCKERS: Record<string, string> = {
  CONNECTION_FAILED: 'The broker connection failed — reason below',
  ORDER_ADAPTER_NOT_AVAILABLE: 'No order adapter for this platform (MT5 through the bridge and Upstox take real orders)',
  ORDER_NEEDS_BRIDGE: 'MT5 orders go through the COREI terminal bridge: add a bridge route to this account (Setup › Bridge nodes)',
  LIVE_CONNECTION_ON_DEMO_SERVER: 'This connection is marked LIVE but its server name says demo — check the environment',
  DEMO_CONNECTION_ON_LIVE_SERVER: 'This connection is marked DEMO but its server name says live — check the environment',
  ACCOUNT_EXPIRED: 'The account has expired at the broker',
  STATIC_IP_MISMATCH: "This server's outbound IP is not the IP registered with the broker app",
  MARGIN_MODE_UNKNOWN: 'Margin mode (hedging or netting) is not known',
  SERVER_CLOCK_UNKNOWN: "The broker server's clock (UTC offset) is not set on the connection",
  MANDATE_MISSING: 'Client account has no mandate reference',
  MANDATE_EXPIRED: 'Client mandate has expired',
  MASTER_PASSWORD_MISSING: 'Only the investor password is saved; trading needs the master password',
  BROKER_LOGIN_NEEDED_TODAY: "Today's broker login (2FA) has not been done or has expired",
  ACCOUNT_EXPIRING: 'Account expires soon',
  MANDATE_EXPIRING: 'Client mandate expires soon',
  STATIC_IP_NOT_RECORDED: 'No registered static IP recorded for this LIVE API app (SEBI framework)',
  RATE_LIMIT_NOT_SET: 'No order rate limit set on the connection (a safe default of 5 per second applies)',
  MARGIN_MODE_NOT_CONFIRMED_BY_BROKER: 'Margin mode was typed, not yet confirmed by the broker',
  PROVIDER_INACTIVE: 'Provider is inactive',
  CONNECTION_INACTIVE: 'Connection is inactive',
  ACCOUNT_INACTIVE: 'Account is inactive',
  ACCOUNT_PERMISSION_DISABLED: 'Account permission is Disabled',
  ACCOUNT_CREDENTIAL_MISSING: 'MT5 login password not saved',
  CONNECTION_CREDENTIAL_MISSING: 'Connection API credential not saved',
  ADAPTER_NOT_AVAILABLE: 'Provider adapter for this platform is not implemented yet',
  CONNECTION_DATA_ONLY: 'Connection is market-data only',
  ACCOUNT_NOT_PERMITTED_TO_TRADE: 'Account permission is not Trading',
  READ_ONLY_CREDENTIAL: 'Saved password is an investor (read-only) password',
  ORDER_SENDING_OFF_DEMO: 'Order sending is off for DEMO',
  ORDER_SENDING_OFF_LIVE: 'Order sending is off for LIVE',
  NO_ADDRESS_AVAILABLE: 'No server address is selected for this account',
  BRIDGE_NOT_CONFIGURED: 'The connection uses the MT5 terminal bridge but has no bridge host and certificate fingerprint — open the connection',
  BRIDGE_TOKEN_MISSING: 'The terminal bridge token is not saved on the connection (Credential)',
  PUSH_TOKEN_MISSING: 'No push token yet: open the account’s server › New push token, then put it in the COREI EA',
  ACCOUNT_ADDRESSES_REMOVED_BY_SERVER: 'Some addresses chosen for this account are no longer listed by the server; they are skipped',
  ACCOUNT_ADDRESSES_FALLBACK_TO_SERVER: 'None of the addresses chosen for this account are listed by the server any more; using the server\'s addresses',
  NO_LATENCY_MEASURED: 'Fastest first has no latency test yet; using the server\'s order until one is run',
  DEPLOYMENT_NOT_ACTIVE: 'Deployment is not active',
  STRATEGY_RETIRED: 'Strategy is retired',
  VERSION_NOT_APPROVED: 'Strategy version is not approved',
  INSTRUMENT_INACTIVE: 'Instrument is inactive',
  DATA_SOURCE_INACTIVE: 'Market-data source (symbol mapping or its connection) is inactive',
  NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION: "No trading symbol mapping for this instrument on the account's connection",
  TRADING_SYMBOL_INACTIVE: "Trading symbol mapping on the account's connection is inactive",
};

class ApiError extends Error {}

/** One password prompt for the whole workstation, registered by the app shell (0.68.1). Before, every screen
 *  registered its own and the most recently opened answered — often a screen in a hidden tab, so the prompt was
 *  invisible and the action (e.g. Settings › Software update › Start update) seemed stuck. */
const reauthHandlers: (() => Promise<boolean>)[] = [];
const reauthenticate = () => reauthHandlers.length ? reauthHandlers[reauthHandlers.length - 1]() : null;
export function addReauthHandler(handler: () => Promise<boolean>): () => void {
  reauthHandlers.push(handler);
  return () => { const i = reauthHandlers.lastIndexOf(handler); if (i >= 0) reauthHandlers.splice(i, 1); };
}

export async function api(path: string, method = 'GET', body?: unknown, raw?: BodyInit, retried = false): Promise<any> {
  const response = await fetch('/api/' + path, {
    method, cache: 'no-store', credentials: 'same-origin',
    headers: { 'x-corei-request': 'workstation', ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(raw ? { 'content-type': 'application/octet-stream' } : {}) },
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (response.status === 204) return null;
  if (response.status === 428 && reauthHandlers.length && !retried) {
    if (await reauthenticate()) return api(path, method, body, raw, true);
  }
  const data = await response.json().catch(() => ({}));
  if (response.ok && (path === 'operator/login' || path === 'operator/logout')) window.dispatchEvent(new Event('corei-session'));
  if (!response.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail) ? detail.map((d: any) => `${d.field}: ${d.message}`).join(' · ')
      : typeof detail === 'string' ? detail
      : detail && typeof detail === 'object' && typeof detail.message === 'string'
        ? [detail.message, ...(Array.isArray(detail.unmet) ? detail.unmet.map((u: any) => u.detail) : [])].join(' · ')
      : `Request failed (HTTP ${response.status})`;
    const error = new ApiError(message); (error as any).status = response.status; (error as any).detail = detail; throw error;
  }
  return data;
}

/* ---------- small building blocks ---------- */

/** Can this server reach the broker API at all (no secret sent)? Tells an edge block from a wrong key. */
function ApiCheck({ connectionId }: { connectionId: string }) {
  const [r, setR] = useState<Row | null>(null), [busy, setBusy] = useState(false);
  return <div className="rd-check">
    <button type="button" className="rd-act" disabled={busy} onClick={async () => { setBusy(true); try { setR(await api(`config/connections/${connectionId}/api-check`, 'POST')); } catch (e) { setR({ detail: (e as Error).message }); } finally { setBusy(false); } }}>
      {busy ? 'Checking…' : 'Check broker API from this server'}</button>
    {r && <span className={r.reachable ? 'cfg-ok' : 'cfg-error'}> {r.reachable ? 'Reachable' : 'Not reachable'}{r.status ? ` (HTTP ${r.status})` : ''}: {r.detail}</span>}
  </div>;
}

/** Daily OAuth login (Upstox): the broker's page, then back to COREI. */
async function brokerLogin(accountId: string) {
  try { const { authorize_url } = await api(`config/accounts/${accountId}/broker-login/start`, 'POST'); window.location.assign(authorize_url); }
  catch (e) { window.alert((e as Error).message); }
}

/** A change that needs a second person comes back as {change_request}. */
export function pendingMessage(result: Row | null): string | null {
  return result?.change_request ? `Sent for approval: ${result.change_request.summary}. This is a LIVE change, so a second person with the Risk approver role must approve it under Approvals (four-eyes). `
    + 'Working alone? Add a second user (Setup › Users) or turn four-eyes off (Setup › Order sending / Controls). For a demo account, choose the broker\'s demo server instead: DEMO needs no approval.' : null;
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="cfg-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function Choice({ value, options, onChange, disabled, label }: { value: string; options: (string | [string, string])[];
  onChange: (v: string) => void; disabled?: boolean; label: string }) {
  return <select aria-label={label} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
    {options.map(o => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o}>{o}</option>)}
  </select>;
}

export function Status({ value }: { value: string }) {
  return <span className={`cfg-pill ${value === 'ACTIVE' || value === 'TRADING' ? 'ok' : value === 'INACTIVE' || value === 'DISABLED' || value === 'ARCHIVED' ? 'off' : ''}`}>{value}</span>;
}

export function Env({ value }: { value: string }) {
  return <span className={`cfg-pill ${value === 'LIVE' ? 'live' : 'demo'}`}>{value}</span>;
}

function Ready({ blockers }: { blockers: string[] }) {
  if (!blockers?.length) return <span className="cfg-pill ok">Ready</span>;
  return <span className="cfg-pill warn" title={blockers.map(b => BLOCKERS[b] ?? b).join('\n')}>{blockers.length} blocker{blockers.length > 1 ? 's' : ''}</span>;
}

export function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return <section className="cfg-section"><h4><span>{n}</span>{title}</h4><div className="cfg-section-body">{children}</div></section>;
}

/* ---------- forms per entity ---------- */

const blank: Record<Entity, Row> = {
  providers: { code: '', name: '', kind: 'BROKER', status: 'ACTIVE', legal_entity: '', regulator: '', licence_number: '', jurisdiction: '',
    execution_model: 'UNKNOWN', website: '', support_contact: '', dealing_contact: '', exposure_limit: '', exposure_currency: '', holidays: [], technologies: [] },
  connections: { provider_id: '', code: '', name: '', platform: 'MT5', environment: 'DEMO', purpose: 'BOTH', status: 'ACTIVE',
    settings: { server_name: '', access_points: [{ name: 'Added manually', host: '', port: 443, source: 'MANUAL' }] },
    adapter: 'DEFAULT', server_utc_offset_minutes: '', dst_rule: 'UNKNOWN', clock_source: 'UNKNOWN', max_orders_per_second: '', max_order_burst: '',
    registered_egress_ip: '', maintenance: [] },
  accounts: { connection_id: '', code: '', name: '', external_account_id: '', holder_name: '', base_currency: 'USD', trading_permission: 'DISABLED', status: 'ACTIVE',
    access_mode: 'FASTEST', access_points: [], margin_mode: 'UNKNOWN', broker_group: '', leverage: '', stop_out_level: '', max_open_orders: '', max_open_positions: '',
    expires_on: '', password_changed_on: '', relationship: 'OWN', mandate_reference: '', mandate_expires_on: '', other_users: '', group_ids: [] },
  groups: { code: '', name: '', description: '', status: 'ACTIVE' },
  instruments: { symbol: '', name: '', asset_class: 'FX', base_currency: '', quote_currency: '', price_precision: 5, quantity_precision: 2,
    tick_size: '0.00001', min_quantity: '0.01', quantity_step: '0.01', contract_size: '100000', contract_unit: 'units', trading_session: '', status: 'ACTIVE',
    exchange: '', market: '', isin: '', lot_size: '', expiry: '', strike: '', option_type: '', underlying: '', aliases: [] },
  mappings: { connection_id: '', account_id: '', instrument_id: '', broker_symbol: '', usage: 'BOTH', broker_contract_size: '', broker_price_digits: '', status: 'ACTIVE' },
};
const FIELDS: Record<Entity, string[]> = {
  providers: ['code', 'name', 'kind', 'status', 'legal_entity', 'regulator', 'licence_number', 'jurisdiction', 'execution_model', 'website',
    'support_contact', 'dealing_contact', 'exposure_limit', 'exposure_currency', 'holidays', 'technologies'],
  connections: ['provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'settings', 'status', 'adapter', 'server_utc_offset_minutes',
    'dst_rule', 'clock_source', 'max_orders_per_second', 'max_order_burst', 'registered_egress_ip', 'maintenance'],
  accounts: ['connection_id', 'code', 'name', 'external_account_id', 'holder_name', 'base_currency', 'trading_permission', 'status', 'access_mode', 'access_points',
    'margin_mode', 'broker_group', 'leverage', 'stop_out_level', 'max_open_orders', 'max_open_positions', 'expires_on', 'password_changed_on',
    'relationship', 'mandate_reference', 'mandate_expires_on', 'other_users', 'group_ids'],
  groups: ['code', 'name', 'description', 'status'],
  instruments: ['symbol', 'name', 'asset_class', 'base_currency', 'quote_currency', 'price_precision', 'quantity_precision',
    'tick_size', 'min_quantity', 'quantity_step', 'contract_size', 'contract_unit', 'trading_session', 'status',
    'exchange', 'market', 'isin', 'lot_size', 'expiry', 'strike', 'option_type', 'underlying', 'aliases'],
  mappings: ['connection_id', 'account_id', 'account_group_id', 'instrument_id', 'broker_symbol', 'usage', 'broker_contract_size', 'broker_price_digits', 'status'],
};
// Only what makes it a different real thing stays fixed (0.68.0). Identifying fields can change, with a reason (audit log).
const IMMUTABLE: Record<Entity, string[]> = {
  providers: [], connections: ['platform', 'environment'], accounts: ['external_account_id'], instruments: [], mappings: [], groups: [],
};
const IDENTITY: Record<Entity, string[]> = {
  providers: ['code'], connections: ['provider_id', 'code'], accounts: ['connection_id', 'code'], instruments: ['symbol'],
  mappings: ['connection_id', 'instrument_id', 'account_id', 'account_group_id'], groups: ['code'],
};
const SINGULAR: Record<Entity, string> = { providers: 'provider', connections: 'server', accounts: 'account', instruments: 'instrument', mappings: 'symbol mapping', groups: 'account group' };
const ARCHIVABLE: Entity[] = ['providers', 'connections', 'accounts', 'groups'];
const orNull = (v: unknown) => v === '' || v == null ? null : v;
const numOrNull = (v: unknown) => v === '' || v == null ? null : Number(v);
const strOrNull = (v: unknown) => v === '' || v == null ? null : String(v);

export function toPayload(entity: Entity, draft: Row): Row {
  const out: Row = {};
  for (const key of FIELDS[entity]) out[key] = draft[key];
  if (entity === 'mappings') {
    out.broker_contract_size = draft.broker_contract_size === '' || draft.broker_contract_size == null ? null : String(draft.broker_contract_size);
    out.broker_price_digits = draft.broker_price_digits === '' || draft.broker_price_digits == null ? null : Number(draft.broker_price_digits);
  }
  if (entity === 'accounts') { out.access_mode = draft.access_mode ?? 'SERVER';
    out.access_points = (draft.access_points ?? []).map((p: Row) => ({ host: p.host, port: Number(p.port) }));
    for (const k of ['leverage', 'max_open_orders', 'max_open_positions']) out[k] = numOrNull(draft[k]);
    out.stop_out_level = strOrNull(draft.stop_out_level);
    for (const k of ['expires_on', 'password_changed_on', 'mandate_expires_on']) out[k] = orNull(draft[k]);
    for (const k of ['margin_mode', 'relationship']) out[k] = draft[k] ?? blank.accounts[k];
    for (const k of ['broker_group', 'mandate_reference', 'other_users']) out[k] = draft[k] ?? '';
    out.group_ids = [...(draft.group_ids ?? [])].sort(); }
  if (entity === 'providers') { out.exposure_limit = strOrNull(draft.exposure_limit);
    for (const k of ['legal_entity', 'regulator', 'licence_number', 'jurisdiction', 'website', 'support_contact', 'dealing_contact', 'exposure_currency']) out[k] = draft[k] ?? '';
    out.execution_model = draft.execution_model ?? 'UNKNOWN'; out.holidays = draft.holidays ?? []; out.technologies = draft.technologies ?? []; }
  if (entity === 'connections') { out.server_utc_offset_minutes = numOrNull(draft.server_utc_offset_minutes); out.max_order_burst = numOrNull(draft.max_order_burst);
    out.max_orders_per_second = strOrNull(draft.max_orders_per_second); out.registered_egress_ip = orNull(draft.registered_egress_ip);
    out.adapter = draft.adapter ?? 'DEFAULT'; out.dst_rule = draft.dst_rule ?? 'UNKNOWN'; out.clock_source = draft.clock_source ?? 'UNKNOWN';
    out.maintenance = draft.maintenance ?? []; }
  if (entity === 'mappings') { out.account_id = orNull(draft.account_id); out.account_group_id = orNull(draft.account_group_id); }
  if (entity === 'instruments') { out.price_precision = Number(draft.price_precision); out.quantity_precision = Number(draft.quantity_precision);
    for (const k of ['lot_size', 'strike', 'expiry']) out[k] = orNull(draft[k]);
    for (const k of ['exchange', 'market', 'isin', 'option_type', 'underlying']) out[k] = draft[k] ?? '';
    out.aliases = Array.isArray(draft.aliases) ? draft.aliases : String(draft.aliases ?? '').split(',').map((a: string) => a.trim()).filter(Boolean); }
  if (entity === 'connections' && (draft.platform === 'MT5' || draft.platform === 'MT4'))
    out.settings = { server_name: draft.settings.server_name, company_name: draft.settings.company_name || null, server_details: draft.settings.server_details ?? {},
      access_points: draft.settings.access_points.map((p: Row) => ({ name: p.name, host: p.host, port: Number(p.port), enabled: p.enabled !== false,
        source: p.source ?? 'UNKNOWN', confirmed_at: p.confirmed_at ?? null })),
      bridge: draft.platform === 'MT5' && draft.adapter === 'TERMINAL_BRIDGE' && draft.settings.bridge?.host
        ? { host: draft.settings.bridge.host, port: Number(draft.settings.bridge.port || 9443), fingerprint: draft.settings.bridge.fingerprint } : null };
  if (entity === 'connections' && draft.platform === 'FIX') {
    const s = draft.settings;
    out.settings = { host: s.host, port: Number(s.port), tls: s.tls !== false, tls_verify: s.tls_verify !== false, begin_string: s.begin_string || 'FIX.4.4',
      sender_comp_id: s.sender_comp_id, target_comp_id: s.target_comp_id, sender_sub_id: s.sender_sub_id || '', target_sub_id: s.target_sub_id || '',
      heartbeat_seconds: Number(s.heartbeat_seconds || 30), reset_seq_on_logon: s.reset_seq_on_logon !== false, md_depth: Number(s.md_depth || 1),
      md_snapshots_only: !!s.md_snapshots_only };
  }
  if (entity === 'connections' && draft.platform === 'CTRADER')
    out.settings = { host: draft.settings.host, port: Number(draft.settings.port || 5035), oauth_redirect_url: draft.settings.oauth_redirect_url || null };
  if (entity === 'connections' && draft.platform === 'REST_API') {
    const s = draft.settings;
    out.settings = { api_base_url: s.api_base_url, auth_method: s.auth_method, stream_url: s.stream_url || null,
      oauth_redirect_url: s.auth_method === 'OAUTH2' ? s.oauth_redirect_url || null : null, notifier_webhook_url: s.notifier_webhook_url || null, upstox_algo_name: s.upstox_algo_name?.trim() || null };
  }
  return out;
}

/** "TimeZone" -> "Time zone", "DST" stays "DST". */
function detailLabel(key: string) {
  const words = key.replace(/_/g, ' ').replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2').trim();
  return words.charAt(0).toUpperCase() + words.slice(1).replace(/\b(?!DST|ID|IP|URL|UTC|GMT)([A-Z])([a-z]+)/g, (_, a, b) => a.toLowerCase() + b);
}

function ServerDescription({ settings }: { settings: Row }) {
  const details: Row = settings.server_details ?? {};
  const keys = Object.keys(details).filter(k => !/^company(name)?$/i.test(k));
  if (!settings.company_name && !keys.length) return null;
  return <dl className="cfg-desc" aria-label="Server description">
    <dt>Server</dt><dd>{settings.server_name}</dd>
    {settings.company_name && <><dt>Company</dt><dd>{settings.company_name}</dd></>}
    {keys.map(k => <Fragment key={k}><dt>{detailLabel(k)}</dt><dd>{String(details[k])}</dd></Fragment>)}
  </dl>;
}

/** Private, loopback and link-local addresses only work inside the broker's network. */
function isPrivate(host: string): boolean {
  const v4 = host.match(/^(\d+)\.(\d+)\.\d+\.\d+$/);
  if (v4) { const [a, b] = [Number(v4[1]), Number(v4[2])];
    return a === 10 || a === 127 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || (a === 169 && b === 254); }
  return /^(f[cd][0-9a-f]{2}|fe80|::1$)/i.test(host);
}
const address = (p: Row) => `${String(p.host).includes(':') ? `[${p.host}]` : p.host}:${p.port}`;

/** Choose one broker server and the addresses to use, from a servers.dat analysis or a
 *  broker-directory search. Two views: by server, or every address in one table
 *  (Westernpips style). Either way one connection uses one server. */
function ServerPicker({ catalog, use, close }: { catalog: Row; use: (server: Row, chosen: Row[]) => void; close: () => void }) {
  const [view, setView] = useState<'servers' | 'addresses'>('servers');
  const [q, setQ] = useState(''), [rows, setRows] = useState<Row[]>([]), [total, setTotal] = useState(0), [matches, setMatches] = useState(0), [loading, setLoading] = useState(false);
  const [server, setServer] = useState<Row | null>(null), [picked, setPicked] = useState<Set<string>>(new Set()), [error, setError] = useState(''), [note, setNote] = useState('');
  const base = `config/mt5/server-file/${catalog.catalog_id}`;
  const id = (p: Row) => `${p.host}|${p.port}`;
  async function fetchRows(offset: number) {
    setLoading(true); setError('');
    try {
      const data = view === 'servers' ? await api(`${base}/servers?${new URLSearchParams({ q, offset: String(offset), limit: '200' })}`)
        : await api(`${base}/access-points?${new URLSearchParams({ broker: q, offset: String(offset), limit: '200' })}`);
      setRows(r => offset ? [...r, ...data.items] : data.items); setTotal(data.total); setMatches(data.server_matches ?? data.total);
      if (view === 'servers' && !offset && data.items.length === 1 && server?.server_name !== data.items[0].server_name) void open(data.items[0].server_name);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }
  useEffect(() => { const t = setTimeout(() => void fetchRows(0), 250); return () => clearTimeout(t); }, [q, view]);
  async function open(name: string, only?: Row) {
    try { const detail = await api(`${base}/server?${new URLSearchParams({ name })}`); setServer(detail);
      setPicked(new Set(only ? [id(only)] : detail.access_points.filter((p: Row) => !isPrivate(p.host)).map(id))); }
    catch (e) { setError((e as Error).message); }
  }
  const toggle = (p: Row) => setPicked(old => { const next = new Set(old); next.has(id(p)) ? next.delete(id(p)) : next.add(id(p)); return next; });
  function toggleRow(r: Row) {
    if (r.host == null) return;
    if (server?.server_name === r.server_name) { toggle(r); return; }
    if (server && picked.size) setNote(`One connection uses one server. Selection moved to ${r.server_name}.`); else setNote('');
    void open(r.server_name, r);
  }
  const points: Row[] = server?.access_points ?? [];
  const chosen = points.filter(p => picked.has(id(p)));
  const source = catalog.source === 'broker-directory' ? `Broker directory · “${catalog.query}”` : 'servers.dat';
  const warnings = [catalog.skipped_servers && `${catalog.skipped_servers} unnamed servers skipped`,
    catalog.invalid_addresses && `${catalog.invalid_addresses} unreadable addresses skipped`,
    catalog.unreadable_extended_records && `${catalog.unreadable_extended_records} extended records unreadable`].filter(Boolean);
  return <div className="cfg-picker" aria-label="Choose broker server">
    <div className="cfg-picker-head">
      <div><strong>{source}</strong><small>{catalog.server_count.toLocaleString()} servers · {catalog.access_point_count.toLocaleString()} addresses{warnings.length ? ` · ${warnings.join(' · ')}` : ''}</small></div>
      <button type="button" onClick={close}>← Back</button>
    </div>
    <div className="cfg-picker-bar">
      <div className="cfg-tabs" role="tablist" aria-label="View">
        <button type="button" role="tab" aria-selected={view === 'servers'} onClick={() => setView('servers')}>By server</button>
        <button type="button" role="tab" aria-selected={view === 'addresses'} onClick={() => setView('addresses')}>All addresses</button>
      </div>
      <span className="cfg-filter">
        <input aria-label="Filter servers" autoFocus placeholder={view === 'servers' ? 'Filter by server, company or address…' : 'Filter by server or company, e.g. live 3'} value={q} onChange={e => setQ(e.target.value)} />
        {q && <button type="button" aria-label="Clear filter" title="Clear filter" onClick={() => setQ('')}>✕</button>}
      </span>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}{note && <p role="status" className="cfg-note">{note}</p>}
    {view === 'servers' ? <div className="cfg-picker-panes">
      <div className="cfg-picker-servers" role="listbox" aria-label="Servers">
        {rows.map(r => <button type="button" role="option" aria-selected={server?.server_name === r.server_name} key={r.server_name}
          className={server?.server_name === r.server_name ? 'on' : ''} onClick={() => { setNote(''); void open(r.server_name); }}>
          <span className="cfg-picker-name">{r.server_name}</span>
          <span className="cfg-picker-meta">{r.company_name ?? 'Company not stated'} · {r.access_point_count} address{r.access_point_count === 1 ? '' : 'es'}</span>
        </button>)}
        {!rows.length && !loading && <p className="cfg-note">No server matches “{q}”.</p>}
        {rows.length < total && <button type="button" className="cfg-more-link" disabled={loading} onClick={() => void fetchRows(rows.length)}>Show {Math.min(200, total - rows.length)} more servers</button>}
      </div>
      <div className="cfg-picker-points">
        {!server ? <p className="cfg-note cfg-picker-empty">Select a server to see its addresses.</p> : <>
          <div className="cfg-picker-server">
            <div><strong>{server.server_name}</strong><small>{server.company_name ?? 'Company not stated'}</small></div>
            <span className="cfg-row-actions">
              <button type="button" onClick={() => setPicked(new Set(points.filter(p => !isPrivate(p.host)).map(id)))}>Public</button>
              <button type="button" onClick={() => setPicked(new Set(points.map(id)))}>All</button>
              <button type="button" onClick={() => setPicked(new Set())}>None</button>
            </span>
          </div>
          <table className="cfg-table compact cfg-pick-table"><thead><tr><th /><th>Address</th><th>Name in file</th></tr></thead><tbody>
            {points.map(p => <tr key={id(p)} className={picked.has(id(p)) ? 'selected' : ''} onClick={() => toggle(p)}>
              <td><input type="checkbox" aria-label={`Select ${address(p)} on ${server.server_name}`} checked={picked.has(id(p))} onChange={() => toggle(p)} onClick={e => e.stopPropagation()} /></td>
              <td className="cfg-mono">{address(p)}{isPrivate(p.host) && <span className="cfg-pill off" title="Only reachable inside the broker's network">Private</span>}</td>
              <td>{p.name}</td></tr>)}
          </tbody></table>
          <details className="cfg-more"><summary>Server details</summary><ServerDescription settings={{ server_name: server.server_name, company_name: server.company_name, server_details: server.details }} /></details>
        </>}
      </div>
    </div> : <div className="cfg-picker-flat">
      <table className="cfg-table compact cfg-pick-table"><thead><tr><th /><th>Address</th><th>Server</th><th>Name in file</th><th>Company</th></tr></thead><tbody>
        {rows.map(r => { const on = server?.server_name === r.server_name && picked.has(id(r));
          return r.host == null ? <tr key={r.server_name + '|none'} className="cfg-off"><td /><td className="cfg-note">no readable address</td><td>{r.server_name}</td><td /><td>{r.company_name ?? '—'}</td></tr>
          : <tr key={`${r.server_name}|${id(r)}`} className={on ? 'selected' : ''} onClick={() => toggleRow(r)}>
            <td><input type="checkbox" aria-label={`Select ${address(r)} on ${r.server_name}`} checked={on} onChange={() => toggleRow(r)} onClick={e => e.stopPropagation()} /></td>
            <td className="cfg-mono">{address(r)}{isPrivate(r.host) && <span className="cfg-pill off">Private</span>}</td><td>{r.server_name}</td><td>{r.name}</td><td>{r.company_name ?? '—'}</td></tr>; })}
        {!rows.length && !loading && <tr><td colSpan={5} className="cfg-note">No address matches “{q}”.</td></tr>}
      </tbody></table>
      {rows.length < total && <button type="button" className="cfg-more-link" disabled={loading} onClick={() => void fetchRows(rows.length)}>Show {Math.min(200, total - rows.length)} more addresses</button>}
    </div>}
    <div className="cfg-picker-foot">
      <small className="cfg-note">{view === 'addresses' ? `${total.toLocaleString()} addresses on ${matches.toLocaleString()} servers · ` : ''}{server ? `${chosen.length} of ${points.length} addresses of ${server.server_name} selected` : 'No server selected'}</small>
      <button type="button" className="primary" disabled={!server || !chosen.length} onClick={() => use(server!, chosen)}>
        {server ? `Use ${server.server_name}` : 'Use server'}</button>
    </div>
  </div>;
}

/** Latency test (TCP connect time from the COREI server, no login). Results are kept
 *  per address; when a connection id is given they are also recorded in its history. */
function useLatency(connectionId?: string) {
  const [results, setResults] = useState<Record<string, Row>>({}), [busy, setBusy] = useState(false), [error, setError] = useState(''), [from, setFrom] = useState('');
  useEffect(() => {
    if (!connectionId) return;
    api(`config/connections/${connectionId}/latency`).then(d => setResults(Object.fromEntries(d.items.map((r: Row) => [`${r.host}|${r.port}`, r])))).catch(() => undefined);
  }, [connectionId]);
  async function test(points: Row[]) {
    setBusy(true); setError('');
    try {
      const next: Record<string, Row> = {};
      for (let i = 0; i < points.length; i += 64) {
        const batch = points.slice(i, i + 64).map(p => ({ host: p.host, port: Number(p.port) }));
        const data = await api('config/mt5/latency', 'POST', { addresses: batch, ...(connectionId ? { connection_id: connectionId } : {}) });
        setFrom(data.measured_from);
        for (const r of data.results) next[`${r.host}|${r.port}`] = { ...r, measured_at: new Date().toISOString(), measured_from: data.measured_from };
      }
      setResults(old => ({ ...old, ...next }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return { results, busy, error, from, test, get: (p: Row) => results[`${p.host}|${p.port}`] };
}

function LatencyBadge({ sample }: { sample?: Row }) {
  if (!sample) return <span className="cfg-muted-cell">—</span>;
  if (!sample.reachable) return <span className="cfg-pill off" title={sample.error ?? 'Not reachable'}>{sample.error?.startsWith('Private') ? 'Not tested' : 'No answer'}</span>;
  const ms = Number(sample.latency_ms);
  return <span className={`cfg-latency ${ms < 60 ? 'fast' : ms < 150 ? 'mid' : 'slow'}`} title={`Best of 3${sample.median_ms != null ? `, median ${sample.median_ms} ms` : ''}${sample.measured_from ? ` · from ${sample.measured_from}` : ''}${sample.measured_at ? ` · ${new Date(sample.measured_at).toLocaleString()}` : ''}`}>{ms.toFixed(ms < 10 ? 1 : 0)} ms</span>;
}

const ACCESS_MODES: [string, string, string][] = [
  ['FASTEST', 'Fastest (automatic)', "The server's ticked addresses, fastest first. COREI measures them again every 6 hours and before each login."],
  ['SERVER', "Server's order", "The server's ticked addresses, in the order set on the server. Changes to the server apply to this account."],
  ['ACCOUNT', 'Choose for this account', "Only the addresses ticked here, in this order. Must be among the server's addresses."],
];

/** How one MT5 account chooses among its server's addresses. */
export function AccountAddresses({ mode, own, serverPoints, connectionId, change, routing }: {
  mode: string; own: Row[]; serverPoints: Row[]; connectionId?: string; routing?: Row;
  change: (mode: string, own: Row[]) => void }) {
  const lat = useLatency(connectionId);
  const key = (p: Row) => `${p.host}|${p.port}`;
  const onServer = new Map(serverPoints.map(p => [key(p), p]));
  const chosen = own.filter(p => onServer.has(key(p)));
  const missing = own.filter(p => !onServer.has(key(p)));
  const rest = serverPoints.filter(p => !own.some(o => key(o) === key(p)));
  const ticked = serverPoints.filter(p => p.enabled !== false);
  const preview = mode === 'ACCOUNT' ? (chosen.length ? chosen : ticked)
    : mode === 'FASTEST' ? [...ticked].sort((a, b) => { const x = lat.get(a), y = lat.get(b);
        const va = x?.reachable ? Number(x.latency_ms) : Infinity, vb = y?.reachable ? Number(y.latency_ms) : Infinity; return va - vb; })
    : ticked;
  const move = (i: number, d: number) => { const next = [...chosen]; const [x] = next.splice(i, 1); next.splice(i + d, 0, x); change(mode, next); };
  return <div className="cfg-route">
    <div className="cfg-route-modes" role="radiogroup" aria-label="Address choice">
      {ACCESS_MODES.map(([value, label, hint]) => <label key={value} className={`cfg-route-mode${mode === value ? ' on' : ''}`}>
        <input type="radio" name="access_mode" value={value} checked={mode === value} onChange={() => change(value, value === 'ACCOUNT' && !own.length ? ticked.map(p => ({ host: p.host, port: p.port })) : own)} />
        <span><strong>{label}</strong><small>{hint}</small></span></label>)}
    </div>
    <div className="cfg-addresses-head">
      <strong>{mode === 'ACCOUNT' ? 'Addresses for this account' : 'This account will connect through'}</strong>
      <span className="cfg-note">{lat.from ? `Latency from ${lat.from}` : 'Latency is measured from the COREI server'}</span>
      <span className="cfg-row-actions"><button type="button" disabled={lat.busy || !serverPoints.length} onClick={() => void lat.test(serverPoints)}>{lat.busy ? 'Testing…' : 'Test latency'}</button></span>
    </div>
    {lat.error && <p role="alert" className="cfg-error">{lat.error}</p>}
    {mode === 'ACCOUNT' ? <div className="cfg-address-box"><table className="cfg-table compact cfg-address-table"><thead><tr><th>Use</th><th>#</th><th>Address</th><th>Latency</th><th /></tr></thead><tbody>
      {chosen.map((p, i) => <tr key={key(p)}>
        <td><input type="checkbox" aria-label={`Account uses ${address(p)}`} checked onChange={() => change(mode, own.filter(o => key(o) !== key(p)))} /></td>
        <td className="cfg-address-n">{i + 1}</td><td className="cfg-mono">{address(p)}{onServer.get(key(p))?.enabled === false && <span className="cfg-pill off" title="Unticked for the server's other accounts">Not used by server</span>}</td>
        <td><LatencyBadge sample={lat.get(p)} /></td>
        <td className="cfg-row-actions"><button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
          <button type="button" disabled={i === chosen.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button></td></tr>)}
      {rest.map(p => <tr key={key(p)} className="cfg-off">
        <td><input type="checkbox" aria-label={`Account uses ${address(p)}`} checked={false} onChange={() => change(mode, [...chosen, { host: p.host, port: p.port }])} /></td>
        <td /><td className="cfg-mono">{address(p)}{isPrivate(p.host) && <span className="cfg-pill off">Private</span>}</td><td><LatencyBadge sample={lat.get(p)} /></td><td /></tr>)}
      {missing.map(p => <tr key={key(p)} className="cfg-off"><td /><td /><td className="cfg-mono">{address(p)} <span className="cfg-pill warn">No longer on server</span></td><td /><td className="cfg-row-actions">
        <button type="button" onClick={() => change(mode, own.filter(o => key(o) !== key(p)))} aria-label={`Forget ${address(p)}`}>✕</button></td></tr>)}
    </tbody></table></div>
    : <ol className="cfg-route-preview">{preview.map(p => <li key={key(p)}><span className="cfg-mono">{address(p)}</span><LatencyBadge sample={lat.get(p)} /></li>)}
        {!preview.length && <li className="cfg-note">No address is ticked on the server.</li>}</ol>}
    {mode === 'ACCOUNT' && !chosen.length && <p role="alert" className="cfg-error">Tick at least one address, or use the server's addresses.</p>}
    {mode !== 'ACCOUNT' && own.length > 0 && <p className="cfg-note">This account's own choice ({own.length} address{own.length === 1 ? '' : 'es'}) is kept; switch to “Choose for this account” to use it again.</p>}
    {routing && routing.warnings?.length > 0 && <ul className="cfg-warnings">{routing.warnings.map((w: string) => <li key={w}>{BLOCKERS[w] ?? w}</li>)}</ul>}
  </div>;
}

/** Addresses of the chosen server: which are used, in which order. */
function AccessPoints({ settings, change, connectionId }: { settings: Row; change: (s: Row) => void; connectionId?: string }) {
  const points: Row[] = settings.access_points ?? [];
  const lat = useLatency(connectionId);
  const fastestFirst = () => { const ms = (p: Row) => { const r = lat.get(p); return r?.reachable ? Number(r.latency_ms) : Infinity; };
    const order = points.map((p, i) => ({ p, i })).sort((x, y) => (x.p.enabled === false ? 1 : 0) - (y.p.enabled === false ? 1 : 0) || ms(x.p) - ms(y.p) || x.i - y.i);
    setPoints(order.map(o => o.p)); setEditable(old => new Set([...old].map(k => order.findIndex(o => o.i === k)))); };
  const [editable, setEditable] = useState(() => new Set(points.map((p, i) => p.host ? -1 : i)));
  const setPoints = (next: Row[]) => change({ ...settings, access_points: next });
  const setPoint = (i: number, patch: Row) => setPoints(points.map((p, j) => j === i ? { ...p, ...patch } : p));
  const move = (i: number, d: number) => { const next = [...points]; const [x] = next.splice(i, 1); next.splice(i + d, 0, x); setPoints(next);
    setEditable(old => new Set([...old].map(k => k === i ? i + d : k === i + d ? i : k))); };
  const remove = (i: number) => { setPoints(points.filter((_, j) => j !== i)); setEditable(old => new Set([...old].filter(k => k !== i).map(k => k > i ? k - 1 : k))); };
  const on = points.filter(p => p.enabled !== false).length;
  return <div className="cfg-addresses">
    <div className="cfg-addresses-head">
      <strong>Addresses</strong>
      <span className="cfg-note">{on} of {points.length} in use · tried top to bottom</span>
      <span className="cfg-row-actions">
        <button type="button" disabled={on === points.length} onClick={() => setPoints(points.map(p => ({ ...p, enabled: true })))}>Select all</button>
        <button type="button" disabled={on === 0} onClick={() => setPoints(points.map(p => ({ ...p, enabled: false })))}>Clear all</button>
        <button type="button" disabled={lat.busy || !points.some(p => p.host)} onClick={() => void lat.test(points.filter(p => p.host))}>{lat.busy ? 'Testing…' : 'Test latency'}</button>
        <button type="button" disabled={!Object.keys(lat.results).length} onClick={fastestFirst} title="Ticked addresses, fastest first">Fastest first</button>
      </span>
    </div>
    {lat.error && <p role="alert" className="cfg-error">{lat.error}</p>}
    {lat.from && <p className="cfg-note">Latency measured from {lat.from} (the COREI server), best of 3 TCP connects. No login is made.</p>}
    <div className="cfg-address-box"><table className="cfg-table compact cfg-address-table"><thead><tr><th>Use</th><th>#</th><th>Address</th><th>Latency</th><th>Name in file</th><th /></tr></thead><tbody>
      {points.map((p, i) => { const edit = editable.has(i);
        return <tr key={i} className={p.enabled === false ? 'cfg-off' : ''}>
          <td><input type="checkbox" aria-label={`Use access point ${i + 1}`} checked={p.enabled !== false} onChange={e => setPoint(i, { enabled: e.target.checked })} /></td>
          <td className="cfg-address-n">{i + 1}</td>
          <td>{edit ? <span className="cfg-address-edit">
              <input aria-label={`Access point ${i + 1} host`} placeholder="Host or IP" required value={p.host} onChange={e => setPoint(i, { host: e.target.value })} />
              <input aria-label={`Access point ${i + 1} port`} placeholder="Port" required type="number" min={1} max={65535} value={p.port} onChange={e => setPoint(i, { port: e.target.value })} />
            </span> : <span className="cfg-mono">{address(p)}</span>}
            {!edit && isPrivate(p.host) && <span className="cfg-pill off" title="Only reachable inside the broker's network">Private</span>}</td>
          <td>{p.host ? <LatencyBadge sample={lat.get(p)} /> : null}</td>
          <td className="cfg-muted-cell">{edit ? 'Added manually' : p.name}{p.source && p.source !== 'UNKNOWN' && <span className={`cfg-pill ${p.source === 'MANUAL' ? 'warn' : 'off'}`}
            title={p.source === 'MANUAL' ? 'Typed by hand: on LIVE a second person must approve it' : `From ${p.source === 'DIRECTORY' ? 'the broker directory' : p.source === 'BROKER' ? 'the broker at login' : 'servers.dat'}${p.confirmed_at ? ' · ' + new Date(p.confirmed_at).toLocaleDateString() : ''}`}>
            {{ DIRECTORY: 'Directory', SERVERS_DAT: 'servers.dat', BROKER: 'Broker', MANUAL: 'Manual' }[p.source as string]}</span>}</td>
          <td className="cfg-row-actions">
            <button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up" title="Try earlier">↑</button>
            <button type="button" disabled={i === points.length - 1} onClick={() => move(i, 1)} aria-label="Move down" title="Try later">↓</button>
            <button type="button" disabled={points.length === 1} onClick={() => remove(i)} aria-label="Remove" title="Remove">✕</button></td>
        </tr>; })}
    </tbody></table></div>
    <button type="button" className="cfg-more-link" disabled={points.length >= 256}
      onClick={() => { setEditable(new Set([...editable, points.length])); setPoints([...points, { name: settings.server_name || 'Added manually', host: '', port: 443, enabled: true, source: 'MANUAL' }]); }}>+ Add address</button>
  </div>;
}

/** DEMO or LIVE as the broker names the server (MetaQuotes-Demo, VTMarkets-Live 3); null when the name does not say. */
export const envOfServer = (name?: string | null): 'DEMO' | 'LIVE' | null =>
  !name ? null : /demo|trial|contest|test/i.test(name) ? 'DEMO' : /live|real/i.test(name) ? 'LIVE' : null;

/** MT5: find the broker's server (directory search or servers.dat), then its addresses. */
function Mt5Server({ settings, change, connectionId, summaryOnly }: { settings: Row; change: (s: Row) => void; connectionId?: string; summaryOnly?: ReactNode }) {
  const [catalog, setCatalog] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [term, setTerm] = useState(''), [manual, setManual] = useState(false), [changing, setChanging] = useState(false), [version, setVersion] = useState(0);
  const points: Row[] = settings.access_points ?? [];
  const hasServer = !!settings.server_name && points.some(p => p.host);
  async function run(task: Promise<Row>) {
    setBusy(true); setError('');
    try { setCatalog(await task); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const search = () => { if (term.trim().length >= 2) void run(api('config/mt5/broker-search', 'POST', { query: term.trim() })); };
  function use(server: Row, picked: Row[]) {
    const id = (p: Row) => `${p.host}|${p.port}`, wanted = new Set(picked.map(id));
    const rest = server.access_points.filter((p: Row) => !wanted.has(id(p)));
    const source = catalog?.source === 'broker-directory' ? 'DIRECTORY' : 'SERVERS_DAT', confirmed_at = new Date().toISOString();
    change({ server_name: server.server_name, company_name: server.company_name ?? null, server_details: server.details ?? {},
      access_points: [...picked.map(p => ({ name: p.name, host: p.host, port: p.port, enabled: true, source, confirmed_at })),
        ...rest.map((p: Row) => ({ name: p.name, host: p.host, port: p.port, enabled: false, source, confirmed_at }))] });
    setCatalog(null); setManual(false); setChanging(false); setTerm(''); setVersion(v => v + 1);
  }
  if (catalog) return <ServerPicker catalog={catalog} use={use} close={() => setCatalog(null)} />;
  const showFinder = (!hasServer && !manual) || changing;
  return <>
    {showFinder && <div className="cfg-finder">
      <div className="cfg-finder-option">
        <strong>Find the broker's servers</strong>
        <div className="cfg-search-row">
          <input aria-label="Find broker" placeholder="Broker name, e.g. VTMarkets" value={term} maxLength={64}
            onChange={e => setTerm(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); search(); } }} />
          <button type="button" className="primary" disabled={busy || term.trim().length < 2} onClick={search}>{busy ? 'Searching…' : 'Find servers'}</button>
        </div>
        <small className="cfg-note">Live from the MT5 broker directory: current servers and addresses.</small>
      </div>
      <div className="cfg-finder-or">or</div>
      <div className="cfg-finder-option">
        <strong>Read a servers.dat file</strong>
        <label className="cfg-filebutton">Open servers.dat…
          <input type="file" accept=".dat" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void run(api('config/mt5/server-file', 'POST', undefined, f)); e.target.value = ''; }} /></label>
        <small className="cfg-note">From an MT5 terminal: File › Open Data Folder › Config.</small>
      </div>
      {error && <p role="alert" className="cfg-error">{error}</p>}
      <div className="cfg-finder-foot">
        {changing ? <button type="button" onClick={() => { setChanging(false); setError(''); }}>← Keep {settings.server_name}</button>
          : <button type="button" className="cfg-link" onClick={() => { setManual(true); if (!points.length) change({ ...settings, access_points: [{ name: 'Added manually', host: '', port: 443, enabled: true, source: 'MANUAL' }] }); }}>Enter server and addresses manually</button>}
      </div>
    </div>}
    {!showFinder && hasServer && !manual && <div className="cfg-chosen">
      <div><span className="cfg-chosen-label">Server</span><strong>{settings.server_name}</strong><small>{settings.company_name ?? 'Company not stated'}</small></div>
      <button type="button" onClick={() => setChanging(true)}>Change server…</button>
    </div>}
    {!showFinder && manual && <Field label="Broker server name" hint="Exactly as in MT5, e.g. VTMarkets-Live 3">
      <input aria-label="Broker server name" required maxLength={120} value={settings.server_name ?? ''} onChange={e => change({ ...settings, server_name: e.target.value })} /></Field>}
    {!showFinder && points.length > 0 && (summaryOnly ?? <AccessPoints key={version} settings={settings} change={change} connectionId={connectionId} />)}
    {!showFinder && points.length > 0 && !points.some(p => p.enabled !== false) && <p role="alert" className="cfg-error">Select at least one address.</p>}
    {!showFinder && !manual && (settings.company_name || Object.keys(settings.server_details ?? {}).length > 0) &&
      <details className="cfg-more"><summary>Server details</summary><ServerDescription settings={settings} /></details>}
  </>;
}

function RestSettings({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const field = (k: string, label: string, hint: string, required = false) => <Field label={label} hint={hint}>
    <input aria-label={label} required={required} type="url" value={settings[k] ?? ''} onChange={e => change({ ...settings, [k]: e.target.value })} /></Field>;
  return <>
    {field('api_base_url', 'REST base URL', 'e.g. https://api.upstox.com', true)}
    {/upstox\.com/i.test(settings.api_base_url ?? '') && field('upstox_algo_name', 'Registered algo name', 'Optional. Enter the exact name configured in Upstox when your app requires X-Algo-Name.')}
    <Field label="Authentication"><Choice label="Authentication" value={settings.auth_method ?? 'API_KEY'} options={[['OAUTH2', 'OAuth 2 login (e.g. Upstox)'], ['API_KEY', 'API key and secret']]}
      onChange={v => change({ ...settings, auth_method: v })} /></Field>
    {settings.auth_method === 'OAUTH2' && field('oauth_redirect_url', 'OAuth redirect URL', 'Must match the broker app exactly, e.g. https://corei-os-mini.coreeye.in/api/oauth/upstox/callback', true)}
    <details className="cfg-more"><summary>Optional URLs</summary>
      {field('stream_url', 'Streaming URL', 'wss:// — leave empty if the broker issues it after login (Upstox does)')}
      {field('notifier_webhook_url', 'Notifier webhook URL', 'https:// — only if registered on the broker app')}
    </details>
    <small className="cfg-note">The API key and secret are entered after saving.</small>
  </>;
}

/** Suggested permanent IDs, filled in until the operator types their own. */
const idPart = (v: string) => (v || '').toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^_+|_+$/g, '');
function suggestId(entity: Entity, d: Row, refs: Record<string, Row[]>): string {
  let id = '';
  if (entity === 'providers') id = idPart(d.name).split('_').slice(0, 2).join('_');
  if (entity === 'connections') {
    const provider = refs.providers.find(p => p.id === d.provider_id);
    if (!provider) return '';
    const base = `${provider.code}_${PLATFORM_TAG[d.platform] ?? 'API'}_${d.environment}`;
    const taken = new Set(refs.connections.map(c => c.code));
    id = base; for (let n = 2; taken.has(id); n++) id = `${base}_${n}`;
  }
  if (entity === 'accounts') {
    const connection = refs.connections.find(c => c.id === d.connection_id);
    if (!connection || !d.external_account_id) return '';
    id = `${connection.provider_code}_${connection.environment}_${idPart(d.external_account_id)}`;
  }
  id = id.slice(0, 32).replace(/_+$/, '');
  return /^[A-Z]/.test(id) ? id : '';
}

const PURGEABLE = ['providers', 'connections', 'accounts'];
export const PLATFORM_TAG: Record<string, string> = { MT5: 'MT5', MT4: 'MT4', CTRADER: 'CTRADER', FIX: 'FIX', REST_API: 'API' };
export const PLATFORM_LABEL: Record<string, string> = { MT5: 'MetaTrader 5', MT4: 'MetaTrader 4', CTRADER: 'cTrader', FIX: 'FIX 4.4 / 4.2', REST_API: 'Broker / exchange API' };
const TECH_OPTIONS: [string, string][] = [['MT5', 'MetaTrader 5'], ['MT4', 'MetaTrader 4'], ['CTRADER', 'cTrader'], ['FIX', 'FIX 4.4 / 4.2'],
  ['API', 'Broker / exchange API'], ['FEED', 'Fast feed / data vendor'], ['EXCHANGE', 'Exchange direct']];

function suggestName(d: Row, refs: Record<string, Row[]>): string {
  const provider = refs.providers.find(p => p.id === d.provider_id);
  if (!provider) return '';
  const env = d.environment === 'LIVE' ? 'live' : 'demo';
  const where = d.settings?.server_name || d.settings?.host || (d.settings?.api_base_url ?? '').replace(/^https:\/\//, '');
  return `${provider.name} · ${PLATFORM_TAG[d.platform] ?? 'API'} ${env}${where ? ` · ${where}` : ''}`.slice(0, 120);
}

function RecordForm({ entity, draft, setDraft, editing, refs }: { entity: Entity; draft: Row; setDraft: (r: Row) => void; editing: boolean; refs: Record<string, Row[]> }) {
  const set = (k: string | Row, v?: unknown) => {
    const patch: Row = typeof k === 'string' ? { [k]: v } : k;
    if (typeof k !== 'string') { k = Object.keys(patch)[0]; v = patch[k as string]; }
    const next: Row = { ...draft, ...patch };
    if (k === 'code') next._codeEdited = v !== '';
    else if (!editing && !next._codeEdited && 'code' in next) next.code = suggestId(entity, next, refs);
    if (k === 'name') next._nameEdited = v !== '';
    else if (!editing && !next._nameEdited && entity === 'connections') next.name = suggestName(next, refs);
    setDraft(next);
  };
  const locked = (k: string) => editing && IMMUTABLE[entity].includes(k);
  const idHint = (k: string, hint?: string) => editing && IDENTITY[entity].includes(k) ? 'Changeable; a reason is asked when saving' : hint;
  const text = (k: string, label: string, props: Row = {}, hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : idHint(k, hint)}><input aria-label={label} value={draft[k] ?? ''} readOnly={locked(k)} onChange={e => set(k, e.target.value)} {...props} /></Field>;
  const pick = (k: string, label: string, options: (string | [string, string])[], hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : hint}><Choice label={label} value={draft[k] ?? ''} options={options} disabled={locked(k)} onChange={v => set(k, v)} /></Field>;
  const ref = (k: string, label: string, rows: Row[], show: (r: Row) => string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : idHint(k)}><select aria-label={label} required value={draft[k] ?? ''} disabled={locked(k)} onChange={e => set(k, e.target.value)}>
      <option value="" disabled>Select…</option>{rows.map(r => <option key={r.id} value={r.id}>{show(r)}</option>)}</select></Field>;
  const status = pick('status', 'Status', editing && draft.status === 'ARCHIVED' ? ['ARCHIVED', 'INACTIVE'] : ['ACTIVE', 'INACTIVE']);
  const code = (label: string, example: string) => text('code', label, { required: true, maxLength: 32, pattern: '[A-Za-z][A-Za-z0-9_]{1,31}' },
    `Suggested; fixed after saving. e.g. ${example}`);

  if (entity === 'providers') return <>
    <Section n={1} title="Provider">
      {text('name', 'Provider name', { required: true, maxLength: 120 }, 'Brand, e.g. IC Markets, VT Markets, Upstox')}
      <div className="cfg-pair">{code('Provider ID', 'ICMARKETS, EXNESS, UPSTOX')}
        {pick('kind', 'Provider type', [['BROKER', 'Broker'], ['EXCHANGE', 'Exchange'], ['ECN', 'ECN'], ['PRIME_BROKER', 'Prime broker'], ['LIQUIDITY_PROVIDER', 'Liquidity provider'], ['FAST_FEED', 'Fast feed'], ['DATA_VENDOR', 'Data vendor'], ['DATA_CENTRE', 'Data centre']])}</div>
      <Field label="Technologies offered" hint="What this provider offers accounts on, e.g. a broker with MT4, MT5 and cTrader. Add account shows these first; any other can still be chosen.">
        <div className="cfg-checks" role="group" aria-label="Technologies offered">{TECH_OPTIONS.map(([id, label]) =>
          <label key={id} className="cfg-check"><input type="checkbox" aria-label={label} checked={(draft.technologies ?? []).includes(id)}
            onChange={e => set('technologies', e.target.checked ? [...(draft.technologies ?? []), id] : (draft.technologies ?? []).filter((t: string) => t !== id))} /> {label}</label>)}</div></Field>
    </Section>
    <Section n={2} title="Legal entity and regulation">
      {text('legal_entity', 'Legal entity', { maxLength: 200 }, 'The firm that holds the accounts, e.g. VT Markets (Pty) Ltd. One brand can have several.')}
      <div className="cfg-pair">{text('regulator', 'Regulator', { maxLength: 120 }, 'e.g. FSCA, ASIC, SEBI')}{text('licence_number', 'Licence number', { maxLength: 80 })}</div>
      <div className="cfg-pair">{text('jurisdiction', 'Jurisdiction', { maxLength: 80 }, 'e.g. South Africa, India')}
        {pick('execution_model', 'Execution model', [['UNKNOWN', 'Not known'], ['MARKET_MAKER', 'Market maker'], ['STP', 'STP'], ['ECN', 'ECN'], ['EXCHANGE', 'Exchange'], ['MIXED', 'Mixed / per account type']])}</div>
    </Section>
    <Section n={3} title="Contacts, exposure and calendar">
      {text('website', 'Website', { maxLength: 200 })}
      <div className="cfg-pair">{text('support_contact', 'Support contact', { maxLength: 300 }, 'Phone, e-mail, chat')}
        {text('dealing_contact', 'Dealing desk (stuck orders)', { maxLength: 300 })}</div>
      <div className="cfg-pair">{text('exposure_limit', 'Exposure limit', { inputMode: 'decimal' }, 'Most capital to hold at this firm across all accounts')}
        {text('exposure_currency', 'Exposure currency', { maxLength: 3 }, 'e.g. USD')}</div>
      <Field label="Holidays (market closed)" hint="One per line: YYYY-MM-DD Name">
        <textarea aria-label="Holidays" rows={3} value={(draft.holidays ?? []).map((h: Row) => `${h.date} ${h.name ?? ''}`.trim()).join('\n')}
          onChange={e => set('holidays', e.target.value.split('\n').map(l => l.trim()).filter(Boolean).map(l => ({ date: l.slice(0, 10), name: l.slice(10).trim() })))} /></Field>
      {status}
    </Section></>;

  if (entity === 'groups') return <>
    {text('name', 'Group name', { required: true, maxLength: 120 }, 'e.g. Gold desk, Client book A')}
    {code('Group ID', 'GOLD_DESK')}
    {text('description', 'Description', { maxLength: 500 })}
    {status}
    {editing && <p className="cfg-note">{draft.account_count ?? 0} account(s). Add accounts to a group from each account's window.</p>}</>;

  if (entity === 'connections') {
    const platform = (v: string) => setDraft({ ...draft, platform: v, adapter: v === 'FIX' ? 'NATIVE' : 'DEFAULT',
      settings: v === 'MT5' || v === 'MT4' ? blank.connections.settings : v === 'FIX' ? { ...FIX_BLANK } : v === 'CTRADER' ? { ...CTRADER_BLANK } : { api_base_url: '', stream_url: '', auth_method: 'API_KEY' } });
    return <>
      <Section n={1} title="Broker">
        {ref('provider_id', 'Provider', refs.providers, r => `${r.name} (${r.code})`)}
        <div className="cfg-pair">
          <Field label="Platform" hint={locked('platform') ? 'Fixed after creation' : undefined}><Choice label="Platform" value={draft.platform} disabled={locked('platform')} options={[['MT5', 'MetaTrader 5'], ['MT4', 'MetaTrader 4'], ['CTRADER', 'cTrader'], ['FIX', 'FIX 4.4 / 4.2'], ['REST_API', 'Broker API']]} onChange={platform} /></Field>
          {pick('environment', 'Environment', [['DEMO', 'Demo'], ['LIVE', 'Live']])}
        </div>
      </Section>
      <Section n={2} title={draft.platform === 'MT5' || draft.platform === 'MT4' ? 'Server and addresses' : draft.platform === 'FIX' ? 'FIX session' : draft.platform === 'CTRADER' ? 'cTrader Open API endpoint' : 'API app'}>
        {draft.platform === 'FIX' ? <FixFields settings={draft.settings} change={s => set('settings', s)} />
          : draft.platform === 'CTRADER' ? <CTraderFields settings={draft.settings} change={s => set('settings', s)} />
          : draft.platform === 'MT4' ? <Mt4Server settings={draft.settings} change={s => set('settings', s)} />
          : draft.platform === 'MT5' ? <Mt5Server settings={draft.settings} connectionId={draft.id} change={s => {
            // The server name decides the environment on a new connection (MetaQuotes-Demo is a demo server).
            const env = envOfServer(s.server_name);
            set(!draft.id && env && env !== draft.environment ? { settings: s, environment: env } : { settings: s });
          }} />
          : <RestSettings settings={draft.settings} change={s => set('settings', s)} />}
      </Section>
      <Section n={3} title="Name">
        <div className="cfg-pair">
          {text('name', 'Connection name', { required: true, maxLength: 120 })}
          {code('Connection ID', 'VTMARKETS_MT5_LIVE')}
        </div>
        <div className="cfg-pair">
          {pick('purpose', 'Role', ROLE_OPTIONS, 'Data source: one of the few feeds prices are taken from. Execution: trading accounts, watched for orders and fills.')}
          {status}
        </div>
      </Section>
      <Section n={4} title="Method, clock and limits">
        <div className="cfg-pair">
          {pick('adapter', 'Connection method', draft.platform === 'MT5'
            ? [['MTAPI_DIRECT', 'Direct (mtapi SDK)'], ['DEFAULT', 'Default (direct)'], ['TERMINAL_BRIDGE', 'Terminal bridge (official terminal)'], ['TERMINAL_PUSH', 'Terminal push — COREI EA (optional)']]
            : draft.platform === 'FIX' ? [['NATIVE', 'FIX session']] : [['DEFAULT', 'Default'], ['NATIVE', 'Broker API']],
            draft.platform === 'MT5' ? 'Direct: the licensed SDK speaks the MT5 protocol. Terminal bridge: COREI reads from the official terminal through the bridge program (Docker or a Windows machine). Terminal push (optional): the COREI EA inside a terminal sends prices over HTTPS' : undefined)}
          {pick('clock_source', 'Clock set from', [['UNKNOWN', 'Not set'], ['MANUAL', 'Entered by hand'], ['SERVER_FILE', 'servers.dat'], ['BROKER_LOGIN', 'Broker at login']])}
        </div>
        <div className="cfg-pair">
          {text('server_utc_offset_minutes', 'Server time offset from UTC (minutes)', { type: 'number', min: -720, max: 840 },
            'Most MT5 brokers: 120 in winter (GMT+2), 180 in summer')}
          {pick('dst_rule', 'Daylight saving', [['UNKNOWN', 'Not known'], ['NONE', 'None'], ['US', 'US rule (2nd Sun Mar – 1st Sun Nov)'], ['EU', 'EU rule (last Sun Mar – last Sun Oct)']])}
        </div>
        {draft.platform === 'MT5' && draft.adapter === 'TERMINAL_BRIDGE' && <BridgeFields settings={draft.settings} change={s => set('settings', s)} />}
        {draft.platform === 'MT5' && draft.settings?.server_details?.TimeZone != null && draft.server_utc_offset_minutes === '' &&
          <p className="cfg-note">servers.dat states TimeZone {String(draft.settings.server_details.TimeZone)}. <button type="button" className="cfg-link"
            onClick={() => setDraft({ ...draft, server_utc_offset_minutes: String(Number(draft.settings.server_details.TimeZone) * (Math.abs(Number(draft.settings.server_details.TimeZone)) <= 14 ? 60 : 1)), clock_source: 'SERVER_FILE' })}>Use it</button> — confirm against the broker's server time.</p>}
        <div className="cfg-pair">
          {text('max_orders_per_second', 'Max orders per second', { inputMode: 'decimal' }, 'Indian broker APIs (SEBI): 10. Empty = 5.')}
          {text('max_order_burst', 'Burst', { type: 'number', min: 1, max: 1000 }, 'Orders allowed at once')}
        </div>
        {draft.platform === 'REST_API' && text('registered_egress_ip', 'Registered static IP', { maxLength: 45 },
          "The IP whitelisted on the broker app (SEBI framework). Must equal this server's outbound IP (see Health).")}
        <Field label="Maintenance windows" hint="One per line: weekday(0=Mon) HH:MM-HH:MM note, in server time">
          <textarea aria-label="Maintenance windows" rows={2} value={(draft.maintenance ?? []).map((m: Row) => `${m.weekday} ${m.start}-${m.end} ${m.note ?? ''}`.trim()).join('\n')}
            onChange={e => set('maintenance', e.target.value.split('\n').map(l => l.trim()).filter(Boolean).map(l => {
              const m = l.match(/^(\d)\s+(\d\d:\d\d)-(\d\d:\d\d)\s*(.*)$/); return m ? { weekday: Number(m[1]), start: m[2], end: m[3], note: m[4], timezone: 'SERVER' } : { weekday: -1, start: l, end: '', note: '' }; }))} /></Field>
        {editing && draft.live_status && <p className="cfg-note">Live status: <strong>{draft.live_status.state}</strong>
          {draft.live_status.current_host ? ` · on ${draft.live_status.current_host}:${draft.live_status.current_port}` : ''}
          {draft.live_status.last_heartbeat_at ? ` · heartbeat ${new Date(draft.live_status.last_heartbeat_at).toLocaleTimeString()}` : ''}
          {draft.live_status.last_error ? ` · ${draft.live_status.last_error}` : ''}</p>}
      </Section></>;
  }

  if (entity === 'accounts') {
    const connection = refs.connections.find(c => c.id === draft.connection_id);
    const mt5 = connection?.platform === 'MT5';
    return <>
      <Section n={1} title="Connection">
        {ref('connection_id', 'Connection', refs.connections, r => `${r.code} · ${PLATFORM_TAG[r.platform] ?? r.platform} ${r.settings?.server_name ?? r.settings?.host ?? (r.settings?.api_base_url ?? '').replace(/^https:\/\//, '')} · ${r.environment}`)}
        {connection && <p className="cfg-note">Environment <Env value={connection.environment} />{mt5 ? <> · server <strong>{connection.settings?.server_name}</strong></> : null}</p>}
      </Section>
      <Section n={2} title="Account">
        <div className="cfg-pair">
          {text('external_account_id', mt5 ? 'Broker login (MT5 account number)' : 'Broker client ID',
            { required: true, maxLength: 64 }, mt5 ? 'The number you log in to MT5 with' : 'The broker\'s client code, e.g. Upstox UCC')}
          {text('holder_name', 'Account holder', { maxLength: 120 }, 'Person or company that owns it')}
        </div>
        <div className="cfg-pair">
          {text('name', 'Account name', { required: true, maxLength: 120 }, 'What it is for, e.g. Gold main, Hedge')}
          {code('Account ID', 'VTMARKETS_LIVE_1298617')}
        </div>
        <div className="cfg-pair">
          {text('base_currency', 'Account currency', { required: true, maxLength: 10 }, 'e.g. USD, INR')}
          {pick('trading_permission', 'Trading permission', [['DISABLED', 'Disabled'], ['READ_ONLY', 'Read only (data and account state)'], ['TRADING', 'Trading (orders allowed once order sending is on)']],
            'Orders also need order sending on for the environment')}
        </div>
        <details className="cfg-more"><summary>More options</summary>{status}</details>
      </Section>
      {mt5 && <Section n={3} title="Server addresses for this account">
        <AccountAddresses mode={draft.access_mode ?? 'SERVER'} own={draft.access_points ?? []} serverPoints={connection.settings?.access_points ?? []}
          connectionId={connection.id} routing={editing ? draft.routing : undefined}
          change={(mode, own) => setDraft({ ...draft, access_mode: mode, access_points: own })} />
      </Section>}
      <Section n={mt5 ? 4 : 3} title="Broker facts">
        <p className="cfg-note">{draft.broker_facts_read_at ? `Read from the broker ${new Date(draft.broker_facts_read_at).toLocaleString()}.`
          : 'Not read from the broker yet. Values typed here are marked as typed and replaced by what the broker reports at login.'}</p>
        <div className="cfg-pair">
          {pick('margin_mode', 'Margin mode', [['UNKNOWN', 'Not known'], ['HEDGING', 'Hedging (several positions per symbol)'], ['NETTING', 'Netting (one position per symbol)'], ['EXCHANGE', 'Exchange']],
            draft.margin_mode_source === 'BROKER' ? 'Confirmed by the broker' : draft.margin_mode !== 'UNKNOWN' ? 'Typed; not yet confirmed by the broker' : 'On netting, a SELL closes a BUY')}
          {text('broker_group', 'Broker account group / type', { maxLength: 120 }, 'e.g. Raw ECN, Standard')}
        </div>
        <div className="cfg-pair">{text('leverage', 'Leverage (1:x)', { type: 'number', min: 1, max: 10000 })}{text('stop_out_level', 'Stop-out level (%)', { inputMode: 'decimal' })}</div>
        <div className="cfg-pair">{text('max_open_orders', 'Broker cap: open orders', { type: 'number', min: 1 }, "The broker's hard cap, if it has one; not a COREI limit")}{text('max_open_positions', 'Broker cap: open positions', { type: 'number', min: 1 }, "The broker's hard cap, if it has one")}</div>
      </Section>
      <Section n={mt5 ? 5 : 4} title="Ownership, lifecycle and groups">
        <div className="cfg-pair">
          {pick('relationship', 'Whose money', [['OWN', 'Own (the holder)'], ['FIRM', 'Firm (Coreeye Group)'], ['CLIENT', 'Client (managed for someone else)']],
            draft.relationship === 'CLIENT' ? 'Needs a written mandate; licensing rules may apply' : undefined)}
          {text('mandate_reference', 'Mandate reference', { maxLength: 200 }, 'Agreement number or document link')}
        </div>
        <div className="cfg-pair">{text('mandate_expires_on', 'Mandate expires', { type: 'date' })}{text('expires_on', 'Account expires', { type: 'date' }, 'Demo accounts expire; idle live accounts get archived')}</div>
        {text('other_users', 'Other users of this login', { maxLength: 300 }, 'e.g. MT5 terminal on office PC, copier — they can conflict with COREI')}
        {editing && draft.password_changed_on && <p className="cfg-note">Password last saved {draft.password_changed_on}.</p>}
        {(refs.groups ?? []).length > 0 && <Field label="Account groups"><div className="cfg-checks">
          {(refs.groups ?? []).map((g: Row) => <label key={g.id}><input type="checkbox" checked={(draft.group_ids ?? []).includes(g.id)}
            onChange={e => set('group_ids', e.target.checked ? [...(draft.group_ids ?? []), g.id] : (draft.group_ids ?? []).filter((x: string) => x !== g.id))} /> {g.name}</label>)}
        </div></Field>}
      </Section></>;
  }

  if (entity === 'instruments') return <>
    {text('symbol', 'Symbol', { required: true, maxLength: 32 }, 'COREI canonical symbol, e.g. XAUUSD')}{text('name', 'Name', { required: true, maxLength: 120 })}
    {pick('asset_class', 'Asset class', ['FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION'])}
    <div className="cfg-pair">{text('base_currency', 'Base currency', { required: true })}{text('quote_currency', 'Quote currency', { required: true })}</div>
    <div className="cfg-pair">{text('price_precision', 'Price decimals', { required: true, type: 'number', min: 0, max: 12 })}{text('tick_size', 'Tick size', { required: true, inputMode: 'decimal' })}</div>
    <p className="cfg-note">These describe the contract itself (smallest tradable unit and increments). They are not your trade size: the size of each order is decided per order by the strategy, signal strength and portfolio risk.</p>
    <div className="cfg-pair">{text('quantity_precision', 'Quantity decimals', { required: true, type: 'number', min: 0, max: 12 })}{text('quantity_step', 'Smallest increment (contract)', { required: true, inputMode: 'decimal' })}</div>
    <div className="cfg-pair">{text('min_quantity', 'Smallest tradable quantity (contract)', { required: true, inputMode: 'decimal' })}{text('contract_size', 'Contract size', { required: true, inputMode: 'decimal' })}</div>
    {text('contract_unit', 'Contract unit', { required: true, maxLength: 32 }, 'e.g. oz, units, barrels')}
    {text('trading_session', 'Trading session', { maxLength: 200 }, 'Optional description, e.g. Sun 23:00 – Fri 22:00 UTC')}
    <h4>Where it trades</h4>
    <div className="cfg-pair">{text('exchange', 'Exchange', { maxLength: 20 }, 'e.g. NSE, BSE, MCX; blank for OTC (FX, CFDs)')}
      {pick('market', 'Market hours', [['', 'Round the clock (not set)'], ['NSE', 'NSE cash and indices'], ['NSE_FO', 'NSE F&O'], ['NSE_CD', 'NSE currency'], ['BSE', 'BSE cash and indices'], ['BSE_FO', 'BSE F&O'], ['MCX', 'MCX commodities'], ['FX', 'FX / CFD 24×5'], ['CRYPTO', 'Crypto 24×7']])}</div>
    <p className="cfg-note">Market hours decide when prices are expected: collection on <em>Market hours</em> and stale-price alarms follow them (NSE 09:15–15:30 IST, MCX to 23:30/23:55, FX Sun–Fri). Blank: round the clock.</p>
    <div className="cfg-pair">{text('isin', 'ISIN', { maxLength: 12 }, 'Equities, e.g. INE002A01018')}
      <Field label="Other names (aliases)" hint="Comma-separated names traders use, e.g. BANKNIFTY, NIFTY BANK; searches find the instrument by them">
        <input aria-label="Aliases" value={Array.isArray(draft.aliases) ? draft.aliases.join(', ') : draft.aliases ?? ''} onChange={e => set('aliases', e.target.value)} /></Field></div>
    <h4>Derivative contract (futures and options)</h4>
    <div className="cfg-pair">{text('underlying', 'Underlying', { maxLength: 40 }, 'e.g. NIFTY, BANKNIFTY, CRUDEOIL')}{text('lot_size', 'Lot size', { inputMode: 'decimal' }, 'Exchange lot, e.g. 75 for NIFTY')}</div>
    <div className="cfg-pair">{text('expiry', 'Expiry', { type: 'date' })}{text('strike', 'Strike', { inputMode: 'decimal' })}</div>
    {pick('option_type', 'Option type', [['', 'Not an option'], ['CE', 'Call (CE)'], ['PE', 'Put (PE)']])}{status}</>;

  const accountsHere = (refs.accounts ?? []).filter((a: Row) => a.connection_id === draft.connection_id);
  return <>{ref('connection_id', 'Connection', refs.connections, r => `${r.code} · ${PURPOSE_LABEL[r.purpose] ?? r.purpose}`)}
    <Field label="Applies to" hint="One broker can name the same market differently per account type (VT Markets: EURUSD-VIP). The most specific wins: one account, then a group, then every account on the server.">
      <select aria-label="Applies to" value={draft.account_id ? `a:${draft.account_id}` : draft.account_group_id ? `g:${draft.account_group_id}` : ''}
        onChange={e => { const v = e.target.value; setDraft((d: Row) => ({ ...d, account_id: v.startsWith('a:') ? v.slice(2) : '', account_group_id: v.startsWith('g:') ? v.slice(2) : '' })); }}>
        <option value="">Every account on this server</option>
        {(refs.groups ?? []).length > 0 && <optgroup label="Accounts in a group">{(refs.groups ?? []).map((g: Row) => <option key={g.id} value={`g:${g.id}`}>Group {g.code} · {g.name}</option>)}</optgroup>}
        {accountsHere.length > 0 && <optgroup label="One account">{accountsHere.map((a: Row) => <option key={a.id} value={`a:${a.id}`}>Only {a.code}{a.name && a.name !== a.code ? ` · ${a.name}` : ''}</option>)}</optgroup>}</select></Field>
    {ref('instrument_id', 'Instrument', refs.instruments, r => `${r.symbol} · ${r.name}`)}
    {text('broker_symbol', 'Broker symbol', { required: true, maxLength: 64 }, 'As shown in the broker platform, e.g. XAUUSD.r')}
    {pick('usage', 'Used for', ROLE_OPTIONS)}
    <div className="cfg-pair">{text('broker_contract_size', 'Broker contract size', { inputMode: 'decimal' }, 'Only if it differs from the instrument')}
      {text('broker_price_digits', 'Broker price digits', { type: 'number', min: 0, max: 12 }, 'Optional')}</div>{status}</>;
}

/* ---------- credentials (write-only) ---------- */

export const ROLE_OPTIONS: [string, string][] = [['MARKET_DATA', 'Data source'], ['TRADING', 'Execution'], ['BOTH', 'Data source and execution']];
export const PURPOSE_LABEL: Record<string, string> = Object.fromEntries(ROLE_OPTIONS);
export const CTRADER_BLANK = { host: 'live.ctraderapi.com', port: 5035, oauth_redirect_url: '' };

/** cTrader Open API: the endpoint per environment; the app's client ID and secret are saved as the server's credential. */
export function CTraderFields({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const s = { ...CTRADER_BLANK, ...settings };
  return <div className="cfg-pair">
    <Field label="Open API host" hint="live.ctraderapi.com or demo.ctraderapi.com"><Choice label="cTrader host" value={s.host}
      options={[['live.ctraderapi.com', 'live.ctraderapi.com (live)'], ['demo.ctraderapi.com', 'demo.ctraderapi.com (demo)']]} onChange={v => change({ ...s, host: v })} /></Field>
    <Field label="Port"><input aria-label="cTrader port" type="number" min={1} max={65535} value={s.port} onChange={e => change({ ...s, port: e.target.value })} /></Field>
    <Field label="OAuth redirect URL" hint="Optional: the address registered with your Open API app"><input aria-label="cTrader redirect URL" type="url" value={s.oauth_redirect_url ?? ''} onChange={e => change({ ...s, oauth_redirect_url: e.target.value })} /></Field>
  </div>;
}

/** MT4 server: the name from the broker's list and its addresses (host:port), typed as the broker gives them. */
export function Mt4Server({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const points: Row[] = settings.access_points ?? [];
  const text = points.filter(p => p.host).map(p => `${p.host}:${p.port ?? 443}`).join(', ');
  return <div className="cfg-pair">
    <Field label="MT4 server name" hint="As in the MT4 terminal's login window, e.g. VTMarkets-Live"><input aria-label="MT4 server name" maxLength={120} value={settings.server_name ?? ''}
      onChange={e => change({ ...settings, server_name: e.target.value, access_points: points.map(p => ({ ...p, name: e.target.value })) })} /></Field>
    <Field label="Addresses" hint="host:port, comma-separated"><input aria-label="MT4 server addresses" defaultValue={text}
      onChange={e => change({ ...settings, access_points: e.target.value.split(/[\s,;]+/).filter(Boolean).map(x => { const m = x.match(/^(.+?)(?::(\d+))?$/)!;
        return { name: settings.server_name ?? '', host: m[1], port: Number(m[2] ?? 443), enabled: true, source: 'MANUAL' }; }) })} /></Field>
  </div>;
}

export const FIX_BLANK = { host: '', port: 443, tls: true, tls_verify: true, begin_string: 'FIX.4.4', sender_comp_id: '', target_comp_id: '', sender_sub_id: '',
  target_sub_id: '', heartbeat_seconds: 30, reset_seq_on_logon: true, md_depth: 1, md_snapshots_only: false };

/** A FIX 4.4 / 4.2 session: where it is and who we are on it (the venue sends these in its onboarding sheet). */
export function FixFields({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const s: Row = { ...FIX_BLANK, ...settings };
  const set = (patch: Row) => change({ ...s, ...patch });
  const box = (k: string, label: string) => <label className="cfg-check"><input type="checkbox" aria-label={label} checked={!!s[k]} onChange={e => set({ [k]: e.target.checked })} /> {label}</label>;
  return <div className="cfg-fix">
    <div className="cfg-pair">
      <Field label="Host"><input aria-label="FIX host" required maxLength={253} value={s.host} onChange={e => set({ host: e.target.value.trim() })} /></Field>
      <Field label="Port"><input aria-label="FIX port" required type="number" min={1} max={65535} value={s.port} onChange={e => set({ port: e.target.value })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="SenderCompID (49)" hint="Your ID on the session"><input aria-label="SenderCompID" required maxLength={64} value={s.sender_comp_id} onChange={e => set({ sender_comp_id: e.target.value.trim() })} /></Field>
      <Field label="TargetCompID (56)" hint="The venue's ID"><input aria-label="TargetCompID" required maxLength={64} value={s.target_comp_id} onChange={e => set({ target_comp_id: e.target.value.trim() })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="SenderSubID (50)"><input aria-label="SenderSubID" maxLength={64} value={s.sender_sub_id} onChange={e => set({ sender_sub_id: e.target.value.trim() })} /></Field>
      <Field label="TargetSubID (57)" hint="Often QUOTE or TRADE"><input aria-label="TargetSubID" maxLength={64} value={s.target_sub_id} onChange={e => set({ target_sub_id: e.target.value.trim() })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="Version"><Choice label="FIX version" value={s.begin_string} options={[['FIX.4.4', 'FIX 4.4'], ['FIX.4.2', 'FIX 4.2']]} onChange={v => set({ begin_string: v })} /></Field>
      <Field label="Heartbeat (s)"><input aria-label="Heartbeat seconds" type="number" min={5} max={120} value={s.heartbeat_seconds} onChange={e => set({ heartbeat_seconds: e.target.value })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="Book depth" hint="1 = top of book"><input aria-label="Book depth" type="number" min={1} max={10} value={s.md_depth} onChange={e => set({ md_depth: e.target.value })} /></Field>
      <Field label="Session">{box('tls', 'TLS')} {box('tls_verify', 'Verify certificate')} {box('reset_seq_on_logon', 'Reset sequence on logon')} {box('md_snapshots_only', 'Snapshots only')}</Field>
    </div>
  </div>;
}

/** Where the COREI MT5 terminal bridge runs (connectors/mt5-bridge): host, port and the certificate fingerprint it prints. */
export function BridgeFields({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const b = settings.bridge ?? { host: '', port: 9443, fingerprint: '' };
  const set = (patch: Row) => change({ ...settings, bridge: { ...b, ...patch } });
  return <div className="cfg-bridge">
    <p className="cfg-note">Run <code>corei_mt5_bridge.py</code> on the machine with the MT5 terminal (Windows VPS or Wine). It prints its certificate fingerprint and makes a token file; enter them here and save the token under Credential.</p>
    <div className="cfg-pair">
      <Field label="Bridge host" hint="DNS name or IP of the machine running the terminal"><input aria-label="Bridge host" maxLength={253} value={b.host} onChange={e => set({ host: e.target.value.trim() })} /></Field>
      <Field label="Bridge port"><input aria-label="Bridge port" type="number" min={1} max={65535} value={b.port} onChange={e => set({ port: e.target.value })} /></Field>
    </div>
    <Field label="Certificate fingerprint (SHA-256)" hint="As the bridge prints it: 32 pairs separated by colons"><input aria-label="Certificate fingerprint" maxLength={95} value={b.fingerprint}
      onChange={e => set({ fingerprint: e.target.value.trim().toUpperCase() })} placeholder="AB:CD:…" /></Field>
  </div>;
}

/** Terminal push: a push token (shown once) and the COREI EA, with the steps to run it in any MT5 terminal. */
export function PushPanel({ conn, canEdit, reload }: { conn: Row; canEdit: boolean; reload: () => void }) {
  const [token, setToken] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [seen, setSeen] = useState<Row[]>([]);
  useEffect(() => { void api('md/push/terminals').then(r => setSeen(r.items)).catch(() => undefined); }, [conn.id]);
  async function issue() {
    if (conn.credential_kind && !window.confirm('Issue a new push token? The EA with the old token stops being accepted until you paste the new one.')) return;
    setBusy(true); setError('');
    try { const r = await api(`md/push-token/${conn.id}`, 'POST'); setToken(r.token ?? ''); if (!r.token) setError(pendingMessage(r) ?? 'Waiting for approval.'); reload(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const mine = seen;  // every terminal sending to COREI, with the account it sends for
  return <section className="cfg-section" aria-label="Terminal push">
    <h4><span>★</span> Terminal push — COREI EA</h4>
    <ol className="cfg-steps-list">
      <li><a href="/api/md/push/ea" download="COREI_Push.mq5">Download COREI_Push.mq5</a>, open it in MetaEditor (in MT5: Tools › MetaQuotes Language Editor) and press <b>F7</b> to compile.</li>
      <li>In MT5: <b>Tools › Options › Expert Advisors</b> › tick “Allow WebRequest for listed URL” and add <code>{window.location.origin}</code>.</li>
      <li>Log the terminal in to the account; attach <b>COREI_Push</b> to any chart. Inputs: <b>AccountCode</b> = the COREI account ID, <b>PushToken</b> = the token below. Nothing is traded.</li>
      <li>Keep that terminal running (your PC, a Windows VPS or the COREI Docker container). Prices arrive within seconds; the account shows CONNECTED.</li>
    </ol>
    {canEdit && <p><button type="button" className="primary" disabled={busy} onClick={() => void issue()}>{conn.credential_kind ? 'New push token…' : 'Create push token'}</button>
      {' '}{conn.credential_kind ? <span className="cfg-note">A token is saved (hidden). A new one replaces it.</span> : <span className="cfg-note">No token yet.</span>}</p>}
    {token && <p className="cfg-ok" role="status">Push token (shown only now — copy it into the EA’s PushToken): <code className="cfg-secret-once">{token}</code>{' '}
      <button type="button" onClick={() => void navigator.clipboard?.writeText(token)}>Copy</button></p>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {mine.length > 0 && <table className="cfg-table" aria-label="Terminals sending to COREI"><thead><tr><th>Account</th><th>Terminal login</th><th>Server</th><th>Build</th><th>Last push</th><th>From</th></tr></thead>
      <tbody>{mine.map(t => <tr key={t.account_id}><td>{t.account_code}</td><td>{t.login}</td><td>{t.server}</td><td>{t.build}</td>
        <td>{new Date(t.last_push_at).toLocaleString()}</td><td>{t.remote_ip}</td></tr>)}</tbody></table>}
  </section>;
}

function CredentialPanel({ owner, row, reload }: { owner: 'accounts' | 'connections'; row: Row; reload: () => void }) {
  const kind = owner === 'accounts' ? 'MT5_LOGIN' : row.platform === 'FIX' ? 'FIX_LOGIN' : row.platform === 'MT5' ? 'BRIDGE_TOKEN' : row.platform === 'CTRADER' || row.settings?.auth_method === 'OAUTH2' ? 'OAUTH_CLIENT' : 'API_KEY';
  const empty = { password: '', access_level: 'FULL', api_key: '', api_secret: '', client_id: '', client_secret: '', token: '', username: '' };
  const [secret, setSecret] = useState(empty), [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const levels = owner === 'accounts'
    ? [['FULL', 'Master password', row.master_credential_kind, row.master_credential_updated_at], ['READ_ONLY', 'Investor password', row.investor_credential_kind, row.investor_credential_updated_at]] as const
    : [['FULL', kind === 'API_KEY' ? 'API key and secret' : kind === 'BRIDGE_TOKEN' ? 'Terminal bridge token' : kind === 'FIX_LOGIN' ? 'FIX session login' : 'OAuth client', row.credential_kind, row.credential_updated_at]] as const;
  const savedLevel = (level: string) => levels.find(l => l[0] === level)?.[2];
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setMessage('');
    const credential = kind === 'MT5_LOGIN' ? { kind, password: secret.password, access_level: secret.access_level }
      : kind === 'API_KEY' ? { kind, api_key: secret.api_key, api_secret: secret.api_secret }
      : kind === 'BRIDGE_TOKEN' ? { kind, token: secret.token }
      : kind === 'FIX_LOGIN' ? { kind, username: secret.username, password: secret.password } : { kind, client_id: secret.client_id, client_secret: secret.client_secret };
    try { const result = await api(`config/${owner}/${row.id}/credential`, 'PUT', { credential }); setSecret(empty);
      setMessage(pendingMessage(result) ?? 'Saved and encrypted.'); reload(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function remove(level: string, label: string) {
    const connectionKey = owner === 'connections' && row.platform === 'REST_API';
    const prompt = connectionKey ? `Delete the saved API key and secret from ${row.code}? This affects every account using this connection. The key remains registered with the broker.`
      : `Remove the ${label.toLowerCase()}? The adapter cannot use it until a new one is saved.`;
    if (!window.confirm(prompt)) return;
    setBusy(true); setError('');
    try { await api(`config/${owner}/${row.id}/credential${owner === 'accounts' ? '?level=' + level : ''}`, 'DELETE'); setMessage(`${label} removed.`); reload(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const input = (k: keyof typeof empty, label: string) => <Field label={label}><input aria-label={label} required type="password" autoComplete="new-password" maxLength={4096}
    value={secret[k]} onChange={e => setSecret({ ...secret, [k]: e.target.value })} /></Field>;
  return <form className="cfg-credential" onSubmit={save}>
    <h3>{owner === 'accounts' ? 'MT5 login' : kind === 'API_KEY' ? 'API key' : kind === 'BRIDGE_TOKEN' ? 'Terminal bridge token' : kind === 'FIX_LOGIN' ? 'FIX session login' : 'OAuth client'}</h3>
    {kind === 'FIX_LOGIN' && <>{input('username', 'Username (tag 553)')}{input('password', 'Password (tag 554)')}<p className="cfg-note">Leave unsaved if the venue authenticates by IP and CompIDs only.</p></>}
    {kind === 'BRIDGE_TOKEN' && row.adapter !== 'TERMINAL_BRIDGE' && <p className="cfg-note">Set the connection method to MT5 terminal bridge first.</p>}
    <ul className="cfg-cred-status">{levels.map(([level, label, saved, updated]) => <li key={level}>
      <span>{label}</span>{saved ? <span className="cfg-pill ok">Saved · {new Date(updated).toLocaleDateString()}</span> : <span className="cfg-pill off">Not saved</span>}
      {saved && <button type="button" className="cfg-link" disabled={busy} onClick={() => void remove(level, label)}>{owner === 'connections' && row.platform === 'REST_API' ? 'Delete API key' : 'Remove'}</button>}</li>)}</ul>
    {owner === 'accounts' && <p className="cfg-note">The master password can trade. The investor password is read-only and is used for data and monitoring, so a leak there cannot place orders.</p>}
    {kind === 'MT5_LOGIN' && <><Field label="Password type"><Choice label="Password type" value={secret.access_level} options={[['FULL', 'Master password (can trade)'], ['READ_ONLY', 'Investor password (read only)']]}
        onChange={v => setSecret({ ...secret, access_level: v })} /></Field>
      {input('password', savedLevel(secret.access_level) ? 'New password' : 'Password')}</>}
    {kind === 'API_KEY' && <>{input('api_key', 'API key')}{input('api_secret', 'API secret')}</>}
    {kind === 'BRIDGE_TOKEN' && input('token', 'Bridge token (contents of corei-bridge.token)')}
    {kind === 'OAUTH_CLIENT' && <>{input('client_id', 'Client ID')}{input('client_secret', 'Client secret')}</>}
    {error && <p role="alert" className="cfg-error">{error}</p>}{message && <p role="status" className="cfg-ok">{message}</p>}
    <div className="cfg-actions"><button className="primary" disabled={busy}>{savedLevel(secret.access_level) ? 'Replace' : 'Save'}</button>
      <small className="cfg-note">You may be asked for your own password again. On LIVE, replacing a saved password needs a second person.</small></div>
  </form>;
}

/* ---------- add an MT5 account in one step ---------- */

const serverKey = (name: string) => name.trim().replace(/\s+/g, ' ').toLowerCase();

/** Login, password and server together, the way MT5 does it. The server's connection is
 *  reused when one exists for this provider, environment and server (or created). */
/** Upstox in one step: the broker and its API connection are created when missing (LIVE: Upstox has no
 * demo for market data; nothing here can send an order), the app's key and secret are saved on the
 * connection, then the account. Next: Log in at broker (daily). */
function AddUpstoxAccount({ close, created }: { close: () => void; created: (account: Row, note: string) => void }) {
  const [f, setF] = useState<Row>({ external_account_id: '', holder_name: '', name: 'Upstox', code: '', base_currency: 'INR',
    trading_permission: 'READ_ONLY', client_id: '', client_secret: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const redirect = `${window.location.origin}/api/oauth/upstox/callback`;
  const set = (patch: Row) => setF(old => { const next = { ...old, ...patch };
    if (!next._codeEdited && next.external_account_id) next.code = `UPSTOX_${idPart(next.external_account_id)}`.slice(0, 32); return next; });
  const ready = f.external_account_id && f.name && f.code;
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try {
      const result = await api('config/upstox/account-setup', 'POST', {
        account: { code: f.code, name: f.name, external_account_id: f.external_account_id, holder_name: f.holder_name, base_currency: f.base_currency,
          trading_permission: f.trading_permission },
        ...(f.client_id || f.client_secret ? { credential: { client_id: f.client_id, client_secret: f.client_secret } } : {}) });
      setF(old => ({ ...old, client_id: '', client_secret: '' }));
      created(result.account, `Upstox account added${result.connection_action === 'CREATED' ? ` with connection ${result.connection.code}` : ` to ${result.connection.code}`}. `
        + 'Next: Log in at broker (below), then Market data › Plans › New.');
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <FloatingWindow storageKey="corei-os-mini.add-upstox-account.v1" width={680} title="Add Upstox account" subtitle="Broker, connection, app key and account in one step"
    onClose={close}
    footer={<div className="cfg-actions"><button className="primary" form="cfg-add-upstox" disabled={busy || !ready}>{busy ? 'Adding…' : 'Add account'}</button>
      {!ready && <span className="cfg-note">Enter the Upstox client ID.</span>}</div>}>
    <form className="cfg-form" id="cfg-add-upstox" onSubmit={save} autoComplete="off">
      <Section n={1} title="Your Upstox app">
        <p className="cfg-note">In the Upstox developer console (account.upstox.com/developer/apps) create an app, set its <b>Redirect URL</b> to exactly:</p>
        <Field label="Redirect URL"><input aria-label="Redirect URL" readOnly value={redirect} onFocus={e => e.currentTarget.select()} /></Field>
        <div className="cfg-pair">
          <Field label="API key" hint="Saved encrypted on the connection; leave empty if already saved"><input aria-label="API key" autoComplete="off" value={f.client_id} onChange={e => set({ client_id: e.target.value })} /></Field>
          <Field label="API secret"><input aria-label="API secret" type="password" autoComplete="new-password" value={f.client_secret} onChange={e => set({ client_secret: e.target.value })} /></Field>
        </div>
      </Section>
      <Section n={2} title="Account">
        <div className="cfg-pair">
          <Field label="Upstox client ID" hint="Your UCC, shown in the Upstox app under Profile (e.g. 7AB123)"><input aria-label="Upstox client ID" required maxLength={64} value={f.external_account_id} onChange={e => set({ external_account_id: e.target.value.trim().toUpperCase() })} /></Field>
          <Field label="Holder name"><input aria-label="Holder name" maxLength={120} value={f.holder_name} onChange={e => set({ holder_name: e.target.value })} /></Field>
        </div>
        <div className="cfg-pair">
          <Field label="Account name"><input aria-label="Account name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value })} /></Field>
          <Field label="Account ID" hint="Suggested; fixed after saving"><input aria-label="Account ID" required maxLength={32} pattern="[A-Za-z][A-Za-z0-9_]{1,31}" value={f.code} onChange={e => set({ code: e.target.value, _codeEdited: e.target.value !== '' })} /></Field>
        </div>
        <div className="cfg-pair">
          <Field label="Currency"><input aria-label="Currency" required maxLength={10} value={f.base_currency} onChange={e => set({ base_currency: e.target.value })} /></Field>
          <Field label="Trading permission" hint="What this account is authorised for. Trading records the permission; no order is sent until order sending is built (Stage 7) and switched on in Setup › Controls › Order sending."><Choice label="Trading permission" value={f.trading_permission}
            options={[['READ_ONLY', 'Read only (data and account state)'], ['TRADING', 'Trading (orders allowed once order sending is on)'], ['DISABLED', 'Disabled']]} onChange={v => set({ trading_permission: v })} /></Field>
        </div>
      </Section>
      <Section n={3} title="After saving">
        <p className="cfg-note">Open the account and click <b>Log in at broker</b> (or TOTP login). Upstox logins last until 03:30 IST, so this is a daily step. Then add a plan under Market data › Plans with an instrument key such as NSE_INDEX|Nifty 50.</p>
      </Section>
      {error && <p role="alert" className="cfg-error">{error}</p>}
    </form>
  </FloatingWindow>;
}

function AddMt5Account({ refs, close, created, preset }: { refs: Record<string, Row[]>; close: () => void; created: (account: Row | null, note: string) => void; preset?: Row | null }) {
  // Opened on a server in the Connectivity tree: broker, environment, server and its addresses come from that connection.
  const [f, setF] = useState<Row>({ provider_id: preset?.provider_id ?? '', environment: preset?.environment ?? 'LIVE', external_account_id: '', holder_name: '', name: '', code: '',
    base_currency: 'USD', trading_permission: 'DISABLED', password: '', access_level: 'FULL', access_mode: 'FASTEST', access_points: [] as Row[],
    server: preset ? { server_name: preset.settings?.server_name ?? '', access_points: (preset.settings?.access_points ?? []).map((p: Row) => ({ ...p })) }
      : { server_name: '', access_points: [] as Row[] }, adapter: preset?.adapter === 'TERMINAL_BRIDGE' ? 'TERMINAL_BRIDGE' : 'MTAPI_DIRECT', bridge_token: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  // Registered bridge nodes (Setup › Bridge nodes): picking one replaces host, port, fingerprint and token.
  const [nodes, setNodes] = useState<Row[]>([]), [nodeId, setNodeId] = useState('');
  useEffect(() => { void api('config/bridge-nodes').then(d => { const usable = d.items.filter((n: Row) => n.status !== 'DISABLED'); setNodes(usable);
    const def = usable.find((n: Row) => n.default_for_new) ?? (usable.length === 1 ? usable[0] : null);
    if (def) { setNodeId(def.id); if (!preset) setF(old => ({ ...old, adapter: 'TERMINAL_BRIDGE' })); } }).catch(() => setNodes([])); }, []);
  const provider = refs.providers.find(p => p.id === f.provider_id);
  const set = (patch: Row) => setF(old => {
    const next = { ...old, ...patch };
    if (!next._codeEdited && provider && next.external_account_id) next.code = `${(refs.providers.find(p => p.id === next.provider_id) ?? provider).code}_${next.environment}_${idPart(next.external_account_id)}`.slice(0, 32).replace(/_+$/, '');
    return next;
  });
  const existing = f.server.server_name ? refs.connections.filter(c => c.platform === 'MT5' && c.provider_id === f.provider_id && c.environment === f.environment
    && serverKey(c.settings?.server_name ?? '') === serverKey(f.server.server_name)) : [];
  const match = existing.length === 1 ? existing[0] : null;
  const key = (p: Row) => `${p.host}|${p.port}`;
  const picked: Row[] = (f.server.access_points ?? []).filter((p: Row) => p.host);
  const newOnServer = match ? picked.filter(p => !(match.settings.access_points ?? []).some((q: Row) => key(q) === key(p))) : [];
  // Addresses the account may choose from: the server's (existing connection) plus any picked here.
  const serverPoints: Row[] = match ? [...match.settings.access_points, ...newOnServer.map(p => ({ ...p, enabled: false }))] : picked;
  const bridgeNew = !match && f.adapter === 'TERMINAL_BRIDGE' && !nodeId;
  const bridgeOk = !bridgeNew || (f.server.bridge?.host && /^([0-9A-F]{2}:){31}[0-9A-F]{2}$/.test(f.server.bridge?.fingerprint ?? '') && f.bridge_token);
  const ready = f.provider_id && f.external_account_id && f.name && f.code && f.server.server_name && picked.length > 0
    && (f.access_mode !== 'ACCOUNT' || f.access_points.length > 0) && bridgeOk;
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try {
      const server = { server_name: f.server.server_name, company_name: f.server.company_name || null, server_details: f.server.server_details ?? {},
        access_points: picked.map((p: Row) => ({ name: p.name || f.server.server_name, host: p.host, port: Number(p.port), enabled: p.enabled !== false,
          source: p.source ?? 'UNKNOWN', confirmed_at: p.confirmed_at ?? null })),
        ...(bridgeNew ? { bridge: { host: f.server.bridge.host, port: Number(f.server.bridge.port || 9443), fingerprint: f.server.bridge.fingerprint } } : {}) };
      const result = await api('config/mt5/account-setup', 'POST', {
        provider_id: f.provider_id, environment: f.environment, server, ...(match ? { connection_id: match.id } : {}),
        ...(bridgeNew ? { adapter: 'TERMINAL_BRIDGE', bridge_token: f.bridge_token } : match ? {} : { adapter: f.adapter === 'TERMINAL_BRIDGE' ? 'DEFAULT' : f.adapter }),
        ...(nodeId ? { bridge_node_id: nodeId } : {}),
        account: { code: f.code, name: f.name, external_account_id: f.external_account_id, holder_name: f.holder_name, base_currency: f.base_currency,
          trading_permission: f.trading_permission, access_mode: f.access_mode, access_points: f.access_points.map((p: Row) => ({ host: p.host, port: Number(p.port) })) },
        ...(f.password ? { credential: { kind: 'MT5_LOGIN', password: f.password, access_level: f.access_level } } : {}) });
      setF(old => ({ ...old, password: '', bridge_token: '' }));
      const pending = pendingMessage(result);
      if (pending) { created(null, pending); return; }
      const note = result.connection_action === 'CREATED' ? `Account added. Connection ${result.connection.code} was created for ${server.server_name}${bridgeNew ? ' through the MT5 terminal bridge' : ''}.`
        : result.connection_action === 'ADDRESSES_ADDED' ? `Account added to ${result.connection.code}. New addresses were added to the server unticked; other accounts are unchanged.`
        : `Account added to ${result.connection.code}.`;
      created(result.account, note + (result.bridge_route_added ? ` It connects through bridge node ${nodes.find(n => n.id === nodeId)?.name ?? '(default)'}.` : ''));
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const dirty = !!(f.provider_id || f.external_account_id || f.server.server_name || f.password);
  return <FloatingWindow storageKey="corei-os-mini.add-mt5-account.v1" width={780} title="Add MT5 account" subtitle="Login, password and server in one step"
    onClose={() => { if (!dirty || window.confirm('Discard this account?')) close(); }}
    footer={<div className="cfg-actions"><button className="primary" form="cfg-add-mt5" disabled={busy || !ready}>{busy ? 'Adding…' : 'Add account'}</button>
      {!ready && <span className="cfg-note">Fill in broker, login, name and server with at least one address.</span>}</div>}>
    <form className="cfg-form" id="cfg-add-mt5" onSubmit={save} autoComplete="off">
      <Section n={1} title="Broker">
        <div className="cfg-pair">
          <Field label="Provider"><select aria-label="Provider" required value={f.provider_id} onChange={e => set({ provider_id: e.target.value })}>
            <option value="" disabled>Select…</option>{refs.providers.map(r => <option key={r.id} value={r.id}>{r.name} ({r.code})</option>)}</select></Field>
          <Field label="Environment" hint={envOfServer(f.server.server_name) && envOfServer(f.server.server_name) !== f.environment ? `Server ${f.server.server_name} is a ${envOfServer(f.server.server_name)!.toLowerCase()} server` : undefined}>
            <Choice label="Environment" value={f.environment} options={[['LIVE', 'Live'], ['DEMO', 'Demo']]} onChange={v => set({ environment: v })} /></Field>
        </div>
      </Section>
      <Section n={2} title="Login">
        <div className="cfg-pair">
          <Field label="Broker login (MT5 account number)"><input aria-label="Broker login" required inputMode="numeric" pattern="[1-9][0-9]{0,19}" maxLength={20}
            value={f.external_account_id} onChange={e => set({ external_account_id: e.target.value.trim() })} /></Field>
          <Field label="Account holder" hint="Person or company that owns it"><input aria-label="Account holder" maxLength={120} value={f.holder_name} onChange={e => set({ holder_name: e.target.value })} /></Field>
        </div>
        <div className="cfg-pair">
          <Field label="Password" hint="Stored encrypted; can be added later"><input aria-label="Password" type="password" autoComplete="new-password" maxLength={4096}
            value={f.password} onChange={e => set({ password: e.target.value })} /></Field>
          <Field label="Password type"><Choice label="Password type" value={f.access_level} options={[['FULL', 'Master (can trade)'], ['READ_ONLY', 'Investor (read only)']]} onChange={v => set({ access_level: v })} /></Field>
        </div>
      </Section>
      <Section n={3} title="Server">
        <Mt5Server settings={f.server} change={server => { const env = envOfServer(server.server_name); set(env ? { server, environment: env } : { server }); }} connectionId={match?.id}
          summaryOnly={match ? <p className="cfg-note">The server's addresses are kept on its connection; choose this account's addresses in step 4.</p> : undefined} />
        {match && <p className="cfg-note cfg-reuse">Uses existing connection <strong>{match.code}</strong> ({match.settings.access_points.length} addresses, {match.account_count} account{match.account_count === 1 ? '' : 's'}).
          {newOnServer.length > 0 && <> {newOnServer.length} address{newOnServer.length === 1 ? '' : 'es'} of this server that it does not have yet will be added to it unticked, so other accounts are not affected.</>}</p>}
        {existing.length > 1 && <p className="cfg-error">{existing.length} connections use this server ({existing.map(c => c.code).join(', ')}). Add the account from the account form and choose one.</p>}
        {!match && f.server.server_name && f.provider_id && <p className="cfg-note">A new connection will be created for this server.</p>}
        {!match && <div className="cfg-method">
          <Field label="How COREI reaches it" hint={f.adapter === 'TERMINAL_BRIDGE' ? 'Fallback when the SDK is behind a broker: the official MT5 terminal logs in and COREI reads from it through the bridge program' : 'The licensed mtapi SDK talks to the broker server itself, as institutional platforms do; keep its update subscription current'}>
            <Choice label="Connection method" value={f.adapter} options={[['MTAPI_DIRECT', 'Direct SDK'], ['TERMINAL_BRIDGE', 'MT5 terminal bridge (official terminal)'], ['TERMINAL_PUSH', 'Terminal push — COREI EA (optional)']]} onChange={v => set({ adapter: v })} /></Field>
          {f.adapter === 'TERMINAL_PUSH' && <p className="cfg-note">The COREI EA in an MT5 terminal logged in to this account sends its prices to COREI over HTTPS. No password is needed here.
            After adding, open the account's server › <b>Create push token</b> and follow the steps there.</p>}
          {f.adapter === 'TERMINAL_BRIDGE' && nodes.length > 0 && <Field label="Bridge node" hint="A Windows machine registered under Setup › Bridge nodes: nothing else to enter">
            <select aria-label="Bridge node" value={nodeId} onChange={e => setNodeId(e.target.value)}>
              {nodes.map(n => <option key={n.id} value={n.id}>{n.name} · {n.mode === 'TUNNEL' ? 'tunnel' : n.host ?? 'not enrolled'} · {String(n.status).toLowerCase()}{n.default_for_new ? ' · default' : ''}</option>)}
              <option value="">Enter host, fingerprint and token by hand…</option></select></Field>}
          {f.adapter === 'TERMINAL_BRIDGE' && nodes.length === 0 && <p className="cfg-note">No bridge node yet. Easier than typing a fingerprint and token: Setup › Bridge nodes › <em>Connect a Windows machine</em> (once), then every account picks it here.</p>}
          {f.adapter === 'TERMINAL_BRIDGE' && !nodeId && <>
            <BridgeFields settings={f.server} change={server => set({ server })} />
            <Field label="Bridge token" hint="Contents of corei-bridge.token on the bridge machine; stored encrypted on the connection"><input aria-label="Bridge token" type="password" autoComplete="new-password" maxLength={200}
              value={f.bridge_token} onChange={e => set({ bridge_token: e.target.value.trim() })} /></Field></>}
        </div>}
        {match && <p className="cfg-note">Reached through {match.adapter === 'TERMINAL_BRIDGE' ? 'the MT5 terminal bridge' : match.adapter === 'TERMINAL_PUSH' ? 'terminal push (COREI EA)' : 'the direct SDK'} (set on the connection).</p>}
      </Section>
      <Section n={4} title="Addresses for this account">
        {serverPoints.length ? <AccountAddresses mode={f.access_mode} own={f.access_points} serverPoints={serverPoints} connectionId={match?.id}
          change={(mode, own) => set({ access_mode: mode, access_points: own })} />
          : <p className="cfg-note">Choose the server first.</p>}
      </Section>
      <Section n={5} title="Name and permission">
        <div className="cfg-pair">
          <Field label="Account name" hint="What it is for, e.g. Gold main"><input aria-label="Account name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value })} /></Field>
          <Field label="Account ID" hint="Suggested; fixed after saving"><input aria-label="Account ID" required maxLength={32} pattern="[A-Za-z][A-Za-z0-9_]{1,31}" value={f.code}
            onChange={e => set({ code: e.target.value, _codeEdited: e.target.value !== '' })} /></Field>
        </div>
        <div className="cfg-pair">
          <Field label="Account currency"><input aria-label="Account currency" required maxLength={10} value={f.base_currency} onChange={e => set({ base_currency: e.target.value })} /></Field>
          <Field label="Trading permission" hint="Orders also need order sending on"><Choice label="Trading permission" value={f.trading_permission}
            options={[['DISABLED', 'Disabled'], ['READ_ONLY', 'Read only'], ['TRADING', 'Trading']]} onChange={v => set({ trading_permission: v })} /></Field>
        </div>
      </Section>
      {error && <p role="alert" className="cfg-error">{error}</p>}
    </form>
  </FloatingWindow>;
}

/* ---------- Add account: the one form for every account ---------- */

/** Where an account is added from (tree, provider, technology, server, Accounts tab): what is already known. */
export type AccountPreset = { provider_id?: string; technology?: string; connection_id?: string };
const DEFAULT_SETTINGS: Record<string, () => Row> = {
  MT4: () => ({ server_name: '', access_points: [] }), CTRADER: () => ({ ...CTRADER_BLANK }), FIX: () => ({ ...FIX_BLANK }),
  REST_API: () => ({ api_base_url: '', auth_method: 'API_KEY', oauth_redirect_url: '' }),
};
const STATE_TEXT: Record<string, string> = { BUILT: 'built', PARTIAL: 'partly built', PLANNED: 'recorded now · connector planned' };

/** Provider › technology › account › server › login. MT5 and Upstox continue in their dedicated one-step forms
 *  (server list, addresses, bridge; app key and daily login); MT4, cTrader, FIX and other APIs are completed here. */
function AddAccount({ refs, preset, close, created, openMt5, openUpstox, newProvider }: { refs: Record<string, Row[]>; preset: AccountPreset;
  close: () => void; created: (account: Row | null, note: string) => void; openMt5: (p: Row) => void; openUpstox: () => void; newProvider: () => void }) {
  const presetConn = refs.connections.find(c => c.id === preset.connection_id);
  const techOf = (platform?: string) => platform === 'REST_API' ? 'API' : platform;
  const [cat, setCat] = useState<Row | null>(null);
  useEffect(() => { void api('config/tree').then(setCat).catch(() => setCat({ technologies: [], api_presets: [] })); }, []);
  const [f, setF] = useState<Row>({ provider_id: preset.provider_id ?? presetConn?.provider_id ?? '', tech: preset.technology ?? techOf(presetConn?.platform) ?? '',
    api: '', environment: presetConn?.environment ?? 'LIVE', server: presetConn?.id ?? '', settings: {}, purpose: 'BOTH',
    external_account_id: '', holder_name: '', name: '', code: '', base_currency: 'USD', trading_permission: 'READ_ONLY',
    password: '', access_level: 'READ_ONLY', key: '', secret: '', username: '', _codeEdited: false, _nameEdited: false });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const providers = refs.providers.filter(p => p.status !== 'ARCHIVED');
  const provider = providers.find(p => p.id === f.provider_id);
  // Every technology is listed: those the provider offers first, then those usual for its type, then the rest.
  const offered = (id: string) => (provider?.technologies ?? []).includes(id);
  const rank = (t: Row) => (offered(t.id) ? 2 : 0) + (provider && t.kinds.includes(provider.kind) ? 1 : 0) + (t.platform ? 0.5 : 0);
  const ordered: Row[] = [...(cat?.technologies ?? [])].sort((a, b) => rank(b) - rank(a));
  const tech = (cat?.technologies ?? []).find((t: Row) => t.id === f.tech);
  const platform: string | undefined = tech?.platform;
  const apiPreset: Row | undefined = (cat?.api_presets ?? []).find((a: Row) => a.id === f.api);
  const dedicated = f.tech === 'MT5' ? 'MT5' : f.tech === 'API' && f.api === 'UPSTOX' ? 'UPSTOX' : '';
  const servers = refs.connections.filter(c => c.provider_id === f.provider_id && c.platform === platform && c.status !== 'ARCHIVED' && c.environment === f.environment
    && (f.tech !== 'API' || !apiPreset?.api_base_url || (c.settings?.api_base_url ?? '').startsWith(apiPreset.api_base_url)));
  const server = refs.connections.find(c => c.id === f.server);
  const settings = f.server === 'new' ? f.settings : server?.settings ?? {};
  const idLabel = f.tech === 'MT4' ? 'MT4 login (account number)' : f.tech === 'CTRADER' ? 'cTrader account number' : f.tech === 'FIX' ? 'Account (FIX tag 1) or session account'
    : apiPreset?.account_label ?? 'Account / client ID';
  function set(patch: Row) {
    setF(o => {
      const n = { ...o, ...patch };
      if ('provider_id' in patch) { n.server = ''; }
      if ('tech' in patch) { n.server = ''; n.api = ''; n.settings = platform ? {} : {}; }
      if ('api' in patch) { const ap = (cat?.api_presets ?? []).find((a: Row) => a.id === patch.api); n.server = '';
        n.settings = { ...DEFAULT_SETTINGS.REST_API(), api_base_url: ap?.api_base_url ?? '', auth_method: ap?.auth_method ?? 'API_KEY',
          oauth_redirect_url: ap?.auth_method === 'OAUTH2' ? `${window.location.origin}/api/oauth/${String(ap?.id ?? 'broker').toLowerCase()}/callback` : '' };
        if (['UPSTOX', 'ANGELONE', 'ZERODHA', 'DHAN', 'FYERS'].includes(patch.api)) n.base_currency = 'INR'; }
      if (patch.server === 'new') { const plat = (cat?.technologies ?? []).find((t: Row) => t.id === n.tech)?.platform;
        n.settings = n.tech === 'API' ? n.settings?.api_base_url !== undefined && n.settings.api_base_url !== '' ? n.settings : { ...DEFAULT_SETTINGS.REST_API(), ...(n.settings ?? {}) }
          : (DEFAULT_SETTINGS[plat] ?? (() => ({})))();
        if (n.tech === 'FIX') n.purpose = 'MARKET_DATA'; if (n.tech === 'CTRADER') n.settings.host = n.environment === 'DEMO' ? 'demo.ctraderapi.com' : 'live.ctraderapi.com'; }
      const p = providers.find(x => x.id === n.provider_id);
      if (!n._codeEdited && p && n.external_account_id) n.code = `${p.code}_${n.environment}_${idPart(n.external_account_id)}`.slice(0, 32).replace(/_+$/, '');
      if (!n._nameEdited && p && n.external_account_id) n.name = `${p.name} · ${PLATFORM_TAG[(cat?.technologies ?? []).find((t: Row) => t.id === n.tech)?.platform] ?? n.tech} ${n.environment === 'LIVE' ? 'live' : 'demo'} · ${n.external_account_id}`.slice(0, 120);
      return n;
    });
  }
  const newServerOk = f.server !== 'new' ? true : f.tech === 'MT4' ? !!(settings.server_name && settings.access_points?.length)
    : f.tech === 'FIX' ? !!(settings.host && settings.sender_comp_id && settings.target_comp_id) : f.tech === 'API' ? !!settings.api_base_url && (settings.auth_method !== 'OAUTH2' || !!settings.oauth_redirect_url) : true;
  const ready = !!(f.provider_id && platform && !dedicated && f.server && newServerOk && f.external_account_id && f.name && f.code && f.base_currency);
  async function save(e: FormEvent) {
    e.preventDefault(); if (!ready) return; setBusy(true); setError('');
    const notes: string[] = [];
    try {
      let connId = f.server;
      if (f.server === 'new') {
        const s = f.tech === 'MT4' ? { server_name: settings.server_name, access_points: settings.access_points.map((p: Row) => ({ name: settings.server_name, host: p.host, port: Number(p.port), enabled: true, source: 'MANUAL' })) }
          : f.tech === 'CTRADER' ? { host: settings.host, port: Number(settings.port || 5035), oauth_redirect_url: settings.oauth_redirect_url || null }
          : f.tech === 'FIX' ? { ...settings, port: Number(settings.port), heartbeat_seconds: Number(settings.heartbeat_seconds), md_depth: Number(settings.md_depth) }
          : { api_base_url: settings.api_base_url, auth_method: settings.auth_method, ...(settings.auth_method === 'OAUTH2' ? { oauth_redirect_url: settings.oauth_redirect_url } : {}) };
        const r = await api('config/venues', 'POST', { provider_id: f.provider_id, connection: { platform, environment: f.environment, purpose: f.purpose,
          adapter: f.tech === 'FIX' || f.tech === 'API' ? 'NATIVE' : 'DEFAULT', settings: s } });
        const pending = pendingMessage(r);
        if (pending) { created(null, `${pending} Add the account after it is approved.`); return; }
        connId = r.connection.id; notes.push(`server ${r.connection.code} created`);
      }
      const acct = await api('config/accounts', 'POST', { connection_id: connId, code: f.code, name: f.name, external_account_id: f.external_account_id,
        holder_name: f.holder_name, base_currency: f.base_currency, trading_permission: f.trading_permission });
      const pendingAcct = pendingMessage(acct);
      if (pendingAcct) { created(null, pendingAcct); return; }
      if (f.tech === 'MT4' && f.password) {
        await api(`config/accounts/${acct.id}/credential`, 'PUT', { credential: { kind: 'MT5_LOGIN', password: f.password, access_level: f.access_level } }); notes.push('password saved');
      }
      const connCredential = f.tech === 'CTRADER' && f.key ? { kind: 'OAUTH_CLIENT', client_id: f.key, client_secret: f.secret }
        : f.tech === 'FIX' && f.username ? { kind: 'FIX_LOGIN', username: f.username, password: f.password }
        : f.tech === 'API' && f.key ? (settings.auth_method === 'OAUTH2' ? { kind: 'OAUTH_CLIENT', client_id: f.key, client_secret: f.secret } : { kind: 'API_KEY', api_key: f.key, api_secret: f.secret }) : null;
      if (connCredential) { await api(`config/connections/${connId}/credential`, 'PUT', { credential: connCredential }); notes.push('server login saved'); }
      setF(o => ({ ...o, password: '', key: '', secret: '' }));
      created(acct, `Account ${acct.code} added${notes.length ? ` (${notes.join(', ')})` : ''}.${tech?.state === 'PLANNED' || (f.tech === 'API' && apiPreset?.state === 'PLANNED') ? ' Its connector is planned: it is recorded with its server and login, and collects once the connector is built.' : ''}`);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const dirty = !!(f.provider_id || f.tech || f.external_account_id);
  return <FloatingWindow storageKey="corei-os-mini.add-account.v1" width={820} title="Add account" subtitle="Provider › technology › account › server › login"
    onClose={() => { if (!dirty || window.confirm('Discard this account?')) close(); }}
    footer={<div className="cfg-actions">
      {dedicated ? <button className="primary" type="button" disabled={!f.provider_id} onClick={() => dedicated === 'MT5' ? openMt5({ provider_id: f.provider_id, environment: f.environment, settings: {} }) : openUpstox()}>
        Continue · {dedicated === 'MT5' ? 'MT5 login, server and addresses' : 'Upstox client ID and app'}</button>
        : <button className="primary" form="cfg-add-account" disabled={busy || !ready}>{busy ? 'Adding…' : 'Add account'}</button>}
      {!dedicated && !ready && <span className="cfg-note">Choose provider and technology, then fill in the account and its server.</span>}</div>}>
    <form className="cfg-form" id="cfg-add-account" onSubmit={save} autoComplete="off">
      <Section n={1} title="Provider">
        <div className="cfg-pair">
          <Field label="Provider" hint="Not listed? + New provider (the one provider form)"><select aria-label="Provider" required value={f.provider_id} onChange={e => set({ provider_id: e.target.value })}>
            <option value="" disabled>Select…</option>{providers.map(r => <option key={r.id} value={r.id}>{r.name} ({r.code})</option>)}</select></Field>
          <Field label=" "><button type="button" onClick={newProvider}>+ New provider…</button></Field>
        </div>
      </Section>
      <Section n={2} title="Technology">
        {!f.provider_id ? <p className="cfg-note">Choose the provider first.</p> : !cat ? <p className="cfg-note">Loading…</p> :
          <div className="cfg-techs" role="radiogroup" aria-label="Technology">{ordered.map((t: Row) =>
            <button key={t.id} type="button" role="radio" aria-checked={f.tech === t.id} disabled={!t.platform} className={`cfg-tech${f.tech === t.id ? ' on' : ''}`}
              onClick={() => set({ tech: t.id })} title={t.platform ? t.summary : `${t.summary} Not recordable yet${t.id === 'FEED' || t.id === 'EXCHANGE' ? ': use FIX 4.4 if the provider offers it' : ''}.`}>
              <b>{t.label}</b><small>{offered(t.id) ? `offered by ${provider?.name} · ` : ''}{t.platform ? STATE_TEXT[t.state] ?? t.state : 'planned'}</small></button>)}</div>}
        {f.tech === 'API' && <Field label="Which API"><Choice label="Which API" value={f.api} options={[['', 'Select…'], ...(cat?.api_presets ?? []).map((a: Row) =>
          [a.id, `${a.label}${a.state === 'BUILT' ? '' : ' (recorded now · data planned)'}`] as [string, string])]} onChange={v => set({ api: v })} /></Field>}
        {dedicated && <p className="cfg-ok">{dedicated === 'MT5' ? 'MT5 continues in its form: login, password, the server picked from the broker\'s list, addresses and connection method.'
          : 'Upstox continues in its form: client ID, app key and secret, then the daily broker login.'}</p>}
      </Section>
      {platform && !dedicated && (f.tech !== 'API' || f.api) && <>
        <Section n={3} title="Account">
          <div className="cfg-pair">
            <Field label={idLabel}><input aria-label="Account number or ID" required maxLength={64} value={f.external_account_id}
              {...(f.tech === 'MT4' ? { inputMode: 'numeric' as const, pattern: '[1-9][0-9]{0,19}' } : {})} onChange={e => set({ external_account_id: e.target.value.trim() })} /></Field>
            <Field label="Environment"><Choice label="Environment" value={f.environment} options={[['LIVE', 'Live'], ['DEMO', 'Demo']]} onChange={v => set({ environment: v, server: '' })} /></Field>
          </div>
          <div className="cfg-pair">
            <Field label="Account name"><input aria-label="Account name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value, _nameEdited: e.target.value !== '' })} /></Field>
            <Field label="Account ID" hint="Suggested; fixed after saving"><input aria-label="Account ID" required maxLength={32} pattern="[A-Za-z][A-Za-z0-9_]{1,31}" value={f.code}
              onChange={e => set({ code: e.target.value, _codeEdited: e.target.value !== '' })} /></Field>
          </div>
          <div className="cfg-pair">
            <Field label="Account holder"><input aria-label="Account holder" maxLength={120} value={f.holder_name} onChange={e => set({ holder_name: e.target.value })} /></Field>
            <Field label="Account currency"><input aria-label="Account currency" required maxLength={10} value={f.base_currency} onChange={e => set({ base_currency: e.target.value.toUpperCase() })} /></Field>
          </div>
          <Field label="Trading permission" hint="Orders also need order sending on"><Choice label="Trading permission" value={f.trading_permission}
            options={[['DISABLED', 'Disabled'], ['READ_ONLY', 'Read only'], ['TRADING', 'Trading']]} onChange={v => set({ trading_permission: v })} /></Field>
        </Section>
        <Section n={4} title="Server">
          <Field label={f.tech === 'FIX' ? 'FIX session' : f.tech === 'API' ? 'API endpoint' : `${tech.label} server`}>
            <Choice label="Server" value={f.server} options={[['', 'Select…'], ...servers.map(c => [c.id, `${c.settings?.server_name ?? c.settings?.host ?? c.settings?.api_base_url ?? c.code} · ${c.code}`] as [string, string]),
              ['new', `+ New ${f.tech === 'FIX' ? 'FIX session' : f.tech === 'API' ? 'API endpoint' : 'server'} of ${provider?.name ?? 'this provider'}`]]} onChange={v => set({ server: v })} /></Field>
          {f.server === 'new' && <>
            {f.tech === 'MT4' && <Mt4Server settings={settings} change={s2 => set({ settings: s2 })} />}
            {f.tech === 'CTRADER' && <CTraderFields settings={settings} change={s2 => set({ settings: s2 })} />}
            {f.tech === 'FIX' && <FixFields settings={settings} change={s2 => set({ settings: s2 })} />}
            {f.tech === 'API' && <div className="cfg-pair">
              <Field label="API base URL"><input aria-label="API base URL" required type="url" value={settings.api_base_url ?? ''} onChange={e => set({ settings: { ...settings, api_base_url: e.target.value } })} /></Field>
              <Field label="Login method"><Choice label="API login method" value={settings.auth_method ?? 'API_KEY'} options={[['API_KEY', 'API key and secret'], ['OAUTH2', 'OAuth (daily login)']]}
                onChange={v => set({ settings: { ...settings, auth_method: v } })} /></Field>
              {settings.auth_method === 'OAUTH2' && <Field label="Redirect URL" hint="Register this address with the broker app"><input aria-label="OAuth redirect URL" required type="url"
                value={settings.oauth_redirect_url ?? ''} onChange={e => set({ settings: { ...settings, oauth_redirect_url: e.target.value } })} /></Field>}
            </div>}
            <Field label="Role" hint="Data source: prices are taken from it. Execution: trading accounts"><Choice label="Role" value={f.purpose} options={ROLE_OPTIONS} onChange={v => set({ purpose: v })} /></Field>
          </>}
          {server && <p className="cfg-note cfg-reuse">Uses {server.code}{server.account_count ? ` (${server.account_count} account${server.account_count === 1 ? '' : 's'} already on it)` : ''}.</p>}
        </Section>
        <Section n={5} title="Login">
          {f.tech === 'MT4' && <div className="cfg-pair">
            <Field label="Password" hint="Stored encrypted; can be added later"><input aria-label="Password" type="password" autoComplete="new-password" maxLength={4096} value={f.password} onChange={e => set({ password: e.target.value })} /></Field>
            <Field label="Password type"><Choice label="Password type" value={f.access_level} options={[['READ_ONLY', 'Investor (read only)'], ['FULL', 'Master (can trade)']]} onChange={v => set({ access_level: v })} /></Field></div>}
          {(f.tech === 'CTRADER' || f.tech === 'API') && <div className="cfg-pair">
            <Field label={f.tech === 'CTRADER' || settings.auth_method === 'OAUTH2' ? 'App client ID' : 'API key'} hint="Saved encrypted on the server; leave empty if it is already saved there">
              <input aria-label="API key" autoComplete="off" value={f.key} onChange={e => set({ key: e.target.value })} /></Field>
            <Field label={f.tech === 'CTRADER' || settings.auth_method === 'OAUTH2' ? 'App client secret' : 'API secret'}><input aria-label="API secret" type="password" autoComplete="new-password" value={f.secret} onChange={e => set({ secret: e.target.value })} /></Field></div>}
          {f.tech === 'FIX' && <div className="cfg-pair">
            <Field label="Session username" hint="Only if the venue uses one (tag 553)"><input aria-label="FIX username" autoComplete="off" value={f.username} onChange={e => set({ username: e.target.value })} /></Field>
            <Field label="Session password" hint="Tag 554"><input aria-label="FIX password" type="password" autoComplete="new-password" value={f.password} onChange={e => set({ password: e.target.value })} /></Field></div>}
          {f.tech === 'API' && apiPreset?.daily_login && <p className="cfg-note">{apiPreset.label} issues a session each trading day: log in from the account after it is added. COREI never stores your PIN or TOTP seed.</p>}
          {(tech?.state === 'PLANNED' || (f.tech === 'API' && apiPreset?.state === 'PLANNED')) && <p className="cfg-note">The {f.tech === 'API' ? apiPreset?.label : tech?.label} connector is planned: the account, its server and login are recorded now and it collects once the connector is built.</p>}
        </Section>
      </>}
      {error && <p role="alert" className="cfg-error">{error}</p>}
    </form>
  </FloatingWindow>;
}

/* ---------- record list + detail ---------- */

/** A link to another record; the list handles the click (data-nav="section:id"). */
const Link = ({ to, id, children }: { to: string; id?: string; children: ReactNode }) =>
  id ? <button type="button" className="cfg-chip" data-nav={`${to}:${id}`} title="Open">{children}</button> : <>{children}</>;
const Pending = ({ r }: { r: Row }) => r.pending_changes > 0 ? <> <span className="cfg-pill warn" title="A change is waiting for a second person under Approvals">Awaiting approval</span></> : null;

const COLUMNS: Record<Entity, [string, (r: Row) => ReactNode][]> = {
  providers: [['Provider ID', r => r.code], ['Provider name', r => r.name], ['Legal entity', r => r.legal_entity || '—'], ['Regulator', r => r.regulator || '—'],
    ['Type', r => r.kind], ['Servers', r => r.connection_count], ['Status', r => <Status value={r.status} />]],
  groups: [['Group ID', r => r.code], ['Name', r => r.name], ['Accounts', r => r.account_count], ['Status', r => <Status value={r.status} />]],
  connections: [['Connection', r => <><strong>{r.code}</strong><small className="cfg-note"> {r.name}</small></>], ['Provider', r => <Link to="providers" id={r.provider_id}>{r.provider_code}</Link>],
    ['Technology', r => PLATFORM_TAG[r.platform] ?? r.platform], ['Role', r => PURPOSE_LABEL[r.purpose] ?? r.purpose], ['Env', r => <Env value={r.environment} />],
    ['Server', r => r.platform === 'FIX' ? `${r.settings.host}:${r.settings.port} · ${r.settings.sender_comp_id} → ${r.settings.target_comp_id}` : r.platform === 'CTRADER' ? `${r.settings.host}:${r.settings.port}` : r.platform === 'MT5' || r.platform === 'MT4' ? <>{r.settings.server_name}<small className="cfg-note"> · {r.settings.access_points.filter((p: Row) => p.enabled !== false).length} access points</small></> : r.settings.api_base_url],
    ['Clock', r => r.server_utc_offset_minutes == null ? <span className="cfg-pill warn">Not set</span> : `UTC${r.server_utc_offset_minutes >= 0 ? '+' : ''}${r.server_utc_offset_minutes / 60}${r.dst_rule && r.dst_rule !== 'NONE' && r.dst_rule !== 'UNKNOWN' ? ' ' + r.dst_rule + ' DST' : ''}`],
    ['Live', r => r.live_status?.state ? <span className={`cfg-pill ${r.live_status.state === 'CONNECTED' ? 'ok' : r.live_status.state === 'FAILED' ? 'warn' : 'off'}`}>{r.live_status.state}</span> : <span className="cfg-note">—</span>],
    ['Accounts', r => r.account_count],
    ['Status', r => <><Status value={r.status} />{r.platform !== 'MT5' && !r.credential_kind && <> <span className="cfg-pill warn">No API key</span></>}<Pending r={r} /></>]],
  accounts: [['Account ID', r => r.code], ['Holder', r => r.holder_name || '—'], ['Provider', r => <Link to="providers" id={r.provider_id}>{r.provider_code}</Link>],
    ['Connection', r => <Link to="connections" id={r.connection_id}>{r.connection_code}</Link>], ['Env', r => <Env value={r.environment} />],
    ['Broker login', r => r.external_account_id], ['Mode', r => r.margin_mode === 'UNKNOWN' ? <span className="cfg-pill warn" title="Margin mode not known">?</span> : String(r.margin_mode).toLowerCase()],
    ['Permission', r => <Status value={r.trading_permission} />],
    ['Data', r => <Ready blockers={r.data_blockers} />], ['Trading', r => <Ready blockers={r.trading_blockers} />],
    ['Status', r => <><Status value={r.status} />{r.warnings?.length > 0 && <> <span className="cfg-pill" title={r.warnings.map((w: string) => BLOCKERS[w] ?? w).join('\n')}>{r.warnings.length} warning{r.warnings.length > 1 ? 's' : ''}</span></>}<Pending r={r} /></>]],
  instruments: [['Symbol', r => <>{r.symbol}{r.aliases?.length ? <small className="cfg-note"> {r.aliases.join(', ')}</small> : null}</>], ['Name', r => r.name], ['Class', r => r.asset_class],
    ['Market', r => r.market ? `${r.market}${r.expiry ? ` · ${r.expiry}` : ''}${r.strike ? ` · ${r.strike} ${r.option_type}` : ''}` : '—'], ['Tick', r => r.tick_size],
    ['Contract min / step', r => `${r.min_quantity} / ${r.quantity_step}`], ['Contract', r => `${r.contract_size} ${r.contract_unit}`], ['Mappings', r => r.mapping_count], ['Status', r => <Status value={r.status} />]],
  mappings: [['Instrument', r => r.instrument_symbol], ['Provider', r => r.provider_code], ['Connection', r => <Link to="connections" id={r.connection_id}>{r.connection_code}</Link>], ['Env', r => <Env value={r.environment} />],
    ['Applies to', r => r.account_id ? <Link to="accounts" id={r.account_id}>{r.account_code}</Link> : r.account_group_id ? `Group ${r.group_code}` : 'All accounts'], ['Broker symbol', r => r.broker_symbol], ['Used for', r => r.usage], ['Contract override', r => r.broker_contract_size ?? '—'], ['Status', r => <Status value={r.status} />]],
};

const SORT_RAW: Record<string, (r: Row) => string | number> = {
  Status: r => r.status, Env: r => r.environment, Permission: r => r.trading_permission, Data: r => r.data_blockers?.length ?? 0,
  Trading: r => r.trading_blockers?.length ?? 0, Connection: r => r.connection_code ?? r.code, Provider: r => r.provider_code, Live: r => r.live_status?.state ?? '',
  Clock: r => r.server_utc_offset_minutes ?? -9999, Mode: r => r.margin_mode, 'Applies to': r => r.account_code ?? '',
};
/** Which field to focus when a readiness reason is clicked. */
const BLOCKER_FIELD: Record<string, string> = {
  MARGIN_MODE_UNKNOWN: 'Margin mode', MARGIN_MODE_NOT_CONFIRMED_BY_BROKER: 'Margin mode', MANDATE_MISSING: 'Mandate reference', MANDATE_EXPIRED: 'Mandate expires',
  MANDATE_EXPIRING: 'Mandate expires', ACCOUNT_EXPIRED: 'Account expires', ACCOUNT_EXPIRING: 'Account expires', ACCOUNT_PERMISSION_DISABLED: 'Trading permission',
  ACCOUNT_NOT_PERMITTED_TO_TRADE: 'Trading permission', ACCOUNT_INACTIVE: 'Status', ACCOUNT_CREDENTIAL_MISSING: 'Password', MASTER_PASSWORD_MISSING: 'Password',
  READ_ONLY_CREDENTIAL: 'Password',
};

function Records({ entity, refs, reloadRefs, canEdit, navigate, openId }: { entity: Entity; refs: Record<string, Row[]>; reloadRefs: () => void;
  canEdit: boolean; navigate: Navigate; openId?: string }) {
  const [rows, setRows] = useState<Row[] | null>(null), [q, setQ] = useState(''), [parent, setParent] = useState('');
  const [selected, setSelected] = useState<Row | null>(null), [draft, setDraft] = useState<Row | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [loadError, setLoadError] = useState('');
  const [mt5Preset, setMt5Preset] = useState<Row | null>(null);
  const [accountPreset, setAccountPreset] = useState<AccountPreset>({});
  const [blocked, setBlocked] = useState('');
  const [adding, setAdding] = useState<'' | 'ACCOUNT' | 'MT5' | 'UPSTOX'>(''), [importing, setImporting] = useState(false), [specsVersion, setSpecsVersion] = useState(0);
  const prefKey = `corei-os-mini.list.${entity}`;
  const [view, setView] = useState<Row>(() => { try { return JSON.parse(localStorage.getItem(prefKey) || '{}'); } catch { return {}; } });
  const setViewPref = (patch: Row) => { const next = { ...view, ...patch }; setView(next); try { localStorage.setItem(prefKey, JSON.stringify(next)); } catch { /* preference only */ } };
  const parentFilter = entity === 'connections' ? ['provider_id', refs.providers, (r: Row) => r.code] as const
    : entity === 'accounts' ? ['connection_id', refs.connections, (r: Row) => r.code] as const
    : entity === 'mappings' ? ['connection_id', refs.connections, (r: Row) => r.code] as const : null;
  async function load(keepId?: string) {
    try {
      const params = new URLSearchParams(); if (q) params.set('q', q); if (parentFilter && parent) params.set(parentFilter[0], parent);
      if (view.archived) params.set('include_archived', 'true');
      const data = await api(`config/${entity}?${params}`); setRows(data.items); setLoadError('');
      if (keepId) { const fresh = data.items.find((r: Row) => r.id === keepId); setSelected(fresh ?? null); if (fresh) setDraft({ ...fresh }); }
      return data.items as Row[];
    } catch (e) { setLoadError((e as Error).message); return []; }
  }
  const accountApiConnection = entity === 'accounts' && selected?.platform === 'REST_API'
    ? refs.connections?.find(connection => connection.id === selected.connection_id) : undefined;
  const shown = useViewVisible(); // shown again (a sub-tab brought back), the list reloads
  useEffect(() => { if (!shown) return; const t = setTimeout(() => void load(selected?.id), q ? 250 : 0); return () => clearTimeout(t); }, [entity, q, parent, view.archived, shown]);
  useEffect(() => { if (!openId) return;
    // From the Connect screen: 'new' opens a blank record, 'new:MT5' / 'new:UPSTOX' the one-step account forms.
    if (openId === 'new') { open(null); return; }
    if (openId === 'new:MT5' || openId === 'new:UPSTOX') { setAdding(openId.slice(4) as 'MT5' | 'UPSTOX'); return; }
    // From the Connectivity tree: an account on a chosen server ('new:MT5:<connection>', 'new:ACCOUNT:<connection>').
    if (openId.startsWith('new:MT5P:')) { setMt5Preset({ provider_id: openId.slice(9), environment: 'LIVE', settings: {} }); setAdding('MT5'); return; }
    if (openId.startsWith('new:MT5:')) { void (async () => { const id = openId.slice(8);
      setMt5Preset(refs.connections.find(c => c.id === id) ?? await api(`config/connections/${id}`).catch(() => null)); setAdding('MT5'); })(); return; }
    if (openId.startsWith('new:ACCOUNT:')) { open(null); setDraft(d => ({ ...(d ?? blank[entity]), connection_id: openId.slice(12) })); return; }
    // The one Add account form: 'add' or 'add:<provider>:<technology>:<server>' (each part optional).
    if (openId === 'add' || openId.startsWith('add:')) { const [, provider_id, technology, connection_id] = openId.split(':');
      setAccountPreset({ provider_id: provider_id || undefined, technology: technology || undefined, connection_id: connection_id || undefined }); setAdding('ACCOUNT'); return; }
    void (async () => {
    let row = rows?.find(r => r.id === openId);
    if (!row) row = (await load()).find(r => r.id === openId) ?? (await api(`config/${entity}/${openId}`).catch(() => null));
    if (row) { setSelected(row); setDraft({ ...row, settings: row.settings ? JSON.parse(JSON.stringify(row.settings)) : undefined }); }
  })(); }, [openId]);
  const dirty = useMemo(() => !!draft && JSON.stringify(toPayload(entity, draft)) !== JSON.stringify(selected ? toPayload(entity, selected) : toPayload(entity, blank[entity])), [draft, selected, entity]);
  useEffect(() => {
    const guard = (e: Event) => { if (dirty && !window.confirm('Discard unsaved configuration changes?')) e.preventDefault(); };
    window.addEventListener('corei-before-layout-change', guard); return () => window.removeEventListener('corei-before-layout-change', guard);
  }, [dirty]);
  function open(row: Row | null) {
    if (dirty && !window.confirm('Discard unsaved changes?')) return;
    setError(''); setNotice(''); setSelected(row);
    setDraft(row ? { ...row, settings: row.settings ? JSON.parse(JSON.stringify(row.settings)) : undefined }
      : { ...blank[entity], ...(parentFilter && parent ? { [parentFilter[0]]: parent } : {}) });
  }
  async function save(e: FormEvent) {
    e.preventDefault(); if (!draft) return; setBusy(true); setError('');
    try {
      const body = toPayload(entity, draft);
      // Changing an identifying field (code, symbol, server, scope…) asks for a reason; it is kept in the audit log.
      const moved = selected ? IDENTITY[entity].filter(k => k in body && String(body[k] ?? '') !== String(selected[k] ?? '')) : [];
      let reason = '';
      if (moved.length) {
        const r = window.prompt(`You are changing ${moved.map(k => k.replace(/_id$/, '').replace(/_/g, ' ')).join(', ')}. Reason (kept in the audit log):`);
        if (r === null) { setBusy(false); return; }
        if (r.trim().length < 5) { setError('Give a reason of at least 5 characters.'); setBusy(false); return; }
        reason = r.trim();
      }
      const saved = selected ? await api(`config/${entity}/${selected.id}`, 'PUT', { ...body, version: selected.version, ...(reason ? { reason } : {}) }) : await api(`config/${entity}`, 'POST', body);
      const pending = pendingMessage(saved);
      if (pending) { setNotice(pending); if (selected) { setDraft({ ...selected }); await load(selected.id); } else { setSelected(null); setDraft(null); await load(); } reloadRefs(); return; }
      setNotice(selected ? 'Changes saved.' : `${SINGULAR[entity][0].toUpperCase() + SINGULAR[entity].slice(1)} created.`);
      setSelected(saved); setDraft({ ...saved }); await load(saved.id); reloadRefs();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function archive() {
    if (!selected) return;
    const reason = window.prompt(`Archive ${selected.code}? It stays on record (read-only) and leaves the lists. Reason:`);
    if (!reason || reason.trim().length < 5) { if (reason !== null) setError('Give a reason of at least 5 characters.'); return; }
    setBusy(true); setError('');
    try { const done = await api(`config/${entity}/${selected.id}/archive`, 'POST', { version: selected.version, reason: reason.trim() });
      setSelected(null); setDraft(null); setNotice(`${done.code} archived.`); await load(); reloadRefs(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!selected || !window.confirm(`Delete this ${SINGULAR[entity]}? This cannot be undone. Records that depend on it must be removed first.`)) return;
    setBusy(true); setError('');
    setBlocked('');
    try { await api(`config/${entity}/${selected.id}?version=${selected.version}`, 'DELETE'); setSelected(null); setDraft(null); setNotice('Deleted.'); await load(); reloadRefs(); }
    catch (err) {
      const message = (err as Error).message;
      // Has history: say why, and offer both ways on — delete permanently with everything attached, or archive.
      if (PURGEABLE.includes(entity) && (err as any).status === 409) setBlocked(message); else setError(message);
    } finally { setBusy(false); }
  }
  async function purge() {
    if (!selected) return;
    const what = entity === 'accounts' ? 'its collected prices and bars, collection plans, symbol catalogue, broker reads, deployments and login'
      : entity === 'connections' ? 'its accounts (with their collected data, plans and logins), symbol mappings, latency checks and login'
      : 'its servers and their accounts, with all their collected data, plans and logins';
    const typed = window.prompt(`Delete ${selected.code} permanently, with ${what}?\nThis cannot be undone; the audit log keeps one entry of what was removed.\n\nType ${selected.code} to confirm:`);
    if (typed === null) return;
    if (typed.trim() !== selected.code) { setError(`Not deleted: type ${selected.code} exactly.`); return; }
    const reason = window.prompt('Reason (kept in the audit log, at least 5 characters):');
    if (!reason || reason.trim().length < 5) { if (reason !== null) setError('Give a reason of at least 5 characters.'); return; }
    setBusy(true); setError(''); setBlocked('');
    try { const r = await api(`config/${entity}/${selected.id}/delete-permanently`, 'POST', { version: selected.version, confirm: typed.trim(), reason: reason.trim() });
      const removed = Object.entries(r.removed as Record<string, number>).map(([k, n]) => `${n.toLocaleString()} ${k}`).join(', ');
      setSelected(null); setDraft(null); setNotice(`${r.code} deleted permanently${removed ? ` (${removed})` : ''}.`); await load(); reloadRefs(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  function focusField(code: string) {
    const label = BLOCKER_FIELD[code]; if (!label) return;
    const target = [...document.querySelectorAll<HTMLElement>('.cfg-detail [aria-label]')].find(el => el.getAttribute('aria-label')?.startsWith(label));
    target?.scrollIntoView({ block: 'center' }); target?.focus();
  }
  const columns = COLUMNS[entity];
  const sorted = useMemo(() => {
    let list = rows ?? [];
    if (entity === 'accounts' && view.env) list = list.filter(r => r.environment === view.env);
    if (entity === 'accounts' && view.notReady) list = list.filter(r => r.trading_blockers?.length);
    if (!view.sort) return list;
    const column = columns.find(([h]) => h === view.sort); if (!column) return list;
    const key = (r: Row) => { const v = column[1](r); return typeof v === 'string' || typeof v === 'number' ? v : SORT_RAW[column[0]]?.(r) ?? ''; };
    return [...list].sort((x, y) => { const a = key(x), b = key(y); const c = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b), undefined, { numeric: true });
      return view.desc ? -c : c; });
  }, [rows, view, entity]);
  const exportable = ['providers', 'connections', 'accounts'].includes(entity);
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar">
        <input aria-label="Search" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
        {parentFilter && <select aria-label="Filter" value={parent} onChange={e => setParent(e.target.value)}>
          <option value="">All {parentFilter[0] === 'provider_id' ? 'providers' : 'connections'}</option>
          {parentFilter[1].map(r => <option key={r.id} value={r.id}>{parentFilter[2](r)}</option>)}</select>}
        {entity === 'accounts' && <><select aria-label="Environment filter" value={view.env ?? ''} onChange={e => setViewPref({ env: e.target.value })}>
          <option value="">LIVE and DEMO</option><option value="LIVE">LIVE only</option><option value="DEMO">DEMO only</option></select>
          <label className="cfg-check"><input type="checkbox" checked={!!view.notReady} onChange={e => setViewPref({ notReady: e.target.checked })} /> Not ready to trade</label></>}
        {ARCHIVABLE.includes(entity) && <label className="cfg-check"><input type="checkbox" checked={!!view.archived} onChange={e => setViewPref({ archived: e.target.checked })} /> Show archived</label>}
        <span className="cfg-toolbar-gap" />
        {exportable && <a className="cfg-button" href={`/api/config/export/${entity}.csv`} download>Export CSV</a>}
        {canEdit && entity === 'accounts' && <button onClick={() => setImporting(true)}>Import CSV</button>}
        {canEdit && entity === 'accounts' && <button className="primary" onClick={() => { setAccountPreset({}); setAdding('ACCOUNT'); }}
          title="Provider › technology (MT5, MT4, cTrader, FIX, API …) › account › server › login">Add account</button>}
        {canEdit && entity === 'connections' && <span className="cfg-note">A server is added with its account (Accounts › Add account): only servers in use are kept. Open one here to change its addresses, clock, limits or login.</span>}
        {canEdit ? <button className={entity === 'accounts' || entity === 'connections' ? '' : 'primary'} onClick={() => open(null)}>New {SINGULAR[entity]}{entity === 'accounts' || entity === 'connections' ? ' (advanced)' : ''}</button> : <span className="cfg-note">Read only · needs Trading operations to change</span>}
      </div>
      {loadError && <p role="alert" className="cfg-error">{loadError}</p>}
      {!draft && notice && <p role="status" className="cfg-ok">{notice}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No {entity === 'mappings' ? 'symbol mappings' : entity === 'groups' ? 'account groups' : entity} yet. {canEdit ? `Choose New ${SINGULAR[entity]} to add one.` : ''}</p> :
        <div className="cfg-scroll"><table className="cfg-table" onClick={e => { const link = (e.target as HTMLElement).closest<HTMLElement>('[data-nav]');
          if (link) { e.stopPropagation(); const [to, id] = link.dataset.nav!.split(':'); navigate(to, id); } }}>
          <thead><tr>{columns.map(([h]) => <th key={h} aria-sort={view.sort === h ? (view.desc ? 'descending' : 'ascending') : undefined}>
            <button type="button" className="cfg-sort" onClick={() => setViewPref({ sort: h, desc: view.sort === h ? !view.desc : false })}>{h}{view.sort === h ? (view.desc ? ' ▼' : ' ▲') : ''}</button></th>)}</tr></thead>
          <tbody>
          {sorted.map(r => <tr key={r.id} className={`${selected?.id === r.id ? 'selected' : ''}${r.status === 'ARCHIVED' ? ' cfg-off' : ''}`} tabIndex={0}
            onClick={e => { if (!(e.target as HTMLElement).closest('[data-nav]')) open(r); }} onKeyDown={e => { if (e.key === 'Enter') open(r); }}>
            {columns.map(([h, cell]) => <td key={h}>{cell(r)}</td>)}</tr>)}
          {!sorted.length && <tr><td colSpan={columns.length} className="cfg-note">Nothing matches the filters.</td></tr>}
        </tbody></table></div>}
    </div>
    {importing && <ImportAccounts close={() => setImporting(false)} done={() => { setImporting(false); void load(); reloadRefs(); }} />}
    {adding === 'ACCOUNT' && <AddAccount refs={refs} preset={accountPreset} close={() => setAdding('')} newProvider={() => { setAdding(''); navigate('providers', 'new'); }}
      openMt5={p => { setMt5Preset(p); setAdding('MT5'); }} openUpstox={() => setAdding('UPSTOX')} created={(account, note) => {
        setAdding(''); reloadRefs();
        if (!account) { setNotice(note); void load(); return; }
        void load(account.id).then(() => { setSelected(account); setDraft({ ...account }); setNotice(note); }); }} />}
    {adding === 'UPSTOX' && <AddUpstoxAccount close={() => setAdding('')} created={(account, note) => {
      setAdding(''); reloadRefs();
      void load(account.id).then(() => { setSelected(account); setDraft({ ...account }); setNotice(note); }); }} />}
    {adding === 'MT5' && <AddMt5Account refs={refs} preset={mt5Preset} close={() => { setAdding(''); setMt5Preset(null); }} created={(account, note) => {
      setAdding(''); setMt5Preset(null); reloadRefs();
      if (!account) { setNotice(note); void load(); return; }
      void load(account.id).then(() => { setSelected(account); setDraft({ ...account }); setNotice(note); }); }} />}
    {draft && <FloatingWindow storageKey={entity === 'connections' ? 'corei-os-mini.connection-window.v1' : entity === 'accounts' ? 'corei-os-mini.account-window.v1' : 'corei-os-mini.config-window.v1'}
      width={entity === 'connections' || entity === 'accounts' || entity === 'providers' ? 780 : 560}
      footer={canEdit ? <div className="cfg-footer-stack"><div className="cfg-actions">
          <button className="primary" form="cfg-record-form" disabled={busy || !dirty}>{selected ? 'Save changes' : `Create ${SINGULAR[entity]}`}</button>
          {selected && ARCHIVABLE.includes(entity) && selected.status !== 'ARCHIVED' && <button type="button" disabled={busy} onClick={() => void archive()}>Archive</button>}
          {selected && <button type="button" className="danger" disabled={busy} onClick={() => void remove()}>Delete</button>}
          {selected && PURGEABLE.includes(entity) && <button type="button" className="danger" disabled={busy} onClick={() => void purge()}
            title="Removes it and everything attached to it (collected data, plans, logins; for a server or provider its accounts too). Typed confirmation; audited.">Delete permanently…</button>}
          {selected && PURGEABLE.includes(entity) && <Info label="Archive, Delete or Delete permanently">
            <b>Archive</b>: stop using it — hidden from lists, collection stops, kept with its history; restore any time (Show archived › set Inactive).<br />
            <b>Delete</b>: remove only this record, and only when nothing is attached; otherwise it says what is.<br />
            <b>Delete permanently</b>: remove it with everything attached ({entity === 'providers' ? 'its servers, accounts, collected data and logins' : entity === 'connections' ? 'its accounts, collected data and logins' : 'its collected data, plans and login'}); type its code to confirm; cannot be undone.</Info>}</div>

        {blocked && <p role="alert" className="cfg-error">{blocked} <button type="button" className="danger" onClick={() => void purge()}>Delete permanently…</button>{' '}
          <button type="button" onClick={() => { setBlocked(''); void archive(); }}>Archive…</button></p>}</div>
        : <p className="cfg-note">Read only. Changes need the Trading operations role.</p>}
      title={selected ? `${SINGULAR[entity][0].toUpperCase()}${SINGULAR[entity].slice(1)} · ${selected.code ?? selected.symbol ?? selected.broker_symbol}` : `New ${SINGULAR[entity]}`}
      subtitle={selected ? `Version ${selected.version} · updated ${new Date(selected.updated_at).toLocaleString()}${dirty ? ' · unsaved changes' : ''}` : dirty ? 'Unsaved' : undefined}
      onClose={() => { if (!dirty || window.confirm('Discard unsaved changes?')) { setSelected(null); setDraft(null); } }}>
      <div className="cfg-detail">
      {selected && (entity === 'accounts' || entity === 'connections') && <p className="cfg-crumbs">
        <button type="button" className="cfg-chip" onClick={() => navigate('providers', selected.provider_id)}>{selected.provider_code}</button>
        {entity === 'accounts' && <> › <strong>{selected.code}</strong> › server <button type="button" className="cfg-chip" onClick={() => navigate('connections', selected.connection_id)}>{selected.connection_code}</button></>}
        {entity === 'connections' && <> › {selected.account_count > 0 ? <button type="button" className="cfg-link" onClick={() => navigate('accounts', undefined)}>{selected.account_count} account(s)</button> : 'no account yet'} › server <strong>{selected.code}</strong></>}</p>}
      {selected && entity === 'providers' && canEdit && <p className="cfg-crumbs">Next · step 2:{' '}
        <button type="button" className="cfg-chip" onClick={() => navigate('accounts', 'add:' + selected.id)}>Add account at {selected.name}</button>
        {' '}(choose its technology{(selected.technologies ?? []).length ? `: ${(selected.technologies as string[]).join(', ')} offered` : ''}) · <button type="button" className="cfg-link" onClick={() => navigate('brokers', 'p:' + selected.id)}>show in the tree</button></p>}
      {selected?.pending_changes > 0 && <p className="cfg-note cfg-reuse">A change to this {SINGULAR[entity]} is waiting for approval. <button type="button" className="cfg-link" onClick={() => navigate('approvals')}>Open Approvals</button></p>}
      {entity === 'accounts' && selected && <div className="cfg-blockers rd">
        {(['data_blockers', 'trading_blockers', 'warnings'] as const).map(k => k === 'trading_blockers' && selected.trading_permission !== 'TRADING'
          ? <div key={k} className="rd-group"><span className="rd-head">Order sending<span className="rd-state off">not used</span></span>
              <span className="rd-same">this account is {selected.trading_permission === 'READ_ONLY' ? 'read only' : 'disabled'}: market data and account reads only</span></div>
          : <div key={k} className="rd-group"><span className="rd-head">{k === 'data_blockers' ? 'Market data' : k === 'trading_blockers' ? 'Order sending' : 'Warnings'}
            <span className={`rd-state ${selected[k]?.length ? (k === 'warnings' ? 'warn' : 'bad') : 'ok'}`}>{selected[k]?.length ? `${selected[k].length} ${k === 'warnings' ? 'warning' : 'blocker'}${selected[k].length > 1 ? 's' : ''}` : k === 'warnings' ? 'none' : 'ready'}</span></span>
          {k === 'trading_blockers' && (selected.data_blockers ?? []).some((b: string) => selected[k]?.includes(b)) && <span className="rd-same">includes the market-data blockers above</span>}
          {selected[k]?.length > 0 && <ul>{selected[k].filter((b: string) => k !== 'trading_blockers' || !(selected.data_blockers ?? []).includes(b)).map((b: string) => <li key={b}>{BLOCKER_FIELD[b]
            ? <button type="button" className="cfg-link" onClick={() => focusField(b)}>{BLOCKERS[b] ?? b}</button> : BLOCKERS[b] ?? b}
            {b === 'BROKER_LOGIN_NEEDED_TODAY' && <> <button type="button" className="primary rd-act" onClick={() => void brokerLogin(selected.id)}>Log in at Upstox now</button>
              <small className="cfg-note"> One login a day; it lasts until 03:30 IST.</small>
              {selected.last_login_error && <div className="rd-reason"><b>Last login refused{selected.last_login_error_at ? ' ' + new Date(selected.last_login_error_at).toLocaleString() : ''}.</b> {selected.last_login_error}
                <ApiCheck connectionId={selected.connection_id} /></div>}</>}
            {b === 'CONNECTION_FAILED' && selected.connection_problem && <div className="rd-reason"><b>{selected.connection_problem.title}.</b> {selected.connection_problem.detail}
              <code>{selected.connection_problem.raw}</code></div>}</li>)}</ul>}</div>)}
      </div>}
      <form className="cfg-form" id="cfg-record-form" onSubmit={save}>
        <fieldset className="cfg-plain" disabled={!canEdit || (selected?.status === 'ARCHIVED' && draft.status === 'ARCHIVED')}>
          <RecordForm key={selected?.id ?? 'new'} entity={entity} draft={draft} setDraft={setDraft} editing={!!selected} refs={refs} />
        </fieldset>
        {selected?.status === 'ARCHIVED' && <p className="cfg-note">Archived. Set status to Inactive and save to restore it.</p>}
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
      </form>
      {canEdit && accountApiConnection && <CredentialPanel key={accountApiConnection.id} owner="connections" row={accountApiConnection} reload={reloadRefs} />}
      {canEdit && selected && entity === 'accounts' && (selected.platform === 'MT5' || selected.platform === 'MT4') && <CredentialPanel owner="accounts" row={selected} reload={() => void load(selected.id)} />}
      {canEdit && selected && entity === 'connections' && (selected.platform === 'REST_API' || selected.platform === 'FIX' || selected.platform === 'CTRADER') && <CredentialPanel owner="connections" row={selected} reload={() => void load(selected.id)} />}
      {selected && entity === 'connections' && selected.platform === 'MT5' && selected.adapter === 'TERMINAL_PUSH' && <PushPanel conn={selected} canEdit={canEdit} reload={() => void load(selected.id)} />}
      {selected && entity === 'accounts' && selected.platform === 'REST_API' && selected.auth_method === 'OAUTH2' && <BrokerLogin account={selected} canEdit={canEdit} reload={() => void load(selected.id)} />}
      {selected && entity === 'accounts' && selected.platform === 'MT5' && <AccountRoutes account={selected} canEdit={canEdit} onChange={() => void load(selected.id)} />}
      {selected && entity === 'accounts' && (selected.platform === 'MT5' || selected.platform === 'FIX') && <BrokerRead account={selected} canEdit={canEdit} platform={selected.platform} onRead={() => { setSpecsVersion(v => v + 1); void load(selected.id); }} />}
      {selected && entity === 'accounts' && <SymbolSpecs account={selected} canEdit={canEdit} refresh={specsVersion} />}
      {selected && (entity === 'accounts' || entity === 'connections') && <CredentialUse owner={entity} id={selected.id} />}
      </div>
    </FloatingWindow>}
  </div>;
}

/* ---------- order sending and audit ---------- */

function OrderSending({ changed, roles }: { changed: () => void; roles: string[] }) {
  const canOn = roles.includes('RISK_APPROVER'), canOff = canOn || roles.includes('TRADING_OPS');
  const [items, setItems] = useState<Row[] | null>(null), [edit, setEdit] = useState<string | null>(null);
  const [reason, setReason] = useState(''), [confirm, setConfirm] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const load = async () => { try { setItems((await api('config/execution-controls')).items); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  async function change(row: Row) {
    setBusy(true); setError('');
    try {
      await api(`config/execution-controls/${row.environment}`, 'PUT', { trading_enabled: !row.trading_enabled, reason, version: row.version,
        confirm_environment: row.trading_enabled ? null : confirm || null });
      setEdit(null); setReason(''); setConfirm(''); await load(); changed();
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="cfg-controls">
    <p>Order sending is a firm-wide switch for each environment. When it is off, no order is sent to any account in that environment, whatever the account's own permission. Every change is recorded in the audit log.</p>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="cfg-cards">{items?.map(row => <article key={row.environment} className={`cfg-card ${row.environment === 'LIVE' ? 'live' : ''} ${row.trading_enabled ? 'on' : ''}`}>
      <header><Env value={row.environment} /><strong>{row.trading_enabled ? 'Order sending ON' : 'Order sending OFF'}</strong></header>
      <p>{row.reason}</p><small>Changed by {row.updated_by} · {new Date(row.updated_at).toLocaleString()}</small>
      {edit === row.environment ? <form onSubmit={e => { e.preventDefault(); void change(row); }}>
        <Field label="Reason (recorded in audit log)"><input aria-label="Reason" required minLength={5} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></Field>
        {!row.trading_enabled && <Field label={`Type ${row.environment} to confirm`}><input aria-label="Confirm environment" required value={confirm} onChange={e => setConfirm(e.target.value)} /></Field>}
        <div className="cfg-actions"><button className={row.trading_enabled ? 'primary' : 'danger'} disabled={busy || (!row.trading_enabled && confirm !== row.environment)}>
          {row.trading_enabled ? 'Turn order sending off' : `Turn ${row.environment} order sending on`}</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
      </form> : (row.trading_enabled ? canOff : canOn) ? <button onClick={() => { setEdit(row.environment); setReason(''); setConfirm(''); setError(''); }}>{row.trading_enabled ? 'Turn off…' : 'Turn on…'}</button>
        : <small>{row.trading_enabled ? 'Turning off needs Risk approver or Trading operations.' : 'Turning on needs the Risk approver role.'}</small>}
    </article>)}</div>
  </div>;
}

function AuditLog() {
  const [items, setItems] = useState<Row[] | null>(null), [entity, setEntity] = useState(''), [error, setError] = useState(''), [open, setOpen] = useState<number | null>(null);
  useEffect(() => { void (async () => { try { setItems((await api(`config/audit?limit=500${entity ? '&entity=' + encodeURIComponent(entity) : ''}`)).items); } catch (e) { setError((e as Error).message); } })(); }, [entity]);
  return <div className="cfg-list">
    <div className="cfg-toolbar"><select aria-label="Audit entity" value={entity} onChange={e => setEntity(e.target.value)}>
      <option value="">All records</option>{['provider', 'connection', 'account', 'instrument', 'symbol mapping', 'execution control', 'user'].map(x => <option key={x}>{x}</option>)}</select></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {!items ? <p className="cfg-note">Loading…</p> : items.length === 0 ? <p className="cfg-empty">No changes recorded yet.</p> :
      <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Record</th><th>Reason</th></tr></thead><tbody>
        {items.map(a => <Fragment key={a.id}><tr tabIndex={0} onClick={() => setOpen(open === a.id ? null : a.id)}><td>{new Date(a.occurred_at).toLocaleString()}</td><td>{a.actor}</td>
          <td>{a.action}</td><td>{a.entity} · {(a.after_state ?? a.before_state)?.code ?? (a.after_state ?? a.before_state)?.username ?? (a.after_state ?? a.before_state)?.symbol ?? (a.after_state ?? a.before_state)?.broker_symbol ?? a.entity_id}</td><td>{a.reason}</td></tr>
          {open === a.id && <tr className="cfg-audit-detail"><td colSpan={5}><div className="cfg-pair"><pre>Before{'\n'}{JSON.stringify(a.before_state, null, 2)}</pre><pre>After{'\n'}{JSON.stringify(a.after_state, null, 2)}</pre></div></td></tr>}</Fragment>)}
      </tbody></table></div>}
  </div>;
}

/* ---------- strategies ---------- */

/* A GRAPH strategy: choose an approved strategy-rule calculation graph and its version; its parameters come from the graph. */
function GraphRulePicker({ value, change, disabled }: { value: Row; change: (v: Row) => void; disabled?: boolean }) {
  const [lib, setLib] = useState<Row[]>([]), [ver, setVer] = useState<Row | null>(null);
  useEffect(() => { void api('calc/graphs').then(d => setLib((d.items ?? []).filter((g: Row) => g.purpose === 'STRATEGY_RULE'))).catch(() => undefined); }, []);
  useEffect(() => { if (value.graph_code && value.graph_version) void api(`calc/graphs/${value.graph_code}/versions/${value.graph_version}`).then(setVer).catch(() => setVer(null)); else setVer(null); }, [value.graph_code, value.graph_version]);
  const g = lib.find(x => x.code === value.graph_code);
  const approved: Row[] = (g?.versions ?? []).filter((v: Row) => v.status === 'APPROVED');
  const gp: Row = value.graph_parameters ?? {};
  return <>
    <Field label="Calculation graph" hint="An approved strategy-rule graph (Strategies › Calculation graphs); the version is pinned in this strategy version">
      <select aria-label="Strategy graph" required disabled={disabled} value={value.graph_code ?? ''} onChange={e => { const n = lib.find(x => x.code === e.target.value);
        const last = (n?.versions ?? []).filter((v: Row) => v.status === 'APPROVED').pop(); change({ ...value, graph_code: e.target.value, graph_version: last?.version ?? '', graph_parameters: {} }); }}>
        <option value="">Choose…</option>{lib.map(x => <option key={x.code} value={x.code}>{x.name} · {x.code}</option>)}</select></Field>
    {g && <Field label="Approved rule revision" hint={approved.length ? 'The strategy saves this exact rule revision.' : 'Approve a revision in Calculation graphs before continuing.'}><select aria-label="Strategy graph version" required disabled={disabled} value={value.graph_version ?? ''} onChange={e => change({ ...value, graph_version: Number(e.target.value) })}>
      <option value="">Choose…</option>{approved.map((v: Row) => <option key={v.version} value={v.version}>v{v.version} · approved by {v.approved_by}</option>)}</select></Field>}
    {(ver?.definition?.parameters ?? []).map((p: Row) => <Field key={p.name} label={p.name} hint={`${p.doc ?? ''} (default ${p.default})`}>
      <input aria-label={`Graph parameter ${p.name}`} type="number" step="any" disabled={disabled} placeholder={String(p.default)} value={gp[p.name] ?? ''}
        onChange={e => { const n = { ...gp }; if (e.target.value === '') delete n[p.name]; else n[p.name] = Number(e.target.value); change({ ...value, graph_parameters: n }); }} /></Field>)}
    {ver && <p className="cfg-note">{ver.definition?.notes}</p>}
  </>;
}

function ParameterFields({ schema, value, change, disabled }: { schema: Row; value: Row; change: (v: Row) => void; disabled?: boolean }) {
  const all: Row = schema?.properties ?? {}, blend = 'components' in all, graph = 'graph_code' in all;
  const shown = Object.entries<Row>(all).filter(([k]) => !(blend && ['components', 'entry', 'exit'].includes(k))
    && !(graph && ['graph_code', 'graph_version', 'graph_parameters', 'graph_hash', 'definition'].includes(k)));
  return <>{shown.map(([key, prop]) => {
    const label = detailLabel(key);
    return <Field key={key} label={label} hint={prop.description}>{prop.enum
      ? <Choice label={label} value={value[key] ?? ''} disabled={disabled} options={['', ...prop.enum].map(v => [v, v || 'Select…'] as [string, string])} onChange={v => change({ ...value, [key]: v })} />
      : <input aria-label={label} required disabled={disabled} type={prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'} min={prop.minimum} max={prop.maximum}
          value={value[key] ?? ''} onChange={e => change({ ...value, [key]: prop.type === 'integer' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value })} />}</Field>;
  })}{blend && !disabled && <BlendEditor value={value} change={change} />}{graph && <GraphRulePicker value={value} change={change} disabled={disabled} />}</>;
}

function paramSummary(p: Row) { return Object.entries(p ?? {}).filter(([k]) => !['definition', 'graph_hash'].includes(k))
  .map(([k, v]) => `${detailLabel(k)} ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · '); }

function Strategies({ roles, reloadRefs }: { roles: string[]; reloadRefs: () => void }) {
  const canEdit = roles.includes('TRADING_OPS'), canApprove = roles.includes('RISK_APPROVER');
  const [rows, setRows] = useState<Row[] | null>(null), [algos, setAlgos] = useState<Row[]>([]), [q, setQ] = useState('');
  const [draft, setDraft] = useState<Row | null>(null), [versions, setVersions] = useState<Row[]>([]), [form, setForm] = useState<Row | null>(null);
  const [rule, setRule] = useState<StrategyRule | null>(null);
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null);
  const visible = useViewVisible();
  useEffect(() => {
    if (!visible || !canEdit) return;
    const receive = () => {
      const incoming = takeStrategyRule();
      if (incoming) setRule(incoming);
    };
    receive();
    window.addEventListener('corei-strategy-rule', receive);
    return () => window.removeEventListener('corei-strategy-rule', receive);
  }, [visible, canEdit]);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const load = async (keep?: string) => {
    try {
      const items = (await api(`config/strategies${q ? '?q=' + encodeURIComponent(q) : ''}`)).items; setRows(items);
      if (keep) { const r = items.find((x: Row) => x.id === keep); if (r) { setDraft({ ...r }); setVersions((await api(`config/strategies/${keep}/versions`)).items); } }
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { void load(); }, [q]);
  useEffect(() => { void (async () => { try { setAlgos((await api('config/algorithms')).items); } catch { /* shown on use */ } })(); }, []);
  async function run(task: () => Promise<any>, message: string, keep?: string) {
    setBusy(true); setError(''); setNotice('');
    try { const result = await task(); setNotice(message); await load(keep ?? result?.id ?? result?.strategy_id); reloadRefs(); return result; }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function open(row: Row | null) {
    setError(''); setNotice(''); setForm(null);
    setDraft(row ? { ...row } : { code: '', name: rule?.name ?? '', description: '', status: 'ACTIVE' });
    setVersions([]); if (row) void (async () => setVersions((await api(`config/strategies/${row.id}/versions`)).items))();
  }
  function newVersion() {
    setForm(rule ? { algorithm: 'GRAPH', parameters: { graph_code: rule.code, graph_version: rule.version } }
      : { algorithm: algos[0]?.id ?? '', parameters: {} });
  }
  async function saveStrategy(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    const { id, code, name, description, status, version } = draft;
    const saved = await run(() => id
      ? api(`config/strategies/${id}`, 'PUT', { code, name, description, status, version })
      : api('config/strategies', 'POST', { code, name, description }),
    id ? 'Strategy saved.' : rule ? 'Strategy created. Review its first version below.' : 'Strategy created. Add a version with its rules next.');
    if (saved?.id && !id && rule) {
      setDraft(saved);
      newVersion();
    }
  }
  const algo = algos.find(a => a.id === form?.algorithm);
  async function saveVersion(e: FormEvent) {
    e.preventDefault(); if (!form || !draft) return;
    const body = { algorithm: form.algorithm, parameters: form.parameters, notes: form.notes ?? '' };
    const saved = await run(() => form.id ? api(`config/strategy-versions/${form.id}`, 'PUT', { ...body, version: form.version })
      : api(`config/strategies/${draft.id}/versions`, 'POST', body), form.id ? 'Draft version saved.' : 'Draft version created. A risk approver must approve it before it can trade.', draft.id);
    if (saved) { setForm(null); setRule(null); }
  }
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar"><input aria-label="Search" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
        {canEdit ? <button className="primary" onClick={() => open(null)}>New strategy</button> : <span className="cfg-note">Read only</span>}</div>
      {rule && <p className="cfg-note"><strong>{rule.name} · v{rule.version}</strong> is ready to use. Choose New strategy or open an existing strategy and select New version.
        {' '}<button className="link" onClick={() => setRule(null)}>Clear selection</button></p>}
      {error && !draft && <p role="alert" className="cfg-error">{error}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No strategies yet.</p> :
        <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Code</th><th>Name</th><th>Versions</th><th>Approved version</th><th>Active deployments</th><th>Status</th></tr></thead><tbody>
          {rows.map(r => <tr key={r.id} tabIndex={0} className={draft?.id === r.id ? 'selected' : ''} onClick={() => open(r)} onKeyDown={e => { if (e.key === 'Enter') open(r); }}>
            <td>{r.code}</td><td>{r.name}</td><td>{r.version_count}</td><td>{r.latest_approved ? `v${r.latest_approved}` : '—'}</td><td>{r.active_deployments}</td><td><Status value={r.status} /></td></tr>)}
        </tbody></table></div>}
    </div>
    {draft && <FloatingWindow storageKey="corei-os-mini.config-window.v1" title={draft.id ? `Strategy · ${draft.code}` : 'New strategy'}
      subtitle={draft.id ? `Version ${draft.version} · updated ${new Date(draft.updated_at).toLocaleString()}` : undefined} onClose={() => setDraft(null)}>
      <div className="cfg-detail">
      <form className="cfg-form" onSubmit={saveStrategy}>
        <fieldset className="cfg-plain" disabled={!canEdit}>
          <Field label="Strategy code" hint={draft.id ? 'Fixed after creation' : 'Letters, digits, underscore'}><input aria-label="Strategy code" required readOnly={!!draft.id} maxLength={32} value={draft.code} onChange={e => setDraft({ ...draft, code: e.target.value })} /></Field>
          <Field label="Name"><input aria-label="Name" required maxLength={120} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} /></Field>
          <Field label="Description"><textarea aria-label="Description" maxLength={2000} rows={3} value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} /></Field>
          {draft.id && <Field label="Status"><Choice label="Status" value={draft.status} options={[['ACTIVE', 'Active'], ['RETIRED', 'Retired']]} onChange={v => setDraft({ ...draft, status: v })} /></Field>}
        </fieldset>
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
        {canEdit && <div className="cfg-actions"><button className="primary" disabled={busy}>{draft.id ? 'Save strategy' : 'Create strategy'}</button></div>}
      </form>
      {draft.id && <section className="cfg-versions" aria-label="Versions">
        <div className="cfg-points-head"><strong>Versions</strong>{canEdit && draft.status === 'ACTIVE' && !form && <button type="button" onClick={newVersion}>New version</button>}</div>
        {form && <form className="cfg-subform" onSubmit={saveVersion} aria-label="Version form">
          <strong>{form.id ? `Edit draft v${form.number}` : 'New strategy version'}</strong>
          <Field label="Algorithm" hint={algo?.description}><Choice label="Algorithm" value={form.algorithm} options={algos.map(a => [a.id, a.label] as [string, string])} onChange={v => setForm({ ...form, algorithm: v, parameters: {} })} /></Field>
          {algo && <ParameterFields schema={algo.schema} value={form.parameters} change={v => setForm({ ...form, parameters: v })} />}
          <Field label="Notes"><input aria-label="Notes" maxLength={2000} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></Field>
          <div className="cfg-actions"><button className="primary" disabled={busy}>{form.id ? 'Save strategy draft' : 'Create strategy draft'}</button><button type="button" onClick={() => setForm(null)}>Cancel</button></div>
        </form>}
        {versions.length === 0 ? <p className="cfg-note">No versions yet.</p> : <table className="cfg-table compact"><thead><tr><th>Version</th><th>Rules</th><th>State</th><th>By</th><th /></tr></thead><tbody>
          {versions.map(v => <tr key={v.id}><td>v{v.number}</td><td><strong>{algos.find(a => a.id === v.algorithm)?.label ?? v.algorithm}</strong><br /><small>{paramSummary(v.parameters)}</small>{v.notes && <><br /><small className="cfg-note">{v.notes}</small></>}</td>
            <td><span className={`cfg-pill ${v.state === 'APPROVED' ? 'ok' : v.state === 'RETIRED' ? 'off' : 'warn'}`}>{v.state}</span></td>
            <td><small>created {v.created_by}{v.approved_by ? <><br />approved {v.approved_by}</> : null}</small></td>
            <td className="cfg-row-actions">
              {v.state === 'DRAFT' && canEdit && <button type="button" onClick={() => setForm({ ...v })}>Edit</button>}
              <button type="button" aria-expanded={evidenceFor === v.id} onClick={() => setEvidenceFor(evidenceFor === v.id ? null : v.id)}>{v.state === 'DRAFT' && canApprove ? 'Evidence & approve' : 'Evidence'}</button>
              {canEdit && v.state !== 'RETIRED' && <button type="button" disabled={busy} onClick={() => void run(async () => { const m = await copyToTimeframe(v.id, v.parameters?.timeframe ?? 'H1'); if (!m) throw new Error('No copy made.'); return { id: draft.id }; }, 'Timeframe copy made: find it in the list as a draft.', draft.id)}>Copy to timeframe…</button>}
              {v.state !== 'RETIRED' && (canEdit || canApprove) && <button type="button" disabled={busy} onClick={() => { if (window.confirm(`Retire v${v.number}? It cannot be used again.`)) void run(() => api(`config/strategy-versions/${v.id}/retire`, 'POST', { version: v.version }), `v${v.number} retired.`, draft.id); }}>Retire</button>}
            </td></tr>).flatMap((tr, i) => evidenceFor === versions[i].id ? [tr, <tr key={versions[i].id + '-ev'}><td colSpan={5}>
              <VersionEvidence versionId={versions[i].id} version={versions[i].version} canApprove={canApprove}
                onDone={m => { setEvidenceFor(null); void run(async () => ({ id: draft.id }), m, draft.id); }} /></td></tr>] : [tr])}
        </tbody></table>}
        <small>Approved versions cannot be changed; create a new version to change rules or parameters. Approval needs the evidence of the strategy-evidence profile (Risk › Limits).</small>
      </section>}
      </div>
    </FloatingWindow>}
  </div>;
}

/* ---------- deployments ---------- */

function sizingBody(s: Row | undefined): Row {
  const m = s?.method ?? 'FIXED_QTY';
  return m === 'RISK_PCT' ? { method: m, risk_pct: String(s?.risk_pct ?? '0.5'), atr_multiple: String(s?.atr_multiple ?? '2'), quantity: null }
    : m === 'FIXED_NOTIONAL' ? { method: m, notional: String(s?.notional ?? '10000'), quantity: null } : { method: m, quantity: String(s?.quantity ?? '1') };
}

function Deployments({ roles, refs }: { roles: string[]; refs: Record<string, Row[]> }) {
  const canEdit = roles.includes('TRADING_OPS'), canApprove = roles.includes('RISK_APPROVER'), canStop = canEdit || canApprove;
  const [rows, setRows] = useState<Row[] | null>(null), [filters, setFilters] = useState<Row>({});
  const [accounts, setAccounts] = useState<Row[]>([]), [mappings, setMappings] = useState<Row[]>([]), [strategies, setStrategies] = useState<Row[]>([]), [versions, setVersions] = useState<Row[]>([]);
  const [draft, setDraft] = useState<Row | null>(null), [reason, setReason] = useState('');
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const load = async (keep?: string) => {
    try {
      const params = new URLSearchParams(Object.entries(filters).filter(([, v]) => v) as [string, string][]);
      const items = (await api(`config/deployments?${params}`)).items; setRows(items);
      if (keep) { const r = items.find((x: Row) => x.id === keep); if (r) setDraft({ ...r }); }
    } catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { void load(); }, [JSON.stringify(filters)]);
  useEffect(() => { void (async () => {
    try { const [a, m, s] = await Promise.all(['config/accounts', 'config/mappings', 'config/strategies'].map(x => api(x))); setAccounts(a.items); setMappings(m.items); setStrategies(s.items); }
    catch (e) { setError((e as Error).message); }
  })(); }, []);
  useEffect(() => { if (draft?.strategy_id) void (async () => setVersions((await api(`config/strategies/${draft.strategy_id}/versions`)).items))(); else setVersions([]); }, [draft?.strategy_id]);
  const body = (d: Row) => ({ strategy_id: d.strategy_id, strategy_version_id: d.strategy_version_id, instrument_id: d.instrument_id, account_id: d.account_id,
    data_mapping_id: d.data_mapping_id, allocation_weight_bps: Number(d.allocation_weight_bps), note: d.note ?? '', mode: d.mode ?? 'PAPER',
    signal_expiry_bars: Number(d.signal_expiry_bars ?? 2), sizing: sizingBody(d.sizing), realign: d.realign !== false,
    protect: Object.fromEntries(Object.entries(d.protect ?? {}).filter(([, v]) => v !== '' && v != null)) });
  async function run(task: () => Promise<any>, message: string) {
    setBusy(true); setError(''); setNotice('');
    try { const r = await task(); setNotice(message); setReason(''); await load(r?.id ?? draft?.id); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const dataSources = mappings.filter(m => m.instrument_id === draft?.instrument_id && m.usage !== 'TRADING');
  const filter = (key: string, label: string, options: [string, string][]) => <select aria-label={label} value={filters[key] ?? ''} onChange={e => setFilters({ ...filters, [key]: e.target.value })}>
    <option value="">All {label.toLowerCase()}</option>{options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select>;
  const transition = (action: string, label: string, cls = '') => <button type="button" className={cls} disabled={busy || reason.trim().length < 5}
    onClick={() => void run(() => api(`config/deployments/${draft!.id}/${action}`, 'POST', { version: draft!.version, reason }), `Deployment ${label.toLowerCase()}d.`.replace('dd.', 'd.'))}>{label}</button>;
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar">
        {filter('provider_id', 'Providers', refs.providers.map(r => [r.id, r.code]))}
        {filter('account_id', 'Accounts', accounts.map(r => [r.id, `${r.provider_code} · ${r.code}`]))}
        {filter('instrument_id', 'Instruments', refs.instruments.map(r => [r.id, r.symbol]))}
        {filter('strategy_id', 'Strategies', strategies.map(r => [r.id, r.code]))}
        {filter('status', 'Statuses', [['PROPOSED', 'Proposed'], ['ACTIVE', 'Active'], ['PAUSED', 'Paused'], ['ENDED', 'Ended']])}
        {canEdit ? <button className="primary" onClick={() => { setError(''); setNotice(''); setDraft({ strategy_id: filters.strategy_id ?? '', strategy_version_id: '', instrument_id: filters.instrument_id ?? '', account_id: filters.account_id ?? '', data_mapping_id: '', allocation_weight_bps: 10000, note: '',
          mode: 'PAPER', signal_expiry_bars: 2, sizing: { method: 'FIXED_QTY', quantity: '1' } }); }}>New deployment</button>
          : <span className="cfg-note">Read only</span>}
      </div>
      {error && !draft && <p role="alert" className="cfg-error">{error}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No deployments match. A strategy trades only where a deployment row exists and is active.</p> :
        <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Strategy</th><th>Instrument</th><th>Account</th><th>Env</th><th>Market data from</th><th>Weight</th><th>Mode</th><th>Status</th><th>Ready</th></tr></thead><tbody>
          {rows.map(r => <tr key={r.id} tabIndex={0} className={draft?.id === r.id ? 'selected' : ''} onClick={() => { setError(''); setNotice(''); setReason(''); setDraft({ ...r }); }}>
            <td>{r.strategy_code} v{r.version_number}</td><td>{r.instrument_symbol}</td><td>{r.provider_code} · {r.account_code}</td><td><Env value={r.environment} /></td>
            <td>{r.data_provider_code} · {r.data_connection_code} · {r.data_symbol}</td><td>{(r.allocation_weight_bps / 100).toFixed(2)}%</td><td>{r.mode === 'BROKER' ? 'Broker' : 'Paper'}</td>
            <td><span className={`cfg-pill ${r.status === 'ACTIVE' ? 'ok' : r.status === 'ENDED' ? 'off' : 'warn'}`}>{r.status}</span></td><td><Ready blockers={r.blockers} /></td></tr>)}
        </tbody></table></div>}
    </div>
    {draft && <FloatingWindow storageKey="corei-os-mini.config-window.v1" title={draft.id ? `Deployment · ${draft.strategy_code} · ${draft.instrument_symbol} · ${draft.account_code}` : 'New deployment'}
      subtitle={draft.id ? `${draft.status} · proposed by ${draft.proposed_by}${draft.activated_by ? ` · activated by ${draft.activated_by}` : ''}` : undefined} onClose={() => setDraft(null)}>
      <div className="cfg-detail">
      {draft.id && <div className="cfg-blockers"><h3>Readiness</h3>{draft.blockers.length ? <ul>{draft.blockers.map((b: string) => <li key={b}>{BLOCKERS[b] ?? b}</li>)}</ul> : <p className="cfg-ok">Ready to trade</p>}</div>}
      <form className="cfg-form" onSubmit={e => { e.preventDefault(); void run(() => draft.id ? api(`config/deployments/${draft.id}`, 'PUT', { ...body(draft), version: draft.version }) : api('config/deployments', 'POST', body(draft)),
        draft.id ? 'Saved. The deployment returns to Proposed and needs activating again.' : 'Deployment proposed. A risk approver must activate it.'); }}>
        <fieldset className="cfg-plain" disabled={!canEdit || draft.status === 'ENDED'}>
          <Field label="Strategy" hint={draft.id ? 'Fixed after creation' : undefined}><select aria-label="Strategy" required disabled={!!draft.id} value={draft.strategy_id} onChange={e => setDraft({ ...draft, strategy_id: e.target.value, strategy_version_id: '' })}>
            <option value="" disabled>Select…</option>{strategies.filter(s => s.status === 'ACTIVE' || s.id === draft.strategy_id).map(s => <option key={s.id} value={s.id}>{s.code} · {s.name}</option>)}</select></Field>
          <Field label="Strategy version" hint="Only approved versions can be activated"><select aria-label="Strategy version" required value={draft.strategy_version_id} onChange={e => setDraft({ ...draft, strategy_version_id: e.target.value })}>
            <option value="" disabled>Select…</option>{versions.filter(v => v.state !== 'RETIRED' || v.id === draft.strategy_version_id).map(v => <option key={v.id} value={v.id}>v{v.number} · {v.state} · {paramSummary(v.parameters)}</option>)}</select></Field>
          <Field label="Instrument" hint={draft.id ? 'Fixed after creation' : undefined}><select aria-label="Instrument" required disabled={!!draft.id} value={draft.instrument_id} onChange={e => setDraft({ ...draft, instrument_id: e.target.value, data_mapping_id: '' })}>
            <option value="" disabled>Select…</option>{refs.instruments.map(i => <option key={i.id} value={i.id}>{i.symbol} · {i.name}</option>)}</select></Field>
          <Field label="Account (orders go here)" hint={draft.id ? 'Fixed after creation' : undefined}><select aria-label="Account" required disabled={!!draft.id} value={draft.account_id} onChange={e => setDraft({ ...draft, account_id: e.target.value })}>
            <option value="" disabled>Select…</option>{accounts.filter(a => a.purpose !== 'MARKET_DATA').map(a => <option key={a.id} value={a.id}>{a.provider_code} · {a.connection_code} · {a.code} · {a.environment}</option>)}</select></Field>
          <Field label="Market data from" hint="Any connection that maps this instrument for market data; it need not be the account's connection"><select aria-label="Market data from" required value={draft.data_mapping_id} onChange={e => setDraft({ ...draft, data_mapping_id: e.target.value })}>
            <option value="" disabled>{draft.instrument_id ? (dataSources.length ? 'Select…' : 'No market-data mapping for this instrument') : 'Choose the instrument first'}</option>
            {dataSources.map(m => <option key={m.id} value={m.id}>{m.provider_code} · {m.connection_code} · {m.broker_symbol} · {m.environment}</option>)}</select></Field>
          <Field label="Allocation weight (%)" hint="Share of the strategy's target for this account when it is allocated across accounts"><input aria-label="Allocation weight" required type="number" min={0.01} max={100} step={0.01}
            value={Number(draft.allocation_weight_bps) / 100} onChange={e => setDraft({ ...draft, allocation_weight_bps: Math.round(Number(e.target.value) * 100) })} /></Field>
          <Field label="How it trades" hint="Paper: COREI simulates fills at live prices (no broker order). Broker: real orders to this account (demo or live) — needs the order adapter and order sending on.">
            <Choice label="Trading mode" value={draft.mode ?? 'PAPER'} options={[['PAPER', 'Paper (simulated by COREI)'], ['BROKER', 'Broker (real orders)']]} onChange={v => setDraft({ ...draft, mode: v })} /></Field>
          <Field label="Order size" hint="Risk rounds it down to the lot step and checks it against every limit">
            <Choice label="Sizing method" value={draft.sizing?.method ?? 'FIXED_QTY'} options={[['FIXED_QTY', 'Fixed quantity'], ['RISK_PCT', '% of equity at risk (ATR stop)'], ['FIXED_NOTIONAL', 'Fixed value']]}
              onChange={v => setDraft({ ...draft, sizing: { ...(draft.sizing ?? {}), method: v } })} /></Field>
          {(draft.sizing?.method ?? 'FIXED_QTY') === 'FIXED_QTY' && <Field label="Quantity (lots / contracts)"><input aria-label="Sizing quantity" inputMode="decimal" value={draft.sizing?.quantity ?? '1'}
            onChange={e => setDraft({ ...draft, sizing: { ...draft.sizing, quantity: e.target.value } })} /></Field>}
          {draft.sizing?.method === 'RISK_PCT' && <div className="cfg-pair"><Field label="Risk per trade (% of equity)"><input aria-label="Sizing risk percent" inputMode="decimal" value={draft.sizing?.risk_pct ?? '0.5'}
            onChange={e => setDraft({ ...draft, sizing: { ...draft.sizing, risk_pct: e.target.value } })} /></Field>
            <Field label="Stop distance (× ATR)"><input aria-label="Sizing ATR multiple" inputMode="decimal" value={draft.sizing?.atr_multiple ?? '2'}
              onChange={e => setDraft({ ...draft, sizing: { ...draft.sizing, atr_multiple: e.target.value } })} /></Field></div>}
          {draft.sizing?.method === 'FIXED_NOTIONAL' && <Field label="Value per order (account currency)"><input aria-label="Sizing notional" inputMode="decimal" value={draft.sizing?.notional ?? '10000'}
            onChange={e => setDraft({ ...draft, sizing: { ...draft.sizing, notional: e.target.value } })} /></Field>}
          <Field label="Signal valid for (bars)" hint="A signal not acted on within this many bars of its timeframe expires"><input aria-label="Signal expiry bars" type="number" min={1} max={500}
            value={draft.signal_expiry_bars ?? 2} onChange={e => setDraft({ ...draft, signal_expiry_bars: e.target.value })} /></Field>
          <div className="cfg-pair">
            <Field label="Stop-loss (× ATR)" hint={draft.sizing?.method === 'RISK_PCT' ? 'Blank: the sizing stop distance is used' : 'Blank: no stop-loss'}><input aria-label="Protect stop ATR" inputMode="decimal"
              value={draft.protect?.stop_atr ?? ''} onChange={e => setDraft({ ...draft, protect: { ...(draft.protect ?? {}), stop_atr: e.target.value } })} /></Field>
            <Field label="Take-profit (× ATR)" hint="Blank: none"><input aria-label="Protect take ATR" inputMode="decimal"
              value={draft.protect?.take_atr ?? ''} onChange={e => setDraft({ ...draft, protect: { ...(draft.protect ?? {}), take_atr: e.target.value } })} /></Field>
          </div>
          <p className="cfg-note">Real MT5 orders carry the stop and target to the broker; on paper and Upstox COREI watches them and closes the position.</p>
          <label className="intel-check"><input type="checkbox" aria-label="Realign" checked={draft.realign !== false} onChange={e => setDraft({ ...draft, realign: e.target.checked })} />
            Retry a decision that was not executed (refused, rejected or expired) on the next bar — at most the attempts set in Execution settings, never after a stop-out</label>
          <Field label="Note"><input aria-label="Note" maxLength={1000} value={draft.note ?? ''} onChange={e => setDraft({ ...draft, note: e.target.value })} /></Field>
        </fieldset>
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
        {canEdit && draft.status !== 'ENDED' && <div className="cfg-actions"><button className="primary" disabled={busy}>{draft.id ? 'Save changes' : 'Propose deployment'}</button>
          {draft.id && draft.status === 'PROPOSED' && <button type="button" className="danger" disabled={busy} onClick={() => { if (window.confirm('Delete this proposed deployment?')) void run(async () => { await api(`config/deployments/${draft.id}?version=${draft.version}`, 'DELETE'); setDraft(null); }, 'Deleted.'); }}>Delete</button>}</div>}
      </form>
      {draft.id && draft.status !== 'ENDED' && canStop && <div className="cfg-credential" aria-label="Deployment status">
        <h3>Change status</h3>
        <Field label="Reason (recorded in audit log)"><input aria-label="Status reason" minLength={5} maxLength={500} value={reason} onChange={e => setReason(e.target.value)} /></Field>
        <div className="cfg-actions">
          {canApprove && ['PROPOSED', 'PAUSED'].includes(draft.status) && transition('activate', 'Activate', 'primary')}
          {draft.status === 'ACTIVE' && transition('pause', 'Pause')}
          {transition('end', 'End', 'danger')}
        </div>
        {!canApprove && ['PROPOSED', 'PAUSED'].includes(draft.status) && <small>Activation needs the Risk approver role{draft.environment === 'LIVE' ? ', and on LIVE a different person from the proposer' : ''}.</small>}
      </div>}
      </div>
    </FloatingWindow>}
  </div>;
}

/* ---------- users and passwords ---------- */

/** A session from an older backend (no roles) is treated as signed out. */
function asSession(data: any): Session | null {
  return data && typeof data.username === 'string' && Array.isArray(data.roles) ? data as Session : null;
}


function ChangePassword({ session, done, cancel }: { session: Session; done: () => void; cancel?: () => void }) {
  const [current, setCurrent] = useState(''), [next, setNext] = useState(''), [again, setAgain] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    if (next !== again) { setError('The new passwords do not match.'); return; }
    setBusy(true);
    try { await api('operator/password', 'POST', { current_password: current, new_password: next }); done(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <form className="cfg-form cfg-password" onSubmit={submit} aria-label="Change password">
    {session.must_change_password && <p className="cfg-note">Your password was set by an administrator. Choose your own password to continue.</p>}
    <Field label="Current password"><input required type="password" autoComplete="current-password" value={current} onChange={e => setCurrent(e.target.value)} /></Field>
    <Field label="New password" hint={`At least ${session.password_min ?? 6} characters`}><input required minLength={session.password_min ?? 6} type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} /></Field>
    <Field label="Repeat new password"><input required minLength={session.password_min ?? 6} type="password" autoComplete="new-password" value={again} onChange={e => setAgain(e.target.value)} /></Field>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="cfg-actions"><button className="primary" disabled={busy}>Change password</button>{cancel && <button type="button" onClick={cancel}>Cancel</button>}</div>
  </form>;
}

function Users({ me }: { me: Session }) {
  const [rows, setRows] = useState<Row[] | null>(null), [draft, setDraft] = useState<Row | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [reset, setReset] = useState('');
  const load = async (keep?: string) => {
    try { const items = (await api('admin/users')).items; setRows(items); if (keep) { const r = items.find((x: Row) => x.id === keep); if (r) setDraft({ ...r }); } }
    catch (e) { setError((e as Error).message); }
  };
  useEffect(() => { void load(); }, []);
  const self = draft?.username === me.username;
  async function save(e: FormEvent) {
    e.preventDefault(); if (!draft) return; setBusy(true); setError(''); setNotice('');
    try {
      const saved = draft.id ? await api(`admin/users/${draft.id}`, 'PUT', { display_name: draft.display_name, roles: draft.roles, status: draft.status, version: draft.version })
        : await api('admin/users', 'POST', { username: draft.username, display_name: draft.display_name, roles: draft.roles, temporary_password: draft.temporary_password });
      setNotice(draft.id ? 'User updated. Their sessions end if roles or status changed.' : 'User created. Give them the temporary password; they must change it at first sign-in.');
      await load(saved.id);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function resetPassword() {
    if (!draft?.id) return; setBusy(true); setError(''); setNotice('');
    try { await api(`admin/users/${draft.id}/password`, 'POST', { temporary_password: reset }); setReset(''); setNotice('Temporary password set. The user must change it at next sign-in; their sessions have ended.'); await load(draft.id); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const toggleRole = (role: string) => setDraft(d => d && ({ ...d, roles: d.roles.includes(role) ? d.roles.filter((r: string) => r !== role) : [...d.roles, role] }));
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar"><button className="primary" onClick={() => { setError(''); setNotice(''); setDraft({ username: '', display_name: '', roles: ['OBSERVER'], status: 'ACTIVE', temporary_password: '' }); }}>New user</button></div>
      {!rows ? <p className="cfg-note">Loading…</p> : <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Username</th><th>Name</th><th>Roles</th><th>Status</th><th>Last sign-in</th><th>Sessions</th></tr></thead><tbody>
        {rows.map(r => <tr key={r.id} tabIndex={0} className={draft?.id === r.id ? 'selected' : ''} onClick={() => { setError(''); setNotice(''); setReset(''); setDraft({ ...r }); }}>
          <td>{r.username}{r.username === me.username ? ' (you)' : ''}</td><td>{r.display_name}</td><td>{r.roles.map((x: string) => ROLE_LABEL[x]).join(', ')}</td>
          <td><Status value={r.status} />{r.must_change_password ? <small className="cfg-note"> · must change password</small> : null}{r.locked_until && new Date(r.locked_until) > new Date() ? <span className="cfg-pill warn">Locked</span> : null}</td>
          <td>{r.last_login_at ? new Date(r.last_login_at).toLocaleString() : 'Never'}</td><td>{r.active_sessions}</td></tr>)}
      </tbody></table></div>}
    </div>
    {draft && <FloatingWindow storageKey="corei-os-mini.user-window.v1" title={draft.id ? `User · ${draft.username}` : 'New user'} onClose={() => setDraft(null)}>
      <form className="cfg-form" onSubmit={save}>
        <Field label="Username" hint={draft.id ? 'Fixed after creation' : 'Lower-case letters, digits, dot, dash, underscore'}>
          <input aria-label="Username" required readOnly={!!draft.id} value={draft.username} onChange={e => setDraft({ ...draft, username: e.target.value })} /></Field>
        <Field label="Display name"><input aria-label="Display name" required maxLength={120} value={draft.display_name} onChange={e => setDraft({ ...draft, display_name: e.target.value })} /></Field>
        <fieldset className="cfg-subform" disabled={self}><legend>Roles</legend>
          {Object.keys(ROLE_LABEL).map(role => <label key={role} className="cfg-check"><input type="checkbox" aria-label={ROLE_LABEL[role]} checked={draft.roles.includes(role)} onChange={() => toggleRole(role)} />
            <span><strong>{ROLE_LABEL[role]}</strong><small>{ROLE_HINT[role]}</small></span></label>)}
          {self && <small>You cannot change your own roles or status.</small>}
        </fieldset>
        {draft.id ? <Field label="Status"><Choice label="Status" value={draft.status} disabled={self} options={[['ACTIVE', 'Active'], ['DISABLED', 'Disabled (cannot sign in)']]} onChange={v => setDraft({ ...draft, status: v })} /></Field>
          : <Field label="Temporary password" hint={`At least ${me.password_min ?? 6} characters. The user must change it at first sign-in.`}><input aria-label="Temporary password" required minLength={me.password_min ?? 6} type="password" autoComplete="new-password" value={draft.temporary_password} onChange={e => setDraft({ ...draft, temporary_password: e.target.value })} /></Field>}
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
        <div className="cfg-actions"><button className="primary" disabled={busy || !draft.roles.length}>{draft.id ? 'Save changes' : 'Create user'}</button></div>
      </form>
      {draft.id && !self && <form className="cfg-credential" onSubmit={e => { e.preventDefault(); void resetPassword(); }}>
        <h3>Reset password</h3><p className="cfg-note">Sets a temporary password, unlocks the account and ends the user's sessions.</p>
        <Field label="New temporary password"><input aria-label="New temporary password" required minLength={me.password_min ?? 6} type="password" autoComplete="new-password" value={reset} onChange={e => setReset(e.target.value)} /></Field>
        <div className="cfg-actions"><button disabled={busy}>Reset password</button></div></form>}
    </FloatingWindow>}
  </div>;
}

/* ---------- workspace ---------- */

/** An old address of a page that moved: opens it in its new place (bookmarks and links keep working). */
function MovedTo({ view, section, label }: { view: View; section: string; label: string }) {
  useEffect(() => { goto(view, section); }, [view, section]);
  return <p className="cfg-note">{label} moved to {view}. <button className="link" onClick={() => goto(view, section)}>Open {label} ↗</button></p>;
}

export default function Configuration() {
  const [user, setUser] = useState<Session | null>(null), [checking, setChecking] = useState(true), [changing, setChanging] = useState(false);
  // The top bar's account menu asks for a password change by opening Setup with this flag.
  useEffect(() => {
    const take = () => { try { if (sessionStorage.getItem('corei-os-mini.change-password')) { sessionStorage.removeItem('corei-os-mini.change-password'); setChanging(true); } } catch { /* ignore */ } };
    take(); window.addEventListener('corei-change-password', take); return () => window.removeEventListener('corei-change-password', take);
  }, []);
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState('');
  // Software update moved to the main Settings (0.67.1): old links (?view=Configuration&section=update) arrive there.
  useEffect(() => { if (new URLSearchParams(window.location.search).get('section') === 'update') goto('Settings', 'software-update'); }, []);
  const [section, setSection] = useSection<Section>('Configuration', 'guide', SECTIONS.map(x => x.id)), [refs, setRefs] = useState<Record<string, Row[]>>({ providers: [], connections: [], instruments: [], groups: [], accounts: [] });
  const [summary, setSummary] = useState<Row | null>(null), [openId, setOpenId] = useState<{ id?: string; n: number }>({ n: 0 });
  // The broker's redirect result, shown once: the address is cleaned so a saved link never repeats an old message.
  const [brokerLogin] = useState(() => new URLSearchParams(window.location.search).get('broker_login'));
  const [brokerReason] = useState(() => new URLSearchParams(window.location.search).get('reason')), [loginShown, setLoginShown] = useState(false);
  useEffect(() => { if (brokerLogin) { try { const url = new URL(window.location.href);
    url.searchParams.delete('broker_login'); url.searchParams.delete('reason');
    window.history.replaceState(window.history.state, '', url); } catch { /* address only */ } } }, []);
  async function loadRefs() {
    try {
      const [p, c, i, g, a, s] = await Promise.all(['providers', 'connections', 'instruments', 'groups', 'accounts', 'summary'].map(x => api('config/' + x)));
      setRefs({ providers: p.items, connections: c.items, instruments: i.items, groups: g.items, accounts: a.items }); setSummary(s);
    } catch (e) { if ((e as any).status === 401) setUser(null); else setError((e as Error).message); }
  }
  // Window positions and list views follow the user to other devices.
  useEffect(() => {
    if (!user || user.must_change_password) return;
    let timer: number | undefined; const queue: Record<string, unknown> = {};
    void api('operator/prefs').then((prefs: Row) => { for (const [k, v] of Object.entries(prefs ?? {})) try { if (!localStorage.getItem(k)) localStorage.setItem(k, JSON.stringify(v)); } catch { /* preference only */ } }).catch(() => undefined);
    const save = (e: Event) => { const { key, value } = (e as CustomEvent).detail ?? {}; if (!key) return; queue[key] = value;
      window.clearTimeout(timer); timer = window.setTimeout(() => { for (const [k, v] of Object.entries(queue)) { void api(`operator/prefs/${encodeURIComponent(k)}`, 'PUT', { value: v }).catch(() => undefined); delete queue[k]; } }, 1500); };
    window.addEventListener('corei-pref', save); return () => { window.removeEventListener('corei-pref', save); window.clearTimeout(timer); };
  }, [user]);
  const navigate: Navigate = (to, id) => {
    if (!window.dispatchEvent(new Event('corei-before-layout-change', { cancelable: true }))) return;
    setSection(to as Section); setOpenId(o => ({ id, n: o.n + 1 }));
  };
  useEffect(() => {
    const check = async () => { try { setUser(asSession(await api('operator/session'))); } catch { setUser(null); } finally { setChecking(false); } };
    void check();
    // Signing in or out on any screen applies here too.
    window.addEventListener('corei-session', check);
    return () => window.removeEventListener('corei-session', check);
  }, []);
  useEffect(() => { if (user && !user.must_change_password) void loadRefs(); }, [user]);
  async function signIn(e: FormEvent) {
    e.preventDefault(); setError('');
    try { const s = asSession(await api('operator/login', 'POST', { username, password })); if (!s) throw new Error('The server did not return a valid session. Update the backend.'); setUser(s); setPassword(''); } catch (err) { setError((err as Error).message); }
  }
  async function signOut() { try { await api('operator/logout', 'POST'); } finally { setUser(null); } }

  if (checking) return <div className="content"><p>Checking sign-in…</p></div>;
  if (!user) return <div className="content cfg-signin"><div className="eyebrow">CONFIGURATION</div><h1>Sign in</h1>
    <form className="cfg-form" onSubmit={signIn}><Field label="Username"><input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></Field>
      <Field label="Password"><input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {error && <p role="alert" className="cfg-error">{error}</p>}<div className="cfg-actions"><button className="primary">Sign in</button></div></form></div>;

  if (user.must_change_password || changing) return <div className="content cfg-signin"><div className="eyebrow">CONFIGURATION</div><h1>Change password</h1>
    <ChangePassword session={user} cancel={user.must_change_password ? undefined : () => setChanging(false)}
      done={() => { setChanging(false); setUser({ ...user, must_change_password: false }); }} />
    {user.must_change_password && <button className="link" onClick={() => void signOut()}>Sign out</button>}</div>;

  const sections = SECTIONS.filter(s => s.id !== 'users' || user.roles.includes('ADMIN'));
  const canEdit = user.roles.includes('TRADING_OPS');
  const current = sections.find(s => s.id === section) ?? sections[0];
  const live = summary?.execution_controls?.find((c: Row) => c.environment === 'LIVE');
  const row = setupRowOf(current.id);
  return <div className="cfg">
    {/* One compact line: the set-up path in order (also in the side bar; this stays when the side bar is hidden). */}
    <nav className="cfg-nav" aria-label="Setup sections">
      {SETUP_GROUPS.map(([g, items]) => <Fragment key={g}><span className="cfg-nav-group">{g}</span>
        {items.filter(([id]) => id !== 'users' || user.roles.includes('ADMIN')).map(([id, label]) => <button key={id} aria-current={row === id ? 'page' : undefined} onClick={() => openSetupItem(id, x => navigate(x))}>
          <span>{label}</span>
          {summary && (id === 'brokers' ? summary.counts?.accounts : summary.counts?.[id]) != null && <small className={id === 'approvals' && summary.counts.approvals > 0 ? 'cfg-live-on' : ''}>{id === 'brokers' ? summary.counts.accounts : summary.counts[id]}</small>}
          {id === 'controls' && live && <small className={live.trading_enabled ? 'cfg-live-on' : ''}>{live.trading_enabled ? 'LIVE ON' : 'off'}</small>}</button>)}</Fragment>)}
    </nav>
    <section className="cfg-main">
      <header className="cfg-head page-head"><h1>{current.label}</h1><Info label={`About ${current.label}`}>{current.hint}</Info><span className="push" />
        <div className="cfg-user" title={`${user.display_name || user.username} · ${user.roles.map(r => ROLE_LABEL[r]).join(', ')}`}>
          <span className="sr-only">{user.display_name || user.username} · {user.roles.map(r => ROLE_LABEL[r]).join(', ')}</span>
          <button className="link" onClick={() => setChanging(true)}>Change password</button> · <button className="link" onClick={() => void signOut()}>Sign out</button></div></header>
      {summary && !summary.credential_storage_ready && <p role="alert" className="cfg-error">Credential encryption is not set up on the server. Run scripts/setup-broker-accounts.sh.</p>}
      {summary && summary.four_eyes_live_changes === false && <p role="alert" className="cfg-error">Four-eyes on LIVE changes is OFF: one person can change LIVE addresses and passwords. Turn it on under Order sending › Policies.</p>}
      {brokerLogin && !loginShown && <p role="status" className={brokerLogin === 'ok' ? 'cfg-ok' : 'cfg-error'}>{brokerLogin === 'ok' ? 'Broker login completed; today’s access token is stored.'
        : `Broker login failed${brokerReason ? ': ' + brokerReason : '.'}`} <button type="button" className="cfg-link" aria-label="Dismiss" onClick={() => setLoginShown(true)}>✕</button></p>}
      {error && <p role="alert" className="cfg-error">{error}</p>}
      <SubWorkspace view="Configuration" label="Setup" tab={current.id} setTab={id => setSection(id as Section)} initial={['guide', 'health', 'brokers', 'instruments', 'strategies', 'controls']}
        items={sections.filter(x => x.id !== 'paper').map(x => ({ id: x.id, label: x.label, hint: x.hint, group: (SETUP_GROUPS.find(([, its]) => its.some(([k]) => k === x.id))?.[0] ?? '').replace(/^\d · /, '') }))}
        render={(id: string) => <Fragment key={id === section && openId.id ? `${id}:${openId.n}` : id}>
          {BROKER_TABS.some(([b]) => b === id) && <nav className="cfg-views" aria-label="Brokers and accounts views">
            {BROKER_TABS.map(([b, label]) => <button key={b} aria-current={id === b ? 'page' : undefined} onClick={() => navigate(b)}><span>{label}</span>
              {summary && b !== 'brokers' && <small>{summary.counts?.[b]}</small>}</button>)}</nav>}
          {id === 'guide' ? <SetupHome />
        : id === 'gates' ? <SetupGates me={user.username} roles={user.roles} />
        : id === 'paper' ? <MovedTo view="Portfolio" section="paper" label="Paper accounts" />
        : id === 'fx' ? <FxRates canEdit={user.roles.includes('RISK_APPROVER')} />
        : id === 'health' ? <div className="cfg-pane"><HealthBoard navigate={navigate} /></div>
        : id === 'brokers' ? <div className="cfg-pane ct-pane"><ConnectTree navigate={navigate} canEdit={canEdit} changed={() => void loadRefs()} focus={id === section ? openId.id : undefined} /></div>
        : id === 'approvals' ? <div className="cfg-pane"><Approvals me={user.username} roles={user.roles} changed={() => void loadRefs()} /></div>
        : id === 'alerts' ? <div className="cfg-pane"><Alerts canEdit={canEdit} /></div>
        : id === 'integrations' ? <Integrations isAdmin={user.roles.includes('ADMIN')} />
        : id === 'mode' ? <PlatformMode isAdmin={user.roles.includes('ADMIN')} />
        : id === 'bridges' ? <div className="cfg-pane"><BridgeNodes canEdit={canEdit} /></div>
        : id === 'controls' ? <div className="cfg-pane"><OrderSending changed={() => void loadRefs()} roles={user.roles} /><Policies roles={user.roles} changed={() => void loadRefs()} /></div> : id === 'audit' ? <AuditLog />
        : id === 'users' ? <Users me={user} />
        : id === 'database' ? (user.roles.includes('ADMIN') ? <DatabaseEditor /> : <p className="cfg-note">The database editor needs the Administrator role.</p>)
        : id === 'strategies' ? <Strategies roles={user.roles} reloadRefs={() => void loadRefs()} />
        : id === 'deployments' ? <Deployments roles={user.roles} refs={refs} />
        : <Records key={id} entity={id as Entity} refs={refs} reloadRefs={() => void loadRefs()} canEdit={canEdit} navigate={navigate}
            openId={openId.id && id === section ? `${openId.id}` : undefined} />}</Fragment>} />
    </section>
  </div>;
}
