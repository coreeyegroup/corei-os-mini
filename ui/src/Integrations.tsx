/* Integrations: connect CORE-AI, n8n and other applications to COREI through the integration gateway.
 *
 * Why it exists: each external application needs its own connection with exactly the permissions an administrator
 * chose, keys that are shown once and can be rotated or revoked, a real external test (not the administrator's
 * session), webhooks, and a record of what it did. Developers need the endpoints, their permissions and ready-to-copy
 * examples.
 * Purpose: Overview tiles; Connections (create from a profile, edit permissions and resource scope, limits, networks);
 * per connection: Permissions, Keys (issue, rotate with grace, revoke), Test & setup (a real Bearer request from this
 * browser without the session cookie, plus cURL / Python / JavaScript / n8n setup), Webhooks, Usage, Signals;
 * API explorer (the gateway's OpenAPI with required permissions; copy URL, cURL, Python, JavaScript, n8n).
 * Must never: keep a key or signing secret after it has been shown; send the session cookie with a Bearer test;
 * offer LIVE order permissions (the server refuses them too); send anything but GET from the explorer.
 * Dependencies: Configuration.api/Field/Choice, StageShell (Kpis, Msg, useData, when), Info.
 * Consumers: Configuration (Setup › 8 · Integrate › Integrations).
 * Architectural position: UI over /api/config/integrations and /api/v1/automation.
 */
import { useMemo, useState } from 'react';
import { api } from './Configuration';
import { Info } from './Info';
import { Kpis, Msg, useData, when, type Row } from './StageShell';

const BASE = `${window.location.origin}/api/v1/automation`;
const TYPES: [string, string][] = [['CORE_AI', 'CORE-AI'], ['N8N', 'n8n'], ['AI_AGENT', 'External AI agent'], ['PYTHON', 'Python application'],
  ['JAVASCRIPT', 'JavaScript application'], ['RESEARCH', 'Research platform'], ['DASHBOARD', 'External dashboard'], ['CUSTOM', 'Custom integration']];
const TABS = ['Permissions', 'Keys', 'Test & setup', 'Webhooks', 'Usage', 'Signals'] as const;

type Rule = { all: boolean; ids: string[] };
type Draft = { id?: string; version?: number; code: string; name: string; client_type: string; description: string; status: string; environment: string;
  scopes: string[]; access: { accounts: Rule; instruments: Rule; strategies: Rule }; requests_per_minute: number; max_concurrent_jobs: number;
  allowed_networks: string; reason: string };

function copy(text: string) { void navigator.clipboard?.writeText(text); }

