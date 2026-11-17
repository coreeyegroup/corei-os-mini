/* Setup › Bridge nodes (DEC-75).
 * Why: one Windows machine (VPS, PC, server) running the COREI MT5 bridge serves many accounts. It is registered once
 * here; the machine enrols itself with a one-time code, so nobody copies fingerprints or tokens between consoles.
 * Purpose: the list of nodes with their health, the guided "Connect a Windows machine" window (what to paste where,
 * with copy buttons, and a live status that turns green when the machine has registered and COREI reached it),
 * using a node for accounts, updating, disabling and deleting.
 * Must never: show a node token (the API never returns one). */
import { useEffect, useState, type FormEvent } from 'react';
import FloatingWindow from './FloatingWindow';
import { api, Choice, Field, type Row } from './Configuration';

const when = (v?: string | null) => v ? new Date(v).toLocaleString() : '—';
const LOCATIONS: [string, string][] = [['WINDOWS_VPS', 'Windows VPS'], ['WINDOWS_PC', 'Windows PC (office/home)'], ['WINDOWS_SERVER', 'Windows server (data centre)'], ['OTHER', 'Other']];
const MODES: [string, string][] = [['DIRECT', 'Direct: COREI connects to it (VPS or server with a public IP)'], ['TUNNEL', 'Tunnel: it connects out to COREI (PC behind a router, no open port)']];

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return <button type="button" onClick={() => { try { void navigator.clipboard.writeText(text).then(() => { setDone(true); window.setTimeout(() => setDone(false), 1500); }); } catch { /* no clipboard */ } }}>
    {done ? 'Copied' : 'Copy'}</button>;
}

function statusPill(n: Row) {
  const cls = n.status === 'ACTIVE' ? (n.last_check_ok === false ? 'warn' : 'ok') : n.status === 'PENDING' ? 'warn' : n.status === 'DISABLED' ? 'off' : 'live';
  const text = n.status === 'PENDING' ? 'waiting for the machine' : n.status === 'ACTIVE' && n.last_check_ok === false ? 'registered · last check failed' : String(n.status).toLowerCase();
  return <span className={`cfg-pill ${cls}`}>{text}</span>;
}

/* The steps COREI gives for a machine: download, then install and register; live status underneath. */
function Steps({ node, steps, close }: { node: Row; steps: Row; close: () => void }) {
  const [live, setLive] = useState<Row>(node);
  useEffect(() => {
    const t = window.setInterval(() => void api(`config/bridge-nodes/${node.id}`).then(setLive).catch(() => undefined), 5000);
    return () => window.clearInterval(t);
  }, [node.id]);
  const ready = live.status === 'ACTIVE' && live.last_check_ok !== false;
  return <FloatingWindow storageKey="corei-os-mini.bridge-steps.v1" title={`Connect ${node.name}`} subtitle="On the Windows machine, in this order" onClose={close} width={760}
    footer={<div className="cfg-actions"><button className="primary" onClick={close}>{ready ? 'Done' : 'Close (the code stays valid)'}</button></div>}>
    <div className="cfg-form bn-steps">
      <p className="cfg-note">Before you start: the broker's MT5 terminal is installed on that machine and has been logged in once by hand (any account).
        Nothing else is needed; the installer gets Python and the bridge itself.</p>
      <ol>{steps.steps.map((s: Row, i: number) => <li key={i}><b>{s.title}</b>{s.detail && <p className="cfg-note">{s.detail}</p>}
        {s.command && <div className="bn-cmd"><code>{s.command}</code><Copy text={s.command} /></div>}</li>)}</ol>
      {steps.code && <p className="cfg-note">The code <code>{steps.code}</code> works once, for {steps.code_minutes} minutes. It is not a password: it only lets this machine register as {node.name}.
        If it expires, use <em>New code</em> on the node.</p>}
      <div className={`bn-live ${ready ? 'ok' : ''}`} role="status">
        {live.status === 'PENDING' && <>Waiting for the machine to register… (checks every 5 s)</>}
        {live.status !== 'PENDING' && <>Registered from {live.machine || '?'} ({live.remote_ip ?? '?'}) · bridge {live.bridge_version} · {statusPill(live)}
          {live.last_check && <> · {live.last_check.ok ? `COREI reached it: ${live.last_check.round_trip_ms} ms, clock ${live.last_check.clock_skew_ms ?? '?'} ms` : live.last_check.error}</>}</>}
        {live.mode === 'TUNNEL' && live.status !== 'PENDING' && !live.last_check_ok && <p className="cfg-note">Tunnel mode: COREI reaches the machine once its tunnel is up. If the installer said the server
          does not accept tunnel keys yet, an administrator runs <code>sudo bash scripts/enable-bridge-tunnels.sh</code> once on the COREI server, then <em>Check now</em>.</p>}
      </div>
      {ready && <p className="cfg-ok">Ready. Close this, then <em>Use for accounts</em> on {node.name}{node.default_for_new ? ' (new MT5 accounts get it automatically)' : ''}.</p>}
    </div>
  </FloatingWindow>;
}

