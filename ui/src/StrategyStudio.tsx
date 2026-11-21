import { useEffect, useMemo, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';

type Row = Record<string, any>;
type Mode = 'observe' | 'scan' | 'build' | 'results';
const modes: { id: Mode; title: string }[] = [
  { id: 'observe', title: 'Observe instrument' },
  { id: 'scan', title: 'Scan strategy' },
  { id: 'build', title: 'Build strategy' },
  { id: 'results', title: 'Results' },
];
function rows(value: unknown): Row[] {
  return value && typeof value === 'object' && Array.isArray((value as Row).items)
    ? (value as Row).items : [];
}
function label(row: Row): string { return String(row.symbol ?? row.instrument_symbol ?? row.code ?? 'Unknown'); }
function jump(view: 'Strategies' | 'Configuration' | 'Signals' | 'Risk', section: string) {
  goto(view, section);
}
export default function StrategyStudio() {
  const [mode, setMode] = useState<Mode>('observe');
  const [instruments, setInstruments] = useState<Row[]>([]);
  const [strategies, setStrategies] = useState<Row[]>([]);
  const [deployments, setDeployments] = useState<Row[]>([]);
  const [signals, setSignals] = useState<Row[]>([]);
  const [runtime, setRuntime] = useState<Row[]>([]);
  const [scan, setScan] = useState<Row[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState('');
  const [instrument, setInstrument] = useState('');
  const [strategy, setStrategy] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState('');
  const [draft, setDraft] = useState(JSON.stringify({
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
      { source: 'fast', source_port: 'value', target: 'bear', target_port: 'right' },
      { source: 'slow', source_port: 'value', target: 'bear', target_port: 'left' },
      { source: 'bull', source_port: 'result', target: 'signal', target_port: 'long' },
      { source: 'bear', source_port: 'result', target: 'signal', target_port: 'short' }
    ]
  }, null, 2));
  const [validation, setValidation] = useState('');
  const [previewBars, setPreviewBars] = useState('');
  const [previewing, setPreviewing] = useState(false);
  const previewDraft = async () => {
    setPreviewing(true); setValidation('');
    try {
      const workflow = JSON.parse(draft);
      const bars = JSON.parse(previewBars);
      const result = await api('trading/studio/builder/preview', 'POST', { workflow, bars });
      setValidation(JSON.stringify(result, null, 2));
    } catch (e) { setValidation(e instanceof Error ? e.message : String(e)); }
    finally { setPreviewing(false); }
  };
  const [validating, setValidating] = useState(false);
  const validateDraft = async () => {
    setValidating(true); setValidation('');
    try {
      const payload = JSON.parse(draft);
      const result = await api('trading/studio/builder/validate', 'POST', payload);
      setValidation(JSON.stringify(result, null, 2));
    } catch (e) { setValidation(e instanceof Error ? e.message : String(e)); }
    finally { setValidating(false); }
  };
  const refresh = async () => {
    setBusy(true); setError('');
    const endpoints = ['config/instruments', 'config/strategies', 'config/deployments', 'trading/signals?limit=500', 'trading/runtime'];
    const results = await Promise.allSettled(endpoints.map(path => api(path)));
    const setters = [setInstruments, setStrategies, setDeployments, setSignals, setRuntime];
    const failures: string[] = [];
    results.forEach((result, index) => {
      if (result.status === 'fulfilled') setters[index](rows(result.value));
      else { setters[index]([]); failures.push(endpoints[index] + ': ' + String(result.reason instanceof Error ? result.reason.message : result.reason)); }
    });
    setError(failures.join(' | '));
    setUpdated(new Date().toLocaleTimeString());
    setBusy(false);
  };
  useEffect(() => { void refresh(); }, []);
  const selectedInstrument = instrument || label(instruments[0] ?? {});
  const selectedStrategy = strategy || String(strategies[0]?.code ?? '');
  const matching = useMemo(() => deployments.filter(d => mode === 'observe'
    ? String(d.instrument_symbol ?? d.symbol ?? '') === selectedInstrument
    : String(d.strategy_code ?? '') === selectedStrategy), [deployments, mode, selectedInstrument, selectedStrategy]);
  const runScan = async () => {
    setScanning(true); setScanError(''); setScan([]);
    const parameter = mode === 'observe' ? 'instrument_symbol=' + encodeURIComponent(selectedInstrument) : 'strategy_code=' + encodeURIComponent(selectedStrategy);
    try {
      const result = await api('trading/studio/scan?' + parameter);
      setScan(rows(result));
    } catch (e) { setScanError(e instanceof Error ? e.message : String(e)); }
    finally { setScanning(false); }
  };
  useEffect(() => { setScan([]); setScanError(''); }, [mode, selectedInstrument, selectedStrategy]);
  const visibleSignals = signals.filter(s => mode === 'observe'
    ? String(s.symbol ?? s.instrument_symbol ?? '') === selectedInstrument
    : String(s.strategy_code ?? '') === selectedStrategy);
  return <section className="intel" aria-label="Strategy Intelligence Studio">
    <div className="intel-bar" style={{justifyContent:'space-between'}}>
      <div><h2 style={{margin:0}}>Strategy Intelligence Studio</h2><small className="cfg-note">Input → Evaluate → Signal → Risk → Execution. Existing strategy intelligence remains unchanged.</small></div>
      <button disabled={busy} onClick={() => void refresh()}>{busy ? 'Loading…' : 'Refresh'}</button>
    </div>
    <div className="intel-bar" role="tablist" aria-label="Studio mode">
      {modes.map(m => <button key={m.id} role="tab" aria-selected={mode===m.id} className={mode===m.id?'primary':''} onClick={()=>setMode(m.id)}>{m.title}</button>)}
    </div>
    {error && <p role="alert" className="cfg-error-text">Some data could not be loaded: {error}</p>}
    {updated && <p className="cfg-note">Last refreshed {updated}. Results reflect stored platform data, not a fresh market scan. No orders are submitted from this workspace.</p>}
    {(mode === 'observe' || mode === 'scan') && <>
      <div className="intel-bar">
        {mode === 'observe' ? <label>Instrument <select aria-label="Instrument" value={selectedInstrument} onChange={e=>setInstrument(e.target.value)}>
          {instruments.map(i=><option key={String(i.id ?? label(i))} value={label(i)}>{label(i)}</option>)}
        </select></label> : <label>Strategy <select aria-label="Strategy" value={selectedStrategy} onChange={e=>setStrategy(e.target.value)}>
          {strategies.map(s=><option key={String(s.id ?? s.code)} value={String(s.code)}>{String(s.name ?? s.code)}</option>)}
        </select></label>}
        <button onClick={()=>jump('Configuration','deployments')}>Configure deployments</button>
      </div>
      <h3>Process · Read-only closed-bar scan</h3>
      <div className="intel-bar"><button className="primary" disabled={scanning || !(mode === 'observe' ? selectedInstrument !== 'Unknown' : selectedStrategy)} onClick={()=>void runScan()}>{scanning?'Evaluating…':'Evaluate qualifying deployments'}</button><small className="cfg-note">Uses the same algorithm as runtime and backtest; does not send orders.</small></div>
      {scanError && <p role="alert" className="cfg-error-text">{scanError}</p>}
      <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Instrument</th><th>Strategy</th><th>Target</th><th>Assessment</th><th>Closed bar</th><th>Reference close</th><th>Reason</th></tr></thead><tbody>
        {scan.map((r,i)=><tr key={String(r.deployment_id??i)}><td>{String(r.instrument_symbol)}</td><td>{String(r.strategy_code)}</td><td>{String(r.target??'—')}</td><td>{String(r.status)}</td><td>{String(r.bar_time??'—')}</td><td>{String(r.reference_close??'—')}</td><td>{String(r.reason??'—')}</td></tr>)}
        {!scan.length && <tr><td colSpan={7}>Run the scan to evaluate existing active deployments. No result is a broker-ready trade plan.</td></tr>}
      </tbody></table></div>
      <h3>Input · Existing deployment coverage</h3>
      <p className="cfg-note">{matching.length} matching deployments. An instrument or strategy without a deployment is not automatically evaluated. This scan evaluates active deployments only; undeployed instruments remain available for future universe-wide discovery.</p>
      <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Strategy</th><th>Instrument</th><th>Account</th><th>Status</th><th>Mode</th></tr></thead><tbody>
        {matching.map((d,i)=><tr key={String(d.id ?? i)}><td>{String(d.strategy_code ?? '—')}</td><td>{String(d.instrument_symbol ?? d.symbol ?? '—')}</td><td>{String(d.account_code ?? '—')}</td><td>{String(d.status ?? '—')}</td><td>{String(d.mode ?? '—')}</td></tr>)}
        {!matching.length && <tr><td colSpan={5}>No matching deployments. Configure a strategy version and its instrument/account coverage.</td></tr>}
      </tbody></table></div>
      <h3>Output · Recorded strategy signals</h3>
      <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>Time</th><th>Strategy</th><th>Instrument</th><th>Side</th><th>State</th><th>Reason</th></tr></thead><tbody>
        {visibleSignals.slice(0,100).map((s,i)=><tr key={String(s.id ?? i)}><td>{String(s.bar_time ?? '—')}</td><td>{String(s.strategy_code ?? '—')}</td><td>{String(s.symbol ?? '—')}</td><td>{String(s.side ?? '—')}</td><td>{String(s.state ?? '—')}</td><td>{Array.isArray(s.reasons)?s.reasons.join('; '):'—'}</td></tr>)}
        {!visibleSignals.length && <tr><td colSpan={6}>No recorded signals for this selection. WAIT is not a trade signal.</td></tr>}
      </tbody></table></div>
      <p className="cfg-note">Entry, stop and targets require an actual validated trade plan; they are never invented from a directional signal. Inspect the risk and execution stages for broker-valid prices.</p>
      <div className="intel-bar"><button onClick={()=>jump('Strategies','signals')}>Signal details</button><button onClick={()=>jump('Risk','decisions')}>Risk decisions</button></div>
    </>}
    {mode === 'build' && <>
      <h3>Build · Universal workflow draft</h3>
      <p className="cfg-note">Portable JSON strategy graph. This editor validates node types, required ports, parameters and cycles. It does not save, backtest, deploy or execute the graph. Existing visual graph authoring remains available below.</p>
      <label htmlFor="studio-workflow-json">Strategy workflow JSON</label>
      <textarea id="studio-workflow-json" aria-label="Strategy workflow JSON" spellCheck={false}
        value={draft} onChange={e=>{setDraft(e.target.value);setValidation('');}}
        rows={16} style={{width:'100%',fontFamily:'monospace',fontSize:'12px'}} />
      <div className="intel-bar"><button className="primary" disabled={validating}
        onClick={()=>void validateDraft()}>{validating?'Validating…':'Validate draft'}</button>
        <button onClick={()=>void api('trading/studio/builder/nodes').then(r=>setValidation(JSON.stringify(r,null,2))).catch(e=>setValidation(String(e)))}>
          Show node catalog</button></div>
      <details><summary>Historical preview (closed OHLC bars)</summary>
        <p className="cfg-note">Paste a JSON array of 2–5000 historical closed bars, each with open, high, low and close. No orders are submitted. Indicator warm-up requires sufficient history.</p>
        <textarea aria-label="Historical OHLC bars JSON" rows={7} value={previewBars}
          onChange={e=>setPreviewBars(e.target.value)}
          placeholder={'[{"open":100,"high":102,"low":99,"close":101}, ...]'}
          style={{width:'100%',fontFamily:'monospace',fontSize:'12px'}} />
        <button disabled={previewing || !previewBars.trim()} onClick={()=>void previewDraft()}>
          {previewing?'Previewing…':'Preview closed bars'}</button>
      </details>
      {validation && <pre role="status" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:300,overflowY:'auto'}}>{validation}</pre>}
      <h3>Existing strategy lifecycle</h3>
      <p>Choose an existing algorithm or visual calculation graph, create a version, validate it, approve it, then assign instruments and accounts through deployments. Drafts do not trade.</p>
      <div className="intel-bar"><button onClick={()=>jump('Strategies','graphs')}>Open visual node builder</button><button onClick={()=>jump('Configuration','strategies')}>Create strategy/version</button><button onClick={()=>jump('Configuration','deployments')}>Assign instruments</button></div>
      <ol><li>Input: collected bars and indicators.</li><li>Process: rules, filters, graph and signal checks.</li><li>Output: qualified signal, risk decision and controlled execution.</li></ol>
      <p className="cfg-note">The existing calculation graph editor supports node-based authoring. Approval, runtime and broker permissions remain enforced by their original services.</p>
    </>}
    {mode === 'results' && <>
      <h3>Results · Runtime and research</h3>
      <p>{runtime.length} runtime records loaded. View performance, backtests and drift in the existing strategy workspace.</p>
      <div className="intel-bar"><button onClick={()=>jump('Strategies','runtime')}>Runtime</button><button onClick={()=>jump('Strategies','backtest')}>Backtest</button><button onClick={()=>jump('Strategies','performance')}>Performance</button><button onClick={()=>jump('Strategies','drift')}>Drift</button></div>
    </>}
  </section>;
}
