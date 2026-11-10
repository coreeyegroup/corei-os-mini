import {useEffect,useRef,useState} from 'react';
import RecordEditor from './RecordEditor';
import RegistryWorkspace from './RegistryWorkspace';

type Point={id:string;server_name:string;name:string;host:string;port:number};
type Account={id:string;name:string;broker:string;platform:string;login:string;mode:string;server_name:string;
 access_point_id:string;access_point_name:string;host:string;port:number;enabled:boolean;version:number};
type Page={items:Account[];total:number;legacy_count:number;credential_storage_ready:boolean};
type Draft={name:string;broker:string;login:string;password:string;mode:string;server_name:string;access_point_id:string};
const blank=():Draft=>({name:'',broker:'',login:'',password:'',mode:'SANDBOX',server_name:'',access_point_id:''});
const endpoint=(p:{host:string;port:number})=>`${p.host.includes(':')?'['+p.host+']':p.host}:${p.port}`;

async function api(path:string,method='GET',body?:unknown,signal?:AbortSignal){
 const raw=body instanceof File;
 const response=await fetch('/api/'+path,{method,signal,credentials:'same-origin',cache:'no-store',
  headers:{'Content-Type':raw?'application/octet-stream':'application/json','X-Corei-Request':'workstation'},
  body:body===undefined?undefined:raw?body:JSON.stringify(body)});
 const data=await response.json().catch(()=>({detail:'Unexpected server response.'}));
 if(!response.ok)throw Object.assign(new Error(typeof data.detail==='string'?data.detail:'Request failed.'),{status:response.status});
 return data;
}

