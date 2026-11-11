/*
 * Institutional controls screens: health board and setup checklist, four-eyes
 * approvals, control policies, alert channels, CSV import, re-authentication,
 * daily broker login, symbol specifications and the credential-use log.
 * Rules live in the backend; these screens show its answers.
 */
import { useEffect, useState, type FormEvent } from 'react';
import FloatingWindow from './FloatingWindow';
import { api, BLOCKERS, Choice, Env, Field, Status, type Navigate, type Row } from './Configuration';

const when = (v?: string | null) => v ? new Date(v).toLocaleString() : '—';
const label = (code: string) => BLOCKERS[code] ?? code.replace(/_/g, ' ').toLowerCase();

/* ---------- health board ---------- */

export function HealthBoard({ navigate }: { navigate: Navigate }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const load = async () => { setBusy(true); try { setData(await api('config/health')); setError(''); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, []);
  if (error) return <p role="alert" className="cfg-error">{error}</p>;
  if (!data) return <p className="cfg-note">Loading…</p>;
  const byProvider = new Map<string, { code: string; id: string; connections: Row[] }>();
  type Entry = { code: string; id: string; connections: Row[] };
  for (const c of data.connections) {
    const entry: Entry = byProvider.get(c.provider_id) ?? { code: c.provider_code, id: c.provider_id, connections: [] };
    entry.connections.push(c as Row); byProvider.set(c.provider_id, entry);
  }
  const accountsOf = (cid: string) => data.accounts.filter((a: Row) => a.connection_id === cid);
  const live = data.controls.find((c: Row) => c.environment === 'LIVE'), demo = data.controls.find((c: Row) => c.environment === 'DEMO');
  const next = data.checklist.find((c: Row) => !c.done);
  return <div className="cfg-health">
    <div className="cfg-tiles">
      <div className="cfg-tile"><span>Accounts</span><strong>{data.summary.accounts}</strong></div>
      <div className="cfg-tile"><span>Ready for data</span><strong>{data.summary.data_ready}</strong></div>
      <div className={`cfg-tile ${data.summary.trading_ready ? 'ok' : ''}`}><span>Ready to trade</span><strong>{data.summary.trading_ready}</strong></div>
      <div className={`cfg-tile ${data.summary.with_warnings ? 'warn' : ''}`}><span>With warnings</span><strong>{data.summary.with_warnings}</strong></div>
      <button type="button" className={`cfg-tile ${data.pending_approvals ? 'warn' : ''}`} onClick={() => navigate('approvals')}><span>Awaiting approval</span><strong>{data.pending_approvals}</strong></button>
      <button type="button" className={`cfg-tile ${data.failed_alerts ? 'warn' : ''}`} onClick={() => navigate('alerts')}><span>Failed alerts</span><strong>{data.failed_alerts}</strong></button>
    </div>
    <div className="cfg-health-row">
      <section className="cfg-panel">
        <h3>Controls and server</h3>
        <dl className="cfg-desc">
          <dt>Order sending</dt><dd><Env value="LIVE" /> {live?.trading_enabled ? <strong className="cfg-live-on">ON</strong> : 'off'} · <Env value="DEMO" /> {demo?.trading_enabled ? 'ON' : 'off'}</dd>
          <dt>Four-eyes on LIVE changes</dt><dd>{data.four_eyes_live_changes ? 'On' : <span className="cfg-pill warn">Off</span>}</dd>
          <dt>Outbound IP</dt><dd>{data.network.egress_ip ?? <span className="cfg-pill warn">Unknown</span>}<small className="cfg-note"> — register this with broker API apps that require a static IP</small></dd>
          <dt>Clock synchronised</dt><dd>{data.network.clock_synchronised === true ? 'Yes (NTP)' : data.network.clock_synchronised === false ? <span className="cfg-pill warn">No — fix NTP before trading</span> : 'Unknown'}</dd>
          <dt>Server time (UTC)</dt><dd>{data.network.server_time_utc}</dd>
        </dl>
        <button type="button" onClick={() => void load()} disabled={busy}>{busy ? 'Refreshing…' : 'Refresh'}</button>
      </section>
      <section className="cfg-panel">
        <h3>Set-up checklist</h3>
        <ol className="cfg-checklist-steps">{data.checklist.map((c: Row) => <li key={c.step} className={c.done ? 'done' : ''}>
          <span aria-hidden>{c.done ? '✓' : '○'}</span><button type="button" className="cfg-link" onClick={() => navigate(c.section)}>{c.step}</button></li>)}</ol>
        {next ? <p className="cfg-note">Next: {next.step}.</p> : <p className="cfg-ok">All set-up steps are done.</p>}
      </section>
    </div>
    <section className="cfg-panel">
      <h3>Brokers, connections and accounts</h3>
      {byProvider.size === 0 && <p className="cfg-note">Nothing configured yet. Start with Add MT5 account under Accounts.</p>}
      {[...byProvider.values()].map(p => <div key={p.id} className="cfg-tree">
        <button type="button" className="cfg-chip strong" onClick={() => navigate('providers', p.id)}>{p.code}</button>
        {p.connections.map(c => <div key={c.id} className="cfg-tree-conn">
          <div className="cfg-tree-head">
            <button type="button" className="cfg-chip" onClick={() => navigate('connections', c.id)}>{c.code}</button>
            <Env value={c.environment} /> <span className="cfg-note">{c.platform === 'MT5' ? c.settings?.server_name : c.settings?.api_base_url}</span>
            {c.live_status?.state ? <span className={`cfg-pill ${c.live_status.state === 'CONNECTED' ? 'ok' : c.live_status.state === 'FAILED' ? 'warn' : 'off'}`}>{c.live_status.state}</span> : <span className="cfg-pill off">Not connected</span>}
            {c.server_utc_offset_minutes == null && <span className="cfg-pill warn">Clock not set</span>}
            {c.max_orders_per_second == null && <span className="cfg-pill">No rate limit</span>}
            {c.pending_changes > 0 && <span className="cfg-pill warn">Awaiting approval</span>}
            {c.status !== 'ACTIVE' && <Status value={c.status} />}
          </div>
          <table className="cfg-table compact"><tbody>{accountsOf(c.id).map((a: Row) => <tr key={a.id} onClick={() => navigate('accounts', a.id)} tabIndex={0}>
            <td><strong>{a.code}</strong><small className="cfg-note"> {a.holder_name}</small></td>
            <td>{a.trading_blockers.length === 0 ? <span className="cfg-pill ok">Ready to trade</span>
              : a.data_blockers.length === 0 ? <span className="cfg-pill">Data ready</span> : <span className="cfg-pill warn">Not ready</span>}</td>
            <td className="cfg-reasons">{[...new Set([...a.data_blockers, ...a.trading_blockers])].slice(0, 3).map(label).join(' · ')}
              {a.data_blockers.length + a.trading_blockers.length > 3 ? ' …' : ''}{a.warnings?.length ? <small className="cfg-note"> · {a.warnings.length} warning(s)</small> : null}</td>
          </tr>)}
          {accountsOf(c.id).length === 0 && <tr><td className="cfg-note">No accounts.</td></tr>}</tbody></table>
        </div>)}
      </div>)}
    </section>
  </div>;
}

/* ---------- approvals ---------- */

export function Approvals({ me, roles, changed }: { me: string; roles: string[]; changed: () => void }) {
  const [items, setItems] = useState<Row[] | null>(null), [status, setStatus] = useState('PENDING'), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(''), [note, setNote] = useState<Record<string, string>>({});
  const canDecide = roles.includes('TRADING_OPS') || roles.includes('RISK_APPROVER');
  const load = async () => { try { setItems((await api(`config/approvals${status ? '?status=' + status : ''}`)).items); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, [status]);
  async function act(id: string, path: string, body?: Row) {
    setBusy(id); setError(''); setNotice('');
    try { const r = await api(`config/approvals/${id}/${path}`, 'POST', body); setNotice(`Change ${String(r.status).toLowerCase()}.`); await load(); changed(); }
    catch (e) { setError((e as Error).message); await load(); } finally { setBusy(''); }
  }
  return <div className="cfg-approvals">
    <p className="cfg-note">LIVE address additions, LIVE reactivation, LIVE password replacement and LIVE account set-up wait here until a different person approves them. Passwords inside a request are encrypted and erased once it is decided.</p>
    <div className="cfg-toolbar"><Choice label="Show" value={status} options={[['PENDING', 'Waiting'], ['APPLIED', 'Applied'], ['REJECTED', 'Rejected'], ['FAILED', 'Failed'], ['WITHDRAWN', 'Withdrawn'], ['', 'All']]} onChange={setStatus} /></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    {!items ? <p className="cfg-note">Loading…</p> : items.length === 0 ? <p className="cfg-empty">{status === 'PENDING' ? 'Nothing is waiting for approval.' : 'No requests.'}</p> :
      <ul className="cfg-requests">{items.map(r => <li key={r.id} className="cfg-panel">
        <div className="cfg-request-head"><strong>{r.action.replace(/_/g, ' ').toLowerCase()}</strong><Status value={r.status} /></div>
        <p>{r.summary}</p>
        <small className="cfg-note">Asked by {r.requested_by} · {when(r.requested_at)}{r.reason ? ` · ${r.reason}` : ''}
          {r.decided_by ? ` · decided by ${r.decided_by} ${when(r.decided_at)}${r.decision_note ? ': ' + r.decision_note : ''}` : ''}</small>
        {r.status === 'PENDING' && <div className="cfg-actions">
          {r.requested_by === me ? <><span className="cfg-note">Waiting for someone else.</span>
            <button type="button" disabled={busy === r.id} onClick={() => void act(r.id, 'withdraw')}>Withdraw</button></>
            : canDecide ? <><input aria-label={`Note for ${r.id}`} placeholder="Note (optional)" value={note[r.id] ?? ''} onChange={e => setNote({ ...note, [r.id]: e.target.value })} />
              <button type="button" className="primary" disabled={busy === r.id} onClick={() => void act(r.id, 'decision', { approve: true, note: note[r.id] ?? '' })}>Approve and apply</button>
              <button type="button" className="danger" disabled={busy === r.id} onClick={() => void act(r.id, 'decision', { approve: false, note: note[r.id] ?? '' })}>Reject</button></>
            : <span className="cfg-note">Needs Trading operations or Risk approver.</span>}
        </div>}
      </li>)}</ul>}
  </div>;
}

/* ---------- policies ---------- */

export function Policies({ roles, changed }: { roles: string[]; changed: () => void }) {
  const [items, setItems] = useState<Row[] | null>(null), [edit, setEdit] = useState<Row | null>(null), [error, setError] = useState('');
  const can = roles.includes('RISK_APPROVER');
  const load = async () => { try { setItems((await api('config/policies')).items); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  async function save(e: FormEvent) {
    e.preventDefault(); if (!edit) return; setError('');
    try { await api(`config/policies/${edit.key}`, 'PUT', { value: edit.kind === 'bool' ? edit.value === true || edit.value === 'true' : Number(edit.value), version: edit.version, reason: edit.reason ?? '' });
      setEdit(null); await load(); changed(); }
    catch (err) { setError((err as Error).message); }
  }
  const names: Row = { four_eyes_live_changes: 'Four-eyes on LIVE changes', reauth_minutes: 'Password re-entry valid for (minutes)', expiry_warning_days: 'Warn before expiry (days)' };
  return <section className="cfg-panel cfg-policies">
    <h3>Policies</h3>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <table className="cfg-table compact"><tbody>{items?.map(p => <tr key={p.key}>
      <td><strong>{names[p.key] ?? p.key}</strong><br /><small className="cfg-note">{p.description}</small></td>
      <td>{p.kind === 'bool' ? (p.value ? 'On' : <span className="cfg-pill warn">Off</span>) : String(p.value)}</td>
      <td><small className="cfg-note">{p.updated_by} · {when(p.updated_at)}</small></td>
      <td>{can && <button type="button" onClick={() => setEdit({ ...p, reason: '' })}>Change…</button>}</td></tr>)}</tbody></table>
    {!can && <p className="cfg-note">Changing a policy needs the Risk approver role.</p>}
    {edit && <form className="cfg-form" onSubmit={save}>
      <h4>{names[edit.key] ?? edit.key}</h4>
      {edit.kind === 'bool' ? <Field label="Value"><Choice label="Value" value={String(edit.value)} options={[['true', 'On'], ['false', 'Off']]} onChange={v => setEdit({ ...edit, value: v === 'true' })} /></Field>
        : <Field label="Value"><input aria-label="Value" type="number" value={edit.value} onChange={e => setEdit({ ...edit, value: e.target.value })} /></Field>}
      <Field label="Reason (recorded in audit log)"><input aria-label="Policy reason" required minLength={5} maxLength={500} value={edit.reason} onChange={e => setEdit({ ...edit, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Save policy</button><button type="button" onClick={() => setEdit(null)}>Cancel</button></div>
    </form>}
  </section>;
}

/* ---------- alerts ---------- */

const EVENTS: [string, string][] = [['LIVE_CHANGE', 'LIVE changes'], ['APPROVAL_NEEDED', 'Approval needed'], ['ORDER_SENDING', 'Order sending switched'],
  ['LOGIN_FAILED', 'Connection or login failed'], ['ROUTE_FALLBACK', 'Moved to another address'], ['EXPIRY', 'Account or mandate expiring']];

export function Alerts({ canEdit }: { canEdit: boolean }) {
  const [channels, setChannels] = useState<Row[] | null>(null), [outbox, setOutbox] = useState<Row[]>([]), [edit, setEdit] = useState<Row | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const load = async () => { try { setChannels((await api('config/alerts/channels')).items); setOutbox((await api('config/alerts/outbox')).items); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  async function save(e: FormEvent) {
    e.preventDefault(); if (!edit) return; setError('');
    const body: Row = { kind: edit.kind, name: edit.name, target: edit.target, events: edit.events, status: edit.status ?? 'ACTIVE', ...(edit.secret ? { secret: edit.secret } : {}) };
    try { if (edit.id) await api(`config/alerts/channels/${edit.id}`, 'PUT', { ...body, version: edit.version }); else await api('config/alerts/channels', 'POST', body);
      setEdit(null); setNotice('Channel saved.'); await load(); }
    catch (err) { setError((err as Error).message); }
  }
  async function test(id: string) { try { await api(`config/alerts/channels/${id}/test`, 'POST'); setNotice('Test queued; it is sent within about 15 seconds.'); window.setTimeout(() => void load(), 16_000); } catch (e) { setError((e as Error).message); } }
  async function remove(id: string) { if (!window.confirm('Delete this alert channel?')) return; try { await api(`config/alerts/channels/${id}`, 'DELETE'); await load(); } catch (e) { setError((e as Error).message); } }
  const targetLabel = (kind: string) => kind === 'EMAIL' ? 'E-mail address' : kind === 'TELEGRAM' ? 'Telegram chat ID or @channel' : 'Webhook URL (https)';
  return <div className="cfg-alerts">
    <p className="cfg-note">Alerts are queued in the same transaction as the change that causes them and retried up to 5 times. E-mail needs the server's SMTP settings (COREI_SMTP_*); Telegram needs a bot token.</p>
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="cfg-toolbar">{canEdit && <button className="primary" onClick={() => setEdit({ kind: 'TELEGRAM', name: '', target: '', events: EVENTS.map(e => e[0]), status: 'ACTIVE' })}>New channel</button>}</div>
    {channels && channels.length === 0 && <p className="cfg-empty">No alert channels yet.</p>}
    {channels && channels.length > 0 && <table className="cfg-table"><thead><tr><th>Name</th><th>Kind</th><th>Target</th><th>Events</th><th>Last sent</th><th>Status</th><th /></tr></thead><tbody>
      {channels.map(c => <tr key={c.id}><td>{c.name}</td><td>{c.kind}</td><td>{c.target}</td><td>{c.events.length}</td><td>{when(c.last_sent_at)}{c.failed_count ? <> <span className="cfg-pill warn">{c.failed_count} failed</span></> : null}</td>
        <td><Status value={c.status} /></td><td className="cfg-row-actions">{canEdit && <><button type="button" onClick={() => void test(c.id)}>Test</button>
          <button type="button" onClick={() => setEdit({ ...c, secret: '' })}>Edit</button><button type="button" onClick={() => void remove(c.id)}>✕</button></>}</td></tr>)}</tbody></table>}
    <h3>Recent alerts</h3>
    <table className="cfg-table compact"><thead><tr><th>Time</th><th>Event</th><th>Title</th><th>Channel</th><th>Status</th></tr></thead><tbody>
      {outbox.map(o => <tr key={o.id}><td>{when(o.created_at)}</td><td>{o.event}</td><td>{o.title}</td><td>{o.channel ?? '—'}</td>
        <td>{o.status}{o.last_error ? <small className="cfg-note" title={o.last_error}> · {o.last_error.slice(0, 60)}</small> : null}</td></tr>)}
      {!outbox.length && <tr><td colSpan={5} className="cfg-note">No alerts yet.</td></tr>}</tbody></table>
    {edit && <FloatingWindow storageKey="corei-os-mini.alert-window.v1" title={edit.id ? `Alert channel · ${edit.name}` : 'New alert channel'} onClose={() => setEdit(null)}
      footer={<div className="cfg-actions"><button className="primary" form="cfg-alert-form">Save channel</button></div>}>
      <form id="cfg-alert-form" className="cfg-form" onSubmit={save}>
        <Field label="Kind"><Choice label="Kind" value={edit.kind} options={[['TELEGRAM', 'Telegram'], ['EMAIL', 'E-mail'], ['WEBHOOK', 'Webhook']]} onChange={v => setEdit({ ...edit, kind: v })} /></Field>
        <Field label="Name"><input aria-label="Channel name" required maxLength={120} value={edit.name} onChange={e => setEdit({ ...edit, name: e.target.value })} /></Field>
        <Field label={targetLabel(edit.kind)}><input aria-label="Target" required maxLength={500} value={edit.target} onChange={e => setEdit({ ...edit, target: e.target.value })} /></Field>
        {edit.kind !== 'EMAIL' && <Field label={edit.kind === 'TELEGRAM' ? 'Bot token' : 'Signing secret (optional)'} hint={edit.has_secret ? 'Saved; leave empty to keep it' : 'Stored encrypted; never shown again'}>
          <input aria-label="Channel secret" type="password" autoComplete="new-password" value={edit.secret ?? ''} onChange={e => setEdit({ ...edit, secret: e.target.value })} /></Field>}
        <Field label="Send these events"><div className="cfg-checks">{EVENTS.map(([k, l]) => <label key={k}><input type="checkbox" checked={edit.events.includes(k)}
          onChange={e => setEdit({ ...edit, events: e.target.checked ? [...edit.events, k] : edit.events.filter((x: string) => x !== k) })} /> {l}</label>)}</div></Field>
        <Field label="Status"><Choice label="Channel status" value={edit.status ?? 'ACTIVE'} options={['ACTIVE', 'INACTIVE']} onChange={v => setEdit({ ...edit, status: v })} /></Field>
        {error && <p role="alert" className="cfg-error">{error}</p>}
      </form>
    </FloatingWindow>}
  </div>;
}

/* ---------- CSV import ---------- */

export function ImportAccounts({ close, done }: { close: () => void; done: () => void }) {
  const [text, setText] = useState(''), [result, setResult] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function run(dryRun: boolean) {
    setBusy(true); setError('');
    try { const r = await api('config/accounts/import', 'POST', { csv: text, dry_run: dryRun }); setResult(r); if (r.applied) done(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <FloatingWindow storageKey="corei-os-mini.import-window.v1" width={760} title="Import accounts from CSV" subtitle="Checked first; applied all-or-nothing"
    onClose={close} footer={<div className="cfg-actions">
      <button type="button" disabled={busy || !text} onClick={() => void run(true)}>Check</button>
      <button type="button" className="primary" disabled={busy || !result?.ok || result?.dry_run === false} onClick={() => void run(false)}>Import {result?.counts?.ok ?? ''} accounts</button></div>}>
    <div className="cfg-form">
      <p className="cfg-note">Columns (first line): connection_code, code, name, external_account_id, base_currency, and optionally holder_name, trading_permission,
        relationship, mandate_reference, mandate_expires_on, margin_mode, expires_on, groups (codes separated by |). Passwords are never imported from files; add them per account.</p>
      <label className="cfg-filebutton">Choose CSV file…<input type="file" accept=".csv,text/csv" onChange={async e => { const f = e.target.files?.[0]; if (f) { setText(await f.text()); setResult(null); } }} /></label>
      <Field label="CSV"><textarea aria-label="CSV" rows={8} value={text} onChange={e => { setText(e.target.value); setResult(null); }} spellCheck={false} /></Field>
      {error && <p role="alert" className="cfg-error">{error}</p>}
      {result && <><p className={result.ok ? 'cfg-ok' : 'cfg-error'} role="status">{result.applied ? `Imported ${result.counts.ok} accounts.` : result.ok ? `All ${result.counts.ok} rows are valid. Choose Import to add them.` : `${result.counts.error} row(s) have problems. Nothing was imported.`}</p>
        <table className="cfg-table compact"><thead><tr><th>Line</th><th>Account ID</th><th>Result</th></tr></thead><tbody>
          {result.rows.map((r: Row) => <tr key={r.line}><td>{r.line}</td><td>{r.code}</td><td>{r.status === 'OK' ? <span className="cfg-pill ok">OK</span> : <span className="cfg-error">{r.message}</span>}</td></tr>)}</tbody></table></>}
    </div>
  </FloatingWindow>;
}

/* ---------- re-authentication ---------- */

export function ReauthDialog({ done }: { done: (ok: boolean) => void }) {
  const [password, setPassword] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { await api('operator/reauth', 'POST', { password }); setPassword(''); done(true); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <div className="cfg-modal" role="dialog" aria-modal="true" aria-label="Confirm with your password">
    <form className="cfg-form cfg-modal-box" onSubmit={submit}>
      <h3>Confirm it is you</h3>
      <p className="cfg-note">This action changes passwords, order sending, approvals or policies. Enter your COREI password to continue.</p>
      <Field label="Your password"><input aria-label="Your password" type="password" autoComplete="current-password" autoFocus required value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {error && <p role="alert" className="cfg-error">{error}</p>}
      <div className="cfg-actions"><button className="primary" disabled={busy}>Confirm</button><button type="button" onClick={() => done(false)}>Cancel</button></div>
    </form>
  </div>;
}

/* ---------- daily broker login (Upstox) ---------- */

export function BrokerLogin({ account, canEdit, reload }: { account: Row; canEdit: boolean; reload: () => void }) {
  const [totp, setTotp] = useState(''), [pin, setPin] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const expires = account.session_token_expires_at, valid = expires && new Date(expires) > new Date();
  async function start() {
    setError(''); try { const { authorize_url } = await api(`config/accounts/${account.id}/broker-login/start`, 'POST'); window.location.assign(authorize_url); }
    catch (e) { setError((e as Error).message); }
  }
  async function withTotp(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { const r = await api(`config/accounts/${account.id}/broker-login/totp`, 'POST', { totp, pin }); setTotp(''); setPin(''); setNotice(`Logged in until ${when(r.expires_at)}.`); reload(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <section className="cfg-credential">
    <h3>Today's broker login</h3>
    <p className="cfg-note">{valid ? <>Logged in · valid until {when(expires)}.</> : <>Not logged in today. The broker requires the account holder's 2FA every trading day (SEBI framework); the token expires at 03:30 IST.</>}</p>
    {canEdit && <>
      <div className="cfg-actions"><button type="button" className="primary" onClick={() => void start()}>Log in with the broker…</button>
        <small className="cfg-note">Opens the broker's login page; you return here when done.</small></div>
      <details className="cfg-more"><summary>Log in with authenticator code and PIN instead</summary>
        <form className="cfg-form" onSubmit={withTotp} autoComplete="off">
          <div className="cfg-pair"><Field label="Authenticator code"><input aria-label="Authenticator code" inputMode="numeric" pattern="\d{6}" required value={totp} onChange={e => setTotp(e.target.value)} /></Field>
            <Field label="PIN"><input aria-label="PIN" type="password" inputMode="numeric" pattern="\d{6}" required value={pin} onChange={e => setPin(e.target.value)} /></Field></div>
          <div className="cfg-actions"><button disabled={busy}>Log in</button><small className="cfg-note">Neither is stored.</small></div>
        </form></details></>}
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
  </section>;
}

/* ---------- symbol specifications and order check ---------- */

export function SymbolSpecs({ account, canEdit, refresh = 0 }: { account: Row; canEdit: boolean; refresh?: number }) {
  const [filter, setFilter] = useState('');
  const [items, setItems] = useState<Row[] | null>(null), [form, setForm] = useState<Row | null>(null), [check, setCheck] = useState<Row>({ broker_symbol: '', side: 'BUY', volume: '' });
  const [result, setResult] = useState<Row | null>(null), [error, setError] = useState('');
  const load = async () => { try { setItems((await api(`config/accounts/${account.id}/symbol-specs`)).items); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, [account.id, refresh]);
  const shown = (items ?? []).filter(s => !filter || s.broker_symbol.toLowerCase().includes(filter.toLowerCase()));
  async function add(e: FormEvent) {
    e.preventDefault(); if (!form) return; setError('');
    try { await api(`config/accounts/${account.id}/symbol-specs`, 'POST', { broker_symbol: form.broker_symbol, spec: { digits: Number(form.digits), volume_min: form.volume_min,
      volume_max: form.volume_max, volume_step: form.volume_step, filling_modes: form.filling_modes, stops_level_points: Number(form.stops_level_points || 0), trade_mode: form.trade_mode } });
      setForm(null); await load(); }
    catch (err) { setError((err as Error).message); }
  }
  async function run(e: FormEvent) {
    e.preventDefault(); setError('');
    try { setResult(await api(`config/accounts/${account.id}/order-check`, 'POST', { ...check, volume: String(check.volume) })); } catch (err) { setError((err as Error).message); }
  }
  const set = (k: string, v: unknown) => setForm({ ...form, [k]: v });
  return <details className="cfg-more cfg-specs"><summary>Broker's order limits on this account ({items?.length ?? 0} symbols)</summary>
    <p className="cfg-note">These are the <strong>broker's</strong> hard limits for each symbol on this account: the smallest lot it accepts, the increment, its maximum, the filling modes and the minimum stop distance. They are <strong>not your trade size</strong>. The size of each order is flexible and decided per order by the strategy, signal strength and portfolio risk; COREI only checks that the chosen size is one the broker will accept (for example rounding 0.137 lots to the broker's 0.01 step). The limits are read from the broker at login; until then they can be entered by hand.</p>
    {items && items.length > 12 && <input className="cfg-search" aria-label="Find symbol" placeholder={`Find among ${items.length} symbols`} value={filter} onChange={e => setFilter(e.target.value)} />}
    {shown.length > 0 && <table className="cfg-table compact"><thead><tr><th>Symbol</th><th>Lots (min / step / max)</th><th>Filling</th><th>Stops</th><th>Mode</th><th>Source</th></tr></thead><tbody>
      {shown.slice(0, 200).map(s => <tr key={s.broker_symbol}><td>{s.broker_symbol}</td><td>{s.spec.volume_min} / {s.spec.volume_step} / {s.spec.volume_max}</td><td>{s.spec.filling_modes?.join(', ')}</td>
        <td>{s.spec.stops_level_points ?? 0} pts</td><td>{s.spec.trade_mode ?? 'FULL'}</td><td>{s.source === 'BROKER' ? 'Broker' : 'Manual'} · {when(s.read_at)}</td></tr>)}</tbody></table>}
    {shown.length > 200 && <p className="cfg-note">Showing 200 of {shown.length}; type to narrow.</p>}
    {canEdit && !form && <button type="button" onClick={() => setForm({ broker_symbol: '', digits: 2, volume_min: '0.01', volume_step: '0.01', volume_max: '100', filling_modes: ['IOC'], stops_level_points: 0, trade_mode: 'FULL' })}>Enter the broker's limits for a symbol…</button>}
    {form && <form className="cfg-form" onSubmit={add}>
      <div className="cfg-pair"><Field label="Broker symbol"><input aria-label="Spec symbol" required value={form.broker_symbol} onChange={e => set('broker_symbol', e.target.value)} /></Field>
        <Field label="Price digits"><input aria-label="Spec digits" type="number" min={0} max={12} value={form.digits} onChange={e => set('digits', e.target.value)} /></Field></div>
      <div className="cfg-pair"><Field label="Broker accepts: smallest lot / increment / largest"><span className="cfg-address-edit"><input aria-label="Spec min" value={form.volume_min} onChange={e => set('volume_min', e.target.value)} />
        <input aria-label="Spec step" value={form.volume_step} onChange={e => set('volume_step', e.target.value)} /><input aria-label="Spec max" value={form.volume_max} onChange={e => set('volume_max', e.target.value)} /></span></Field>
        <Field label="Stops level (points)"><input aria-label="Spec stops" type="number" min={0} value={form.stops_level_points} onChange={e => set('stops_level_points', e.target.value)} /></Field></div>
      <Field label="Filling modes allowed"><div className="cfg-checks">{['FOK', 'IOC', 'RETURN'].map(m => <label key={m}><input type="checkbox" checked={form.filling_modes.includes(m)}
        onChange={e => set('filling_modes', e.target.checked ? [...form.filling_modes, m] : form.filling_modes.filter((x: string) => x !== m))} /> {m}</label>)}</div></Field>
      <Field label="Trade mode"><Choice label="Spec trade mode" value={form.trade_mode} options={['FULL', 'LONG_ONLY', 'SHORT_ONLY', 'CLOSE_ONLY', 'DISABLED']} onChange={v => set('trade_mode', v)} /></Field>
      <div className="cfg-actions"><button className="primary">Save rules</button><button type="button" onClick={() => setForm(null)}>Cancel</button></div>
    </form>}
    <form className="cfg-form cfg-order-check" onSubmit={run}>
      <strong>Would the broker accept this order? (nothing is sent)</strong>
      <div className="cfg-pair"><Field label="Symbol"><input aria-label="Check symbol" required value={check.broker_symbol} onChange={e => setCheck({ ...check, broker_symbol: e.target.value })} /></Field>
        <Field label="Side and lots"><span className="cfg-address-edit"><Choice label="Check side" value={check.side} options={['BUY', 'SELL']} onChange={v => setCheck({ ...check, side: v })} />
          <input aria-label="Check volume" required inputMode="decimal" value={check.volume} onChange={e => setCheck({ ...check, volume: e.target.value })} /></span></Field></div>
      <div className="cfg-actions"><button>Check</button></div>
      {result && (result.ok ? <p className="cfg-ok">The broker should accept this order{result.filling ? ` (filling ${result.filling})` : ''}.</p>
        : <ul className="cfg-warnings">{result.problems.map((p: string) => <li key={p}>{p}</li>)}</ul>)}
    </form>
    {error && <p role="alert" className="cfg-error">{error}</p>}
  </details>;
}

/* ---------- read real data from the broker (MT5) ---------- */

export function BrokerRead({ account, canEdit, onRead }: { account: Row; canEdit: boolean; onRead: () => void }) {
  const [reads, setReads] = useState<Row[] | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const load = async () => { try { setReads((await api(`config/accounts/${account.id}/broker-reads`)).items); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, [account.id]);
  async function read() {
    setBusy(true); setError('');
    try { await api(`config/accounts/${account.id}/read-from-broker`, 'POST', {}); await load(); onRead(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const last = reads?.[0];
  const hasPassword = Boolean(account.investor_credential_kind || account.master_credential_kind);
  return <section className="cfg-panel cfg-broker-read" aria-label="Read from broker">
    <header><strong>Real data from the broker</strong>
      {canEdit && <button type="button" className="primary" disabled={busy || !hasPassword} onClick={() => void read()}>{busy ? 'Logging in…' : 'Log in and read now'}</button>}</header>
    <p className="cfg-note">One read-only login {account.investor_credential_kind ? 'with the investor password' : 'with the master password (save an investor password to read without trading rights)'}.
      COREI stores the margin mode, leverage, group and stop-out, the server clock offset, the address that answered, and every symbol's broker limits. Nothing is traded.</p>
    {!hasPassword && <p className="cfg-note">Save a password above first.</p>}
    {last && (last.connected
      ? <dl className="cfg-facts">
          <div><dt>Last read</dt><dd>{when(last.read_at)} by {last.requested_by}</dd></div>
          <div><dt>Answered</dt><dd>{last.host}:{last.port}{last.connect_ms != null ? ` · ${last.connect_ms} ms` : ''}</dd></div>
          <div><dt>Margin mode</dt><dd>{last.stored?.facts?.margin_mode ?? 'not reported'}</dd></div>
          <div><dt>Leverage</dt><dd>{last.stored?.facts?.leverage ? `1:${last.stored.facts.leverage}` : '—'}</dd></div>
          <div><dt>Group</dt><dd>{last.stored?.facts?.broker_group ?? '—'}</dd></div>
          <div><dt>Balance</dt><dd>{last.stored?.facts?.balance != null ? `${last.stored.facts.balance} ${last.stored.facts.currency ?? ''}` : '—'}</dd></div>
          <div><dt>Server clock</dt><dd>{last.server_time_offset_minutes != null ? `UTC${last.server_time_offset_minutes >= 0 ? '+' : '−'}${Math.abs(last.server_time_offset_minutes) / 60}h` : '—'}</dd></div>
          <div><dt>Symbols</dt><dd>{last.symbols_stored} stored of {last.symbols_available}{last.symbols_unmapped?.length ? ` · ${last.symbols_unmapped.length} need mapping` : ''}</dd></div>
        </dl>
      : <p role="status" className="cfg-error">Last login failed {when(last.read_at)}: {last.error}</p>)}
    {last && last.attempts?.length > 0 && <details className="cfg-more"><summary>Addresses tried ({last.attempts.length})</summary>
      <table className="cfg-table compact"><tbody>{last.attempts.map((a: Row, i: number) => <tr key={i}><td>{a.host}:{a.port}</td><td>{a.ok ? 'Logged in' : a.error}</td><td>{a.ms != null ? `${a.ms} ms` : ''}</td></tr>)}</tbody></table></details>}
    {reads && reads.length > 1 && <details className="cfg-more"><summary>Earlier reads ({reads.length - 1})</summary>
      <table className="cfg-table compact"><tbody>{reads.slice(1).map(r => <tr key={r.id}><td>{when(r.read_at)}</td><td>{r.requested_by}</td><td>{r.connected ? `${r.host} · ${r.symbols_stored} symbols` : r.error}</td></tr>)}</tbody></table></details>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
  </section>;
}

/* ---------- credential-use log ---------- */

export function CredentialUse({ owner, id }: { owner: string; id: string }) {
  const [items, setItems] = useState<Row[] | null>(null);
  useEffect(() => { void api(`config/${owner}/${id}/credential-use`).then(d => setItems(d.items)).catch(() => setItems([])); }, [owner, id]);
  return <details className="cfg-more"><summary>Password use log ({items?.length ?? 0})</summary>
    <p className="cfg-note">Every time COREI reads a stored password or token, who and why is recorded here. This log cannot be edited.</p>
    {items && items.length > 0 ? <table className="cfg-table compact"><thead><tr><th>Time</th><th>Used by</th><th>Purpose</th></tr></thead><tbody>
      {items.map((u, i) => <tr key={i}><td>{when(u.used_at)}</td><td>{u.used_by}</td><td>{u.purpose}</td></tr>)}</tbody></table> : <p className="cfg-note">Not used yet.</p>}
  </details>;
}
