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
type Section = 'providers' | 'connections' | 'accounts' | 'instruments' | 'mappings' | 'controls' | 'audit';
type Entity = Exclude<Section, 'controls' | 'audit'>;

const SECTIONS: { id: Section; label: string; hint: string }[] = [
  { id: 'providers', label: 'Providers', hint: 'Brokers, exchanges and data vendors' },
  { id: 'connections', label: 'Connections', hint: 'Platform, environment and endpoints per provider' },
  { id: 'accounts', label: 'Accounts', hint: 'Broker accounts, login and trading permission' },
  { id: 'instruments', label: 'Instruments', hint: 'Canonical instruments and contract specifications' },
  { id: 'mappings', label: 'Symbol mappings', hint: 'Broker symbol for each instrument on a connection' },
  { id: 'controls', label: 'Order sending', hint: 'Firm-wide switch per environment' },
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

/* ---------- forms per entity ---------- */

const blank: Record<Entity, Row> = {
  providers: { code: '', name: '', kind: 'BROKER', status: 'ACTIVE' },
  connections: { provider_id: '', code: '', name: '', platform: 'MT5', environment: 'DEMO', purpose: 'BOTH', status: 'ACTIVE',
    settings: { server_name: '', access_points: [{ name: '', host: '', port: 443 }] } },
  accounts: { connection_id: '', code: '', name: '', external_account_id: '', base_currency: 'USD', trading_permission: 'DISABLED', status: 'ACTIVE' },
  instruments: { symbol: '', name: '', asset_class: 'FX', base_currency: '', quote_currency: '', price_precision: 5, quantity_precision: 2,
    tick_size: '0.00001', min_quantity: '0.01', quantity_step: '0.01', contract_size: '100000', contract_unit: 'units', trading_session: '', status: 'ACTIVE' },
  mappings: { connection_id: '', instrument_id: '', broker_symbol: '', usage: 'BOTH', broker_contract_size: '', broker_price_digits: '', status: 'ACTIVE' },
};
const FIELDS: Record<Entity, string[]> = {
  providers: ['code', 'name', 'kind', 'status'],
  connections: ['provider_id', 'code', 'name', 'platform', 'environment', 'purpose', 'settings', 'status'],
  accounts: ['connection_id', 'code', 'name', 'external_account_id', 'base_currency', 'trading_permission', 'status'],
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
    out.settings = { server_name: draft.settings.server_name, access_points: draft.settings.access_points.map((p: Row) => ({ ...p, port: Number(p.port) })) };
  if (entity === 'connections' && draft.platform === 'REST_API') {
    const s = draft.settings;
    out.settings = { api_base_url: s.api_base_url, auth_method: s.auth_method, stream_url: s.stream_url || null,
      oauth_redirect_url: s.auth_method === 'OAUTH2' ? s.oauth_redirect_url || null : null };
  }
  return out;
}

function Mt5Endpoints({ settings, change, disabled }: { settings: Row; change: (s: Row) => void; disabled: boolean }) {
  const [servers, setServers] = useState<Row[] | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const points: Row[] = settings.access_points ?? [];
  const setPoint = (i: number, patch: Row) => change({ ...settings, access_points: points.map((p, j) => j === i ? { ...p, ...patch } : p) });
  const move = (i: number, d: number) => { const next = [...points]; const [x] = next.splice(i, 1); next.splice(i + d, 0, x); change({ ...settings, access_points: next }); };
  async function read(file: File) {
    setBusy(true); setError('');
    try { setServers((await api('config/mt5/server-file', 'POST', undefined, file)).servers); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <fieldset className="cfg-subform" disabled={disabled}>
    <legend>MT5 server</legend>
    <Field label="Broker server name" hint="Exactly as the broker supplies it, e.g. ICMarketsSC-Demo">
      <input required maxLength={120} value={settings.server_name ?? ''} onChange={e => change({ ...settings, server_name: e.target.value })} /></Field>
    <Field label="Read access points from servers.dat" hint="Optional. Fills the list below from the broker's MT5 server file; nothing is stored until you save.">
      <input type="file" accept=".dat" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (f) void read(f); e.target.value = ''; }} /></Field>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {servers && <Field label={`Servers in file (${servers.length})`}>
      <select aria-label="Server from file" defaultValue="" onChange={e => { const s = servers.find(x => x.server_name === e.target.value); if (s) { change({ server_name: s.server_name, access_points: s.access_points }); setServers(null); } }}>
        <option value="" disabled>Choose the server for this connection…</option>
        {servers.map(s => <option key={s.server_name} value={s.server_name}>{s.server_name} · {s.access_points.length} access points</option>)}
      </select></Field>}
    <div className="cfg-points-head"><strong>Access points ({points.length})</strong>
      {points.length > 1 && <button type="button" onClick={() => { if (window.confirm('Remove all access points except the first?')) change({ ...settings, access_points: points.slice(0, 1) }); }}>Keep first only</button>}</div>
    <div className="cfg-points"><table className="cfg-table compact"><thead><tr><th>#</th><th>Access point</th><th>Host</th><th>Port</th><th /></tr></thead><tbody>
      {points.map((p, i) => <tr key={i}><td>{i + 1}</td>
        <td><input aria-label={`Access point ${i + 1} name`} required value={p.name} onChange={e => setPoint(i, { name: e.target.value })} /></td>
        <td><input aria-label={`Access point ${i + 1} host`} required value={p.host} onChange={e => setPoint(i, { host: e.target.value })} /></td>
        <td><input aria-label={`Access point ${i + 1} port`} required type="number" min={1} max={65535} value={p.port} onChange={e => setPoint(i, { port: e.target.value })} /></td>
        <td className="cfg-row-actions"><button type="button" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Move up">↑</button>
          <button type="button" disabled={i === points.length - 1} onClick={() => move(i, 1)} aria-label="Move down">↓</button>
          <button type="button" disabled={points.length === 1} onClick={() => change({ ...settings, access_points: points.filter((_, j) => j !== i) })} aria-label="Remove">✕</button></td></tr>)}
    </tbody></table></div>
    <button type="button" disabled={points.length >= 256} onClick={() => change({ ...settings, access_points: [...points, { name: '', host: '', port: 443 }] })}>Add access point</button>
    <small>The adapter tries access points in this order.</small>
  </fieldset>;
}

function RestSettings({ settings, change, disabled }: { settings: Row; change: (s: Row) => void; disabled: boolean }) {
  return <fieldset className="cfg-subform" disabled={disabled}>
    <legend>API endpoints</legend>
    <Field label="REST base URL" hint="https:// only"><input required type="url" value={settings.api_base_url ?? ''} onChange={e => change({ ...settings, api_base_url: e.target.value })} /></Field>
    <Field label="Streaming URL" hint="Optional, wss:// only"><input type="url" value={settings.stream_url ?? ''} onChange={e => change({ ...settings, stream_url: e.target.value })} /></Field>
    <Field label="Authentication"><Choice label="Authentication" value={settings.auth_method ?? 'API_KEY'} options={[['API_KEY', 'API key and secret'], ['OAUTH2', 'OAuth 2 client']]}
      onChange={v => change({ ...settings, auth_method: v })} /></Field>
    {settings.auth_method === 'OAUTH2' && <Field label="OAuth redirect URL" hint="Registered with the provider; https:// only">
      <input required type="url" value={settings.oauth_redirect_url ?? ''} onChange={e => change({ ...settings, oauth_redirect_url: e.target.value })} /></Field>}
  </fieldset>;
}

function RecordForm({ entity, draft, setDraft, editing, refs }: { entity: Entity; draft: Row; setDraft: (r: Row) => void; editing: boolean; refs: Record<string, Row[]> }) {
  const set = (k: string, v: unknown) => setDraft({ ...draft, [k]: v });
  const locked = (k: string) => editing && IMMUTABLE[entity].includes(k);
  const text = (k: string, label: string, props: Row = {}, hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : hint}><input aria-label={label} value={draft[k] ?? ''} readOnly={locked(k)} onChange={e => set(k, e.target.value)} {...props} /></Field>;
  const pick = (k: string, label: string, options: (string | [string, string])[], hint?: string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : hint}><Choice label={label} value={draft[k] ?? ''} options={options} disabled={locked(k)} onChange={v => set(k, v)} /></Field>;
  const ref = (k: string, label: string, rows: Row[], show: (r: Row) => string) =>
    <Field label={label} hint={locked(k) ? 'Fixed after creation' : undefined}><select aria-label={label} required value={draft[k] ?? ''} disabled={locked(k)} onChange={e => set(k, e.target.value)}>
      <option value="" disabled>Select…</option>{rows.map(r => <option key={r.id} value={r.id}>{show(r)}</option>)}</select></Field>;
  const status = pick('status', 'Status', ['ACTIVE', 'INACTIVE']);
  const code = (label: string) => text('code', label, { required: true, maxLength: 32, pattern: '[A-Za-z][A-Za-z0-9_]{1,31}' }, 'Letters, digits, underscore; stored upper-case');

  if (entity === 'providers') return <>{code('Provider code')}{text('name', 'Name', { required: true, maxLength: 120 })}
    {pick('kind', 'Type', [['BROKER', 'Broker'], ['EXCHANGE', 'Exchange'], ['DATA_VENDOR', 'Data vendor']])}{status}</>;

  if (entity === 'connections') {
    const platform = (v: string) => setDraft({ ...draft, platform: v, settings: v === 'MT5' ? blank.connections.settings : { api_base_url: '', stream_url: '', auth_method: 'API_KEY' } });
    return <>{ref('provider_id', 'Provider', refs.providers, r => `${r.code} · ${r.name}`)}{code('Connection code')}{text('name', 'Name', { required: true, maxLength: 120 })}
      <Field label="Platform" hint={locked('platform') ? 'Fixed after creation' : undefined}><Choice label="Platform" value={draft.platform} disabled={locked('platform')} options={[['MT5', 'MetaTrader 5'], ['REST_API', 'Broker REST / WebSocket API']]} onChange={platform} /></Field>
      {pick('environment', 'Environment', [['DEMO', 'Demo'], ['LIVE', 'Live']], 'Inherited by every account on this connection')}
      {pick('purpose', 'Purpose', [['BOTH', 'Market data and trading'], ['MARKET_DATA', 'Market data only'], ['TRADING', 'Trading only']])}{status}
      {draft.platform === 'MT5' ? <Mt5Endpoints settings={draft.settings} disabled={false} change={s => set('settings', s)} />
        : <RestSettings settings={draft.settings} disabled={false} change={s => set('settings', s)} />}</>;
  }

  if (entity === 'accounts') {
    const connection = refs.connections.find(c => c.id === draft.connection_id);
    return <>{ref('connection_id', 'Connection', refs.connections, r => `${r.code} · ${r.platform} · ${r.environment}`)}
      {connection && <p className="cfg-note">Environment <Env value={connection.environment} /> from the connection.</p>}
      {code('Account code')}{text('name', 'Name', { required: true, maxLength: 120 })}
      {text('external_account_id', connection?.platform === 'MT5' ? 'MT5 login (account number)' : 'Broker account ID', { required: true, maxLength: 64 })}
      {text('base_currency', 'Account currency', { required: true, maxLength: 10 })}
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
  providers: [['Code', r => r.code], ['Name', r => r.name], ['Type', r => r.kind], ['Connections', r => r.connection_count], ['Status', r => <Status value={r.status} />]],
  connections: [['Code', r => r.code], ['Provider', r => r.provider_code], ['Platform', r => r.platform], ['Env', r => <Env value={r.environment} />],
    ['Purpose', r => r.purpose], ['Endpoint', r => r.platform === 'MT5' ? r.settings.server_name : r.settings.api_base_url],
    ['Credential', r => r.platform === 'MT5' ? 'Per account' : r.credential_kind ? 'Saved' : <span className="cfg-pill warn">Missing</span>],
    ['Accounts', r => r.account_count], ['Status', r => <Status value={r.status} />]],
  accounts: [['Code', r => r.code], ['Provider', r => r.provider_code], ['Connection', r => r.connection_code], ['Env', r => <Env value={r.environment} />],
    ['Account no.', r => r.external_account_id], ['Ccy', r => r.base_currency], ['Permission', r => <Status value={r.trading_permission} />],
    ['Data', r => <Ready blockers={r.data_blockers} />], ['Trading', r => <Ready blockers={r.trading_blockers} />], ['Status', r => <Status value={r.status} />]],
  instruments: [['Symbol', r => r.symbol], ['Name', r => r.name], ['Class', r => r.asset_class], ['Tick', r => r.tick_size],
    ['Min / step', r => `${r.min_quantity} / ${r.quantity_step}`], ['Contract', r => `${r.contract_size} ${r.contract_unit}`], ['Mappings', r => r.mapping_count], ['Status', r => <Status value={r.status} />]],
  mappings: [['Instrument', r => r.instrument_symbol], ['Provider', r => r.provider_code], ['Connection', r => r.connection_code], ['Env', r => <Env value={r.environment} />],
    ['Broker symbol', r => r.broker_symbol], ['Used for', r => r.usage], ['Contract override', r => r.broker_contract_size ?? '—'], ['Status', r => <Status value={r.status} />]],
};

function Records({ entity, refs, reloadRefs }: { entity: Entity; refs: Record<string, Row[]>; reloadRefs: () => void }) {
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
        <button className="primary" onClick={() => open(null)}>New {SINGULAR[entity]}</button>
      </div>
      {loadError && <p role="alert" className="cfg-error">{loadError}</p>}
      {!rows ? <p className="cfg-note">Loading…</p> : rows.length === 0 ? <p className="cfg-empty">No {entity === 'mappings' ? 'symbol mappings' : entity} yet. Choose New {SINGULAR[entity]} to add one.</p> :
        <div className="cfg-scroll"><table className="cfg-table"><thead><tr>{columns.map(([h]) => <th key={h}>{h}</th>)}</tr></thead><tbody>
          {rows.map(r => <tr key={r.id} className={selected?.id === r.id ? 'selected' : ''} tabIndex={0} onClick={() => open(r)} onKeyDown={e => { if (e.key === 'Enter') open(r); }}>
            {columns.map(([h, cell]) => <td key={h}>{cell(r)}</td>)}</tr>)}
        </tbody></table></div>}
    </div>
    {draft && <FloatingWindow storageKey="corei-os-mini.config-window.v1"
      title={selected ? `${SINGULAR[entity][0].toUpperCase()}${SINGULAR[entity].slice(1)} · ${selected.code ?? selected.symbol ?? selected.broker_symbol}` : `New ${SINGULAR[entity]}`}
      subtitle={selected ? `Version ${selected.version} · updated ${new Date(selected.updated_at).toLocaleString()}${dirty ? ' · unsaved changes' : ''}` : dirty ? 'Unsaved' : undefined}
      onClose={() => { if (!dirty || window.confirm('Discard unsaved changes?')) { setSelected(null); setDraft(null); } }}>
      <div className="cfg-detail">
      {entity === 'accounts' && selected && <div className="cfg-blockers">
        <h3>Readiness</h3>
        {(['data_blockers', 'trading_blockers'] as const).map(k => <div key={k}><strong>{k === 'data_blockers' ? 'Market data and account state' : 'Order sending'}</strong>
          {selected[k].length ? <ul>{selected[k].map((b: string) => <li key={b}>{BLOCKERS[b] ?? b}</li>)}</ul> : <p className="cfg-ok">Ready</p>}</div>)}
      </div>}
      <form className="cfg-form" onSubmit={save}>
        <RecordForm entity={entity} draft={draft} setDraft={setDraft} editing={!!selected} refs={refs} />
        {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
        <div className="cfg-actions"><button className="primary" disabled={busy || !dirty}>{selected ? 'Save changes' : `Create ${SINGULAR[entity]}`}</button>
          {selected && <button type="button" className="danger" disabled={busy} onClick={() => void remove()}>Delete</button>}</div>
      </form>
      {selected && entity === 'accounts' && selected.platform === 'MT5' && <CredentialPanel owner="accounts" row={selected} reload={() => void load(selected.id)} />}
      {selected && entity === 'connections' && selected.platform === 'REST_API' && <CredentialPanel owner="connections" row={selected} reload={() => void load(selected.id)} />}
      </div>
    </FloatingWindow>}
  </div>;
}

/* ---------- order sending and audit ---------- */

function OrderSending({ changed }: { changed: () => void }) {
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
      </form> : <button onClick={() => { setEdit(row.environment); setReason(''); setConfirm(''); setError(''); }}>{row.trading_enabled ? 'Turn off…' : 'Turn on…'}</button>}
    </article>)}</div>
  </div>;
}

