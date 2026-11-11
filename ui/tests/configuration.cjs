// Configuration workspace interaction test against a small stateful mock of /api/config.
const {JSDOM}=require('jsdom');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'ui/dist/index.html'),'utf8');
const entry=html.match(/src="([^" ]+\.js)"/)[1].replace(/^\//,'');
const js=fs.readFileSync(path.join(root,'ui/dist',entry),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,40));

(async()=>{
 const layout={version:1,workspace:'Research',activity:'Configuration',groups:[['Configuration']],active:['Configuration'],focus:0,left:false,right:false,bottom:false,axis:'row',leftWidth:220,rightWidth:270,bottomHeight:180,maximized:false};
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.localStorage.setItem('corei-os-mini.workstation.layout.v1',JSON.stringify(layout));
 w.confirm=()=>true;

 // ---- mock backend ----
 let signedIn=false;const session={roles:['ADMIN','OBSERVER','RISK_APPROVER','TRADING_OPS'],must:true};
 const users=[{id:'u1',username:'risk-ops',display_name:'risk-ops',roles:['ADMIN','OBSERVER','RISK_APPROVER','TRADING_OPS'],status:'ACTIVE',must_change_password:false,version:1,active_sessions:1}];const db={providers:[],connections:[],accounts:[],instruments:[],mappings:[],groups:[]};const prefs={};const approvals=[];const importCalls=[];let needReauth=false,reauthCalls=0;
 const controls={DEMO:{environment:'DEMO',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()},
                  LIVE:{environment:'LIVE',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()}};
 const searched=[],latencyCalls=[],setups=[];
 const requests=[];let n=0;
 const ok=(body,status=200)=>({ok:true,status,json:async()=>body});
 const fail=(status,detail)=>({ok:false,status,json:async()=>({detail})});
 const enrich=(entity,r)=>{
   if(entity==='connections'){const p=db.providers.find(x=>x.id===r.provider_id);return {...r,provider_code:p.code,account_count:db.accounts.filter(a=>a.connection_id===r.id).length};}
   if(entity==='accounts'){const c=db.connections.find(x=>x.id===r.connection_id);const blockers=[...(r.credential_kind?[]:['ACCOUNT_CREDENTIAL_MISSING']),'ADAPTER_NOT_AVAILABLE'];
     return {...r,platform:c.platform,environment:c.environment,connection_code:c.code,provider_code:'ICM',data_blockers:blockers,
       trading_blockers:[...blockers,...(controls[c.environment].trading_enabled?[]:['ORDER_SENDING_OFF_'+c.environment])]};}
   return r;};
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';requests.push({url,method,body:options.body});
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   const me={username:'risk-ops',display_name:'risk-ops',roles:session.roles,must_change_password:session.must};
   if(url.endsWith('/operator/session'))return signedIn?ok(me):fail(401,'Sign in');
   if(url.endsWith('/operator/login')){signedIn=true;return ok(me);}
   if(url.endsWith('/operator/logout')){signedIn=false;return ok({signed_out:true});}
   if(url.endsWith('/operator/password')){session.must=false;return ok({changed:true});}
   if(url.includes('/operator/prefs')){if(method==='PUT'){prefs[decodeURIComponent(url.split('/prefs/')[1])]=JSON.parse(options.body).value;return ok({saved:true});}return ok(prefs);}
   if(url.endsWith('/operator/reauth')){reauthCalls++;needReauth=false;return ok({reauthenticated:true,minutes:5});}
   if(url.endsWith('/config/health'))return ok({generated_at:new Date().toISOString(),network:{egress_ip:'203.0.113.200',clock_synchronised:true,server_time_utc:'2026-11-11T10:00:00Z'},
     controls:Object.values(controls),four_eyes_live_changes:true,pending_approvals:approvals.length,failed_alerts:0,
     checklist:[{step:'Add a provider (broker)',done:db.providers.length>0,section:'providers'},{step:'Add an account (MT5: login, password and server)',done:db.accounts.length>0,section:'accounts'}],
     connections:db.connections.map(r=>enrich('connections',r)),accounts:db.accounts.map(r=>enrich('accounts',r)),
     summary:{accounts:db.accounts.length,data_ready:0,trading_ready:0,with_warnings:0}});
   if(url.includes('/config/approvals/')&&url.endsWith('/decision')){const id=url.split('/approvals/')[1].split('/')[0];const r=approvals.find(a=>a.id===id);
     const body=JSON.parse(options.body);r.status=body.approve?'APPLIED':'REJECTED';r.decided_by='risk-ops';if(body.approve&&r.apply)r.apply();return ok({id,status:r.status});}
   if(url.includes('/config/approvals'))return ok({items:approvals.filter(a=>!url.includes('status=PENDING')||a.status==='PENDING')});
   if(url.endsWith('/accounts/import')){const body=JSON.parse(options.body);importCalls.push(body);
     const lines=body.csv.trim().split('\n').slice(1);return ok({dry_run:body.dry_run,applied:!body.dry_run,ok:true,counts:{ok:lines.length,error:0},rows:lines.map((l,i)=>({line:i+2,code:l.split(',')[1],status:'OK'}))});}
   if(url.endsWith('/config/policies'))return ok({items:[{key:'four_eyes_live_changes',value:true,version:1,kind:'bool',description:'LIVE changes need a second person.',updated_by:'system',updated_at:new Date().toISOString()}]});
   if(url.includes('/credential-use'))return ok({items:[]});
   if(url.includes('/symbol-specs'))return ok({items:[]});
   if(url.includes('/api/admin/users')){
     if(method==='GET')return ok({items:users});
     const body=JSON.parse(options.body);
     if(method==='POST'&&url.endsWith('/users')){const u={id:'u'+(users.length+1),username:body.username.toLowerCase(),display_name:body.display_name,roles:body.roles,status:'ACTIVE',must_change_password:true,version:1,active_sessions:0};users.push(u);return ok(u,201);}
     if(method==='PUT'){const u=users.find(x=>url.endsWith(x.id));Object.assign(u,body,{version:u.version+1});return ok(u);}
     return ok({reset:true});}
   const u=new URL(url,'https://x');const parts=u.pathname.replace('/api/config/','').split('/');
   if(parts[0]==='summary')return ok({counts:Object.fromEntries(Object.entries(db).map(([k,v])=>[k,v.length])),execution_controls:Object.values(controls),credential_storage_ready:true,available_adapters:[]});
   if(parts[0]==='execution-controls'){
     if(method==='GET')return ok({items:Object.values(controls)});
     const body=JSON.parse(options.body);const c=controls[parts[1]];
     if(body.trading_enabled&&body.confirm_environment!==parts[1])return fail(422,'confirm');
     Object.assign(c,{trading_enabled:body.trading_enabled,reason:body.reason,version:c.version+1,updated_by:'risk-ops'});return ok(c);}
   if(parts[0]==='audit')return ok({items:[]});
   if(parts[0]==='mt5'){
     const servers=[
       {server_name:'ICMarketsSC-Demo',company_name:'Raw Trading Ltd',details:{CompanyName:'Raw Trading Ltd',TimeZone:120},access_points:[{name:'Access Point 1',host:'203.0.113.10',port:443},{name:'Access Point 2',host:'10.30.210.41',port:443}]},
       {server_name:'MarketEquityInc-Demo',company_name:'Market Equity Inc.',details:{CompanyName:'Market Equity Inc.'},access_points:[{name:'Access Point 1',host:'192.109.15.68',port:443},{name:'Access Point 2',host:'192.109.15.68',port:1950}]},
       {server_name:'Marketmates-Live',company_name:'Marketmates Ltd',details:{CompanyName:'Marketmates Ltd'},access_points:[{name:'Access Point 1',host:'142.91.121.204',port:1950},{name:'Access Point 2',host:'142.91.121.204',port:443},{name:'Access Point 3',host:'5.181.78.117',port:1950}]},
       {server_name:'MetaQuotes-Demo',company_name:'MetaQuotes Ltd.',details:{CompanyName:'MetaQuotes Ltd.',TimeZone:120,DST:1,Description:'MetaTrader 5 demo server operated by MetaQuotes Ltd. for testing and evaluation'},
        access_points:Array.from({length:50},(_,i)=>({name:'Access Point '+(i+1),host:i<25?'78.140.180.'+(i+1):'2a00:1178:1:60::'+(i+1).toString(16),port:i%2?1950:443}))}];
     if(parts[1]==='latency'){const body=JSON.parse(options.body);latencyCalls.push(body);
       return ok({measured_from:'core-node',attempts:3,results:body.addresses.map(a=>({...a,reachable:true,latency_ms:Number(a.host.split('.').pop())+1,median_ms:Number(a.host.split('.').pop())+2,error:null}))});}
     if(parts[1]==='account-setup'){const body=JSON.parse(options.body);setups.push(body);
       const c=db.connections.find(x=>x.id===body.connection_id);
       const extra=body.server.access_points.filter(p=>!c.settings.access_points.some(q=>q.host===p.host&&q.port===p.port));
       c.settings.access_points.push(...extra.map(p=>({...p,enabled:false})));c.version++;
       const row={...body.account,id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,connection_id:c.id,version:1,updated_at:new Date().toISOString(),
         credential_kind:body.credential?'MT5_LOGIN':null,routing:{mode:body.account.access_mode,used_mode:body.account.access_mode,addresses:[],warnings:[]}};
       db.accounts.push(row);
       return ok({connection_action:extra.length?'ADDRESSES_ADDED':'REUSED',connection:enrich('connections',c),account:enrich('accounts',row)},201);}
     if(parts[1]==='broker-search'){const query=JSON.parse(options.body).query;searched.push(query);
       return ok({catalog_id:'cat1',source:'broker-directory',query,server_count:servers.length,access_point_count:56,skipped_servers:0,invalid_addresses:0,servers_without_access_points:0,unreadable_extended_records:0});}
     if(method==='POST')return ok({catalog_id:'cat1',server_count:servers.length,access_point_count:servers.reduce((n,s)=>n+s.access_points.length,0),skipped_servers:0,invalid_addresses:0,servers_without_access_points:0,unreadable_extended_records:2});
     if(u.pathname.endsWith('/servers')){const t=(u.searchParams.get('q')||'').toLowerCase().split(/\s+/).filter(Boolean);
       const items=servers.filter(x=>t.every(k=>[x.server_name,x.company_name,...x.access_points.map(a=>a.host+':'+a.port)].join(' ').toLowerCase().includes(k)))
         .map(x=>({server_name:x.server_name,company_name:x.company_name,access_point_count:x.access_points.length}));
       return ok({total:items.length,offset:0,items});}
     if(u.pathname.endsWith('/server'))return ok(servers.find(s=>s.server_name===u.searchParams.get('name')));
     const b=(u.searchParams.get('broker')||'').toLowerCase().split(/\s+/).filter(Boolean),q=(u.searchParams.get('q')||'').toLowerCase().split(/\s+/).filter(Boolean);
     const rows=servers.flatMap(s=>s.access_points.map(a=>({server_name:s.server_name,company_name:s.company_name,...a})))
       .filter(r=>b.every(t=>(r.server_name+' '+r.company_name).toLowerCase().includes(t))&&q.every(t=>`${r.host}:${r.port} ${r.name}`.toLowerCase().includes(t)));
     return ok({total:rows.length,server_matches:new Set(rows.map(r=>r.server_name)).size,offset:0,items:rows.slice(0,200)});}
   const entity=parts[0];
   if(parts[2]==='latency')return ok({items:[]});
   if(parts[2]==='credential'&&needReauth)return fail(428,'Re-enter your password to confirm this action.');
   if(parts[2]==='credential'){const body=JSON.parse(options.body);const row=db[entity].find(x=>x.id===parts[1]);
     row.credential_kind=body.credential.kind;row.credential_access_level=body.credential.access_level;row.credential_updated_at=new Date().toISOString();
     if(body.credential.access_level==='READ_ONLY'){row.investor_credential_kind=body.credential.kind;row.investor_credential_updated_at=row.credential_updated_at;}
     else{row.master_credential_kind=body.credential.kind;row.master_credential_updated_at=row.credential_updated_at;}
     return ok({kind:body.credential.kind,access_level:body.credential.access_level});}
   if(method==='GET'&&parts.length===1)return ok({items:db[entity].map(r=>enrich(entity,r))});
   if(method==='POST'){const body=JSON.parse(options.body);
     if(entity==='providers'&&db.providers.some(p=>p.code===body.code.toUpperCase()))return fail(409,'A provider with this code already exists.');
     if(entity==='connections'&&body.environment==='LIVE'){const cr={id:'cr'+(approvals.length+1),entity:'connections',action:'CREATE_CONNECTION',status:'PENDING',
       summary:`New LIVE connection ${body.code.toUpperCase()} adds address ${body.settings.access_points[0].host}:${body.settings.access_points[0].port}`,requested_by:'other-user',requested_at:new Date().toISOString(),
       apply:()=>db.connections.push({...body,code:body.code.toUpperCase(),id:'live-conn',version:1,updated_at:new Date().toISOString()})};approvals.push(cr);
       return ok({change_request:cr},202);}
     const row={...body,id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,version:1,updated_at:new Date().toISOString(),...(body.code?{code:body.code.toUpperCase()}:{})};
     db[entity].push(row);return ok(enrich(entity,row),201);}
   if(method==='PUT'){const body=JSON.parse(options.body);const row=db[entity].find(x=>x.id===parts[1]);
     if(body.version!==row.version)return fail(409,'Changed by someone else');Object.assign(row,body,{version:row.version+1});return ok(enrich(entity,row));}
   throw Error('Unexpected request '+method+' '+url);
 };

 w.eval(js);await tick();await tick();
 const $=s=>w.document.querySelector(s);
 const fill=(el,value)=>{const proto=el.tagName==='SELECT'?w.HTMLSelectElement.prototype:el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const byLabel=l=>$(`.cfg-detail [aria-label="${l}"]`)||$(`[aria-label="${l}"]`);
 const nav=label=>[...w.document.querySelectorAll('.cfg-nav button')].find(b=>b.querySelector('span').textContent===label);
 const button=text=>[...w.document.querySelectorAll('button')].find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();};

 // Sign in
 assert.match(w.document.body.textContent,/Sign in/);
 const signin=$('.cfg-signin form');fill(signin.querySelector('[autocomplete="username"]'),'risk-ops');fill(signin.querySelector('[type="password"]'),'secret-pass');
 await submit(signin);
 // A temporary password must be changed before anything else.
 assert.match($('.content h1').textContent,/Change password/);
 const pw=$('[aria-label="Change password"]');const pwInputs=pw.querySelectorAll('input');
 fill(pwInputs[0],'Temp-pass-4821');fill(pwInputs[1],'My-own-pass-993');fill(pwInputs[2],'My-own-pass-99X');await tick();
 await submit(pw);assert.match($('[aria-label="Change password"]').textContent,/do not match/);
 fill(pwInputs[2],'My-own-pass-993');await tick();await submit(pw);
 assert.equal(JSON.parse(requests.find(r=>r.url.endsWith('/operator/password')).body).new_password,'My-own-pass-993');
 assert.match($('.cfg-user').textContent,/risk-ops/);
 assert.match($('.cfg-nav').textContent,/Order sending/);
 assert.match($('.cfg-user').textContent,/Administrator, Observer, Risk approver, Trading operations/);
 assert.match($('.cfg-head h1').textContent,/Health/);  // opens on the health board
 assert.match($('.cfg-health').textContent,/203\.0\.113\.200/);
 assert.match($('.cfg-health').textContent,/Next: Add a provider/);
 console.log('PASS: operator sign-in, forced password change, roles shown, section navigation.');
 nav('Providers').click();await tick();await tick();

 // Provider: create, duplicate rejected with backend message
 button('New provider').click();await tick();
 fill(byLabel('Provider name'),'IC Markets (Raw Trading Ltd)');await tick();
 assert.equal(byLabel('Provider ID').value,'IC_MARKETS');  // suggested from the name
 fill(byLabel('Provider ID'),'icm');await tick();
 await submit($('.cfg-form:not(.cfg-signin .cfg-form)'));
 assert.equal(db.providers.length,1);assert.equal(db.providers[0].code,'ICM');
 assert.match($('.cfg-table').textContent,/IC Markets/);
 button('New provider').click();await tick();fill(byLabel('Provider name'),'Again');fill(byLabel('Provider ID'),'ICM');await tick();
 await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-detail').textContent,/already exists/);
 button('✕')?.click?.();await tick();
 console.log('PASS: provider create, upper-case code, duplicate shows backend message.');

 // Connection: MT5 entered manually, with ordered addresses
 nav('Connections').click();await tick();await tick();
 button('New connection').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 assert.equal(byLabel('Connection ID').value,'ICM_MT5_DEMO');  // suggested from provider, platform, environment
 assert.equal(byLabel('Connection name').value,'IC Markets (Raw Trading Ltd) MT5 demo');  // name suggested too
 assert.deepEqual([...w.document.querySelectorAll('.cfg-section h4')].map(h=>h.textContent),['1Broker','2Server and addresses','3Name','4Method, clock and limits']);
 button('Enter server and addresses manually').click();await tick();
 fill(byLabel('Connection name'),'ICM MT5 demo');
 fill(byLabel('Broker server name'),'ICMarketsSC-Demo');
 fill(byLabel('Access point 1 host'),'192.0.2.10');await tick();
 button('+ Add address').click();await tick();
 fill(byLabel('Access point 2 host'),'192.0.2.11');fill(byLabel('Access point 2 port'),'444');await tick();
 [...w.document.querySelectorAll('[aria-label="Move up"]')][1].click();await tick();
 // The editor is a movable, resizable window: Alt+arrows move it, Shift+arrows resize it, dock and maximise toggle.
 const win=$('.fw.fw-float');assert.equal(win.getAttribute('role'),'dialog');assert.equal(w.document.querySelectorAll('.fw-edge').length,8,'eight resize handles');
 const title=$('.fw-title');const x0=parseFloat(win.style.left),w0=parseFloat(win.style.width);
 title.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',altKey:true,bubbles:true}));await tick();assert.equal(parseFloat($('.fw').style.left),x0-16);
 title.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',shiftKey:true,bubbles:true}));await tick();assert.equal(parseFloat($('.fw').style.width),Math.min(w0+16,w.innerWidth));
 $('[aria-label="Dock window right"]').click();await tick();assert.ok($('.fw').classList.contains('fw-dock'));
 $('[aria-label="Float window"]').click();await tick();assert.ok($('.fw').classList.contains('fw-float'));
 $('[aria-label="Maximise window"]').click();await tick();assert.ok($('.fw').classList.contains('fw-max'));
 $('[aria-label="Restore window"]').click();await tick();assert.ok(!$('.fw').classList.contains('fw-max'));
 assert.match($('.fw-caption').textContent,/Unsaved/);
 assert.ok($('.fw-footer button.primary'),'actions stay in the window footer');
 w.confirm=()=>false;$('.fw').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();assert.ok($('.fw'),'Esc keeps window when discard is declined');w.confirm=()=>true;
 await submit($('.cfg-detail .cfg-form'));
 const conn=db.connections[0];
 assert.equal(conn.platform,'MT5');assert.equal(conn.environment,'DEMO');
 assert.deepEqual(conn.settings.access_points.map(p=>[p.name,p.host,p.port]),[['ICMarketsSC-Demo','192.0.2.11',444],['Added manually','192.0.2.10',443]]);
 assert.ok(byLabel('Platform').disabled,'platform fixed after creation');
 console.log('PASS: MT5 connection entered manually with ordered addresses; floating window moves, resizes, maximises; platform fixed after creation.');

 // servers.dat: choose a server on the left, its addresses on the right.
 button('New connection').click();await tick();
 const fileInput=$('.cfg-finder input[type="file"]');Object.defineProperty(fileInput,'files',{value:[new w.File(['x'],'servers.dat')]});
 fileInput.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();await new Promise(r=>setTimeout(r,300));
 assert.match($('.cfg-picker').textContent,/servers\.dat4 servers · 57 addresses/);
 assert.equal(w.document.querySelectorAll('.cfg-picker-servers [role="option"]').length,4);
 fill(byLabel('Filter servers'),'market');await new Promise(r=>setTimeout(r,350));await tick();
 assert.deepEqual([...w.document.querySelectorAll('.cfg-picker-name')].map(x=>x.textContent),['ICMarketsSC-Demo','MarketEquityInc-Demo','Marketmates-Live']);
 fill(byLabel('Filter servers'),'142.91');await new Promise(r=>setTimeout(r,350));await tick();await tick();
 assert.match($('.cfg-picker-server').textContent,/Marketmates-Live/);  // a single match opens itself
 fill(byLabel('Filter servers'),'icmarkets');await new Promise(r=>setTimeout(r,350));await tick();await tick();
 // Private addresses are shown but not pre-selected.
 assert.equal(byLabel('Select 203.0.113.10:443 on ICMarketsSC-Demo').checked,true);
 assert.equal(byLabel('Select 10.30.210.41:443 on ICMarketsSC-Demo').checked,false);
 assert.match($('.cfg-picker-points').textContent,/Private/);
 fill(byLabel('Filter servers'),'');await new Promise(r=>setTimeout(r,350));await tick();
 [...w.document.querySelectorAll('.cfg-picker-servers [role="option"]')].find(b=>b.textContent.includes('MetaQuotes-Demo')).click();await tick();await tick();
 assert.match($('.cfg-picker').textContent,/MetaTrader 5 demo server operated by MetaQuotes Ltd\. for testing and evaluation/);
 assert.match($('.cfg-picker').textContent,/Time zone120/);
 button('None').click();await tick();
 byLabel('Select 78.140.180.8:1950 on MetaQuotes-Demo').click();byLabel('Select 78.140.180.2:1950 on MetaQuotes-Demo').click();await tick();
 assert.match($('.cfg-picker-foot').textContent,/2 of 50 addresses of MetaQuotes-Demo selected/);
 button('Use MetaQuotes-Demo').click();await tick();
 assert.equal($('.cfg-picker'),null);
 assert.match($('.cfg-chosen').textContent,/ServerMetaQuotes-DemoMetaQuotes Ltd\./);
 assert.match($('[aria-label="Server description"]').textContent,/CompanyMetaQuotes Ltd\./);
 assert.match($('.cfg-addresses-head').textContent,/2 of 50 in use/);
 assert.deepEqual([...w.document.querySelectorAll('.cfg-address-table tbody td:nth-child(3)')].slice(0,2).map(x=>x.textContent),['78.140.180.2:1950','78.140.180.8:1950']);
 byLabel('Use access point 3').click();await tick();
 assert.match($('.cfg-addresses-head').textContent,/3 of 50 in use/);
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 assert.equal(byLabel('Connection ID').value,'ICM_MT5_DEMO_2');  // ICM_MT5_DEMO is taken
 fill(byLabel('Connection ID'),'mt5001');fill(byLabel('Connection name'),'MetaQuotes demo');await tick();
 await submit($('.cfg-detail .cfg-form'));
 const mq=db.connections.find(c=>c.code==='MT5001');
 assert.equal(mq.settings.access_points.length,50);
 assert.equal(mq.settings.access_points.filter(p=>p.enabled).length,3);
 assert.deepEqual(mq.settings.access_points.slice(0,2).map(p=>p.name),['Access Point 2','Access Point 8']);  // the name the file gives each address is kept
 assert.equal(mq.settings.company_name,'MetaQuotes Ltd.');
 assert.match(mq.settings.server_details.Description,/demo server/);
 console.log('PASS: server picker lists servers, shows addresses and full description, skips private addresses, saves the selection.');
 // Change server, then go back without losing the current one.
 button('Change server…').click();await tick();
 assert.ok(byLabel('Find broker'));
 button('← Keep MetaQuotes-Demo').click();await tick();
 assert.match($('.cfg-chosen').textContent,/MetaQuotes-Demo/);
 // All addresses view (Westernpips style) with a clearable filter.
 button('Change server…').click();await tick();
 const file2=$('.cfg-finder input[type="file"]');Object.defineProperty(file2,'files',{value:[new w.File(['x'],'servers.dat')]});
 file2.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();await new Promise(r=>setTimeout(r,300));
 [...w.document.querySelectorAll('[role="tab"]')].find(b=>b.textContent==='All addresses').click();await new Promise(r=>setTimeout(r,350));await tick();
 assert.equal(w.document.querySelectorAll('.cfg-picker-flat tbody tr').length,57);
 fill(byLabel('Filter servers'),'marketmates');await new Promise(r=>setTimeout(r,350));await tick();
 assert.equal(w.document.querySelectorAll('.cfg-picker-flat tbody tr').length,3);
 byLabel('Select 142.91.121.204:443 on Marketmates-Live').click();await tick();await tick();
 assert.match($('.cfg-picker-foot').textContent,/1 of 3 addresses of Marketmates-Live selected/);
 byLabel('Clear filter').click();await new Promise(r=>setTimeout(r,350));await tick();
 assert.equal(byLabel('Filter servers').value,'');assert.equal(w.document.querySelectorAll('.cfg-picker-flat tbody tr').length,57);
 button('← Back').click();await tick();button('← Keep MetaQuotes-Demo').click();await tick();
 assert.match($('.cfg-chosen').textContent,/MetaQuotes-Demo/);
 console.log('PASS: change server can go back; all-addresses view with clearable filter.');
 button('Clear all').click();await tick();
 assert.match($('.cfg-detail').textContent,/Select at least one address/);
 button('Select all').click();await tick();
 assert.equal(button('+ Add address').disabled,false);
 w.confirm=()=>true;$('[aria-label="Close window"]').click();await tick();assert.equal($('.fw'),null);
 console.log('PASS: 50 addresses accepted; closing the window with confirmation.');
 // Online broker directory: same picker, live results.
 button('New connection').click();await tick();
 assert.equal(button('Find servers').disabled,true);
 fill(byLabel('Find broker'),'VTMarkets');await tick();
 button('Find servers').click();await tick();await tick();await new Promise(r=>setTimeout(r,300));
 assert.deepEqual(searched,['VTMarkets']);
 assert.match($('.cfg-picker-head').textContent,/Broker directory · “VTMarkets”4 servers/);
 button('← Back').click();await tick();assert.equal($('.cfg-picker'),null);
 assert.equal(byLabel('Find broker').value,'VTMarkets');  // back keeps what was typed
 w.confirm=()=>true;$('[aria-label="Close window"]').click();await tick();
 console.log('PASS: broker directory search opens the same server picker.');

 // Account: readiness shown; password saved write-only
 nav('Accounts').click();await tick();await tick();
 button('New account').click();await tick();
 fill(byLabel('Connection'),conn.id);await tick();
 assert.match($('.cfg-detail').textContent,/Environment DEMO · server ICMarketsSC-Demo/);
 fill(byLabel('Broker login (MT5 account number)'),'51234567');await tick();
 assert.equal(byLabel('Account ID').value,'ICM_DEMO_51234567');
 fill(byLabel('Account holder'),'Yatheesh N S');fill(byLabel('Account name'),'Demo 1');await tick();
 fill(byLabel('Trading permission'),'TRADING');await tick();
 await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-blockers').textContent,/MT5 login password not saved/);
 assert.match($('.cfg-blockers').textContent,/Order sending is off for DEMO/);
 fill(byLabel('Password'),'master-secret');await tick();
 await submit($('.cfg-credential'));
 const put=requests.find(r=>r.url.endsWith('/credential'));assert.equal(JSON.parse(put.body).credential.password,'master-secret');
 assert.equal(byLabel('New password').value,'','secret cleared from the form after saving');
 assert.ok(!w.document.body.textContent.includes('master-secret'));
 assert.ok(!$('.cfg-blockers').textContent.includes('MT5 login password not saved'));
 console.log('PASS: account readiness explains blockers; credential is write-only and cleared after save.');

 // Account address choice: server's / fastest / own list; latency test recorded on the connection.
 assert.match($('.cfg-route').textContent,/This account will connect through/);
 assert.deepEqual([...w.document.querySelectorAll('.cfg-route-preview .cfg-mono')].map(x=>x.textContent),['192.0.2.11:444','192.0.2.10:443']);
 button('Test latency').click();await tick();await tick();
 assert.equal(latencyCalls.at(-1).connection_id,conn.id);
 assert.deepEqual([...w.document.querySelectorAll('.cfg-route-preview .cfg-latency')].map(x=>x.textContent),['12 ms','11 ms']);
 $('.cfg-route input[value="FASTEST"]').click();await tick();
 assert.deepEqual([...w.document.querySelectorAll('.cfg-route-preview .cfg-mono')].map(x=>x.textContent),['192.0.2.10:443','192.0.2.11:444']);
 $('.cfg-route input[value="ACCOUNT"]').click();await tick();
 byLabel('Account uses 192.0.2.11:444').click();await tick();
 await submit($('.cfg-detail .cfg-form'));
 const routed=JSON.parse(requests.filter(r=>r.method==='PUT'&&r.url.includes('/accounts/')).at(-1).body);
 assert.equal(routed.access_mode,'ACCOUNT');assert.deepEqual(routed.access_points,[{host:'192.0.2.10',port:443}]);
 $('.cfg-route input[value="SERVER"]').click();await tick();
 assert.match($('.cfg-route').textContent,/own choice \(1 address\) is kept/);
 $('.cfg-route input[value="ACCOUNT"]').click();await tick();
 assert.equal(byLabel('Account uses 192.0.2.10:443').checked,true);
 console.log('PASS: account chooses server addresses, fastest first or its own list; latency test recorded; own list kept when switching.');

 // Stale edit shows conflict
 db.accounts[0].version=5;fill(byLabel('Account name'),'Renamed');await tick();await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-detail').textContent,/Changed by someone else/);
 // Unsaved changes guard section switches
 w.confirm=()=>false;nav('Providers').click();await tick();
 assert.match($('.cfg-head h1').textContent,/Accounts/);w.confirm=()=>true;
 console.log('PASS: version conflict surfaced; unsaved changes block navigation until discarded.');

 // Add MT5 account in one step: login, password, server; existing connection reused.
 $('[aria-label="Close window"]').click();await tick();
 button('Add MT5 account').click();await tick();
 const addWin=$('.fw');assert.match(addWin.textContent,/Add MT5 account/);
 assert.equal(button('Add account').disabled,true);
 fill(byLabel('Provider'),db.providers[0].id);fill(byLabel('Environment'),'DEMO');await tick();
 fill(byLabel('Broker login'),'777001');fill(byLabel('Account holder'),'Coreeye Group Pvt Ltd');fill(byLabel('Password'),'setup-secret');await tick();
 const datFile=addWin.querySelector('.cfg-finder input[type="file"]');Object.defineProperty(datFile,'files',{value:[new w.File(['x'],'servers.dat')]});
 datFile.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();await new Promise(r=>setTimeout(r,300));
 fill(byLabel('Filter servers'),'icmarkets');await new Promise(r=>setTimeout(r,350));await tick();await tick();
 button('Use ICMarketsSC-Demo').click();await tick();
 assert.match(addWin.textContent,/Uses existing connection ICM_MT5_DEMO/);
 assert.match(addWin.textContent,/2 addresses of this server that it does not have yet will be added to it unticked/);
 $('.fw .cfg-route input[value="ACCOUNT"]').click();await tick();
 byLabel('Account uses 203.0.113.10:443').click();await tick();
 fill(byLabel('Account name'),'Firm gold');await tick();
 assert.equal(byLabel('Account ID').value,'ICM_DEMO_777001');
 await submit($('#cfg-add-mt5'));await tick();
 const sent=setups.at(-1);
 assert.equal(sent.connection_id,conn.id);assert.equal(sent.environment,'DEMO');
 assert.equal(sent.account.access_mode,'ACCOUNT');
 assert.deepEqual(sent.account.access_points,[{host:'192.0.2.11',port:444},{host:'192.0.2.10',port:443},{host:'203.0.113.10',port:443}]);
 assert.deepEqual(sent.server.access_points.map(p=>[p.host,p.enabled]),[['203.0.113.10',true],['10.30.210.41',false]]);
 assert.equal(sent.credential.password,'setup-secret');assert.equal(sent.server.server_name,'ICMarketsSC-Demo');
 assert.match($('.cfg-detail').textContent,/New addresses were added to the server unticked/);
 assert.ok(!w.document.body.textContent.includes('setup-secret'));
 $('[aria-label="Close window"]').click();await tick();
 console.log('PASS: Add MT5 account sends login, password, server and per-account addresses in one step; reuses the existing connection.');

 // Re-authentication: a sensitive action asks for the password again, then retries by itself.
 nav('Accounts').click();await tick();await tick();
 [...w.document.querySelectorAll('.cfg-table tbody tr')].find(r=>r.textContent.includes('ICM_DEMO_51234567')).click();await tick();await tick();
 needReauth=true;fill(byLabel('New password'),'rotated-secret');await tick();
 $('.cfg-credential').dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.ok($('[aria-label="Confirm with your password"]'),'password re-entry dialog shown');
 fill(byLabel('Your password'),'My-own-pass-993');await tick();
 await submit($('.cfg-modal form'));await tick();await tick();
 assert.equal(reauthCalls,1);assert.equal($('.cfg-modal'),null);
 assert.equal(JSON.parse(requests.filter(r=>r.url.endsWith('/credential')).at(-1).body).credential.password,'rotated-secret');
 assert.match($('.cfg-credential').textContent,/Saved and encrypted/);
 console.log('PASS: re-authentication dialog appears on 428 and the action is retried.');

 // Links between records and sortable lists.
 $('[aria-label="Close window"]').click();await tick();
 const chip=[...w.document.querySelectorAll('.cfg-table [data-nav^="connections:"]')][0];chip.click();await tick();await tick();await tick();
 assert.match($('.cfg-head h1').textContent,/Connections/);
 assert.match($('.fw-title').textContent,/Connection · ICM_MT5_DEMO/);
 $('[aria-label="Close window"]').click();await tick();
 nav('Accounts').click();await tick();await tick();
 [...w.document.querySelectorAll('.cfg-sort')].find(b=>b.textContent.startsWith('Account ID')).click();await tick();
 [...w.document.querySelectorAll('.cfg-sort')].find(b=>b.textContent.startsWith('Account ID')).click();await tick();
 assert.equal($('th[aria-sort]').getAttribute('aria-sort'),'descending');
 assert.deepEqual([...w.document.querySelectorAll('.cfg-table tbody tr td:first-child')].map(td=>td.textContent),['ICM_DEMO_51234567','ICM_DEMO_777001']);  // numeric-aware: 51234567 > 777001
 console.log('PASS: chips open the linked record in its section; lists sort by column.');

 // CSV import: checked first, then applied.
 button('Import CSV').click();await tick();
 fill(byLabel('CSV'),'connection_code,code,name,external_account_id,base_currency\nICM_MT5_DEMO,ICM_D2,Second,5002,USD');await tick();
 button('Check').click();await tick();await tick();
 assert.equal(importCalls.at(-1).dry_run,true);assert.match($('.fw').textContent,/All 1 rows are valid/);
 button('Import 1 accounts').click();await tick();await tick();
 assert.equal(importCalls.at(-1).dry_run,false);
 console.log('PASS: CSV import checks first and applies all-or-nothing.');

 // Four-eyes: a LIVE connection goes to Approvals; another person approves it.
 nav('Connections').click();await tick();await tick();
 button('New connection').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);fill(byLabel('Environment'),'LIVE');await tick();
 button('Enter server and addresses manually').click();await tick();
 fill(byLabel('Broker server name'),'ICMarketsSC-Live');fill(byLabel('Access point 1 host'),'198.51.100.7');await tick();
 await submit($('.cfg-detail .cfg-form'));await tick();
 assert.equal($('.fw'),null,'window closes; nothing created yet');
 assert.match($('.cfg-records').textContent,/Sent for approval: New LIVE connection ICM_MT5_LIVE adds address 198\.51\.100\.7:443/);
 assert.ok(!db.connections.some(c=>c.environment==='LIVE'));
 nav('Approvals').click();await tick();await tick();
 assert.match($('.cfg-requests').textContent,/create connection/);
 button('Approve and apply').click();await tick();await tick();
 assert.ok(db.connections.some(c=>c.environment==='LIVE'));
 assert.match($('.cfg-approvals').textContent,/Change applied/);
 console.log('PASS: LIVE change waits under Approvals and is applied by a second person.');

 // Order sending: confirmation required
 nav('Order sending').click();await tick();await tick();
 const live=[...w.document.querySelectorAll('.cfg-card')].find(c=>c.textContent.includes('LIVE'));
 [...live.querySelectorAll('button')].find(b=>b.textContent.startsWith('Turn on')).click();await tick();
 fill(live.querySelector('[aria-label="Reason"]'),'Go-live approved by risk');await tick();
 const turnOn=[...live.querySelectorAll('button')].find(b=>b.textContent.includes('order sending on'));
 assert.ok(turnOn.disabled,'disabled until environment typed');
 fill(live.querySelector('[aria-label="Confirm environment"]'),'LIVE');await tick();assert.equal(turnOn.disabled,false);
 await submit(live.querySelector('form'));
 assert.equal(controls.LIVE.trading_enabled,true);
 assert.match($('.cfg-nav').textContent,/LIVE ON/);
 console.log('PASS: LIVE order sending needs reason and typed confirmation; status visible in navigation.');

 // Users (administrator): create a user with roles.
 nav('Users').click();await tick();await tick();
 assert.match($('.cfg-head h1').textContent,/Users/);
 assert.match($('.cfg-table').textContent,/risk-ops \(you\)/);
 button('New user').click();await tick();
 fill(byLabel('Username'),'Olga');fill(byLabel('Display name'),'Olga Observer');fill(byLabel('Temporary password'),'Temp-pass-4821');
 byLabel('Trading operations').click();await tick();
 await submit($('.fw .cfg-form'));
 assert.deepEqual(users[1].roles.sort(),['OBSERVER','TRADING_OPS']);
 // Own roles cannot be changed.
 w.confirm=()=>true;$('[aria-label="Close window"]').click();await tick();
 [...w.document.querySelectorAll('.cfg-table tbody tr')][0].click();await tick();
 assert.ok(byLabel('Administrator').matches(':disabled'));
 console.log('PASS: administrator manages users and roles; own roles locked.');

 // An observer sees configuration read-only and no Users section.
 session.roles=['OBSERVER'];$('[aria-label="Close window"]').click();await tick();
 [...w.document.querySelectorAll('.cfg-user .link')].find(b=>b.textContent==='Sign out').click();await tick();
 const signin2=$('.cfg-signin form');fill(signin2.querySelector('[autocomplete="username"]'),'olga');fill(signin2.querySelector('[type="password"]'),'x');await submit(signin2);
 assert.ok(![...w.document.querySelectorAll('.cfg-nav button')].some(b=>b.textContent.includes('Users')));
 nav('Providers').click();await tick();await tick();
 assert.equal(button('New provider'),undefined);
 $('.cfg-table tbody tr').click();await tick();
 assert.ok($('.fw fieldset.cfg-plain').disabled);assert.equal(button('Save changes'),undefined);
 $('[aria-label="Close window"]').click();await tick();
 nav('Order sending').click();await tick();await tick();
 assert.ok(![...w.document.querySelectorAll('.cfg-card button')].length);
 console.log('PASS: observer is read-only: no create, save, credential or order-sending actions.');

 assert.deepEqual(errors,[]);
 console.log('PASS: configuration workspace has no runtime errors.');
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
