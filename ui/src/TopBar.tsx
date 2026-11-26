/* Top bar: the always-visible safety status (left) and the layout, notification and account controls (right).
 *
 * Why it exists: the top bar is the one place visible from every screen, so it carries what an operator must never
 * miss — feeds, whether orders can leave the building, engaged kill switches, unresolved (UNKNOWN) orders, setup
 * progress, approvals — and, as in VS Code, toggles for the side bar, panel and inspector, a layout menu, a bell and
 * the account menu.
 * Purpose: TopBarStatus (status chips, each chosen in Settings › Top bar; kill switches and unknown orders are shown
 * whenever present, whatever the setting) and TopBarRight (layout toggles, customise-layout menu, notifications,
 * settings gear, account menu with sign out).
 * Must never: change trading state — every chip only opens the page that owns the control; hide an engaged kill
 * switch or an UNKNOWN order.
 * Dependencies: GET /trading/topbar, nav.Overview, userSettings. Consumers: App header.
 * Architectural position: UI shell.
 */
import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import type { View } from './layout';
import type { Overview } from './nav';
import { useData } from './StageShell';
import { fmtTime, setting, setSetting, TOPBAR_ITEMS, useSettings } from './userSettings';

type Open = (view: View, section?: string) => void;
type Setup = { land_on_setup: boolean; done: number; total: number; target_track: string; overrides: number } | null;
type TopData = { kill_switches: Record<string, number>; desks: Record<string, number>; unknown_orders: number; approvals_waiting: number; server_time: string };
type LayoutBits = { left: boolean; right: boolean; bottom: boolean; maximized: boolean };

const on = (id: string) => setting<boolean>(`topbar.${id}`) !== false;

/** Forex runs Sunday 22:00 to Friday 22:00 UTC; NSE cash 09:15–15:30 IST Monday–Friday (holidays not shown here). */
export function sessions(now: Date) {
  const d = now.getUTCDay(), m = now.getUTCHours() * 60 + now.getUTCMinutes();
  const fx = !(d === 6 || (d === 5 && m >= 22 * 60) || (d === 0 && m < 22 * 60));
  const ist = new Date(now.getTime() + 330 * 60_000), im = ist.getUTCHours() * 60 + ist.getUTCMinutes(), idow = ist.getUTCDay();
  const nse = idow >= 1 && idow <= 5 && im >= 9 * 60 + 15 && im < 15 * 60 + 30;
  return { fx, nse };
}

function Clock({ server }: { server?: string }) {
  const [now, setNow] = useState(() => new Date());
  const skew = useRef(0);
  useEffect(() => { if (server) skew.current = new Date(server).getTime() - Date.now(); }, [server]);
  useEffect(() => { const t = window.setInterval(() => setNow(new Date(Date.now() + skew.current)), 1000); return () => window.clearInterval(t); }, []);
  const s = sessions(now);
  return <span className="chip clock" title={`Server time (${setting('region.timezone') === 'LOCAL' ? 'this computer' : setting('region.timezone')}). Display only — trading uses market event time.\nNSE holidays are not reflected here.`}>
    {fmtTime(now, false)}<span className={`mkt ${s.fx ? 'open' : ''}`}>FX</span><span className={`mkt ${s.nse ? 'open' : ''}`}>NSE</span></span>;
}

export function TopBarStatus({ overview, signedOut, setup, live, open }: { overview: Overview | null; signedOut: boolean; setup: Setup; live: string[]; open: Open }) {
  useSettings();
  const [top] = useData<TopData>(overview ? 'trading/topbar' : null, 15_000);
  if (signedOut) return <div className="status-chips" aria-label="Live status"><button className="chip" onClick={() => open('Overview')}>Sign in</button></div>;
  if (!overview) return <div className="status-chips" aria-label="Live status" />;
  const kills = Object.entries(top?.kill_switches ?? {}).filter(([, n]) => n > 0);
  const running = top?.desks?.RUNNING ?? 0, paused = top?.desks?.PAUSED ?? 0;
  return <div className="status-chips" aria-label="Live status">
    {kills.length > 0 && <button className="chip bad" title="Engaged kill switches by scope" onClick={() => open('Risk', 'kill')}>
      ⛔ Kill {kills.map(([s, n]) => `${s.toLowerCase()} ${n}`).join(' · ')}</button>}
    {(top?.unknown_orders ?? 0) > 0 && <button className="chip bad" title="Orders whose outcome at the broker is unknown. They are never resent; resolve them on the Orders page." onClick={() => open('Execution')}>
      ? Unknown orders {top!.unknown_orders}</button>}
    {on('feeds') && <button className={`chip ${overview.feeds.problems ? 'bad' : overview.feeds.live ? 'ok' : ''}`} title="Market-data feeds" onClick={() => open('Market data', 'live')}>
      <span className="chip-dot" />Feeds {overview.feeds.live}/{overview.feeds.collectors}{overview.feeds.problems ? ` · ${overview.feeds.problems} problem${overview.feeds.problems > 1 ? 's' : ''}` : ''}</button>}
    {on('orders') && <button className={`chip ${live.includes('LIVE') ? 'live' : ''}`} title={overview.order_adapter === false && live.length ? 'Order sending is switched ON, but no order adapter is built: nothing can be sent yet' : 'Order sending (firm-wide switch)'} onClick={() => open('Configuration', 'controls')}>
      Orders {live.length ? `ON · ${live.join(', ')}${overview.order_adapter === false ? ' · no adapter' : ''}` : 'OFF'}</button>}
    {on('approvals') && overview.approvals > 0 && <button className="chip warn" onClick={() => open('Configuration', 'approvals')}>Approvals {overview.approvals}</button>}
    {on('setup') && setup?.total ? <button className={`chip ${setup.land_on_setup ? 'warn' : 'ok'}`} title={`Setup of the ${setup.target_track} track: ${setup.done} of ${setup.total} steps done${setup.overrides ? `; ${setup.overrides} override(s) active` : ''}`} onClick={() => open('Configuration', 'guide')}>
      Setup {setup.target_track} {setup.done}/{setup.total}{setup.overrides ? ` · ${setup.overrides} override${setup.overrides > 1 ? 's' : ''}` : ''}</button> : null}
    {on('desks') && (running + paused > 0) && <button className="chip" title="Workspaces running / paused" onClick={() => open('My Workspaces')}>▶ {running}{paused ? ` · ❚❚ ${paused}` : ''}</button>}
    {on('clock') && <Clock server={top?.server_time} />}
  </div>;
}