export function BridgeNodes({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState('');
  const [form, setForm] = useState<Row | null>(null), [steps, setSteps] = useState<{ node: Row; steps: Row } | null>(null), [edit, setEdit] = useState<Row | null>(null);
  const [attach, setAttach] = useState<Row | null>(null), [accounts, setAccounts] = useState<Row[]>([]), [picked, setPicked] = useState<string[]>([]);
  const load = async () => { try { setData(await api('config/bridge-nodes')); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, []);
  const act = async (id: string, fn: () => Promise<unknown>, message?: string) => {
    setBusy(id); setError(''); setNotice('');
    try { const r = await fn(); if (message) setNotice(message); await load(); return r; } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  async function create(e: FormEvent) {
    e.preventDefault(); if (!form) return;
    const r = await act('create', () => api('config/bridge-nodes', 'POST', { name: form.name, location: form.location, mode: form.mode, max_terminals: Number(form.max_terminals),
      default_for_new: !!form.default_for_new, notes: form.notes ?? '' })) as Row | undefined;
    if (r) { setForm(null); setSteps({ node: r, steps: r.instructions }); }
  }
  async function newCode(n: Row) {
    const r = await act(n.id, () => api(`config/bridge-nodes/${n.id}/code`, 'POST')) as Row | undefined;
    if (r) setSteps({ node: r, steps: r.instructions });
  }
  async function check(n: Row) {
    const r = await act(n.id, () => api(`config/bridge-nodes/${n.id}/check`, 'POST')) as Row | undefined;
    if (r) r.ok ? setNotice(`${n.name}: reached in ${r.round_trip_ms} ms · bridge ${r.version} · ${r.terminals_open}/${r.max_terminals} terminals open (${r.streaming} streaming) · clock ${r.clock_skew_ms} ms.`)
      : setError(`${n.name}: ${r.error}`);
  }
  async function openAttach(n: Row) {
    setAttach(n);
    try { const a = await api('config/accounts'); setAccounts(a.items.filter((x: Row) => x.platform === 'MT5' && x.status !== 'ARCHIVED')); setPicked([]); } catch (e) { setError((e as Error).message); }
  }
  async function doAttach(all: boolean) {
    if (!attach) return;
    const r = await act(attach.id, () => api(`config/bridge-nodes/${attach.id}/attach`, 'POST', { account_ids: all ? 'ALL_MT5' : picked })) as Row | undefined;
    if (r) { setNotice(`${attach.name}: added to ${r.added.length} account(s)${r.already.length ? `, ${r.already.length} already used it` : ''}.`); setAttach(null); }
  }
  async function saveEdit(e: FormEvent) {
    e.preventDefault(); if (!edit) return;
    const r = await act(edit.id, () => api(`config/bridge-nodes/${edit.id}`, 'PUT', { location: edit.location, host: edit.host || null, port: Number(edit.port),
      max_terminals: Number(edit.max_terminals), default_for_new: !!edit.default_for_new, enabled: edit.status !== 'DISABLED', notes: edit.notes ?? '', version: edit.version }), 'Saved.');
    if (r) setEdit(null);
  }
  const nodes: Row[] = data?.items ?? [];
  return <section className="bn">
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="intel-bar">
      {canEdit && <button className="primary" onClick={() => setForm({ name: '', location: 'WINDOWS_VPS', mode: 'DIRECT', max_terminals: 20, default_for_new: nodes.length === 0, notes: '' })}>Connect a Windows machine</button>}
      <button onClick={() => void load()}>Reload</button>
      <small className="cfg-note">Latest bridge: {data?.latest_bridge ?? '…'}</small>
    </div>
    <details className="cfg-more" open={nodes.length === 0}><summary>How this works</summary>
      <ol className="cfg-note">
        <li><b>Here:</b> <em>Connect a Windows machine</em>: give it a name and say how COREI reaches it. COREI shows two lines and a one-time code.</li>
        <li><b>On the Windows machine</b> (VPS or PC, with the broker's MT5 terminal installed): open PowerShell as Administrator and paste the two lines. The installer sets up
          Python, the bridge, a task that keeps it running and the firewall (or the tunnel), then registers with COREI. You copy nothing back.</li>
        <li><b>Here again:</b> the node turns green. <em>Use for accounts</em> adds it as a route to the MT5 accounts you choose (or all), or tick <em>Default for new MT5 accounts</em>.</li>
      </ol>
      <p className="cfg-note">One machine serves many accounts: market data is collected once per broker server, and account reads share a pool of terminals
        (up to <em>max terminals</em> open at once; the least recently used idle one is closed to make room; a streaming one never is). Size by memory, roughly 150–300 MB per terminal.
        Add a second node for capacity or failover: give an account routes through both, in order.</p>
      <p className="cfg-note">A web terminal into the VPS or the COREI server is deliberately not offered: it would make this website a door to those machines.
        The installer and the checks here replace the console work instead.</p>
    </details>
    <div className="cfg-scroll"><table className="cfg-table compact"><thead><tr><th>Node</th><th>How COREI reaches it</th><th>Bridge</th><th>State</th><th>Pool</th><th>Accounts</th><th /></tr></thead><tbody>
      {nodes.map(n => <tr key={n.id} className={n.status === 'DISABLED' ? 'muted' : ''}>
        <td><b>{n.name}</b>{n.default_for_new && <span className="cfg-pill" title="New MT5 accounts get a route through this node"> default</span>}<br />
          <small className="cfg-note">{LOCATIONS.find(l => l[0] === n.location)?.[1]}{n.machine ? ` · ${n.machine}` : ''}</small></td>
        <td>{n.mode === 'TUNNEL' ? <>tunnel · 127.0.0.1:{n.tunnel_port}</> : <>direct · {n.host ?? '(from enrolment)'}:{n.port}</>}<br />
          <small className="cfg-note">{n.fingerprint ? `pinned ${String(n.fingerprint).slice(0, 11)}…` : 'not enrolled'}</small></td>
        <td>{n.bridge_version || '–'}{n.update_available && <><br /><span className="cfg-pill warn" title={`Latest ${n.latest_bridge}`}>update</span></>}</td>
        <td>{statusPill(n)}<br /><small className="cfg-note">{n.last_check_at ? `checked ${when(n.last_check_at)}` : n.status === 'PENDING' && n.enroll_expires_at ? `code valid until ${new Date(n.enroll_expires_at).toLocaleTimeString()}` : ''}</small>
          {n.last_check && !n.last_check.ok && <><br /><small className="cfg-error-text" title={n.last_check.error}>{String(n.last_check.error).slice(0, 80)}</small></>}</td>
        <td>{n.last_check?.ok ? `${n.last_check.terminals_open}/${n.last_check.max_terminals}` : `max ${n.max_terminals}`}</td>
        <td>{n.accounts}</td>
        <td><div className="cfg-route-actions">
          <button disabled={busy === n.id || n.status === 'PENDING'} onClick={() => void check(n)}>{busy === n.id ? '…' : 'Check now'}</button>
          {canEdit && <>
            <button disabled={n.status === 'DISABLED'} onClick={() => void openAttach(n)}>Use for accounts</button>
            <button title="Install again or on a new machine with this name (a new one-time code)" onClick={() => void newCode(n)}>{n.status === 'PENDING' ? 'Show steps (new code)' : 'New code'}</button>
            {n.update_available && <button onClick={() => setSteps({ node: n, steps: { steps: [{ title: 'On the machine, PowerShell as Administrator', command: `powershell -ExecutionPolicy Bypass -File C:\\corei-setup\\install-corei-bridge.ps1 -Corei ${data?.corei_url} -Update -Name ${n.name}`,
              detail: 'If C:\\corei-setup\\install-corei-bridge.ps1 is missing, use New code first (its first line downloads it).' }] } })}>Update steps</button>}
            <button onClick={() => setEdit({ ...n, host: n.host ?? '' })}>Edit</button>
            {n.accounts > 0 && <button onClick={() => { if (window.confirm(`Remove ${n.name} from all ${n.accounts} account(s)? Those accounts fall back to their other routes.`)) void act(n.id, () => api(`config/bridge-nodes/${n.id}/detach`, 'POST'), `${n.name} removed from its accounts.`); }}>Detach from all accounts</button>}
            <button className="danger" disabled={n.accounts > 0} title={n.accounts > 0 ? 'Detach it from its accounts first' : ''}
              onClick={() => { if (window.confirm(`Delete node ${n.name}? Its token is deleted with it; the bridge on the machine keeps running until you uninstall it.`)) void act(n.id, () => api(`config/bridge-nodes/${n.id}`, 'DELETE'), `${n.name} deleted.`); }}>Delete</button></>}
        </div></td></tr>)}
      {data && !nodes.length && <tr><td colSpan={7} className="cfg-note">No machines yet. <em>Connect a Windows machine</em> to start.</td></tr>}
    </tbody></table></div>

    {form && <FloatingWindow storageKey="corei-os-mini.bridge-new.v1" title="Connect a Windows machine" onClose={() => setForm(null)}
      footer={<div className="cfg-actions"><button className="primary" form="bn-new" disabled={busy === 'create'}>Next: show what to run</button><button onClick={() => setForm(null)}>Cancel</button></div>}>
      <form id="bn-new" className="cfg-form" onSubmit={create}>
        <Field label="Name" hint="Lower case, e.g. vps-london, office-pc. Shown on routes and in the machine's task names."><input aria-label="Node name" required pattern="[a-z0-9][a-z0-9-]{1,40}" value={form.name}
          onChange={e => setForm({ ...form, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '-') })} /></Field>
        <Field label="Where it runs"><Choice label="Where it runs" value={form.location} options={LOCATIONS} onChange={v => setForm({ ...form, location: v, mode: v === 'WINDOWS_PC' ? 'TUNNEL' : form.mode })} /></Field>
        <Field label="How COREI reaches it"><Choice label="How COREI reaches it" value={form.mode} options={MODES} onChange={v => setForm({ ...form, mode: v })} /></Field>
        <Field label="Terminals open at once" hint="The pool size. Roughly 150–300 MB memory each: 8 GB ≈ 20, 16 GB ≈ 40–60."><input aria-label="Max terminals" type="number" min={1} max={500} value={form.max_terminals}
          onChange={e => setForm({ ...form, max_terminals: e.target.value })} /></Field>
        <label><input type="checkbox" checked={!!form.default_for_new} onChange={e => setForm({ ...form, default_for_new: e.target.checked })} /> Default for new MT5 accounts (they get a route through it by themselves)</label>
        <Field label="Notes"><input aria-label="Node notes" maxLength={500} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></Field>
        <p className="cfg-note">You will be asked for your password: the next screen shows a one-time code that registers a machine.</p>
      </form>
    </FloatingWindow>}

    {steps && <Steps node={steps.node} steps={steps.steps} close={() => { setSteps(null); void load(); }} />}

    {attach && <FloatingWindow storageKey="corei-os-mini.bridge-attach.v1" title={`Use ${attach.name} for accounts`} onClose={() => setAttach(null)}
      footer={<div className="cfg-actions"><button className="primary" disabled={!picked.length} onClick={() => void doAttach(false)}>Use for {picked.length} chosen</button>
        <button onClick={() => void doAttach(true)}>Use for all MT5 accounts</button><button onClick={() => setAttach(null)}>Cancel</button></div>}>
      <div className="cfg-form">
        <p className="cfg-note">Adds a route through {attach.name} to each chosen account (after its existing routes). Accounts that already use it are left as they are.</p>
        <div className="intel-checks">{accounts.map(a => <label key={a.id} className="intel-check"><input type="checkbox" checked={picked.includes(a.id)}
          onChange={e => setPicked(e.target.checked ? [...picked, a.id] : picked.filter(x => x !== a.id))} /> {a.code} <small className="cfg-note">{a.name}</small></label>)}
          {!accounts.length && <small className="cfg-note">No MT5 accounts yet.</small>}</div>
      </div>
    </FloatingWindow>}

    {edit && <FloatingWindow storageKey="corei-os-mini.bridge-edit.v1" title={`Node · ${edit.name}`} onClose={() => setEdit(null)}
      footer={<div className="cfg-actions"><button className="primary" form="bn-edit">Save</button><button onClick={() => setEdit(null)}>Cancel</button></div>}>
      <form id="bn-edit" className="cfg-form" onSubmit={saveEdit}>
        <Field label="Where it runs"><Choice label="Where it runs" value={edit.location} options={LOCATIONS} onChange={v => setEdit({ ...edit, location: v })} /></Field>
        {edit.mode === 'DIRECT' && <div className="cfg-pair"><Field label="Host" hint="Its public IP or name (set from the enrolment)"><input aria-label="Node host" value={edit.host} onChange={e => setEdit({ ...edit, host: e.target.value })} /></Field>
          <Field label="Port"><input aria-label="Node port" type="number" min={1} max={65535} value={edit.port} onChange={e => setEdit({ ...edit, port: e.target.value })} /></Field></div>}
        <Field label="Terminals open at once" hint="Change it on the machine too: run the update steps (it reads the pool size from COREI at install)."><input aria-label="Node max terminals" type="number" min={1} max={500} value={edit.max_terminals} onChange={e => setEdit({ ...edit, max_terminals: e.target.value })} /></Field>
        <label><input type="checkbox" checked={!!edit.default_for_new} onChange={e => setEdit({ ...edit, default_for_new: e.target.checked })} /> Default for new MT5 accounts</label>
        <label><input type="checkbox" checked={edit.status !== 'DISABLED'} onChange={e => setEdit({ ...edit, status: e.target.checked ? 'ACTIVE' : 'DISABLED' })} /> In use (off: its routes are skipped, failover goes to the next route)</label>
        <Field label="Notes"><input aria-label="Node notes edit" maxLength={500} value={edit.notes ?? ''} onChange={e => setEdit({ ...edit, notes: e.target.value })} /></Field>
      </form>
    </FloatingWindow>}
  </section>;
}
