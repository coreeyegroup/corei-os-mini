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
  window.dispatchEvent(new CustomEvent('corei-goto', { detail: { view } }));
}
export default function StrategyStudio() {
  const [mode, setMode] = useState<Mode>('observe');
  const [instruments, setInstruments] = useState<Row[]>([]);
  const [strategies, setStrategies] = useState<Row[]>([]);
  const [deployments, setDeployments] = useState<Row[]>([]);
  const [signals, setSignals] = useState<Row[]>([]);
  const [runtime, setRuntime] = useState<Row[]>([]);
  const [instrument, setInstrument] = useState('');
  const [strategy, setStrategy] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [updated, setUpdated] = useState('');
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
      <h3>Input · Existing deployment coverage</h3>
      <p className="cfg-note">{matching.length} matching deployments. An instrument or strategy without a deployment is not automatically evaluated. Strategy-first discovery across undeployed instruments requires a dedicated scanner backend.</p>
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
      <h3>Build · Reuse the approved strategy lifecycle</h3>
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
