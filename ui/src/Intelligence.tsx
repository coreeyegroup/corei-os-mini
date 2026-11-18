/* Stage 2 · Market intelligence, the screen.
 * Why: one place to look at, analyse, watch and configure what the collected market data says.
 * Purpose: the grouped navigation (Overview · Analyse · Monitor · Configure, also in the side bar), the sign-in, and
 * which section shows. Sections live in IntelBoard (dashboard, screener, instrument, sessions, engine settings),
 * IntelWorkspace (sources, correlation, strength, watchlists, rules, calendar, indicators), MarketBoard (select &
 * compare) and Pipeline (stage map).
 * Must never: change market data or orders. */
import { Fragment, useEffect, useState } from 'react';
import { addReauthHandler, api } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { SignIn } from './Infrastructure';
import { EngineSettings, IntelDashboard, InstrumentView, Screener, SessionsView } from './IntelBoard';
import { CalendarView, CorrelationView, IndicatorsView, RulesView, SourcesView, StrengthView, WatchlistsView } from './IntelWorkspace';
import MarketBoard from './MarketBoard';
import Stage from './Pipeline';
import type { View } from './layout';
import { INTEL_GROUPS, useSection } from './nav';
import { StageBar, Workspace } from './StageShell';
import { SubWorkspace } from './SubWorkspace';
import { Formulas } from './SignalLibrary';
import GraphWorkbench from './calc/GraphWorkbench';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

type Tab = 'workspace' | 'dashboard' | 'screener' | 'instrument' | 'sources' | 'correlation' | 'strength' | 'watchlists' | 'rules' | 'calendar' | 'sessions' | 'compare'
  | 'indicators' | 'settings' | 'formulas' | 'graphs' | 'stage';
const HINTS: Record<Tab, string> = {
  workspace: 'Every panel of market intelligence on one page — dashboard, screener, analysis, monitors and settings — each opens and closes.',
  dashboard: 'Coverage, regime mix per timeframe, what needs history (one click, or all at once), top attention, biggest moves, events and fired rules.',
  screener: 'Every collected instrument ranked for attention, from the shared feature store (closed bars). Choose the columns: trend, momentum, volatility, volume, cost, feed quality and your own indicators.',
  instrument: 'One instrument across every timeframe: regime and the reasons, every feature (standard and custom) on the chosen timeframe, chart with price, EMAs, Bollinger, volume and RSI; CSV of the history.',
  sources: 'One instrument from every broker account side by side: price, deviation from the other sources, spread, feed quality, latency; the cheapest, fastest and cleanest source.',
  correlation: 'How the collected instruments move together (correlation of returns), per timeframe and window.',
  strength: 'Currency strength: each currency\'s average move against the others, from every FX pair collected.',
  sessions: 'Which trading centres are open now, overlaps, rollover and the weekend close, and the next changes.',
  compare: 'Choose sources and instruments, compare them side by side, chart and overlay sources. Data comes from Stage 1 · Market data.',
  watchlists: 'Named groups of instruments across brokers and accounts; filter the screener and scope rules with them.',
  rules: 'Conditions on any feature, against a number or another feature (EMA 9 crosses above EMA 21, RSI below 30, regime becomes trend…), checked on every closed bar; fired rules and alerts.',
  calendar: 'Economic events and holidays (add or import CSV); high-impact events flag event risk on the instruments in those currencies.',
  indicators: 'The standard set every screen shares, and your own indicators with the lengths each strategy needs (EMA 9/21, Bollinger 20/2.5, MACD 8/21/5…), per timeframe.',
  settings: 'The feature engine: on or paused, how often it runs, which timeframes, which instruments, how long history is kept, the event-risk window.',
  formulas: 'Every feature and regime formula (EMA, RSI, ATR, Bollinger, ADX, efficiency ratio, volatility, MACD, stochastic, Donchian, z-score, VWAP, OBV, regime, attention) with its value on the instrument you choose.',
  graphs: 'Features as calculation graphs (n8n-style): every step with its value on the chosen bar; draft your own, check, propose and approve.',
  stage: 'What this stage does, its components and how far each is built.',
};
const LABELS = { workspace: 'All panels', ...Object.fromEntries(INTEL_GROUPS.flatMap(([, items]) => items)) } as Record<Tab, string>;
const TABS = Object.keys(HINTS) as Tab[];

