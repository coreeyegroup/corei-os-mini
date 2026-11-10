/*
 * Configuration workspace: providers, connections, accounts, instruments, symbol
 * mappings, order-sending controls and the audit log (Blueprint v1.2 §8, §13.3, §17.2).
 *
 * Owns: presentation and form state only. Every rule is enforced by the backend
 * (/api/config); this screen shows the backend's own validation messages.
 * Secrets are write-only: the screen never receives a stored credential back.
 */
import { Fragment, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react';
import './configuration.css';
import FloatingWindow from './FloatingWindow';

type Row = Record<string, any>;
type Section = 'providers' | 'connections' | 'accounts' | 'instruments' | 'mappings' | 'strategies' | 'deployments' | 'controls' | 'users' | 'audit';
type Entity = Exclude<Section, 'controls' | 'audit' | 'users' | 'strategies' | 'deployments'>;
type Session = { username: string; display_name: string; roles: string[]; must_change_password: boolean };
const ROLE_LABEL: Record<string, string> = { ADMIN: 'Administrator', TRADING_OPS: 'Trading operations', RISK_APPROVER: 'Risk approver', OBSERVER: 'Observer' };
const ROLE_HINT: Record<string, string> = { ADMIN: 'Manage users and roles', TRADING_OPS: 'Change configuration and credentials; turn order sending off',
  RISK_APPROVER: 'Turn order sending on or off', OBSERVER: 'Read configuration and audit log' };

const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: 'providers', label: 'Providers', hint: 'Brokers, exchanges and data vendors' },
  { id: 'connections', label: 'Connections', hint: 'Platform, environment and endpoints per provider' },
  { id: 'accounts', label: 'Accounts', hint: 'Broker accounts, login and trading permission' },
  { id: 'instruments', label: 'Instruments', hint: 'Canonical instruments and contract specifications' },
  { id: 'mappings', label: 'Symbol mappings', hint: 'Broker symbol for each instrument on a connection' },
  { id: 'strategies', label: 'Strategies', hint: 'Strategy rules and their approved versions' },
  { id: 'deployments', label: 'Deployments', hint: 'Which strategy version trades which instrument on which account, and from which data source' },
  { id: 'controls', label: 'Order sending', hint: 'Firm-wide switch per environment' },
  { id: 'users', label: 'Users', hint: 'Who can sign in and what each role may do' },
  { id: 'audit', label: 'Audit log', hint: 'Every configuration and control change' },
];

const BLOCKERS: Record<string, string> = {
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
  DEPLOYMENT_NOT_ACTIVE: 'Deployment is not active',
  STRATEGY_RETIRED: 'Strategy is retired',
  VERSION_NOT_APPROVED: 'Strategy version is not approved',
  INSTRUMENT_INACTIVE: 'Instrument is inactive',
  DATA_SOURCE_INACTIVE: 'Market-data source (symbol mapping or its connection) is inactive',
  NO_TRADING_SYMBOL_ON_ACCOUNT_CONNECTION: "No trading symbol mapping for this instrument on the account's connection",
  TRADING_SYMBOL_INACTIVE: "Trading symbol mapping on the account's connection is inactive",
};

class ApiError extends Error {}

