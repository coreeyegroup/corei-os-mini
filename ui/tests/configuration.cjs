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
 let signedIn=false;const db={providers:[],connections:[],accounts:[],instruments:[],mappings:[]};
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
   if(url.endsWith('/operator/session'))return signedIn?ok({username:'risk-ops'}):fail(401,'Sign in');
   if(url.endsWith('/operator/login')){signedIn=true;return ok({username:'risk-ops'});}
   const u=new URL(url,'https://x');const parts=u.pathname.replace('/api/config/','').split('/');
   if(parts[0]==='summary')return ok({counts:Object.fromEntries(Object.entries(db).map(([k,v])=>[k,v.length])),execution_controls:Object.values(controls),credential_storage_ready:true,available_adapters:[]});
   if(parts[0]==='execution-controls'){
     if(method==='GET')return ok({items:Object.values(controls)});
     const body=JSON.parse(options.body);const c=controls[parts[1]];
     if(body.trading_enabled&&body.confirm_environment!==parts[1])return fail(422,'confirm');
     Object.assign(c,{trading_enabled:body.trading_enabled,reason:body.reason,version:c.version+1,updated_by:'risk-ops'});return ok(c);}
   if(parts[0]==='audit')return ok({items:[]});
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
 const button=text=>[...w.document.querySelectorAll('button')].find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();};

 // Sign in
 assert.match(w.document.body.textContent,/Sign in/);
 const signin=$('.cfg-signin form');fill(signin.querySelector('[autocomplete="username"]'),'risk-ops');fill(signin.querySelector('[type="password"]'),'secret-pass');
 await submit(signin);
 assert.match($('.cfg-nav').textContent,/risk-ops/);
 assert.match($('.cfg-nav').textContent,/Order sending/);
 console.log('PASS: operator sign-in and section navigation.');

 // Provider: create, duplicate rejected with backend message
 button('New provider').click();await tick();
 fill(byLabel('Provider code'),'icm');fill(byLabel('Name'),'IC Markets');await tick();
 await submit($('.cfg-form:not(.cfg-signin .cfg-form)'));
 assert.equal(db.providers.length,1);assert.equal(db.providers[0].code,'ICM');
 assert.match($('.cfg-table').textContent,/IC Markets/);
 button('New provider').click();await tick();fill(byLabel('Provider code'),'ICM');fill(byLabel('Name'),'Again');await tick();
 await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-detail').textContent,/already exists/);
 button('✕')?.click?.();await tick();
 console.log('PASS: provider create, upper-case code, duplicate shows backend message.');

 // Connection: MT5 with ordered access points
 $('.cfg-nav button:nth-child(2)').click();await tick();await tick();
 button('New connection').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);fill(byLabel('Connection code'),'icm_mt5_demo');fill(byLabel('Name'),'ICM MT5 demo');
 fill($('.cfg-subform input[maxlength="120"]'),'ICMarketsSC-Demo');
 fill(byLabel('Access point 1 name'),'Access Point 1');fill(byLabel('Access point 1 host'),'192.0.2.10');await tick();
 button('Add access point').click();await tick();
 fill(byLabel('Access point 2 name'),'Access Point 2');fill(byLabel('Access point 2 host'),'192.0.2.11');fill(byLabel('Access point 2 port'),'444');await tick();
 [...w.document.querySelectorAll('[aria-label="Move up"]')][1].click();await tick();
 await submit($('.cfg-detail .cfg-form'));
 const conn=db.connections[0];
 assert.equal(conn.platform,'MT5');assert.equal(conn.environment,'DEMO');
 assert.deepEqual(conn.settings.access_points.map(p=>[p.host,p.port]),[['192.0.2.11',444],['192.0.2.10',443]]);
 assert.ok(byLabel('Platform').disabled,'platform fixed after creation');
 console.log('PASS: MT5 connection with ordered access points; platform fixed after creation.');

 // Account: readiness shown; password saved write-only
 $('.cfg-nav button:nth-child(3)').click();await tick();await tick();
 button('New account').click();await tick();
 fill(byLabel('Connection'),conn.id);await tick();
 assert.match($('.cfg-detail').textContent,/DEMO from the connection/);
 fill(byLabel('Account code'),'icm_demo_1');fill(byLabel('Name'),'Demo 1');fill(byLabel('MT5 login (account number)'),'51234567');
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
 db.accounts[0].version=5;fill(byLabel('Name'),'Renamed');await tick();await submit($('.cfg-detail .cfg-form'));
 assert.match($('.cfg-detail').textContent,/Changed by someone else/);
 // Unsaved changes guard section switches
 w.confirm=()=>false;$('.cfg-nav button:nth-child(1)').click();await tick();
 assert.match($('.cfg-head h1').textContent,/Accounts/);w.confirm=()=>true;
 console.log('PASS: version conflict surfaced; unsaved changes block navigation until discarded.');

 // Order sending: confirmation required
 $('.cfg-nav button:nth-child(6)').click();await tick();await tick();
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

 assert.deepEqual(errors,[]);
 console.log('PASS: configuration workspace has no runtime errors.');
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
