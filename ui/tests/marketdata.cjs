// Market data workspace: live prices and collectors, plan creation with size estimate,
// LIVE trading-feed approval, collection switch with reason, quality comparison, archive.
const {JSDOM}=require('jsdom');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'ui/dist/index.html'),'utf8');
const entry=html.match(/src="([^" ]+\.js)"/)[1].replace(/^\//,'');
const js=fs.readFileSync(path.join(root,'ui/dist',entry),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,40));
const settle=async(n=8)=>{for(let i=0;i<n;i++)await tick();};

(async()=>{
 const layout={version:1,workspace:'Research',activity:'Market data',groups:[['Market data']],active:['Market data'],focus:0,left:false,right:false,bottom:false,axis:'row',leftWidth:220,rightWidth:270,bottomHeight:180,maximized:false};
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.localStorage.setItem('corei-os-mini.workstation.layout.v1',JSON.stringify(layout));
 w.confirm=()=>true;
 const ok=(body,status=200)=>({ok:true,status,json:async()=>body});
 const fail=(status,detail)=>({ok:false,status,json:async()=>({detail})});
 const now=new Date().toISOString();
 const accounts=[{id:'a1',code:'VTM_A',provider_code:'VTM',environment:'LIVE',platform:'MT5'},{id:'a2',code:'ICM_B',provider_code:'ICM',environment:'LIVE',platform:'MT5'}];
 const instruments=[{id:'i1',symbol:'XAUUSD'}];
 const plans=[{id:'p1',code:'GOLD_A',account_id:'a1',account_code:'VTM_A',broker_symbol:'XAUUSD.r',instrument_id:'i1',instrument_symbol:'XAUUSD',purpose:'TRADING_FEED',feed_role:'PRIMARY',
   data_types:['QUOTES','BARS'],bar_timeframes:['M1'],schedule:'ALWAYS',window_days:[1,2,3,4,5],hot_days:14,bars_hot_days:365,archive:true,status:'ACTIVE',status_reason:'Start',version:3,
   environment:'LIVE',active_feed:true,score:96,stale_seconds:10,max_latency_ms:1500,max_gap_seconds:60,conflate_ms:0,depth_levels:0,backfill_per_minute:30,on_breach:'SWITCH',pending_changes:0}];
 const controls=[{scope:'ALL',paused:false,reason:'Initial state',version:1,updated_by:'system',updated_at:now}];
 const created=[],statusCalls=[],switches=[],estimates=[],tickCalls=[],prefPuts=[];
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';const body=options.body?JSON.parse(options.body):undefined;
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   if(url.endsWith('/operator/session'))return ok({username:'risk-ops',display_name:'Risk',roles:['ADMIN','TRADING_OPS'],must_change_password:false});
   if(url.endsWith('/md/latest'))return ok({items:[{account_id:'a1',account_code:'VTM_A',broker_symbol:'XAUUSD.r',bid:2401.15,ask:2401.4,digits:2,age_seconds:0.4,score:96},
     {account_id:'a2',account_code:'ICM_B',broker_symbol:'XAUUSD',bid:2401.0,ask:2401.3,digits:2,age_seconds:'42.5',score:31}]});
   if(url.endsWith('/md/collectors'))return ok({items:[{account_id:'a1',account_code:'VTM_A',account_name:'VT Markets gold',state:'CONNECTED',host:'198.51.100.10',port:443,lag_ms:12,subscribed:1,ticks_total:12345,backlog:0,heartbeat_age_seconds:'2.31',problem:null},
     {account_id:'a2',account_code:'ICM_B',account_name:'IC Markets B',state:'FAILED',host:null,port:null,lag_ms:null,subscribed:0,ticks_total:0,backlog:0,heartbeat_age_seconds:3,last_error:'ServerException: OLD_VERSION',
      problem:{title:'Broker refused the connector version',detail:'The broker requires a newer MT5 version.',permanent:true,raw:'ServerException: OLD_VERSION'}}]});
   if(url.endsWith('/md/sources'))return ok({items:[{account_id:'a1',account_code:'VTM_A',account_name:'VT Markets gold',provider_name:'VT Markets',platform:'MT5',environment:'LIVE',purpose:'BOTH',collector_state:'CONNECTED',active_plans:1,symbols_with_prices:1},
     {account_id:'a2',account_code:'ICM_B',account_name:'IC Markets B',provider_name:'IC Markets',platform:'MT5',environment:'LIVE',purpose:'BOTH',collector_state:'FAILED',active_plans:1,symbols_with_prices:1}]});
   if(url.endsWith('/md/instruments'))return ok({items:[{symbol:'XAUUSD',asset_class:'METAL',sources:2,priced:true,broker_symbols:['XAUUSD','XAUUSD.r']},{symbol:'NIFTY',asset_class:null,sources:0,priced:false,broker_symbols:[]}]});
   if(url.includes('/md/bars/'))return ok({items:Array.from({length:30},(_,i)=>({bar_time:new Date(Date.parse(now)-i*60000).toISOString(),open:2400+i%5,high:2403+i%5,low:2398+i%5,close:2401+(i%3),ticks:40,source:'BUILT'}))});
   if(url.includes('/md/ticks/')){tickCalls.push(url);return ok({items:[{received_at:now,source_time:now,bid:2401.15,ask:2401.4},{received_at:now,source_time:now,bid:2401.2,ask:2401.45}]});}
   if(url.endsWith('/operator/prefs')&&method==='GET')return ok({});
   if(url.includes('/operator/prefs/')){prefPuts.push(body);return ok({saved:true});}
   if(url.startsWith('/api/md/plans')&&method==='GET')return ok({items:plans});
   if(url.endsWith('/md/plans')&&method==='POST'){created.push(body);const p={...body,id:'p2',status:'DRAFT',version:1,account_code:'ICM_B',environment:'LIVE',instrument_symbol:'XAUUSD',status_reason:''};plans.push(p);return ok(p,201);}
   if(url.endsWith('/md/plans/p2/status')){statusCalls.push(body);return ok({change_request:{id:'r1',summary:'Start LIVE trading feed GOLD_B (XAUUSD)'}},202);}
   if(url.endsWith('/md/estimate')){estimates.push(body);return ok({ticks_per_second:2,rate_source:'TYPICAL',rows_per_day:173000,database_gb_steady:0.29,archive_gb_per_year:0.88,account_active_symbols:0,account_symbol_limit:300,notes:[]});}
   if(url.endsWith('/config/accounts'))return ok({items:accounts});
   if(url.endsWith('/config/instruments'))return ok({items:instruments});
   if(url.endsWith('/md/controls'))return ok({items:controls});
   if(url.includes('/md/controls/')){switches.push({url,body});controls[0]={...controls[0],paused:body.paused,reason:body.reason,version:2};return ok(controls[0]);}
   if(url.includes('/md/quality?'))return ok({items:[{account_id:'a1',account_code:'VTM_A',connection_code:'VTM_L3',broker_symbol:'XAUUSD.r',instrument_symbol:'XAUUSD',score:96.2,ticks:120000,latency_p50_ms:38,latency_p95_ms:90,spread_avg:22,spread_max:41,max_gap_seconds:3,spikes:0,deviation_bp:0.4,empty_minutes:0},
     {account_id:'a2',account_code:'ICM_B',connection_code:'ICM_1',broker_symbol:'XAUUSD',instrument_symbol:'XAUUSD',score:71.5,ticks:90000,latency_p50_ms:80,latency_p95_ms:400,spread_avg:30,spread_max:90,max_gap_seconds:45,spikes:2,deviation_bp:3.1,empty_minutes:12}]});
   if(url.includes('/md/quality/'))return ok({items:[{minute:now,score:90,ticks:100}]});
   if(url.includes('/md/events'))return ok({items:[{id:1,at:now,severity:'CRITICAL',kind:'FEED_SWITCH',account_code:null,broker_symbol:null,detail:{from:'GOLD_A',to:'GOLD_B'}}]});
   if(url.endsWith('/md/jobs'))return ok({items:[]});
   if(url.endsWith('/md/files'))return ok({items:[{id:1,day:'2026-10-20',kind:'TICKS',account_code:'VTM_A',broker_symbol:'XAUUSD.r',rows:180000,bytes:2400000,sha256:'a'.repeat(64),verify_status:'OK',verified_at:now}]});
   if(url.endsWith('/md/backups'))return ok({items:[{id:1,kind:'DATABASE',ok:true,started_at:now,path:'/backup/db/corei.dump',bytes:5e6,files:1}]});
   return fail(404,'Not mocked: '+url);
 };
 w.eval(js);await settle();
 const $=s=>w.document.querySelector(s),$$=s=>[...w.document.querySelectorAll(s)];
 const fill=(el,value)=>{const proto=el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:el.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const nav=label=>$$('.cfg-nav button').find(b=>b.querySelector('span').textContent===label);
 const button=text=>$$('button').find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle(3);};

 // Dashboard (default): one workspace with every source and instrument; pickers, filters, grouping, columns, panes.
 const tabs=()=>$$('.mb-tabs [role=tab]').map(b=>b.textContent);
 const pickBtn=l=>$$('.mb-pickbtn').find(b=>b.textContent.startsWith(l));
 const boardRows=()=>$$('.mb-board tbody tr:not(.mb-group)');
 assert.deepEqual(tabs(),['All sources']);
 assert.equal(boardRows().length,2);
 assert.match(pickBtn('Sources').textContent,/All \(2\)/);
 assert.match($('.mb-kpis').textContent,/Feeds live1\/2/);
 assert.match($('.mb-problem').textContent,/IC Markets B: Broker refused the connector version/);
 assert.equal($$('.mb-svg g.up, .mb-svg g.down').length,30,'candles drawn for the first row');
 assert.ok(tickCalls.some(u=>u.includes('seconds=120')),'time & sales loads recent ticks');
 assert.match($('[aria-label=Sources]').textContent,/XAUUSD · 2 sources/,'same instrument from both sources');
 // Sources picker: grouped by provider, searchable; "only" keeps one source.
 pickBtn('Sources').click();await settle();
 assert.deepEqual($$('.mb-popgroup').map(g=>g.textContent),['VT Markets · MT51','IC Markets · MT51']);
 fill($('[aria-label="Search sources"]'),'gold');await settle();
 assert.equal($$('.mb-popitem').length,1);
 $('.mb-popitem .mb-only').click();await settle();
 assert.equal(boardRows().length,1);assert.match(pickBtn('Sources').textContent,/1 of 2/);
 const esc=async()=>{w.document.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape'}));await settle();};
 await esc();assert.equal($('.mb-pop'),null,'Escape closes the picker');
 // Instrument picker lists the register too, by asset class; not-collected ones are marked.
 pickBtn('Instruments').click();await settle();
 assert.match($('.mb-pop').textContent,/Metals.*XAUUSD.*2 sources.*Indices.*NIFTY.*not collected/);
 await esc();
 // Back to all sources; filters, grouping, columns and sort.
 pickBtn('Sources').click();await settle();$$('.mb-poptools button').find(b=>b.textContent==='Reset').click();await settle();await esc();
 assert.equal(boardRows().length,2);
 $$('.mb-seg[aria-label=Status] button').find(b=>b.textContent==='Stale').click();await settle();
 assert.equal(boardRows().length,1,'stale filter');
 $$('.mb-seg[aria-label=Status] button').find(b=>b.textContent==='All').click();await settle();
 fill($('[aria-label="Group rows by"]'),'source');await settle();
 assert.deepEqual($$('.mb-group b').map(b=>b.textContent),['IC Markets B','VT Markets gold']);
 fill($('[aria-label="Group rows by"]'),'none');await settle();
 pickBtn('Columns').click();await settle();
 $$('.mb-popitem').find(l=>l.textContent.startsWith('Provider')).querySelector('input').click();await settle();await esc();
 assert.ok($$('.mb-board th').some(th=>th.textContent.startsWith('Provider')),'column added');
 $$('.mb-board th').find(th=>th.textContent.startsWith('Q')).click();await settle();
 assert.match(boardRows()[0].textContent,/VT Markets gold/,'sorted by quality, best first');
 $$('.mb-seg[aria-label=Charts] button').find(b=>b.textContent==='2').click();await settle();
 assert.equal($$('.mb-charts > section').length,2);
 // Workspaces: one per source; they are saved for the user.
 button('One per source').click();await settle();
 assert.deepEqual(tabs(),['All sources','VT Markets gold','IC Markets B']);
 await new Promise(r=>setTimeout(r,900));
 assert.equal(prefPuts.at(-1).value.length,3,'workspaces saved as a preference');
 assert.equal(boardRows().length,1,'the new workspace shows its source only');
 console.log('PASS: dashboard: source and instrument pickers (grouped, searchable, only), filters, grouping, columns, sort, chart panes, workspaces saved.');
 nav('Table').click();await settle();

 // Live: collector card and prices; a 42 s old price is flagged.
 assert.ok($('.md-collector').textContent.includes('VT Markets gold'));assert.ok($('.md-collector').textContent.includes('live'));
 // A refused login is explained in plain words, with the broker's own message and the next step.
 const problem=$('.md-problem');assert.ok(problem.textContent.includes('IC Markets B: Broker refused the connector version'));
 assert.ok(problem.textContent.includes('OLD_VERSION'));assert.ok(problem.textContent.includes('retrying every 5 minutes'));
 const rows=$$('.md-live tbody tr');assert.equal(rows.length,2);
 assert.ok(rows[0].textContent.includes('2401.15'));assert.equal(rows[0].querySelectorAll('td')[5].textContent,'25');
 assert.ok(rows[1].querySelector('.md-bad'));
 console.log('PASS: live prices with spread, age and score; collector status; refused login explained in plain words.');

 // Plans: matrix, then a new plan with a live size estimate; starting a LIVE trading feed goes for approval.
 nav('Collection plans').click();await settle();
 assert.ok($$('.cfg-table tbody tr').some(r=>r.textContent.includes('GOLD_A')),'plans list by default');
 button('Matrix').click();await settle(2);assert.ok($('.md-matrix').textContent.includes('XAUUSD'));assert.ok($('.md-cell-line').textContent.includes('active'));
 button('New collection plan').click();await settle(2);
 fill($('[aria-label="Plan code"]'),'gold_b');fill($('[aria-label="Plan account"]'),'a2');fill($('[aria-label="Broker symbol"]'),'XAUUSD');
 fill($('[aria-label="Plan instrument"]'),'i1');fill($('[aria-label="Plan role"]'),'BACKUP');await settle(2);
 await new Promise(r=>setTimeout(r,500));await settle(2);
 assert.ok(estimates.length>0);assert.equal(estimates.at(-1).code,'GOLD_B');
 assert.ok($('.md-estimate').textContent.includes('173,000'));
 await submit($('#md-plan-form'));
 assert.equal(created[0].feed_role,'BACKUP');assert.equal(created[0].instrument_id,'i1');assert.equal(created[0].max_spread_points,null);
 button('Start collecting…').click();await settle(2);
 fill($('[aria-label="Status reason"]'),'Second source for gold');await submit($('[aria-label="Change plan status"] form'));
 assert.deepEqual(statusCalls[0],{status:'ACTIVE',version:1,reason:'Second source for gold'});
 assert.ok($('[role="status"]').textContent.includes('Sent for approval'));
 $('[aria-label="Close window"]').click();await settle(2);
 console.log('PASS: plans matrix, new plan with size estimate, LIVE trading feed sent for approval.');

 nav('Controls').click();await settle();
 button('Pause everything…').click();await settle(2);
 fill($('[aria-label="Switch reason"]'),'Broker maintenance');await submit($('[aria-label="Collection switch"] form'));
 assert.deepEqual(switches[0].body,{paused:true,version:1,reason:'Broker maintenance'});
 assert.ok($('.md-switch').textContent.includes('PAUSED'));
 nav('Quality').click();await settle();
 const q=$$('.md-quality tbody tr');assert.equal(q.length,2);assert.ok(q[0].textContent.includes('XAUUSD'));assert.equal(q[1].querySelector('td').textContent,'');assert.ok(q[1].textContent.includes('3.1 bp'));
 q[0].click();await settle();assert.ok($('.md-spark .md-bar'));
 nav('Events').click();await settle();assert.ok($('.md-sev-CRITICAL').textContent.includes('feed switch'));
 nav('Archive').click();await settle();assert.ok($$('code').some(c=>c.textContent.startsWith('aaaaaaaaaaaa')));
 console.log('PASS: collection switch with reason, broker comparison, events, archive and backups.');
 assert.deepEqual(errors,[]);
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