function AuditLog() {
  const [items, setItems] = useState<Row[] | null>(null), [entity, setEntity] = useState(''), [error, setError] = useState(''), [open, setOpen] = useState<number | null>(null);
  useEffect(() => { void (async () => { try { setItems((await api(`config/audit?limit=500${entity ? '&entity=' + encodeURIComponent(entity) : ''}`)).items); } catch (e) { setError((e as Error).message); } })(); }, [entity]);
  return <div className="cfg-list">
    <div className="cfg-toolbar"><select aria-label="Audit entity" value={entity} onChange={e => setEntity(e.target.value)}>
      <option value="">All records</option>{['provider', 'connection', 'account', 'instrument', 'symbol mapping', 'execution control'].map(x => <option key={x}>{x}</option>)}</select></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {!items ? <p className="cfg-note">Loading…</p> : items.length === 0 ? <p className="cfg-empty">No changes recorded yet.</p> :
      <div className="cfg-scroll"><table className="cfg-table"><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Record</th><th>Reason</th></tr></thead><tbody>
        {items.map(a => <Fragment key={a.id}><tr tabIndex={0} onClick={() => setOpen(open === a.id ? null : a.id)}><td>{new Date(a.occurred_at).toLocaleString()}</td><td>{a.actor}</td>
          <td>{a.action}</td><td>{a.entity} · {(a.after_state ?? a.before_state)?.code ?? (a.after_state ?? a.before_state)?.symbol ?? (a.after_state ?? a.before_state)?.broker_symbol ?? a.entity_id}</td><td>{a.reason}</td></tr>
          {open === a.id && <tr className="cfg-audit-detail"><td colSpan={5}><div className="cfg-pair"><pre>Before{'\n'}{JSON.stringify(a.before_state, null, 2)}</pre><pre>After{'\n'}{JSON.stringify(a.after_state, null, 2)}</pre></div></td></tr>}</Fragment>)}
      </tbody></table></div>}
  </div>;
}

