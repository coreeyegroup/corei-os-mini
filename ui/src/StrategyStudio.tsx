import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import { goto, publishSections, useSection } from './nav';
import UniversalCanvas, { readWorkflow, type NodeSpec } from './UniversalCanvas';
import StudioDrafts, { type DraftVersion } from './StudioDrafts';
import './strategy-studio.css';

type Row = Record<string, unknown>;
type Mode = 'observe' | 'scan' | 'build' | 'results';
const researchSteps = [
  { id: 'observe', title: 'Observe instrument' },
  { id: 'scan', title: 'Scan strategy' },
  { id: 'results', title: 'Results' },
] as const;
type ResearchStep = typeof researchSteps[number]['id'];
type ScanScope = 'instrument' | 'strategy';
type ScanRun = { scope: ScanScope; selection: string; items: Row[]; completedAt: Date };
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
  ...researchSteps.map(({ id, title }, index) => ({ id, label: `${index + 1}. ${title}`, group: 'Research & results' })),
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

function StudioTable({ title, columns, items, empty }: {
  title: string; columns: [string, string][]; items: Row[]; empty: string;
}) {
  return <div className="cfg-scroll studio-table" tabIndex={0} role="region" aria-label={title}>
    <table className="cfg-table compact">
      <thead><tr>{columns.map(([label]) => <th key={label} scope="col">{label}</th>)}</tr></thead>
      <tbody>
        {items.map((row, index) => <tr key={String(row.id ?? row.deployment_id ?? index)}>
          {columns.map(([label, key]) => <td key={label}>
            {Array.isArray(row[key]) ? row[key].join(' · ') : String(row[key] ?? '—')}
          </td>)}
        </tr>)}
        {!items.length && <tr><td colSpan={columns.length}>{empty}</td></tr>}
      </tbody>
    </table>
  </div>;
}

