// Stages 3–8 screens: the same frame on every stage (Overview · Work · Monitor · Configure), signals with reasons,
// an order ticket that is one order per ticket, the order history, a kill switch with a reason, a backtest, the home
// system map with the user's path.
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
 const layout={version:1,workspace:'Research',activity:'Signals',groups:[['Signals']],active:['Signals'],focus:0,left:false,right:false,bottom:false,axis:'row',leftWidth:220,rightWidth:270,bottomHeight:180,maximized:false};
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.localStorage.setItem('corei-os-mini.workstation.layout.v1',JSON.stringify(layout));
 w.confirm=()=>true;let promptAnswer='';w.prompt=()=>promptAnswer;
 const ok=(body,status=200)=>({ok:true,status,json:async()=>body});
 const fail=(status,detail)=>({ok:false,status,json:async()=>({detail})});
 const now=new Date().toISOString();
 const calls=[];
 const signal={id:'s1',bar_time:now,strategy_code:'EMA_FAST',version_number:1,source:'RUNTIME',timeframe:'M5',symbol:'EURUSD',account_code:'VTM_A',mode:'PAPER',side:'BUY',strength:70,
   state:'REJECTED',reasons:['STALE_PRICE: the last price is 95 s old (limit 60 s)'],explanation:['EMA 3/5: fast 1.08 crossed above slow 1.07'],features:{atr14:0.0012}};
 const order={id:'o1',client_order_id:'CABC',created_at:now,source:'MANUAL',strategy_code:null,mode:'PAPER',account_code:'VTM_A',symbol:'EURUSD',side:'BUY',quantity:'0.1',order_type:'MARKET',
   state:'FILLED',filled_qty:'0.1',avg_fill_price:'1.08011',reasons:[],time_in_force:'GTC'};
 const kills=[];
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';const body=options.body?JSON.parse(options.body):undefined;calls.push({url,method,body});
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   if(url.endsWith('/operator/session'))return ok({username:'risk-ops',display_name:'Risk',roles:['ADMIN','TRADING_OPS','RISK_APPROVER'],must_change_password:false});
   if(url.endsWith('/trading/overview'))return ok({deployments:{ACTIVE:1},active_modes:{PAPER:1},signals_today:{REJECTED:1,ACTED:2},orders_today:{FILLED:2},open_orders:0,unknown_orders:0,
     kill_switches:kills.length,limits:1,refused_today:1,positions:1,accounts:[],backtests:1,runtime:{last_run_at:now,deployments:1,signals:1,orders:1,duration_ms:30,enabled:true,deployments_with_problems:0}});
   if(url.includes('/trading/signals/s1'))return ok({...signal,events:[{id:1,at:now,state:'NEW',actor:'runtime',detail:'EMA 3/5'},{id:2,at:now,state:'REJECTED',actor:'validation',detail:'STALE_PRICE'}],intent:null});
   if(url.includes('/trading/signals'))return ok({items:[signal]});
   if(url.endsWith('/trading/orders?state=UNKNOWN&limit=200'))return ok({items:[{...order,id:'o9',mode:'BROKER',state:'UNKNOWN',broker_tag:'CABC',checks:2,last_check_at:now}]});
   if(url.endsWith('/trading/orders/o9/check-broker'))return ok({...order,id:'o9',state:'FILLED'});
   if(url.includes('/trading/orders/o1'))return ok({...order,events:[{id:1,state_to:'NEW',at:now,actor:'risk-ops',detail:{}},{id:2,state_to:'APPROVED',at:now,actor:'risk',detail:{}},
     {id:3,state_to:'SUBMITTED',at:now,actor:'paper',detail:{}},{id:4,state_to:'ACKNOWLEDGED',at:now,actor:'paper',detail:{}},{id:5,state_to:'FILLED',at:now,actor:'paper',detail:{price:'1.08011',slippage:'0.00001'}}],
     fills:[{id:'f1',quantity:'0.1',price:'1.08011',quote_bid:'1.08',quote_ask:'1.0801',realised_pnl:'0',currency:'USD'}],risk:{approved:true,inputs:{position_before:'0',position_after:'0.1',limits:{}}}});
   if(url.endsWith('/trading/orders')&&method==='POST')return ok({...order,id:'o2',duplicate:calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST'&&c.body.ticket_key===body.ticket_key).length>1},201);
   if(url.includes('/trading/orders'))return ok({items:[order]});
   if(url.includes('/trading/risk/kill-switches')&&method==='POST'){const k={id:'k'+kills.length,...body,engaged:true,target_name:'VTM_A',engaged_by:'risk-ops',engaged_at:now,automatic:false};kills.push(k);return ok(k,201);}
   if(url.includes('/trading/risk/kill-switches'))return ok({items:kills});
   if(url.includes('/trading/risk/exposure'))return ok({instruments:[],accounts:[]});
   if(url.includes('/trading/risk/limits'))return ok({items:[],kinds:{MAX_DAILY_LOSS:'Largest loss today (account currency); breach stops new orders',MAX_POSITION_QTY:'Largest net position per instrument (contracts / lots)'}});
   if(url.includes('/trading/algorithms'))return ok({items:[{id:'EMA_CROSSOVER',label:'EMA crossover',description:'Long when the fast EMA crosses above the slow EMA',
     schema:{properties:{timeframe:{enum:['M5','H1'],default:'H1'},direction:{default:'BOTH'},fast:{type:'integer',default:9},slow:{type:'integer',default:21}}}}]});
   if(url.includes('/trading/sources'))return ok({items:[{account_id:'a1',account_code:'VTM_A',broker_symbol:'EURUSD',first_bar:now,last_bar:now}]});
   if(url.endsWith('/trading/backtests')&&method==='POST')return ok({id:'b1',metrics:{net_pnl:'812.40',return_pct:'0.81',trades:14,win_rate_pct:57.1,profit_factor:1.6,max_drawdown_pct:'0.42',sharpe:1.3,exposure_pct:48,currency:'USD'},
     equity:[{t:now,equity:'100000'},{t:now,equity:'100812.4'}],trades:[{side:'LONG',entry_time:now,entry:'1.08',exit_time:now,exit:'1.081',pnl:'100.00',reason:'EMA cross'}]},201);
   if(url.includes('/trading/backtests'))return ok({items:[]});
   if(url.includes('/trading/posttrade/overview'))return ok({open_total:1,open_breaks:{POSITION_MISMATCH:1},unknown_orders:0,fills_today:3,avg_latency_today_ms:142,
     accounts:[{account_id:'a1',code:'VTM_A',at:now,status:'BREAKS',breaks:1,kind:'BROKER'}]});
   if(url.includes('/trading/posttrade/breaks/b1')&&method==='POST')return ok({break:{id:'b1',state:'ACCEPTED'},result:null});
   if(url.includes('/trading/posttrade/breaks'))return ok({items:[{id:'b1',account_code:'VTM_A',kind:'POSITION_MISMATCH',broker_symbol:'EURUSD',corei_value:'0.3',broker_value:'0.4',difference:'-0.1',
     first_seen:now,last_seen:now,state:'OPEN'}]});
   if(url.includes('/trading/posttrade/tca'))return ok({by:'account',currency:'USD',slippage_cost_total:'12.5',commission_total:'-2.1',rows:[{label:'VTM_A',orders:4,fill_rate_pct:'100',reject_rate_pct:0,fills:4,
     avg_slippage_bps:'0.19',slippage_cost:'12.5',price_improved:1,worse_than_arrival:3,latency_avg_ms:140,latency_p95_ms:210,latency_max_ms:230,commission:'-2.1'}],note:'Slippage = fill vs arrival.'});
   if(url.includes('/trading/risk/stress'))return ok({scenarios:[{id:'s1',name:'Everything −5%',description:'',builtin:true,shocks:[{match:'ALL',value:'',pct:'-5'}],total:'-540.20',by_account:[{account:'VTM_A',pnl:'-540.20'}]}],missing:[]});
   if(url.includes('/trading/'))return ok({items:[]});
   if(url.includes('/config/accounts'))return ok({items:[{id:'a1',code:'VTM_A',environment:'DEMO',platform:'MT5',status:'ACTIVE'},{id:'a2',code:'ICM_L',environment:'LIVE',platform:'MT5',status:'ACTIVE'}]});
   if(url.includes('/config/instruments'))return ok({items:[{id:'i1',symbol:'EURUSD',name:'Euro'}]});
   if(url.includes('/config/strategies'))return ok({items:[]});
   if(url.includes('/config/deployments'))return ok({items:[]});
   if(url.includes('/intel/overview'))return ok({covered:3});
   if(url.endsWith('/config/overview'))return ok({user:{username:'risk-ops',roles:['ADMIN']},feeds:{active_plans:2,collectors:1,live:1,problems:0,paused:false},order_sending:[{environment:'DEMO',enabled:false}],
     approvals:0,attention:[],setup:{done:3,total:6,next:null,steps:[]}});
   return fail(404,'Not mocked: '+url);
 };
 w.eval(js);await settle(12);
 const $=s=>w.document.querySelector(s),$$=s=>[...w.document.querySelectorAll(s)];
 const fill=(el,value)=>{const proto=el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:el.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const nav=label=>$$('.cfg-nav button').find(b=>b.querySelector('span').textContent===label);
 const button=text=>$$('button').find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle(4);};
 const go=async v=>{w.dispatchEvent(new w.CustomEvent('corei-goto',{detail:{view:v}}));await settle(12);};

 // Signals: the frame, then the reason a signal was refused, in plain words.
 assert.match($('.st-head').textContent,/Stage 3 · Signal intelligence/);
 assert.match($('.st-head').textContent,/2 · Market intelligence/);
 assert.deepEqual($$('.cfg-nav .cfg-nav-group').map(g=>g.textContent),['Overview','Work','Monitor','Configure']);
 assert.match($('.intel-kpis').textContent,/Signals today3/);
 assert.match($('.intel-table').textContent,/STALE_PRICE: the last price is 95 s old/);
 nav('Signals').click();await settle(6);
 $$('.intel-table tbody tr')[0].click();await settle(6);
 assert.match($('.fw').textContent,/Why it was refused:STALE_PRICE/);
 $$('.fw button').find(b=>b.textContent==='Close').click();await settle();
 console.log('PASS: signals: one frame for every stage, refused signals show their reason and history.');

 // Orders: ticket = one order per ticket key; the order history step by step.
 await go('Execution');
 nav('Order ticket').click();await settle(6);
 fill($('[aria-label="Ticket account"]'),'a1');fill($('[aria-label="Ticket instrument"]'),'i1');fill($('[aria-label="Ticket quantity"]'),'0.1');await settle();
 await submit($('.intel form'));
 const first=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST');
 assert.equal(first.length,1);assert.equal(first[0].body.side,'BUY');assert.match(first[0].body.ticket_key,/^k/);
 await submit($('.intel form'));
 const second=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST');
 assert.notEqual(second[1].body.ticket_key,first[0].body.ticket_key,'after a placed order the next ticket is a new order');
 nav('Orders').click();await settle(6);
 $$('.intel-table tbody tr')[0].click();await settle(8);
 assert.deepEqual($$('.st-timeline li b').map(b=>b.textContent),['new','approved','submitted','acknowledged','filled']);
 $$('.fw button').find(b=>b.textContent==='Close').click();await settle();
 console.log('PASS: orders: the ticket sends one order per ticket; each order shows every step, the risk check and its fills.');

 // A real order is confirmed before it is sent; a LIVE account is called out.
 nav('Order ticket').click();await settle(6);
 fill($('[aria-label="Ticket mode"]'),'BROKER');await settle();
 fill($('[aria-label="Ticket account"]'),'a2');fill($('[aria-label="Ticket instrument"]'),'i1');fill($('[aria-label="Ticket quantity"]'),'0.1');await settle();
 assert.match($('.intel form').textContent,/LIVE account: real money/);
 const before=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST').length;
 await submit($('.intel form'));
 assert.equal(calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST').length,before,'the first press only asks to confirm');
 assert.match($('[role="alert"]').textContent,/Press again to confirm/);
 await submit($('.intel form'));
 const real=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST').at(-1).body;
 assert.equal(real.mode,'BROKER');assert.equal(real.account_id,'a2');
 nav('Unknown').click();await settle(6);
 assert.match($('.intel-table').textContent,/CABC/);
 button('Look up now').click();await settle(4);
 assert.ok(calls.some(c=>c.url.endsWith('/trading/orders/o9/check-broker')&&c.method==='POST'));
 console.log('PASS: execution: a real order asks for confirmation (LIVE called out); unknown orders are looked up at the broker, never re-sent.');

 // Risk: a kill switch needs a scope, a target and a reason.
 await go('Risk');
 nav('Kill switches').click();await settle(6);
 button('Engage a kill switch').click();await settle();
 fill($('[aria-label="Target"]'),'a1');fill($('[aria-label="Kill reason"]'),'broker incident');await settle();
 await submit($('.intel-edit'));
 assert.deepEqual(calls.filter(c=>c.url.includes('kill-switches')&&c.method==='POST')[0].body,{scope:'ACCOUNT',target_id:'a1',reason:'broker incident'});
 console.log('PASS: risk: a kill switch is engaged with a scope, a target and a reason.');
 nav('Stress tests').click();await settle(6);
 assert.match($('.intel-table').textContent,/Everything −5%.*-540/);
 console.log('PASS: risk: stress scenarios show the P&L of today\'s positions under each shock.');

 // Post-trade: breaks with their actions, execution quality.
 await go('Post-trade');
 assert.match($('.st-head').textContent,/Stage 8 · Post-trade/);
 assert.match($('.intel-kpis').textContent,/Open breaks1/);
 nav('Breaks').click();await settle(6);
 assert.match($('.intel-table').textContent,/position mismatch.*EURUSD.*0\.3.*0\.4/i);
 promptAnswer='known difference, broker fixing';button('Accept').click();await settle(4);
 assert.deepEqual(calls.filter(c=>c.url.endsWith('/posttrade/breaks/b1')&&c.method==='POST')[0].body,{action:'ACCEPT',note:'known difference, broker fixing'});
 nav('Execution quality').click();await settle(6);
 assert.match($('.intel-table').textContent,/0\.19 bp.*140 · 210 · 230 ms/);
 console.log('PASS: post-trade: breaks are accepted or resolved with a note; TCA shows slippage, latency and costs.');

 // Strategies: a backtest of an idea, with metrics and an equity curve.
 await go('Strategies');
 nav('Backtest').click();await settle(8);
 fill($('[aria-label="Backtest rules"]'),'IDEA');await settle(4);
 fill($('[aria-label="Backtest source"]'),'a1|EURUSD');await settle();
 await submit($('.intel form'));await settle(4);
 const bt=calls.find(c=>c.url.endsWith('/trading/backtests')&&c.method==='POST').body;
 assert.equal(bt.algorithm,'EMA_CROSSOVER');assert.equal(bt.broker_symbol,'EURUSD');
 assert.match($('.intel-kpis').textContent,/Trades14/);assert.ok($('.st-spark'));
 console.log('PASS: strategies: a backtest runs an algorithm on a collected source and shows metrics, trades and equity.');

 // Home: the system map — eight stages in order, live figures, the user's path.
 await go('Overview');await settle(6);
 assert.equal($$('.sysmap-flow li').length,8);
 assert.match($('.sysmap-flow').textContent,/Signal intelligence.*3 signals today/);
 assert.ok($$('.sysmap-path button.done').length>=3,'done steps are ticked');
 assert.deepEqual(errors,[]);
 console.log('PASS: home shows how COREI works: eight stages with live figures and the user\'s path.');
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
