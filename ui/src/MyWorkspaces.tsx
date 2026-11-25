import { useEffect, useId, useState } from 'react';
import { api } from './Configuration';
import { SignIn } from './Infrastructure';
import { goto, openAccountView, openDeployment, openOrder, openOrderRisk, openScanEvidence, openScopedTicket } from './nav';
import { Msg, useData, when, type Row } from './StageShell';
import { launchContext, usePublishRoute } from './workspaceLinks';
import WorkspaceControl, { type ScanConfig } from './WorkspaceControl';
import WorkspaceMapping from './WorkspaceMapping';
import './my-workspaces.css';

const labels = {
  market: 'Market data', intelligence: 'Market intelligence', signals: 'Signals', strategy: 'Strategy',
  risk: 'Risk', execution: 'Execution', portfolio: 'Portfolio', posttrade: 'Post-trade',
};
type PanelId = keyof typeof labels;
type Panel = { id: PanelId; visible: boolean; wide: boolean };
type Mode = 'PAPER' | 'DEMO' | 'LIVE';
const modeLabels: Record<Mode, string> = { PAPER: 'Paper', DEMO: 'Broker DEMO', LIVE: 'LIVE' };
const timeframes = ['S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1', 'W1', 'MN1'];
type Scope = { mode: Mode; timeframe: string; account: string | null; instrument: string | null;
  deployment: string | null; source: string | null; symbol: string };
type Workspace = Scope & { number: number; name: string; panels: Panel[]; scan?: ScanConfig | null; mapping_accounts?: string[] };
type Layouts = { revision: number; next_number: number; active: number; items: Workspace[] };
const panelIds = Object.keys(labels) as PanelId[];
function linkedWorkspace() {
  const route = launchContext(window.location.search);
  const number = route.view === 'My Workspaces' ? Number(route.section?.match(/^workspace-([1-9]\d*)$/)?.[1]) : NaN;
  return { number: Number.isSafeInteger(number) ? number : null,
    panels: panelIds.filter(id => route.panels?.split(',').includes(id)), decision: route.decision };
}

const templates: Record<string, PanelId[]> = {
  Guided: panelIds,
  'Instrument desk': ['market', 'intelligence', 'signals', 'portfolio', 'strategy', 'risk', 'execution', 'posttrade'],
  'Strategy desk': ['strategy', 'signals', 'risk', 'execution', 'portfolio', 'market', 'intelligence', 'posttrade'],
  'Account operations': ['execution', 'portfolio', 'posttrade', 'risk', 'strategy', 'signals', 'market', 'intelligence'],
};
const newWorkspace = (number: number): Workspace => ({
  number, name: '', mode: 'PAPER', timeframe: 'H1', account: null, instrument: null, deployment: null, source: null, symbol: '',
  panels: panelIds.map(id => ({ id, visible: true, wide: id === 'execution' || id === 'posttrade' })),
});
const initial = (): Layouts => ({ revision: 0, next_number: 2, active: 1, items: [newWorkspace(1)] });
const title = (w: Workspace) => `My Workspace ${w.number}${w.name ? ` · ${w.name}` : ''}`;
const plain = (value?: string) => value?.toLowerCase().replaceAll('_', ' ') ?? '—';
const dateLabel = (value?: string) => value ? when(value) : '—';
const stateLabel: Record<string, string> = {
  PENDING: 'Waiting for approval', NO_CHANGE: 'No position change needed', REFUSED: 'Decision refused',
  ORDERED: 'Order created', RECORDED: 'Signal recorded', DECLINED: 'Decision declined', SUPERSEDED: 'Replaced', EXPIRED: 'Decision expired',
};
const scopeQuery = (w: Scope) => {
  const q = new URLSearchParams({ mode: w.mode, timeframe: w.timeframe });
  for (const k of ['account', 'instrument', 'source', 'symbol', 'deployment'] as const) if (w[k]) q.set(k, w[k]!);
  return q.toString();
};

export default function MyWorkspaces({ username, signedOut, loadError, refreshSession }: {
  username?: string; signedOut: boolean; loadError: string; refreshSession: () => void;
}) {
  if (signedOut) return <SignIn title="MY WORKSPACES" done={refreshSession} />;
  if (!username) return <section className="my-workspaces"><Msg error={loadError} /><p role="status">Waiting for your operator session…</p></section>;
  return <SavedWorkspaces key={username} />;
}

