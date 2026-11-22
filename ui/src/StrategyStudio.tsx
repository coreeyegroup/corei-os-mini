import { useEffect, useId, useRef, useState } from 'react';
import { api } from './Configuration';
import { goto, openScanEvidence, publishSections, useSection } from './nav';
import UniversalCanvas, { readWorkflow, type NodeSpec } from './UniversalCanvas';
import StudioBrokerCatalogue from './StudioBrokerCatalogue';
import StudioDrafts, { type DraftVersion } from './StudioDrafts';
import './strategy-studio.css';

type Row = Record<string, unknown>;
type Mode = 'observe' | 'scan' | 'build' | 'results';
const researchSteps = [
  { id: 'scan', title: 'Scan by instrument' },
  { id: 'scan-strategy', title: 'Scan by strategy' },
  { id: 'results', title: 'Runs & results' },
  { id: 'observe', title: 'Inspect instrument' },
] as const;
type ResearchStep = typeof researchSteps[number]['id'];
type ScanScope = 'instrument' | 'strategy';
type ScanRequest = { instrument_symbols?: string[]; strategy_codes?: string[];
  entry_lookback: number; stop_atr: string; reward_risk: string };
type ScanRun = { scope: ScanScope; selection: string; groups: string[]; request: ScanRequest; items: Row[]; completedAt: Date };
type ScanPreset = { name: string; mode: ScanScope; request: ScanRequest; allInstruments?: boolean; allStrategies?: boolean };
const presetKey = 'corei.studio.scan-presets';
const scanRunsPath = 'trading/studio/scan-runs';
type ScanJob = { id: string; mode: ScanScope; status: string; total: number; created_at: string; updated_at: string;
  name?: string; request: ScanRequest; failure?: string; retrying?: boolean; processed_instruments?: number; instruments: string[]; strategies: string[]; summary: Row[] };
type ScanJobListItem = Pick<ScanJob, 'id' | 'mode' | 'status' | 'created_at' | 'name'> & { instruments: number; strategies: number };
const running = (status: string) => status === 'RUNNING' || status === 'QUEUED';

type ScanFailure = { scope: ScanScope; selection: string; message: string };
const buildSteps = [
  { id: 'templates', title: 'Start', description: 'Choose a template, edit the starter workflow, or reopen a saved revision.' },
  { id: 'canvas', title: 'Build rules', description: 'Add and connect nodes. Select a node to edit its parameters.' },
  { id: 'test', title: 'Validate & preview', description: 'Check the graph and preview its signals on closed bars.' },
  { id: 'drafts', title: 'Save revision', description: 'Save the workflow you want to keep or send for review.' },
  { id: 'review', title: 'Review & use', description: 'Send a saved revision for approval, then use the approved rule in a strategy.' },
] as const;
type BuildStep = typeof buildSteps[number]['id'];
type EditorView = BuildStep | 'json';
type StudioSection = ResearchStep | EditorView;
type TestOutput = { title: string; draft: string; error?: string; data?: Row };
const studioSections: StudioSection[] = [...buildSteps.map(step => step.id), 'json', ...researchSteps.map(step => step.id)];
const explorerSections = [
  ...buildSteps.map(({ id, title }, index) => ({ id, label: `${index + 1}. ${title}`, group: 'Build strategy' })),
  { id: 'json', label: 'Workflow JSON', group: 'Build tools' },
  ...researchSteps.map(({ id, title }) => ({ id, label: title, group: 'Signal scanner' })),
];
const timeframes = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];

const starterDraft = JSON.stringify({
  schema_version: '1.0', name: 'EMA directional draft',
  nodes: [
    { id: 'bars', type: 'input.bars' },
    { id: 'fast', type: 'indicator.ema', params: { period: 20 } },
    { id: 'slow', type: 'indicator.ema', params: { period: 50 } },
    { id: 'bull', type: 'condition.compare', params: { operator: '>' } },
    { id: 'bear', type: 'condition.compare', params: { operator: '<' } },
    { id: 'signal', type: 'signal.direction' }
  ],
  edges: [
    { source: 'bars', source_port: 'close', target: 'fast', target_port: 'source' },
    { source: 'bars', source_port: 'close', target: 'slow', target_port: 'source' },
    { source: 'fast', source_port: 'value', target: 'bull', target_port: 'left' },
    { source: 'slow', source_port: 'value', target: 'bull', target_port: 'right' },
    { source: 'fast', source_port: 'value', target: 'bear', target_port: 'left' },
    { source: 'slow', source_port: 'value', target: 'bear', target_port: 'right' },
    { source: 'bull', source_port: 'result', target: 'signal', target_port: 'long' },
    { source: 'bear', source_port: 'result', target: 'signal', target_port: 'short' }
  ]
}, null, 2);

function rows(value: { items?: Row[] }): Row[] {
  return Array.isArray(value?.items) ? value.items : [];
}

function instrumentLabel(row: Row): string {
  return String(row.symbol ?? row.instrument_symbol ?? row.code ?? '');
}

