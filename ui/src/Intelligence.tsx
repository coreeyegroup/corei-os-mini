/* Stage 2 · Market intelligence: select the collected data for analysis and compare it.
 * Analysis board (every source × instrument, pickers, filters, 26 columns, grouping, 1/2/4 charts with compare,
 * time & sales, the same instrument from every source, CSV) · Stage map (components and build state). */
import { useEffect, useState } from 'react';
import { addReauthHandler, api } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { SignIn } from './Infrastructure';
import MarketBoard from './MarketBoard';
import Stage from './Pipeline';
import type { View } from './layout';
import { useSection } from './nav';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

type Tab = 'compare' | 'stage';
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'compare', label: 'Select & compare', hint: 'Choose sources and instruments for analysis, compare them side by side, chart and overlay sources. Data comes from Stage 1 · Market data.' },
  { id: 'stage', label: 'Stage map', hint: 'What this stage does, its components and how far each is built.' },
];

export default function Intelligence({ open }: { open: (v: View) => void }) {
  const [user, setUser] = useState<Record<string, any> | null | undefined>(undefined), [tab, setTab] = useSection<Tab>('Intelligence', 'compare', TABS.map(t => t.id));
  const [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check(); window.addEventListener('corei-session', check); return () => window.removeEventListener('corei-session', check);
  }, []);
  if (tab === 'stage') return <div className="cfg md"><Tabs tab={tab} setTab={setTab} user={user?.username} /><Stage view="Intelligence" open={open} /></div>;
  if (user === undefined) return <div className="content"><p>Checking sign-in…</p></div>;
  if (user === null) return <SignIn title="MARKET INTELLIGENCE" done={s => setUser(s)} />;
  const current = TABS.find(t => t.id === tab)!;
  return <div className="cfg md">
    <Tabs tab={tab} setTab={setTab} user={user.username} />
    <section className="cfg-main">
      <header className="cfg-head"><h1>{current.label}</h1><p>{current.hint}</p></header>
      <div className="cfg-pane"><MarketBoard /></div>
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

function Tabs({ tab, setTab, user }: { tab: Tab; setTab: (t: Tab) => void; user?: string }) {
  return <div className="cfg-topbar"><span className="eyebrow">2 · MARKET INTELLIGENCE</span>
    <nav className="cfg-nav" aria-label="Market intelligence sections">{TABS.map(t =>
      <button key={t.id} aria-current={tab === t.id ? 'page' : undefined} onClick={() => setTab(t.id)}><span>{t.label}</span></button>)}</nav>
    <div className="cfg-user">{user ?? ''}</div></div>;
}
