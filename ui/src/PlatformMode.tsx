/* Platform mode: switch between Development (build and test the platform as a user) and Production (real trading).
 *
 * Why it exists: one installation is used for two jobs; the rules that keep real trading safe get in the way of testing,
 * and testing freedoms must never reach real trading. One administrator switch, with a reason, decides which apply.
 * Purpose: show the current mode and what it means; switch (Production needs the typed word PRODUCTION); in Development,
 * the "Allow LIVE in Development" option (typed word LIVE) and "Reset test data" (typed word RESET; paper accounts are
 * archived with their history and replaced by fresh copies).
 * Must never: turn LIVE order sending on (only the Order sending page does that, with its own checks); offer reset in
 * Production; act without a reason.
 * Dependencies: Configuration.api, StageShell (Msg, useData, when), Info.
 * Consumers: Configuration (Setup › 9 · Admin › Platform mode).
 * Architectural position: UI over /api/config/platform-mode.
 */
import { useState } from 'react';
import { api } from './Configuration';
import { Info } from './Info';
import { Msg, useData, when, type Row } from './StageShell';

const RULES: [string, string, string][] = [
  ['Setup gates', 'PAPER and DEMO gates only warn; LIVE gates keep their levels', 'Strict: gates block at their chosen levels'],
  ['LIVE order sending', 'Forced OFF and cannot be switched on — unless "Allow LIVE in Development" is on', 'Available through Order sending (Risk approver, password, readiness, four-eyes)'],
  ['Orders', 'Paper and DEMO', 'Paper, DEMO and LIVE'],
  ['Passwords', 'At least 2 characters', 'At least 6 characters'],
  ['Test data reset', 'Available (paper accounts start fresh; history kept)', 'Not available'],
];

export default function PlatformMode({ isAdmin }: { isAdmin: boolean }) {
  const [m, error, reload] = useData<Row>('config/platform-mode', 15_000);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [busy, setBusy] = useState(false);
  const [reset, setReset] = useState<Row | null>(null);
  if (!m) return <div className="cfg-pane"><Msg error={error} />{!error && <p role="status">Loading…</p>}</div>;
  const dev = m.mode === 'DEVELOPMENT';
  const ask = (q: string) => { const r = window.prompt(q + ' (at least 5 characters)')?.trim(); if (r !== undefined && r.length < 5) setMsg({ error: 'Nothing was done: a reason of at least 5 characters is needed.', notice: '' }); return r && r.length >= 5 ? r : null; };
  const run = async (f: () => Promise<Row>, ok: (r: Row) => string) => {
    setBusy(true); setMsg({ error: '', notice: '' });
    try { const r = await f(); setMsg({ error: '', notice: ok(r) }); reload(); window.dispatchEvent(new Event('corei-session')); }
    catch (e) { setMsg({ error: (e as Error).message, notice: '' }); } finally { setBusy(false); }
  };
  const put = (mode: string, allow: boolean, confirm = '') => {
    const reason = ask(`Reason for ${mode === m.mode ? (allow ? 'allowing' : 'stopping') + ' LIVE in Development' : 'switching to ' + mode.toLowerCase()}`);
    if (!reason) return;
    void run(() => api('config/platform-mode', 'PUT', { mode, allow_live_in_development: allow, confirm, reason, version: m.version }),
      r => `Now in ${r.mode.toLowerCase()} mode.` + (r.live_switched_off ? ' LIVE order sending was turned off.' : ''));
  };
  const toProduction = () => { const c = window.prompt('Switch to Production: gates become strict and passwords need 6 characters. Type PRODUCTION to confirm.'); if (c !== null) put('PRODUCTION', false, c.trim()); };
  const toDevelopment = () => { if (window.confirm('Switch to Development? PAPER and DEMO gates will only warn, and LIVE order sending will be turned OFF.')) put('DEVELOPMENT', false); };
  const allowLive = () => { const c = window.prompt('Allow LIVE order sending in Development? It stays OFF until someone turns it on in Order sending, with all LIVE checks. Type LIVE to confirm.'); if (c !== null) put('DEVELOPMENT', true, c.trim()); };
  const doReset = () => {
    const c = window.prompt('Reset test data: every active paper account is archived (its history kept) and replaced by a fresh copy with the same settings. Accounts with active runs are skipped. Type RESET to confirm.');
    if (c === null) return;
    const reason = ask('Reason for resetting test data'); if (!reason) return;
    void run(async () => { const r = await api('config/platform-mode/reset-test-data', 'POST', { confirm: c.trim(), reason }); setReset(r); return r; },
      r => `${r.reset.length} paper account(s) reset, ${r.skipped.length} skipped.`);
  };
  return <div className="cfg-pane pm" aria-label="Platform mode">
    <Msg error={msg.error || error} notice={msg.notice} />
    <div className={`pm-now ${dev ? 'dev' : 'prod'}`} role="status">
      <b>{dev ? 'Development' : 'Production'}</b>
      <span>{dev ? (m.allow_live_in_development ? 'Testing — LIVE allowed' : 'Testing — LIVE off') : 'Real trading rules'}</span>
      <small>{m.updated_by && m.updated_by !== 'system' ? `Set by ${m.updated_by}${m.updated_at ? ' · ' + when(m.updated_at) : ''}${m.reason ? ' · ' + m.reason : ''}` : 'Default since installation'}</small>
      <Info label="About platform mode">Development is for building the platform and testing it as a user. Production is for real trading. Only an administrator switches, with a reason; every switch is in the audit log.</Info>
    </div>
    <table className="cfg-table pm-rules" aria-label="What each mode does"><thead><tr><th /><th className={dev ? 'on' : ''}>Development</th><th className={dev ? '' : 'on'}>Production</th></tr></thead>
      <tbody>{RULES.map(([k, d, p]) => <tr key={k}><th>{k}</th><td className={dev ? 'on' : ''}>{d}</td><td className={dev ? '' : 'on'}>{p}</td></tr>)}</tbody></table>
    {isAdmin ? <div className="pm-actions">
      {dev ? <>
        <button className="primary" disabled={busy} onClick={toProduction}>Switch to Production…</button>
        {m.allow_live_in_development
          ? <button disabled={busy} onClick={() => put('DEVELOPMENT', false)}>Stop LIVE in Development (turns LIVE off)</button>
          : <button disabled={busy} onClick={allowLive}>Allow LIVE in Development…</button>}
        <button disabled={busy} onClick={doReset}>Reset test data…</button>
      </> : <button className="primary" disabled={busy} onClick={toDevelopment}>Switch to Development…</button>}
    </div> : <p className="cfg-note">Only an administrator can change the platform mode.</p>}
    {reset && <table className="cfg-table" aria-label="Reset result"><thead><tr><th>Paper account</th><th>Result</th></tr></thead>
      <tbody>{reset.reset.map((r: Row) => <tr key={r.old}><td>{r.old}</td><td>archived · fresh copy {r.new}</td></tr>)}
        {reset.skipped.map((r: Row) => <tr key={r.code}><td>{r.code}</td><td className="dk-warn">skipped: {r.why}</td></tr>)}</tbody></table>}
  </div>;
}
