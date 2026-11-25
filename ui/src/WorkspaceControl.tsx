import { useEffect, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import { Msg, useData, when, type Row } from './StageShell';

export type ScanConfig = { instrument_symbols: string[]; strategy_codes: string[]; grouping: 'instrument' | 'strategy';
  entry_lookback: number; stop_atr: string; reward_risk: string };
const defaults: ScanConfig = { instrument_symbols: [], strategy_codes: [], grouping: 'instrument', entry_lookback: 1, stop_atr: '1.5', reward_risk: '2' };

function Picker({ label, items, selected, code, change, disabled }: {
  label: string; items: Row[]; selected: string[]; code: string; change: (v: string[]) => void; disabled: boolean;
}) {
  const [query,setQuery]=useState(''), [page,setPage]=useState(0);
  const list=items.filter(i=>(String(i[code])+' '+(i.name??'')).toLowerCase().includes(query.toLowerCase()));
  const keys=new Set(selected), max=Math.max(0,Math.ceil(list.length/40)-1), offset=Math.min(page,max)*40;
  return <details className="mw-picker"><summary>{label} · {selected.length} selected / {items.length}</summary>
    <input aria-label={'Search '+label} value={query} onChange={e=>{setQuery(e.target.value);setPage(0);}} />
    <div className="mw-actions">
      <button disabled={disabled||!list.length} onClick={()=>change([...new Set([...selected,...list.map(i=>String(i[code]))])])}>Select matching</button>
      <button disabled={disabled||!selected.length} onClick={()=>change([])}>Clear selection</button>
    </div>
    <div className="mw-picker-list">{list.slice(offset,offset+40).map(i=>{
      const k=String(i[code]);
      return <label key={k}><input type="checkbox" checked={keys.has(k)} disabled={disabled}
        onChange={e=>change(e.target.checked?[...selected,k]:selected.filter(x=>x!==k))}/>{k}{i.name?' · '+i.name:''}{i.has_source===false?' · No mapped data':''}</label>;
    })}{!list.length&&<p>No matching registered items.</p>}</div>
    <div className="mw-actions"><button disabled={!offset} onClick={()=>setPage(Math.max(0,Math.min(page,max)-1))}>Previous</button>
      <small>{list.length?offset+1:0}–{Math.min(offset+40,list.length)} of {list.length}</small>
      <button disabled={offset+40>=list.length} onClick={()=>setPage(Math.min(max,page+1))}>Next</button></div>
  </details>;
}

export default function WorkspaceControl({number,name,config,revision,dirty,saving,saved,onChange,onSave}: {
  number:number; name:string; config?:ScanConfig|null; revision:number; dirty:boolean; saving:boolean; saved:boolean;
  onChange:(v:ScanConfig)=>void; onSave:()=>void;
}) {
  const c=config??defaults, path='trading/workspaces/'+number+'/scan';
  const [universe,universeError,reloadUniverse]=useData<Row>('trading/studio/scan-universe',0);
  const [run,runError,reloadRun]=useData<Row>(saved?path:null,3000);
  const [job,setJob]=useState<Row|null>(null);
  useEffect(()=>{if(run)setJob(run);},[run]);
  const current=job, running=current?.status==='QUEUED'||current?.status==='RUNNING';
  const [busy,setBusy]=useState(false), [error,setError]=useState('');
  const [readiness,setReadiness]=useState<{key:string;data:Row}|null>(null);
  const [page,setPage]=useState(0), [filter,setFilter]=useState('');
  const [results,resultError,reloadResults]=useData<Row>(current?.id?'trading/studio/scan-runs/'+current.id+'/results?offset='+page*50+(filter?'&status='+filter:''):null,running?3000:0);
  const instruments:Row[]=universe?.instruments??[], strategies:Row[]=universe?.strategies??[];
  const request={instrument_symbols:c.instrument_symbols,strategy_codes:c.strategy_codes,entry_lookback:c.entry_lookback,stop_atr:c.stop_atr,reward_risk:c.reward_risk};
  const key=JSON.stringify(request), check=readiness?.key===key?readiness.data:null;
  const eligible=!!universe&&c.instrument_symbols.length>0&&c.strategy_codes.length>0&&
    c.instrument_symbols.every(x=>instruments.some(i=>i.symbol===x))&&c.strategy_codes.every(x=>strategies.some(i=>i.code===x))&&
    Number.isInteger(c.entry_lookback)&&c.entry_lookback>=1&&c.entry_lookback<=20&&
    Number(c.stop_atr)>=.1&&Number(c.stop_atr)<=20&&Number(c.reward_risk)>=.1&&Number(c.reward_risk)<=20;
  const locked=busy||saving||running;
  const patch=(v:Partial<ScanConfig>)=>{setReadiness(null);setError('');onChange({...c,...v});};
  async function action(kind:'validate'|'start'|'cancel'|'resume'){
    if(busy)return;
    setBusy(true);setError('');
    try{
      if(kind==='validate')setReadiness({key,data:await api('trading/studio/scan-readiness','POST',request)});
      else {
        const next=kind==='start'?await api(path,'POST',{revision}):await api(path+'/'+current?.id+'/'+kind,'POST');
        setJob(next);setPage(0);reloadRun();reloadResults();
      }
    }catch(e){setError(e instanceof Error?e.message:String(e));reloadRun();}
    finally{setBusy(false);}
  }
  return <section className="mw-control" aria-label="Workspace control">
    <header className="mw-header"><div><strong>{name||'Workspace '+number} · Control desk</strong> <span className="cfg-pill">{current?.status??'Not started'}</span></div>
      <div className="mw-actions">
        <button disabled={saving||!dirty} onClick={onSave}>Save control settings</button>
        <button disabled={locked||!eligible} onClick={()=>void action('validate')}>Validate scan</button>
        <button className="primary" disabled={locked||dirty||!saved||!eligible||!!runError} onClick={()=>void action('start')}>Start scan</button>
        <button disabled={busy||!running} onClick={()=>void action('cancel')}>Stop scan</button>
        <button disabled={busy||dirty||!current||!['FAILED','CANCELLED'].includes(current.status)} onClick={()=>void action('resume')}>Resume saved scan</button>
      </div></header>
    <div className="mw-control-grid">
      <fieldset disabled={locked}><legend>01 · Inputs</legend>
        <Picker label="Instruments" items={instruments} selected={c.instrument_symbols} code="symbol" disabled={locked} change={v=>patch({instrument_symbols:v})}/>
        <label>Scan data source <strong>Auto · mapped collection plans</strong></label>
        <label>Scan timeframe <strong>Latest approved strategy version</strong></label>
        <div className="mw-actions"><button onClick={()=>goto('Market data','plans')}>Collection plans ↗</button><button onClick={reloadUniverse}>Refresh universe</button></div>
      </fieldset>
      <fieldset disabled={locked}><legend>02 · Processing</legend>
        <Picker label="Strategies" items={strategies} selected={c.strategy_codes} code="code" disabled={locked} change={v=>patch({strategy_codes:v})}/>
        <label>Group results <select value={c.grouping} onChange={e=>patch({grouping:e.target.value as ScanConfig['grouping']})}><option value="instrument">By instrument</option><option value="strategy">By strategy</option></select></label>
        <details><summary>Scan protection settings</summary>
          <label>Entry lookback<input type="number" min={1} max={20} value={c.entry_lookback} onChange={e=>patch({entry_lookback:Number(e.target.value)})}/></label>
          <label>Stop ATR<input type="number" min=".1" max="20" step=".01" value={c.stop_atr} onChange={e=>patch({stop_atr:e.target.value})}/></label>
          <label>Reward / risk<input type="number" min=".1" max="20" step=".01" value={c.reward_risk} onChange={e=>patch({reward_risk:e.target.value})}/></label>
        </details><button onClick={()=>goto('Strategies')}>Inspect strategies ↗</button>
      </fieldset>
      <fieldset><legend>03 · Outputs</legend>
        <label>Scan output <strong>Read-only opportunities</strong></label>
        <label>Trade execution <strong>Existing deployment evidence below</strong></label>
        <label>Potential checks <strong>{c.instrument_symbols.length*c.strategy_codes.length}</strong></label>
        <p className="cfg-note">Start queues a durable research scan. Stop cancels this scan only; Resume continues its saved request. No orders, deployments or global controls change.</p>
        <div className="mw-actions"><button onClick={()=>goto('Risk')}>Risk settings ↗</button><button onClick={()=>goto('Execution')}>Execution desk ↗</button></div>
      </fieldset>
    </div>
    <Msg error={error||universeError||runError||resultError}/>
    {dirty&&<p className="cfg-note">Save workspace changes before starting.</p>}
    {universe&&!eligible&&<p className="cfg-note">Select available instruments and strategies, and valid protection settings. Clear unavailable saved selections if the registry changed.</p>}
    {check&&<div className="mw-readiness" role="status"><strong>Readiness: {check.ready} / {check.requested} · {when(check.checked_at)}</strong>
      <details><summary>Sources and blocked checks</summary>{(check.items??[]).map((i:Row)=><p key={i.instrument}>{i.instrument} · {i.ready} ready · {i.blocked} blocked · {(i.sources??[]).join(', ')} · {Object.entries(i.reasons??{}).map(([r,n])=>r+': '+n).join('; ')}</p>)}</details></div>}
    {current&&<section className="mw-scan-results">
      <div className="mw-header"><strong>Latest workspace scan · {current.status}</strong><small>{current.processed_instruments??0}/{current.instruments?.length??0} instruments · {current.total} checks · {when(current.created_at)}</small>
      <button onClick={()=>{reloadRun();reloadResults();}}>Refresh scan</button></div>
      <p className="cfg-note">Frozen selection: {(current.instruments??[]).length} instruments · {(current.strategies??[]).length} strategies. Research results are not executable trade signals.</p>
      <Msg error={current.failure}/>
      <div className="mw-actions"><label>Filter <select value={filter} onChange={e=>{setFilter(e.target.value);setPage(0);}}><option value="">All</option><option>SIGNAL</option><option>WAIT</option><option>ERROR</option></select></label>
        <button disabled={!page} onClick={()=>setPage(p=>p-1)}>Previous results</button><small>Page {page+1}</small><button disabled={!results?.has_more} onClick={()=>setPage(p=>p+1)}>Next results</button></div>
      <div className="mw-results-scroll"><table><thead><tr>{['Instrument','Strategy','Version','TF','Source','Status','Side','Reason','Entry','Stop','Target'].map(h=><th key={h}>{h}</th>)}</tr></thead>
        <tbody>{(results?.items??[]).map((i:Row)=><tr key={i.instrument_symbol+':'+i.strategy_code}>{[i.instrument_symbol,i.strategy_code,i.version,i.timeframe,i.account_code,i.status,i.side,i.reason,i.entry,i.stop_loss,i.take_profit].map((v,k)=><td key={k}>{String(v??'—')}</td>)}</tr>)}</tbody></table>
        {!results?.items?.length&&<p className="cfg-note">No results on this page yet.</p>}</div>
    </section>}
  </section>;
}
