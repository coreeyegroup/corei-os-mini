/* Setup home: the systematic path from nothing to a first trade, per track, and the gates that hold each step.
 *
 * Why it exists: every feature existed but nothing said what to do first, or whether a step was done. This page reads
 * the gate status the server computes from real data (never ticked by hand) and shows, per track (Paper, DEMO, LIVE),
 * the eight steps in order: done / open / blocked / overridden, why, how to fix, and a button to the form that fixes it.
 * SetupGates sets each gate's level (off · warn · soft · supervised · hard) and handles scoped, time-boxed overrides
 * (request, second-person approval, revoke); loosening a LIVE gate waits for a second person.
 * Must never: tick a step by hand; grant or approve an override, or change a level, without the person pressing the
 * button and giving a reason.
 * Dependencies: Configuration.api, StageShell helpers, nav.goto. Consumers: Configuration (sections "guide" and "gates").
 * Architectural position: UI over /api/trading/setup*.
 */
import { useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import type { View } from './layout';
import { Msg, ago, useData, type Row } from './StageShell';

const TRACKS: [string, string, string][] = [
  ['PAPER', 'Paper', 'Simulated orders at live prices. Nothing reaches a broker.'],
  ['DEMO', 'Broker DEMO', 'Real orders to demo accounts.'],
  ['LIVE', 'LIVE', 'Real money. The strictest rules.'],
];
const LEVELS: [string, string][] = [['OFF', 'Off · shown only'], ['WARN', 'Warn · allowed, warned'], ['SOFT', 'Soft · you may override'],
  ['SUPERVISED', 'Supervised · a second person overrides'], ['HARD', 'Hard · no override']];
const STATE: Record<string, [string, string]> = { DONE: ['✓', 'done'], OVERRIDDEN: ['⇢', 'overridden'], OPEN: ['○', 'to do'], BLOCKED: ['✕', 'blocked'] };
const ENFORCED = ['PEOPLE', 'ACCOUNTS', 'SYMBOLS', 'MARKET_DATA', 'RISK', 'EVIDENCE', 'PAPER_RECORD'];
const GATE_NAME: Record<string, string> = { PEOPLE: 'People and alerts', ACCOUNTS: 'Brokers and accounts', SYMBOLS: 'Instruments and symbols',
  MARKET_DATA: 'Market data', RISK: 'Risk', EVIDENCE: 'Strategies on evidence', PAPER_RECORD: 'Paper record' };
const words = (v?: string) => String(v ?? '').toLowerCase().replaceAll('_', ' ');
const go = (g: Row) => goto(g.view as View, g.section);

function useTrack(): [string, (t: string) => void] {
  const [t, setT] = useState(() => { try { return window.localStorage.getItem('corei-os-mini.setup.track') || ''; } catch { return ''; } });
  return [t, (x: string) => { setT(x); try { window.localStorage.setItem('corei-os-mini.setup.track', x); } catch { /* storage may be unavailable */ } }];
}

export function SetupHome() {
  const [st, error, reload] = useData<Row>('trading/setup', 30_000);
  const [picked, setTrack] = useTrack();
  const [msg, setMsg] = useState({ error: '', notice: '' });
  if (!st) return <div className="cfg-pane"><Msg error={error} />{!error && <p role="status">Checking every step…</p>}</div>;
  if (!st.tracks || !st.policy) return <div className="cfg-pane"><p role="alert" className="cfg-error">The setup status could not be read from the server (an older server version?). Update the server, then reload.</p></div>;
  const track = picked && st.tracks[picked] ? picked : st.policy.target_track;
  const t = st.tracks[track];
  const pct = Math.round((100 * t.done) / t.total);
  const request = async (gate: Row, accountId?: string, accountCode?: string) => {
    const reason = window.prompt(`Override “${gate.name}” on ${track}${accountCode ? ` for ${accountCode} only` : ''} for ${st.policy.default_hours} hours (max ${st.policy.max_hours[track]}). ` +
      `${gate.level === 'SUPERVISED' ? 'A second person must approve it. ' : ''}Reason (recorded, at least 5 characters):`)?.trim();
    if (reason === undefined) return;
    if (reason.length < 5) { setMsg({ error: 'Nothing was asked: a reason of at least 5 characters is needed.', notice: '' }); return; }
    try {
      const o = await api('trading/setup/overrides', 'POST', { gate: gate.id, track, reason, ...(accountId ? { account_id: accountId } : {}) });
      setMsg({ error: '', notice: o.approved_at ? `Override active until ${new Date(o.expires_at).toLocaleString()}.` : 'Override asked: a second person approves it under Gates & overrides.' });
      reload();
    } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); }
  };
  return <div className="cfg-pane su" aria-label="Setup path">
    <nav className="su-tracks" aria-label="Track">{TRACKS.map(([id, label, hint]) => { const x = st.tracks[id]; return <button key={id} aria-current={id === track ? 'page' : undefined} title={hint} onClick={() => setTrack(id)}>
      <b>{label}</b> <small>{x.done}/{x.total}{x.complete ? ' ✓' : ''}{id === st.policy.target_track ? ' · target' : ''}</small></button>; })}</nav>
    <div className={`su-next ${t.next ? '' : 'done'}`} role="status">
      <div className="su-bar" aria-label={`${pct}% done`}><span style={{ width: `${pct}%` }} /></div>
      {t.next ? <><b>Next step · {t.next.name}:</b> {t.next.text} {t.next.fix && <span className="cfg-note">{t.next.fix}</span>}
        <button className="primary" onClick={() => go(t.next.goto)}>Do it ↗</button></>
        : <><b>{TRACKS.find(x => x[0] === track)?.[1]} is set up.</b> Every step is done{st.overrides.some((o: Row) => o.track === track) ? ' or overridden' : ''}. Checked {ago(st.checked_at)}.</>}
    </div>
    <Msg error={msg.error} notice={msg.notice} />
    <ol className="su-steps">{t.gates.map((g: Row) => { const [mark, label] = STATE[g.state] ?? ['○', g.state]; return <li key={g.id} className={`su-step ${g.state.toLowerCase()}`}>
      <div className="su-step-head"><span className="su-mark" aria-hidden="true">{mark}</span>
        <b>{g.step} · {g.name}</b> <small>unlocks {g.unlocks.toLowerCase()}</small>
        <span className={`su-level ${g.level.toLowerCase()}`} title="How strictly this step holds actions on this track (Gates & overrides)">{g.level === 'INFO' ? 'status' : words(g.level)}</span>
        <span className="su-state">{label}</span>
        <span className="push" /><button onClick={() => go(g.goto)}>{g.state === 'DONE' ? 'Open' : 'Do it'} ↗</button>
        {['SOFT', 'SUPERVISED'].includes(g.level) && g.open_items.length > 0 && <button onClick={() => void request(g)}>Override…</button>}</div>
      {g.items.length > 0 && <ul className="su-items">{g.items.map((i: Row, n: number) => <li key={n} className={g.open_items.some((x: Row) => x.text === i.text) ? '' : 'covered'}>
        {i.text} {i.fix && <span className="cfg-note">{i.fix}</span>}
        {i.account_id && ['SOFT', 'SUPERVISED'].includes(g.level) && g.open_items.some((x: Row) => x.account_id === i.account_id) &&
          <button className="link" onClick={() => void request(g, i.account_id, i.account)}>override for {i.account ?? 'this account'} only</button>}</li>)}</ul>}
      {g.overrides.length > 0 && <p className="cfg-note">{g.overrides.map((o: Row) => `${o.approved_at ? 'Overridden' : 'Override asked'}${o.account_code ? ` for ${o.account_code}` : ''} by ${o.requested_by}${o.approved_by && o.approved_by !== o.requested_by ? `, approved by ${o.approved_by}` : ''}${o.expires_at ? ` until ${new Date(o.expires_at).toLocaleString()}` : ''}: ${o.reason}`).join(' · ')}</p>}
    </li>; })}</ol>
    <p className="cfg-note">Steps are checked from your real data every time this page opens, so a feed that stops or a limit that is deleted reopens its step. How strictly each step holds actions (off, warn, soft, supervised, hard) is set under <button className="link" onClick={() => goto('Configuration', 'gates')}>Gates & overrides</button>.</p>
  </div>;
}

