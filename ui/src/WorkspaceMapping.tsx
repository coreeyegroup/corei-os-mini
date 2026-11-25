import { useState } from 'react';
import { api } from './Configuration';
import { goto, openDeployment } from './nav';
import { Msg, useData, when, type Row } from './StageShell';
import type { ScanConfig } from './WorkspaceControl';

const pretty = (v: unknown) => v == null ? '—' : typeof v === 'object'
  ? JSON.stringify(v) : String(v);
const book = (d: Row) => d.mode === 'PAPER' ? 'PAPER' : d.environment ?? 'Unknown broker environment';
const PAGE = 25;

export default function WorkspaceMapping({config,accounts,selectedDeployment,disabled,onAccounts,onInspect}: {
  config?: ScanConfig | null; accounts: string[]; selectedDeployment: string | null; disabled: boolean;
  onAccounts: (ids:string[])=>void; onInspect: (d:Row)=>void;
}) {
  const configured = !!config?.instrument_symbols.length && !!config?.strategy_codes.length;
  const [registry, registryError, reloadRegistry] = useData<Row>('trading/workspaces/catalogue',0);
  const [deployments, deploymentError, reloadDeployments] = useData<Row>(configured?'config/deployments':null,0);
  const [sources, sourceError, reloadSources] = useData<Row>(configured?'trading/sources':null,0);
  const [policies, policyError, reloadPolicies] = useData<Row>(configured?'trading/execution/policies':null,0);
  const [limits, limitError, reloadLimits] = useData<Row>(configured?'trading/risk/limits':null,0);
  const [kills, killError, reloadKills] = useData<Row>(configured?'trading/risk/kill-switches':null,0);
  const [monitor, monitorError, reloadMonitor] = useData<Row>(configured?'trading/strategies/monitor':null,0);
  const [operator, operatorError, reloadOperator] = useData<Row>('operator/session',0);
  const [query,setQuery] = useState(''), [page,setPage] = useState(0);
  const [busy,setBusy] = useState(''), [error,setError] = useState(''), [notice,setNotice] = useState('');
  const scope = new Set(config?.instrument_symbols ?? []), strategies = new Set(config?.strategy_codes ?? []);
  const selectedAccounts = new Set(accounts);
  const allAccounts: Row[] = registry?.accounts ?? [];
  const all: Row[] = deployments?.items ?? [];
  const scoped = all.filter(d=>scope.has(d.instrument_symbol) && strategies.has(d.strategy_code) &&
    (!accounts.length || selectedAccounts.has(d.account_id)));
  const filtered = scoped.filter(d=>[d.instrument_symbol,d.strategy_code,d.account_code,d.status,book(d)].join(' ').toLowerCase().includes(query.toLowerCase()));
  const max = Math.max(0,Math.ceil(filtered.length/PAGE)-1), currentPage = Math.min(page,max);
  const live = new Map<string,Row>((monitor?.items ?? []).map((d:Row)=>[d.id,d]));
  const byPolicy = new Map<string,Row>((policies?.items ?? []).map((d:Row)=>[d.id,d.policy]));
  const sourceRows: Row[] = (sources?.items ?? []).filter((s:Row)=>scope.has(s.instrument));
  const roles: string[] = operator?.roles ?? [];
  const canRisk = roles.includes('ADMIN') || roles.includes('RISK_APPROVER');
  const canPause = canRisk || roles.includes('TRADING_OPS');
  const complete = !!operator && !operatorError && !!deployments && !deploymentError && !!policies && !policyError && !!limits && !limitError && !!kills && !killError;
  const refresh = () => {reloadRegistry();reloadDeployments();reloadSources();reloadPolicies();reloadLimits();reloadKills();reloadMonitor();reloadOperator();};
  const relevant = (rows:Row[],d:Row) => rows.filter(x=>x.scope==='FIRM' ||
    (x.scope==='ACCOUNT' && x.target_id===d.account_id) || (x.scope==='STRATEGY' && x.target_id===d.strategy_id) ||
    (x.scope==='INSTRUMENT' && x.target_id===d.instrument_id) || (x.scope==='DEPLOYMENT' && x.target_id===d.id));
  function inspect(d:Row) {
    const m=live.get(d.id);
    // Do not guess a broker account from a symbol if several feeds share the same symbol.
    const candidates=sourceRows.filter(s=>s.instrument===d.instrument_symbol && s.broker_symbol===d.data_symbol &&
      allAccounts.some(a=>a.id===s.account_id && a.connection_code && a.connection_code===d.data_connection_code));
    onInspect({...d,actual_timeframe:m?.timeframe ?? m?.parameters?.timeframe,
      observed_source:candidates.length===1?candidates[0]:null});
  }
  async function transition(d:Row,action:'activate'|'pause') {
    if(busy || disabled || !complete || (action==='activate' ? !canRisk : !canPause)) return;
    const label=d.strategy_code+' · '+d.instrument_symbol+' · '+d.account_code+' · '+book(d);
    const reason=window.prompt((action==='activate'?'Activate':'Pause')+' existing shared deployment '+label+'? Reason (audited):')?.trim();
    if(!reason || reason.length<5) return;
    if(!window.confirm('Confirm '+action+' for '+label+'. This changes the existing deployment everywhere it is used, not the workspace scan or global runtime.'))return;
    setBusy(d.id);setError('');setNotice('');
    try {await api('config/deployments/'+encodeURIComponent(d.id)+'/'+action,'POST',{version:d.version,reason});setNotice('Existing deployment updated: '+label);refresh();}
    catch(e){setError(e instanceof Error?e.message:String(e));refresh();}
    finally{setBusy('');}
  }
  return <section className="mw-mapping" aria-label="Mapped existing capabilities">
    <header className="mw-header"><div><strong>Existing engine mapping</strong> <small className="cfg-note">Instrument × strategy × account</small></div>
      <button onClick={refresh}>Refresh mappings</button></header>
    <div className="mw-map-inputs">
      <details><summary>Input feeds · {sourceRows.length} mapped collection records</summary>
        <p className="cfg-note">These are the existing collected sources for the selected universe. The research scanner still chooses automatically; a deployment uses its configured mapping.</p>
        {sourceRows.slice(0,100).map((s,i)=><p key={s.account_id+':'+s.broker_symbol+':'+i}>{s.instrument} · {s.account_code} · {s.broker_symbol} · last bar {when(s.last_bar)}</p>)}
        {sourceRows.length>100&&<p>First 100 shown. Full source list in Market data.</p>}
        {!sourceRows.length&&<p>No collected source for the selected instruments.</p>}
      </details>
      <details><summary>Execution account filter · {accounts.length?accounts.length+' selected':'All accounts'}</summary>
        <p className="cfg-note">Filters the existing deployment roster only; does not allocate or enable trading accounts.</p>
        {allAccounts.map(a=><label key={a.id} className="mw-account-choice"><input type="checkbox" disabled={disabled} checked={selectedAccounts.has(a.id)}
          onChange={e=>{setPage(0);onAccounts(e.target.checked?[...accounts,a.id]:accounts.filter(x=>x!==a.id));}}/> {a.code} · {a.environment}</label>)}
        <button disabled={disabled||!accounts.length} onClick={()=>{setPage(0);onAccounts([]);}}>Show all accounts</button>
        {accounts.some(id=>!allAccounts.some(a=>a.id===id))&&<p className="cfg-error">A saved account is unavailable. Clear the filter or review the account registry.</p>}
      </details>
    </div>
    <Msg error={error||registryError||deploymentError||sourceError||policyError||limitError||killError||monitorError||operatorError} notice={notice}/>
    {!configured?<p className="cfg-note">Select instruments and strategies above to map the existing deployments. No trading changes are made by selecting them.</p>:
      !deployments&&!deploymentError?<p role="status">Loading existing deployment mappings…</p>:<>
      <div className="mw-actions"><strong>{scoped.length} mapped deployments</strong><small>{scoped.filter(d=>d.status==='ACTIVE').length} active · {scoped.filter(d=>d.status==='PAUSED').length} paused</small>
        <input aria-label="Search mapped deployments" placeholder="Instrument, strategy, account…" value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}}/>
        <button onClick={()=>goto('Configuration','deployments')}>Existing deployment setup ↗</button></div>
      {all.length>=2000&&<p className="cfg-error">The existing deployment API returns at most 2,000 records. This roster may be incomplete; use the existing setup filters for additional records.</p>}
      {!scoped.length&&deployments&&<p>No existing deployment matches the selected instrument, strategy and account scope. Research remains available. Use existing deployment setup to configure one; nothing is created automatically.</p>}
      {filtered.slice(currentPage*PAGE,(currentPage+1)*PAGE).map(d=>{
        const m=live.get(d.id), policy=byPolicy.get(d.id), applicable=relevant(limits?.items??[],d).filter(x=>x.enabled!==false),
          stopped=relevant(kills?.items??[],d).filter(x=>x.engaged!==false);
        const blockers: string[]=d.blockers??[];
        return <article className="mw-mapped-row" key={d.id}>
          <div className="mw-header"><strong>{d.strategy_code} · {d.instrument_symbol} · {d.account_code}</strong>
            <small>{book(d)} · {d.status} · v{d.version_number??'—'} · {m?.timeframe??m?.parameters?.timeframe??'—'}</small></div>
          <div className="mw-mapped-fields">
            <span>Input <b>{d.data_connection_code??'—'} · {d.data_symbol??'—'}</b></span>
            <span>Processing <b>{d.algorithm??'—'}</b></span>
            <span>Output <b>{policy?.handoff??d.execution?.handoff??'—'} · {policy?.order_type??d.execution?.order_type??'—'}</b></span>
            <span>Risk <b>{limitError||killError||!limits||!kills?'Unavailable':stopped.length?stopped.length+' kill switch(es)':applicable.length+' configured limits'}</b></span>
            <span>Position <b>{pretty(m?.position?.quantity)} · P&L {pretty(m?.position?.unrealised)} {m?.position?.currency??''}</b></span>
            <span>Latest decision <b>{m?.last_decision?.outcome??'—'} · {m?.pending??'—'} pending</b></span>
          </div>
          {!!blockers.length&&<p className="cfg-error">{blockers.join(' · ')}</p>}
          {m?.last_error&&<p className="cfg-error">{m.last_error}</p>}
          <details><summary>Inherited configuration and risk</summary>
            <p>Trading symbol: {d.trading_symbol??'—'} · Allocation: {pretty(d.allocation_weight_bps)} bps · Sizing: {pretty(d.sizing)}</p>
            <p>Protection: {pretty(d.protect)} · Execution policy: {pretty(policy??d.execution)} · Last evaluated: {when(m?.last_bar_time)}</p>
            <p>Strategy parameters: {pretty(m?.parameters)} · Drift: {m?.drift?.status??'—'}</p>
            {applicable.map(x=><p key={x.id}>{x.scope} · {x.kind}: {pretty(x.value)}</p>)}
            {stopped.map(x=><p key={x.id} className="cfg-error">{x.scope} · Kill switch: {x.reason}</p>)}
            <p className="cfg-note">These are inherited configured controls, not a pre-trade approval. Existing risk and order checks still apply.</p>
          </details>
          <div className="mw-actions"><button disabled={disabled} onClick={()=>inspect(d)}>Inspect mapped deployment</button>
            <button onClick={()=>openDeployment('runtime',{id:d.id,label:d.strategy_code})}>Runtime evidence ↗</button>
            <button onClick={()=>openDeployment('queue',{id:d.id,label:d.strategy_code})}>Decision queue ↗</button>
            {selectedDeployment===d.id&&<small>Focused below</small>}
            {d.status==='ACTIVE'&&canPause&&<button disabled={disabled||!!busy||!complete} onClick={()=>void transition(d,'pause')}>Pause existing deployment</button>}
            {['PAUSED','PROPOSED'].includes(d.status)&&canRisk&&<button disabled={disabled||!!busy||!complete||!!stopped.length||blockers.some(b=>b!=='DEPLOYMENT_NOT_ACTIVE')} onClick={()=>void transition(d,'activate')}>Activate existing deployment</button>}
          </div>
        </article>;
      })}
      {scoped.length>0&&!filtered.length&&<p>No mapped deployments match the search.</p>}
      {filtered.length>0&&<div className="mw-actions"><button disabled={!currentPage} onClick={()=>setPage(currentPage-1)}>Previous mappings</button>
        <small>{currentPage*PAGE+1}–{Math.min((currentPage+1)*PAGE,filtered.length)} of {filtered.length}</small>
        <button disabled={currentPage>=max} onClick={()=>setPage(currentPage+1)}>Next mappings</button></div>}
      <p className="cfg-note">Mapped controls are shared deployment controls, not workspace-owned trading. Only an explicit, confirmed deployment action changes its existing runtime status. Workspace scan Start/Stop never changes deployments.</p>
    </>}
  </section>;
}