function Copy({ text, label = 'Copy' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" onClick={() => { copy(text); setDone(true); window.setTimeout(() => setDone(false), 1200); }}>{done ? 'Copied ✓' : label}</button>;
}

function Secret({ title, value, note }: { title: string; value: string; note: string }) {
  return <div className="int-secret" role="alert"><b>{title}</b><code>{value}</code><Copy text={value} /><small>{note}</small></div>;
}

function RuleEdit({ label, rule, options, onChange }: { label: string; rule: Rule; options: Row[]; onChange: (r: Rule) => void }) {
  return <fieldset className="int-rule"><legend>{label}</legend>
    <label className="dk-radio"><input type="radio" checked={rule.all} onChange={() => onChange({ all: true, ids: [] })} /> All</label>
    <label className="dk-radio"><input type="radio" checked={!rule.all} onChange={() => onChange({ all: false, ids: rule.ids })} /> Only these ({rule.ids.length})</label>
    {!rule.all && <select multiple size={Math.min(6, Math.max(3, options.length))} aria-label={`${label} in scope`} value={rule.ids}
      onChange={e => onChange({ all: false, ids: [...e.target.selectedOptions].map(o => o.value) })}>
      {options.map(o => <option key={o.id} value={o.id}>{o.code ?? o.symbol}{o.name ? ` · ${o.name}` : ''}</option>)}</select>}
    {!rule.all && !rule.ids.length && <small className="dk-warn">Nothing selected: grants no {label.toLowerCase()}.</small>}
  </fieldset>;
}

function Editor({ draft, setDraft, catalog, refs, save, cancel, busy }: { draft: Draft; setDraft: (d: Draft) => void; catalog: Row; refs: Row; save: () => void; cancel: () => void; busy: boolean }) {
  const groups = useMemo(() => {
    const g: Record<string, Row[]> = {};
    for (const s of catalog?.scopes ?? []) (g[s.group] ??= []).push(s);
    return g;
  }, [catalog]);
  const toggle = (s: string) => setDraft({ ...draft, scopes: draft.scopes.includes(s) ? draft.scopes.filter(x => x !== s) : [...draft.scopes, s] });
  return <form className="pa-form int-form" aria-label={draft.id ? `Edit ${draft.code}` : 'New connection'} onSubmit={e => { e.preventDefault(); save(); }}>
    <h3>{draft.id ? `Connection · ${draft.code}` : 'New connection'}<button type="button" className="link" aria-label="Close" onClick={cancel}>✕</button></h3>
    <fieldset><legend>Application</legend><div className="dk-grid">
      <label>Code <input aria-label="Code" required disabled={!!draft.id} pattern="[a-z0-9][a-z0-9_-]{1,39}" value={draft.code} onChange={e => setDraft({ ...draft, code: e.target.value.toLowerCase() })} placeholder="core-ai" /></label>
      <label>Name <input aria-label="Name" required maxLength={120} value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="CORE-AI Financial Intelligence" /></label>
      <label>Profile <select aria-label="Profile" value={draft.client_type} onChange={e => setDraft({ ...draft, client_type: e.target.value,
        scopes: draft.id ? draft.scopes : [...(catalog?.profiles?.[e.target.value]?.scopes ?? [])] })}>{TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>Environment <select aria-label="Environment" value={draft.environment} onChange={e => setDraft({ ...draft, environment: e.target.value })}>
        <option value="PAPER">Paper (simulated orders only)</option><option value="DEMO">Paper and DEMO broker accounts</option></select></label>
      <label>Status <select aria-label="Status" value={draft.status} onChange={e => setDraft({ ...draft, status: e.target.value })}><option>ACTIVE</option><option>DISABLED</option></select></label>
      <label>Description <input maxLength={1000} value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} /></label>
    </div></fieldset>
    <fieldset><legend>Permissions <Info label="About permissions">Each endpoint checks its own permission. Profiles start read-only or signal-only; order permissions are added explicitly. LIVE order permissions are not available to integrations.</Info></legend>
      <div className="int-scopes">{Object.entries(groups).map(([g, list]) => <div key={g} className="int-scope-group"><b>{g}</b>
        {list.map(s => <label key={s.scope} className={s.status === 'UNAVAILABLE' ? 'off' : ''} title={s.note || s.label}>
          <input type="checkbox" aria-label={s.scope} disabled={s.status === 'UNAVAILABLE'} checked={draft.scopes.includes(s.scope)} onChange={() => toggle(s.scope)} />
          <span>{s.label}<small>{s.scope}{s.status !== 'AVAILABLE' ? ` · ${s.status.toLowerCase()}` : ''}</small></span></label>)}</div>)}</div>
    </fieldset>
    <fieldset><legend>Resource scope</legend><div className="int-rules">
      <RuleEdit label="Accounts" rule={draft.access.accounts} options={refs?.accounts ?? []} onChange={r => setDraft({ ...draft, access: { ...draft.access, accounts: r } })} />
      <RuleEdit label="Instruments" rule={draft.access.instruments} options={refs?.instruments ?? []} onChange={r => setDraft({ ...draft, access: { ...draft.access, instruments: r } })} />
      <RuleEdit label="Strategies" rule={draft.access.strategies} options={refs?.strategies ?? []} onChange={r => setDraft({ ...draft, access: { ...draft.access, strategies: r } })} />
    </div></fieldset>
    <fieldset><legend>Limits</legend><div className="dk-grid">
      <label>Requests per minute <input type="number" min={1} max={6000} value={draft.requests_per_minute} onChange={e => setDraft({ ...draft, requests_per_minute: Number(e.target.value) })} /></label>
      <label>Concurrent jobs <input type="number" min={1} max={50} value={draft.max_concurrent_jobs} onChange={e => setDraft({ ...draft, max_concurrent_jobs: Number(e.target.value) })} /></label>
      <label>Allowed networks <small>(CIDR, comma separated; blank = any)</small><input aria-label="Allowed networks" value={draft.allowed_networks} placeholder="203.0.113.0/24" onChange={e => setDraft({ ...draft, allowed_networks: e.target.value })} /></label>
      <label>Reason (audit log) <input aria-label="Reason" required minLength={3} maxLength={300} value={draft.reason} onChange={e => setDraft({ ...draft, reason: e.target.value })} /></label>
    </div></fieldset>
    <span className="mw-actions"><button type="button" onClick={cancel}>Cancel</button><button className="primary" disabled={busy}>{draft.id ? 'Save' : 'Create connection'}</button></span>
  </form>;
}

function snippets(key = '<API_KEY>') {
  return {
    curl: `curl -s ${BASE}/auth/test \\\n  -H "Authorization: Bearer ${key}"`,
    python: `import requests\nBASE = "${BASE}"\nH = {"Authorization": "Bearer ${key}"}\nprint(requests.get(f"{BASE}/capabilities", headers=H, timeout=30).json())\nsnap = requests.post(f"{BASE}/market-data/snapshot", headers=H, timeout=60, json={\n    "request_id": "analysis-001", "instruments": ["EURUSD"], "timeframes": ["H4", "H1", "M15"],\n    "include": ["instrument_metadata", "latest_quote", "candles", "indicators", "market_regime", "data_quality"],\n    "format": "AI_COMPACT", "candles_per_timeframe": 100, "max_output_chars": 16000}).json()`,
    js: `const BASE = "${BASE}";\nconst H = { Authorization: "Bearer ${key}", "Content-Type": "application/json" };\nconst caps = await (await fetch(\`\${BASE}/capabilities\`, { headers: H })).json();`,
    n8n: `HTTP Request node\n  Method: GET\n  URL: ${BASE}/auth/test\n  Authentication: Generic Credential Type\n  Generic Auth Type: Bearer Auth\n  Credential › Bearer Token: the raw key only (no "Bearer " prefix)\n  Options › Timeout: 30000`,
  };
}

function TestSetup({ conn }: { conn: Row }) {
  const [key, setKey] = useState('');
  const [result, setResult] = useState<{ step: string; ok: boolean; status: number; detail: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    const steps: { step: string; ok: boolean; status: number; detail: string }[] = [];
    for (const [step, path] of [['Authentication (Bearer)', '/auth/test'], ['Capability discovery', '/capabilities'], ['Data retrieval: instruments', '/instruments?limit=5']]) {
      try {
        // A real external request: Bearer key, no session cookie, no workstation header.
        const r = await fetch(`/api/v1/automation${path}`, { headers: { Authorization: `Bearer ${key.trim()}` }, credentials: 'omit', cache: 'no-store' });
        const j = await r.json().catch(() => ({}));
        steps.push({ step, ok: r.ok, status: r.status, detail: r.ok ? (path === '/auth/test' ? `connection ${j.connection}, ${j.scopes?.length} permissions, key ${j.key_prefix}`
          : path.startsWith('/instruments') ? `${j.total} instrument(s) in scope` : `${(j.scopes ?? []).filter((s: Row) => s.granted).length} permissions granted`)
          : `${j.error?.code ?? r.status}: ${j.error?.message ?? r.statusText}` });
        if (!r.ok) break;
      } catch (e) { steps.push({ step, ok: false, status: 0, detail: `Network: ${(e as Error).message}` }); break; }
    }
    setResult(steps); setBusy(false);
  };
  const s = snippets();
  return <div className="int-test">
    <p className="cfg-note">This test sends a real request from this browser with the key below and <b>without</b> your session, through the public address, exactly as CORE-AI or n8n would.</p>
    <div className="intel-bar"><input type="password" aria-label="Key to test" placeholder="corei_…" value={key} onChange={e => setKey(e.target.value)} style={{ minWidth: 320 }} autoComplete="off" />
      <button className="primary" disabled={busy || !key.startsWith('corei_')} onClick={() => void run()}>Run Bearer test</button><small>Connection: {conn.code}</small></div>
    {result.length > 0 && <table className="cfg-table" aria-label="Test results"><tbody>{result.map(r => <tr key={r.step}><td>{r.ok ? '✓' : '✕'}</td><td>{r.step}</td><td>{r.status || '—'}</td><td>{r.detail}</td></tr>)}</tbody></table>}
    <h4>Set up the client</h4>
    <div className="int-snips">{(Object.entries(s) as [string, string][]).map(([k, v]) => <div key={k}><b>{({ curl: 'cURL', python: 'Python', js: 'JavaScript', n8n: 'n8n (HTTP Request node)' } as Row)[k]}</b><pre>{v}</pre><Copy text={v} /></div>)}</div>
    <p className="cfg-note">Importable n8n workflows (no keys inside) are in the repository under <code>integrations/n8n/</code>; the full guide is <code>docs/INTEGRATION_GATEWAY.md</code>.</p>
  </div>;
}

function Keys({ conn, isAdmin, reload }: { conn: Row; isAdmin: boolean; reload: () => void }) {
  const [shown, setShown] = useState<Row | null>(null);
  const [error, setError] = useState('');
  const [days, setDays] = useState('90');
  const issue = async () => { setError(''); try { setShown(await api(`config/integrations/${conn.id}/keys`, 'POST', { label: 'issued from workstation', expires_days: days ? Number(days) : null })); reload(); } catch (e) { setError((e as Error).message); } };
  const rotate = async (k: Row) => { const h = window.prompt(`Rotate ${k.prefix}: hours the old key keeps working (0 = stop now)`, '24'); if (h === null) return;
    try { setShown(await api(`config/integrations/${conn.id}/keys/rotate`, 'POST', { old_key_id: k.id, grace_hours: Number(h) || 0, expires_days: days ? Number(days) : null })); reload(); } catch (e) { setError((e as Error).message); } };
  const revoke = async (k: Row) => { if (!window.confirm(`Revoke key ${k.prefix}? Applications using it stop working at once.`)) return;
    try { await api(`config/integrations/keys/${k.id}/revoke`, 'POST'); reload(); } catch (e) { setError((e as Error).message); } };
  return <div>
    <Msg error={error} />
    {shown && <Secret title={`New key ${shown.prefix}`} value={shown.key} note="Copy it now: COREI keeps only a hash and cannot show it again." />}
    {isAdmin && <div className="intel-bar"><button className="primary" onClick={() => void issue()}>+ Issue key</button>
      <label>Expires after <select aria-label="Key expiry" value={days} onChange={e => setDays(e.target.value)}><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option><option value="">never</option></select></label></div>}
    <table className="cfg-table"><thead><tr><th>Key</th><th>State</th><th>Expires</th><th>Last used</th><th>From</th><th>Created</th><th /></tr></thead>
      <tbody>{(conn.keys ?? []).map((k: Row) => <tr key={k.id}><td><code>corei_{k.prefix}_…</code><br /><small>{k.label}</small></td><td>{k.state}</td><td>{k.expires_at ? when(k.expires_at) : 'never'}</td>
        <td>{k.last_used_at ? when(k.last_used_at) : '—'}</td><td>{k.last_used_ip ?? '—'}</td><td>{when(k.created_at)} · {k.created_by}</td>
        <td>{isAdmin && k.state === 'ACTIVE' && <><button onClick={() => void rotate(k)}>Rotate…</button><button onClick={() => void revoke(k)}>Revoke</button></>}</td></tr>)}</tbody></table>
    {!(conn.keys ?? []).length && <p className="cfg-note">No key yet. Issue one and give it to the application.</p>}
  </div>;
}

function Hooks({ conn, catalog, isAdmin }: { conn: Row; catalog: Row; isAdmin: boolean }) {
  const [data, error, reload] = useData<Row>(`config/integrations/${conn.id}/webhooks`, 10_000, [conn.id]);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<string[]>(['job.completed', 'job.failed', 'signal.created']);
  const [shown, setShown] = useState<Row | null>(null);
  const [msg, setMsg] = useState('');
  const add = async () => { setMsg(''); try { setShown(await api(`config/integrations/${conn.id}/webhooks`, 'POST', { url, events })); setUrl(''); reload(); } catch (e) { setMsg((e as Error).message); } };
  return <div>
    <Msg error={msg || error} />
    {shown && <Secret title="Signing secret" value={shown.signing_secret} note="Verify X-COREI-Signature = sha256=HMAC_SHA256(secret, timestamp + '.' + body). Shown once." />}
    {!conn.scopes?.includes('events.subscribe') && <p className="cfg-note">This connection lacks the events.subscribe permission: its webhooks receive nothing until it is granted.</p>}
    {isAdmin && <form className="int-hook" onSubmit={e => { e.preventDefault(); void add(); }}>
      <input aria-label="Webhook URL" required placeholder="https://n8n.coreeye.in/webhook/…" value={url} onChange={e => setUrl(e.target.value)} />
      <div className="int-events">{Object.entries(catalog?.events ?? {}).map(([k, d]) => <label key={k} title={String(d)}><input type="checkbox" checked={events.includes(k)}
        onChange={() => setEvents(events.includes(k) ? events.filter(x => x !== k) : [...events, k])} /> {k}</label>)}</div>
      <button className="primary" disabled={!url || !events.length}>Add webhook</button></form>}
    <table className="cfg-table"><thead><tr><th>Destination</th><th>Events</th><th className="num">Sent</th><th className="num">Pending</th><th className="num">Failed</th><th>Last sent</th><th /></tr></thead>
      <tbody>{(data?.items ?? []).map((w: Row) => <tr key={w.id}><td>{w.url}</td><td><small>{w.events.join(', ')}</small></td><td className="num">{w.sent}</td><td className="num">{w.pending}</td>
        <td className="num">{w.failed}</td><td>{w.last_sent_at ? when(w.last_sent_at) : '—'}</td>
        <td>{isAdmin && <><button onClick={() => void api(`config/integrations/webhooks/${w.id}/test`, 'POST').then(reload)}>Test</button>
          <button onClick={() => { if (window.confirm('Delete this webhook?')) void api(`config/integrations/webhooks/${w.id}`, 'DELETE').then(reload); }}>✕</button></>}</td></tr>)}</tbody></table>
    <h4>Recent deliveries</h4>
    <table className="cfg-table"><thead><tr><th>Event</th><th>Status</th><th className="num">Attempts</th><th>HTTP</th><th>Error</th><th>Created</th></tr></thead>
      <tbody>{(data?.deliveries ?? []).map((d: Row) => <tr key={d.id}><td>{d.event}</td><td>{d.status}</td><td className="num">{d.attempts}</td><td>{d.last_status ?? '—'}</td><td><small>{d.last_error ?? ''}</small></td><td>{when(d.created_at)}</td></tr>)}</tbody></table>
  </div>;
}

function Usage({ conn }: { conn: Row }) {
  const [data, error] = useData<Row>(`config/integrations/${conn.id}/usage`, 10_000, [conn.id]);
  return <div><Msg error={error} />
    <h4>Last 24 hours by endpoint</h4>
    <table className="cfg-table"><thead><tr><th>Endpoint</th><th className="num">Requests</th><th className="num">Errors</th><th className="num">Avg ms</th></tr></thead>
      <tbody>{(data?.by_endpoint_24h ?? []).map((r: Row) => <tr key={r.method + r.path}><td><code>{r.method} {r.path.replace('/api/v1/automation', '')}</code></td><td className="num">{r.n}</td><td className="num">{r.errors}</td><td className="num">{r.avg_ms}</td></tr>)}</tbody></table>
    <h4>Recent requests</h4>
    <table className="cfg-table"><thead><tr><th>Time</th><th>Request</th><th>Status</th><th>Error</th><th className="num">ms</th><th>Key</th><th>From</th></tr></thead>
      <tbody>{(data?.recent ?? []).map((r: Row, i: number) => <tr key={i}><td>{when(r.at)}</td><td><code>{r.method} {r.path.replace('/api/v1/automation', '')}</code></td><td>{r.status}</td><td>{r.error_code ?? ''}</td>
        <td className="num">{r.duration_ms}</td><td>{r.key_prefix ?? '—'}</td><td>{r.ip}</td></tr>)}</tbody></table>
  </div>;
}

function Signals({ conn }: { conn: Row }) {
  const [data, error] = useData<Row>(`config/integrations/${conn.id}/signals`, 15_000, [conn.id]);
  return <div><Msg error={error} />
    <p className="cfg-note">Candidate signals from this application, with their provenance. A signal is never an order.</p>
    <table className="cfg-table"><thead><tr><th>Time</th><th>Instrument</th><th>Direction</th><th>Entry · stop · targets</th><th>Strength</th><th>Status</th><th>Risk</th><th>Source</th></tr></thead>
      <tbody>{(data?.items ?? []).map((s: Row) => <tr key={s.id}><td>{when(s.created_at)}</td><td>{s.symbol}</td><td>{s.direction}</td>
        <td>{s.entry ?? s.entry_condition ?? '—'} · {s.stop_loss ?? '—'} · {(s.targets ?? []).join(', ') || '—'}</td><td>{s.strength ?? '—'} <small>{s.confidence_kind?.toLowerCase()}</small></td>
        <td>{s.status}</td><td>{s.risk_decision ?? '—'}</td><td><small>{[s.provenance?.application, s.provenance?.agent, s.provenance?.model].filter(Boolean).join(' · ')}</small></td></tr>)}</tbody></table>
  </div>;
}

function Explorer() {
  const [doc, error] = useData<Row>('v1/automation/openapi.json', 0);
  const [sel, setSel] = useState<string>('');
  const ops = useMemo(() => Object.entries(doc?.paths ?? {}).flatMap(([p, ms]) => Object.entries(ms as Row).map(([m, op]) => ({ key: `${m} ${p}`, method: m.toUpperCase(), path: p, op: op as Row }))), [doc]);
  const cur = ops.find(o => o.key === sel) ?? ops[0];
  const url = cur ? BASE + cur.path.replace('/api/v1/automation', '') : '';
  const body = cur?.method === 'POST' ? ' \\\n  -H "Content-Type: application/json" -d \'{...}\'' : '';
  return <div className="int-explorer"><Msg error={error} />
    <nav aria-label="Endpoints">{ops.map(o => <button key={o.key} aria-current={cur?.key === o.key ? 'true' : undefined} onClick={() => setSel(o.key)}><span className={`int-m ${o.method.toLowerCase()}`}>{o.method}</span>{o.path.replace('/api/v1/automation', '')}</button>)}</nav>
    {cur && <section aria-label="Endpoint detail"><h4><span className={`int-m ${cur.method.toLowerCase()}`}>{cur.method}</span> {cur.path}</h4>
      <p>{cur.op.summary ?? ''}</p>
      <dl className="pd-stats"><div><dt>Authentication</dt><dd>Bearer key</dd></div><div><dt>Permission</dt><dd>{(cur.op['x-required-scopes'] ?? []).join(', ') || 'a valid key'}</dd></div>
        <div><dt>Parameters</dt><dd>{(cur.op.parameters ?? []).map((p: Row) => `${p.name}${p.required ? '*' : ''}`).join(', ') || '—'}</dd></div>
        <div><dt>Body</dt><dd>{cur.op.requestBody ? Object.keys(cur.op.requestBody.content ?? {}).join(', ') : '—'}</dd></div></dl>
      <div className="intel-bar"><Copy text={url} label="Copy URL" /><Copy text={`curl -s -X ${cur.method} "${url}" \\\n  -H "Authorization: Bearer <API_KEY>"${body}`} label="Copy cURL" />
        <Copy text={`requests.${cur.method.toLowerCase()}("${url}", headers={"Authorization": "Bearer <API_KEY>"}, timeout=30)`} label="Copy Python" />
        <Copy text={`await fetch("${url}", { method: "${cur.method}", headers: { Authorization: "Bearer <API_KEY>" } })`} label="Copy JavaScript" />
        <Copy text={`HTTP Request · Method ${cur.method} · URL ${url} · Authentication: Generic Credential Type › Bearer Auth`} label="Copy n8n" /></div>
      <p className="cfg-note">Errors share one shape: {'{"error": {"code", "message", "request_id"}}'}. Order examples default to PAPER; LIVE is refused.</p></section>}
  </div>;
}

export default function Integrations({ isAdmin }: { isAdmin: boolean }) {
  const [data, error, reload] = useData<Row>('config/integrations', 10_000);
  const [catalog] = useData<Row>('config/integrations/catalog', 0);
  const [accounts] = useData<Row>('config/accounts', 60_000);
  const [ins] = useData<Row>('config/instruments', 120_000);
  const [strats] = useData<Row>('config/strategies', 120_000);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<typeof TABS[number]>('Permissions');
  const [view, setView] = useState<'connections' | 'explorer'>('connections');
  const [draft, setDraft] = useState<Draft | null>(null);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [busy, setBusy] = useState(false);
  const items: Row[] = data?.items ?? [];
  const o: Row = data?.overview ?? {};
  const conn = items.find(c => c.id === selected);
  const refs = { accounts: accounts?.items ?? [], instruments: ins?.items ?? [], strategies: strats?.items ?? [] };
  const blank = (): Draft => ({ code: '', name: '', client_type: 'CORE_AI', description: '', status: 'ACTIVE', environment: 'PAPER',
    scopes: [...(catalog?.profiles?.CORE_AI?.scopes ?? [])], access: { accounts: { all: false, ids: [] }, instruments: { all: true, ids: [] }, strategies: { all: true, ids: [] } },
    requests_per_minute: 60, max_concurrent_jobs: 2, allowed_networks: '', reason: 'New integration connection' });
  const edit = (c: Row) => setDraft({ id: c.id, version: c.version, code: c.code, name: c.name, client_type: c.client_type, description: c.description, status: c.status,
    environment: c.environment, scopes: c.scopes, access: { accounts: { all: !!c.access?.accounts?.all, ids: c.access?.accounts?.ids ?? [] },
      instruments: { all: !!c.access?.instruments?.all, ids: c.access?.instruments?.ids ?? [] }, strategies: { all: !!c.access?.strategies?.all, ids: c.access?.strategies?.ids ?? [] } },
    requests_per_minute: c.requests_per_minute, max_concurrent_jobs: c.max_concurrent_jobs, allowed_networks: (c.allowed_networks ?? []).join(', '), reason: 'Update integration connection' });
  const save = async () => {
    if (!draft) return;
    setBusy(true); setMsg({ error: '', notice: '' });
    const body = { ...draft, allowed_networks: draft.allowed_networks.split(',').map(x => x.trim()).filter(Boolean) };
    delete (body as Row).id;
    try {
      const r = draft.id ? await api(`config/integrations/${draft.id}`, 'PUT', body) : await api('config/integrations', 'POST', { ...body, version: undefined });
      setDraft(null); setSelected(r.id); setMsg({ error: '', notice: `${r.code} saved.` }); reload();
    } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); } finally { setBusy(false); }
  };
  return <div className="cfg-pane int" aria-label="Integrations">
    <div className="intel-bar">
      <div role="tablist" aria-label="Integrations views" className="int-views">{([['connections', 'Connections'], ['explorer', 'API explorer']] as const).map(([v, l]) =>
        <button key={v} role="tab" aria-selected={view === v} onClick={() => setView(v)}>{l}</button>)}</div>
      {isAdmin && view === 'connections' && <button className="primary" onClick={() => setDraft(blank())}>+ New connection</button>}
      <Info label="About integrations">Each application gets its own connection, keys and permissions. Keys are sent as <code>Authorization: Bearer &lt;key&gt;</code> to {BASE}. COREI keeps working when CORE-AI or n8n is offline; LIVE orders are never available to integrations.</Info>
    </div>
    <Kpis items={[['Connections', o.connections ?? 0, `${o.active ?? 0} active · ${o.disabled ?? 0} disabled`], ['Requests 24h', o.requests_24h ?? 0],
      ['Auth failures 24h', o.auth_failures_24h ?? 0, '', o.auth_failures_24h ? 'warn' : ''], ['Running jobs', o.running_jobs ?? 0], ['Failed jobs', o.failed_jobs ?? 0],
      ['Avg latency', o.avg_ms != null ? `${o.avg_ms} ms` : '—'], ['Last success', o.last_success ? when(o.last_success) : '—']]} />
    <Msg error={msg.error || error} notice={msg.notice} />
    {draft && <Editor draft={draft} setDraft={setDraft} catalog={catalog ?? {}} refs={refs} save={() => void save()} cancel={() => setDraft(null)} busy={busy} />}
    {view === 'explorer' ? <Explorer /> : <>
      {items.length > 0 ? <table className="cfg-table pa-table" aria-label="Connections"><thead><tr><th>Connection</th><th>Profile</th><th>Environment</th><th>Status</th>
        <th className="num">Permissions</th><th className="num">Keys</th><th className="num">Requests 24h</th><th>Last success</th><th /></tr></thead>
        <tbody>{items.map(c => <tr key={c.id} className={`pa-row ${selected === c.id ? 'selected' : ''}`} tabIndex={0} onClick={e => { if (!(e.target as Element).closest('button')) setSelected(c.id); }}>
          <td><b>{c.name}</b><br /><small>{c.code}</small></td><td>{TYPES.find(t => t[0] === c.client_type)?.[1]}</td><td>{c.environment}</td><td>{c.status}</td>
          <td className="num">{c.scopes.length}</td><td className="num">{(c.keys ?? []).filter((k: Row) => k.state === 'ACTIVE').length}</td>
          <td className="num">{c.usage_24h?.requests ?? 0}{c.usage_24h?.auth_failures ? <small className="dk-warn"> · {c.usage_24h.auth_failures} refused</small> : null}</td>
          <td>{c.usage_24h?.last_success ? when(c.usage_24h.last_success) : '—'}</td>
          <td className="pa-actions"><button className="primary" onClick={() => setSelected(c.id)}>Open</button>{isAdmin && <><button onClick={() => edit(c)}>Edit</button>
            <button onClick={() => { if (window.confirm(`Delete ${c.code}? Its keys stop working at once.`)) void api(`config/integrations/${c.id}`, 'DELETE').then(() => { setSelected(null); reload(); }); }}>✕</button></>}</td></tr>)}</tbody></table>
        : data && <div className="pa-empty"><b>No integration yet</b><p>Create a connection for CORE-AI or n8n, issue a key and paste it into that application. Start read-only; add order permissions later if needed.</p></div>}
      {conn && <section className="pd-tabs int-detail" aria-label={`Connection ${conn.code}`}>
        <div role="tablist" aria-label="Connection tabs">{TABS.map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>)}</div>
        <div role="tabpanel" aria-label={tab}>
          {tab === 'Permissions' && <div className="int-perm">
            <p><b>{conn.name}</b> · {conn.environment === 'DEMO' ? 'paper and DEMO orders allowed if granted' : 'paper only'} · {conn.requests_per_minute}/min · {conn.max_concurrent_jobs} job(s)
              {(conn.allowed_networks ?? []).length ? ` · networks ${conn.allowed_networks.join(', ')}` : ''}</p>
            <div className="int-chips">{conn.scopes.map((s: string) => <span key={s} className={`int-chip ${s.startsWith('orders.') && s !== 'orders.read' && s !== 'orders.preview' ? 'act' : ''}`}>{s}</span>)}</div>
            <p className="cfg-note">Accounts: {conn.access?.accounts?.all ? 'all' : `${conn.access?.accounts?.ids?.length ?? 0} chosen`} · Instruments: {conn.access?.instruments?.all ? 'all' : `${conn.access?.instruments?.ids?.length ?? 0} chosen`} · Strategies: {conn.access?.strategies?.all ? 'all' : `${conn.access?.strategies?.ids?.length ?? 0} chosen`}</p>
            {isAdmin && <button onClick={() => edit(conn)}>Edit permissions…</button>}</div>}
          {tab === 'Keys' && <Keys conn={conn} isAdmin={isAdmin} reload={reload} />}
          {tab === 'Test & setup' && <TestSetup conn={conn} />}
          {tab === 'Webhooks' && <Hooks conn={conn} catalog={catalog ?? {}} isAdmin={isAdmin} />}
          {tab === 'Usage' && <Usage conn={conn} />}
          {tab === 'Signals' && <Signals conn={conn} />}
        </div></section>}
    </>}
  </div>;
}