export default function Intelligence({ open }: { open: (v: View) => void }) {
  const [user, setUser] = useState<Record<string, any> | null | undefined>(undefined), [tab, setTab] = useSection<Tab>('Intelligence', 'workspace', TABS);
  const [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  const canEdit = (user?.roles ?? []).includes('TRADING_OPS') || (user?.roles ?? []).includes('ADMIN');
  const [pick, setPick] = useState<Record<string, any> | null>(null), [items, setItems] = useState<Record<string, any>[]>([]);
  useEffect(() => { if (tab === 'instrument' || tab === 'workspace') void api('intel/screener?timeframe=H1').then(d => setItems(d.items)).catch(() => undefined); }, [tab]);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check(); window.addEventListener('corei-session', check); return () => window.removeEventListener('corei-session', check);
  }, []);
  const nav = <Nav tab={tab} setTab={setTab} />;
  if (tab === 'stage' && !user) return <div className="cfg md">{nav}<Stage view="Intelligence" open={open} /></div>;
  if (user === undefined) return <div className="content"><p>Checking sign-in…</p></div>;
  if (user === null) return <SignIn title="MARKET INTELLIGENCE" done={s => setUser(s)} />;
  const openInstrument = (r: Record<string, any>) => { setPick(r); setTab('instrument'); };
  const panel = (t: Tab) => t === 'dashboard' ? <IntelDashboard canEdit={canEdit} openInstrument={openInstrument} gotoScreener={() => setTab('screener')} gotoSettings={() => setTab('settings')} />
    : t === 'screener' ? <Screener canRefresh={canEdit} openInstrument={openInstrument} /> : t === 'instrument' ? <InstrumentView pick={pick} items={items} setPick={setPick} />
    : t === 'sources' ? <SourcesView /> : t === 'correlation' ? <CorrelationView /> : t === 'strength' ? <StrengthView /> : t === 'sessions' ? <SessionsView />
    : t === 'compare' ? <MarketBoard /> : t === 'watchlists' ? <WatchlistsView canEdit={canEdit} /> : t === 'rules' ? <RulesView canEdit={canEdit} />
    : t === 'calendar' ? <CalendarView canEdit={canEdit} /> : t === 'indicators' ? <IndicatorsView canEdit={canEdit} /> : t === 'settings' ? <EngineSettings canEdit={canEdit} /> : t === 'formulas' ? <Formulas stage="Intelligence" features />
    : t === 'graphs' ? <GraphWorkbench ctx={{ user: user ?? {}, canOps: canEdit, canRisk: (user?.roles ?? []).includes('RISK_APPROVER'), open: () => undefined }} purposes={['FEATURE']} /> : null;
  return <div className="cfg md">
    {nav}
    <section className="cfg-main">
      <StageBar view="Intelligence" title={LABELS[tab]} hint={HINTS[tab]} />
      <SubWorkspace view="Intelligence" label="Market intelligence" tab={tab} setTab={id => setTab(id as Tab)}
        items={INTEL_GROUPS.flatMap(([g, its]) => its.map(([id]) => ({ id, label: LABELS[id as Tab], group: g, hint: HINTS[id as Tab] })))}
        render={id => <div className="cfg-pane">{id === 'workspace' ? <Workspace view="Intelligence" groups={INTEL_GROUPS.map(([g]) => g)} alone={x => setTab(x as Tab)} sections={INTEL_GROUPS.flatMap(([g, its]) =>
          its.filter(([x]) => x !== 'stage' && x !== 'workspace').map(([x, label]) => ({ id: x, label, group: g, hint: HINTS[x as Tab], render: () => panel(x as Tab) })))} />
          : id === 'stage' ? <Stage view="Intelligence" open={open} /> : panel(id as Tab)}</div>} />
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

/* One compact line, grouped like the side bar (which replaces it when the side bar is shown). */
function Nav({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  return <nav className="cfg-nav" aria-label="Market intelligence sections">
    {INTEL_GROUPS.map(([g, items]) => <Fragment key={g}><span className="cfg-nav-group">{g}</span>
      {items.map(([id, label]) => <button key={id} aria-current={tab === id ? 'page' : undefined} onClick={() => setTab(id as Tab)}><span>{label}</span></button>)}</Fragment>)}
  </nav>;
}
