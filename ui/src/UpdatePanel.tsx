/* Settings › Update: see whether GitHub main has a newer COREI-OS-MINI release and install it from here.
 *
 * Why it exists: updating needed SSH and five commands. The workstation cannot deploy itself (no root); it asks the
 * server's updater, which checks everything again, backs up, fast-forwards, builds, migrates, deploys, verifies and
 * rolls back on failure. This screen shows what would change, what blocks it now, what the operator must accept,
 * and the progress and log while it runs.
 * Purpose: running vs latest version; Check now; what's new (commits, database changes, GitHub checks); blockers;
 * Update now (reason, every acknowledgement, typed UPDATE, password asked again by the server); live steps and log;
 * reload when the new version is running; history (audit); manual commands as the fallback.
 * Must never: start anything itself; hide the restart; offer Update while something blocks it.
 * Dependencies: Configuration.api (handles the password prompt on 428), StageShell (Msg, useData, when), Info.
 * Consumers: Configuration (Setup › 7 · Settings › Update).
 * Architectural position: UI over /api/config/update.
 */
import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import { Info } from './Info';
import { Msg, useData, when, type Row } from './StageShell';

const STATE: Record<string, string> = { RUNNING: 'Running…', SUCCEEDED: 'Updated', ROLLED_BACK: 'Failed — rolled back to the previous version',
  FAILED: 'Failed — needs attention', REFUSED: 'Not started' };

