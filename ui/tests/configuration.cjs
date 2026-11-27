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
 w.confirm=()=>true;const purges=[];

 // ---- mock backend ----
 let signedIn=false;const session={roles:['ADMIN','OBSERVER','RISK_APPROVER','TRADING_OPS'],must:true};
 const users=[{id:'u1',username:'risk-ops',display_name:'risk-ops',roles:['ADMIN','OBSERVER','RISK_APPROVER','TRADING_OPS'],status:'ACTIVE',must_change_password:false,version:1,active_sessions:1}];const db={providers:[],connections:[],accounts:[],instruments:[],mappings:[],groups:[]};const prefs={};const approvals=[];const importCalls=[];let needReauth=false,reauthCalls=0;
 const controls={DEMO:{environment:'DEMO',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()},
                  LIVE:{environment:'LIVE',trading_enabled:false,reason:'Initial state: off',version:1,updated_by:'system',updated_at:new Date().toISOString()}};
 const searched=[],latencyCalls=[],setups=[],upstoxSetups=[],venues=[],mt5Calls=[],bnodes=[],bnCalls=[];
 const requests=[];let n=0;
 const ok=(body,status=200)=>({ok:true,status,json:async()=>body});
 const fail=(status,detail)=>({ok:false,status,json:async()=>({detail})});
 const enrich=(entity,r)=>{
   if(entity==='connections'){const p=db.providers.find(x=>x.id===r.provider_id);return {...r,provider_code:p.code,account_count:db.accounts.filter(a=>a.connection_id===r.id).length};}
   if(entity==='accounts'){const c=db.connections.find(x=>x.id===r.connection_id);const blockers=[...(r.credential_kind?[]:['ACCOUNT_CREDENTIAL_MISSING']),'ADAPTER_NOT_AVAILABLE'];
     return {...r,platform:c.platform,environment:c.environment,connection_code:c.code,provider_code:'ICM',data_blockers:blockers,
       trading_blockers:[...blockers,...(controls[c.environment].trading_enabled?[]:['ORDER_SENDING_OFF_'+c.environment])]};}
   return r;};
