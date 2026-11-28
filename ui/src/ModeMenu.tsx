/* Top-bar Mode chip: shows how the platform runs (Development/Production · Team/Single user) and switches it.
 *
 * Why it exists: the owner switches between building/testing and real trading, and between working alone and as a
 * team, often; the switch belonged in sight, not three levels deep in Setup (28 Nov 2026: "give option for Mode").
 * Purpose: chip "DEV · SINGLE", "PROD · TEAM" (+ "LIVE allowed"); its menu: Development ⇄ Production, allow/stop
 * LIVE in Development, Team ⇄ Single user, reset test data, open the Platform mode page. Uses the same actions as
 * the page (PlatformMode.useModeActions): reason, typed words, password — nothing is switched in one click.
 * Must never: show switches to someone who is not an administrator (they see the state only).
 * Dependencies: PlatformMode.useModeActions, StageShell.useData. Consumers: TopBar (TopBarStatus).
 */
import { useEffect, useRef, useState } from 'react';
import { useModeActions } from './PlatformMode';
import { useData, type Row } from './StageShell';

export default function ModeMenu({ openPage }: { openPage: () => void }) {
  const [m, , reload] = useData<Row>('config/platform-mode', 30_000);
  const [me] = useData<Row>('operator/session', 0);
  const act = useModeActions(m, reload);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => { if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  if (!m) return null;
  const dev = m.mode === 'DEVELOPMENT', solo = m.operating === 'SOLO';
  const admin = (me?.roles ?? []).includes('ADMIN');
  const label = `${dev ? 'DEV' : 'PROD'} · ${solo ? 'SINGLE' : 'TEAM'}${dev && m.allow_live_in_development ? ' · LIVE allowed' : ''}`;
  const item = (text: string, hint: string, run: () => void, danger = false) =>
    <button role="menuitem" className={danger ? 'danger' : ''} disabled={act.busy} onClick={() => { setOpen(false); run(); }}><b>{text}</b><small>{hint}</small></button>;
  return <div className="tb-menu mode-menu" ref={ref}>
    <button className={`chip mode-chip ${dev ? 'dev' : 'prod'}`} aria-label={`Mode: ${label}`} aria-haspopup="menu" aria-expanded={open}
      title="How the platform runs: Development (build and test) or Production (real trading); Team (four-eyes) or Single user. Click to switch."
      onClick={() => setOpen(o => !o)}>{label}</button>
    {open && <div className="tb-pop mode-pop" role="menu" aria-label="Mode">
      <p className="mode-now"><b>{dev ? 'Development' : 'Production'}</b> · {dev ? 'gates warn only; ' + (m.allow_live_in_development ? 'LIVE allowed' : 'LIVE order sending off') : 'strict gates; LIVE through Order sending'}<br />
        <b>{solo ? 'Single user' : 'Team'}</b> · {solo ? 'you confirm your own LIVE steps' : 'a different person confirms LIVE steps'}</p>
      {admin ? <>
        {dev ? item('Switch to Production…', 'Strict gates, 6-character passwords; type PRODUCTION', act.toProduction)
             : item('Switch to Development…', 'Gates warn only; LIVE order sending turned off', act.toDevelopment)}
        {dev && (m.allow_live_in_development ? item('Stop LIVE in Development', 'Turns LIVE order sending off', act.stopLive)
                                             : item('Allow LIVE in Development…', 'Real money possible; type LIVE', act.allowLive, true))}
        {solo ? item('Switch to Team…', 'A different person confirms LIVE steps', act.toTeam)
              : item('Switch to Single user…', 'You confirm your own LIVE steps; type SOLO', act.toSolo)}
        {dev && item('Reset test data…', 'Paper accounts start fresh; history kept; type RESET', act.doReset)}
      </> : <p className="cfg-note">Only an administrator can switch.</p>}
      <button role="menuitem" onClick={() => { setOpen(false); openPage(); }}><b>Platform mode settings…</b><small>What each mode does, history</small></button>
    </div>}
    {(act.msg.error || act.msg.notice) && <span className={`mode-msg ${act.msg.error ? 'bad' : ''}`} role="status" onClick={() => act.setMsg({ error: '', notice: '' })}>{act.msg.error || act.msg.notice}</span>}
  </div>;
}