function Menu({ label, icon, children, badge }: { label: string; icon: string; children: (close: () => void) => React.ReactNode; badge?: number }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => { if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close); document.addEventListener('keydown', close);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close); };
  }, [open]);
  return <div className="tb-menu" ref={ref}>
    <button className="tb-icon" aria-label={label} title={label} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(o => !o)}>{icon}{badge ? <span className="tb-badge">{badge > 99 ? '99+' : badge}</span> : null}</button>
    {open && <div className="tb-pop" role="menu" aria-label={label}>{children(() => setOpen(false))}</div>}
  </div>;
}

export function TopBarRight({ overview, layout, patch, open, onBell }: { overview: Overview | null; layout: LayoutBits; patch: (p: Partial<LayoutBits>) => void; open: Open; onBell: () => void }) {
  useSettings();
  const attention = overview?.attention ?? [];
  const critical = attention.filter(a => a.severity === 'CRITICAL').length;
  const toggles: [keyof LayoutBits, string, string, string][] = [
    ['left', 'Toggle side bar (Ctrl+B)', '◧', 'Side bar'], ['bottom', 'Toggle panel (Ctrl+J)', '⬓', 'Panel'], ['right', 'Toggle inspector (Ctrl+Alt+B)', '◨', 'Inspector'],
  ];
  return <div className="tb-right">
    {on('layout') && <div className="tb-toggles" role="group" aria-label="Layout">
      {toggles.map(([k, title, icon]) => <button key={k} className="tb-icon" aria-label={title} title={title} aria-pressed={!!layout[k]} onClick={() => patch({ [k]: !layout[k] })}>{icon}</button>)}
      <Menu label="Customize layout" icon="⋯">{close => <>
        <div className="tb-pop-h">Visibility</div>
        {toggles.map(([k, , , label]) => <label key={k} role="menuitemcheckbox" aria-checked={!!layout[k]}><input type="checkbox" checked={!!layout[k]} onChange={() => patch({ [k]: !layout[k] })} />{label}</label>)}
        <label role="menuitemcheckbox" aria-checked={layout.maximized}><input type="checkbox" checked={layout.maximized} onChange={() => patch({ maximized: !layout.maximized })} />Focus mode (one group, no side bar)</label>
        <div className="tb-pop-h">Top bar shows</div>
        {TOPBAR_ITEMS.filter(([id]) => id !== 'layout').map(([id, label]) => <label key={id} role="menuitemcheckbox" aria-checked={on(id)}><input type="checkbox" checked={on(id)} onChange={e => setSetting(`topbar.${id}`, e.target.checked)} />{label}</label>)}
        <button className="link" onClick={() => { close(); open('Settings'); }}>All settings…</button>
      </>}</Menu>
    </div>}
    {overview && <Menu label={`Notifications${attention.length ? ` (${attention.length})` : ''}`} icon="🔔" badge={attention.length}>{close => <>
      <div className="tb-pop-h">Needs attention{critical ? ` · ${critical} critical` : ''}</div>
      {attention.length ? attention.slice(0, 12).map((a, i) => <button key={i} className={`tb-note ${a.severity.toLowerCase()}`} onClick={() => { close(); open(a.goto.view, a.goto.section); }}>
        <b>{a.title}</b><small>{a.area} · {a.detail}</small></button>) : <p className="tb-empty">Nothing needs attention.</p>}
      <button className="link" onClick={() => { close(); onBell(); }}>Open the panel</button>
    </>}</Menu>}
    <button className="tb-icon" aria-label="Settings" title="Settings (Ctrl+,)" onClick={() => open('Settings')}>⚙</button>
    {overview && <Menu label={`Account ${overview.user.username}`} icon={overview.user.username.slice(0, 1).toUpperCase()}>{close => <>
      <div className="tb-pop-h">{overview.user.username}</div>
      <p className="tb-empty">Roles: {overview.user.roles.join(', ') || '—'}</p>
      <button className="link" onClick={() => { close(); open('Settings'); }}>Settings</button>
      <button className="link" onClick={() => { close(); open('Configuration', 'users'); }}>Users, roles & password</button>
      <button className="link" onClick={() => { void api('operator/logout', 'POST').finally(() => window.dispatchEvent(new Event('corei-session'))); close(); }}>Sign out</button>
    </>}</Menu>}
  </div>;
}