function SavedWorkspaces() {
  const [data, setData] = useState<Layouts | null>(null);
  const [savedNumbers, setSavedNumbers] = useState<number[]>([]);
  const [state, setState] = useState({ dirty: false, saving: false, error: '', notice: '' });
  const [loadKey, setLoadKey] = useState(0);
  const [customize, setCustomize] = useState(false);
  const [linked, setLinked] = useState(linkedWorkspace);
  const [openPanels, setOpenPanels] = useState<Partial<Record<number, PanelId[]>>>(
    () => linked.number === null ? {} : { [linked.number]: linked.panels });
  const [selectedDecisions, setSelectedDecisions] = useState<Partial<Record<number, string>>>(
    () => linked.number && linked.decision ? { [linked.number]: linked.decision } : {});
  const active = data?.items.some(w => w.number === linked.number) ? linked.number! : data?.active;
  const expanded = active != null ? openPanels[active] ?? [] : [];
  usePublishRoute('My Workspaces', active != null ? `workspace-${active}` : undefined, expanded.join(','), !!data, active != null ? selectedDecisions[active] : undefined);
  useEffect(() => {
    const restore = () => {
      if (launchContext(window.location.search).view !== 'My Workspaces') return;
      const route = linkedWorkspace();
      setLinked(route);
      if (route.number != null) setSelectedDecisions(previous => ({ ...previous, [route.number!]: route.decision }));
      if (route.number != null) setOpenPanels(previous => ({ ...previous, [route.number!]: route.panels }));
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);
  useEffect(() => {
    let current = true;
    api('trading/workspaces').then(v => { if (current) { setData(v ?? initial()); setSavedNumbers(v?.items.map((item: Workspace) => item.number) ?? []); } })
      .catch(e => { if (current) setState(s => ({ ...s, error: e.message })); });
    return () => { current = false; };
  }, [loadKey]);
  useEffect(() => {
    if (!state.dirty) return;
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [state.dirty]);
  const reload = () => {
    if (state.dirty && !window.confirm('Discard unsaved workspace changes and reload saved layouts?')) return;
    setData(null); setState({ dirty: false, saving: false, error: '', notice: '' }); setLoadKey(n => n + 1);
  };
  if (!data) return <section className="my-workspaces">
    <h2>My Workspaces</h2><Msg error={state.error} />
    {state.error ? <button onClick={reload}>Retry loading workspaces</button> : <p role="status">Loading saved workspaces…</p>}
  </section>;
  const current = data.items.find(w => w.number === active)!;
  const togglePanel = (id: PanelId) => setOpenPanels(previous => {
    const open = previous[current.number] ?? [];
    return { ...previous, [current.number]: panelIds.filter(panel => panel === id ? !open.includes(panel) : open.includes(panel)) };
  });
  const change = (next: Layouts) => {
    setData(next);
    setState({ dirty: true, saving: false, error: '', notice: '' });
  };
  const patch = (values: Partial<Workspace>) => change({ ...data, items: data.items.map(w => w.number === current.number ? { ...w, ...values } : w) });
  const add = (duplicate: boolean) => {
    const number = data.next_number;
    const item = duplicate ? { ...current, number } : newWorkspace(number);
    setLinked({ number: null, panels: [], decision: undefined });
    change({ ...data, next_number: number + 1, active: number, items: [...data.items, item] });
  };
  const remove = () => {
    if (!window.confirm(`Remove ${title(current)} from your saved layouts? Any running research scan continues in Strategy Studio Runs & results.`)) return;
    const items = data.items.filter(w => w.number !== current.number);
    setLinked({ number: null, panels: [], decision: undefined });
    change({ ...data, items, active: items[0].number });
  };
  const save = async () => {
    setState(s => ({ ...s, saving: true, error: '', notice: '' }));
    try {
      const saved = await api('trading/workspaces', 'PUT', { ...data, active: current.number });
      setData(saved); setSavedNumbers(saved.items.map((item: Workspace) => item.number));
      setState({ dirty: false, saving: false, error: '', notice: 'Workspaces saved.' });
    } catch (e) {
      setState(s => ({ ...s, saving: false, error: (e as Error).message }));
    }
  };
  return <section className="my-workspaces">
    <header className="mw-header">
      <h2>My Workspaces</h2>
      <span className="mw-actions">
        <button disabled={state.saving} onClick={() => add(false)}>+ New workspace</button>
        <button disabled={state.saving} aria-expanded={customize} onClick={() => setCustomize(v => !v)}>Customize</button>
        <button className="primary" disabled={!state.dirty || state.saving} onClick={() => void save()}>{state.saving ? 'Saving…' : 'Save workspaces'}</button>
      </span>
    </header>
    <nav aria-label="Saved workspaces" className="mw-tabs">
      {data.items.map(w => <button key={w.number} aria-current={w.number === active ? 'page' : undefined}
        disabled={state.saving} onClick={() => { if (w.number !== active) { setLinked({ number: null, panels: [], decision: undefined }); change({ ...data, active: w.number }); } }}>{title(w)}</button>)}
    </nav>
    {linked.number !== null && linked.number !== active && <p role="status">The linked workspace is unavailable for this operator. Showing {title(current)}.</p>}
    <Msg error={state.error} notice={state.notice || (state.dirty ? 'Unsaved workspace changes' : '')} />
    {state.error && <button onClick={reload}>Reload saved workspaces</button>}
    {customize && <fieldset className="mw-custom" disabled={state.saving}>
      <legend>Customize {title(current)}</legend>
      <div className="mw-actions">
        <label>Workspace name <input maxLength={80} value={current.name} onChange={e => patch({ name: e.target.value })} /></label>
        <button onClick={() => add(true)}>Duplicate workspace</button>
        <button disabled={data.items.length <= 1} onClick={remove}>Remove workspace</button>
      </div>
      <label>Apply layout preset <select value="" onChange={e => {
        const preset = templates[e.target.value];
        if (preset) patch({ panels: preset.map(id => ({ id, visible: true, wide: id === 'execution' || id === 'posttrade' })) });
      }}>
        <option value="">Choose a preset</option>{Object.keys(templates).map(name => <option key={name}>{name}</option>)}
      </select></label>
      <PanelOptions panels={current.panels} onChange={panels => patch({ panels })} />
    </fieldset>}
    <WorkspaceControl key={`control-${current.number}`} number={current.number} name={current.name} config={current.scan}
      revision={data.revision} dirty={state.dirty} saving={state.saving} saved={savedNumbers.includes(current.number)}
      onChange={scan => patch({ scan })}
      mapping={<WorkspaceMapping config={current.scan} accounts={current.mapping_accounts ?? []}
        selectedDeployment={current.deployment} disabled={state.saving}
        onAccounts={mapping_accounts => patch({ mapping_accounts })}
        onInspect={d => {
          setSelectedDecisions(previous => ({ ...previous, [current.number]: undefined }));
          const mode: Mode = d.mode === 'PAPER' ? 'PAPER' : d.environment === 'LIVE' ? 'LIVE' : 'DEMO';
          patch({ mode, account: d.account_id, instrument: d.instrument_id, deployment: d.id,
            source: d.observed_source?.account_id ?? null, symbol: d.observed_source?.broker_symbol ?? '',
            timeframe: timeframes.includes(d.actual_timeframe) ? d.actual_timeframe : current.timeframe });
        }} />}>
      <WorkspaceBody key={current.number} workspace={current} update={patch} locked={state.saving} expanded={expanded} togglePanel={togglePanel}
        decisionId={selectedDecisions[current.number] ?? ''}
        setDecisionId={id => setSelectedDecisions(previous => ({ ...previous, [current.number]: id || undefined }))} />
    </WorkspaceControl>
  </section>;
}

function PanelOptions({ panels, onChange }: { panels: Panel[]; onChange: (panels: Panel[]) => void }) {
  const move = (index: number, difference: number) => {
    const reordered = [...panels];
    [reordered[index], reordered[index + difference]] = [reordered[index + difference], reordered[index]];
    onChange(reordered);
  };
  return <div className="mw-panel-options">{panels.map((p, i) => <div key={p.id}>
    <label><input type="checkbox" checked={p.visible} onChange={e => onChange(panels.map(x => x.id === p.id ? { ...x, visible: e.target.checked } : x))} /> {labels[p.id]}</label>
    <label><input type="checkbox" aria-label={`Full width ${labels[p.id]}`} checked={p.wide}
      onChange={e => onChange(panels.map(x => x.id === p.id ? { ...x, wide: e.target.checked } : x))} /> Full width</label>
    <button aria-label={`Move ${labels[p.id]} earlier`} disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
    <button aria-label={`Move ${labels[p.id]} later`} disabled={i === panels.length - 1} onClick={() => move(i, 1)}>↓</button>
  </div>)}</div>;
}

function WorkspaceBody({ workspace: w, update, locked, expanded, togglePanel, decisionId, setDecisionId }: {
  workspace: Workspace; update: (patch: Partial<Workspace>) => void; locked: boolean;
  expanded: PanelId[]; togglePanel: (panel: PanelId) => void; decisionId: string; setDecisionId: (id: string) => void;
}) {
  const [catalogue, catError, reloadCatalogue] = useData<Row>('trading/workspaces/catalogue', 60_000);
  const [data, error, refresh] = useData<Row>(`trading/workspaces/snapshot?${scopeQuery(w)}`, 15_000);
  const datalist = useId();
  const [search, setSearch] = useState('');
  const instruments: Row[] = catalogue?.instruments ?? [];
  const instrument = instruments.find(i => i.id === w.instrument);
  const accounts: Row[] = (catalogue?.accounts ?? []).filter((a: Row) => w.mode === 'PAPER' || a.environment === w.mode);
  const sources: Row[] = data?.sources ?? [];
  const sourceIndex = sources.findIndex(s => s.account_id === w.source && s.broker_symbol === w.symbol);
  const deployments: Row[] = data?.deployments ?? [];
  const deployment = deployments.find(d => d.id === w.deployment);
  const decisions: Row[] = data?.decisions ?? [];
  const evaluation = deployment?.last_evaluation;
  const latestId = deployment ? data?.evaluation_decision?.id : decisions[0]?.id;
  const focusedId = decisionId || latestId;
  const query = w.account && focusedId && !data?.scope_error
    ? `trading/workspaces/decision/${focusedId}?account=${w.account}&mode=${w.mode}` : null;
  const [lineage, lineageError, refreshLineage] = useData<Row>(query, 15_000);
  const decision = lineage?.decision ?? decisions.find(d => d.id === focusedId);
  const evidence = !catError && !error ? data : null;
  const orders: Row[] = lineageError ? [] : lineage?.items ?? [];
  const checks: Row[] = lineageError ? [] : lineage?.checks ?? [];
  const evaluatedBar = decision ? decision.evaluated_bar ?? (decision.signal_id === evaluation?.signal_id ? evaluation : null)
    : decisionId ? null : evaluation;
  const running = deployment?.status === 'ACTIVE';
  const outcome = decision ? stateLabel[decision.outcome] ?? plain(decision.outcome)
    : decisionId ? (lineageError ? 'Selected decision unavailable' : 'Loading selected decision…')
    : deployment?.last_error ?? (!deployment ? 'Choose a deployment to trace its evaluations'
      : !running ? `Deployment ${plain(deployment.status)}`
      : evaluation?.outcome === 'BASELINE' ? 'First bar evaluated · baseline established'
      : evaluation?.outcome === 'TARGET_UNCHANGED' ? 'Bar evaluated · target unchanged'
      : data?.signal?.state === 'REJECTED' ? 'Signal rejected before a decision'
      : evaluation ? 'Bar evaluated · no linked decision'
      : deployment.last_bar_time ? 'Waiting for a newly recorded evaluation' : 'Waiting for the first evaluation');
  const review = () => {
    if (decision?.deployment_id) openDeployment('queue', { id: decision.deployment_id, intentId: decision.id, label: decision.strategy_code ?? 'Selected decision' });
  };
  const changeScope = (values: Partial<Scope>) => { setDecisionId(''); update(values); };
  const chooseInstrument = (id: string | null) => changeScope({ instrument: id, deployment: null, source: null, symbol: '' });
  const chart = () => openScanEvidence('chart', {
    account_id: w.source!, broker_symbol: w.symbol, instrument_symbol: instrument?.symbol ?? w.symbol,
    timeframe: w.timeframe, strategy_code: deployment?.code ?? '',
  });
  const hardError = evidence?.scope_error;
  const modeText = modeLabels[w.mode];
  const selectPrompt = !w.account ? 'Choose an execution account' : hardError ?? 'No scoped records yet';
  const scopedSignal = decision ? { side: decision.signal_side, bar_time: decision.bar_time, strength: decision.strength, explanation: decision.explanation, symbol: decision.symbol } : decisionId ? null : evidence?.signal;
  const positions: Row[] = evidence?.positions ?? [];
  const availableOrders: Row[] = evidence?.orders ?? [];
  const breakCount = evidence?.reconciliation?.detail?.new?.length;
  const reconciliation = evidence?.reconciliation;
  const mismatch = deployment?.source_account && w.source &&
    (deployment.source_account !== w.source || deployment.source_symbol !== w.symbol || deployment.timeframe !== w.timeframe);
  const quoteAge = evidence?.quote?.received_at ? (Date.now() - Date.parse(evidence.quote.received_at)) / 1000 : null;
  const staleAfter = sources[sourceIndex]?.stale_seconds;
  const linkedFills: Row[] = orders.flatMap(o => o.fills?.slice(0, 50) ?? []);
  const evaluationSource = catalogue?.accounts?.find((a: Row) => a.id === evaluatedBar?.source_account)?.code;
  const cards: Record<PanelId, { status: string; body: React.ReactNode }> = {
    market: { status: !w.source ? 'Choose a data source' : evidence?.quote ? (quoteAge != null && staleAfter != null && quoteAge > staleAfter ? 'Observed quote is stale' : 'Observed quote available') : evidence?.bar ? 'Stored bar available' : 'Data unavailable',
      body: <>{evaluatedBar ? <><p>Strategy input · {evaluationSource ?? 'Configured source'} · {evaluatedBar.source_symbol} · {evaluatedBar.timeframe} · {dateLabel(evaluatedBar.bar_time)}</p>
        <p>Open {evaluatedBar.ohlc?.open ?? '—'} · High {evaluatedBar.ohlc?.high ?? '—'} · Low {evaluatedBar.ohlc?.low ?? '—'} · Close {evaluatedBar.ohlc?.close ?? '—'}</p>
        <button onClick={() => openScanEvidence('chart', { account_id: evaluatedBar.source_account, broker_symbol: evaluatedBar.source_symbol,
          instrument_symbol: decision?.symbol ?? deployment?.symbol ?? evaluatedBar.source_symbol, timeframe: evaluatedBar.timeframe,
          strategy_code: decision?.strategy_code ?? deployment?.code ?? '', bar_time: evaluatedBar.bar_time })}>View strategy source chart</button></>
        : decision && <p>Recorded source and OHLC unavailable for this decision.</p>}
        <p>Observed quote received {dateLabel(evidence?.quote?.received_at)} · Bid {evidence?.quote?.bid ?? '—'} · Ask {evidence?.quote?.ask ?? '—'}</p>
        <p>{w.timeframe} bar {dateLabel(evidence?.bar?.bar_time)} · Close {evidence?.bar?.close ?? '—'}</p>
        <button onClick={sourceIndex >= 0 ? chart : () => goto('Market data', 'plans')}>{sourceIndex >= 0 ? 'Open scoped chart' : 'Open collection plans'}</button></> },
    intelligence: { status: evidence?.intelligence ? plain(evidence.intelligence.regime) : 'No market analysis for this scope',
      body: <><p>Current market analysis · As of {dateLabel(evidence?.intelligence?.bar_time)} · Volatility {plain(evidence?.intelligence?.volatility)}</p>
        {evidence?.intelligence?.reasons?.filter((r: unknown) => typeof r === 'string').map((r: string, i: number) => <p key={i}>{r}</p>)}
        <button onClick={sourceIndex >= 0 ? chart : () => goto('Intelligence')}>Open market intelligence</button></> },
    signals: { status: scopedSignal?.side ? `${scopedSignal.symbol} · ${scopedSignal.side}` : 'No signal in this scope',
      body: <><p>{decision ? 'Signal for the selected decision' : 'Signal recorded for the latest evaluation'} · Bar {dateLabel(scopedSignal?.bar_time)} · Strength {scopedSignal?.strength ?? '—'}</p>
        <p>Signal state {plain(decision?.signal_state ?? scopedSignal?.state)}</p>
        {data?.signal?.id === scopedSignal?.id && data?.signal?.reasons?.map((r: string) => <p key={r}>{r}</p>)}
        {scopedSignal?.explanation?.filter((r: unknown) => typeof r === 'string').map((r: string, i: number) => <p key={i}>{r}</p>)}
        <button onClick={() => goto('Signals')}>Open Signals</button></> },
    strategy: { status: w.account ? outcome : selectPrompt,
      body: <><p>{decision?.strategy_code ?? deployment?.code ?? 'All deployments'}{(decision?.strategy_version ?? deployment?.version) != null && ` · version ${decision?.strategy_version ?? deployment?.version}`}</p>
        {decision ? <><p>Target {plain(decision.target)} · Decided {dateLabel(decision.created_at)}</p>
          <p>Handoff {plain(decision.handoff)} · Requested {decision.quantity ?? '—'}{decision.expires_at && ` · Expires ${dateLabel(decision.expires_at)}`}</p>
          {decision.sizing_note && <p>{decision.sizing_note}</p>}</> :
          evaluation && !decisionId && <p>Evaluated {dateLabel(evaluation.bar_time)} · Target {plain(evaluation.target)} · {evaluation.note ?? evaluation.reason}</p>}
        {deployment?.last_error && <p className="cfg-error">{deployment.last_error}</p>}
        <button onClick={() => deployment ? openDeployment('runtime', { id: deployment.id, label: deployment.code }) : goto('Strategies', 'runtime')}>Open strategy runtime</button></> },
    risk: { status: lineageError ? 'Linked risk evidence unavailable' : (checks.some(c => c.approved === false) || orders.some(o => o.risk?.approved === false)) ? 'Risk refusal recorded'
      : (checks.some(c => c.approved === true) || orders.some(o => o.risk?.approved === true)) ? 'Risk approval recorded' : decision?.outcome === 'PENDING' ? 'Risk checked when executed' : decision?.outcome === 'REFUSED' ? 'Review refusal reasons' : query && !lineage ? 'Checking linked risk' : 'No linked risk result',
      body: <><Msg error={lineageError} />{decision?.reasons?.map((r: string, i: number) => <p key={i}>{r}</p>)}
        {checks.map(c => <p key={c.id}>{c.approved ? 'Approved' : 'Refused'} · Requested {c.requested_qty ?? '—'} · Approved {c.approved_qty ?? '—'}{c.reasons?.length ? ` · ${c.reasons.join(', ')}` : ''}</p>)}
        {orders.map(o => <p key={o.id}>{o.client_order_id} · {o.risk ? (o.risk.approved ? 'Approved' : 'Refused') : 'Risk result unavailable'}
          {o.risk?.approved_qty != null && ` · allowed quantity ${o.risk.approved_qty}`}
          {o.risk?.reasons?.length > 0 && ` · ${o.risk.reasons.join(', ')}`}</p>)}
        <button onClick={() => orders.length === 1 ? openOrderRisk(orders[0].id, orders[0].client_order_id) : goto('Risk', 'decisions')}>Open Risk decisions</button></> },
    execution: { status: lineageError ? 'Linked orders unavailable' : orders.length ? `${orders.length}${lineage?.more ? '+' : ''} linked orders`
      : decision?.parent_id ? 'Algorithm parent recorded' : decision?.order_id ? 'Linked order recorded' : decision ? 'No order for this decision' : 'No order linked to this evaluation',
      body: <><Msg error={lineageError} />
        {query && !lineage && !lineageError && <p>Loading order evidence…</p>}
        {decision?.outcome === 'PENDING' && decision.deployment_id && <button onClick={review}>Review this decision</button>}
        {lineage?.parent && <p>{lineage.parent.algo} · {plain(lineage.parent.parent_state)} · Total filled {lineage.parent.parent_filled_qty}</p>}
        {orders.map(o => <div key={o.id} className="mw-order"><button onClick={() => openOrder(o.id)}>Open {o.client_order_id}</button>
          <span>{o.side} {o.quantity} · {plain(o.state)} · Filled {o.filled_qty ?? '0'}</span>
          {o.fills?.slice(0, 50).map((f: Row) => <p key={f.id}>{f.quantity} @ {f.price} · {dateLabel(f.at)}{f.broker_deal_id && ` · deal ${f.broker_deal_id}`}</p>)}{o.fills?.length > 50 && <p>Open the order to see all fills.</p>}</div>)}
        {lineage?.more && <p>Showing the first 50 children. Open Execution for the remaining orders.</p>}
        {availableOrders.length > 0 && <details><summary>Other account activity · recent orders</summary>
          {availableOrders.slice(0, 25).map(o => <div className="mw-order" key={o.id}><button onClick={() => openOrder(o.id)}>{o.symbol} · {o.side} {o.quantity}</button>
            <span>{plain(o.state)} · {dateLabel(o.created_at)}</span></div>)}</details>}
          {w.account && instrument && !hardError && evidence?.orders && <button onClick={() => openScopedTicket({
          account_id: w.account!, instrument_id: w.instrument!, mode: w.mode === 'PAPER' ? 'PAPER' : 'BROKER',
        })} title="Starts a separate manual order">Open order ticket</button>}
        <button onClick={() => goto('Execution', 'orders')}>Open order desk</button></> },
    portfolio: { status: positions.length ? `${Math.min(50, positions.length)}${positions.length > 50 ? '+' : ''} open positions` : evidence?.positions ? 'No open positions in this scope' : selectPrompt,
      body: <>{orders.filter(o => o.fills?.length).map(o => <p key={o.id}>{o.client_order_id} · {o.position
          ? `Linked position book · Current quantity ${o.position.quantity}` : 'Position book evidence unavailable'}</p>)}
        <p>Current book for this scope</p>{positions.slice(0, 50).map(p => <p key={p.id}>{p.symbol} · Quantity {p.quantity} · Entry {p.avg_price ?? '—'} · Realised {p.realised_pnl} {p.currency}</p>)}
        <button onClick={() => w.account ? openAccountView('positions', w.account) : goto('Portfolio', 'positions')}>Open Portfolio</button></> },
    posttrade: { status: reconciliation ? (reconciliation.status === 'MATCHED' ? 'No open breaks at run time' : plain(reconciliation.status)) : 'No reconciliation run for this account and comparison type',
      body: <><p>Account-level comparison · {reconciliation?.kind === 'BROKER' ? 'Broker book' : reconciliation ? 'Internal books' : 'No coverage recorded'} · {dateLabel(reconciliation?.at)}</p>
        {reconciliation?.detail?.broker_read && <p>Broker data {plain(reconciliation.detail.broker_read)} · New breaks in run {breakCount ?? 0}</p>}
        {linkedFills.length > 0 && <details><summary>Evidence for linked fills</summary>
          <p>Broker deal links and account run coverage are recorded independently.</p>
          {linkedFills.map(f => <p key={f.id}>{f.broker_deal_id ? `Deal ${f.broker_deal_id}` : dateLabel(f.at)} · {plain(f.evidence_state ?? 'EVIDENCE_UNAVAILABLE')}</p>)}
        </details>}
        <Msg error={reconciliation?.detail?.error} />
        <button onClick={() => w.account ? openAccountView('breaks', w.account) : goto('Post-trade', 'breaks')}>Review account breaks</button></> },
  };
  const scopeCaption = `${instrument?.symbol ?? 'All instruments'} · ${accounts.find(a => a.id === w.account)?.code ?? 'No execution account'} · ${modeText}`;
  return <>
    <fieldset className="mw-scope" disabled={locked}>
      <legend>Evidence focus · inspect one account, instrument or deployment</legend>
      <label>Evidence mode <select value={w.mode} onChange={e => changeScope({ mode: e.target.value as Mode, account: null, deployment: null })}>
        {Object.entries(modeLabels).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <label>Focused account <select value={w.account ?? ''} onChange={e => changeScope({ account: e.target.value || null, deployment: null })}>
        <option value="">Choose an account</option>{w.account && !accounts.some(a => a.id === w.account) && <option value={w.account}>Saved account unavailable</option>}
        {accounts.map(a => <option key={a.id} value={a.id}>{a.code} · {a.environment}</option>)}</select></label>
      <label>Focused instrument <input type="search" list={datalist} value={search || instrument?.symbol || ''} placeholder="All instruments · search by symbol"
        onChange={e => {
          const value = e.target.value;
          const found = instruments.find(i => i.symbol.toLowerCase() === value.toLowerCase());
          setSearch(found ? '' : value);
          if (found || !value) chooseInstrument(found?.id ?? null);
        }}
        onBlur={() => setSearch('')} />
        <datalist id={datalist}>{instruments.map(i => <option key={i.id} value={i.symbol}>{i.name}</option>)}</datalist></label>
      <label>Chart source <select value={sourceIndex < 0 ? '' : sourceIndex} disabled={!w.instrument} onChange={e => {
        const s = e.target.value ? sources[Number(e.target.value)] : undefined;
        changeScope({ source: s?.account_id ?? null, symbol: s?.broker_symbol ?? '' });
      }}><option value="">{w.source && sourceIndex < 0 ? 'Saved source unavailable' : 'Choose a source'}</option>
        {sources.map((s, i) => <option key={`${s.account_id}:${s.broker_symbol}`} value={i}>{s.code} · {s.broker_symbol}</option>)}</select></label>
      <label>Timeframe <select value={w.timeframe} onChange={e => changeScope({ timeframe: e.target.value })}>{timeframes.map(t => <option key={t}>{t}</option>)}</select></label>
      <label>Strategy deployment <select value={w.deployment ?? ''} disabled={!w.account} onChange={e => {
        const d = deployments.find(opt => opt.id === e.target.value);
        changeScope({ deployment: d?.id ?? null, ...(d?.timeframe ? { timeframe: d.timeframe } : {}) });
      }}><option value="">All deployments</option>{w.deployment && !deployment && <option value={w.deployment}>Saved deployment unavailable</option>}
      {deployments.map(d => <option key={d.id} value={d.id}>{d.code} v{d.version} · {d.symbol} · {plain(d.status)}</option>)}</select></label>
    </fieldset>
    <Msg error={catError || error || hardError} />
    {w.source && !w.deployment && <p className="mw-context-note">Market panels use the selected source. Strategy decisions use each deployment’s configured source.</p>}
    {mismatch && <p className="mw-context-note">The selected chart source or timeframe differs from the deployment runtime. Decision evidence follows the deployment.</p>}
    <div className="mw-summary">
      <div><strong className={w.mode === 'LIVE' ? 'cfg-error' : undefined}>{scopeCaption}</strong><p>{error ? 'Scope evidence is unavailable.' : !data ? 'Loading scope…' :
        !w.account ? 'Choose an account to see its trading activity.' : hardError ?? outcome}</p></div>
      <div className="mw-actions">
        <label>Decision <select value={decision?.id ?? ''} onChange={e => setDecisionId(e.target.value)}><option value="" disabled>{decisionId && !decision ? 'Selected decision left the recent window' : 'No linked decisions'}</option>
          {decision && !decisions.some(d => d.id === decision.id) && <option value={decision.id}>{decision.symbol} · {decision.strategy_code} · {outcome}</option>}
          {decisions.map(d => <option key={d.id} value={d.id}>{dateLabel(d.created_at)} · {d.symbol} · {d.strategy_code ?? 'Strategy'} · {stateLabel[d.outcome] ?? plain(d.outcome)}</option>)}</select></label>
        <button onClick={() => { reloadCatalogue(); refresh(); refreshLineage(); }}>Refresh</button>
      </div>
    </div>
    {decisionId && <button onClick={() => setDecisionId('')}>Follow latest evaluation</button>}
    {decision?.outcome === 'PENDING' && decision.deployment_id && <button className="primary" onClick={review}>Review selected decision</button>}
    <nav className="mw-actions" aria-label="Trade lifecycle">
      {panelIds.filter(id => w.panels.some(p => p.id === id && p.visible)).map(id =>
        <button key={id} aria-expanded={expanded.includes(id)} onClick={() => togglePanel(id)}>{labels[id]} · {data && !error ? cards[id].status : 'Awaiting evidence'}</button>)}
    </nav>
    <div className="mw-panels">
      {w.panels.filter(p => p.visible).map(p => <section className={`mw-panel${p.wide ? ' mw-wide' : ''}`} key={p.id}>
        <button className="mw-panel-heading" aria-expanded={expanded.includes(p.id)} onClick={() => togglePanel(p.id)}>
          <strong>{labels[p.id]}</strong><span>{error || catError ? 'Evidence unavailable' : !data ? 'Loading…' : cards[p.id].status}</span>
        </button>
        {expanded.includes(p.id) && <div className="mw-panel-body">{cards[p.id].body}</div>}
      </section>)}
      {!w.panels.some(p => p.visible) && <p>Open Customize to show panels.</p>}
    </div>
  </>;
}