export default function UpdatePanel({ isAdmin }: { isAdmin: boolean }) {
  const [poll, setPoll] = useState(30_000);
  const [u, error, reload] = useData<Row>(isAdmin ? 'config/update' : null, poll);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<{ reason: string; confirm: string; acks: string[] } | null>(null);
  const opened = useRef<string | null>(null);   // the version this page was loaded with
  const running = !!u?.update_running;
  if (u && opened.current === null) opened.current = u.running.version;
  useEffect(() => { setPoll(running || busy ? 3_000 : 30_000); }, [running, busy]);
  if (!isAdmin) return <div className="cfg-pane"><p className="cfg-note">Only an administrator can see and install updates.</p></div>;
  if (!u) return <div className="cfg-pane"><Msg error={error ? `${error} — the workstation may be restarting for an update; this page retries.` : ''} />{!error && <p role="status">Loading…</p>}</div>;
  const a: Row = u.available ?? {};
  const st: Row | null = u.status;
  const newer = !!a.behind;
  const check = async () => { setBusy(true); setMsg({ error: '', notice: '' });
    try { await api('config/update/check', 'POST'); setMsg({ error: '', notice: 'Checking GitHub… the result appears here in a few seconds.' }); window.setTimeout(reload, 4000); }
    catch (e) { setMsg({ error: (e as Error).message, notice: '' }); } finally { setBusy(false); } };
  const apply = async () => {
    if (!form) return;
    setBusy(true); setMsg({ error: '', notice: '' });
    try {
      const r = await api('config/update/apply', 'POST', { target_sha: a.latest_sha, reason: form.reason, confirm: form.confirm, acknowledged: form.acks });
      setForm(null); setMsg({ error: '', notice: `Update ${r.id} to ${r.target_version} requested. The workstation restarts in a moment; this page reconnects.` }); reload();
    } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); } finally { setBusy(false); }
  };
  const ackCodes: string[] = (u.acknowledgements ?? []).map((x: Row) => x.code);
  const ready = form && form.reason.trim().length >= 5 && form.confirm === 'UPDATE' && ackCodes.every(c => form.acks.includes(c));
  return <div className="cfg-pane up" aria-label="Update">
    <Msg error={msg.error || error} notice={msg.notice} />
    <div className="up-versions">
      <div><small>Running</small><b>{u.running.version}</b><code>{u.running.commit}</code></div>
      <span aria-hidden="true">→</span>
      <div className={newer ? 'new' : ''}><small>GitHub main</small><b>{a.latest_version ?? '—'}</b><code>{(a.latest_sha ?? '').slice(0, 12) || '—'}</code></div>
      <div className="up-meta"><small>Checked {a.checked_at ? when(a.checked_at) : 'never'} · automatic check every 6 hours</small>
        <span>{newer ? `${a.behind} new commit(s)` : a.checked_at ? 'Up to date' : ''}{a.ci ? ` · GitHub checks ${a.ci.toLowerCase()}` : ''}{a.migrations?.length ? ` · ${a.migrations.length} database change(s)` : ''}</span></div>
      <span className="up-actions">
        <button disabled={busy || running || !u.installed} onClick={() => void check()}>Check now</button>
        <button className="primary" disabled={busy || !u.can_update || !!form} onClick={() => setForm({ reason: '', confirm: '', acks: [] })}>Update now…</button>
        <Info label="How updating works">The server's updater (a separate service with the needed rights) checks GitHub again, backs up the database, moves the code forward to exactly this commit, builds, applies database changes, restarts and verifies. If a step fails after the code moved, it goes back to the previous version automatically (database changes are additive and stay). Every request and outcome is in the audit log.</Info>
      </span>
    </div>
    {u.blockers?.length > 0 && !running && <ul className="up-blockers" aria-label="Why it cannot update now">{u.blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>}
    {form && <form className="pa-form up-form" aria-label="Confirm update" onSubmit={e => { e.preventDefault(); void apply(); }}>
      <h3>Update {u.running.version} → {a.latest_version}<button type="button" className="link" aria-label="Close" onClick={() => setForm(null)}>✕</button></h3>
      {(u.acknowledgements ?? []).map((x: Row) => <label key={x.code} className="up-ack"><input type="checkbox" checked={form.acks.includes(x.code)}
        onChange={() => setForm({ ...form, acks: form.acks.includes(x.code) ? form.acks.filter(c => c !== x.code) : [...form.acks, x.code] })} /> {x.text}</label>)}
      <div className="dk-grid">
        <label>Reason (audit log) <input aria-label="Update reason" required minLength={5} maxLength={500} value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} /></label>
        <label>Type UPDATE to confirm <input aria-label="Type UPDATE" value={form.confirm} onChange={e => setForm({ ...form, confirm: e.target.value.trim() })} autoComplete="off" /></label>
      </div>
      <span className="mw-actions"><small>Your password is asked again before it starts.</small><button type="button" onClick={() => setForm(null)}>Cancel</button>
        <button className="primary" disabled={busy || !ready}>Start update</button></span>
    </form>}
    {st && <section className="up-status" aria-label="Last update">
      <h4>{st.kind === 'APPLY' ? `${STATE[st.state] ?? st.state}` : 'Last check'} {st.to_version ? `· ${st.from_version} → ${st.to_version}` : ''}
        <small>{st.requested_by ? ` by ${st.requested_by}` : ''}{st.started_at ? ` · ${when(st.started_at)}` : ''}</small></h4>
      {st.error && <p className="cfg-error" role="alert">{st.error}</p>}
      <ol className="up-steps">{(st.steps ?? []).map((s: Row) => <li key={s.name} className={s.state.toLowerCase()}><b>{s.name}</b><span>{s.state}</span><small>{s.detail}</small></li>)}</ol>
      {st.state === 'SUCCEEDED' && u.running.version !== opened.current && <p className="cfg-note">The new version is running. <button onClick={() => window.location.reload()}>Reload the workstation</button></p>}
      {u.log && <details open={running}><summary>Log</summary><pre className="up-log">{u.log}</pre></details>}
    </section>}
    {newer && <details className="up-new" open><summary>What's new ({a.commits?.length ?? 0})</summary>
      <table className="cfg-table"><tbody>{(a.commits ?? []).map((c: Row) => <tr key={c.sha}><td><code>{c.sha.slice(0, 8)}</code></td><td>{c.subject}</td><td>{when(c.date)}</td></tr>)}</tbody></table>
      {a.migrations?.length > 0 && <p className="cfg-note">Database changes: {a.migrations.join(', ')} (applied after a backup when backups are on).</p>}</details>}
    <details className="up-history"><summary>History ({u.history?.length ?? 0})</summary>
      <table className="cfg-table"><thead><tr><th>Time</th><th>Event</th><th>By</th><th>From → to</th><th>Reason / error</th></tr></thead>
        <tbody>{(u.history ?? []).map((h: Row, i: number) => <tr key={i}><td>{when(h.at)}</td><td>{h.action.replace('UPDATE_', '').replace('_', ' ').toLowerCase()}</td><td>{h.actor}</td>
          <td>{h.before_state?.version} → {h.after_state?.version}</td><td><small>{h.after_state?.error ?? h.reason}</small></td></tr>)}</tbody></table></details>
    <details className="up-manual"><summary>Update by hand (SSH)</summary><pre className="up-log">{(u.manual_commands ?? []).join('\n')}</pre>
      <p className="cfg-note">Needed once to install the updater, and whenever the updater cannot run.</p></details>
  </div>;
}