/* ---------- workspace ---------- */

export default function Configuration() {
  const [user, setUser] = useState<string | null>(null), [checking, setChecking] = useState(true);
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
    try { setUser((await api('operator/session')).username); } catch { setUser(null); } finally { setChecking(false); }
  })(); }, []);
  useEffect(() => { if (user) void loadRefs(); }, [user]);
  async function signIn(e: FormEvent) {
    e.preventDefault(); setError('');
    try { setUser((await api('operator/login', 'POST', { username, password })).username); setPassword(''); } catch (err) { setError((err as Error).message); }
  }
  async function signOut() { try { await api('operator/logout', 'POST'); } finally { setUser(null); } }

  if (checking) return <div className="content"><p>Checking sign-in…</p></div>;
  if (!user) return <div className="content cfg-signin"><div className="eyebrow">CONFIGURATION</div><h1>Sign in</h1>
    <form className="cfg-form" onSubmit={signIn}><Field label="Username"><input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></Field>
      <Field label="Password"><input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {error && <p role="alert" className="cfg-error">{error}</p>}<div className="cfg-actions"><button className="primary">Sign in</button></div></form></div>;

  const current = SECTIONS.find(s => s.id === section)!;
  const live = summary?.execution_controls?.find((c: Row) => c.environment === 'LIVE');
  return <div className="cfg">
    <div className="cfg-topbar"><span className="eyebrow">CONFIGURATION</span>
    <nav className="cfg-nav" aria-label="Configuration sections">
      {SECTIONS.map(s => <button key={s.id} aria-current={section === s.id ? 'page' : undefined} onClick={() => {
        if (window.dispatchEvent(new Event('corei-before-layout-change', { cancelable: true }))) setSection(s.id);
      }}><span>{s.label}</span>{summary && s.id in (summary.counts ?? {}) && <small>{summary.counts[s.id]}</small>}
        {s.id === 'controls' && live && <small className={live.trading_enabled ? 'cfg-live-on' : ''}>{live.trading_enabled ? 'LIVE ON' : 'off'}</small>}</button>)}
    </nav>
    <div className="cfg-user">{user} · <button className="link" onClick={() => void signOut()}>Sign out</button></div></div>
    <section className="cfg-main">
      <header className="cfg-head"><h1>{current.label}</h1><p>{current.hint}</p></header>
      {summary && !summary.credential_storage_ready && <p role="alert" className="cfg-error">Credential encryption is not set up on the server. Run scripts/setup-broker-accounts.sh.</p>}
      {error && <p role="alert" className="cfg-error">{error}</p>}
      {section === 'controls' ? <OrderSending changed={() => void loadRefs()} /> : section === 'audit' ? <AuditLog />
        : <Records key={section} entity={section} refs={refs} reloadRefs={() => void loadRefs()} />}
    </section>
  </div>;
}