async function api(path: string, method = 'GET', body?: unknown, raw?: BodyInit): Promise<any> {
  const response = await fetch('/api/' + path, {
    method, cache: 'no-store', credentials: 'same-origin',
    headers: { 'x-corei-request': 'workstation', ...(body !== undefined ? { 'content-type': 'application/json' } : {}),
      ...(raw ? { 'content-type': 'application/octet-stream' } : {}) },
    body: raw ?? (body !== undefined ? JSON.stringify(body) : undefined),
  });
  if (response.status === 204) return null;
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data?.detail;
    const message = Array.isArray(detail) ? detail.map((d: any) => `${d.field}: ${d.message}`).join(' · ')
      : typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status})`;
    const error = new ApiError(message); (error as any).status = response.status; throw error;
  }
  return data;
}

/* ---------- small building blocks ---------- */

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return <label className="cfg-field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label>;
}

function Choice({ value, options, onChange, disabled, label }: { value: string; options: (string | [string, string])[];
  onChange: (v: string) => void; disabled?: boolean; label: string }) {
  return <select aria-label={label} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
    {options.map(o => Array.isArray(o) ? <option key={o[0]} value={o[0]}>{o[1]}</option> : <option key={o}>{o}</option>)}
  </select>;
}

function Status({ value }: { value: string }) {
  return <span className={`cfg-pill ${value === 'ACTIVE' || value === 'TRADING' ? 'ok' : value === 'INACTIVE' || value === 'DISABLED' ? 'off' : ''}`}>{value}</span>;
}

function Env({ value }: { value: string }) {
  return <span className={`cfg-pill ${value === 'LIVE' ? 'live' : 'demo'}`}>{value}</span>;
}

function Ready({ blockers }: { blockers: string[] }) {
  if (!blockers?.length) return <span className="cfg-pill ok">Ready</span>;
  return <span className="cfg-pill warn" title={blockers.map(b => BLOCKERS[b] ?? b).join('\n')}>{blockers.length} blocker{blockers.length > 1 ? 's' : ''}</span>;
}

function Section({ n, title, children }: { n: number; title: string; children: ReactNode }) {
  return <section className="cfg-section"><h4><span>{n}</span>{title}</h4><div className="cfg-section-body">{children}</div></section>;
}

/* ---------- forms per entity ---------- */

const blank: Record<Entity, Row> = {
  providers: { code: '', name: '', kind: 'BROKER', status: 'ACTIVE' },
  connections: { provider_id: '', code: '', name: '', platform: 'MT5', environment: 'DEMO', purpose: 'BOTH', status: 'ACTIVE',
    settings: { server_name: '', access_points: [{ name: 'Access point 1', host: '', port: 443 }] } },
  accounts: { connection_id: '', code: '', name: '', external_account_id: '', holder_name: '', base_currency: 'USD', trading_permission: 'DISABLED', status: 'ACTIVE' },
  instruments: { symbol: '', name: '', asset_class: 'FX', base_currency: '', quote_currency: '', price_precision: 5, quantity_precision: 2,
    tick_size: '0.00001', min_quantity: '0.01', quantity_step: '0.01', contract_size: '100000', contract_unit: 'units', trading_session: '', status: 'ACTIVE' },
  mappings: { connection_id: '', instrument_id: '', broker_symbol: '', usage: 'BOTH', broker_contract_size: '', broker_price_digits: '', status: 'ACTIVE' },
};
const FIELDS: Record<Entity, string[]> = {
  providers: ['code', 'name', 'kind', 'status'],
  connections: ['provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'settings', 'status'],
  accounts: ['connection_id', 'code', 'name', 'external_account_id', 'holder_name', 'base_currency', 'trading_permission', 'status'],
  instruments: ['symbol', 'name', 'asset_class', 'base_currency', 'quote_currency', 'price_precision', 'quantity_precision',
    'tick_size', 'min_quantity', 'quantity_step', 'contract_size', 'contract_unit', 'trading_session', 'status'],
  mappings: ['connection_id', 'instrument_id', 'broker_symbol', 'usage', 'broker_contract_size', 'broker_price_digits', 'status'],
};
const IMMUTABLE: Record<Entity, string[]> = {
  providers: ['code'], connections: ['provider_id', 'code', 'platform', 'environment'],
  accounts: ['connection_id', 'code', 'external_account_id'], instruments: ['symbol'], mappings: ['connection_id', 'instrument_id'],
};
const SINGULAR: Record<Entity, string> = { providers: 'provider', connections: 'connection', accounts: 'account', instruments: 'instrument', mappings: 'symbol mapping' };

function toPayload(entity: Entity, draft: Row): Row {
  const out: Row = {};
  for (const key of FIELDS[entity]) out[key] = draft[key];
  if (entity === 'mappings') {
    out.broker_contract_size = draft.broker_contract_size === '' || draft.broker_contract_size == null ? null : String(draft.broker_contract_size);
    out.broker_price_digits = draft.broker_price_digits === '' || draft.broker_price_digits == null ? null : Number(draft.broker_price_digits);
  }
  if (entity === 'instruments') { out.price_precision = Number(draft.price_precision); out.quantity_precision = Number(draft.quantity_precision); }
  if (entity === 'connections' && draft.platform === 'MT5')
    out.settings = { server_name: draft.settings.server_name, company_name: draft.settings.company_name || null, server_details: draft.settings.server_details ?? {},
      access_points: draft.settings.access_points.map((p: Row) => ({ name: p.name, host: p.host, port: Number(p.port), enabled: p.enabled !== false })) };
  if (entity === 'connections' && draft.platform === 'REST_API') {
    const s = draft.settings;
    out.settings = { api_base_url: s.api_base_url, auth_method: s.auth_method, stream_url: s.stream_url || null,
      oauth_redirect_url: s.auth_method === 'OAUTH2' ? s.oauth_redirect_url || null : null, notifier_webhook_url: s.notifier_webhook_url || null };
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

/** Choose one broker server, then the addresses to use. Works on a servers.dat analysis
 *  or a broker-directory search (both are catalogs on the backend). */
function ServerPicker({ catalog, use, close }: { catalog: Row; use: (server: Row, chosen: Row[]) => void; close: () => void }) {
  const [q, setQ] = useState(''), [list, setList] = useState<Row[]>([]), [total, setTotal] = useState(0), [loading, setLoading] = useState(false);
  const [server, setServer] = useState<Row | null>(null), [picked, setPicked] = useState<Set<string>>(new Set()), [error, setError] = useState('');
  const base = `config/mt5/server-file/${catalog.catalog_id}`;
  const id = (p: Row) => `${p.host}|${p.port}`;
  async function fetchList(offset: number) {
    setLoading(true); setError('');
    try { const data = await api(`${base}/servers?${new URLSearchParams({ q, offset: String(offset), limit: '200' })}`);
      setList(l => offset ? [...l, ...data.items] : data.items); setTotal(data.total);
      if (!offset && data.items.length === 1 && server?.server_name !== data.items[0].server_name) void open(data.items[0].server_name);
    } catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }
  useEffect(() => { const t = setTimeout(() => void fetchList(0), 250); return () => clearTimeout(t); }, [q]);
  async function open(name: string) {
    try { const detail = await api(`${base}/server?${new URLSearchParams({ name })}`); setServer(detail);
      setPicked(new Set(detail.access_points.filter((p: Row) => !isPrivate(p.host)).map(id))); }
    catch (e) { setError((e as Error).message); }
  }
  const toggle = (p: Row) => setPicked(old => { const next = new Set(old); next.has(id(p)) ? next.delete(id(p)) : next.add(id(p)); return next; });
  const points: Row[] = server?.access_points ?? [];
  const chosen = points.filter(p => picked.has(id(p)));
  const source = catalog.source === 'broker-directory' ? `Broker directory · “${catalog.query}”` : 'servers.dat';
  return <div className="cfg-picker" aria-label="Choose broker server">
    <div className="cfg-picker-head">
      <div><strong>{source}</strong><small>{catalog.server_count.toLocaleString()} servers · {catalog.access_point_count.toLocaleString()} addresses</small></div>
      <button type="button" onClick={close}>Cancel</button>
    </div>
    <input aria-label="Filter servers" autoFocus placeholder="Filter by server, company or address…" value={q} onChange={e => setQ(e.target.value)} />
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="cfg-picker-panes">
      <div className="cfg-picker-servers" role="listbox" aria-label="Servers">
        {list.map(r => <button type="button" role="option" aria-selected={server?.server_name === r.server_name} key={r.server_name}
          className={server?.server_name === r.server_name ? 'on' : ''} onClick={() => void open(r.server_name)}>
          <span className="cfg-picker-name">{r.server_name}</span>
          <span className="cfg-picker-meta">{r.company_name ?? 'Company not stated'} · {r.access_point_count} address{r.access_point_count === 1 ? '' : 'es'}</span>
        </button>)}
        {!list.length && !loading && <p className="cfg-note">No server matches “{q}”.</p>}
        {list.length < total && <button type="button" className="cfg-more-link" disabled={loading} onClick={() => void fetchList(list.length)}>Show {Math.min(200, total - list.length)} more servers</button>}
      </div>
      <div className="cfg-picker-points">
        {!server ? <p className="cfg-note cfg-picker-empty">Select a server on the left to see its addresses.</p> : <>
          <div className="cfg-picker-server">
            <div><strong>{server.server_name}</strong><small>{server.company_name ?? 'Company not stated'}</small></div>
            <span className="cfg-row-actions">
              <button type="button" onClick={() => setPicked(new Set(points.filter(p => !isPrivate(p.host)).map(id)))}>Public</button>
              <button type="button" onClick={() => setPicked(new Set(points.map(id)))}>All</button>
              <button type="button" onClick={() => setPicked(new Set())}>None</button>
            </span>
          </div>
          <ul className="cfg-checklist">
            {points.map(p => <li key={id(p)}><label>
              <input type="checkbox" aria-label={`Select ${address(p)} on ${server.server_name}`} checked={picked.has(id(p))} onChange={() => toggle(p)} />
              <span className="cfg-mono">{address(p)}</span>{isPrivate(p.host) && <span className="cfg-pill off" title="Only reachable inside the broker's network">Private</span>}
            </label></li>)}
          </ul>
          <details className="cfg-more"><summary>Server details</summary><ServerDescription settings={{ server_name: server.server_name, company_name: server.company_name, server_details: server.details }} /></details>
        </>}
      </div>
    </div>
    <div className="cfg-picker-foot">
      <small className="cfg-note">{server ? `${chosen.length} of ${points.length} addresses selected` : `${total.toLocaleString()} servers`}</small>
      <button type="button" className="primary" disabled={!server || !chosen.length} onClick={() => use(server!, chosen)}>
        {server ? `Use ${server.server_name}` : 'Use server'}</button>
    </div>
  </div>;
}

/** Addresses of the chosen server: which are used, in which order. */
function AccessPoints({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const points: Row[] = settings.access_points ?? [];
  const [editable] = useState(() => new Set(points.map((p, i) => p.host ? -1 : i)));
  const [added, setAdded] = useState<Set<number>>(new Set());
  const setPoints = (next: Row[]) => change({ ...settings, access_points: next.map((p, i) => ({ ...p, name: `Access point ${i + 1}` })) });
  const setPoint = (i: number, patch: Row) => setPoints(points.map((p, j) => j === i ? { ...p, ...patch } : p));
  const move = (i: number, d: number) => { const next = [...points]; const [x] = next.splice(i, 1); next.splice(i + d, 0, x); setPoints(next); };
  const on = points.filter(p => p.enabled !== false).length;
  return <div className="cfg-addresses">
    <div className="cfg-addresses-head">
      <span>{on} of {points.length} address{points.length === 1 ? '' : 'es'} in use · tried top to bottom</span>
      <span className="cfg-row-actions">
        <button type="button" disabled={on === points.length} onClick={() => setPoints(points.map(p => ({ ...p, enabled: true })))}>Select all</button>
        <button type="button" disabled={on === 0} onClick={() => setPoints(points.map(p => ({ ...p, enabled: false })))}>Clear all</button>
      </span>
    </div>
    <ol className="cfg-address-list">
      {points.map((p, i) => { const edit = editable.has(i) || added.has(i);
        return <li key={i} className={p.enabled === false ? 'off' : ''}>
          <input type="checkbox" aria-label={`Use access point ${i + 1}`} checked={p.enabled !== false} onChange={e => setPoint(i, { enabled: e.target.checked })} />
          <span className="cfg-address-n">{i + 1}</span>
          {edit ? <span className="cfg-address-edit">
              <input aria-label={`Access point ${i + 1} host`} placeholder="Host or IP" required value={p.host} onChange={e => setPoint(i, { host: e.target.value })} />
              <input aria-label={`Access point ${i + 1} port`} placeholder="Port" required type="number" min={1} max={65535} value={p.port} onChange={e => setPoint(i, { port: e.target.value })} />
            </span>
            : <span className="cfg-mono cfg-address-text">{address(p)}</span>}
          {!edit && isPrivate(p.host) && <span className="cfg-pill off" title="Only reachable inside the broker's network">Private</span>}
          <span className="cfg-row-actions">
            <button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up" title="Try earlier">↑</button>
            <button type="button" disabled={i === points.length - 1} onClick={() => move(i, 1)} aria-label="Move down" title="Try later">↓</button>
            <button type="button" disabled={points.length === 1} onClick={() => setPoints(points.filter((_, j) => j !== i))} aria-label="Remove" title="Remove">✕</button>
          </span>
        </li>; })}
    </ol>
    <button type="button" className="cfg-more-link" disabled={points.length >= 256}
      onClick={() => { setAdded(new Set([...added, points.length])); setPoints([...points, { host: '', port: 443, enabled: true }]); }}>+ Add address</button>
  </div>;
}

/** MT5: find the broker's server (directory search or servers.dat), then its addresses. */
function Mt5Server({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const [catalog, setCatalog] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [term, setTerm] = useState(''), [manual, setManual] = useState(false), [version, setVersion] = useState(0);
  const points: Row[] = settings.access_points ?? [];
  const chosen = !!settings.server_name && !manual;
  async function run(task: Promise<Row>) {
    setBusy(true); setError('');
    try { setCatalog(await task); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  function use(server: Row, picked: Row[]) {
    const id = (p: Row) => `${p.host}|${p.port}`, wanted = new Set(picked.map(id));
    const rest = server.access_points.filter((p: Row) => !wanted.has(id(p)));
    const ordered = [...picked.map(p => ({ ...p, enabled: true })), ...rest.map((p: Row) => ({ ...p, enabled: false }))];
    change({ server_name: server.server_name, company_name: server.company_name ?? null, server_details: server.details ?? {},
      access_points: ordered.map((p, i) => ({ host: p.host, port: p.port, enabled: p.enabled, name: `Access point ${i + 1}` })) });
    setCatalog(null); setManual(false); setVersion(v => v + 1);
  }
  if (catalog) return <ServerPicker catalog={catalog} use={use} close={() => setCatalog(null)} />;
  const finder = <div className="cfg-finder">
    <div className="cfg-search-row">
      <input aria-label="Find broker" placeholder="Broker name, e.g. VTMarkets" value={term} maxLength={64}
        onChange={e => setTerm(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); if (term.trim().length >= 2) void run(api('config/mt5/broker-search', 'POST', { query: term.trim() })); } }} />
      <button type="button" className="primary" disabled={busy || term.trim().length < 2} onClick={() => void run(api('config/mt5/broker-search', 'POST', { query: term.trim() }))}>{busy ? 'Searching…' : 'Find servers'}</button>
    </div>
    <small className="cfg-note">Searches the MT5 broker directory for the broker's current servers and addresses.{' '}
      <label className="cfg-link">Open servers.dat<input type="file" accept=".dat" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void run(api('config/mt5/server-file', 'POST', undefined, f)); e.target.value = ''; }} /></label>
      {' · '}<button type="button" className="cfg-link" onClick={() => { setManual(true); if (!points.length) change({ ...settings, access_points: [{ name: 'Access point 1', host: '', port: 443, enabled: true }] }); }}>Enter manually</button></small>
    {error && <p role="alert" className="cfg-error">{error}</p>}
  </div>;
  return <>
    {chosen ? <div className="cfg-chosen">
        <div><strong>{settings.server_name}</strong><small>{settings.company_name ?? 'Company not stated'}</small></div>
        <button type="button" onClick={() => change({ ...settings, server_name: '' })}>Change server</button>
      </div> : finder}
    {manual && <Field label="Broker server name" hint="Exactly as in MT5, e.g. VTMarkets-Live 3">
      <input aria-label="Broker server name" required maxLength={120} value={settings.server_name ?? ''} onChange={e => change({ ...settings, server_name: e.target.value })} /></Field>}
    {(chosen || manual) && points.length > 0 && <AccessPoints key={version} settings={settings} change={change} />}
    {(chosen || manual) && points.length > 0 && !points.some(p => p.enabled !== false) && <p role="alert" className="cfg-error">Select at least one address.</p>}
    {chosen && (settings.company_name || Object.keys(settings.server_details ?? {}).length > 0) &&
      <details className="cfg-more"><summary>Server details</summary><ServerDescription settings={settings} /></details>}
  </>;
}

function RestSettings({ settings, change }: { settings: Row; change: (s: Row) => void }) {
  const field = (k: string, label: string, hint: string, required = false) => <Field label={label} hint={hint}>
    <input aria-label={label} required={required} type="url" value={settings[k] ?? ''} onChange={e => change({ ...settings, [k]: e.target.value })} /></Field>;
  return <>
    {field('api_base_url', 'REST base URL', 'e.g. https://api.upstox.com', true)}
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
    const base = `${provider.code}_${d.platform === 'MT5' ? 'MT5' : 'API'}_${d.environment}`;
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

function suggestName(d: Row, refs: Record<string, Row[]>): string {
  const provider = refs.providers.find(p => p.id === d.provider_id);
  if (!provider) return '';
  const env = d.environment === 'LIVE' ? 'live' : 'demo';
  if (d.platform === 'MT5') return `${provider.name} MT5 ${env}${d.settings?.server_name ? ` · ${d.settings.server_name}` : ''}`.slice(0, 120);
  return `${provider.name} API ${env}`.slice(0, 120);
}

function RecordForm({ entity, draft, setDraft, editing, refs }: { entity: Entity; draft: Row; setDraft: (r: Row) => void; editing: boolean; refs: Record<string, Row[]> }) {
  const set = (k: string, v: unknown) => {
    const next: Row = { ...draft, [k]: v };
    if (k === 'code') next._codeEdited = v !== '';
    else if (!editing && !next._codeEdited && 'code' in next) next.code = suggestId(entity, next, refs);
    if (k === 'name') next._nameEdited = v !== '';
    else if (!editing && !next._nameEdited && entity === 'connections') next.name = suggestName(next, refs);
    setDraft(next);
  };
  const locked = (k: string) => editing && IMMUTABLE[entity].includes(k);
  const text = (k: string, label: string, props: Row = {}, hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : hint}><input aria-label={label} value={draft[k] ?? ''} readOnly={locked(k)} onChange={e => set(k, e.target.value)} {...props} /></Field>;
  const pick = (k: string, label: string, options: (string | [string, string])[], hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : hint}><Choice label={label} value={draft[k] ?? ''} options={options} disabled={locked(k)} onChange={v => set(k, v)} /></Field>;
  const ref = (k: string, label: string, rows: Row[], show: (r: Row) => string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : undefined}><select aria-label={label} required value={draft[k] ?? ''} disabled={locked(k)} onChange={e => set(k, e.target.value)}>
      <option value="" disabled>Select…</option>{rows.map(r => <option key={r.id} value={r.id}>{show(r)}</option>)}</select></Field>;
  const status = pick('status', 'Status', ['ACTIVE', 'INACTIVE']);
  const code = (label: string, example: string) => text('code', label, { required: true, maxLength: 32, pattern: '[A-Za-z][A-Za-z0-9_]{1,31}' },
    `Suggested; fixed after saving. e.g. ${example}`);

  if (entity === 'providers') return <>
    {text('name', 'Provider name', { required: true, maxLength: 120 }, 'Brand and legal entity, e.g. IC Markets (Raw Trading Ltd), Upstox (RKSV Securities)')}
    {code('Provider ID', 'ICMARKETS, EXNESS, UPSTOX')}
    {pick('kind', 'Provider type', [['BROKER', 'Broker'], ['EXCHANGE', 'Exchange'], ['DATA_VENDOR', 'Data vendor']])}{status}</>;

  if (entity === 'connections') {
    const platform = (v: string) => setDraft({ ...draft, platform: v, settings: v === 'MT5' ? blank.connections.settings : { api_base_url: '', stream_url: '', auth_method: 'API_KEY' } });
    return <>
      <Section n={1} title="Broker">
        {ref('provider_id', 'Provider', refs.providers, r => `${r.name} (${r.code})`)}
        <div className="cfg-pair">
          <Field label="Platform" hint={locked('platform') ? 'Fixed after creation' : undefined}><Choice label="Platform" value={draft.platform} disabled={locked('platform')} options={[['MT5', 'MetaTrader 5'], ['REST_API', 'Broker API']]} onChange={platform} /></Field>
          {pick('environment', 'Environment', [['DEMO', 'Demo'], ['LIVE', 'Live']])}
        </div>
      </Section>
      <Section n={2} title={draft.platform === 'MT5' ? 'Server and addresses' : 'API app'}>
        {draft.platform === 'MT5' ? <Mt5Server settings={draft.settings} change={s => set('settings', s)} />
          : <RestSettings settings={draft.settings} change={s => set('settings', s)} />}
      </Section>
      <Section n={3} title="Name">
        <div className="cfg-pair">
          {text('name', 'Connection name', { required: true, maxLength: 120 })}
          {code('Connection ID', 'VTMARKETS_MT5_LIVE')}
        </div>
        <details className="cfg-more"><summary>More options</summary>
          <div className="cfg-pair">
            {pick('purpose', 'Used for', [['BOTH', 'Market data and trading'], ['MARKET_DATA', 'Market data only'], ['TRADING', 'Trading only']])}
            {status}
          </div>
        </details>
      </Section></>;
  }

  if (entity === 'accounts') {
    const connection = refs.connections.find(c => c.id === draft.connection_id);
    return <>{ref('connection_id', 'Connection', refs.connections, r => `${r.code} · ${r.platform} · ${r.environment}`)}
      {connection && <p className="cfg-note">Environment <Env value={connection.environment} /> from the connection.</p>}
      {text('external_account_id', connection?.platform === 'MT5' ? 'Broker login (MT5 account number)' : 'Broker client ID',
        { required: true, maxLength: 64 }, connection?.platform === 'MT5' ? 'The number you log in to MT5 with, e.g. 51234567' : 'The broker\'s client code for this account, e.g. Upstox UCC')}
      {text('holder_name', 'Account holder', { maxLength: 120 }, 'Person or company that owns the account at the broker, e.g. Yatheesh N S or Coreeye Group Pvt Ltd')}
      {code('Account ID', 'ICMARKETS_LIVE_51234567')}
      {text('name', 'Account name', { required: true, maxLength: 120 }, 'What the account is for, e.g. Gold main, Hedge, Scalping test')}
      {text('base_currency', 'Account currency', { required: true, maxLength: 10 }, 'Deposit currency at the broker, e.g. USD, INR')}
      {pick('trading_permission', 'Trading permission', [['DISABLED', 'Disabled'], ['READ_ONLY', 'Read only (data and account state)'], ['TRADING', 'Trading']],
        'Orders also need order sending switched on for the environment')}{status}</>;
  }

  if (entity === 'instruments') return <>
    {text('symbol', 'Symbol', { required: true, maxLength: 32 }, 'COREI canonical symbol, e.g. XAUUSD')}{text('name', 'Name', { required: true, maxLength: 120 })}
    {pick('asset_class', 'Asset class', ['FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION'])}
    <div className="cfg-pair">{text('base_currency', 'Base currency', { required: true })}{text('quote_currency', 'Quote currency', { required: true })}</div>
    <div className="cfg-pair">{text('price_precision', 'Price decimals', { required: true, type: 'number', min: 0, max: 12 })}{text('tick_size', 'Tick size', { required: true, inputMode: 'decimal' })}</div>
    <div className="cfg-pair">{text('quantity_precision', 'Quantity decimals', { required: true, type: 'number', min: 0, max: 12 })}{text('quantity_step', 'Quantity step', { required: true, inputMode: 'decimal' })}</div>
    <div className="cfg-pair">{text('min_quantity', 'Minimum quantity', { required: true, inputMode: 'decimal' })}{text('contract_size', 'Contract size', { required: true, inputMode: 'decimal' })}</div>
    {text('contract_unit', 'Contract unit', { required: true, maxLength: 32 }, 'e.g. oz, units, barrels')}
    {text('trading_session', 'Trading session', { maxLength: 200 }, 'Optional description, e.g. Sun 23:00 – Fri 22:00 UTC')}{status}</>;

  return <>{ref('connection_id', 'Connection', refs.connections, r => `${r.code} · ${r.purpose}`)}
    {ref('instrument_id', 'Instrument', refs.instruments, r => `${r.symbol} · ${r.name}`)}
    {text('broker_symbol', 'Broker symbol', { required: true, maxLength: 64 }, 'As shown in the broker platform, e.g. XAUUSD.r')}
    {pick('usage', 'Used for', [['BOTH', 'Market data and trading'], ['MARKET_DATA', 'Market data only'], ['TRADING', 'Trading only']])}
    <div className="cfg-pair">{text('broker_contract_size', 'Broker contract size', { inputMode: 'decimal' }, 'Only if it differs from the instrument')}
      {text('broker_price_digits', 'Broker price digits', { type: 'number', min: 0, max: 12 }, 'Optional')}</div>{status}</>;
}

/* ---------- credentials (write-only) ---------- */

function CredentialPanel({ owner, row, reload }: { owner: 'accounts' | 'connections'; row: Row; reload: () => void }) {
  const kind = owner === 'accounts' ? 'MT5_LOGIN' : row.settings?.auth_method === 'OAUTH2' ? 'OAUTH_CLIENT' : 'API_KEY';
  const empty = { password: '', access_level: 'FULL', api_key: '', api_secret: '', client_id: '', client_secret: '' };
  const [secret, setSecret] = useState(empty), [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [error, setError] = useState('');
  const saved = row.credential_kind;
  async function save(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setMessage('');
    const credential = kind === 'MT5_LOGIN' ? { kind, password: secret.password, access_level: secret.access_level }
      : kind === 'API_KEY' ? { kind, api_key: secret.api_key, api_secret: secret.api_secret } : { kind, client_id: secret.client_id, client_secret: secret.client_secret };
    try { await api(`config/${owner}/${row.id}/credential`, 'PUT', { credential }); setSecret(empty); setMessage('Credential saved and encrypted.'); reload(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!window.confirm('Remove the stored credential? The adapter will not be able to log in until a new one is saved.')) return;
    setBusy(true); setError('');
    try { await api(`config/${owner}/${row.id}/credential`, 'DELETE'); setMessage('Credential removed.'); reload(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const input = (k: keyof typeof empty, label: string) => <Field label={label}><input aria-label={label} required type="password" autoComplete="new-password" maxLength={4096}
    value={secret[k]} onChange={e => setSecret({ ...secret, [k]: e.target.value })} /></Field>;
  return <form className="cfg-credential" onSubmit={save}>
    <h3>{owner === 'accounts' ? 'MT5 login' : kind === 'API_KEY' ? 'API key' : 'OAuth client'}</h3>
    <p className="cfg-note">{saved ? <>Saved{row.credential_access_level === 'READ_ONLY' ? ' · investor (read-only) password' : ''} · updated {new Date(row.credential_updated_at).toLocaleString()}. Stored encrypted; it cannot be viewed, only replaced.</>
      : 'Not saved.'}</p>
    {kind === 'MT5_LOGIN' && <>{input('password', saved ? 'New password' : 'Password')}
      <Field label="Password type"><Choice label="Password type" value={secret.access_level} options={[['FULL', 'Master password (can trade)'], ['READ_ONLY', 'Investor password (read only)']]}
        onChange={v => setSecret({ ...secret, access_level: v })} /></Field></>}
    {kind === 'API_KEY' && <>{input('api_key', 'API key')}{input('api_secret', 'API secret')}</>}
    {kind === 'OAUTH_CLIENT' && <>{input('client_id', 'Client ID')}{input('client_secret', 'Client secret')}</>}
    {error && <p role="alert" className="cfg-error">{error}</p>}{message && <p role="status" className="cfg-ok">{message}</p>}
    <div className="cfg-actions"><button className="primary" disabled={busy}>{saved ? 'Replace credential' : 'Save credential'}</button>
      {saved && <button type="button" disabled={busy} onClick={() => void remove()}>Remove</button>}</div>
  </form>;
}

/* ---------- record list + detail ---------- */

const COLUMNS: Record<Entity, [string, (r: Row) => ReactNode][]> = {
  providers: [['Provider ID', r => r.code], ['Provider name', r => r.name], ['Type', r => r.kind], ['Connections', r => r.connection_count], ['Status', r => <Status value={r.status} />]],
  connections: [['Connection', r => <><strong>{r.code}</strong><small className="cfg-note"> {r.name}</small></>], ['Provider', r => r.provider_code],
    ['Platform', r => r.platform === 'MT5' ? 'MT5' : 'API'], ['Env', r => <Env value={r.environment} />],
    ['Server', r => r.platform === 'MT5' ? <>{r.settings.server_name}<small className="cfg-note"> · {r.settings.access_points.filter((p: Row) => p.enabled !== false).length} access points</small></> : r.settings.api_base_url],
    ['Accounts', r => r.account_count],
    ['Status', r => <><Status value={r.status} />{r.platform !== 'MT5' && !r.credential_kind && <> <span className="cfg-pill warn">No API key</span></>}</>]],
  accounts: [['Account ID', r => r.code], ['Holder', r => r.holder_name || '—'], ['Provider', r => r.provider_code], ['Connection', r => r.connection_code], ['Env', r => <Env value={r.environment} />],
    ['Broker login', r => r.external_account_id], ['Ccy', r => r.base_currency], ['Permission', r => <Status value={r.trading_permission} />],
    ['Data', r => <Ready blockers={r.data_blockers} />], ['Trading', r => <Ready blockers={r.trading_blockers} />], ['Status', r => <Status value={r.status} />]],
  instruments: [['Symbol', r => r.symbol], ['Name', r => r.name], ['Class', r => r.asset_class], ['Tick', r => r.tick_size],
    ['Min / step', r => `${r.min_quantity} / ${r.quantity_step}`], ['Contract', r => `${r.contract_size} ${r.contract_unit}`], ['Mappings', r => r.mapping_count], ['Status', r => <Status value={r.status} />]],
  mappings: [['Instrument', r => r.instrument_symbol], ['Provider', r => r.provider_code], ['Connection', r => r.connection_code], ['Env', r => <Env value={r.environment} />],
    ['Broker symbol', r => r.broker_symbol], ['Used for', r => r.usage], ['Contract override', r => r.broker_contract_size ?? '—'], ['Status', r => <Status value={r.status} />]],
};

function Records({ entity, refs, reloadRefs, canEdit }: { entity: Entity; refs: Record<string, Row[]>; reloadRefs: () => void; canEdit: boolean }) {
  const [rows, setRows] = useState<Row[] | null>(null), [q, setQ] = useState(''), [parent, setParent] = useState('');
  const [selected, setSelected] = useState<Row | null>(null), [draft, setDraft] = useState<Row | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [loadError, setLoadError] = useState('');
  const parentFilter = entity === 'connections' ? ['provider_id', refs.providers, (r: Row) => r.code] as const
    : entity === 'accounts' ? ['connection_id', refs.connections, (r: Row) => r.code] as const
    : entity === 'mappings' ? ['connection_id', refs.connections, (r: Row) => r.code] as const : null;
  async function load(keepId?: string) {
    try {
      const params = new URLSearchParams(); if (q) params.set('q', q); if (parentFilter && parent) params.set(parentFilter[0], parent);
      const data = await api(`config/${entity}?${params}`); setRows(data.items); setLoadError('');
      if (keepId) { const fresh = data.items.find((r: Row) => r.id === keepId); setSelected(fresh ?? null); if (fresh) setDraft({ ...fresh }); }
    } catch (e) { setLoadError((e as Error).message); }
  }
  useEffect(() => { const t = setTimeout(() => void load(selected?.id), q ? 250 : 0); return () => clearTimeout(t); }, [entity, q, parent]);
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
      const saved = selected ? await api(`config/${entity}/${selected.id}`, 'PUT', { ...body, version: selected.version }) : await api(`config/${entity}`, 'POST', body);
      setNotice(selected ? 'Changes saved.' : `${SINGULAR[entity][0].toUpperCase() + SINGULAR[entity].slice(1)} created.`);
      setSelected(saved); setDraft({ ...saved }); await load(saved.id); reloadRefs();
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function remove() {
    if (!selected || !window.confirm(`Delete this ${SINGULAR[entity]}? This cannot be undone. Records that depend on it must be removed first.`)) return;
    setBusy(true); setError('');
    try { await api(`config/${entity}/${selected.id}?version=${selected.version}`, 'DELETE'); setSelected(null); setDraft(null); setNotice('Deleted.'); await load(); reloadRefs(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const columns = COLUMNS[entity];
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar">
        <input aria-label="Search" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
        {parentFilter && <select aria-label="Filter" value={parent} onChange={e => setParent(e.target.value)}>
          <option value="">All {parentFilter[0] === 'provider_id' ? 'providers' : 'connections'}</option>
          {parentFilter[1].map(r => <option key={r.id} value={r.id}>{parentFilter[2](r)}</option>)}</select>}
        {canEdit ? <button className="primary" onClick={() => open(null)}>New {SINGULAR[entity]}</button> : <span className="cfg-note">Read only · needs Trading operations to change</span>}
      </div>
      {loadError && <p role="alert" className="cfg-error">{loadError}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No {entity === 'mappings' ? 'symbol mappings' : entity} yet. {canEdit ? `Choose New ${SINGULAR[entity]} to add one.` : ''}</p> :
        <div className="cfg-scroll"><table className="cfg-table"><thead><tr>{columns.map(([h]) => <th key={h}>{h}</th>)}</tr></thead><tbody>
          {rows.map(r => <tr key={r.id} className={selected?.id === r.id ? 'selected' : ''} tabIndex={0} onClick={() => open(r)} onKeyDown={e => { if (e.key === 'Enter') open(r); }}>
            {columns.map(([h, cell]) => <td key={h}>{cell(r)}</td>)}</tr>)}
        </tbody></table></div>}
    </div>
    {draft && <FloatingWindow storageKey={entity === 'connections' ? 'corei-os-mini.connection-window.v1' : 'corei-os-mini.config-window.v1'}
      width={entity === 'connections' ? 760 : 560}
      footer={canEdit ? <div className="cfg-actions">
          <button className="primary" form="cfg-record-form" disabled={busy || !dirty}>{selected ? 'Save changes' : `Create ${SINGULAR[entity]}`}</button>
          {selected && <button type="button" className="danger" disabled={busy} onClick={() => void remove()}>Delete</button>}</div>
        : <p className="cfg-note">Read only. Changes need the Trading operations role.</p>}
      title={selected ? `${SINGULAR[entity][0].toUpperCase()}${SINGULAR[entity].slice(1)} · ${selected.code ?? selected.symbol ?? selected.broker_symbol}` : `New ${SINGULAR[entity]}`}
      subtitle={selected ? `Version ${selected.version} · updated ${new Date(selected.updated_at).toLocaleString()}${dirty ? ' · unsaved changes' : ''}` : dirty ? 'Unsaved' : undefined}
      onClose={() => { if (!dirty || window.confirm('Discard unsaved changes?')) { setSelected(null); setDraft(null); } }}>
      <div className="cfg-detail">
      {entity === 'accounts' && selected && <div className="cfg-blockers">
        <h3>Readiness</h3>
        {(['data_blockers', 'trading_blockers'] as const).map(k => <div key={k}><strong>{k === 'data_blockers' ? 'Market data and account state' : 'Order sending'}</strong>
          {selected[k].length ? <ul>{selected[k].map((b: string) => <li key={b}>{BLOCKERS[b] ?? b}</li>)}</ul> : <p className="cfg-ok">Ready</p>}</div>)}
      </div>}
      <form className="cfg-form" id="cfg-record-form" onSubmit={save}>
        <fieldset className="cfg-plain" disabled={!canEdit}>
          <RecordForm entity={entity} draft={draft} setDraft={setDraft} editing={!!selected} refs={refs} />
        </fieldset>
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
      </form>
      {canEdit && selected && entity === 'accounts' && selected.platform === 'MT5' && <CredentialPanel owner="accounts" row={selected} reload={() => void load(selected.id)} />}
      {canEdit && selected && entity === 'connections' && selected.platform === 'REST_API' && <CredentialPanel owner="connections" row={selected} reload={() => void load(selected.id)} />}
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

function ParameterFields({ schema, value, change, disabled }: { schema: Row; value: Row; change: (v: Row) => void; disabled?: boolean }) {
  return <>{Object.entries<Row>(schema?.properties ?? {}).map(([key, prop]) => {
    const label = detailLabel(key);
    return <Field key={key} label={label} hint={prop.description}>{prop.enum
      ? <Choice label={label} value={value[key] ?? ''} disabled={disabled} options={['', ...prop.enum].map(v => [v, v || 'Select…'] as [string, string])} onChange={v => change({ ...value, [key]: v })} />
      : <input aria-label={label} required disabled={disabled} type={prop.type === 'integer' || prop.type === 'number' ? 'number' : 'text'} min={prop.minimum} max={prop.maximum}
          value={value[key] ?? ''} onChange={e => change({ ...value, [key]: prop.type === 'integer' ? (e.target.value === '' ? '' : Number(e.target.value)) : e.target.value })} />}</Field>;
  })}</>;
}

function paramSummary(p: Row) { return Object.entries(p ?? {}).map(([k, v]) => `${detailLabel(k)} ${v}`).join(' · '); }

function Strategies({ roles, reloadRefs }: { roles: string[]; reloadRefs: () => void }) {
  const canEdit = roles.includes('TRADING_OPS'), canApprove = roles.includes('RISK_APPROVER');
  const [rows, setRows] = useState<Row[] | null>(null), [algos, setAlgos] = useState<Row[]>([]), [q, setQ] = useState('');
  const [draft, setDraft] = useState<Row | null>(null), [versions, setVersions] = useState<Row[]>([]), [form, setForm] = useState<Row | null>(null);
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
    setDraft(row ? { ...row } : { code: '', name: '', description: '', status: 'ACTIVE' });
    setVersions([]); if (row) void (async () => setVersions((await api(`config/strategies/${row.id}/versions`)).items))();
  }
  const algo = algos.find(a => a.id === form?.algorithm);
  async function saveVersion(e: FormEvent) {
    e.preventDefault(); if (!form || !draft) return;
    const body = { algorithm: form.algorithm, parameters: form.parameters, notes: form.notes ?? '' };
    await run(() => form.id ? api(`config/strategy-versions/${form.id}`, 'PUT', { ...body, version: form.version })
      : api(`config/strategies/${draft.id}/versions`, 'POST', body), form.id ? 'Draft version saved.' : 'Draft version created. A risk approver must approve it before it can trade.', draft.id);
    setForm(null);
  }
  return <div className="cfg-records">
    <div className="cfg-list">
      <div className="cfg-toolbar"><input aria-label="Search" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
        {canEdit ? <button className="primary" onClick={() => open(null)}>New strategy</button> : <span className="cfg-note">Read only</span>}</div>
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
      <form className="cfg-form" onSubmit={e => { e.preventDefault(); void run(() => draft.id ? api(`config/strategies/${draft.id}`, 'PUT', { code: draft.code, name: draft.name, description: draft.description, status: draft.status, version: draft.version })
        : api('config/strategies', 'POST', { code: draft.code, name: draft.name, description: draft.description }), draft.id ? 'Strategy saved.' : 'Strategy created. Add a version with its rules next.'); }}>
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
        <div className="cfg-points-head"><strong>Versions</strong>{canEdit && draft.status === 'ACTIVE' && !form && <button type="button" onClick={() => setForm({ algorithm: algos[0]?.id ?? '', parameters: {}, notes: '' })}>New version</button>}</div>
        {form && <form className="cfg-subform" onSubmit={saveVersion} aria-label="Version form">
          <strong>{form.id ? `Edit draft v${form.number}` : 'New draft version'}</strong>
          <Field label="Algorithm" hint={algo?.description}><Choice label="Algorithm" value={form.algorithm} options={algos.map(a => [a.id, a.label] as [string, string])} onChange={v => setForm({ ...form, algorithm: v, parameters: {} })} /></Field>
          {algo && <ParameterFields schema={algo.schema} value={form.parameters} change={v => setForm({ ...form, parameters: v })} />}
          <Field label="Notes"><input aria-label="Notes" maxLength={2000} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></Field>
          <div className="cfg-actions"><button className="primary" disabled={busy}>{form.id ? 'Save draft' : 'Create draft'}</button><button type="button" onClick={() => setForm(null)}>Cancel</button></div>
        </form>}
        {versions.length === 0 ? <p className="cfg-note">No versions yet.</p> : <table className="cfg-table compact"><thead><tr><th>Version</th><th>Rules</th><th>State</th><th>By</th><th /></tr></thead><tbody>
          {versions.map(v => <tr key={v.id}><td>v{v.number}</td><td><strong>{algos.find(a => a.id === v.algorithm)?.label ?? v.algorithm}</strong><br /><small>{paramSummary(v.parameters)}</small>{v.notes && <><br /><small className="cfg-note">{v.notes}</small></>}</td>
            <td><span className={`cfg-pill ${v.state === 'APPROVED' ? 'ok' : v.state === 'RETIRED' ? 'off' : 'warn'}`}>{v.state}</span></td>
            <td><small>created {v.created_by}{v.approved_by ? <><br />approved {v.approved_by}</> : null}</small></td>
            <td className="cfg-row-actions">
              {v.state === 'DRAFT' && canEdit && <button type="button" onClick={() => setForm({ ...v })}>Edit</button>}
              {v.state === 'DRAFT' && canApprove && <button type="button" disabled={busy} onClick={() => { if (window.confirm(`Approve v${v.number}? Its rules and parameters can no longer be changed.`)) void run(() => api(`config/strategy-versions/${v.id}/approve`, 'POST', { version: v.version }), `v${v.number} approved.`, draft.id); }}>Approve</button>}
              {v.state !== 'RETIRED' && (canEdit || canApprove) && <button type="button" disabled={busy} onClick={() => { if (window.confirm(`Retire v${v.number}? It cannot be used again.`)) void run(() => api(`config/strategy-versions/${v.id}/retire`, 'POST', { version: v.version }), `v${v.number} retired.`, draft.id); }}>Retire</button>}
            </td></tr>)}
        </tbody></table>}
        <small>Approved versions cannot be changed; create a new version to change rules or parameters.</small>
      </section>}
      </div>
    </FloatingWindow>}
  </div>;
}

/* ---------- deployments ---------- */

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
    data_mapping_id: d.data_mapping_id, allocation_weight_bps: Number(d.allocation_weight_bps), note: d.note ?? '' });
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
        {canEdit ? <button className="primary" onClick={() => { setError(''); setNotice(''); setDraft({ strategy_id: filters.strategy_id ?? '', strategy_version_id: '', instrument_id: filters.instrument_id ?? '', account_id: filters.account_id ?? '', data_mapping_id: '', allocation_weight_bps: 10000, note: '' }); }}>New deployment</button>
          : <span className="cfg-note">Read only</span>}
      </div>
      {error && !draft && <p role="alert" className="cfg-error">{error}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No deployments match. A strategy trades only where a deployment row exists and is active.</p> :
        <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Strategy</th><th>Instrument</th><th>Account</th><th>Env</th><th>Market data from</th><th>Weight</th><th>Status</th><th>Ready</th></tr></thead><tbody>
          {rows.map(r => <tr key={r.id} tabIndex={0} className={draft?.id === r.id ? 'selected' : ''} onClick={() => { setError(''); setNotice(''); setReason(''); setDraft({ ...r }); }}>
            <td>{r.strategy_code} v{r.version_number}</td><td>{r.instrument_symbol}</td><td>{r.provider_code} · {r.account_code}</td><td><Env value={r.environment} /></td>
            <td>{r.data_provider_code} · {r.data_connection_code} · {r.data_symbol}</td><td>{(r.allocation_weight_bps / 100).toFixed(2)}%</td>
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
    <Field label="New password" hint="At least 12 characters, not containing your username"><input required minLength={12} type="password" autoComplete="new-password" value={next} onChange={e => setNext(e.target.value)} /></Field>
    <Field label="Repeat new password"><input required minLength={12} type="password" autoComplete="new-password" value={again} onChange={e => setAgain(e.target.value)} /></Field>
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
          : <Field label="Temporary password" hint="At least 12 characters. The user must change it at first sign-in."><input aria-label="Temporary password" required minLength={12} type="password" autoComplete="new-password" value={draft.temporary_password} onChange={e => setDraft({ ...draft, temporary_password: e.target.value })} /></Field>}
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
        <div className="cfg-actions"><button className="primary" disabled={busy || !draft.roles.length}>{draft.id ? 'Save changes' : 'Create user'}</button></div>
      </form>
      {draft.id && !self && <form className="cfg-credential" onSubmit={e => { e.preventDefault(); void resetPassword(); }}>
        <h3>Reset password</h3><p className="cfg-note">Sets a temporary password, unlocks the account and ends the user's sessions.</p>
        <Field label="New temporary password"><input aria-label="New temporary password" required minLength={12} type="password" autoComplete="new-password" value={reset} onChange={e => setReset(e.target.value)} /></Field>
        <div className="cfg-actions"><button disabled={busy}>Reset password</button></div></form>}
    </FloatingWindow>}
  </div>;
}

/* ---------- workspace ---------- */

export default function Configuration() {
  const [user, setUser] = useState<Session | null>(null), [checking, setChecking] = useState(true), [changing, setChanging] = useState(false);
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState('');
  const [section, setSection] = useState<Section>('providers'), [refs, setRefs] = useState<Record<string, Row[]>>({ providers: [], connections: [], instruments: [] });
  const [summary, setSummary] = useState<Row | null>(null);
  async function loadRefs() {
    try {
      const [p, c, i, s] = await Promise.all(['providers', 'connections', 'instruments', 'summary'].map(x => api('config/' + x)));
      setRefs({ providers: p.items, connections: c.items, instruments: i.items }); setSummary(s);
    } catch (e) { if ((e as any).status === 401) setUser(null); else setError((e as Error).message); }
  }
  useEffect(() => { void (async () => {
    try { setUser(asSession(await api('operator/session'))); } catch { setUser(null); } finally { setChecking(false); }
  })(); }, []);
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
  const current = sections.find(s => s.id === section) ?? sections[0];
  const live = summary?.execution_controls?.find((c: Row) => c.environment === 'LIVE');
  return <div className="cfg">
    <div className="cfg-topbar"><span className="eyebrow">CONFIGURATION</span>
    <nav className="cfg-nav" aria-label="Configuration sections">
      {sections.map(s => <button key={s.id} aria-current={current.id === s.id ? 'page' : undefined} onClick={() => {
        if (window.dispatchEvent(new Event('corei-before-layout-change', { cancelable: true }))) setSection(s.id);
      }}><span>{s.label}</span>{summary && s.id in (summary.counts ?? {}) && <small>{summary.counts[s.id]}</small>}
        {s.id === 'controls' && live && <small className={live.trading_enabled ? 'cfg-live-on' : ''}>{live.trading_enabled ? 'LIVE ON' : 'off'}</small>}</button>)}
    </nav>
    <div className="cfg-user" title={user.roles.map(r => ROLE_LABEL[r]).join(', ')}>{user.display_name || user.username}
      <small> · {user.roles.map(r => ROLE_LABEL[r]).join(', ')}</small> · <button className="link" onClick={() => setChanging(true)}>Change password</button> · <button className="link" onClick={() => void signOut()}>Sign out</button></div></div>
    <section className="cfg-main">
      <header className="cfg-head"><h1>{current.label}</h1><p>{current.hint}</p></header>
      {summary && !summary.credential_storage_ready && <p role="alert" className="cfg-error">Credential encryption is not set up on the server. Run scripts/setup-broker-accounts.sh.</p>}
      {error && <p role="alert" className="cfg-error">{error}</p>}
      {current.id === 'controls' ? <OrderSending changed={() => void loadRefs()} roles={user.roles} /> : current.id === 'audit' ? <AuditLog />
        : current.id === 'users' ? <Users me={user} />
        : current.id === 'strategies' ? <Strategies roles={user.roles} reloadRefs={() => void loadRefs()} />
        : current.id === 'deployments' ? <Deployments roles={user.roles} refs={refs} />
        : <Records key={current.id} entity={current.id as Entity} refs={refs} reloadRefs={() => void loadRefs()} canEdit={user.roles.includes('TRADING_OPS')} />}
    </section>
  </div>;
}
