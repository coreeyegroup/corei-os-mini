/* Stage 2 · Market intelligence: the screener (ranked universe from the feature store), one instrument across every
 * timeframe, the session map, and select & compare. Analysis board (every source × instrument, pickers, filters, 26 columns, grouping, 1/2/4 charts with compare,
 * time & sales, the same instrument from every source, CSV) · Stage map (components and build state). */
import { useEffect, useState } from 'react';
import { addReauthHandler, api } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { SignIn } from './Infrastructure';
import { IntelDashboard, InstrumentView, Screener, SessionsView } from './IntelBoard';
import { CalendarView, CorrelationView, RulesView, SourcesView, StrengthView, WatchlistsView } from './IntelWorkspace';
import MarketBoard from './MarketBoard';
import Stage from './Pipeline';
import type { View } from './layout';
import { useSection } from './nav';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

type Tab = 'dashboard' | 'screener' | 'instrument' | 'sources' | 'correlation' | 'strength' | 'watchlists' | 'rules' | 'calendar' | 'sessions' | 'compare' | 'stage';
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'dashboard', label: 'Dashboard', hint: 'Coverage, regime mix per timeframe, what needs history (one click), top attention, biggest moves, and the engine controls.' },
  { id: 'screener', label: 'Screener', hint: 'Every collected instrument ranked for attention: regime, volatility, trend, momentum, cost against ATR and feed quality, from the shared feature store (closed bars, recomputed every minute).' },
  { id: 'instrument', label: 'Instrument', hint: 'One instrument across every timeframe: regime and the reasons, indicators, chart with EMA and Bollinger bands, regime strip and RSI; CSV of the feature history.' },
  { id: 'sources', label: 'Sources', hint: 'One instrument from every broker account side by side: price, deviation from the other sources, spread, feed quality, latency; the cheapest, fastest and cleanest source.' },
  { id: 'correlation', label: 'Correlation', hint: 'How the collected instruments move together (correlation of returns), per timeframe and window.' },
  { id: 'strength', label: 'Strength', hint: 'Currency strength: each currency\'s average move against the others, from every FX pair collected.' },
  { id: 'watchlists', label: 'Watchlists', hint: 'Named groups of instruments across brokers and accounts; filter the screener and scope rules with them.' },
  { id: 'rules', label: 'Rules & alerts', hint: 'Conditions on features (RSI crosses 30, regime becomes trend, ATR rank above 90…) checked on every closed bar; fired rules and alerts.' },
  { id: 'calendar', label: 'Calendar', hint: 'Economic events and holidays (add or import CSV); high-impact events flag event risk on the instruments in those currencies.' },
  { id: 'sessions', label: 'Sessions', hint: 'Which trading centres are open now, overlaps, rollover and the weekend close, and the next changes.' },
  { id: 'compare', label: 'Select & compare', hint: 'Choose sources and instruments for analysis, compare them side by side, chart and overlay sources. Data comes from Stage 1 · Market data.' },
  { id: 'stage', label: 'Stage map', hint: 'What this stage does, its components and how far each is built.' },
];

export default function Intelligence({ open }: { open: (v: View) => void }) {
  const [user, setUser] = useState<Record<string, any> | null | undefined>(undefined), [tab, setTab] = useSection<Tab>('Intelligence', 'dashboard', TABS.map(t => t.id));
  const [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  const canEdit = (user?.roles ?? []).includes('TRADING_OPS') || (user?.roles ?? []).includes('ADMIN');
  const [pick, setPick] = useState<Record<string, any> | null>(null), [items, setItems] = useState<Record<string, any>[]>([]);
  useEffect(() => { if (tab === 'instrument') void api('intel/screener?timeframe=H1').then(d => setItems(d.items)).catch(() => undefined); }, [tab]);
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
      <div className="cfg-pane">
        {tab === 'dashboard' && <IntelDashboard canEdit={(user.roles ?? []).includes('TRADING_OPS') || (user.roles ?? []).includes('ADMIN')}
          openInstrument={r => { setPick(r); setTab('instrument'); }} gotoScreener={() => setTab('screener')} />}
        {tab === 'screener' && <Screener canRefresh={(user.roles ?? []).includes('TRADING_OPS') || (user.roles ?? []).includes('ADMIN')} openInstrument={r => { setPick(r); setTab('instrument'); }} />}
        {tab === 'instrument' && <InstrumentView pick={pick} items={items} setPick={setPick} />}
        {tab === 'sources' && <SourcesView />}
        {tab === 'correlation' && <CorrelationView />}
        {tab === 'strength' && <StrengthView />}
        {tab === 'watchlists' && <WatchlistsView canEdit={canEdit} />}
        {tab === 'rules' && <RulesView canEdit={canEdit} />}
        {tab === 'calendar' && <CalendarView canEdit={canEdit} />}
        {tab === 'sessions' && <SessionsView />}
        {tab === 'compare' && <MarketBoard />}
      </div>
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