const intClients=[],intPosts=[],bearerCalls=[],modePuts=[],resets=[];
let pm={mode:'PRODUCTION',allow_live_in_development:false,live_blocked:false,password_min:6,reset_allowed:false,version:1,updated_by:'system'};
const setupOverrides=[],setupPolicyPuts=[],paperRows=[],paperPosts=[],paperFunds=[],fxRows=[],paperOrders=[],paperCloses=[];
const gate=(id,step,name,state,level,items)=>({id,step,name,unlocks:'Orders',level,state,items,open_items:state==='DONE'?[]:items,overrides:[],goto:{view:'Configuration',section:'accounts'}});
const setupStatus=()=>({checked_at:'2026-11-26T00:00:00Z',pending:null,overrides:[],
  policy:{preset:'RECOMMENDED',custom:{},target_track:'DEMO',default_hours:24,max_hours:{PAPER:168,DEMO:168,LIVE:24},
    levels:{PAPER:{PEOPLE:'OFF',ACCOUNTS:'WARN',SYMBOLS:'SOFT',MARKET_DATA:'WARN',EVIDENCE:'WARN'},DEMO:{PEOPLE:'WARN',ACCOUNTS:'SOFT',SYMBOLS:'SOFT',MARKET_DATA:'SOFT',RISK:'SOFT',EVIDENCE:'SOFT',PAPER_RECORD:'WARN'},
      LIVE:{PEOPLE:'SUPERVISED',ACCOUNTS:'HARD',SYMBOLS:'HARD',MARKET_DATA:'SUPERVISED',RISK:'SUPERVISED',EVIDENCE:'SUPERVISED',PAPER_RECORD:'SUPERVISED'}}},
  tracks:{PAPER:{done:5,total:5,complete:true,next:null,gates:[gate('ACCOUNTS',1,'Brokers and accounts','DONE','WARN',[])]},
    DEMO:{done:1,total:3,complete:false,next:{gate:'RISK',name:'Risk',text:'ICM_DEMO has no MAX_DAILY_LOSS limit (firm or account).',fix:'Risk › Limits › Size limits from a profile.',goto:{view:'Risk',section:'limits'}},
      gates:[gate('ACCOUNTS',1,'Brokers and accounts','DONE','SOFT',[]),
        gate('RISK',2,'Risk','BLOCKED','SOFT',[{gate:'RISK',text:'ICM_DEMO has no MAX_DAILY_LOSS limit (firm or account).',fix:'Risk › Limits',account_id:'acc-1',account:'ICM_DEMO',goto:{view:'Risk',section:'limits'}}]),
        gate('ORDER_SENDING',3,'Order sending','OPEN','INFO',[{gate:'ORDER_SENDING',text:'DEMO order sending is off.',fix:'Settings › Order sending',goto:{view:'Configuration',section:'controls'}}])]},
    LIVE:{done:0,total:1,complete:false,next:null,gates:[gate('PEOPLE',1,'People and alerts','BLOCKED','SUPERVISED',[{gate:'PEOPLE',text:'Only 1 person can approve.',fix:'Add a second approver.',goto:{view:'Configuration',section:'users'}}])]}}});
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';requests.push({url,method,body:options.body});
   if(url.endsWith('/config/platform-mode')&&method==='PUT'){const b=JSON.parse(options.body);modePuts.push(b);
     if(b.mode==='PRODUCTION'&&pm.mode!=='PRODUCTION'&&b.confirm!=='PRODUCTION')return {ok:false,status:422,json:async()=>({detail:'To switch to Production, confirm by typing PRODUCTION.'})};
     const dev=b.mode==='DEVELOPMENT',allow=dev&&b.allow_live_in_development;
     pm={...pm,mode:b.mode,allow_live_in_development:allow,live_blocked:dev&&!allow,password_min:dev?2:6,reset_allowed:dev,version:pm.version+1,updated_by:'risk-ops',reason:b.reason};
     return ok({...pm,live_switched_off:dev&&!allow});}
   if(url.endsWith('/config/platform-mode/reset-test-data')){resets.push(JSON.parse(options.body));return ok({reset:[{old:'PAPER_USD',new:'PAPER_USD_R2'}],skipped:[{code:'PAPER_RUN',why:'PAPER_RUN still has 1 active run(s).'}]});}
   if(url.endsWith('/config/platform-mode'))return ok(pm);
   if(url.includes('/api/v1/automation/openapi.json'))return ok({paths:{'/api/v1/automation/auth/test':{get:{'x-required-scopes':[]}},
     '/api/v1/automation/market-data/snapshot':{post:{summary:'Market snapshot','x-required-scopes':['market_data.snapshot'],requestBody:{content:{'application/json':{}}}}}}});
   if(url.includes('/api/v1/automation/')){bearerCalls.push({url,headers:options.headers||{},credentials:options.credentials});
     if(url.endsWith('/auth/test'))return ok({authenticated:true,connection:'core-ai',key_prefix:'ABCDEFGHJK',scopes:['system.read','signals.propose']});
     if(url.endsWith('/capabilities'))return ok({scopes:[{scope:'system.read',granted:true},{scope:'orders.live_request',granted:false}]});
     return ok({total:3,items:[]});}
   if(url.endsWith('/config/integrations/catalog'))return ok({scopes:[{scope:'system.read',group:'System',label:'Platform health',status:'AVAILABLE',note:''},
     {scope:'signals.propose',group:'Signals',label:'Submit candidate signals',status:'AVAILABLE',note:''},
     {scope:'orders.paper',group:'Orders',label:'Place paper orders',status:'AVAILABLE',note:''},
     {scope:'orders.live_request',group:'Orders',label:'Request LIVE orders',status:'UNAVAILABLE',note:'Not enabled.'}],
     profiles:{CORE_AI:{scopes:['system.read','signals.propose']},N8N:{scopes:['system.read']}},events:{'job.completed':'A job finished','signal.created':'A signal'}});
   if(/\/config\/integrations\/ic-1\/keys$/.test(url)&&method==='POST'){const k={id:'k1',prefix:'ABCDEFGHJK',state:'ACTIVE',label:'issued',created_at:'2026-11-27T00:00:00Z',created_by:'risk-ops'};
     intClients[0].keys=[k];return ok({...k,key:'corei_ABCDEFGHJK_'+'x'.repeat(43)},201);}
   if(/\/config\/integrations\/ic-1\/(webhooks|usage|signals)$/.test(url))return ok({items:[],deliveries:[],recent:[],by_endpoint_24h:[]});
   if(url.endsWith('/config/integrations')&&method==='POST'){const b=JSON.parse(options.body);intPosts.push(b);
     intClients.push({...b,id:'ic-1',version:1,keys:[],usage_24h:{requests:0},jobs:{running:0,failed:0},allowed_networks:b.allowed_networks});return ok(intClients[0],201);}
   if(url.endsWith('/config/integrations'))return ok({items:intClients,overview:{connections:intClients.length,active:intClients.length,disabled:0,requests_24h:0}});
   if(url.endsWith('/trading/paper-accounts')&&method==='POST'){const b=JSON.parse(options.body);paperPosts.push(b);const a={id:'pa-'+paperRows.length,version:1,status:'ACTIVE',starting_amount:b.amount,equity:b.amount,return_pct:'0.00',drawdown_pct:'0.00',complete:true,missing_fx:[],open_positions:0,runs:0,fills:0,...b,commission_per_lot:b.commission_per_lot,asset_classes:b.asset_classes,markets:b.markets,instruments:b.instruments};paperRows.push(a);return ok(a,201);}
   if(/\/trading\/paper-accounts\/[^/]+\/funds$/.test(url)){const b=JSON.parse(options.body);paperFunds.push(b);paperRows[0].starting_amount=b.amount;return ok(paperRows[0]);}
   if(/\/trading\/paper-accounts\/pa-0$/.test(url))return ok({...paperRows[0],created_at:'2026-11-20T04:30:00Z',balance:'2500012.50',realised:'12.50',unrealised:'-3.10',exposure:'24000.00',leverage:'0.01',
     positions:[{instrument_id:'ins-nifty',symbol:'NIFTY',asset_class:'INDEX',quote_currency:'INR',quantity:'1',avg_price:'24000',mark:'23996.9',unrealised_account:'-3.1',notional_account:'23996.9',valuation:'OK'}],
     orders:[],fills:[{id:'f1',at:'2026-11-21T05:00:00Z',side:'SELL',quantity:'1',price:'24020',realised_pnl:'20',commission:'7.5',currency:'INR',symbol:'NIFTY'}],
     performance:{closed_trades:1,wins:1,losses:0,win_rate_pct:'100.00',gross_profit:'20.00',gross_loss:'0.00',profit_factor:null,best:'20.00',worst:null,average:'20.00',costs:'7.50'},
     equity_curve:[{at:'2026-11-20T04:30:00Z',equity:'2500000'},{at:'2026-11-21T05:00:00Z',equity:'2500012.50'},{at:null,equity:'2500009.40'}],runs_list:[],activity:[{occurred_at:'2026-11-20T04:30:00Z',actor:'risk-ops',action:'CREATE',entity:'account',reason:'New paper account'}]});
   if(url.endsWith('/trading/orders')&&method==='POST'){const b=JSON.parse(options.body);paperOrders.push(b);return ok({id:'o-'+paperOrders.length,state:'FILLED'},201);}
   if(url.endsWith('/trading/portfolio/flatten')&&method==='POST'){paperCloses.push(JSON.parse(options.body));return ok({state:'FILLED'});}
   if(url.includes('/trading/paper-accounts'))return ok({items:paperRows,currencies:['USD','INR','EUR']});
   if(url.endsWith('/trading/fx-rates')&&method==='PUT'){const b=JSON.parse(options.body);fxRows.push({...b,set_at:'2026-11-26T00:00:00Z',set_by:'risk-ops'});return ok(fxRows.at(-1));}
   if(url.includes('/trading/fx-rates'))return ok({items:fxRows});
   if(url.endsWith('/trading/setup/overrides')&&method==='POST'){const b=JSON.parse(options.body);setupOverrides.push(b);return ok({id:'ov1',...b,approved_at:'2026-11-26T00:00:00Z',expires_at:'2026-11-27T00:00:00Z',requested_by:'risk-ops'},201);}
   if(url.endsWith('/trading/setup/policy')&&method==='PUT'){setupPolicyPuts.push(JSON.parse(options.body));return ok({pending:null});}
   if(url.includes('/trading/setup/landing'))return ok({target_track:'DEMO',land_on_setup:true,done:3,total:8,overrides:0,next:null});
   if(url.includes('/trading/setup'))return ok(setupStatus());
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   const me={username:'risk-ops',display_name:'risk-ops',roles:session.roles,must_change_password:session.must};
   if(url.endsWith('/operator/session'))return signedIn?ok(me):fail(401,'Sign in');
   if(url.endsWith('/operator/login')){signedIn=true;return ok(me);}
   if(url.endsWith('/operator/logout')){signedIn=false;return ok({signed_out:true});}
   if(url.endsWith('/operator/password')){session.must=false;return ok({changed:true});}
   if(url.includes('/operator/prefs')){if(method==='PUT'){prefs[decodeURIComponent(url.split('/prefs/')[1])]=JSON.parse(options.body).value;return ok({saved:true});}return ok(prefs);}
   if(url.endsWith('/operator/reauth')){reauthCalls++;needReauth=false;return ok({reauthenticated:true,minutes:5});}
   if(url.includes('/config/bridge-nodes')){const body=options.body?JSON.parse(options.body):{};bnCalls.push({url,method,body});
     if(method==='POST'&&url.endsWith('/config/bridge-nodes')){const nd={id:'bn'+(bnodes.length+1),...body,status:'PENDING',accounts:0,version:1,bridge_version:'',port:9443,tunnel_port:body.mode==='TUNNEL'?9450:null};bnodes.push(nd);
       return ok({...nd,instructions:{code:'ABCDE-FGHJK-LMNPQ-RSTUV',code_minutes:60,steps:[{title:'Open PowerShell as Administrator'},{title:'Download the installer',command:'Invoke-WebRequest https://x/api/bridge-enroll/files/install-corei-bridge.ps1 -OutFile C:\\corei-setup\\install-corei-bridge.ps1'},
         {title:'Install and register this machine',command:'powershell -ExecutionPolicy Bypass -File C:\\corei-setup\\install-corei-bridge.ps1 -Corei https://x -Code ABCDE-FGHJK-LMNPQ-RSTUV'}]}},201);}
     if(url.endsWith('/attach'))return ok({added:['ICM_LIVE_1'],already:[]});
     const one=bnodes.find(x=>url.endsWith('/'+x.id));if(one)return ok(one);
     return ok({items:bnodes,latest_bridge:'0.42.0',corei_url:'https://x'});}
   if(url.endsWith('/config/health'))return ok({generated_at:new Date().toISOString(),network:{egress_ip:'203.0.113.200',clock_synchronised:true,server_time_utc:'2026-11-11T10:00:00Z'},
     controls:Object.values(controls),four_eyes_live_changes:true,pending_approvals:approvals.length,failed_alerts:0,
     checklist:[{step:'1 Broker',how:'Add a provider (broker)',done:db.providers.length>0,section:'brokers'},{step:'2 Account',how:'Add an account (MT5: login, password and server)',done:db.accounts.length>0,section:'brokers'}],
     connections:db.connections.map(r=>enrich('connections',r)),accounts:db.accounts.map(r=>enrich('accounts',r)),
     summary:{accounts:db.accounts.length,data_ready:0,trading_ready:0,with_warnings:0}});
   if(url.endsWith('/config/tree')){const kinds={BROKER:'Broker',EXCHANGE:'Exchange',ECN:'ECN',FAST_FEED:'Fast feed',DATA_VENDOR:'Data vendor',DATA_CENTRE:'Data centre'};
     const conns=db.connections.map(c=>({...enrich('connections',c),technology:{MT5:'MT5',MT4:'MT4',CTRADER:'CTRADER',FIX:'FIX',REST_API:'API'}[c.platform],accounts:db.accounts.filter(a=>a.connection_id===c.id).map(a=>enrich('accounts',a))}));
     const tech=(id,label,platform,state,k)=>{const who=db.providers.map(p=>({provider:p,connections:conns.filter(c=>c.technology===id&&c.provider_id===p.id)})).filter(x=>x.connections.length);
       return {id,label,platform,state,kinds:k,who_hint:'who',account:'login',summary:label+' summary',adapter_details:[{id:id+'_A',label:label+' adapter',data:state,orders:'PLANNED',how:'how',credential:'login',examples:'ex'}],
         who,counts:{who:who.length,connections:who.reduce((n,x)=>n+x.connections.length,0),accounts:who.reduce((n,x)=>n+x.connections.reduce((m,c)=>m+c.accounts.length,0),0)}};};
     const used=new Set(conns.map(c=>c.provider_id));
     return ok({technologies:[tech('MT5','MetaTrader 5','MT5','BUILT',['BROKER']),tech('MT4','MetaTrader 4','MT4','PLANNED',['BROKER']),tech('CTRADER','cTrader','CTRADER','PLANNED',['BROKER']),tech('FIX','FIX 4.4 / 4.2','FIX','BUILT',['ECN','BROKER','FAST_FEED']),tech('API','Broker & exchange APIs','REST_API','PARTIAL',['BROKER']),tech('FEED','Fast feeds & data vendors',null,'PLANNED',['FAST_FEED'])],
       api_presets:[{id:'UPSTOX',label:'Upstox API',state:'BUILT',api_base_url:'https://api.upstox.com',auth_method:'OAUTH2',account_label:'Upstox client ID (UCC)',daily_login:true},
         {id:'ANGELONE',label:'Angel One SmartAPI',state:'PLANNED',api_base_url:'https://apiconnect.angelone.in',auth_method:'API_KEY',account_label:'Angel One client code',daily_login:true}],
       providers:db.providers,connections:conns,unconnected:db.providers.filter(p=>!used.has(p.id)),provider_kinds:kinds,roles:{},summary:{accounts:db.accounts.length,data_ready:0,trading_ready:0,with_warnings:0}});}
   if(url.endsWith('/config/venues')){const body=JSON.parse(options.body);venues.push(body);
     let p=db.providers.find(x=>x.id===body.provider_id);
     if(!p){p={...body.provider,id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,status:'ACTIVE',version:1};db.providers.push(p);}
     const c={...body.connection,provider_id:p.id,code:p.code+'_FIX_DEMO',name:p.name+' FIX',status:'ACTIVE',id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,version:1,updated_at:new Date().toISOString()};
     db.connections.push(c);return ok({provider_action:body.provider?'CREATED':'REUSED',provider:p,connection:enrich('connections',c)},201);}
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
   if(parts[2]==='delete-permanently'){const body=JSON.parse(options.body);purges.push({entity:parts[0],...body});
     const list=db[parts[0]];const i=list.findIndex(x=>x.id===parts[1]);const code=list[i].code;
     if(body.confirm!==code)return fail(422,'Type the code');list.splice(i,1);return ok({code,removed:{'collected prices':12,accounts:1}});}
   if(parts[0]==='summary')return ok({counts:Object.fromEntries(Object.entries(db).map(([k,v])=>[k,v.length])),execution_controls:Object.values(controls),credential_storage_ready:true,available_adapters:[]});
   if(parts[0]==='execution-controls'){
     if(method==='GET')return ok({items:Object.values(controls)});
     const body=JSON.parse(options.body);const c=controls[parts[1]];
     if(body.trading_enabled&&body.confirm_environment!==parts[1])return fail(422,'confirm');
     Object.assign(c,{trading_enabled:body.trading_enabled,reason:body.reason,version:c.version+1,updated_by:'risk-ops'});return ok(c);}
   if(parts[0]==='audit')return ok({items:[]});
   if(parts[0]==='upstox'&&parts[1]==='account-setup'){const body=JSON.parse(options.body);upstoxSetups.push(body);
     const c=db.connections[0];const row={...body.account,id:`00000000-0000-4000-8000-${String(++n).padStart(12,'0')}`,connection_id:c.id,version:1,updated_at:new Date().toISOString(),routing:null};
     return ok({provider_action:'CREATED',connection_action:'CREATED',connection:{...enrich('connections',c),code:'UPSTOX_API_LIVE'},account:enrich('accounts',row)},201);}
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
   if(parts[0]==='accounts'&&parts[2]==='mt5-latency'){mt5Calls.push('latency');return ok(enrich('accounts',db.accounts.find(a=>a.id===parts[1])));}
   if(parts[0]==='accounts'&&parts[2]==='mt5-addresses'){mt5Calls.push('refresh');const a=db.accounts.find(x=>x.id===parts[1]);
     return ok({server_name:'ICMarketsSC-Demo',source:'DIRECTORY',published:3,added:1,awaiting_approval:0,change_request:null,not_listed:[{host:'203.0.113.10',port:443}],account:enrich('accounts',a)});}
   if(parts[0]==='accounts'&&parts[2]==='mt5-server'){const body=JSON.parse(options.body);mt5Calls.push('move:'+body.server.server_name);
     const a=db.accounts.find(x=>x.id===parts[1]);a.version++;return ok({connection_action:'CREATED',account:enrich('accounts',a),connection:enrich('connections',db.connections.find(c=>c.id===a.connection_id))});}
   const entity=parts[0];
   if(parts[2]==='latency')return ok({items:[]});
   if(parts[2]==='credential'&&needReauth)return fail(428,'Re-enter your password to confirm this action.');
   if(parts[2]==='credential'&&method==='DELETE'){
     const connection=db.connections.find(c=>c.id===parts[1]);connection.credential_kind=null;
     return ok(null,204);
   }
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
 const shownView=()=>[...w.document.querySelectorAll('.group.focused .view > div:not([hidden])')][0]||w.document;
 const shownItem=()=>shownView().querySelector('.sw-pane.focused .sw-item:not([hidden])');
 const shown=()=>shownView();
 const $=s=>(shownItem()&&shownItem().querySelector(s))||shown().querySelector(s)||w.document.querySelector(s);
 const fill=(el,value)=>{const proto=el.tagName==='SELECT'?w.HTMLSelectElement.prototype:el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const byLabel=l=>$(`.cfg-detail [aria-label="${l}"]`)||$(`[aria-label="${l}"]`);
 const nav=label=>vis('.cfg-nav button').find(b=>b.querySelector('span').textContent===label);
 const tab=label=>vis('.cfg-views button').filter(x=>!x.closest('[hidden]')).find(b=>b.querySelector('span').textContent===label);
 // The three record lists are tabs of the Connect screen (Connectivity): open it, then the tab.
 const open2=async label=>{nav('Connectivity map').click();await tick();await tick();tab(label).click();await tick();await tick();};
 const vis=sel=>[...w.document.querySelectorAll(sel)].filter(x=>!x.closest('[hidden]'));
 const visibleFirst=sel=>{const all=[...w.document.querySelectorAll(sel)];return [...all.filter(x=>!x.closest('[hidden]')),...all.filter(x=>x.closest('[hidden]'))];};
 const button=text=>visibleFirst('button').find(b=>b.textContent.trim()===text);
 // The one Add account form: provider, technology (and API), then Continue (MT5, Upstox) or fill in here.
 const startAdd=async(techLabel,apiId,go=true)=>{button('Add account').click();await tick();await tick();await tick();
   fill($('#cfg-add-account [aria-label="Provider"]'),db.providers[0].id);await tick();await tick();
   vis('#cfg-add-account [role=radio]').find(b=>b.querySelector('b').textContent===techLabel).click();await tick();
   if(apiId){fill(byLabel('Which API'),apiId);await tick();}
   if(go){vis('.fw button.primary').find(b=>b.textContent.startsWith('Continue')).click();await tick();await tick();}};
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
 const qa=sel=>[...w.document.querySelectorAll(sel)];
 assert.match($('.cfg-head h1').textContent,/Setup path/);  // opens on the guided path
 assert.match($('[aria-label="Setup path"]').textContent,/Next step · Risk: ICM_DEMO has no MAX_DAILY_LOSS/);
 assert.match($('[aria-label="Track"]').textContent,/Broker DEMO 1\/3 · target/);
 assert.match($('.su-steps').textContent,/✕2 · Risk.*soft.*blocked/);
 assert.deepEqual([...qa('.cfg-nav .cfg-nav-group')].map(g=>g.textContent),['1 · Setup','2 · Connect','3 · Markets','4 · Risk','5 · Strategies','6 · Run','7 · Settings','8 · Integrate','9 · Admin']);
 const beforeOv=requests.filter(r=>r.method!=='GET').length;
 w.prompt=()=>'ok';[...qa('.su-items button')].find(b=>/override for ICM_DEMO only/.test(b.textContent)).click();await tick();await tick();
 assert.equal(requests.filter(r=>r.method!=='GET').length,beforeOv,'a too-short reason asks nothing');
 assert.match($('[aria-label="Setup path"]').textContent,/a reason of at least 5 characters/);
 w.prompt=()=>'limits sized after the open';[...qa('.su-items button')].find(b=>/override for ICM_DEMO only/.test(b.textContent)).click();await tick();await tick();
 assert.deepEqual(setupOverrides[0],{gate:'RISK',track:'DEMO',reason:'limits sized after the open',account_id:'acc-1'},'scoped to one gate, one track, one account');
 [...qa('.su-tracks button')].find(b=>/LIVE/.test(b.textContent)).click();await tick();await tick();
 assert.ok([...qa('.su-step button')].some(b=>b.textContent==='Override…'),'a supervised gate can be asked to override');
 nav('Gates & overrides').click();await tick();await tick();await tick();
 assert.match($('[aria-label="Gate levels"]').textContent,/Brokers and accounts.*Hard · no override/);
 fill($('[aria-label="RISK LIVE"]'),'HARD');await tick();
 w.prompt=()=>'tighten live risk';[...qa('[aria-label="Gate levels"] button')].find(b=>b.textContent==='Save levels').click();await tick();await tick();
 assert.equal(setupPolicyPuts[0].levels.LIVE.RISK,'HARD');assert.equal(setupPolicyPuts[0].reason,'tighten live risk');
 console.log('PASS: setup path: next step, eight ordered menus, scoped override with a reason, gate levels saved with a reason.');
 // Paper accounts: one per idea, own currency and amount, Indian brokerage as % of value; FX rate for INR valuation.
 // Paper accounts live in Portfolio; Setup's entry opens them there.
 nav('Paper accounts ↗').click();for(let i=0;i<8;i++)await tick();
 assert.ok($('[aria-label="Paper accounts"]'),'Setup › Paper accounts opens Portfolio › Paper accounts');
 assert.match($('.stage-head').textContent,/Portfolio intelligence.*Paper accounts/);
 assert.match($('[aria-label="Paper accounts"]').textContent,/No paper account yet/);
 [...w.document.querySelectorAll('[aria-label="Paper accounts"] button')].find(b=>/New paper account/.test(b.textContent)).click();await tick();
 const pf=$('[aria-label="New paper account"]');
 const inp=l=>pf.querySelector(`[aria-label="${l}"]`);
 fill(pf.querySelector('input[placeholder="PAPER_USD_1"]'),'paper_inr');fill(pf.querySelector('input[placeholder="Forex practice"]'),'NSE practice');
 fill(inp('Currency'),'inr');fill(inp('Starting amount'),'1000000');fill(inp('Scope'),'INDIA');fill(inp('Commission percent'),'0.03');fill(inp('Minimum commission'),'20');fill(inp('Risk limits'),'PROFESSIONAL');await tick();
 await submit(pf);await tick();
 assert.deepEqual([paperPosts[0].code,paperPosts[0].currency,paperPosts[0].amount,paperPosts[0].scope,paperPosts[0].commission_pct,paperPosts[0].min_commission,paperPosts[0].risk_profile],
   ['PAPER_INR','INR','1000000','INDIA','0.03','20','PROFESSIONAL'],'currency, amount, markets, costs and limits as typed text');
 assert.match($('[aria-label="Paper accounts"]').textContent,/PAPER_INR created with 1000000 INR/);
 assert.match($('.pa-table').textContent,/INR.*PAPER_INR.*1,000,000\.00.*Indian markets/,'compact row: currency tag, code, grouped amount, markets');
 w.prompt=()=>'2500000';[...w.document.querySelectorAll('.pa-actions button')].find(b=>b.textContent==='Funds…').click();await tick();await tick();
 assert.equal(paperFunds[0].amount,'2500000');
 // The account opens like a broker account: balance, equity, positions with Close, history, performance, and a paper-only ticket.
 db.instruments.push({id:'ins-nifty',symbol:'NIFTY',name:'Nifty 50',asset_class:'INDEX',market:'NSE',quote_currency:'INR',contract_size:'1'});
 [...qa('.pa-actions button')].find(b=>b.textContent==='Open').click();await tick();await tick();await tick();
 const pd=$('[aria-label="Paper account PAPER_INR"]');assert.ok(pd,'account page opens');
 assert.match(pd.querySelector('[aria-label="Account summary"]').textContent,/Balance2,500,012\.50 INR.*Open P&L-3\.10 INR.*Exposure24,000\.00 INR.*Win rate100\.00%/);
 assert.match(pd.querySelector('.pd-table').textContent,/NIFTY.*LONG.*24,000.*23,996\.9.*-3\.10/);
 w.prompt=()=>'take the loss';[...pd.querySelectorAll('.pd-table button')].find(b=>b.textContent==='Close').click();await tick();await tick();
 assert.deepEqual([paperCloses[0].mode,paperCloses[0].instrument_id,paperCloses[0].reason],['PAPER','ins-nifty','take the loss'],'close is a paper order with a reason');
 [...pd.querySelectorAll('[role="tab"]')].find(b=>/History/.test(b.textContent)).click();await tick();
 assert.match(pd.querySelector('[role="tabpanel"]').textContent,/NIFTY.*SELL.*24,020.*7\.5 INR.*20 INR/);
 [...pd.querySelectorAll('[role="tab"]')].find(b=>/Performance/.test(b.textContent)).click();await tick();
 assert.ok(pd.querySelector('[aria-label="Equity curve"] svg path'),'equity curve drawn');assert.match(pd.querySelector('.pd-stats').textContent,/Costs paid7\.50 INR/);
 const tk=pd.querySelector('[aria-label="Order ticket"]');fill(tk.querySelector('[aria-label="Instrument"]'),'ins-nifty');fill(tk.querySelector('[aria-label="Quantity"]'),'2');await tick();
 [...tk.querySelectorAll('[role="radio"]')].find(b=>b.textContent==='Sell').click();await tick();
 w.confirm=()=>true;await submit(tk);await tick();await tick();
 assert.deepEqual([paperOrders[0].mode,paperOrders[0].side,paperOrders[0].quantity,paperOrders[0].instrument_id,paperOrders[0].account_id],['PAPER','SELL','2','ins-nifty','pa-0'],'ticket sends a paper order, quantity as typed text');
 assert.match(paperOrders[0].ticket_key,/^[A-Za-z0-9_-]{8,64}$/);
 [...pd.querySelectorAll('button')].find(b=>/Paper accounts/.test(b.textContent)).click();await tick();await tick();
 assert.ok($('.pa-table'),'back to the list');
 w.document.querySelector('[aria-label="Close Portfolio in group 1"]').click();for(let i=0;i<6;i++)await tick();
 if(!w.document.querySelector('.cfg-nav'))w.document.querySelector('nav.activity [aria-label="Configuration"]').click();for(let i=0;i<4;i++)await tick();
 nav('FX rates').click();await tick();await tick();
 const fxf=$('[aria-label="Set a rate"]');fill(fxf.querySelector('[aria-label="Rate"]'),'84.25');fill(fxf.querySelector('[aria-label="Rate note"]'),'RBI reference');await tick();
 await submit(fxf);await tick();
 assert.deepEqual(fxRows[0].base+fxRows[0].quote+fxRows[0].rate,'USDINR84.25');
 assert.match($('[aria-label="FX rates"]').textContent,/USD\/INR.*84\.25.*RBI reference/);
 console.log('PASS: paper accounts: own currency, amount, Indian markets, % brokerage, limits; funds; account page with positions, close, history, performance, paper ticket; FX rate for INR valuation.');
 nav('Readiness per account').click();await tick();await tick();await tick();
 assert.match($('.cfg-health').textContent,/203\.0\.113\.200/);
 assert.match($('.cfg-health').textContent,/Next: 1 Broker/);
 console.log('PASS: operator sign-in, forced password change, roles shown, section navigation.');
 await open2('Providers');

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
 await open2('Servers');
 button('New server (advanced)').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 assert.equal(byLabel('Connection ID').value,'ICM_MT5_DEMO');  // suggested from provider, platform, environment
 assert.equal(byLabel('Connection name').value,'IC Markets (Raw Trading Ltd) · MT5 demo');  // name suggested too
 assert.deepEqual(vis('.cfg-section h4').map(h=>h.textContent),['1Broker','2Server and addresses','3Name','4Method, clock and limits']);
 button('Enter server and addresses manually').click();await tick();
 fill(byLabel('Connection name'),'ICM MT5 demo');
 fill(byLabel('Broker server name'),'ICMarketsSC-Demo');
 fill(byLabel('Access point 1 host'),'192.0.2.10');await tick();
 button('+ Add address').click();await tick();
 fill(byLabel('Access point 2 host'),'192.0.2.11');fill(byLabel('Access point 2 port'),'444');await tick();
 vis('[aria-label="Move up"]')[1].click();await tick();
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
 button('New server (advanced)').click();await tick();
 const fileInput=$('.cfg-finder input[type="file"]');Object.defineProperty(fileInput,'files',{value:[new w.File(['x'],'servers.dat')]});
 fileInput.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();await new Promise(r=>setTimeout(r,300));
 assert.match($('.cfg-picker').textContent,/servers\.dat4 servers · 57 addresses/);
 assert.equal(w.document.querySelectorAll('.cfg-picker-servers [role="option"]').length,4);
 fill(byLabel('Filter servers'),'market');await new Promise(r=>setTimeout(r,350));await tick();
 assert.deepEqual(vis('.cfg-picker-name').map(x=>x.textContent),['ICMarketsSC-Demo','MarketEquityInc-Demo','Marketmates-Live']);
 fill(byLabel('Filter servers'),'142.91');await new Promise(r=>setTimeout(r,350));await tick();await tick();
 assert.match($('.cfg-picker-server').textContent,/Marketmates-Live/);  // a single match opens itself
 fill(byLabel('Filter servers'),'icmarkets');await new Promise(r=>setTimeout(r,350));await tick();await tick();
 // Private addresses are shown but not pre-selected.
 assert.equal(byLabel('Select 203.0.113.10:443 on ICMarketsSC-Demo').checked,true);
 assert.equal(byLabel('Select 10.30.210.41:443 on ICMarketsSC-Demo').checked,false);
 assert.match($('.cfg-picker-points').textContent,/Private/);
 fill(byLabel('Filter servers'),'');await new Promise(r=>setTimeout(r,350));await tick();
 vis('.cfg-picker-servers [role="option"]').find(b=>b.textContent.includes('MetaQuotes-Demo')).click();await tick();await tick();
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
 vis('[role="tab"]').find(b=>b.textContent==='All addresses').click();await new Promise(r=>setTimeout(r,350));await tick();
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
 button('New server (advanced)').click();await tick();
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
 await open2('Accounts');
 button('New account (advanced)').click();await tick();
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
 assert.deepEqual(vis('.cfg-route-preview .cfg-mono').map(x=>x.textContent),['192.0.2.11:444','192.0.2.10:443']);
 button('Test latency').click();await tick();await tick();
 assert.equal(latencyCalls.at(-1).connection_id,conn.id);
 // New accounts default to fastest: the measured order applies at once.
 assert.deepEqual(vis('.cfg-route-preview .cfg-latency').map(x=>x.textContent),['11 ms','12 ms']);
 $('.cfg-route input[value="SERVER"]').click();await tick();
 assert.deepEqual(vis('.cfg-route-preview .cfg-mono').map(x=>x.textContent),['192.0.2.11:444','192.0.2.10:443']);
 $('.cfg-route input[value="FASTEST"]').click();await tick();
 assert.deepEqual(vis('.cfg-route-preview .cfg-mono').map(x=>x.textContent),['192.0.2.10:443','192.0.2.11:444']);
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
 w.confirm=()=>false;nav('Instruments').click();await tick();
 assert.match($('.cfg-head h1').textContent,/Accounts/);w.confirm=()=>true;
 console.log('PASS: version conflict surfaced; unsaved changes block navigation until discarded.');

 // Add MT5 account in one step: login, password, server; existing connection reused.
 $('[aria-label="Close window"]').click();await tick();
 await startAdd('MetaTrader 5');
 const addWin=$('.fw');assert.match(addWin.textContent,/Add MT5 account/);
 assert.equal($('.fw button.primary[form="cfg-add-mt5"]').disabled,true);
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
 assert.equal(sent.adapter,undefined,'an existing connection keeps its method');assert.equal(sent.server.bridge,undefined);
 assert.match($('.cfg-detail').textContent,/New addresses were added to the server unticked/);
 assert.ok(!w.document.body.textContent.includes('setup-secret'));
 $('[aria-label="Close window"]').click();await tick();
 console.log('PASS: Add MT5 account sends login, password, server and per-account addresses in one step; reuses the existing connection.');

 // A new server: the direct SDK is the default; choosing the terminal bridge (fallback) needs host, fingerprint and token before Add is enabled.
 await startAdd('MetaTrader 5');
 const addWin2=$('.fw');
 fill(byLabel('Provider'),db.providers[0].id);await tick();
 fill(byLabel('Broker login'),'777002');fill(byLabel('Account name'),'Bridge test');await tick();
 button('Enter server and addresses manually').click();await tick();
 fill(byLabel('Broker server name'),'VTMarkets-Live 3');fill(byLabel('Access point 1 host'),'198.51.100.7');await tick();
 assert.match(addWin2.textContent,/A new connection will be created/);
 assert.equal(byLabel('Connection method').value||$('.fw .cfg-method input:checked')?.value,'MTAPI_DIRECT');
 fill(byLabel('Connection method'),'TERMINAL_BRIDGE');await tick();
 assert.ok($('.fw button.primary[form="cfg-add-mt5"]').disabled,'bridge details missing: cannot add yet');
 fill(byLabel('Bridge host'),'mt5-vps.example.net');fill(byLabel('Certificate fingerprint'),Array(32).fill('AB').join(':'));fill(byLabel('Bridge token'),'tok-123');await tick();
 assert.ok(!$('.fw button.primary[form="cfg-add-mt5"]').disabled);
 await submit($('#cfg-add-mt5'));await tick();
 const sent2=setups.at(-1);
 assert.equal(sent2.adapter,'TERMINAL_BRIDGE');assert.equal(sent2.bridge_token,'tok-123');
 assert.deepEqual(sent2.server.bridge,{host:'mt5-vps.example.net',port:9443,fingerprint:Array(32).fill('AB').join(':')});
 assert.ok(!w.document.body.textContent.includes('tok-123'));
 $('[aria-label="Close window"]').click();await tick();
 console.log("PASS: a new MT5 server defaults to the direct SDK; the terminal bridge fallback's host, fingerprint and token travel with the one-step set-up.");

 // Add Upstox account in one step: client ID, app key and secret; redirect URL shown to copy.
 await startAdd('Broker & exchange APIs','UPSTOX');
 const upWin=$('.fw');assert.match(upWin.textContent,/Add Upstox account/);
 assert.match(byLabel('Redirect URL').value,/\/api\/oauth\/upstox\/callback$/);
 fill(byLabel('Upstox client ID'),'7ab123');fill(byLabel('API key'),'app-key');fill(byLabel('API secret'),'app-secret');await tick();
 assert.equal(byLabel('Account ID').value,'UPSTOX_7AB123');
 await submit($('#cfg-add-upstox'));await tick();
 const up=upstoxSetups.at(-1);assert.equal(up.account.external_account_id,'7AB123');assert.equal(up.account.trading_permission,'READ_ONLY');
 assert.deepEqual(up.credential,{client_id:'app-key',client_secret:'app-secret'});
 assert.ok(!w.document.body.textContent.includes('app-secret'));
 $('[aria-label="Close window"]').click();await tick();
 console.log('PASS: Add Upstox account sends client ID, app key and secret in one step.');

 // The same Add account form for other technologies: an Angel One API account and an MT4 account, each with a new server and its login.
 await startAdd('Broker & exchange APIs','ANGELONE',false);
 assert.ok(vis('#cfg-add-account [role=radio]').some(b=>b.querySelector('b').textContent==='cTrader'),'cTrader offered');
 assert.ok(vis('#cfg-add-account [role=radio]').find(b=>b.querySelector('b').textContent==='Fast feeds & data vendors').disabled,'feeds: planned, not recordable');
 fill($('#cfg-add-account [aria-label="Environment"]'),'DEMO');fill(byLabel('Account number or ID'),'A123456');await tick();
 assert.equal(byLabel('Account ID').value,'ICM_DEMO_A123456');assert.equal(byLabel('Account currency').value,'INR');
 fill(byLabel('Server'),'new');await tick();
 assert.equal(byLabel('API base URL').value,'https://apiconnect.angelone.in');
 fill(byLabel('API key'),'ang-key');fill(byLabel('API secret'),'ang-secret');await tick();
 assert.match($('#cfg-add-account').textContent,/connector is planned: the account, its server and login are recorded now/);
 const before=venues.length;await submit($('#cfg-add-account'));await tick();await tick();await tick();
 assert.equal(venues.length,before+1);assert.equal(venues.at(-1).connection.platform,'REST_API');assert.equal(venues.at(-1).connection.settings.api_base_url,'https://apiconnect.angelone.in');
 const ang=db.accounts.find(a=>a.external_account_id==='A123456');assert.ok(ang,'account created on the new server');
 assert.deepEqual(JSON.parse(requests.filter(r=>r.url.endsWith('/credential')).at(-1).body).credential,{kind:'API_KEY',api_key:'ang-key',api_secret:'ang-secret'});
 assert.ok(!w.document.body.textContent.includes('ang-secret'));
 const deleteKey=button('Delete API key');
 const credentialDeletes=()=>requests.filter(r=>r.method==='DELETE'&&r.url.endsWith('/credential'));
 const deleteCount=credentialDeletes().length;
 w.confirm=()=>false;deleteKey.click();await tick();
 assert.equal(credentialDeletes().length,deleteCount);
 w.confirm=()=>true;deleteKey.click();await tick();await tick();
 assert.equal(credentialDeletes().at(-1).url,'/api/config/connections/'+ang.connection_id+'/credential');
 assert.ok(!vis('button').some(b=>b.textContent==='Delete API key'));
 $('[aria-label="Close window"]')?.click();await tick();
 await startAdd('MetaTrader 4',null,false);
 fill($('#cfg-add-account [aria-label="Environment"]'),'DEMO');fill(byLabel('Account number or ID'),'700123');fill(byLabel('Server'),'new');await tick();
 fill(byLabel('MT4 server name'),'ICMarkets-Live07');fill(byLabel('MT4 server addresses'),'198.51.100.21:443');fill(byLabel('Password'),'mt4-pass');await tick();
 await submit($('#cfg-add-account'));await tick();await tick();await tick();
 assert.equal(venues.at(-1).connection.platform,'MT4');assert.deepEqual(venues.at(-1).connection.settings.access_points.map(p=>p.host),['198.51.100.21']);
 const mt4=db.accounts.find(a=>a.external_account_id==='700123');assert.ok(mt4);
 assert.deepEqual(JSON.parse(requests.filter(r=>r.url.endsWith('/credential')).at(-1).body).credential,{kind:'MT5_LOGIN',password:'mt4-pass',access_level:'READ_ONLY'});
 $('[aria-label="Close window"]')?.click();await tick();
 console.log('PASS: one Add account form: provider › technology › account › server › login for API (Angel One), MT4 and the dedicated MT5 / Upstox forms.');

 // A FIX data source: platform FIX, session settings, role Data source; the record lists show the role.
 await open2('Servers');
 button('New server (advanced)').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);fill(byLabel('Platform'),'FIX');await tick();
 assert.deepEqual(vis('.fw .cfg-section h4').map(h=>h.textContent).slice(0,2),['1Broker','2FIX session']);
 fill(byLabel('Environment'),'DEMO');fill(byLabel('FIX host'),'fix.example.net');fill(byLabel('FIX port'),'5201');
 fill(byLabel('SenderCompID'),'COREI');fill(byLabel('TargetCompID'),'FASTFEED');fill(byLabel('TargetSubID'),'QUOTE');
 fill(byLabel('Role'),'MARKET_DATA');fill(byLabel('Connection name'),'Fast feed FIX');fill(byLabel('Connection ID'),'FAST_FIX_DEMO');await tick();
 await submit($('.fw form'));await tick();
 const fixRow=db.connections.find(c=>c.code==='FAST_FIX_DEMO');
 assert.equal(fixRow.platform,'FIX');assert.equal(fixRow.purpose,'MARKET_DATA');assert.equal(fixRow.adapter,'NATIVE');
 assert.deepEqual([fixRow.settings.host,fixRow.settings.port,fixRow.settings.sender_comp_id,fixRow.settings.target_comp_id,fixRow.settings.target_sub_id,fixRow.settings.begin_string],
   ['fix.example.net',5201,'COREI','FASTFEED','QUOTE','FIX.4.4']);
 assert.match($('.cfg-detail').textContent,/FIX session login/,'the FIX login is saved on the connection');
 assert.ok(vis('.cfg-table tbody tr').some(r=>r.textContent.includes('FAST_FIX_DEMO')&&r.textContent.includes('Data source')));
 $('[aria-label="Close window"]')?.click();await tick();
 console.log('PASS: a FIX connection is created with its session settings and the Data source role.');
 // Connectivity tree: technology › who › server › account; add a who (new ECN + FIX session) in one step; add an account on a server.
 nav('Connectivity map').click();await tick();await tick();await tick();
 const rows=()=>vis('.ct-row');const row=t=>rows().find(r=>r.querySelector('.ct-label').textContent===t);
 // Default: provider › account › server; the overview shows the six steps; + Provider opens the one provider form.
 assert.match($('.ct-head').textContent,/Provider › account › server/);
 assert.deepEqual(vis('.ct-flow b').map(b=>b.textContent),['1 · Provider','2 · Account','3 · Server','4 · Login','5 · Test','6 · Collect']);
 vis('.ct-tools button').find(b=>b.textContent==='+ Provider').click();await tick();await tick();await tick();
 assert.match($('.fw-title').textContent,/New provider/,'the same form as Providers › New provider');
 assert.equal(w.document.querySelectorAll('[aria-label="Provider name"]').length,1,'one provider form only');
 fill(byLabel('Provider name'),'Exness Global');fill(byLabel('Regulator'),'FSA');await tick();
 byLabel('MetaTrader 5').click();byLabel('MetaTrader 4').click();await tick();
 await submit($('.cfg-detail .cfg-form'));await tick();await tick();
 const exn=db.providers.find(p=>p.name==='Exness Global');
 assert.ok(exn&&exn.code==='EXNESS_GLOBAL'&&exn.regulator==='FSA');assert.deepEqual(exn.technologies,['MT5','MT4'],'technologies offered saved');
 assert.match($('.cfg-detail').textContent,/Next · step 2: Add account at Exness Global \(choose its technology: MT5, MT4 offered\)/);
 vis('.cfg-detail .cfg-link').find(b=>b.textContent==='show in the tree').click();await tick();await tick();await tick();await tick();
 assert.match($('.ct-detail').textContent,/Step 1 · Provider/,'the tree opens on the new provider');
 assert.match($('.ct-detail').textContent,/Next step: Add account — choose its technology \(MT5, MT4 offered\)/);
 assert.ok(row('Exness Global'),'listed in the provider tree');
 vis('.ct-seg button').find(b=>b.textContent==='Technology').click();await tick();
 assert.deepEqual(rows().filter(r=>r.classList.contains('ct-tech')).map(r=>r.querySelector('.ct-label').textContent),['MetaTrader 5','MetaTrader 4','cTrader','FIX 4.4 / 4.2','Broker & exchange APIs','Fast feeds & data vendors']);
 assert.ok(row('MetaTrader 4').classList.contains('planned'));
 row('FIX 4.4 / 4.2').click();await tick();
 assert.match($('.ct-detail').textContent,/TechnologyFIX 4.4 \/ 4.2/);
 vis('.ct-actions button').find(b=>b.textContent==='Add FIX 4.4 / 4.2 account').click();await tick();await tick();await tick();
 assert.match($('.fw-title').textContent,/Add account/,'the one Add account form, technology preset');
 assert.equal(byLabel('Provider name'),null,'no second provider form');
 fill($('#cfg-add-account [aria-label="Provider"]'),db.providers[0].id);await tick();await tick();
 assert.equal($('#cfg-add-account [role=radio][aria-checked=true] b').textContent,'FIX 4.4 / 4.2');
 fill($('#cfg-add-account [aria-label="Environment"]'),'DEMO');fill(byLabel('Account number or ID'),'LMX-1');fill(byLabel('Server'),'new');await tick();
 fill(byLabel('FIX host'),'fix-md.example.net');fill(byLabel('SenderCompID'),'COREI');fill(byLabel('TargetCompID'),'LMXBDM');await tick();
 assert.equal(byLabel('Role').value,'MARKET_DATA','FIX defaults to a data source');
 await submit($('#cfg-add-account'));await tick();await tick();await tick();
 const v=venues.at(-1);
 assert.equal(v.provider_id,db.providers[0].id);assert.equal(v.provider,undefined);
 assert.equal(v.connection.platform,'FIX');assert.equal(v.connection.settings.port,443);assert.equal(v.connection.settings.target_comp_id,'LMXBDM');
 const fixAcct=db.accounts.find(x=>x.external_account_id==='LMX-1');assert.ok(fixAcct,'FIX account on its new session');
 $('[aria-label="Close window"]')?.click();await tick();
 nav('Connectivity map').click();await tick();await tick();
 vis('[aria-label="Expand all"]')[0].click();await tick();
 rows().find(r=>r.classList.contains('ct-conn')&&r.querySelector('.ct-label').textContent==='fix-md.example.net:443').click();await tick();
 vis('.ct-actions button').find(b=>b.textContent==='Add account').click();await tick();await tick();await tick();
 assert.equal($('#cfg-add-account [aria-label="Server"]').value,fixAcct.connection_id,'Add account opens on that server');
 $('[aria-label="Close window"]')?.click();await tick();
 nav('Connectivity map').click();await tick();await tick();
 vis('.ct-seg button').find(b=>b.textContent==='Provider type').click();await tick();
 vis('[aria-label="Expand all"]')[0].click();await tick();
 assert.ok(row('Broker')&&row(db.providers[0].name),'grouped by provider type');
 vis('.ct-seg button').find(b=>b.textContent==='Technology').click();await tick();
 console.log('PASS: Connectivity: one provider form with technologies offered and a next step; tree lists every technology; a FIX account and its session through Add account; Add account opens on a chosen server.');
 // MT5: provider › server › account, like every technology; the account has its route (fastest by default), measure, refresh, change server.
 vis('[aria-label="Expand all"]')[0].click();await tick();
 const mt5Acct=db.accounts.find(a=>db.connections.find(c=>c.id===a.connection_id)?.platform==='MT5');
 const acctRow=rows().find(r=>r.classList.contains('ct-acct')&&r.querySelector('.ct-label').textContent===mt5Acct.external_account_id);
 assert.ok(acctRow,'MT5 account listed in the tree');
 const serverRow=rows().find(r=>r.classList.contains('ct-conn')&&r.querySelector('.ct-label').textContent==='ICMarketsSC-Demo');
 assert.ok(serverRow,'the server the account uses is shown under it');
 assert.ok(rows().indexOf(acctRow)<rows().indexOf(serverRow)&&Number(serverRow.getAttribute('aria-level'))===Number(acctRow.getAttribute('aria-level'))+1,'provider › account › server');
 acctRow.click();await tick();await tick();
 assert.match($('.ct-steps').textContent,/4 · Login saved.*5 · Login tested.*6 · Collecting/);
 assert.match($('.ct-route').textContent,/Server & routeMeasure nowRefresh from brokerChange server…/);
 vis('.ct-route button').find(b=>b.textContent==='Measure now').click();await tick();await tick();
 vis('.ct-route button').find(b=>b.textContent==='Refresh from broker').click();await tick();await tick();
 assert.match($('.ct-route [role=status]').textContent,/3 address\(es\) published by the broker for ICMarketsSC-Demo; 1 new added and measured; 1 on record no longer listed/);
 vis('.ct-route button').find(b=>b.textContent==='Change server…').click();await tick();
 fill(byLabel('New server name'),'ICMarketsSC-Demo02');fill(byLabel('New server addresses'),'203.0.113.77:443');await tick();
 vis('.ct-route button').find(b=>b.textContent==='Move to ICMarketsSC-Demo02').click();await tick();await tick();
 assert.deepEqual(mt5Calls,['latency','refresh','move:ICMarketsSC-Demo02']);
 // MT5 technology: Add account opens the one form with MT5 chosen; it continues in the MT5 form.
 row('MetaTrader 5').click();await tick();
 vis('.ct-actions button').find(b=>b.textContent==='Add MetaTrader 5 account').click();await tick();await tick();await tick();
 assert.match($('.fw').textContent,/Add account/);assert.match($('.fw').textContent,/MT5 continues in its form/);
 $('[aria-label="Close window"]')?.click();await tick();
 console.log('PASS: provider › account › server: each MT5 account shows the server it uses; route, measure, refresh and change server work from the account; a broker goes straight on to its first account.');

 // Re-authentication: a sensitive action asks for the password again, then retries by itself.
 await open2('Accounts');
 vis('.cfg-table tbody tr').find(r=>r.textContent.includes('ICM_DEMO_51234567')).click();await tick();await tick();
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
 const chip=vis('.cfg-table [data-nav^="connections:"]')[0];chip.click();await tick();await tick();await tick();
 assert.match($('.cfg-head h1').textContent,/Servers/);
 assert.match($('.fw-title').textContent,/Server · ICM_MT5_DEMO/);
 $('[aria-label="Close window"]').click();await tick();
 await open2('Accounts');
 vis('.cfg-sort').find(b=>b.textContent.startsWith('Account ID')).click();await tick();
 vis('.cfg-sort').find(b=>b.textContent.startsWith('Account ID')).click();await tick();
 assert.equal($('th[aria-sort]').getAttribute('aria-sort'),'descending');
 assert.deepEqual(vis('.cfg-table tbody tr td:first-child').map(td=>td.textContent),['ICM_DEMO_LMX_1','ICM_DEMO_A123456','ICM_DEMO_51234567','ICM_DEMO_777001','ICM_DEMO_700123']);  // descending; numeric-aware: 51234567 > 777001
 console.log('PASS: chips open the linked record in its section; lists sort by column.');
 // Delete permanently: typed code + reason; the account and everything attached go (audited on the server).
 const victim=db.accounts.find(a=>a.code==='ICM_DEMO_700123');
 vis('.cfg-table tbody tr').find(tr=>tr.textContent.includes('ICM_DEMO_700123')).click();await tick();await tick();
 const answers=['ICM_DEMO_700123','remove test account'];w.prompt=()=>answers.shift();
 button('Delete permanently…').click();await tick();await tick();await tick();
 assert.deepEqual(purges.at(-1),{entity:'accounts',version:victim.version,confirm:'ICM_DEMO_700123',reason:'remove test account'});
 assert.ok(!db.accounts.some(a=>a.code==='ICM_DEMO_700123'));
 assert.match(w.document.body.textContent,/ICM_DEMO_700123 deleted permanently \(12 collected prices, 1 accounts\)/);
 console.log('PASS: Delete permanently removes an account with everything attached after the typed code and a reason.');

 // CSV import: checked first, then applied.
 button('Import CSV').click();await tick();
 fill(byLabel('CSV'),'connection_code,code,name,external_account_id,base_currency\nICM_MT5_DEMO,ICM_D2,Second,5002,USD');await tick();
 button('Check').click();await tick();await tick();
 assert.equal(importCalls.at(-1).dry_run,true);assert.match($('.fw').textContent,/All 1 rows are valid/);
 button('Import 1 accounts').click();await tick();await tick();
 assert.equal(importCalls.at(-1).dry_run,false);
 console.log('PASS: CSV import checks first and applies all-or-nothing.');

 // Four-eyes: a LIVE connection goes to Approvals; another person approves it.
 await open2('Servers');
 button('New server (advanced)').click();await tick();
 fill(byLabel('Provider'),db.providers[0].id);fill(byLabel('Environment'),'LIVE');await tick();
 button('Enter server and addresses manually').click();await tick();
 fill(byLabel('Broker server name'),'ICMarketsSC-Live');fill(byLabel('Access point 1 host'),'198.51.100.7');await tick();
 await submit($('.cfg-detail .cfg-form'));await tick();
 assert.equal($('.fw'),null,'window closes; nothing created yet');
 assert.match($('.cfg-records').textContent,/Sent for approval: New LIVE connection ICM_MT5_LIVE adds address 198\.51\.100\.7:443/);
 assert.ok(!db.connections.some(c=>c.environment==='LIVE'));
 nav('Approvals (four-eyes)').click();await tick();await tick();
 assert.match($('.cfg-requests').textContent,/create connection/);
 button('Approve and apply').click();await tick();await tick();
 assert.ok(db.connections.some(c=>c.environment==='LIVE'));
 assert.match($('.cfg-approvals').textContent,/Change applied/);
 console.log('PASS: LIVE change waits under Approvals and is applied by a second person.');

 // Order sending: confirmation required
 nav('Order sending').click();await tick();await tick();
 const live=vis('.cfg-card').find(c=>c.textContent.includes('LIVE'));
 [...live.querySelectorAll('button')].find(b=>b.textContent.startsWith('Turn on')).click();await tick();
 fill(live.querySelector('[aria-label="Reason"]'),'Go-live approved by risk');await tick();
 const turnOn=[...live.querySelectorAll('button')].find(b=>b.textContent.includes('order sending on'));
 assert.ok(turnOn.disabled,'disabled until environment typed');
 fill(live.querySelector('[aria-label="Confirm environment"]'),'LIVE');await tick();assert.equal(turnOn.disabled,false);
 await submit(live.querySelector('form'));
 assert.equal(controls.LIVE.trading_enabled,true);
 assert.match($('.cfg-nav').textContent,/LIVE ON/);
 console.log('PASS: LIVE order sending needs reason and typed confirmation; status visible in navigation.');

 // Bridge nodes: connect a Windows machine with a one-time code, then use it for accounts (no fingerprint or token typed).
 nav('Bridge nodes').click();await tick();await tick();
 button('Connect a Windows machine').click();await tick();
 fill(byLabel('Node name'),'VPS London');await tick();
 assert.equal(byLabel('Node name').value,'vps-london');
 await submit($('#bn-new'));await tick();await tick();
 assert.deepEqual(bnCalls.filter(c=>c.method==='POST').at(-1).body,{name:'vps-london',location:'WINDOWS_VPS',mode:'DIRECT',max_terminals:20,default_for_new:true,notes:''});
 const steps=$('.bn-steps');
 assert.match(steps.textContent,/install-corei-bridge\.ps1 -Corei https:\/\/x -Code ABCDE-FGHJK-LMNPQ-RSTUV/);
 assert.match(steps.textContent,/Waiting for the machine to register/);
 assert.equal(steps.querySelectorAll('.bn-cmd button').length,2,'each command has a copy button');
 button('Close (the code stays valid)').click();await tick();
 Object.assign(bnodes[0],{status:'ACTIVE',host:'38.253.125.46',bridge_version:'0.42.0',fingerprint:Array(32).fill('AB').join(':'),last_check_ok:true,last_check:{ok:true,terminals_open:0,max_terminals:20}});
 button('Reload').click();await tick();await tick();
 assert.match($('.bn .cfg-table').textContent,/vps-london.*direct · 38\.253\.125\.46:9443/);
 button('Use for accounts').click();await tick();await tick();
 button('Use for all MT5 accounts').click();await tick();await tick();
 assert.deepEqual(bnCalls.find(c=>c.url.endsWith('/attach')).body,{account_ids:'ALL_MT5'});
 assert.match($('[role=status]').textContent,/added to 1 account/);
 console.log('PASS: bridge nodes: a machine is connected with a one-time code and copy-ready commands, then used for accounts in one click.');

 // Users (administrator): create a user with roles.
 nav('Users & roles').click();await tick();await tick();
 assert.match($('.cfg-head h1').textContent,/Users/);
 assert.match($('.cfg-table').textContent,/risk-ops \(you\)/);
 button('New user').click();await tick();
 fill(byLabel('Username'),'Olga');fill(byLabel('Display name'),'Olga Observer');fill(byLabel('Temporary password'),'Temp-pass-4821');
 byLabel('Trading operations').click();await tick();
 await submit($('.fw .cfg-form'));
 assert.deepEqual(users[1].roles.sort(),['OBSERVER','TRADING_OPS']);
 // Own roles cannot be changed.
 w.confirm=()=>true;$('[aria-label="Close window"]').click();await tick();
 vis('.cfg-table tbody tr')[0].click();await tick();
 assert.ok(byLabel('Administrator').matches(':disabled'));
 console.log('PASS: administrator manages users and roles; own roles locked.');
 // Integrations: a connection per application, a key shown once, a real Bearer test without the session, an explorer with permissions.
 w.confirm=()=>true;if($('[aria-label="Close window"]'))$('[aria-label="Close window"]').click();await tick();
 nav('Integrations').click();for(let i=0;i<4;i++)await tick();
 assert.match($('[aria-label="Integrations"]').textContent,/No integration yet/);
 button('+ New connection').click();await tick();
 const f=$('[aria-label="New connection"]');assert.ok(f,'connection form');
 assert.ok(f.querySelector('[aria-label="orders.live_request"]').disabled,'LIVE order permission cannot be ticked');
 assert.deepEqual([...f.querySelectorAll('.int-scopes input:checked')].map(x=>x.getAttribute('aria-label')).sort(),['signals.propose','system.read'],'profile defaults');
 fill(f.querySelector('[aria-label="Code"]'),'core-ai');fill(f.querySelector('[aria-label="Name"]'),'CORE-AI Financial Intelligence');await tick();
 await submit(f);
 assert.deepEqual([intPosts[0].code,intPosts[0].client_type,intPosts[0].environment,intPosts[0].access.accounts.all,intPosts[0].access.accounts.ids],['core-ai','CORE_AI','PAPER',false,[]]);
 for(let i=0;i<3;i++)await tick();
 const pane=$('[aria-label="Connection core-ai"]');assert.ok(pane,'connection detail opens');
 [...pane.querySelectorAll('[role="tab"]')].find(b=>b.textContent==='Keys').click();await tick();
 [...pane.querySelectorAll('button')].find(b=>b.textContent==='+ Issue key').click();for(let i=0;i<3;i++)await tick();
 assert.match($('.int-secret').textContent,/corei_ABCDEFGHJK_x+.*cannot show it again/);
 [...$('[aria-label="Connection core-ai"]').querySelectorAll('[role="tab"]')].find(b=>b.textContent==='Test & setup').click();await tick();
 fill(byLabel('Key to test'),'corei_ABCDEFGHJK_'+'x'.repeat(43));await tick();
 button('Run Bearer test').click();for(let i=0;i<4;i++)await tick();
 assert.equal(bearerCalls[0].headers.Authorization,'Bearer corei_ABCDEFGHJK_'+'x'.repeat(43));
 assert.equal(bearerCalls[0].credentials,'omit','the test never sends the session cookie');
 assert.equal(bearerCalls[0].headers['x-corei-request'],undefined,'nor the workstation header');
 assert.match($('[aria-label="Test results"]').textContent,/✓Authentication \(Bearer\)200connection core-ai.*✓Capability discovery.*✓Data retrieval: instruments2003 instrument/);
 assert.match($('.int-snips').textContent,/Generic Credential Type[\s\S]*Bearer Auth[\s\S]*raw key only/);
 [...$('[aria-label="Integrations views"]').querySelectorAll('button')].find(b=>b.textContent==='API explorer').click();for(let i=0;i<3;i++)await tick();
 [...$('.int-explorer nav').querySelectorAll('button')].find(b=>/market-data\/snapshot/.test(b.textContent)).click();await tick();
 assert.match($('[aria-label="Endpoint detail"]').textContent,/POST.*PermissionMARKET_DATA\.SNAPSHOT|Permissionmarket_data\.snapshot/i);
 console.log('PASS: integrations: connection from a profile (LIVE permission disabled), key shown once, real Bearer test without the session, API explorer with required permissions.');

 // Platform mode (0.66.0): administrator switches with a reason; Production needs PRODUCTION typed; LIVE in Development needs LIVE; reset needs RESET.
 const pmAnswers=[];w.prompt=()=>pmAnswers.shift()??null;w.confirm=()=>true;
 nav('Platform mode').click();for(let i=0;i<4;i++)await tick();
 const pmPane=()=>$('[aria-label="Platform mode"]');
 assert.match(pmPane().textContent,/Production.*Real trading rules/);
 assert.match(pmPane().querySelector('[aria-label="What each mode does"]').textContent,/PasswordsAt least 2 charactersAt least 6 characters/);
 assert.equal(button('Reset test data…'),undefined,'no reset in Production');
 pmAnswers.push('start testing');button('Switch to Development…').click();for(let i=0;i<4;i++)await tick();
 assert.deepEqual([modePuts[0].mode,modePuts[0].allow_live_in_development,modePuts[0].reason,modePuts[0].version],['DEVELOPMENT',false,'start testing',1]);
 assert.match(pmPane().textContent,/LIVE order sending was turned off/);
 pmAnswers.push('nope','try live');button('Allow LIVE in Development…').click();for(let i=0;i<4;i++)await tick();
 assert.deepEqual([modePuts[1].allow_live_in_development,modePuts[1].confirm],[true,'nope'],'the typed word is sent; the server decides');
 pmAnswers.push('RESET','clean slate');button('Reset test data…').click();for(let i=0;i<4;i++)await tick();
 assert.deepEqual(resets[0],{confirm:'RESET',reason:'clean slate'});
 assert.match($('[aria-label="Reset result"]').textContent,/PAPER_USDarchived · fresh copy PAPER_USD_R2PAPER_RUNskipped: PAPER_RUN still has 1 active run/);
 pmAnswers.push('production','go live');button('Switch to Production…').click();for(let i=0;i<4;i++)await tick();
 assert.match(pmPane().textContent,/confirm by typing PRODUCTION/);
 pmAnswers.push('PRODUCTION','go live');button('Switch to Production…').click();for(let i=0;i<4;i++)await tick();
 assert.match(pmPane().textContent,/Now in production mode/);
 console.log('PASS: platform mode: administrator switches with a reason; Production and LIVE need typed words; reset only in Development, result listed.');

 // An observer sees configuration read-only and no Users section.
 session.roles=['OBSERVER'];if($('[aria-label="Close window"]'))$('[aria-label="Close window"]').click();await tick();
 vis('.cfg-user .link').find(b=>b.textContent==='Sign out').click();await tick();
 const signin2=$('.cfg-signin form');fill(signin2.querySelector('[autocomplete="username"]'),'olga');fill(signin2.querySelector('[type="password"]'),'x');await submit(signin2);
 assert.ok(!vis('.cfg-nav button').some(b=>b.textContent.includes('Users')));
 await open2('Providers');
 assert.equal(button('New provider'),undefined);
 $('.cfg-table tbody tr').click();await tick();
 assert.ok($('.fw fieldset.cfg-plain').disabled);assert.equal(button('Save changes'),undefined);
 $('[aria-label="Close window"]').click();await tick();
 nav('Order sending').click();await tick();await tick();
 assert.ok(!vis('.cfg-card button').length);
 console.log('PASS: observer is read-only: no create, save, credential or order-sending actions.');

 assert.deepEqual(errors,[]);
 console.log('PASS: configuration workspace has no runtime errors.');
 w.close();
})().catch(e=>{console.error(String(e&&e.stack||e).slice(0,4000));process.exit(1)});