export default function StrategyStudio() {
  const [section, setSection] = useSection('Strategy Studio', 'templates', studioSections);
  const lastEditor = useRef<EditorView>('templates');
  const lastResearch = useRef<ResearchStep>('observe');
  const currentSection = useRef(section);
  const researchHeading = useRef<HTMLHeadingElement>(null);
  const focusResearchHeading = useRef(false);
  const mode: Mode = section === 'observe' || section === 'scan' || section === 'results' ? section : 'build';
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
  const [strategy, setStrategy] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [refreshError, setRefreshError] = useState('');
  const [updated, setUpdated] = useState('');
  const [scanBusy, setScanBusy] = useState(false);
  const [scanScope, setScanScope] = useState<ScanScope>('strategy');
  const [scan, setScan] = useState<ScanRun | null>(null);
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

  async function refresh() {
    setRefreshing(true);
    setRefreshError('');
    const endpoints = [
      ['Instruments', 'config/instruments', setInstruments],
      ['Strategies', 'config/strategies', setStrategies],
      ['Deployments', 'config/deployments', setDeployments],
      ['Market context', 'intel/screener?timeframe=H1', setMarketSnapshots],
      ['Signals', 'trading/signals?limit=500', setSignals],
      ['Runtime', 'trading/runtime', (items: Row[]) => setRuntimeCount(items.length)],
    ] as const;
    const results = await Promise.allSettled(endpoints.map(([, path]) => api(path)));
    const failures: string[] = [];
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
    else lastResearch.current = mode;
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
  const selectedInstrument = instruments.some(item => instrumentLabel(item) === instrument)
    ? instrument : instrumentLabel(instruments[0] ?? {});
  const selectedStrategy = strategies.some(item => item.code === strategy)
    ? strategy : String(strategies[0]?.code ?? '');
  const scope = observing ? 'instrument' : scanScope;
  const byInstrument = scope === 'instrument';
  const selection = byInstrument ? selectedInstrument : selectedStrategy;
  const matching = selection ? deployments.filter(row => byInstrument
    ? (row.instrument_symbol ?? row.symbol) === selection : row.strategy_code === selection) : [];
  const observedSnapshots = observing && selectedInstrument ? marketSnapshots.filter(row => row.instrument === selectedInstrument) : [];
  const signalScope = observing ? { scope: 'instrument', selection: selectedInstrument } : scan;
  const visibleSignals = signalScope?.selection ? signals.filter(row => signalScope.scope === 'instrument'
    ? (row.symbol ?? row.instrument_symbol) === signalScope.selection : row.strategy_code === signalScope.selection) : [];
  const currentScanError = scanError?.scope === scanScope && scanError.selection === selection ? scanError.message : '';
  const scanCounts = scan && [
    ['Evaluated', scan.items.length],
    ['Qualified', scan.items.filter(item => item.status === 'QUALIFIED').length],
    ['Waiting', scan.items.filter(item => item.status === 'WAIT').length],
    ['Errors', scan.items.filter(item => item.status === 'ERROR').length],
  ] as const;

  function advanceResearch(next: ResearchStep) {
    focusResearchHeading.current = true;
    setSection(next);
  }

  function openScan() {
    setScanScope(selectedInstrument ? 'instrument' : 'strategy');
    advanceResearch('scan');
  }

  async function runScan() {
    if (!selection || scanBusy) return;
    setScanBusy(true);
    setScanError(null);
    const parameter = byInstrument ? 'instrument_symbol' : 'strategy_code';
    try {
      const result = await api('trading/studio/scan?' + new URLSearchParams({ [parameter]: selection }));
      setScan({ scope: scanScope, selection, items: rows(result), completedAt: new Date() });
      if (currentSection.current === 'scan') advanceResearch('results');
    } catch (error) {
      setScanError({ scope: scanScope, selection, message: message(error) });
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
        onClick={() => setSection(lastResearch.current)}>Research &amp; results</button>
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

    {mode !== 'build' && <nav className="studio-steps studio-research-steps" aria-label="Strategy research steps">
      {researchSteps.map(({ id, title }, index) => <button key={id} type="button"
        aria-current={mode === id ? 'step' : undefined} onClick={() => setSection(id)}>
        <span className="studio-step-number" aria-hidden="true">{index + 1}</span>
        <span className="studio-step-label"><strong>{title}</strong></span>
      </button>)}
    </nav>}
    {mode !== 'build' && refreshError && <p role="alert" className="cfg-error-text">Data refresh failed for {refreshError}</p>}

    {(observing || mode === 'scan') && <div className="studio-stack">
      <section className="studio-panel" aria-labelledby="studio-research-scope">
        <div className="studio-section-title">
          <div><h3 id="studio-research-scope" ref={researchHeading} tabIndex={-1}>{observing ? 'Select an instrument' : 'Choose what to scan'}</h3>
            <p className="cfg-note">{observing ? 'Review the market context and the strategies assigned to this instrument.'
              : 'Evaluate active deployments using the latest stored closed bars.'}</p></div>
        </div>
        <div className="studio-form-row">
          {!observing && <label>Scan by
            <select value={scanScope} disabled={scanBusy} onChange={event => setScanScope(event.target.value as ScanScope)}>
              <option value="strategy">Strategy across instruments</option>
              <option value="instrument">Instrument across strategies</option>
            </select>
          </label>}
          {byInstrument ? <label>Instrument
            <select value={selectedInstrument} disabled={scanBusy} onChange={event => setInstrument(event.target.value)}>
              {!instruments.length && <option value="">{refreshing ? 'Loading instruments…' : 'No instruments'}</option>}
              {instruments.map(item => <option key={String(item.id ?? instrumentLabel(item))} value={instrumentLabel(item)}>
                {instrumentLabel(item)}
              </option>)}
            </select>
          </label> : <label>Strategy
            <select value={selectedStrategy} disabled={scanBusy} onChange={event => setStrategy(event.target.value)}>
              {!strategies.length && <option value="">{refreshing ? 'Loading strategies…' : 'No strategies'}</option>}
              {strategies.map(item => <option key={String(item.id ?? item.code)} value={String(item.code)}>
                {String(item.name ?? item.code)}
              </option>)}
            </select>
          </label>}
          <div className="studio-actions">
            {observing
              ? <button className="primary" disabled={scanBusy || (refreshing && !updated)} onClick={openScan}>Next: scan strategy</button>
              : <button className="primary" disabled={scanBusy || !selection} onClick={() => void runScan()}>
                {scanBusy ? 'Scanning…' : currentScanError ? 'Retry scan' : 'Run scan'}
              </button>}
          </div>
        </div>
        {!observing && scanBusy && <p role="status" className="cfg-note">Evaluating active deployments for {selection}…</p>}
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
      <section className="studio-panel">
        <div className="studio-section-title">
          <h3>Deployment coverage</h3>
          <button onClick={() => goto('Configuration', 'deployments')}>Configure deployments</button>
        </div>
        <StudioTable title="Deployment coverage" items={matching.map(row => ({ ...row, instrument: row.instrument_symbol ?? row.symbol }))}
          columns={[[ 'Strategy', 'strategy_code' ], [ 'Instrument', 'instrument' ], [ 'Account', 'account_code' ],
            [ 'Status', 'status' ], [ 'Mode', 'mode' ]]}
          empty={refreshing ? 'Loading deployments…' : !selection ? 'Select a scope to see deployments.' : 'No matching deployments.'} />
      </section>
      {observing && recordedSignals}
    </div>}

    {mode === 'results' && <div className="studio-stack">
      <section className="studio-panel" aria-labelledby="studio-assessment">
        <div className="studio-section-title">
          <div>
            <h3 id="studio-assessment" ref={researchHeading} tabIndex={-1}>Closed-bar assessment</h3>
            {scan && <p className="cfg-note">{scan.scope === 'instrument' ? 'Instrument' : 'Strategy'} · {scan.selection}
              {' · '}{scan.completedAt.toLocaleString()}</p>}
          </div>
          <button className={scan ? undefined : 'primary'} onClick={() => advanceResearch('scan')}>
            {scan ? 'Back to scan' : 'Go to scan'}
          </button>
        </div>
        {scanBusy && <p role="status" className="cfg-note">{scan ? 'New scan running. Showing the last completed scan.' : 'Scan in progress…'}</p>}
        {scan && <>
          <dl className="studio-scan-summary">
            {scanCounts?.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
          </dl>
          <p className="cfg-note">Read-only assessment. A position target can reflect an existing setup. Check the reason for each deployment.</p>
          <StudioTable title="Closed-bar assessment" items={scan.items}
            columns={[[ 'Instrument', 'instrument_symbol' ], [ 'Strategy', 'strategy_code' ], [ 'Account', 'account_code' ],
              [ 'Timeframe', 'timeframe' ], [ 'Position target', 'target' ], [ 'Assessment', 'status' ],
              [ 'Closed bar', 'bar_time' ], [ 'Reference close', 'reference_close' ], [ 'Reason', 'reason' ]]}
            empty="No active deployments matched this scan." />
        </>}
        {!scan && !scanBusy && <p className="cfg-note">Run a scan to see deployment assessments here.</p>}
      </section>
      {scan && recordedSignals}
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
