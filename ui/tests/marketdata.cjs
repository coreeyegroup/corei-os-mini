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
 const created=[],statusCalls=[],switches=[],estimates=[];
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';const body=options.body?JSON.parse(options.body):undefined;
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   if(url.endsWith('/operator/session'))return ok({username:'risk-ops',display_name:'Risk',roles:['ADMIN','TRADING_OPS'],must_change_password:false});
   if(url.endsWith('/md/latest'))return ok({items:[{account_id:'a1',account_code:'VTM_A',broker_symbol:'XAUUSD.r',bid:2401.15,ask:2401.4,digits:2,age_seconds:0.4,score:96},
     {account_id:'a2',account_code:'ICM_B',broker_symbol:'XAUUSD',bid:2401.0,ask:2401.3,digits:2,age_seconds:42,score:31}]});
   if(url.endsWith('/md/collectors'))return ok({items:[{account_id:'a1',account_code:'VTM_A',state:'CONNECTED',host:'198.51.100.10',port:443,lag_ms:12,subscribed:1,ticks_total:12345,backlog:0,heartbeat_age_seconds:2}]});
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

 // Live: collector card and prices; a 42 s old price is flagged.
 assert.ok($('.md-collector').textContent.includes('connected'));
 const rows=$$('.md-live tbody tr');assert.equal(rows.length,2);
 assert.ok(rows[0].textContent.includes('2401.15'));assert.ok(rows[0].textContent.includes('25.0'));
 assert.ok(rows[1].querySelector('.md-bad'));
 console.log('PASS: live prices with spread, age and score; collector status.');

 // Plans: matrix, then a new plan with a live size estimate; starting a LIVE trading feed goes for approval.
 nav('Collection plans').click();await settle();
 assert.ok($('.md-matrix').textContent.includes('XAUUSD'));
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
 const q=$$('.cfg-panel table tbody tr');assert.equal(q.length,2);assert.ok(q[1].textContent.includes('3.1 bp'));
 q[0].click();await settle();assert.ok($('.md-spark .md-bar'));
 nav('Events').click();await settle();assert.ok($('.md-sev-CRITICAL').textContent.includes('feed switch'));
 nav('Archive').click();await settle();assert.ok($$('code').some(c=>c.textContent.startsWith('aaaaaaaaaaaa')));
 console.log('PASS: collection switch with reason, broker comparison, events, archive and backups.');
 assert.deepEqual(errors,[]);
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