export default function BrokerAccounts(){
 const [user,setUser]=useState(''),[checking,setChecking]=useState(true),[username,setUsername]=useState(''),[operatorPassword,setOperatorPassword]=useState('');
 const [page,setPage]=useState<Page|null>(null),[query,setQuery]=useState(''),[offset,setOffset]=useState(0),[revision,setRevision]=useState(0);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [open,setOpen]=useState(false),[editing,setEditing]=useState<Account|null>(null),[draft,setDraft]=useState<Draft>(blank),[dirty,setDirty]=useState(false);
 const [points,setPoints]=useState<Point[]>([]),[filename,setFilename]=useState(''),[importing,setImporting]=useState(false);
 const upload=useRef<AbortController|null>(null);
 const reset=()=>{upload.current?.abort();setImporting(false);setOpen(false);setEditing(null);setDraft(blank());setPoints([]);setFilename('');setDirty(false);};
 useEffect(()=>{const c=new AbortController();api('operator/session','GET',undefined,c.signal).then(x=>setUser(x.username)).catch(e=>{if(!c.signal.aborted&&e.status!==401)setError(e.message);}).finally(()=>{if(!c.signal.aborted)setChecking(false);});return()=>c.abort();},[]);
 useEffect(()=>{const logout=()=>{reset();setUser('');setPage(null);setError('');setNotice('');};const storage=(e:StorageEvent)=>{if(e.key==='corei-os-mini.operator-signout')logout();};window.addEventListener('corei-operator-signout',logout);window.addEventListener('storage',storage);return()=>{window.removeEventListener('corei-operator-signout',logout);window.removeEventListener('storage',storage);upload.current?.abort();};},[]);
 useEffect(()=>{if(!user)return;const c=new AbortController();const timer=setTimeout(()=>{api(`registry/broker-accounts?limit=50&offset=${offset}&q=${encodeURIComponent(query)}`,'GET',undefined,c.signal).then(setPage).catch(e=>{if(!c.signal.aborted){setError(e.message);if(e.status===401){reset();setUser('');setPage(null);}}});},150);return()=>{clearTimeout(timer);c.abort();};},[user,revision,query,offset]);
 useEffect(()=>{const warn=(e:BeforeUnloadEvent)=>{if(dirty||busy){e.preventDefault();e.returnValue='';}};const guard=(e:Event)=>{if(busy||importing||(dirty&&!window.confirm('Discard unsaved account changes?')))e.preventDefault();};window.addEventListener('beforeunload',warn);window.addEventListener('corei-before-layout-change',guard);return()=>{window.removeEventListener('beforeunload',warn);window.removeEventListener('corei-before-layout-change',guard);};},[dirty,busy,importing]);
 const close=()=>{if(busy)return;if(dirty&&!window.confirm('Discard unsaved account changes?'))return;reset();};
 const change=(key:keyof Draft,value:string)=>{setDirty(true);setDraft(x=>({...x,[key]:value,...(key==='server_name'?{access_point_id:''}:{})}));};
 const matching=points.filter(p=>p.server_name===draft.server_name.trim());
 const start=(row?:Account)=>{setError('');setNotice('');setEditing(row??null);setDraft(row?{name:row.name,broker:row.broker,login:row.login,password:'',mode:row.mode,server_name:row.server_name,access_point_id:row.access_point_id}:blank());setPoints(row?[{id:row.access_point_id,server_name:row.server_name,name:row.access_point_name,host:row.host,port:row.port}]:[]);setFilename('');setDirty(false);setOpen(true);};
 async function signIn(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');try{const data=await api('operator/login','POST',{username,password:operatorPassword});setOperatorPassword('');setUser(data.username);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 async function analyse(file:File){
  if(file.size<32||file.size>1048576){setError('Select a servers.dat file between 32 bytes and 1 MiB.');return;}
  upload.current?.abort();const c=new AbortController();upload.current=c;setImporting(true);setError('');setPoints([]);setFilename('');setDraft(x=>({...x,access_point_id:''}));setDirty(true);
  try{const result=await api('registry/broker-accounts/server-catalog','POST',file,c.signal);if(c.signal.aborted)return;setPoints(result.items);setFilename(file.name);setDraft(x=>({...x,access_point_id:''}));setDirty(true);}
  catch(e){if(!c.signal.aborted)setError((e as Error).message);}
  finally{if(!c.signal.aborted)setImporting(false);}
 }
 async function save(e:React.FormEvent){e.preventDefault();setBusy(true);setError('');
  try{const payload=editing?{name:draft.name,access_point_id:draft.access_point_id,enabled:editing.enabled,version:editing.version,...(draft.password?{password:draft.password}:{})}:{...draft,platform:'MT5'};
   await api('registry/broker-accounts'+(editing?'/'+editing.id:''),editing?'PUT':'POST',payload);
   reset();setNotice('Account saved. Not connected; no broker login or trading action occurred.');setRevision(x=>x+1);
  }catch(e){setError((e as Error).message+' If the request was interrupted, refresh before retrying.');}finally{setBusy(false);}
 }
 async function toggle(row:Account){setBusy(true);setError('');try{await api('registry/broker-accounts/'+row.id,'PUT',{name:row.name,access_point_id:row.access_point_id,enabled:!row.enabled,version:row.version});setRevision(x=>x+1);}catch(e){setError((e as Error).message);}finally{setBusy(false);}}
 return <div className="content registry-workspace broker-accounts"><div className="registry-heading"><div><div className="eyebrow">WORKSTATION / ACCOUNTS</div><h1>Broker accounts</h1></div><span className="configuration-badge">Saved configuration · connection tool pending</span></div>
 {error&&!open&&<p role="alert" className="danger">{error}</p>}
 {checking?<p>Checking operator session…</p>:!user?<form className="registry-form" onSubmit={signIn}><h2>Operator sign in</h2><label>Username<input required autoComplete="username" value={username} onChange={e=>setUsername(e.target.value)}/></label><label>Password<input required type="password" autoComplete="current-password" value={operatorPassword} onChange={e=>setOperatorPassword(e.target.value)}/></label><button disabled={busy}>Sign in</button></form>:<>
 <p>Add each broker account once. Its broker server and selected access point are saved separately.</p>
 <div className="registry-commandbar"><label>Search accounts<input value={query} onChange={e=>{setQuery(e.target.value);setOffset(0);}}/></label><div className="registry-actions"><button disabled={busy} onClick={()=>{setError('');setRevision(x=>x+1);}}>Refresh accounts</button><button className="primary-action" disabled={busy} onClick={()=>start()}>Add broker account</button><button disabled={busy} onClick={async()=>{setBusy(true);try{await api('operator/logout','POST');window.dispatchEvent(new Event('corei-operator-signout'));try{localStorage.setItem('corei-os-mini.operator-signout',String(Date.now()));}catch{/* Server cookie already cleared. */}}catch(e){setError((e as Error).message);}finally{setBusy(false);}}}>Sign out</button></div></div>
 {page&&!page.credential_storage_ready&&<p role="alert">Secure password storage needs server setup before accounts can be added.</p>}
 {notice&&<p role="status" className="registry-success">{notice}</p>}
 {!page?<p>Loading accounts…</p>:<><div className="registry-table"><table><thead><tr>{['Account','Broker / platform','Login','Environment','Broker server','Selected access point','Status','Actions'].map(x=><th key={x}>{x}</th>)}</tr></thead><tbody>{page.items.map(row=><tr key={row.id}><td>{row.name}</td><td>{row.broker} · {row.platform}</td><td>{row.login}</td><td>{row.mode==='LIVE'?'Live (blocked)':'Demo'}</td><td>{row.server_name}</td><td>{row.access_point_name}<br/>{endpoint(row)}</td><td>Saved · Not connected<br/>{row.enabled?'Enabled configuration':'Disabled configuration'}</td><td><button disabled={busy} onClick={()=>start(row)}>Edit / replace password</button><button disabled={busy||(row.mode==='LIVE'&&!row.enabled)} title={row.mode==='LIVE'?'Paper-only project: live accounts cannot be enabled.':undefined} onClick={()=>void toggle(row)}>{row.enabled?'Disable':'Enable configuration'}</button></td></tr>)}</tbody></table></div>{page.items.length===0&&<p>No saved broker accounts match. Choose Add broker account to begin.</p>}<div className="registry-pagination"><button disabled={offset===0} onClick={()=>setOffset(Math.max(0,offset-50))}>Previous</button><span>{page.total?offset+1:0}–{offset+page.items.length} of {page.total}</span><button disabled={offset+50>=page.total} onClick={()=>setOffset(offset+50)}>Next</button></div>
 {page.legacy_count>0&&<details><summary>Earlier account records ({page.legacy_count})</summary><p>These records have no saved credential/access-point configuration. They remain available here.</p><RegistryWorkspace initial="accounts"/></details>}</>}
 {open&&<RecordEditor editing={!!editing} close={close} title={editing?'Edit broker account':'Add broker account'} dialogLabel="Broker account editor"><header><h2>{editing?'Edit broker account':'Add broker account'}</h2><button disabled={busy} aria-label="Close broker account editor" onClick={close}>×</button></header>
 {error&&<p role="alert" className="danger">{error}</p>}
 <form className="registry-form" onSubmit={save}><fieldset disabled={busy||importing} style={{border:0,padding:0,display:'contents'}}>
 <label>Account label<input required maxLength={120} value={draft.name} onChange={e=>change('name',e.target.value)}/></label>
 <label>Broker name<input required maxLength={120} readOnly={!!editing} value={draft.broker} onChange={e=>change('broker',e.target.value)}/></label>
 <label>Platform<input readOnly value="MT5"/></label>
 <label>Account environment<input readOnly value={editing&&draft.mode==='LIVE'?'Live (blocked: paper-only)':'Demo / sandbox'}/></label>
 <small>COREI-OS-MINI is paper-only. Add your broker's demo account; live accounts cannot be added or enabled.</small>
 <label>Broker login<input required readOnly={!!editing} inputMode="numeric" pattern="[1-9][0-9]{0,19}" maxLength={20} value={draft.login} onChange={e=>change('login',e.target.value)}/></label>
 <label>{editing?'New password (leave blank to keep saved password)':'Broker password'}<input required={!editing} type="password" autoComplete="new-password" maxLength={256} value={draft.password} onChange={e=>change('password',e.target.value)}/></label>
 <label>Broker-provided server name<input required readOnly={!!editing} maxLength={120} value={draft.server_name} onChange={e=>change('server_name',e.target.value)}/></label>
 <small>Enter the exact server name supplied by your broker. This is separate from its IP address and port.</small>
 <label>Browse and analyse servers.dat<input type="file" accept=".dat" onChange={e=>{const file=e.target.files?.[0];if(file)void analyse(file);e.target.value='';}}/></label>
 {filename&&<small>Analysed: {filename}. {points.length} access points in the file.</small>}
 <label>Access point for this broker server<select required value={draft.access_point_id} onChange={e=>change('access_point_id',e.target.value)}><option value="">Select an access point…</option>{matching.map(p=><option key={p.id} value={p.id}>{p.name} · {endpoint(p)}</option>)}</select></label>
 {points.length>0&&matching.length===0&&<p role="status">No exact match for this broker server. Check the name or import the broker's server file. Available servers: {[...new Set(points.map(p=>p.server_name))].join(', ')}</p>}
 <small>Passwords are encrypted on the server and never returned to this form. {editing?'Broker, login, environment and server identify the account; add another account to change them.':''}</small>
 <button className="primary-action" disabled={!draft.access_point_id||busy||importing}>{busy?'Saving…':'Save account'}</button></fieldset>
 {importing&&<p role="status">Analysing server file…</p>}<p>Saving does not connect to the broker or enable trading.</p></form>
 </RecordEditor>}
 </>}
 </div>;
}