export function SetupGates({ me, roles }: { me: string; roles: string[] }) {
  const [st, error, reload] = useData<Row>('trading/setup', 30_000);
  const [rep, setRep] = useState<Row | null>(null);
  const [levels, setLevels] = useState<Record<string, Record<string, string>> | null>(null);
  const [preset, setPreset] = useState<string | null>(null);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const canSet = roles.includes('ADMIN') || roles.includes('RISK_APPROVER');
  if (!st) return <div className="cfg-pane"><Msg error={error} />{!error && <p role="status">Loading…</p>}</div>;
  if (!st.tracks || !st.policy) return <div className="cfg-pane"><p role="alert" className="cfg-error">The gate settings could not be read from the server (an older server version?). Update the server, then reload.</p></div>;
  const pol = st.policy;
  const cur = levels ?? pol.levels;
  const act = async (f: () => Promise<Row>, ok: string) => { try { await f(); setMsg({ error: '', notice: ok }); reload(); } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); } };
  const reasonOf = (q: string) => { const r = window.prompt(q + ' (at least 5 characters)')?.trim(); if (r !== undefined && r.length < 5) setMsg({ error: 'Nothing was done: a reason of at least 5 characters is needed.', notice: '' }); return r && r.length >= 5 ? r : ''; };
  const save = () => { const reason = reasonOf('Reason for changing the gate levels (audit log)'); if (!reason) return;
    void act(async () => { const r = await api('trading/setup/policy', 'PUT', { preset: preset ?? pol.preset, levels: levels ?? pol.custom, target_track: pol.target_track,
      default_hours: pol.default_hours, max_hours: pol.max_hours, reason }); setLevels(null); setPreset(null);
      if (r.pending) setMsg({ error: '', notice: '' }); return r; }, 'Saved. Loosening a LIVE gate waits for a second person.'); };
  const pending = st.pending;
  const waiting = st.overrides.filter((o: Row) => !o.approved_at);
  const active = st.overrides.filter((o: Row) => o.approved_at);
  return <div className="cfg-pane su" aria-label="Gates and overrides">
    <Msg error={msg.error || error} notice={msg.notice} />
    {pending && <div className="su-pending" role="alert"><b>Waiting for a second person:</b> {pending.by} asked to loosen LIVE gates ({pending.loosens.join('; ')}): {pending.reason}
      {canSet && pending.by !== me && <><button className="primary" onClick={() => { const r = reasonOf('Reason for approving'); if (r) void act(() => api('trading/setup/policy/approve', 'POST', { reason: r }), 'Approved: the new levels apply.'); }}>Approve</button>
        <button onClick={() => { const r = reasonOf('Reason for rejecting'); if (r) void act(() => api('trading/setup/policy/reject', 'POST', { reason: r }), 'Rejected.'); }}>Reject</button></>}</div>}
    <section aria-label="Gate levels"><h3>How strictly each step holds actions</h3>
      <p className="cfg-note">Checked when order sending is switched on, a workspace starts and a deployment is activated; hard gates also at the order dispatcher. Overrides are scoped to one gate and track (optionally one account) and expire.</p>
      <div className="su-row"><label>Preset <select aria-label="Gate preset" disabled={!canSet} value={preset ?? pol.preset} onChange={e => { setPreset(e.target.value); setLevels({}); }}>
        <option value="RECOMMENDED">Recommended</option><option value="ALL_WARN">All warn (flexible)</option><option value="STRICT">Strict</option><option value="ALL_OFF">All off (sandbox)</option></select></label>
        <span className="cfg-note">Target track (where people land until it is set up): <b>{pol.target_track}</b> · overrides last {pol.default_hours} h by default, at most {pol.max_hours.PAPER} / {pol.max_hours.DEMO} / {pol.max_hours.LIVE} h (Paper / DEMO / LIVE).</span></div>
      <table className="cfg-table compact su-levels"><thead><tr><th>Step</th>{TRACKS.map(([t, l]) => <th key={t}>{l}</th>)}</tr></thead>
        <tbody>{ENFORCED.map(g => <tr key={g}><td>{GATE_NAME[g]}</td>{TRACKS.map(([t]) => {
          const applies = !(t === 'PAPER' && (g === 'RISK' || g === 'PAPER_RECORD'));
          const value = (levels && levels[t]?.[g]) ?? (preset ? undefined : cur[t]?.[g]) ?? (preset ? '(preset)' : 'OFF');
          return <td key={t}>{applies ? <select aria-label={`${g} ${t}`} disabled={!canSet} value={value} onChange={e => setLevels(l => ({ ...(l ?? pol.custom), [t]: { ...((l ?? pol.custom)[t] ?? {}), [g]: e.target.value } }))}>
            {value === '(preset)' && <option value="(preset)">preset</option>}{LEVELS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select> : '—'}</td>; })}</tr>)}</tbody></table>
      {canSet && <span className="mw-actions"><button disabled={!levels && !preset} onClick={() => { setLevels(null); setPreset(null); }}>Undo</button>
        <button className="primary" disabled={!levels && !preset} onClick={save}>Save levels</button></span>}
    </section>
    <section aria-label="Overrides"><h3>Overrides</h3>
      {!waiting.length && !active.length && <p className="cfg-note">No override is active. Ask for one from a blocked step on the Setup path.</p>}
      <table className="cfg-table compact"><tbody>{[...waiting, ...active].map((o: Row) => <tr key={o.id}>
        <td>{GATE_NAME[o.gate]} · {o.track}{o.account_code ? ` · ${o.account_code}` : ''}</td><td>{o.reason}</td>
        <td>{o.approved_at ? `active until ${new Date(o.expires_at).toLocaleString()}` : `waiting for a second person (${o.hours} h)`}</td>
        <td>asked by {o.requested_by}{o.approved_by && o.approved_by !== o.requested_by ? `, approved by ${o.approved_by}` : ''}</td>
        <td>{!o.approved_at && canSet && o.requested_by !== me && <button className="primary" onClick={() => void act(() => api(`trading/setup/overrides/${o.id}/approve`, 'POST'), 'Override approved.')}>Approve</button>}
          <button onClick={() => { const r = reasonOf('Reason for revoking'); if (r) void act(() => api(`trading/setup/overrides/${o.id}/revoke`, 'POST', { reason: r }), 'Revoked: the step holds again.'); }}>Revoke</button></td></tr>)}</tbody></table>
    </section>
    <section aria-label="Setup report"><h3>Setup report</h3>
      <span className="mw-actions"><button onClick={() => void api('trading/setup/report').then(setRep).catch(e => setMsg({ error: (e as Error).message, notice: '' }))}>Build report</button>
        {rep && <button onClick={() => window.print()}>Print</button>}</span>
      {rep && <div className="su-report">{TRACKS.map(([t, l]) => <div key={t}><h4>{l} · {rep.tracks[t].done}/{rep.tracks[t].total}</h4>
        <ul>{rep.tracks[t].gates.map((g: Row) => <li key={g.id}>{STATE[g.state]?.[0]} {g.name} · {words(g.level)} · {STATE[g.state]?.[1]}{g.open_items.length ? `: ${g.open_items.map((i: Row) => i.text).join('; ')}` : ''}</li>)}</ul></div>)}
        <h4>Last gate decisions</h4><ul>{rep.history.slice(0, 50).map((h: Row, n: number) => <li key={n}>{new Date(h.occurred_at).toLocaleString()} · {h.actor} · {words(h.action)} · {h.reason}</li>)}</ul>
        <small>Generated {new Date(rep.checked_at).toLocaleString()}.</small></div>}
    </section>
  </div>;
}
