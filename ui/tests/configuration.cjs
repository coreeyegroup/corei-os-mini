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
 const users=[{id:'u1',username:'risk-ops',display_name:'risk-ops',roles:['ADMIN','OBSERVER','RISK_APPROVER','TRADING_OPS'],status:'ACTIVE',must_change_password:false,version:1,active_sessions:1}];const db={providers:[],connections:[],accounts:[],instruments:[],mappings:[]};
 const controls={DEMO:{environment:'DEMO',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()},
                  LIVE:{environment:'LIVE',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()}};
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
       {server_name:'ICMarketsSC-Demo',company_name:'Raw Trading Ltd',details:{CompanyName:'Raw Trading Ltd',TimeZone:120},access_points:[{name:'Access Point 1',host:'203.0.113.10',port:443}]},
       {server_name:'MarketEquityInc-Demo',company_name:'Market Equity Inc.',details:{CompanyName:'Market Equity Inc.'},access_points:[{name:'Access Point 1',host:'192.109.15.68',port:443},{name:'Access Point 2',host:'192.109.15.68',port:1950}]},
       {server_name:'Marketmates-Live',company_name:'Marketmates Ltd',details:{CompanyName:'Marketmates Ltd'},access_points:[{name:'Access Point 1',host:'142.91.121.204',port:1950},{name:'Access Point 2',host:'142.91.121.204',port:443},{name:'Access Point 3',host:'5.181.78.117',port:1950}]},
       {server_name:'MetaQuotes-Demo',company_name:'MetaQuotes Ltd.',details:{CompanyName:'MetaQuotes Ltd.',TimeZone:120,DST:1,Description:'MetaTrader 5 demo server operated by MetaQuotes Ltd. for testing and evaluation'},
        access_points:Array.from({length:50},(_,i)=>({name:'Access Point '+(i+1),host:i<25?'78.140.180.'+(i+1):'2a00:1178:1:60::'+(i+1).toString(16),port:i%2?1950:443}))}];
     if(method==='POST')return ok({catalog_id:'cat1',server_count:servers.length,access_point_count:servers.reduce((n,s)=>n+s.access_points.length,0),skipped_servers:0,invalid_addresses:0,servers_without_access_points:0,unreadable_extended_records:2});
     if(u.pathname.endsWith('/server'))return ok(servers.find(s=>s.server_name===u.searchParams.get('name')));
     const b=(u.searchParams.get('broker')||'').toLowerCase().split(/\s+/).filter(Boolean),q=(u.searchParams.get('q')||'').toLowerCase().split(/\s+/).filter(Boolean);
     const rows=servers.flatMap(s=>s.access_points.map(a=>({server_name:s.server_name,company_name:s.company_name,...a})))
       .filter(r=>b.every(t=>(r.server_name+' '+r.company_name).toLowerCase().includes(t))&&q.every(t=>`${r.host}:${r.port} ${r.name}`.toLowerCase().includes(t)));
     return ok({total:rows.length,server_matches:new Set(rows.map(r=>r.server_name)).size,offset:0,items:rows.slice(0,200)});}
   const entity=parts[0];
   if(parts[2]==='credential'){const body=JSON.parse(options.body);const row=db[entity].find(x=>x.id===parts[1]);
     row.credential_kind=body.credential.kind;row.credential_access_level=body.credential.access_level;row.credential_updated_at=new Date().toISOString();
     return ok({kind:body.credential.kind,access_level:body.credential.access_level});}
   if(method==='GET'&&parts.length===1)return ok({items:db[entity].map(r=>enrich(entity,r))});
   if(method==='POST'){const body=JSON.parse(options.body);
     if(entity==='providers'&&db.providers.some(p=>p.code===body.code.toUpperCase()))return fail(409,'A provider with this code already exists.');
     const row={...body,id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,version:1,updated_at:new Date().toISOString(),...(body.code?{code:body.code.toUpperCase()}:{})};
     db[entity].push(row);return ok(enrich(entity,row),201);}
   if(method==='PUT'){const body=JSON.parse(options.body);const row=db[entity].find(x=>x.id===parts[1]);
     if(body.version!==row.version)return fail(409,'Changed by someone else');Object.assign(row,body,{version:row.version+1});return ok(enrich(entity,row));}
   throw Error('Unexpected request '+method+' '+url);
 };

 w.eval(js);await tick();await tick();
 const $=s=>w.document.querySelector(s);
 const fill=(el,value)=>{const proto=el.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
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
 console.log('PASS: operator sign-in, forced password change, roles shown, section navigation.');

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

 // Connection: MT5 with ordered access points
 nav('Connections').click();await tick();await tick();
 button('New connection').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 assert.equal(byLabel('Connection ID').value,'ICM_MT5_DEMO');  // suggested from provider, platform, environment
 fill(byLabel('Connection name'),'ICM MT5 demo');
 fill($('.cfg-subform input[maxlength="120"]'),'ICMarketsSC-Demo');
 fill(byLabel('Access point 1 name'),'Access Point 1');fill(byLabel('Access point 1 host'),'192.0.2.10');await tick();
 button('Add access point').click();await tick();
 fill(byLabel('Access point 2 name'),'Access Point 2');fill(byLabel('Access point 2 host'),'192.0.2.11');fill(byLabel('Access point 2 port'),'444');await tick();
 [...w.document.querySelectorAll('[aria-label="Move up"]')][1].click();await tick();
 // The editor is a floating window: keyboard move/resize, maximise/restore, all eight resize edges.
 const win=$('.fw');assert.equal(win.getAttribute('role'),'dialog');assert.equal(win.querySelectorAll('.fw-edge').length,8);
 const left0=parseFloat(win.style.left),width0=parseFloat(win.style.width);
 $('.fw-title').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));await tick();assert.equal(parseFloat($('.fw').style.left),left0-16);
 $('.fw-title').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowLeft',shiftKey:true,bubbles:true}));await tick();assert.equal(parseFloat($('.fw').style.width),width0-16);
 $('[aria-label="Maximise window"]').click();await tick();assert.ok($('.fw').classList.contains('fw-max'));
 $('[aria-label="Restore window"]').click();await tick();assert.ok(!$('.fw').classList.contains('fw-max'));
 assert.match($('.fw-caption').textContent,/Unsaved/);
 w.confirm=()=>false;$('.fw').dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();assert.ok($('.fw'),'Esc keeps window when discard is declined');w.confirm=()=>true;
 await submit($('.cfg-detail .cfg-form'));
 const conn=db.connections[0];
 assert.equal(conn.platform,'MT5');assert.equal(conn.environment,'DEMO');
 assert.deepEqual(conn.settings.access_points.map(p=>[p.host,p.port]),[['192.0.2.11',444],['192.0.2.10',443]]);
 assert.ok(byLabel('Platform').disabled,'platform fixed after creation');
 console.log('PASS: MT5 connection with ordered access points; floating window moves, resizes, maximises; platform fixed after creation.');
 // A broker server with 50 access points (MetaQuotes-Demo) can be entered.
 button('New connection').click();await tick();
 // Analyze servers.dat: every access point of every broker in one table, with broker and address filters.
 const fileInput=$('.cfg-subform input[type="file"]');Object.defineProperty(fileInput,'files',{value:[new w.File(['x'],'servers.dat')]});
 fileInput.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();await new Promise(r=>setTimeout(r,300));
 assert.match($('.cfg-picker').textContent,/4 servers · 56 access points/);
 assert.match($('.cfg-picker').textContent,/2 extended access records could not be read/);
 assert.equal(w.document.querySelectorAll('.cfg-analyser tbody tr').length,56);
 fill(byLabel('Broker filter'),'market');await new Promise(r=>setTimeout(r,350));await tick();
 assert.match($('.cfg-picker').textContent,/6 access points on 3 servers match/);  // ICMarketsSC-Demo contains "market" too
 assert.deepEqual([...w.document.querySelectorAll('.cfg-analyser tbody tr td:nth-child(2)')].map(t=>t.textContent),['203.0.113.10:443','192.109.15.68:443','192.109.15.68:1950','142.91.121.204:1950','142.91.121.204:443','5.181.78.117:1950']);
 byLabel('Select 192.109.15.68:1950 on MarketEquityInc-Demo').click();await tick();
 byLabel('Select 142.91.121.204:443 on Marketmates-Live').click();await tick();
 assert.match($('.cfg-picker').textContent,/Selection switched to Marketmates-Live/);
 assert.match($('.cfg-picker').textContent,/1 selected on Marketmates-Live/);
 fill(byLabel('Broker filter'),'metaquotes');fill(byLabel('Address filter'),'1950');await new Promise(r=>setTimeout(r,350));await tick();
 assert.equal(w.document.querySelectorAll('.cfg-analyser tbody tr').length,25);
 const mqRows=[...w.document.querySelectorAll('.cfg-analyser tbody tr')];
 mqRows[3].click();await tick();
 assert.match($('.cfg-picker').textContent,/Selection switched to MetaQuotes-Demo/);
 mqRows[0].click();await tick();await tick();
 assert.match($('.cfg-picker').textContent,/MetaTrader 5 demo server operated by MetaQuotes Ltd\. for testing and evaluation/);
 assert.match($('.cfg-picker').textContent,/Time zone120/);
 button('Use 2 selected access points').click();await tick();
 assert.equal($('.cfg-picker'),null);
 assert.equal(byLabel('Broker server name').value,'MetaQuotes-Demo');
 assert.match($('[aria-label="Server description"]').textContent,/CompanyMetaQuotes Ltd\./);
 assert.match($('.cfg-points-head').textContent,/2 of 50 selected/);
 assert.deepEqual([byLabel('Access point 1 host').value,byLabel('Access point 2 host').value],['78.140.180.8','78.140.180.2']);
 button('Select all').click();await tick();
 // Search access points, select only the matching ones.
 fill(byLabel('Search access points'),'1950');await tick();
 assert.equal(w.document.querySelectorAll('.cfg-points tbody tr').length,25);
 button('Clear shown').click();await tick();
 assert.match($('.cfg-points-head').textContent,/25 of 50 selected/);
 fill(byLabel('Search access points'),'');await tick();
 byLabel('Use access point 3').click();await tick();  // a port-443 row, still selected
 assert.match($('.cfg-points-head').textContent,/24 of 50 selected/);
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 assert.equal(byLabel('Connection ID').value,'ICM_MT5_DEMO_2');  // ICM_MT5_DEMO is taken
 fill(byLabel('Connection ID'),'mt5001');fill(byLabel('Connection name'),'MetaQuotes demo');await tick();
 await submit($('.cfg-detail .cfg-form'));
 const mq=db.connections.find(c=>c.code==='MT5001');
 assert.equal(mq.settings.access_points.length,50);
 assert.equal(mq.settings.access_points.filter(p=>p.enabled).length,24);
 assert.equal(mq.settings.company_name,'MetaQuotes Ltd.');
 assert.match(mq.settings.server_details.Description,/demo server/);
 console.log('PASS: servers.dat picker searches and shows full server description; access points searchable and selectable; selection saved.');
 button('Clear all').click();await tick();
 assert.match($('.cfg-detail').textContent,/Select at least one access point/);
 button('Select all').click();await tick();
 assert.equal(button('Add access point').disabled,false);
 w.confirm=()=>true;$('[aria-label="Close window"]').click();await tick();assert.equal($('.fw'),null);
 console.log('PASS: 50 access points accepted; closing the window with confirmation.');

 // Account: readiness shown; password saved write-only
 nav('Accounts').click();await tick();await tick();
 button('New account').click();await tick();
 fill(byLabel('Connection'),conn.id);await tick();
 assert.match($('.cfg-detail').textContent,/DEMO from the connection/);
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

 // Stale edit shows conflict
 db.accounts[0].version=5;fill(byLabel('Account name'),'Renamed');await tick();await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-detail').textContent,/Changed by someone else/);
 // Unsaved changes guard section switches
 w.confirm=()=>false;nav('Providers').click();await tick();
 assert.match($('.cfg-head h1').textContent,/Accounts/);w.confirm=()=>true;
 console.log('PASS: version conflict surfaced; unsaved changes block navigation until discarded.');

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