function sourceId(row: Row): string {
  return String(row.account_id) + '|' + String(row.broker_symbol);
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function StudioTable({ title, columns, items, empty, onGroup, onInspect }: {
  title: string; columns: [string, string][]; items: Row[]; empty: string; onGroup?: (group: string) => void; onInspect?: (row: Row) => void;
}) {
  return <div className="cfg-scroll studio-table" tabIndex={0} role="region" aria-label={title}>
    <table className="cfg-table compact">
      <thead><tr>{columns.map(([label]) => <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>
        {items.map((row, index) => <tr key={String(row.id ?? row.deployment_id ?? index)}>
          {columns.map(([label, key]) => <td key={label}>
            {onInspect && key === 'instrument_symbol'
              ? <button type="button" className="studio-result-link" onClick={() => onInspect(row)}>{String(row[key])}</button>
              : onGroup && (key === 'scope' || key === 'group') && typeof row[key] === 'string'
              ? <button type="button" className="studio-result-link" onClick={() => onGroup(String(row[key]))}>{String(row[key])}</button>
              : Array.isArray(row[key]) ? row[key].join(' · ') : String(row[key] ?? '—')}
          </td>)}
        </tr>)}
        {!items.length && <tr><td colSpan={columns.length}>{empty}</td></tr>}
      </tbody>
    </table>
  </div>;
}

function SignalDetail({ row, close }: { row: Row; close: () => void }) {
  const instrument = String(row.instrument_symbol);
  const strategy = String(row.strategy_code);
  const source = [row.account_code, row.broker_symbol].filter(Boolean).join(' · ');
  const evidence = {
    instrument_symbol: instrument, strategy_code: strategy,
    account_id: String(row.account_id ?? ''), broker_symbol: String(row.broker_symbol ?? ''),
    timeframe: String(row.timeframe ?? ''),
    strategy_version_id: typeof row.strategy_version_id === 'string' ? row.strategy_version_id : undefined,
  };
  const sourceResolved = evidence.account_id && evidence.broker_symbol && evidence.timeframe;
  const fields = [
    ['Strategy', strategy], ['Version', row.version], ['Timeframe', row.timeframe], ['Source', source],
    ['Outcome', row.status], ['Side', row.side], ['Strength within strategy', row.strength],
    ['Entry', row.entry], ['Stop loss', row.stop_loss], ['Target', row.take_profit],
    ['Trigger bar', row.trigger_bar_time], ['Bars since trigger', row.bars_since_trigger],
    ['Risk per contract', row.risk_per_contract && [row.risk_per_contract, row.quote_currency].join(' ')],
    ['Reward / risk', row.reward_risk],
  ];
  return <section className="studio-panel studio-evidence" aria-label="Scan result detail" tabIndex={-1}
    ref={element => { if (element && !element.contains(document.activeElement)) element.focus(); }}>
    <div className="studio-actions"><h3>{instrument} · Result detail</h3><button onClick={close}>Close detail</button></div>
    <p>{String(row.reason ?? '')}</p>
    <dl className="studio-scan-summary">{fields.map(([label, value]) => <div key={String(label)}>
      <dt>{String(label)}</dt><dd>{String(value ?? '—') || '—'}</dd></div>)}</dl>
    <div className="studio-actions">
      <button disabled={!sourceResolved} onClick={() => openScanEvidence('chart', evidence)}>Open chart</button>
      <button disabled={!sourceResolved || !evidence.strategy_version_id} onClick={() => openScanEvidence('backtest', evidence)}>Open backtest</button>
    </div>
    {(!sourceResolved || !evidence.strategy_version_id) && <p className="cfg-note">A resolved data source and strategy version are required for the backtest handoff.</p>}
  </section>;
}

function runCounts(run: ScanJob | null) {
  return (run?.summary ?? []).reduce<{ checked: number; signals: number; errors: number }>((counts, row) => ({
    checked: counts.checked + Number(row.checked),
    signals: counts.signals + Number(row.signals),
    errors: counts.errors + Number(row.errors),
  }), { checked: 0, signals: 0, errors: 0 });
}

function ScanSelection({ label, items, selected, change, disabled, loading }: {
  label: string; items: { value: string; label: string; search?: string }[]; selected: string[];
  change: (values: string[]) => void; disabled: boolean; loading: boolean;
}) {
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);
  const pageSize = 100;
  const chosen = new Set(selected);
  const matching = items.filter(item => (item.value + ' ' + item.label + ' ' + (item.search ?? '')).toLowerCase().includes(query.trim().toLowerCase()));
  const currentPage = Math.min(page, Math.max(0, Math.ceil(matching.length / pageSize) - 1));
  return <fieldset className="studio-strategy-filter studio-scan-picker" disabled={disabled}>
    <legend>{label} · {selected.length} selected</legend>
    <div className="studio-actions">
      <input type="search" aria-label={'Search ' + label.toLowerCase()} placeholder={'Search ' + label.toLowerCase()} value={query}
        onChange={event => { setQuery(event.target.value); setPage(0); }} />
      <button type="button" disabled={!matching.length} onClick={() => change([...new Set([...selected, ...matching.map(item => item.value)])])}>Select all matching</button>
      <button type="button" disabled={!selected.length} onClick={() => change([])}>Clear selection</button>
    </div>
    <div className="studio-picker-options">
      {matching.slice(currentPage * pageSize, (currentPage + 1) * pageSize).map(item => <label key={item.value}>
        <input type="checkbox" checked={chosen.has(item.value)} onChange={event =>
          change(event.target.checked ? [...selected, item.value] : selected.filter(value => value !== item.value))} />
        {item.label}
      </label>)}
    </div>
    {matching.length > pageSize && <div className="studio-actions">
      <button type="button" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Previous {label.toLowerCase()}</button>
      <span>{currentPage * pageSize + 1}–{Math.min((currentPage + 1) * pageSize, matching.length)} of {matching.length.toLocaleString()}</span>
      <button type="button" disabled={(currentPage + 1) * pageSize >= matching.length} onClick={() => setPage(currentPage + 1)}>Next {label.toLowerCase()}</button>
    </div>}
    {!matching.length && <p className="cfg-note">{loading ? 'Loading…' : 'No matching ' + label.toLowerCase() + '.'}</p>}
  </fieldset>;
}

function BackgroundScans({ runId, selectRun }: { runId: string; selectRun: (id: string) => void }) {
  const [job, setJob] = useState<ScanJob | null>(null);
  const [history, setHistory] = useState<ScanJobListItem[]>([]);
  const [historyOffset, setHistoryOffset] = useState(0);
  const [older, setOlder] = useState(false);
  const [results, setResults] = useState<Row[]>([]);
  const [more, setMore] = useState(false);
  const [group, setGroup] = useState('');
  const [status, setStatus] = useState('SIGNAL');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [runError, setRunError] = useState('');
  const [resultError, setResultError] = useState('');
  const [actionError, setActionError] = useState('');
  const [loadingResults, setLoadingResults] = useState(false);
  const [detail, setDetail] = useState<Row | null>(null);
  const [compareId, setCompareId] = useState('');
  const [comparison, setComparison] = useState<ScanJob | null>(null);
  const [compareError, setCompareError] = useState('');
  const current = job?.id === runId ? job : null;

  useEffect(() => {
    let live = true;
    api(scanRunsPath + '?offset=' + historyOffset).then(data => {
      if (live) { setHistory(Array.isArray(data.items) ? data.items : []); setOlder(Boolean(data.has_more)); setHistoryError(''); }
    }).catch(error => { if (live) setHistoryError(message(error)); });
    return () => { live = false; };
  }, [runId, revision, historyOffset, current?.status]);

  useEffect(() => {
    if (!runId) return;
    let live = true;
    let timer: number | undefined;
    async function refresh() {
      try {
        const data: ScanJob = await api(scanRunsPath + '/' + runId);
        if (!live) return;
        setJob(data);
        setRunError('');
        if (running(data.status)) timer = window.setTimeout(refresh, 1500);
      } catch (error) {
        if (live) { setRunError(message(error)); timer = window.setTimeout(refresh, 5000); }
      }
    }
    void refresh();
    return () => { live = false; window.clearTimeout(timer); };
  }, [runId, revision]);

  useEffect(() => {
    setDetail(null);
  }, [runId, group, status, offset]);

  useEffect(() => {
    if (!compareId || compareId === runId) return;
    let live = true;
    setCompareError('');
    api(scanRunsPath + '/' + compareId).then(data => {
      if (live) setComparison(data);
    }).catch(error => { if (live) setCompareError(message(error)); });
    return () => { live = false; };
  }, [compareId, runId]);

  // Fetch the selected page on demand and once more when the run reaches a final status.
  useEffect(() => {
    if (!runId) return;
    let live = true;
    const params = new URLSearchParams({group, offset: String(offset)});
    if (status) params.set('status', status);
    setLoadingResults(true);
    api(scanRunsPath + '/' + runId + '/results?' + params).then(data => {
      if (live) { setResults(rows(data)); setMore(Boolean(data.has_more)); setResultError(''); }
    }).catch(error => { if (live) { setResults([]); setMore(false); setResultError(message(error)); } })
      .finally(() => { if (live) setLoadingResults(false); });
    return () => { live = false; };
  }, [runId, group, status, offset, current?.status, revision]);

  function open(id: string) {
    setGroup(''); setOffset(0); setResults([]); setMore(false);
    setActionError(''); setRunError(''); setResultError(''); selectRun(id);
  }

  async function action(path: 'cancel' | 'retry') {
    setBusy(true); setActionError('');
    try {
      await api(scanRunsPath + '/' + runId + '/' + path, 'POST');
      setRevision(value => value + 1);
    } catch (error) { setActionError(message(error)); }
    finally { setBusy(false); }
  }

  async function rerun() {
    if (!current?.request) return;
    setBusy(true); setActionError('');
    try {
      const result = await api(scanRunsPath, 'POST', { mode: current.mode, name: current.name,
        request: { ...current.request, instrument_symbol: undefined, strategy_code: undefined,
          instrument_symbols: current.instruments, strategy_codes: current.strategies } });
      setCompareId(current.id);
      open(result.id);
    } catch (error) { setActionError(message(error)); }
    finally { setBusy(false); }
  }

  const { checked, errors, signals: signalCount } = runCounts(current);
  const compared = compareId && compareId !== runId && comparison?.id === compareId ? comparison : null;
  return <section className="studio-panel">
    <h3>Saved runs</h3>
    <div className="studio-actions">
      <label>Run history<select value={history.some(row => row.id === runId) ? runId : ''} onChange={event => open(event.target.value)}>
        <option value="">{history.length ? 'Choose a saved run' : 'No runs on this page'}</option>
        {history.map(row => <option key={row.id} value={row.id}>{row.name ? row.name + ' · ' : ''}{new Date(row.created_at).toLocaleString()} · {row.instruments} × {row.strategies} · {row.status}</option>)}
      </select></label>
      <button disabled={!historyOffset} onClick={() => setHistoryOffset(historyOffset - 20)}>Newer runs</button>
      <button disabled={!older} onClick={() => setHistoryOffset(historyOffset + 20)}>Older runs</button>
    </div>
    {historyError && <p role="alert" className="cfg-error-text">{historyError}</p>}
    {runError && <p role="alert" className="cfg-error-text">{runError}</p>}
    {runId && !current && !runError && <p role="status">Loading run…</p>}
    {current && <>
      <div className="studio-actions">
        <p role="status">{current.name && current.name + ' · '}{new Date(current.created_at).toLocaleString()} · {current.status} · {checked.toLocaleString()} / {current.total.toLocaleString()} checked · {signalCount} signals · {errors} errors
          {current.retrying && running(current.status) && ` · Retrying errors · ${current.processed_instruments ?? 0}/${current.instruments.length} instruments visited`}</p>
        {running(current.status) && <button disabled={busy} onClick={() => void action('cancel')}>Cancel run</button>}
        {(!running(current.status) && (errors > 0 || current.status !== 'COMPLETED')) &&
          <button disabled={busy} onClick={() => void action('retry')}>{current.status === 'COMPLETED' ? 'Retry errors' : 'Resume run'}</button>}
        <button disabled={busy || running(current.status) || !current.request} onClick={() => void rerun()}>Rerun with current data</button>
        <button onClick={() => setRevision(value => value + 1)}>Refresh results</button>
      </div>
      <progress className="studio-run-progress" aria-label="Scan progress" value={checked} max={current.total || 1} />
      {current.failure && <p role="alert" className="cfg-error-text">{current.failure}</p>}
      {actionError && <p role="alert" className="cfg-error-text">{actionError}</p>}
      <p className="cfg-note">Strength is strategy-specific. Results are grouped by strategy and ordered by strength within each strategy.</p>
      <StudioTable title="Run summary" items={current.summary} onGroup={value => { setGroup(value); setOffset(0); }}
        columns={[[current.mode === 'instrument' ? 'Instrument' : 'Strategy', 'scope'], ['Checked', 'checked'], ['Signals', 'signals'],
          ['Errors', 'errors'], ...(current.mode === 'instrument'
            ? [['Strategies with signals', 'signal_strategies'] as [string, string]]
            : [['Strongest matches', 'strongest'], ['Strength', 'strength']] as [string, string][])]} empty="Waiting for the first checks." />
      <div className="studio-actions">
        <label>{current.mode === 'instrument' ? 'Instrument' : 'Strategy'}<select value={group} onChange={event => { setGroup(event.target.value); setOffset(0); }}>
          <option value="">All results</option>
          {(current.mode === 'instrument' ? current.instruments : current.strategies).map(item => <option key={item} value={item}>{item}</option>)}
        </select></label>
        <label>Outcome<select value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}>
          <option value="">All outcomes</option><option value="SIGNAL">Signals</option><option value="WAIT">Waiting</option><option value="ERROR">Errors</option>
        </select></label>
        <button disabled={!offset || loadingResults} onClick={() => setOffset(offset - 50)}>Previous page</button>
        <button disabled={!more || loadingResults} onClick={() => setOffset(offset + 50)}>Next page</button>
      </div>
      {resultError && <p role="alert" className="cfg-error-text">{resultError}</p>}
      <StudioTable title="Run results" onInspect={setDetail} items={loadingResults ? [] : results}
        columns={[[ 'Instrument', 'instrument_symbol' ], ['Strategy', 'strategy_code'], ['Version', 'version'], ['Timeframe', 'timeframe'],
          ['Status', 'status'], ['Side', 'side'], ['Strength', 'strength'], ['Entry', 'entry'], ['Stop', 'stop_loss'],
          ['Target', 'take_profit'], ['Trigger', 'trigger_bar_time'], ['Risk / contract', 'risk_per_contract'], ['Currency', 'quote_currency'],
          ['Reward / risk', 'reward_risk'], ['Source', 'account_code'], ['Reason', 'reason']]}
        empty={loadingResults || !results.length && running(current.status) ? 'Waiting for results…' : 'No records on this page.'} />
      {detail && <SignalDetail row={detail} close={() => setDetail(null)} />}
      <details className="studio-scan-settings">
        <summary>Compare saved runs</summary>
        <label>Compare with<select value={compareId === runId ? '' : compareId} onChange={event => setCompareId(event.target.value)}>
          <option value="">Select another run</option>
          {compared && !history.some(row => row.id === compared.id) && <option value={compared.id}>{compared.name || new Date(compared.created_at).toLocaleString()}</option>}
          {history.filter(row => row.id !== runId).map(row => <option key={row.id} value={row.id}>
            {row.name ? row.name + ' · ' : ''}{new Date(row.created_at).toLocaleString()} · {row.status}</option>)}
        </select></label>
        {compareError && <p role="alert" className="cfg-error-text">{compareError}</p>}
        {compared && <StudioTable title="Run comparison" empty="Choose a run to compare."
          columns={[[ 'Run', 'run' ], ['Date','date'], ['Mode','mode'], ['Status','status'], ['Instruments','instruments'],
            ['Strategies','strategies'], ['Requested','total'], ['Checked','checked'], ['Signals','signals'], ['Errors','errors']]}
          items={[current, compared].map(run => ({ ...runCounts(run), run: run.name || (run === current ? 'Current' : 'Comparison'),
            date: new Date(run.created_at).toLocaleString(), mode: run.mode, status: run.status,
            instruments: run.instruments.length, strategies: run.strategies.length, total: run.total }))} />}
        {compared && current.request && compared.request &&
          (['entry_lookback', 'stop_atr', 'reward_risk'] as const).some(field => current.request[field] !== compared.request[field]) &&
          <p className="cfg-note">The entry window or protection settings differ.</p>}
        {compared && (current.mode !== compared.mode || current.instruments.join('|') !== compared.instruments.join('|') ||
          current.strategies.join('|') !== compared.strategies.join('|')) && <p className="cfg-note">The scan scopes differ.</p>}
      </details>
    </>}
  </section>;
}

export default function StrategyStudio() {
  const [section, setSection] = useSection('Strategy Studio', 'templates', studioSections);
  const lastEditor = useRef<EditorView>('templates');
  const lastResearch = useRef<ResearchStep>('scan');
  const currentSection = useRef(section);
  const researchHeading = useRef<HTMLHeadingElement>(null);
  const focusResearchHeading = useRef(false);
  const mode: Mode = section === 'scan-strategy' ? 'scan' : section === 'observe' || section === 'scan' || section === 'results' ? section : 'build';
  const editor = mode === 'build' ? section as EditorView : lastEditor.current;
  const [draft, setDraft] = useState(starterDraft);
  const [opened, setOpened] = useState<{ revision: DraftVersion; text: string } | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [canvasRevision, setCanvasRevision] = useState(0);
  const [catalog, setCatalog] = useState<NodeSpec[]>([]);
  const [catalogError, setCatalogError] = useState('');
  const [catalogBusy, setCatalogBusy] = useState(true);
  const [templates, setTemplates] = useState<Row[] | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState('');
  const [templateBusy, setTemplateBusy] = useState(false);
  const [applyingTemplate, setApplyingTemplate] = useState(false);
  const [templateError, setTemplateError] = useState('');
  const [templateNotice, setTemplateNotice] = useState('');
  const [testSource, setTestSource] = useState<'stored' | 'pasted'>('stored');
  const [previewBars, setPreviewBars] = useState('');
  const [storedSources, setStoredSources] = useState<Row[] | null>(null);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [sourceError, setSourceError] = useState('');
  const [storedSource, setStoredSource] = useState('');
  const [storedTimeframe, setStoredTimeframe] = useState('H1');
  const [storedCount, setStoredCount] = useState('300');
  const [testBusy, setTestBusy] = useState(false);
  const [checkedDraft, setCheckedDraft] = useState<string | null>(null);
  const [output, setOutput] = useState<TestOutput | null>(null);
  const [instruments, setInstruments] = useState<Row[]>([]);
  const [strategies, setStrategies] = useState<Row[]>([]);
  const [deployments, setDeployments] = useState<Row[]>([]);
  const [marketSnapshots, setMarketSnapshots] = useState<Row[]>([]);
  const [signals, setSignals] = useState<Row[]>([]);
  const [runtimeCount, setRuntimeCount] = useState<number | null>(null);
  const [instrument, setInstrument] = useState('');
  const [scanInstruments, setScanInstruments] = useState<string[] | null>(null);
  const [scanStrategies, setScanStrategies] = useState<string[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [universeReady, setUniverseReady] = useState(false);
  const [brokerOpen, setBrokerOpen] = useState(false);
  const brokerPanelId = useId();
  const [refreshError, setRefreshError] = useState('');
  const [updated, setUpdated] = useState('');
  const [background, setBackground] = useState(false);
  const [backgroundId, setBackgroundId] = useState('');
  const [watchlists, setWatchlists] = useState<Row[]>([]);
  const [watchSources, setWatchSources] = useState<Row[]>([]);
  const [watchlistError, setWatchlistError] = useState('');
  const [allInstrumentGroups, setAllInstrumentGroups] = useState(false);
  const [allStrategyGroups, setAllStrategyGroups] = useState(false);
  const [instrumentFilter, setInstrumentFilter] = useState<'all' | 'selected'>('all');
  const [presetName, setPresetName] = useState('');
  const [presets, setPresets] = useState<ScanPreset[]>(() => {
    try {
      const data = JSON.parse(localStorage.getItem(presetKey) ?? '[]');
      return Array.isArray(data) ? data.filter(item => typeof item?.name === 'string' && item.request &&
        (item.mode === 'instrument' || item.mode === 'strategy')) : [];
    } catch { return []; }
  });
  const [runName, setRunName] = useState('');
  const [readiness, setReadiness] = useState<{ key: string; data?: { requested: number; ready: number; items: Row[]; strategies?: Row[]; checked_at: string }; error?: string } | null>(null);
  const [readinessBusy, setReadinessBusy] = useState(false);
  const [quickDetail, setQuickDetail] = useState<Row | null>(null);
  const [presetError, setPresetError] = useState('');
  const [scanBusy, setScanBusy] = useState(false);
  const [scanScope, setScanScope] = useState<ScanScope>('instrument');
  const [strategyFilter, setStrategyFilter] = useState<'all' | 'selected'>('all');
  const [filteredStrategies, setFilteredStrategies] = useState<string[]>([]);
  const [entryLookback, setEntryLookback] = useState(3);
  const [stopAtr, setStopAtr] = useState('1.5');
  const [rewardRisk, setRewardRisk] = useState('2');
  const [scanRuns, setScanRuns] = useState<ScanRun[]>([]);
  const [viewedRun, setViewedRun] = useState(0);
  const [resultGroup, setResultGroup] = useState('');
  const scan = scanRuns[viewedRun] ?? null;
  const [scanError, setScanError] = useState<ScanFailure | null>(null);

  async function loadCatalog() {
    setCatalogBusy(true);
    setCatalogError('');
    try {
      const result = await api('trading/studio/builder/nodes');
      if (!Array.isArray(result.nodes) || !result.nodes.length) throw new Error('The server returned an empty node catalog.');
      setCatalog(result.nodes);
    } catch (error) {
      setCatalogError(message(error));
    } finally {
      setCatalogBusy(false);
    }
  }

  async function loadTemplates() {
    setTemplateBusy(true);
    setTemplateError('');
    try {
      const items = rows(await api('trading/studio/builder/templates'));
      setTemplates(items);
      setSelectedTemplate(current => items.some(item => item.id === current) ? current : '');
    } catch (error) {
      setTemplateError(message(error));
    } finally {
      setTemplateBusy(false);
    }
  }

  async function loadSources() {
    setSourceBusy(true);
    setSourceError('');
    try {
      const items = rows(await api('trading/sources'));
      setStoredSources(items);
      setStoredSource(current => items.some(item => sourceId(item) === current)
        ? current : items[0] ? sourceId(items[0]) : '');
    } catch (error) {
      setSourceError(message(error));
    } finally {
      setSourceBusy(false);
    }
  }

  async function loadScanUniverse() {
    const data = await api('trading/studio/scan-universe');
    setInstruments(data.instruments ?? []);
    setStrategies(data.strategies ?? []);
    setUniverseReady(true);
    setReadiness(null);
    return (data.instruments ?? []) as Row[];
  }

  async function refresh() {
    setUniverseReady(false);
    setRefreshing(true);
    setRefreshError('');
    const endpoints = [
      ['Deployments', 'config/deployments', setDeployments],
      ['Market context', 'intel/screener?timeframe=H1', setMarketSnapshots],
      ['Signals', 'trading/signals?limit=500', setSignals],
      ['Runtime', 'trading/runtime', (items: Row[]) => setRuntimeCount(items.length)],
    ] as const;
    const [universe, ...results] = await Promise.allSettled([loadScanUniverse(), ...endpoints.map(([, path]) => api(path))]);
    const failures: string[] = universe.status === 'rejected' ? ['Scan universe — ' + message(universe.reason)] : [];
    results.forEach((result, index) => {
      const [label, , setItems] = endpoints[index];
      if (result.status === 'fulfilled') setItems(rows(result.value));
      else failures.push(label + ' — ' + message(result.reason));
    });
    setRefreshError(failures.join(' · '));
    setUpdated(new Date().toLocaleTimeString());
    setRefreshing(false);
  }

  useEffect(() => {
    publishSections('Strategy Studio', explorerSections);
    void loadCatalog();
  }, []);
  useEffect(() => {
    currentSection.current = section;
    if (mode === 'build') lastEditor.current = editor;
    else if (section !== 'observe' && section !== 'results') lastResearch.current = section as ResearchStep;
    if (section === 'scan' || section === 'scan-strategy') setScanScope(section === 'scan' ? 'instrument' : 'strategy');
  }, [mode, editor, section]);
  useEffect(() => {
    if (focusResearchHeading.current) {
      focusResearchHeading.current = false;
      researchHeading.current?.focus();
    }
  }, [section]);
  useEffect(() => {
    if (section === 'templates' && templates === null && !templateBusy && !templateError) void loadTemplates();
  }, [section, templates, templateBusy, templateError]);
  useEffect(() => {
    if (section === 'test' && storedSources === null && !sourceBusy && !sourceError) void loadSources();
  }, [section, storedSources, sourceBusy, sourceError]);
  useEffect(() => {
    if (mode !== 'build' && !updated && !refreshing) void refresh();
  }, [mode, updated, refreshing]);

  async function useTemplate() {
    if (!selectedTemplate || templateBusy || applyingTemplate) return;
    if (draft !== starterDraft && draft !== opened?.text && !window.confirm('Replace the unsaved edits with this template?')) return;
    setApplyingTemplate(true);
    setTemplateError('');
    try {
      const definition = await api('trading/studio/builder/templates/' + encodeURIComponent(selectedTemplate));
      setDraft(JSON.stringify(definition, null, 2));
      setOpened(null);
      setOutput(null);
      setCheckedDraft(null);
      setCanvasRevision(current => current + 1);
      setTemplateNotice('Template loaded into the draft.');
      setSection('canvas');
    } catch (error) {
      setTemplateError(message(error));
    } finally {
      setApplyingTemplate(false);
    }
  }

  async function runTest(action: 'validate' | 'preview') {
    const capturedDraft = draft;
    const pasted = testSource === 'pasted';
    const source = storedSources?.find(item => sourceId(item) === storedSource);
    const title = action === 'validate' ? 'Draft validation' : pasted ? 'Pasted OHLC preview'
      : [source?.broker_symbol, source?.account_code ?? source?.account_id, storedTimeframe + ' preview'].filter(Boolean).join(' · ');
    setTestBusy(true);
    setOutput(null);
    try {
      const workflow = JSON.parse(capturedDraft);
      let data: Row;
      if (action === 'validate') {
        data = await api('trading/studio/builder/validate', 'POST', workflow);
      } else if (pasted) {
        data = await api('trading/studio/builder/preview', 'POST', { workflow, bars: JSON.parse(previewBars) });
      } else {
        if (!source) throw new Error('Select a market-data source.');
        data = await api('trading/studio/builder/preview-stored', 'POST', {
          workflow, account_id: source.account_id, symbol: source.broker_symbol,
          timeframe: storedTimeframe, bars: Number(storedCount),
        });
      }
      if (action === 'preview' || data.valid === true) setCheckedDraft(capturedDraft);
      else setCheckedDraft(previous => previous === capturedDraft ? null : previous);
      setOutput({ title, draft: capturedDraft, data });
    } catch (error) {
      setOutput({ title, draft: capturedDraft, error: message(error) });
    } finally {
      setTestBusy(false);
    }
  }

  const observing = mode === 'observe';
  useEffect(() => {
    if (mode !== 'scan') return;
    let live = true;
    setWatchlistError('');
    Promise.all([api('intel/watchlists'), api('trading/sources')]).then(([lists, sources]) => {
      if (live) { setWatchlists(rows(lists)); setWatchSources(rows(sources)); }
    }).catch(error => { if (live) setWatchlistError(message(error)); });
    return () => { live = false; };
  }, [mode, updated]);

  const activeInstruments = instruments;
  const activeStrategies = strategies;
  const selectedInstrument = activeInstruments.some(item => instrumentLabel(item) === instrument)
    ? instrument : instrumentLabel(activeInstruments[0] ?? {});
  const scope = observing || section === 'scan' ? 'instrument' : section === 'scan-strategy' ? 'strategy' : scanScope;
  const byInstrument = scope === 'instrument';
  const allInstruments = activeInstruments.map(instrumentLabel);
  const allStrategies = activeStrategies.map(item => String(item.code));
  const instrumentSet = new Set(allInstruments);
  const strategySet = new Set(allStrategies);
  const selectedSymbols = (scanInstruments ?? [selectedInstrument]).filter(symbol => instrumentSet.has(symbol));
  const selectedCodes = filteredStrategies.filter(code => strategySet.has(code));
  const chosenStrategies = (scanStrategies ?? allStrategies.slice(0, 1)).filter(code => strategySet.has(code));
  const selectInstruments = byInstrument ? !allInstrumentGroups : instrumentFilter === 'selected';
  const selectStrategies = byInstrument ? strategyFilter === 'selected' : !allStrategyGroups;
  const instrumentScope = selectInstruments ? selectedSymbols : allInstruments;
  const strategyScope = selectStrategies ? (byInstrument ? selectedCodes : chosenStrategies) : allStrategies;
  const groups = byInstrument ? (observing ? [selectedInstrument] : instrumentScope) : strategyScope;
  const selection = groups.length > 5 ? groups.slice(0, 5).join(', ') + ' and ' + (groups.length - 5) + ' more' : groups.join(', ');
  const matching = deployments.filter(row => groups.some(group => group && group === (byInstrument
    ? (row.instrument_symbol ?? row.symbol) : row.strategy_code)));
  const observedSnapshots = observing && selectedInstrument ? marketSnapshots.filter(row => row.instrument === selectedInstrument) : [];
  const signalScope = observing ? { scope: 'instrument', groups: [selectedInstrument] } : scan;
  const visibleSignals = signalScope ? signals.filter(row => signalScope.groups.some(group => group === (signalScope.scope === 'instrument'
    ? (row.symbol ?? row.instrument_symbol) : row.strategy_code))) : [];
  const currentScanError = scanError?.scope === scope && scanError.selection === selection ? scanError.message : '';
  const pairCount = instrumentScope.length * strategyScope.length;
  const useBackground = background || pairCount > 100 || Boolean(runName.trim());
  const validProtection = [stopAtr, rewardRisk].every(value => /^\d+(\.\d{1,2})?$/.test(value) && Number(value) >= 0.1 && Number(value) <= 20);
  const canScan = universeReady && !refreshing && !scanBusy && pairCount > 0 && validProtection;
  const groupKey = scan?.scope === 'strategy' ? 'strategy_code' : 'instrument_symbol';
  const resultItems = scan?.items.filter(item => !resultGroup || item[groupKey] === resultGroup) ?? [];
  const rankedSignals = resultItems.filter(item => item.status === 'SIGNAL').sort((a, b) =>
    String(a.strategy_code).localeCompare(String(b.strategy_code)) || Number(b.strength) - Number(a.strength));
  const comparable = new Set(rankedSignals.map(row => row.strategy_code)).size === 1;
  const strongest = comparable ? rankedSignals[0] : undefined;
  const tiedForStrongest = strongest ? rankedSignals.filter(item => item.strength === strongest.strength).length : 0;
  const scanCounts = scan && [
    ['Checked', resultItems.length],
    ['Signals', rankedSignals.length],
    ['Waiting', resultItems.filter(item => item.status === 'WAIT').length],
    ['Errors', resultItems.filter(item => item.status === 'ERROR').length],
  ] as const;

  function advanceResearch(next: ResearchStep) {
    focusResearchHeading.current = true;
    setSection(next);
  }

  const scanSection = scope === 'strategy' ? 'scan-strategy' : 'scan';

  async function addBrokerSymbols(symbols: string[]) {
    const active = new Set((await loadScanUniverse()).map(instrumentLabel));
    const added = symbols.filter(symbol => active.has(symbol));
    if (!added.length) throw new Error('No active COREI instruments were found for this selection.');
    setAllInstrumentGroups(false);
    setInstrumentFilter('selected');
    setScanInstruments(previous => [...new Set([...(previous ?? [selectedInstrument]).filter(symbol => active.has(symbol)), ...added])]);
    setReadiness(null);
  }

  function openScan() {
    setAllInstrumentGroups(false);
    setScanInstruments(selectedInstrument ? [selectedInstrument] : null);
    setScanScope(selectedInstrument ? 'instrument' : 'strategy');
    advanceResearch('scan');
  }

  const scanRequest: ScanRequest = {
    ...(byInstrument
      ? { instrument_symbols: instrumentScope, ...(selectStrategies ? { strategy_codes: strategyScope } : {}) }
      : { strategy_codes: strategyScope, ...(selectInstruments ? { instrument_symbols: instrumentScope } : {}) }),
    entry_lookback: entryLookback, stop_atr: stopAtr, reward_risk: rewardRisk,
  };

  useEffect(() => {
    if (mode !== 'scan') return;
    let live = true;
    api(scanRunsPath + '/profiles').then(data => {
      if (!live) return;
      const profiles = rows(data).map(item => ({ name: String(item.name), mode: item.mode as ScanScope,
        request: item.request as ScanRequest, allInstruments: Boolean(item.all_instruments), allStrategies: Boolean(item.all_strategies) }));
      setPresets(previous => [...previous.filter(item => !profiles.some(profile => profile.name === item.name)), ...profiles]);
    }).catch(error => { if (live) setPresetError(message(error)); });
    return () => { live = false; };
  }, [mode]);

  const frozenRequest = { ...scanRequest, instrument_symbols: instrumentScope, strategy_codes: strategyScope };
  const readinessKey = JSON.stringify([frozenRequest, updated]);
  const currentReadiness = readiness?.key === readinessKey ? readiness : null;

  async function checkReadiness() {
    if (!canScan || readinessBusy) return;
    setReadinessBusy(true);
    try {
      const data = await api('trading/studio/scan-readiness', 'POST', frozenRequest);
      setReadiness({ key: readinessKey, data });
    } catch (error) {
      setReadiness({ key: readinessKey, error: message(error) });
    } finally {
      setReadinessBusy(false);
    }
  }

  async function savePreset() {
    const name = presetName.trim();
    if (!name || !pairCount || !validProtection) return;
    const preset = { name, mode: scope, request: scanRequest, allInstruments: allInstrumentGroups, allStrategies: allStrategyGroups };
    try {
      await api(scanRunsPath + '/profiles', 'PUT', { name, mode: scope, request: scanRequest,
        all_instruments: allInstrumentGroups, all_strategies: allStrategyGroups });
      setPresets(previous => [...previous.filter(item => item.name !== name), preset]);
      setPresetError(''); setPresetName('');
    } catch (error) { setPresetError(message(error)); }
  }

  function openPreset(preset: ScanPreset) {
    const request = preset.request;
    setScanScope(preset.mode);
    setSection(preset.mode === 'strategy' ? 'scan-strategy' : 'scan');
    setAllInstrumentGroups(Boolean(preset.allInstruments));
    setAllStrategyGroups(Boolean(preset.allStrategies));
    setScanInstruments(request.instrument_symbols ?? null);
    setInstrumentFilter(request.instrument_symbols ? 'selected' : 'all');
    setStrategyFilter(request.strategy_codes ? 'selected' : 'all');
    setFilteredStrategies(request.strategy_codes ?? []);
    setScanStrategies(request.strategy_codes ?? null);
    setEntryLookback(request.entry_lookback);
    setStopAtr(request.stop_atr); setRewardRisk(request.reward_risk);
    setScanError(null);
  }

  async function runScan() {
    if (!canScan) return;
    setScanBusy(true);
    setScanError(null);
    const request = frozenRequest;
    try {
      if (useBackground) {
        const result = await api(scanRunsPath, 'POST', { mode: scope,
          name: runName.trim() || undefined, request: frozenRequest });
        setBackgroundId(result.id);
        if (currentSection.current === scanSection) advanceResearch('results');
        return;
      }
      setBackgroundId('');
      const result = await api('trading/studio/signal-scan', 'POST', request);
      const completed = { scope, selection, groups, request, items: rows(result),
        completedAt: result.scanned_at ? new Date(result.scanned_at) : new Date() };
      setScanRuns(current => [completed, ...current].slice(0, 12));
      setViewedRun(0);
      setResultGroup('');
      setQuickDetail(null);
      if (currentSection.current === scanSection) advanceResearch('results');
    } catch (error) {
      setScanError({ scope, selection, message: message(error) });
    } finally {
      setScanBusy(false);
    }
  }

  const recordedSignals = <details className="studio-panel studio-details">
    <summary>Recorded signals <small>· {visibleSignals.length} loaded for this scope</small></summary>
    <p className="cfg-note">Recorded signals come from the runtime. Open signal details to inspect validated trade plans.</p>
    <div className="studio-actions">
      <button onClick={() => goto('Strategies', 'signals')}>Signal details</button>
      <button onClick={() => goto('Risk', 'decisions')}>Risk decisions</button>
    </div>
    <StudioTable title="Recorded signals" items={visibleSignals.slice(0, 100)}
      columns={[[ 'Time', 'bar_time' ], [ 'Strategy', 'strategy_code' ], [ 'Instrument', 'symbol' ],
        [ 'Side', 'side' ], [ 'State', 'state' ], [ 'Reason', 'reasons' ]]}
      empty={refreshing ? 'Loading recorded signals…' : 'No recorded signals for this selection.'} />
  </details>;

  const workflow = readWorkflow(draft);
  const saved = opened?.text === draft ? opened.revision : null;
  const savedStatus = saved ? 'Saved research draft · v' + saved.version
    : opened ? 'Unsaved edits to v' + opened.revision.version : 'Unsaved research draft';
  const checked = checkedDraft === draft;
  const validCount = Number.isInteger(Number(storedCount)) && Number(storedCount) >= 10 && Number(storedCount) <= 5000;
  const currentStep = editor === 'json' ? 'canvas' : editor;
  const stepIndex = buildSteps.findIndex(step => step.id === currentStep);
  const step = buildSteps[stepIndex];
  const locked = applyingTemplate || draftBusy;
  const stepStatus: Partial<Record<BuildStep, string>> = {
    test: checked ? 'Checked' : undefined,
    drafts: saved ? 'v' + saved.version + ' saved' : undefined,
    review: saved?.promoted_graph_code ? 'Graph created' : undefined,
  };
  const instrumentScopeCard = <section key="instruments" className="studio-scope-card" aria-label="Instrument scope">
    <h4>{byInstrument ? '1. Instruments' : '2. Instruments'} <small>{instrumentScope.length.toLocaleString()} in scope</small></h4>
    <small className="cfg-note">{activeInstruments.length.toLocaleString()} registered active · {activeInstruments.filter(row => row.has_source).length.toLocaleString()} with a mapped source</small>
    <div className="studio-actions"><label>Instruments to scan
      <select value={selectInstruments ? 'selected' : 'all'} disabled={scanBusy} onChange={event => {
        const selected = event.target.value === 'selected';
        if (byInstrument) setAllInstrumentGroups(!selected);
        else setInstrumentFilter(selected ? 'selected' : 'all');
      }}>
        <option value="all">All registered active instruments</option><option value="selected">Selected instruments</option>
      </select>
    </label></div>
    <div className="studio-actions">
      <button type="button" aria-expanded={brokerOpen} aria-controls={brokerOpen ? brokerPanelId : undefined} disabled={scanBusy}
        onClick={() => setBrokerOpen(value => !value)}>{brokerOpen ? 'Hide broker catalogue' : 'Browse broker catalogue'}</button>
    </div>
    {!observing && watchlists.length > 0 && <div className="studio-actions"><label>Use instrument watchlist
      <select value="" disabled={scanBusy} onChange={event => {
        const list = watchlists.find(item => item.id === event.target.value);
        if (!list) return;
        const entries = Array.isArray(list.items) ? list.items as Row[] : [];
        const keys = new Set(entries.map(sourceId));
        const mapped = [...new Set(watchSources.filter(source => keys.has(sourceId(source)))
          .map(source => String(source.instrument ?? ''))
          .filter(symbol => instrumentSet.has(symbol)))];
        setAllInstrumentGroups(false);
        setScanInstruments(mapped);
        setInstrumentFilter('selected');
        setWatchlistError(mapped.length ? '' : 'This watchlist has no collected sources mapped to active instruments.');
      }}>
        <option value="">Choose a watchlist</option>
        {watchlists.map(item => <option key={String(item.id)} value={String(item.id)}>{String(item.name)}</option>)}
      </select>
    </label></div>}
    {!observing && watchlistError && <p className="cfg-note">Watchlists · {watchlistError}</p>}
    {selectInstruments && <ScanSelection label="Instruments" items={activeInstruments.map(item => ({
      value: instrumentLabel(item),
      label: [instrumentLabel(item), item.name, item.has_source === false ? 'Needs data source' : ''].filter(Boolean).join(' · '),
      search: [item.asset_class, ...(Array.isArray(item.aliases) ? item.aliases : [])].filter(Boolean).join(' ')
    }))} selected={selectedSymbols} change={setScanInstruments} disabled={scanBusy} loading={refreshing} />}
  </section>;
  const strategyScopeCard = <section key="strategies" className="studio-scope-card" aria-label="Strategy scope">
    <h4>{byInstrument ? '2. Strategies' : '1. Strategies'} <small>{strategyScope.length.toLocaleString()} in scope</small></h4>
    <div className="studio-actions"><label>Strategies to scan
      <select value={selectStrategies ? 'selected' : 'all'} disabled={scanBusy} onChange={event => {
        const selected = event.target.value === 'selected';
        if (byInstrument) setStrategyFilter(selected ? 'selected' : 'all');
        else setAllStrategyGroups(!selected);
      }}>
        <option value="all">All strategies</option><option value="selected">Selected strategies</option>
      </select>
    </label></div>
    {selectStrategies && <ScanSelection label="Strategies" items={activeStrategies.map(item => ({
      value: String(item.code), label: String(item.name ?? item.code)
    }))} selected={byInstrument ? selectedCodes : chosenStrategies}
      change={byInstrument ? setFilteredStrategies : setScanStrategies} disabled={scanBusy} loading={refreshing} />}
  </section>;
  return <section className="studio" aria-label="Strategy Intelligence Studio">
    <header className="studio-header">
      <h2>Strategy Intelligence Studio</h2>
      {mode !== 'build' && <div className="studio-actions">
        {updated && <small>Refreshed {updated}</small>}
        <button disabled={refreshing} onClick={() => void refresh()}>{refreshing ? 'Refreshing…' : 'Refresh data'}</button>
      </div>}
    </header>
    <nav className="studio-nav" aria-label="Studio workspace">
      <button aria-pressed={mode === 'build'} disabled={locked}
        onClick={() => setSection(lastEditor.current)}>Build strategy</button>
      <button aria-pressed={mode !== 'build'} disabled={locked}
        onClick={() => setSection(lastResearch.current)}>Signal scanner</button>
    </nav>

    <div hidden={mode !== 'build'}>
      <nav className="studio-steps" aria-label="Strategy build steps">
        {buildSteps.map(({ id, title }, index) => <button key={id} type="button"
          aria-current={currentStep === id ? 'step' : undefined}
          disabled={locked} onClick={() => setSection(id)}>
          <span className="studio-step-number" aria-hidden="true">{index + 1}</span>
          <span className="studio-step-label"><strong>{title}</strong>{stepStatus[id] && <small>{stepStatus[id]}</small>}</span>
        </button>)}
      </nav>
      <div className="studio-stage">
        <div>
          <h3>{editor === 'json' ? 'Workflow JSON' : step.title}</h3>
          <p className="cfg-note">{editor === 'json' ? 'Edit the same workflow shown on the canvas.' : step.description}</p>
        </div>
        <div className="studio-draft-title">
          <strong>{workflow?.name || 'Workflow draft'}</strong>
          <small>{savedStatus}</small>
        </div>
        <div className="studio-actions studio-stage-actions">
          {editor === 'templates' && <button disabled={locked} onClick={() => setSection('canvas')}>{draft === starterDraft && !opened ? 'Edit starter workflow' : 'Resume current workflow'}</button>}
          {editor === 'canvas' && <>
            <button disabled={locked} onClick={() => setSection('json')}>Workflow JSON</button>
            <button className="primary" disabled={locked} onClick={() => setSection('test')}>Next: validate & preview</button>
          </>}
          {editor === 'json' && <button className="primary" disabled={locked} onClick={() => setSection('canvas')}>Back to canvas</button>}
          {editor === 'test' && <button className={checked ? 'primary' : undefined} disabled={testBusy}
            onClick={() => setSection('drafts')}>Next: save revision</button>}
          {editor === 'drafts' && saved && <button className="primary" disabled={locked} onClick={() => setSection('review')}>Next: review & use</button>}
          {editor === 'review' && !saved && <button className="primary" disabled={locked} onClick={() => setSection('drafts')}>Save a revision</button>}
        </div>
      </div>
      {templateNotice && editor === 'canvas' && <div className="studio-inline-notice" role="status">{templateNotice}</div>}
      <div hidden={editor !== 'canvas'} inert={draftBusy || applyingTemplate}>
        {catalogBusy && <div className="studio-empty" role="status">Loading node catalog…</div>}
        {catalogError && <div className="studio-empty">
          <p role="alert" className="cfg-error-text">{catalogError}</p>
          <button onClick={() => void loadCatalog()}>Retry node catalog</button>
        </div>}
        {catalog.length > 0 && <UniversalCanvas key={canvasRevision} draft={draft}
          onChange={value => { setDraft(value); setTemplateNotice(''); }} catalog={catalog}
          onOpenJson={() => setSection('json')} onRefreshCatalog={() => void loadCatalog()} />}
      </div>

      {editor === 'templates' && <section className="studio-panel">
        <h3>Research templates</h3>
        <p className="cfg-note">Start a new research draft from a template.</p>
        {templateError && <p role="alert" className="cfg-error-text">{templateError}</p>}
        <div className="studio-actions">
          <label>Template
            <select value={selectedTemplate} disabled={templateBusy || applyingTemplate} onChange={event => setSelectedTemplate(event.target.value)}>
              <option value="">{templateBusy && !templates ? 'Loading templates…' : 'Select a template'}</option>
              {templates?.map(item => <option key={String(item.id)} value={String(item.id)}>
                {String(item.category)} · {String(item.name)}
              </option>)}
            </select>
          </label>
          <button className="primary" disabled={!selectedTemplate || templateBusy || applyingTemplate} onClick={() => void useTemplate()}>
            {applyingTemplate ? 'Loading draft…' : 'Load into draft'}
          </button>
          <button disabled={templateBusy || applyingTemplate} onClick={() => void loadTemplates()}>Refresh templates</button>
        </div>
        {templates?.length === 0 && <p role="status" className="cfg-note">No templates are available.</p>}
      </section>}

      <StudioDrafts draft={draft} opened={opened} section={mode === 'build' && (editor === 'templates' || editor === 'drafts' || editor === 'review') ? editor : null}
        onBusy={setDraftBusy} onResume={() => setSection('canvas')}
        onOpen={value => {
          const edited = draft !== value.text;
          setOpened(value);
          setDraft(value.text);
          setTemplateNotice('');
          if (edited) setCanvasRevision(current => current + 1);
        }} />

      {editor === 'json' && <section className="studio-panel">
        <textarea id="studio-workflow-json" aria-label="Strategy workflow JSON" className="studio-json" spellCheck={false} rows={18} value={draft}
          disabled={draftBusy || applyingTemplate}
          onChange={event => { setDraft(event.target.value); setTemplateNotice(''); }} />
      </section>}

      {editor === 'test' && <section className="studio-panel" aria-label="Draft tests">
        <div className="studio-section-title">
          <div><h3>Validate workflow</h3><p className="cfg-note">Check node parameters, required connections and cycles.</p></div>
          <div className="studio-actions">
            {checked && <small>Current workflow checked</small>}
            <button className={!checked ? 'primary' : undefined} disabled={testBusy} onClick={() => void runTest('validate')}>Validate draft</button>
          </div>
        </div>
        <div className="studio-test-section">
          <h3>Preview on closed bars</h3>
          <p className="cfg-note">Preview shows historical signals. Backtest in Results estimates fills and performance.</p>
          <nav className="studio-subnav" aria-label="Preview data">
            <button aria-pressed={testSource === 'stored'} disabled={testBusy} onClick={() => setTestSource('stored')}>Stored market data</button>
            <button aria-pressed={testSource === 'pasted'} disabled={testBusy} onClick={() => setTestSource('pasted')}>Paste OHLC JSON</button>
          </nav>
          {testSource === 'stored' ? <>
            {sourceError && <p role="alert" className="cfg-error-text">{sourceError}</p>}
            <div className="studio-form-row">
              <label>Source
                <select aria-label="Stored market-data source" value={storedSource} disabled={sourceBusy || testBusy}
                  onChange={event => setStoredSource(event.target.value)}>
                  <option value="" disabled>{sourceBusy ? 'Loading sources…' : 'Select a source'}</option>
                  {storedSources?.map(item => <option key={sourceId(item)} value={sourceId(item)}>
                    {String(item.broker_symbol)} · {String(item.account_code ?? item.account_id)}
                  </option>)}
                </select>
              </label>
              <label>Timeframe
                <select value={storedTimeframe} disabled={testBusy} onChange={event => setStoredTimeframe(event.target.value)}>
                  {timeframes.map(timeframe => <option key={timeframe}>{timeframe}</option>)}
                </select>
              </label>
              <label>Bars
                <input aria-label="Preview bar count" type="number" min={10} max={5000} value={storedCount} disabled={testBusy}
                  onChange={event => setStoredCount(event.target.value)} />
              </label>
              <div className="studio-actions">
                <button disabled={sourceBusy || testBusy} onClick={() => void loadSources()}>Refresh sources</button>
                <button className={checked ? 'primary' : undefined} disabled={testBusy || sourceBusy || !storedSource || !validCount}
                  onClick={() => void runTest('preview')}>Preview stored bars</button>
              </div>
            </div>
            {storedSources?.length === 0 && <p role="status" className="cfg-note">No collected market-data sources are available.</p>}
            {!validCount && <p className="cfg-note">Enter a whole number from 10 to 5000.</p>}
          </> : <>
            <label className="studio-field">Historical OHLC bars JSON
              <textarea className="studio-json" rows={7} spellCheck={false} value={previewBars} disabled={testBusy}
                onChange={event => setPreviewBars(event.target.value)}
                placeholder='[{"open":100,"high":102,"low":99,"close":101}]' />
            </label>
            <p className="cfg-note">Paste 2–5000 historical bars in chronological order. Indicators may require additional warm-up bars.</p>
            <button className="primary" disabled={testBusy || !previewBars.trim()} onClick={() => void runTest('preview')}>Preview closed bars</button>
          </>}
        </div>
        {testBusy && <p role="status" className="cfg-note">Evaluating draft…</p>}
        {output && output.draft !== draft && <p role="status" className="cfg-note">The draft changed. Run validation or preview again.</p>}
        {output && output.draft === draft && <div className="studio-test-section">
          <h3>{output.title}</h3>
          {output.error && <p role="alert" className="cfg-error-text">{output.error}</p>}
          {output.data && <>
            <p role="status">{output.data.bars_evaluated !== undefined
              ? <><strong>{String(output.data.latest)}</strong> on the last closed bar · {String(output.data.bars_evaluated)} bars evaluated</>
              : <strong>{output.data.valid ? 'Workflow is valid' : 'Validation completed'}</strong>}</p>
            {output.data.notice != null && <p className="cfg-note">{String(output.data.notice)}</p>}
            <details><summary>Response details</summary><pre>{JSON.stringify(output.data, null, 2)}</pre></details>
          </>}
        </div>}
      </section>}
    </div>

    {mode !== 'build' && <nav className="studio-steps studio-research-steps" aria-label="Signal scanner tools">
      {researchSteps.map(({ id, title }) => <button key={id} type="button"
        aria-current={section === id ? 'page' : undefined} disabled={scanBusy}
        onClick={() => advanceResearch(id)}>
        <span className="studio-step-label"><strong>{title}</strong></span>
      </button>)}
    </nav>}
    {mode !== 'build' && refreshError && <p role="alert" className="cfg-error-text">Data refresh failed for {refreshError}</p>}

    {(observing || mode === 'scan') && <div className="studio-stack">
      <section className="studio-panel" aria-labelledby="studio-research-scope">
        <div className="studio-section-title">
          <div><h3 id="studio-research-scope" ref={researchHeading} tabIndex={-1}>{observing ? 'Inspect one instrument' : 'Configure scan'}</h3>
            <p className="cfg-note">{observing ? 'Review the market context, then carry this instrument into the signal scanner.'
              : byInstrument ? 'Choose your instruments, then choose the strategies to check against them.'
              : 'Choose your strategies, then choose where to look for matching instruments.'}</p></div>
        </div>
        {observing && <div className="studio-form-row">
          {observing && <label>Instrument
            <select value={selectedInstrument} disabled={scanBusy} onChange={event => setInstrument(event.target.value)}>
              {!activeInstruments.length && <option value="">{refreshing ? 'Loading instruments…' : 'No instruments'}</option>}
              {activeInstruments.map(item => <option key={String(item.id ?? instrumentLabel(item))} value={instrumentLabel(item)}>
                {instrumentLabel(item)}
              </option>)}
            </select>
          </label>}
          {observing && <div className="studio-actions">
            <button className="primary" disabled={scanBusy || !selectedInstrument} onClick={openScan}>Open scanner for this instrument</button>
          </div>}
        </div>}
        {!observing && <>
          <div className="studio-scope-grid">
            {byInstrument ? instrumentScopeCard : strategyScopeCard}
            {byInstrument ? strategyScopeCard : instrumentScopeCard}
          </div>
          {brokerOpen && <StudioBrokerCatalogue id={brokerPanelId} add={addBrokerSymbols} disabled={scanBusy || refreshing} />}
        </>}
        {!observing && <div className="studio-scan-options">
        {!observing && <details className="studio-scan-settings">
          <summary>Signal settings · Stop {stopAtr || '—'}× ATR14 · Target {rewardRisk || '—'}R</summary>
          <div className="studio-actions">
            <label>Entry trigger window
              <select value={entryLookback} disabled={scanBusy} onChange={event => setEntryLookback(Number(event.target.value))}>
                <option value={1}>Latest closed bar only</option>
                <option value={3}>Last 3 closed bars</option>
                <option value={5}>Last 5 closed bars</option>
                <option value={10}>Last 10 closed bars</option>
                <option value={20}>Last 20 closed bars</option>
              </select>
            </label>
            <label>Stop distance (ATR14)
              <input type="number" min="0.1" max="20" step="0.01" value={stopAtr} disabled={scanBusy}
                onChange={event => setStopAtr(event.target.value)} />
            </label>
            <label>Target reward / risk
              <input type="number" min="0.1" max="20" step="0.01" value={rewardRisk} disabled={scanBusy}
                onChange={event => setRewardRisk(event.target.value)} />
            </label>
          </div>
        </details>}
        {!observing && <details className="studio-scan-settings">
          <summary>Scan presets</summary>
          <div className="studio-actions">
            <label>Preset<select value="" disabled={scanBusy} onChange={event => {
              const preset = presets.find(item => item.name === event.target.value);
              if (preset) openPreset(preset);
            }}>
              <option value="">Load a preset</option>
              {presets.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}
            </select></label>
            <label>Preset name<input value={presetName} maxLength={80} onChange={event => setPresetName(event.target.value)} /></label>
            <button type="button" disabled={scanBusy || !pairCount || !validProtection || !presetName.trim()} onClick={() => void savePreset()}>Save preset</button>
          </div>
          {presetError && <p role="alert" className="cfg-error-text">{presetError}</p>}
        </details>}
        </div>}
        {!observing && <div className="studio-run-bar">
          <div>
            <strong>{pairCount.toLocaleString()} {pairCount === 1 ? 'check' : 'checks'}</strong>
            <p className="cfg-note">{instrumentScope.length.toLocaleString()} {instrumentScope.length === 1 ? 'instrument' : 'instruments'} × {strategyScope.length.toLocaleString()} {strategyScope.length === 1 ? 'strategy' : 'strategies'}
              {' · '}{useBackground ? 'Background run' : 'Quick scan'}</p>
          </div>
          <div className="studio-actions">
            {pairCount <= 100 && <label className="studio-background-option"><input type="checkbox" checked={background} disabled={scanBusy}
              onChange={event => setBackground(event.target.checked)} /> Run in background and save results</label>}
            <label>Run name <input placeholder="Optional" maxLength={80} value={runName} disabled={scanBusy}
              onChange={event => setRunName(event.target.value)} /></label>
            <button disabled={!canScan || readinessBusy} onClick={() => void checkReadiness()}>
              {readinessBusy ? 'Checking readiness…' : 'Check readiness'}
            </button>
            <button className="primary" disabled={!canScan} onClick={() => void runScan()}>
              {scanBusy ? 'Scanning…' : currentScanError ? 'Retry scan' : 'Run scan'}
            </button>
          </div>
        </div>}
        {!observing && currentReadiness?.error && <p role="alert" className="cfg-error-text">{currentReadiness.error}</p>}
        {!observing && currentReadiness?.data && <section className="studio-scan-settings">
          <h3>Scan readiness</h3>
          <p role="status">{currentReadiness.data.requested.toLocaleString()} requested · {currentReadiness.data.ready.toLocaleString()} ready ·
            {' '}{(currentReadiness.data.requested - currentReadiness.data.ready).toLocaleString()} blocked</p>
          <p className="cfg-note">Checked at {new Date(currentReadiness.data.checked_at).toLocaleString()}. Readiness uses the latest approved strategy settings and available closed bars.</p>
          {!!currentReadiness.data.strategies?.length && <details><summary>Strategy versions and timeframes</summary>
            <StudioTable title="Strategy readiness" columns={[[ 'Strategy', 'strategy' ], ['Approved version','version'],
              ['Timeframe','timeframe'], ['Ready','ready'], ['Blocked','blocked']]}
              items={currentReadiness.data.strategies} empty="No strategies in scope." />
          </details>}
          {currentReadiness.data.requested > currentReadiness.data.ready && <>
            <div className="studio-actions">
              <button onClick={() => goto('Market data', 'plans')}>Market data setup</button>
              <button onClick={() => goto('Configuration', 'strategies')}>Strategy setup</button>
            </div>
          </>}
          <details><summary>Data sources and readiness by instrument</summary>
            <StudioTable title="Instrument readiness" columns={[[ 'Instrument', 'instrument' ], ['Sources','sources'],
              ['Ready','ready'], ['Blocked','blocked'], ['Blockers','reason']]}
              items={currentReadiness.data.items.map(row => ({ ...row,
                reason: Object.entries(row.reasons as Record<string, number>).map(([reason, count]) => reason + ' (' + count + ')') }))}
              empty="No instruments in scope." />
          </details>
        </section>}
        {!observing && !validProtection && <p role="alert" className="cfg-error-text">Use values from 0.10 to 20 with up to two decimal places.</p>}
        {!observing && scanBusy && <p role="status" className="cfg-note">Scanning closed bars for {selection}…</p>}
        {!observing && currentScanError && <p role="alert" className="cfg-error-text">Scan failed. {currentScanError}</p>}
      </section>
      {observing && <section className="studio-panel">
        <div className="studio-section-title">
          <h3>Market context · H1</h3>
          <button onClick={() => goto('Intelligence', 'instrument')}>Open instrument analysis</button>
        </div>
        <StudioTable title="Market context" items={observedSnapshots}
          columns={[[ 'Broker symbol', 'broker_symbol' ], [ 'Account', 'account_code' ], [ 'Regime', 'regime' ],
            [ 'Volatility', 'volatility' ], [ 'Closed bar', 'bar_time' ]]}
          empty={refreshing ? 'Loading market context…' : !selection ? 'Select an instrument to see its market context.'
            : 'No H1 snapshot is available for this instrument.'} />
      </section>}
      {observing && <details className="studio-panel studio-details">
        <summary>Deployment coverage</summary>
        <div className="studio-section-title">
          <button onClick={() => goto('Configuration', 'deployments')}>Configure deployments</button>
        </div>
        <StudioTable title="Deployment coverage" items={matching.map(row => ({ ...row, instrument: row.instrument_symbol ?? row.symbol }))}
          columns={[[ 'Strategy', 'strategy_code' ], [ 'Instrument', 'instrument' ], [ 'Account', 'account_code' ],
            [ 'Status', 'status' ], [ 'Mode', 'mode' ]]}
          empty={refreshing ? 'Loading deployments…' : !selection ? 'Select a scope to see deployments.' : 'No matching deployments.'} />
      </details>}
      {observing && recordedSignals}
    </div>}

    {mode === 'results' && <div className="studio-stack">
      <section className="studio-panel" aria-labelledby="studio-assessment">
        <div className="studio-section-title">
          <div>
            <h3 id="studio-assessment" ref={researchHeading} tabIndex={-1}>Signal scan results</h3>
            {scan && !backgroundId && <p className="cfg-note">{scan.scope === 'instrument' ? 'Instrument' : 'Strategy'} · {scan.selection}
              {' · '}{scan.completedAt.toLocaleString()}{scan.scope === 'instrument'
                ? ' · ' + (scan.request.strategy_codes?.join(', ') ?? 'All strategies') : ''}</p>}
          </div>
          <button className={scan ? undefined : 'primary'} onClick={() => advanceResearch(scanSection)}>
            {scan || backgroundId ? 'Back to scan' : 'New scan'}
          </button>
        </div>
        {!backgroundId && scanRuns.length > 1 && <div className="studio-actions"><label>Scan history
          <select value={viewedRun} onChange={event => { setViewedRun(Number(event.target.value)); setQuickDetail(null); setResultGroup(''); }}>
            {scanRuns.map((run, index) => <option key={index} value={index}>
              {run.scope === 'instrument' ? 'Instrument' : 'Strategy'} · {run.selection} · {run.completedAt.toLocaleTimeString()}
            </option>)}
          </select>
        </label></div>}
        {scanBusy && <p role="status" className="cfg-note">{scan ? 'New scan running. Showing a completed scan.' : 'Scan in progress…'}</p>}
        {backgroundId && scan && <button onClick={() => setBackgroundId('')}>Show quick scan results</button>}
        {scan && !backgroundId && <>
          {scan.groups.length > 1 && <div className="studio-actions"><label>{scan.scope === 'strategy' ? 'Results by strategy' : 'Results by instrument'}
            <select value={resultGroup} onChange={event => { setResultGroup(event.target.value); setQuickDetail(null); }}>
              <option value="">All results</option>
              {scan.groups.map(group => <option key={group} value={group}>{group}</option>)}
            </select>
          </label></div>}
          {scan.groups.length > 1 && !resultGroup && <StudioTable title="Best matches by selection" onGroup={value => { setResultGroup(value); setQuickDetail(null); }} items={scan.groups.map(group => {
            const hits = rankedSignals.filter(row => row[groupKey] === group);
            const best = hits[0];
            const ties = scan.scope === 'instrument' ? hits : best ? hits.filter(row => row.strength === best.strength) : [];
            return { group, signals: hits.length, best: ties.length ? ties.map(row => row[scan.scope === 'strategy' ? 'instrument_symbol' : 'strategy_code']) : 'No qualifying entry',
              strength: best?.strength };
          })} columns={[[scan.scope === 'strategy' ? 'Strategy' : 'Instrument', 'group'], ['Signals', 'signals'],
            [scan.scope === 'instrument' ? 'Strategies with signals' : 'Strongest match', 'best'],
            ...(scan.scope === 'strategy' ? [['Strength / 100', 'strength'] as [string, string]] : [])]} empty="No scan results." />}
          <dl className="studio-scan-summary">
            {scanCounts?.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
          </dl>
          {strongest && (scan.groups.length === 1 || resultGroup) && (tiedForStrongest > 1
            ? <p><strong>Strongest score</strong> · {String(strongest.strength)}/100 · {tiedForStrongest} tied matches</p>
            : <p><strong>Strongest match</strong> · {String(strongest.instrument_symbol)}
              {' · '}{String(strongest.strategy_code)}{' · '}{String(strongest.strength)}/100</p>)}
          <p className="cfg-note">Strength is strategy-specific. Results are grouped by strategy and ordered by strength within each strategy.</p>
          <p className="cfg-note">Trigger window · {scan.request.entry_lookback} closed bar(s). Entry and strength come from the trigger bar. The plan uses a {scan.request.stop_atr}× ATR14 stop and a {scan.request.reward_risk}R target. </p>
          <StudioTable title="Ranked signals" onInspect={setQuickDetail} items={rankedSignals.map((row, index) => ({ ...row, rank: comparable ? index + 1 : undefined,
            strategy_version: `${row.strategy_code} v${row.version}`,
            signal_age: row.bars_since_trigger === 0 ? 'New entry' : row.bars_since_trigger != null
              ? `${row.bars_since_trigger} bar(s) ago` : '—',
            source: [row.account_code, row.broker_symbol].filter(Boolean).join(' · '),
            risk: [row.risk_per_contract, row.quote_currency].filter(Boolean).join(' ') }))}
            columns={[[ '#', 'rank' ], [ 'Instrument', 'instrument_symbol' ], [ 'Strategy', 'strategy_version' ],
              [ 'Side', 'side' ], [ 'Strength / 100', 'strength' ], [ 'Reference entry', 'entry' ],
              [ 'Stop loss', 'stop_loss' ], [ 'Take profit', 'take_profit' ], [ 'Reward / risk', 'reward_risk' ],
              [ 'Risk / contract', 'risk' ], [ 'Timeframe', 'timeframe' ], [ 'Signal age', 'signal_age' ], [ 'Trigger bar', 'trigger_bar_time' ],
              [ 'Data source', 'source' ], [ 'Reason', 'reason' ]]}
            empty={resultItems.length ? 'No qualifying entry for this scan. See Scan checks for the reason.' : 'No instruments or strategies were available for this scan.'} />
          {resultItems.length > rankedSignals.length && <details key={viewedRun + String(scan.completedAt.getTime())} open={rankedSignals.length === 0} className="studio-scan-settings">
            <summary>Scan checks · {resultItems.length - rankedSignals.length} without a signal</summary>
            <StudioTable title="Scan checks" onInspect={setQuickDetail} items={resultItems.filter(row => row.status !== 'SIGNAL')}
              columns={[[ 'Instrument', 'instrument_symbol' ], [ 'Strategy', 'strategy_code' ], [ 'Timeframe', 'timeframe' ],
                [ 'Status', 'status' ], [ 'Closed bar', 'bar_time' ], [ 'Reason', 'reason' ]]} empty="No scan checks." />
          </details>}
          {quickDetail && <SignalDetail row={quickDetail} close={() => setQuickDetail(null)} />}
        </>}
        {!scan && !scanBusy && !backgroundId && <p className="cfg-note">Run a new scan or choose a saved run below.</p>}
      </section>
      <BackgroundScans runId={backgroundId} selectRun={setBackgroundId} />
      {scan && !backgroundId && recordedSignals}
      <details className="studio-panel studio-details">
        <summary>Runtime and research</summary>
        <p className="cfg-note">{runtimeCount === null ? 'Runtime data has yet to load.' : runtimeCount + ' runtime records loaded.'}</p>
        <div className="studio-actions">
          <button onClick={() => goto('Strategies', 'runtime')}>Runtime</button>
          <button onClick={() => goto('Strategies', 'backtest')}>Backtest</button>
          <button onClick={() => goto('Strategies', 'performance')}>Performance</button>
          <button onClick={() => goto('Strategies', 'drift')}>Drift</button>
        </div>
      </details>
      <details className="studio-panel studio-details">
        <summary>Strategy lifecycle</summary>
        <div className="studio-actions">
          <button onClick={() => goto('Strategies', 'graphs')}>Calculation graph builder</button>
          <button onClick={() => goto('Configuration', 'strategies')}>Create strategy or version</button>
          <button onClick={() => goto('Configuration', 'deployments')}>Assign instruments and accounts</button>
        </div>
      </details>
    </div>}
  </section>;
}
