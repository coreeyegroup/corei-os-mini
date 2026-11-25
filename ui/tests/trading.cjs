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
 const pendingWorkspaceDecision='8fda0b19-a858-4b3c-810a-3513a5f92d2f';
 const workspacePending={id:pendingWorkspaceDecision,deployment_id:'dep-2',symbol:'EURUSD',strategy_code:'TREND',
   outcome:'PENDING',signal_side:'BUY',target:'LONG',created_at:now,bar_time:now};
 const calls=[];
 let savedWorkspaces=null;
 let deskScan=null;
 let largeScanCatalog=false;
 let beyondRegistryLimit=false;
 let brokerSymbolCollected=false;
 let backgroundScanRequest;
 const savedRuns=[];
 let savedProfile;
 const backgroundSummary=[{scope:'EURUSD',checked:2,signals:1,errors:0,signal_strategies:['EMA_EXISTING'],strength:85}];
 const backgroundRun=()=>({id:'batch-'+(savedRuns.length+1),mode:'instrument',status:'COMPLETED',total:10302,request:backgroundScanRequest.request,name:backgroundScanRequest.name,
   created_at:now,updated_at:now,summary:backgroundSummary,instruments:backgroundScanRequest.request.instrument_symbols,
   strategies:backgroundScanRequest.request.strategy_codes});
 const signal={id:'s1',bar_time:now,strategy_code:'EMA_FAST',version_number:1,source:'RUNTIME',timeframe:'M5',symbol:'EURUSD',account_code:'VTM_A',mode:'PAPER',side:'BUY',strength:70,
   state:'REJECTED',reasons:['STALE_PRICE: the last price is 95 s old (limit 60 s)'],explanation:['EMA 3/5: fast 1.08 crossed above slow 1.07'],features:{atr14:0.0012}};
 const order={id:'o1',client_order_id:'CABC',created_at:now,source:'MANUAL',strategy_code:null,mode:'PAPER',account_code:'VTM_A',symbol:'EURUSD',side:'BUY',quantity:'0.1',order_type:'MARKET',
   state:'FILLED',filled_qty:'0.1',avg_fill_price:'1.08011',reasons:[],time_in_force:'GTC'};
 const kills=[];
 const studioVersions=[];
 let strategyScanAttempts=0;
 const studioTemplate={schema_version:'1.0',name:'EMA research template',nodes:[
   {id:'bars',type:'input.bars'},{id:'fast',type:'indicator.ema',params:{period:20}},
 ],edges:[{source:'bars',source_port:'close',target:'fast',target_port:'source'}]};
 const CALC=JSON.parse(fs.readFileSync(path.join(__dirname,'fixtures/calc.json'),'utf8'));
 const strategyRule={...CALC.version,code:'EMA_STUDIO',name:'EMA studio rule',purpose:'STRATEGY_RULE',
   builtin:false,version:1,status:'APPROVED',definition:{...CALC.version.definition,purpose:'STRATEGY_RULE',parameters:[]}};
 const registeredStrategies=[{id:'existing-strategy',code:'EMA_EXISTING',name:'Existing EMA strategy',status:'ACTIVE'}];
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';const body=options.body?JSON.parse(options.body):undefined;calls.push({url,method,body});
   if(url.endsWith('/calc/nodes'))return ok(CALC.nodes);
   if(url.endsWith('/calc/flow'))return ok(CALC.flow);
   if(url.includes('/calc/flow/live'))return ok({bars:600,bar_time:now,close:'1.0853',values:{in_atr:{value:0.0912,label:'atr_pct'}}});
   if(url.endsWith('/calc/formulas'))return ok(CALC.formulas);
   if(url.includes('/calc/formulas/')&&url.endsWith('/evaluate'))return ok(CALC.evaluate);
   if(url.endsWith('/calc/graphs')&&method==='GET')return ok({items:[{code:'SIG_EMA_TREND',name:'EMA trend',purpose:'SIGNAL',builtin:true,bound:0,versions:[{version:1,status:'APPROVED'}]},
    {...strategyRule,versions:[{version:1,status:'APPROVED',approved_by:'reviewer'},{version:2,status:'DRAFT'}]}]});
   if(url.endsWith('/calc/graphs/EMA_STUDIO'))return ok({...strategyRule,versions:[{version:1,status:'APPROVED'},{version:2,status:'DRAFT'}]});
   if(url.endsWith('/calc/graphs/EMA_STUDIO/versions/1'))return ok(strategyRule);
   if(url.endsWith('/calc/graphs/EMA_STUDIO/versions/2'))return ok({...strategyRule,version:2,status:'DRAFT'});
   if(url.includes('/calc/graphs/SIG_EMA_TREND/copy'))return ok({...CALC.version,code:body.code,builtin:false,status:'DRAFT'},201);
   if(url.includes('/calc/graphs/SIG_EMA_TREND/versions/'))return ok(CALC.version);
   if(url.endsWith('/calc/graphs/SIG_EMA_TREND'))return ok({...CALC.version,versions:[{version:1,status:'APPROVED',approved_by:'platform'}]});
   if(url.endsWith('/calc/preview'))return ok(CALC.preview);
   if(url.includes('/calc/bindings'))return ok({items:[],targets:{SIGNAL_DEFINITION:['EMA_TREND']}});
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   if(url.endsWith('/operator/session'))return ok({username:'risk-ops',display_name:'Risk',roles:['ADMIN','TRADING_OPS','RISK_APPROVER'],must_change_password:false});
   if(url.endsWith('/studio/builder/nodes'))return ok({nodes:[
     {type:'input.bars',inputs:{},outputs:{close:'series'},parameters:{}},
     {type:'indicator.ema',inputs:{source:'series'},outputs:{value:'series'},parameters:{period:[2,1000]}},
   ]});
   if(url.endsWith('/studio/builder/templates'))return ok({items:[{id:'ema_trend_20_50',category:'Trend',name:studioTemplate.name}]});
   if(url.endsWith('/studio/builder/templates/ema_trend_20_50'))return ok(studioTemplate);
   if(url.endsWith('/studio/builder/validate'))return ok({valid:true,execution_order:['bars','fast']});
   if(url.endsWith('/studio/builder/preview-stored'))return ok({bars_evaluated:300,latest:'LONG'});
   if(url.endsWith('/studio/builder/preview'))return ok({bars_evaluated:2,latest:'WAIT'});
   if(url.endsWith('/studio/drafts'))return ok({items:studioVersions.length
     ? [{id:studioVersions[0].draft_id,name:studioVersions.at(-1).workflow.name,version:studioVersions.length}] : []});
   if(url.includes('/studio/drafts/')&&method==='PUT'){
     const version={draft_id:url.split('/').at(-1),version:studioVersions.length+1,created_at:now,created_by:'risk-ops',workflow:body.workflow};
     studioVersions.push(version);return ok(version);
   }
   if(url.includes('/studio/drafts/')&&url.endsWith('/versions'))return ok({items:[...studioVersions].reverse()});
   if(url.includes('/studio/drafts/')&&url.endsWith('/promote'))return ok({...studioVersions[0],promoted_graph_code:body.code,promoted_graph_version:1});
   if(url.includes('/studio/drafts/')&&url.includes('/versions/'))return ok(studioVersions[Number(url.split('/').at(-1))-1]);
   if(url.endsWith('/intel/screener?timeframe=H1'))return ok({items:[
     {instrument:'EURUSD',broker_symbol:'EURUSD',account_id:'a1',account_code:'VTM_A',provider_name:'Demo',regime:'TREND_UP',bar_time:now,features:{close:1.0853}},
   ]});
   if(url.endsWith('/studio/scan-runs/profiles')&&method==='PUT'){
     savedProfile=body;return ok({name:body.name});
   }
   if(url.endsWith('/studio/scan-runs/profiles'))return ok({items:savedProfile?[savedProfile]:[]});
   if(url.endsWith('/studio/scan-universe'))return ok({
     instruments:[{symbol:'EURUSD',name:'Euro',has_source:true},{symbol:'XAUUSD',name:'Gold',has_source:true},
       ...(largeScanCatalog?Array.from({length:99},(_,i)=>({symbol:'BATCH'+i})):[]),
       ...(beyondRegistryLimit?Array.from({length:1100},(_,i)=>({symbol:'REG'+i})):[]),
       ...(brokerSymbolCollected?[{symbol:'BROKER1255',has_source:true}]:[])],
     strategies:[...registeredStrategies,...(largeScanCatalog?Array.from({length:100},(_,i)=>({code:'BATCH_STRAT_'+i})):[])]
   });
   if(url.endsWith('/md/sources'))return ok({items:[{account_id:'a1',account_code:'VTM_A',provider_name:'Demo'}]});
   if(url.endsWith('/md/catalog/a1/collect')&&method==='POST'){
     brokerSymbolCollected=true;
     return ok({created:[{broker_symbol:'BROKER1255.r',active_instrument:'BROKER1255'}],errors:[],skipped:[]});
   }
   if(url.includes('/md/catalog/a1?')){
     const query=new URL(url,w.location.href).searchParams;
     const matching=query.get('q')==='BROKER1255';
     const offset=Number(query.get('offset'));
     const row=matching?{broker_symbol:'BROKER1255.r',display_symbol:'BROKER1255',asset_class:'FX',
       suggested_instrument:'BROKER1255',...(brokerSymbolCollected?{plan_id:'broker-plan',plan_status:'ACTIVE',
       linked_instrument:'BROKER1255',linked_instrument_status:'ACTIVE'}:{})}:
       {broker_symbol:'BROKER'+offset,display_symbol:'BROKER'+offset,asset_class:'FX'};
     return ok({items:[row],total:matching?1:1256,catalogue_size:1256,facets:{asset_class:[{k:'FX'}]}});
   }
   if(url.endsWith('/md/jobs')&&method==='POST')return ok({id:'broker-backfill'},201);
   if(url.endsWith('/studio/scan-readiness'))return ok({requested:10302,ready:10201,checked_at:now,
     items:[{instrument:'EURUSD',ready:1,blocked:101,reasons:{'Strategy has no approved version':101}}]});
   if(url.endsWith('/studio/scan-runs')&&method==='POST'){
     backgroundScanRequest=body;
     const run=backgroundRun();savedRuns.push(run);return ok(run);
   }
   if(url.includes('/studio/scan-runs?'))return ok({items:savedRuns.map(run=>({...run,instruments:101,strategies:102})).reverse(),has_more:false});
   if(/\/studio\/scan-runs\/batch-\d+\/results\?/.test(url))return ok({items:[{
     instrument_symbol:'EURUSD',strategy_code:'EMA_EXISTING',strength:85,status:'SIGNAL',side:'BUY',
     version:2,strategy_version_id:'scan-version',timeframe:'H1',account_id:'a1',broker_symbol:'EURUSD',account_code:'VTM_A',
     entry:'1.0860',stop_loss:'1.0840',take_profit:'1.0900',reason:'EMA rules matched'
   }],has_more:false});
   if(/\/studio\/scan-runs\/batch-\d+$/.test(url))return ok(savedRuns.find(run=>url.endsWith('/'+run.id)));
   if(url.includes('/intel/instrument/a1/EURUSD?'))return ok({snapshots:[],history:[],timeframe:'H1'});
   if(url.endsWith('/intel/watchlists'))return ok({items:[{id:'test-watchlist',name:'Research FX',items:[{account_id:'a1',broker_symbol:'EURUSD'},{account_id:'a1',broker_symbol:'UNCOLLECTED'}]}]});
   if(url.endsWith('/studio/signal-scan')&&method==='POST'){
     const candidate={bar_time:now,strategy_code:'EMA_EXISTING',version:2,account_code:'VTM_A',broker_symbol:'EURUSD',
       timeframe:'H1',status:'SIGNAL',side:'BUY',strength:85,entry:'1.0860',stop_loss:'1.0840',take_profit:'1.0900'};
     if(body.instrument_symbols.length===1 || body.strategy_codes.length===1)return ok({scanned_at:now,items:body.instrument_symbols.flatMap(symbol=>[
       {...candidate,instrument_symbol:symbol},
       ...(body.strategy_codes.length===1?[]:[{instrument_symbol:symbol,strategy_code:'EMA_NEW',status:'WAIT',reason:'Strategy has no approved version'}]),
     ])});
     if(++strategyScanAttempts===1)return fail(503,'Market data temporarily unavailable');
     return ok({scanned_at:now,items:[
       {...candidate,instrument_symbol:'XAUUSD',broker_symbol:'XAUUSD',strength:94,side:'SELL',entry:'2670',stop_loss:'2675',take_profit:'2660'},
       {...candidate,instrument_symbol:'EURUSD'},
       ...(body.strategy_codes.includes('EMA_NEW')?[{...candidate,strategy_code:'EMA_NEW',instrument_symbol:'EURUSD',strength:80}]:[]),
       {instrument_symbol:'XAUUSD',strategy_code:'EMA_NEW',status:'WAIT',reason:'No new entry trigger'},
     ]});
   }
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
   if(url.includes('/trading/risk/decisions'))return ok({items:[{
     id:'r1',order_id:'o1',at:now,account_code:'VTM_A',symbol:'EURUSD',side:'BUY',requested_qty:'0.001',approved_qty:'0.001',
     approved:true,reasons:[],inputs:{mode:'PAPER',position_before:'0',position_after:'0.001',limits:{MAX_ORDER_QTY:'0.01'},
       evaluated_limits:{MAX_ORDER_QTY:{value:'0.001',threshold:'0.01',scope:'ACCOUNT',breached:false}}}
   }]});
   if(url.includes('/trading/portfolio/accounts'))return ok({items:[{account_id:'a1',account_code:'VTM_A',currency:'USD',equity:'10002',day_pnl:'2',drawdown_pct:'0'}]});
   if(url.includes('/trading/portfolio/positions'))return ok({items:[
     {id:'p1',account_id:'a1',account_code:'VTM_A',symbol:'EURUSD',instrument_id:'i1',mode:'PAPER',quantity:'0.1',mark:null,unrealised_pnl:'0',quote_currency:'USD',account_currency:'USD'},
     {id:'p2',account_id:'a1',account_code:'VTM_A',symbol:'EURUSD',instrument_id:'i1',mode:'BROKER',quantity:'0.2',mark:'1.1',quote_currency:'USD',account_currency:'USD'}
   ]});
   if(url.endsWith('/trading/portfolio/flatten')&&method==='POST')return ok({orders:[{state:'FILLED'}]});
   if(url.includes('/trading/fills?position_id=p1'))return ok({items:[{id:'f1',order_id:'o1',side:'BUY',quantity:'0.1',price:'1.08011',at:now,currency:'USD',realised_pnl:'0'}]});
   if(url.includes('/trading/risk/exposure'))return ok({instruments:[],accounts:[]});
   if(url.includes('/trading/risk/limits'))return ok({items:[],kinds:{MAX_DAILY_LOSS:'Largest loss today (account currency); breach stops new orders',MAX_POSITION_QTY:'Largest net position per instrument (contracts / lots)'}});
   if(url.includes('/trading/algorithms'))return ok({items:[{id:'EMA_CROSSOVER',label:'EMA crossover',description:'Long when the fast EMA crosses above the slow EMA',equation:'LONG when EMA(fast) crosses above EMA(slow)',strength_equation:'strength = 100 × |gap| ÷ ATR',
     schema:{properties:{timeframe:{enum:['M5','H1'],default:'H1'},direction:{default:'BOTH'},fast:{type:'integer',default:9},slow:{type:'integer',default:21}}}},
     {id:'SIGNAL_BLEND',label:'Signal blend',description:'Weighted Stage 3 signals',equation:'combined = Σ wᵢ · scoreᵢ ÷ Σ |wᵢ|',strength_equation:'strength = clamp(|combined|, 10, 100)',
     schema:{properties:{timeframe:{enum:['M5','H1'],default:'H1'},direction:{default:'BOTH'},components:{type:'array'},entry:{type:'number',default:40},exit:{type:'number',default:10}}}}]});
   if(url.includes('/trading/sources'))return ok({items:[{account_id:'a1',account_code:'VTM_A',broker_symbol:'EURUSD',instrument:'EURUSD',first_bar:now,last_bar:now}]});
   if(url.endsWith('/trading/backtests')&&method==='POST')return ok({id:'b1',metrics:{net_pnl:'812.40',return_pct:'0.81',trades:14,win_rate_pct:57.1,profit_factor:1.6,max_drawdown_pct:'0.42',sharpe:1.3,exposure_pct:48,currency:'USD'},
     equity:[{t:now,equity:'100000'},{t:now,equity:'100812.4'}],trades:[{side:'LONG',entry_time:now,entry:'1.08',exit_time:now,exit:'1.081',pnl:'100.00',reason:'EMA cross'}]},201);
   if(url.includes('/trading/backtests'))return ok({items:[]});
   if(url.endsWith('/trading/quickstart')&&method==='POST')return ok({strategy:{code:'EMA_EURUSD_M5'},steps:['strategy EMA_EURUSD_M5','version 1 (EMA_CROSSOVER)','approved','paper deployment','activated'],activated:true,next:'Running on paper.'},201);
   if(url.endsWith('/trading/quickstart'))return ok({active:calls.some(c=>c.url.endsWith('/trading/quickstart')&&c.method==='POST')?1:0,strategies:0,deployments:0,
     sources:[{account_id:'a1',account_code:'VTM_A',environment:'DEMO',broker_symbol:'EURUSD',instrument:'EURUSD',ready:true}],
     presets:[{id:'EMA_FAST',label:'Fast trend · EMA 3/8 cross',algorithm:'EMA_CROSSOVER',parameters:{fast:3,slow:8},why:'Many signals.'}]});
   if(url.endsWith('/trading/workspaces')&&method==='PUT'){
     savedWorkspaces={...body,revision:body.revision+1};return ok(savedWorkspaces);
   }
   if(url.endsWith('/trading/workspaces'))return ok(savedWorkspaces);
   if(/\/trading\/workspaces\/\d+\/scan$/.test(url)){
      if(method==='POST'){
        const desk=savedWorkspaces.items.find(d=>url.endsWith('/'+d.number+'/scan'));
        deskScan={id:'desk-scan',status:'QUEUED',total:desk.scan.instrument_symbols.length*desk.scan.strategy_codes.length,
          instruments:desk.scan.instrument_symbols,strategies:desk.scan.strategy_codes,created_at:now,updated_at:now};
      }
      return ok(deskScan);
    }
    if(url.endsWith('/scan/desk-scan/cancel')){deskScan={...deskScan,status:'CANCELLED'};return ok(deskScan);}
    if(url.endsWith('/scan/desk-scan/resume')){deskScan={...deskScan,status:'QUEUED'};return ok(deskScan);}
    if(url.includes('/scan-runs/desk-scan/results?'))return ok({items:[],has_more:false});
    if(url.endsWith('/trading/workspaces/catalogue'))return ok({
     accounts:[{id:'a1',code:'VTM_A',environment:'DEMO'},{id:'a2',code:'LIVE_A',environment:'LIVE'}],
     instruments:[{id:'i1',symbol:'EURUSD',name:'EURUSD'}]});
   if(url.includes('/trading/workspaces/snapshot')){
     const q=new URL(url,w.location.origin).searchParams;
     const live=q.get('account')==='a2'&&q.get('mode')==='LIVE';
     const waiting=q.get('deployment')==='dep-2';
     return ok({sources:q.has('instrument')?[{account_id:'a1',code:'VTM_A',broker_symbol:'EURUSD'}]:[],
       deployments:live?[{id:'dep-1',code:'EMA_FAST',status:'ACTIVE',symbol:'EURUSD',timeframe:'H1',last_evaluation:{
         outcome:'TARGET_UNCHANGED',bar_time:now,timeframe:'H1',source_account:'a1',source_symbol:'EURUSD',target:'LONG',reason:'Rule unchanged'}},
         {id:'dep-2',code:'TREND',status:'ACTIVE',symbol:'EURUSD',timeframe:'H1'}]:[],
       evaluation_decision:waiting?{id:pendingWorkspaceDecision}:null,positions:[],
       orders:live?[{id:'unrelated',symbol:'OTHER',state:'FILLED',side:'SELL',quantity:'1',created_at:now}]:[],
       decisions:live?[waiting?workspacePending:{id:'d9',symbol:'EURUSD',strategy_code:'EMA_FAST',
         outcome:'ORDERED',signal_side:'BUY',side:'BUY',target:'LONG',order_id:'o1',created_at:now,bar_time:now}]:[]});
   }
   if(url.includes('/trading/workspaces/decision/'+pendingWorkspaceDecision))return ok({decision:workspacePending,items:[],checks:[]});
   if(url.includes('/trading/workspaces/decision/d9'))return ok({items:[{id:'o1',client_order_id:'LIVE-TEST',
     state:'PARTIALLY_FILLED',side:'BUY',quantity:'1',filled_qty:'0.1',risk:{approved:true},
     fills:[{id:'f1',at:now,quantity:'0.1',price:'1.10'}]}]});
   if(url.includes('/trading/execution/flow')&&url.includes('intent_id='+pendingWorkspaceDecision))return ok({items:[{
     ...workspacePending,mode:'BROKER',account_code:'LIVE_A',quantity:'1',side:'BUY',reasons:[],timeframe:'H1',
   }]});
   if(url.includes('/trading/posttrade/overview'))return ok({open_total:1,open_breaks:{POSITION_MISMATCH:1},unknown_orders:0,fills_today:3,avg_latency_today_ms:142,
     accounts:[{account_id:'a1',code:'VTM_A',at:now,kind:'BROKER',status:'BREAKS',environment:'DEMO',breaks:1,
       detail:{broker_read:'COMPARED',internal_books:2,broker_positions:1,cached_deals_checked:3}},
       {account_id:'a2',code:'NEW_ACCOUNT',environment:'LIVE',breaks:0}]});
   if(url.includes('/trading/posttrade/breaks/b1')&&method==='POST')return ok({break:{id:'b1',state:'ACCEPTED'},result:null});
   if(url.includes('/trading/posttrade/breaks'))return ok({items:[{id:'b1',account_id:'a1',account_code:'VTM_A',kind:'POSITION_MISMATCH',broker_symbol:'EURUSD',corei_value:'0.3',broker_value:'0.4',difference:'-0.1',
     first_seen:now,last_seen:now,state:'OPEN'}]});
   if(url.includes('/trading/posttrade/tca'))return ok({by:'account',currency:'USD',slippage_cost_total:'12.5',commission_total:'-2.1',rows:[{label:'VTM_A',orders:4,fill_rate_pct:'100',reject_rate_pct:0,fills:4,
     avg_slippage_bps:'0.19',slippage_cost:'12.5',price_improved:1,worse_than_arrival:3,latency_avg_ms:140,latency_p95_ms:210,latency_max_ms:230,commission:'-2.1'}],note:'Slippage = fill vs arrival.'});
   if(url.includes('/trading/risk/stress'))return ok({scenarios:[{id:'s1',name:'Everything −5%',description:'',builtin:true,shocks:[{match:'ALL',value:'',pct:'-5'}],total:'-540.20',by_account:[{account:'VTM_A',pnl:'-540.20'}]}],missing:[]});
   if(url.includes('/trading/formulas'))return ok({stages:{Signals:[{name:'EMA trend (EMA_TREND)',equation:'score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100)',variables:'fast = 9, slow = 21',code:'trading/signal_library.py · EMA_TREND'}],
     Risk:[{name:'Margin and leverage',equation:'margin = Σ value ÷ account leverage',code:'trading/risk.py: margin'}],Intelligence:[{name:'RSI(14) — rsi14',equation:'RSI = 100 − 100 ÷ (1 + RS)',code:'intel/indicators.py: rsi'}]}});
   if(url.includes('/trading/signal-library/catalogue'))return ok({items:[{kind:'EMA_TREND',label:'EMA trend',family:'Trend',defaults:{fast:9,slow:21,scale:1},equation:'score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100)',detail:'Trend',horizon:'days'},
     {kind:'RSI_REVERSION',label:'RSI reversion',family:'Mean reversion',defaults:{period:14},equation:'score = 2 × (50 − RSI)',detail:'Reversion',horizon:'hours'}]});
   if(url.includes('/trading/signal-library/definitions')&&method==='POST')return ok({id:'d9',...body,version:1},201);
   if(url.includes('/trading/signal-library/definitions'))return ok({items:[{id:'d1',code:'EMA_TREND',name:'EMA trend 9/21',kind:'EMA_TREND',family:'Trend',parameters:{fast:9,slow:21,scale:1},threshold:'20',timeframes:['M5','H1'],status:'ACTIVE',builtin:true,
     equation:'score = clamp(100 × (EMA(fast) − EMA(slow)) ÷ (scale × ATR14), −100, +100)'}]});
   if(url.includes('/trading/signal-library/overview'))return ok({timeframe:'H1',definitions_active:12,definitions_total:13,instruments:1,scores:12,long:5,short:3,flat:4,flips_24h:7,last_computed:now,instruments_by_timeframe:{H1:1},
     strongest:[{code:'EMA_TREND',account_code:'VTM_A',broker_symbol:'EURUSD',score:'62.4',direction:'LONG',worked:'x',bar_time:now,threshold:'20'}],consensus:[{account_code:'VTM_A',broker_symbol:'EURUSD',account_id:'a1',score:23.4,agree_pct:67,n:12}]});
   if(url.includes('/trading/signal-library/events'))return ok({items:[{id:1,code:'RSI_REVERSION',account_code:'VTM_A',broker_symbol:'EURUSD',timeframe:'H1',bar_time:now,from_direction:'FLAT',to_direction:'SHORT',score:'-34.2',previous_score:'12'}]});
   if(url.includes('/trading/signal-library/instrument'))return ok({broker_symbol:'EURUSD',timeframes:['M5','H1'],signals:['EMA_TREND'],cells:{EMA_TREND:{M5:{code:'EMA_TREND',timeframe:'M5',score:'40',worked:'a'},H1:{code:'EMA_TREND',timeframe:'H1',score:'-30',worked:'b'}}},
     consensus:{M5:{score:40,agree_pct:100,n:1},H1:{score:-30,agree_pct:100,n:1}},overall:{score:5,agree_pct:50,n:2}});
   if(url.includes('/trading/signal-library/correlation'))return ok({codes:['EMA_TREND','SMA_TREND'],matrix:[[1,0.91],[0.91,1]],redundant:[{a:'EMA_TREND',b:'SMA_TREND',r:0.91}],bars:1000});
   if(url.includes('/trading/signal-library/explain'))return ok({symbol:'EURUSD',timeframe:'H1',bars:600,bar_source:'BROKER',last_bar:now,last_close:'1.0861',closes:[],consensus:{score:23.4,agree_pct:67,n:12},items:[]});
   if(url.includes('/trading/signal-library/research-all'))return ok({symbol:'EURUSD',timeframe:'H1',bars:900,horizons:[1,5],items:[{code:'EMA_TREND',family:'Trend',verdict:'PREDICTIVE',best_horizon:5,ic:0.061,ic_t:3.1,hit_rate_pct:55.2,edge_bp:4.1,cost_bp:1.2,coverage_pct:40,turnover_per_bar:0.05,ic_by_horizon:{1:0.03,5:0.061}},
     {code:'RSI_REVERSION',family:'Mean reversion',verdict:'NO_EDGE',best_horizon:1,ic:-0.004,ic_t:-0.2,ic_by_horizon:{1:-0.004,5:0.001}}]});
   if(url.includes('/trading/strategies/monitor'))return ok({items:[{id:'d1',status:'ACTIVE',mode:'PAPER',version:4,strategy_code:'EMA_FAST',version_number:1,algorithm:'EMA_CROSSOVER',symbol:'EURUSD',account_code:'VTM_A',timeframe:'M5',
     last_target:'LONG',handoff:'AUTO',execution:{},position:{quantity:'0.1',unrealised:'12.5',realised:'40',currency:'USD'},live:{trades:3,win_rate_pct:66.7,profit_factor:2.1},drift:{status:'OK'},
     last_signal:{side:'BUY',strength:70,state:'ACTED',bar_time:now,why:'EMA 3/5 crossed up'},last_decision:{outcome:'ORDERED',side:'BUY',quantity:'0.1',created_at:now},signals_today:2,orders_today:1,pending:0}],
     summary:{active:1,paused:0,proposed:0,paper:1,real:0,long:1,short:0,drifting:0,errors:0,pending:1,signals_today:2,orders_today:1,pnl_by_currency:{USD:{unrealised:'12.5',realised:'40'}}}});
   if(url.includes('/trading/execution/decisions/'))return ok({state:url.endsWith('/decline')?'DECLINED':'ORDERED'});
   if(url.includes('/trading/execution/flow'))return ok({items:[{id:'t1',outcome:'PENDING',handoff:'APPROVE',strategy_code:'EMA_FAST',version_number:1,algorithm:'EMA_CROSSOVER',symbol:'EURUSD',account_code:'VTM_A',mode:'PAPER',
     signal_side:'SELL',strength:66,side:'SELL',quantity:'0.2',target:'SHORT',current_qty:'0.1',target_qty:'-0.1',order_type:'LIMIT',limit_price:'1.0801',time_in_force:'GTC',decision_bid:'1.08',decision_ask:'1.0801',
     bar_time:now,timeframe:'M5',created_at:now,expires_at:new Date(Date.now()+600000).toISOString(),explanation:['EMA 3/5 crossed down'],reasons:[]},
     {id:'t2',outcome:'REFUSED',handoff:'AUTO',strategy_code:'EMA_FAST',symbol:'EURUSD',account_code:'VTM_A',signal_side:'BUY',side:'BUY',quantity:'0.1',target:'LONG',order_id:'o2',order_state:'REJECTED',created_at:now,
     reasons:['MAX_DAILY_LOSS: today −512 beyond −500'],explanation:[]}].filter(x=>!url.includes('outcome=')||url.includes('outcome='+x.outcome)),summary:{today:{ORDERED:3,REFUSED:1},pending:1,next_expiry:new Date(Date.now()+600000).toISOString(),active_by_handoff:{AUTO:1,APPROVE:1}}});
   if(url.includes('/trading/execution/policies/')&&method==='PUT')return ok({id:'d1',policy:body});
   if(url.includes('/trading/execution/policies'))return ok({items:[{id:'d1',status:'ACTIVE',mode:'PAPER',version:4,strategy_code:'EMA_FAST',version_number:1,algorithm:'EMA_CROSSOVER',symbol:'EURUSD',account_code:'VTM_A',environment:'DEMO',
     policy:{handoff:'AUTO',order_type:'MARKET',limit_mode:'MARKETABLE',limit_offset_ticks:0,time_in_force:'GTC',approval_minutes:10}}]});
   if(url.includes('/trading/signal-library/board'))return ok({timeframe:'H1',items:[{definition_id:'d1',code:'EMA_TREND',family:'Trend',kind:'EMA_TREND',account_id:'a1',account_code:'VTM_A',broker_symbol:'EURUSD',score:'62.4',previous_score:'40',direction:'LONG',threshold:'20',bar_time:now,
     inputs:{ema_fast:1.0861,ema_slow:1.0842,atr14:0.003},worked:'100 × (1.0861 − 1.0842) ÷ (1 × 0.003) = +62.4'}]});
   if(url.includes('/trading/signal-library/blend-preview'))return ok({components:[{kind:'EMA_TREND',weight:2,score:62.4,worked:'100 × (1.0861 − 1.0842) ÷ (1 × 0.003) = +62.4'},{kind:'RSI_REVERSION',weight:1,score:-10,worked:'2 × (50 − 55) = -10.0'}],
     combined:38.3,worked:'2 × +62.4 + 1 × -10.0 ÷ 3 = +38.3',side:'FLAT or hold (inside ±entry)',entry:40,exit:10,symbol:'EURUSD',timeframe:'H1',last_bar:now});
   if(url.includes('/trading/'))return ok({items:[]});
   if(url.includes('/config/accounts'))return ok({items:[{id:'a1',code:'VTM_A',environment:'DEMO',platform:'MT5',status:'ACTIVE'},{id:'a2',code:'ICM_L',environment:'LIVE',platform:'MT5',status:'ACTIVE'}]});
   if(url.includes('/config/instruments'))return ok({items:[
     {id:'i1',symbol:'EURUSD',name:'Euro'},{id:'i2',symbol:'XAUUSD',name:'Gold'},
     ...(largeScanCatalog?Array.from({length:99},(_,i)=>({id:'batch-'+i,symbol:'BATCH'+i})):[])
   ]});
   if(url.endsWith('/config/algorithms'))return ok({items:[{id:'GRAPH',label:'Calculation graph',
     schema:{properties:{timeframe:{enum:['H1']},direction:{enum:['BOTH']},graph_code:{type:'string'}}}}]});
   if(url.endsWith('/config/strategies')&&method==='POST'){
     const strategy={...body,id:'new-strategy',status:'ACTIVE'};
     registeredStrategies.push(strategy);
     return ok(strategy,201);
   }
   if(url.endsWith('/config/strategies/new-strategy/versions')&&method==='POST')return ok({id:'new-version'},201);
   if(url.endsWith('/config/strategies/existing-strategy/versions'))return ok({items:[{id:'scan-version',number:2,state:'APPROVED',algorithm:'EMA_CROSSOVER'}]});
   if(url.includes('/config/strategies/')&&url.endsWith('/versions'))return ok({items:[]});
   if(url.includes('/config/strategies'))return ok({items:[...registeredStrategies,
     ...(largeScanCatalog?Array.from({length:100},(_,i)=>({id:'batch-strategy-'+i,code:'BATCH_STRAT_'+i,status:'ACTIVE'})):[])
   ]});
   if(url.includes('/config/deployments'))return ok({items:[{id:'mapped-eur',strategy_code:'EMA_EXISTING',instrument_symbol:'EURUSD',instrument_id:'i1',account_id:'a1',account_code:'VTM_A',environment:'DEMO',mode:'PAPER',status:'ACTIVE',version:3,version_number:1,data_symbol:'EURUSD',data_connection_code:'DEMO',trading_symbol:'EURUSD',blockers:[],sizing:{method:'FIXED_QTY',quantity:'0.1'},execution:{handoff:'APPROVE',order_type:'MARKET'}}]});
   if(url.includes('/intel/overview'))return ok({covered:3});
   if(url.endsWith('/config/overview'))return ok({user:{username:'risk-ops',roles:['ADMIN']},feeds:{active_plans:2,collectors:1,live:1,problems:0,paused:false},order_sending:[{environment:'DEMO',enabled:false}],
     approvals:0,attention:[],setup:{done:3,total:6,next:null,steps:[]}});
   return fail(404,'Not mocked: '+url);
 };
 w.eval(js);await settle(12);
 // Every screen is its own workspace tab now: look inside the screen on show (overlays fall back to the page).
 const shownView=()=>[...w.document.querySelectorAll('.group.focused .view > div:not([hidden])')][0]||w.document;
 const shownItem=()=>shownView().querySelector('.sw-pane.focused .sw-item:not([hidden])');
 const shown=()=>shownView();
 const $=s=>(shownItem()&&shownItem().querySelector(s))||shown().querySelector(s)||w.document.querySelector(s),$$=s=>{const i=shownItem();const b=i?[...i.querySelectorAll(s)]:[];const a=[...shown().querySelectorAll(s)].filter(x=>!x.closest('.sw-item[hidden]')&&!b.includes(x));const r=[...b,...a];return r.length?r:[...w.document.querySelectorAll(s)];};
 const fill=(el,value)=>{const proto=el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:el.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const nav=label=>$$('.cfg-nav button').find(b=>b.querySelector('span').textContent===label);
 const button=text=>$$('button').find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await settle(4);};
 const go=async v=>{w.dispatchEvent(new w.CustomEvent('corei-goto',{detail:{view:v}}));await settle(12);};

 // Nothing running yet: every stage offers to start a paper strategy in one step.
 assert.match($('.st-start').textContent,/Nothing is running yet/);
 button('Start on paper').click();await settle(6);
 assert.deepEqual(calls.filter(c=>c.url.endsWith('/trading/quickstart')&&c.method==='POST')[0].body,{account_id:'a1',broker_symbol:'EURUSD',preset:'EMA_FAST',timeframe:'M5',quantity:null});
 assert.match($('.st-start').textContent,/Running on paper: EMA_EURUSD_M5/);
 button('Close').click();await settle(4);
 console.log('PASS: start here: an empty trading stage starts a paper strategy on a collected instrument in one step.');

 // Every stage opens as one workspace: every panel (dashboard, work, monitor, settings) on one page.
 const PANELS=['Dashboard','Live signal board','Signal graphs','Explain an instrument','Across timeframes','Signal research','Rank every signal','Signal correlation','Signal flips','Signal library','Calculations & formulas'];
 assert.deepEqual($$('.ws-panel .ws-head b').map(b=>b.textContent),PANELS);
 assert.ok($$('.ws-panel .ws-body').length===PANELS.length,'all panels open');
 assert.ok($('.ws-panel [aria-label="Min strength"], .ws-panel input')!==null,'settings controls are on the workspace');
 $$('.ws-head')[7].click();await settle(2);
 assert.equal($$('.ws-panel .ws-body').length,PANELS.length-1,'a panel closes');
 assert.match(w.localStorage.getItem('corei.workspace.closed.Signals'),/correlation/);
 button('Open all').click();await settle(2);
 // Stage 3 owns the maths: the live board shows each score and, on a click, the equation with this bar's numbers.
 // Stage 3 dashboard: long / short / flat now, the strongest scores, the composite of each instrument, the latest flips.
 assert.match($('.intel-kpis').textContent,/Long5.*Short3.*Flat4.*Flips in 24 h7/);
 assert.match($('.sig-split').textContent,/Composite per instrument.*VTM_A · EURUSD.*\+23\.4.*67% agree/);
 assert.match($('.sig-split').textContent,/RSI_REVERSION.*flat.*short/i);
 const boardPanel=$$('.ws-panel').find(p=>/Live signal board/.test(p.querySelector('.ws-head').textContent));
 assert.match(boardPanel.querySelector('thead').textContent,/Composite/);
 const cell=boardPanel.querySelectorAll('tbody td.sig-cell')[1];assert.equal(cell.textContent,'+62.4');cell.click();await settle(4);
 assert.match(boardPanel.querySelector('.sig-detail').textContent,/100 × \(1\.0861 − 1\.0842\) ÷ \(1 × 0\.003\) = \+62\.4/);
 // A click focuses the instrument in Explain, Timeframes, Correlation and Rank.
 assert.equal(JSON.parse(w.localStorage.getItem('corei.signals.focus')).src,'a1|EURUSD');await settle(6);
 const panel=name=>$$('.ws-panel').find(p=>p.querySelector('.ws-head b').textContent===name);
 globalThis.panel2=panel;
 assert.match(panel('Across timeframes').textContent,/Composite.*\+40\.0.*100%.*−?-30\.0/);
 assert.match(panel('Signal correlation').textContent,/Redundant pairs.*EMA_TREND ~ SMA_TREND \(0\.91\)/);
 assert.match(panel('Explain an instrument').textContent,/Composite of 12 signals.*\+23\.4.*67% agree/);
 panel('Rank every signal').querySelector('button.primary').click();await settle(6);
 assert.deepEqual(calls.find(c=>c.url.endsWith('/signal-library/research-all')).body,{account_id:'a1',symbol:'EURUSD',timeframe:'H1',days:180,horizons:[1,2,5,10,20]});
 assert.match(panel('Rank every signal').textContent,/1EMA_TREND.*predictive.*3\.1.*2RSI_REVERSION.*no edge/i);
 assert.match(panel('Signal flips').textContent,/RSI_REVERSION.*flat.*short/i);
 console.log('PASS: signals: dashboard, board with composite, one focus for explain, timeframes, correlation and the ranking of every signal; flips.');
 assert.match(boardPanel.querySelector('.sig-detail').textContent,/ema_fast 1\.0861/);
 assert.match($$('.ws-panel').find(p=>/Signal library/.test(p.querySelector('.ws-head').textContent)).textContent,/EMA\(fast\) − EMA\(slow\).*fast 9 · slow 21.*±20/);
 assert.match($$('.ws-panel').find(p=>/Calculations/.test(p.querySelector('.ws-head').textContent)).textContent,/EMA trend \(EMA_TREND\).*score = clamp/);
 button('+ New signal').click();await settle(2);
 fill($('[aria-label="Signal code"]'),'EMA_FAST');fill($('[aria-label="Signal name"]'),'Fast EMA');fill($('[aria-label="Signal parameter fast"]'),'5');fill($('[aria-label="Signal reason"]'),'desk preset');await settle();
 await submit($('.sig-edit'));
 const def=calls.find(c=>c.url.endsWith('/trading/signal-library/definitions')&&c.method==='POST').body;
 assert.equal(def.kind,'EMA_TREND');assert.equal(def.parameters.fast,5);assert.equal(def.parameters.slow,21);assert.equal(def.reason,'desk preset');
 console.log('PASS: signal library: live board with worked equations, the formulas with parameters, a new signal saved with its reason, every formula listed.');
 console.log('PASS: workspace: every panel of a stage — dashboards, work, monitors, settings — open on one page, each closable, remembered.');
 // Sub-workspace: inside the Signals workspace, every Signals item is its own sub-tab; split side by side; remembered.
 const subTabs=()=>[...w.document.querySelectorAll('.group.focused .view > div:not([hidden]) .sw-pane')].map(p=>[...p.querySelectorAll('.sw-tab button[role=tab]')].map(b=>b.textContent));
 assert.deepEqual(subTabs(),[['All panels',...PANELS,'Stage map']]);
 nav('Signal flips').click();await settle(4);
 assert.equal(w.document.querySelector('.view > div:not([hidden]) .sw-tab.active button').textContent,'Signal flips','the side bar opens the item as a sub-tab');
 assert.match($('.sw-item:not([hidden])').textContent,/RSI_REVERSION/);
 w.document.querySelector('.view > div:not([hidden]) [aria-label="Close Signal flips"]').click();await settle(3);
 assert.ok(!subTabs()[0].includes('Signal flips'),'a sub-tab closes');
 fill(w.document.querySelector('.view > div:not([hidden]) [aria-label="Open a Signal intelligence item"]'),'flips');await settle(3);
 assert.ok(subTabs()[0].includes('Signal flips'),'and opens again from + Open…');
 w.document.querySelector('.view > div:not([hidden]) [aria-label="Split Signal flips to the right"]').click();await settle(4);
 assert.equal(subTabs().length,2);assert.deepEqual(subTabs()[1],['Signal flips'],'split: the item moves to a pane on the right');
 let sub=JSON.parse(w.localStorage.getItem('corei.subworkspace.v2.Signals'));assert.equal(sub.panes.length,2);assert.equal(sub.focus,1);
 const opened=[];w.open=(...a)=>{opened.push(a);return null;};
 w.document.querySelector('.view > div:not([hidden]) [aria-label="Open Signal flips in new tab"]').click();
 w.document.querySelector('.view > div:not([hidden]) [aria-label="Open Signal flips in new window"]').click();
 const u=new URL(opened[0][0]);assert.deepEqual([u.searchParams.get('view'),u.searchParams.get('section'),u.searchParams.get('detached')],['Signals','flips','1']);
 assert.equal(opened[0][1],'_blank');assert.match(opened[1][2],/popup=yes/);
 w.document.querySelector('.view > div:not([hidden]) [aria-label="Move Signal flips to the pane on the left"]').click();await settle(3);
 assert.equal(subTabs().length,1,'an empty pane closes');
 [...w.document.querySelectorAll('.view > div:not([hidden]) .sw-tab button[role=tab]')].find(b=>b.textContent==='All panels').click();await settle(3);
 console.log('PASS: sub-workspace: each item of a domain is a sub-tab (side bar opens it), closes and reopens, splits side by side, opens in a new tab or window.');

 // Signals: the frame, then the reason a signal was refused, in plain words.
 assert.match($('.st-head').textContent,/Stage 3 · Signal intelligence/);
 assert.match($('.st-head').textContent,/Stage 3 · Signal intelligence ›All panels/);
 assert.equal($('.st-steps'),null,'the stages are the main workspaces, not repeated inside one');
 assert.deepEqual($$('.cfg-nav .cfg-nav-group').map(g=>g.textContent),['Overview','Work','Monitor','Configure']);
 // Stage 4 owns what a strategy decides: its signals, their checks and the decisions; the desk shows every strategy at once.
 await go('Strategies');
 assert.match($('.st-head').textContent,/Stage 4 · Strategy intelligence/);
 assert.match($('.st-head').textContent,/›Dashboard/);
 assert.deepEqual(subTabs(),[['Dashboard','Runtime','Strategy signals','Trade decisions','All panels']]);
 assert.match($('.sd-card').textContent,/EMA_FAST.*running.*paper.*automatic.*long 0\.10.*\+12\.50.*BUY.*70.*acted/);
 promptAnswer='review the levels';$('.sd-card footer button').click();await settle(4);
 assert.deepEqual(calls.find(c=>c.url.endsWith('/config/deployments/d1/pause')).body,{version:4,reason:'review the levels'});
 fill($('[aria-label="Handoff EMA_FAST EURUSD"]'),'APPROVE');await settle(4);
 const hand=calls.find(c=>c.url.endsWith('/trading/execution/policies/d1')&&c.method==='PUT').body;
 assert.equal(hand.handoff,'APPROVE');assert.equal(hand.version,4);assert.equal(hand.reason,'review the levels');assert.equal(hand.order_type,'MARKET');
 console.log('PASS: strategies: the desk shows each strategy with position, P&L, last signal and decision; pause and handoff change from the card.');
 $('.sd-card footer button:nth-last-child(1)').click();await settle(6);
 assert.match($('.st-head').textContent,/Trade decisions/);
 assert.ok(calls.some(c=>c.url.includes('/execution/flow?limit=30&deployment_id=d1')));
 button('Show all deployments').click();await settle(4);
 assert.ok(calls.some(c=>c.url.endsWith('/execution/flow?limit=30')));
 nav('Dashboard').click();await settle(4);
 $('.sd-card footer button:nth-last-child(2)').click();await settle(6);
 assert.ok(calls.some(c=>c.url.includes('/trading/signals?limit=500&deployment_id=d1')));
 assert.match($('.intel-table').textContent,/STALE_PRICE: the last price is 95 s old/);
 button('Show all deployments').click();await settle(4);
 $$('.intel-table tbody tr')[0].click();await settle(6);
 assert.match($('.fw').textContent,/Why it was refused:STALE_PRICE/);
 $$('.fw button').find(b=>b.textContent==='Close').click();await settle();
 console.log('PASS: signals: one frame for every stage, refused signals show their reason and history.');

 // Execution receives every decision from the strategies: the chain, what waits for a trader, execute or decline.
 await go('Execution');
 assert.match($('.st-head').textContent,/›Dashboard/);
 assert.ok(button('Review decisions'));
 nav('All panels').click();await settle(6);
 const ep=name=>$$('.ws-panel').find(p=>p.querySelector('.ws-head b').textContent===name);
 assert.match(ep('From strategies').textContent,/Waiting for execution1.*Ordered today3/);
 assert.match(ep('From strategies').querySelector('.ex-flow').textContent,/EMA_FAST.*SignalSELL.*66.*Decisionshort.*Riskat execution.*waiting for execution/);
 assert.match(ep('From strategies').textContent,/MAX_DAILY_LOSS.*refused/);
 assert.match(ep('Decision queue').textContent,/SELL 0\.20 EURUSD.*limit @ 1\.08010/);
 assert.equal(ep('Decision queue').querySelectorAll('.ex-card').length,1,'only what waits');
 ep('Decision queue').querySelector('.ex-card button.primary').click();await settle(4);
 fill($('[aria-label="Execute quantity"]'),'0.1');fill($('[aria-label="Execute note"]'),'half now');await settle();
 await submit($('.ex-dialog form'));
 assert.deepEqual(calls.find(c=>c.url.endsWith('/execution/decisions/t1/execute')).body,{quantity:'0.1',order_type:'LIMIT',limit_price:'1.0801',note:'half now'});
 ep('Decision queue').querySelectorAll('.ex-card button')[1].click();await settle(4);
 fill($('[aria-label="Decline reason"]'),'news in 5 minutes');await settle();
 await submit($('.ex-dialog form'));
 assert.deepEqual(calls.find(c=>c.url.endsWith('/execution/decisions/t1/decline')).body,{reason:'news in 5 minutes'});
 [...ep('Execution policy').querySelectorAll('button')].find(b=>b.textContent==='Change').click();await settle(4);
 const radios=[...ep('Execution policy').querySelectorAll('.ex-radio input')];radios[1].click();await settle(2);
 fill($('[aria-label="Policy order type"]'),'LIMIT');await settle(2);
 fill($('[aria-label="Policy limit mode"]'),'PASSIVE');fill($('[aria-label="Policy offset"]'),'2');fill($('[aria-label="Policy approval minutes"]'),'5');fill($('[aria-label="Policy reason"]'),'trader reviews gold');await settle();
 await submit($('.ex-policy'));
 assert.deepEqual(calls.filter(c=>c.url.endsWith('/trading/execution/policies/d1')&&c.method==='PUT').at(-1).body,
   {handoff:'APPROVE',order_type:'LIMIT',limit_mode:'PASSIVE',limit_offset_ticks:2,time_in_force:'GTC',approval_minutes:5,version:4,reason:'trader reviews gold'});
 console.log('PASS: execution: decisions from strategies arrive with their chain; a waiting one is executed (smaller, limit) or declined; the policy per strategy is set.');

 // Orders: ticket = one order per ticket key; the order history step by step.
 nav('Order ticket').click();await settle(6);
 fill($('[aria-label="Ticket account"]'),'a1');fill($('[aria-label="Ticket instrument"]'),'i1');fill($('[aria-label="Ticket quantity"]'),'0.1');await settle();
 await submit($('.intel form'));
 const first=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST');
 assert.equal(first.length,1);assert.equal(first[0].body.side,'BUY');assert.match(first[0].body.ticket_key,/^k/);
 await submit($('.intel form'));
 const second=calls.filter(c=>c.url.endsWith('/trading/orders')&&c.method==='POST');
 assert.notEqual(second[1].body.ticket_key,first[0].body.ticket_key,'after a placed order the next ticket is a new order');
 nav('Orders').click();await settle(6);
 fill($('[aria-label="Find an order"]'),'missing-symbol');await settle(2);
 assert.match($('.intel-table').textContent,/No orders/);
 fill($('[aria-label="Find an order"]'),'EURUSD');await settle(2);
 button('EURUSD').click();await settle(8);
 assert.deepEqual($$('.st-timeline li b').map(b=>b.textContent),['new','approved','submitted','acknowledged','filled']);
 button('Inspect risk checks').click();await settle(6);
 assert.ok(calls.some(c=>c.url.includes('/trading/risk/decisions?limit=200&order_id=o1')));
 button('Inspect check').click();await settle(3);
 assert.match($('.fw').textContent,/MAX_ORDER_QTY0\.0010\.01ACCOUNTWithin limit/);
 button('Inspect order').click();await settle(6);
 assert.match($('.fw').textContent,/Order · EURUSD/);
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

 await go('Portfolio');
 assert.match($('.st-head').textContent,/›Accounts/);
 assert.match($('.st-card').textContent,/COREI equity estimate/);
 button('Positions').click();await settle(6);
 assert.equal($('[aria-label="Position account"]').value,'a1');
 fill($('[aria-label="Position mode"]'),'PAPER');await settle(2);
 assert.match($('.intel-table').textContent,/Awaiting mark/);
 assert.equal(button('Flatten broker'),undefined);
 button('Inspect position').click();await settle(4);
 assert.match($('.fw').textContent,/Latest 50 fills.*1\.08011/);
 button('Inspect order').click();await settle(6);
 assert.match($('.fw').textContent,/Order · EURUSD/);
 $$('.fw button').find(b=>b.textContent==='Close').click();await settle();
 await go('Portfolio');
 promptAnswer='close paper book';button('Flatten paper').click();await settle(4);
 assert.equal(calls.findLast(c=>c.url.endsWith('/portfolio/flatten')).body.mode,'PAPER');
 console.log('PASS: portfolio account scope, position mark, exact fills and paper-only flatten.');

 // Saved workspace scope and numbered layouts, including a LIVE ticket handoff without submitting it.
 await go('My Workspaces');
 assert.ok(button('My Workspace 1'));
  assert.ok($('.mw-control-grid'));
  assert.ok(button('Start scan').disabled,'empty workspace must not start an evaluation');
  assert.match($('.mw-control').textContent,/Read-only opportunities/);
 fill($('.mw-scope label:nth-of-type(1) select'),'LIVE');await settle(4);
 assert.deepEqual([...$('.mw-scope label:nth-of-type(2) select').options].map(o=>o.value),['','a2']);
 fill($('.mw-scope label:nth-of-type(2) select'),'a2');fill($('.mw-scope input'),'EURUSD');await settle(6);
 fill($('.mw-scope label:nth-of-type(4) select'),'0');await settle(6);
 assert.ok(calls.some(c=>c.url.includes('mode=LIVE')&&c.url.includes('source=a1')&&c.url.includes('account=a2')));
 assert.match($('.mw-summary').textContent,/EURUSD · LIVE_A · LIVE.*Order created/);
 $$('.mw-panel-heading').find(b=>b.textContent.startsWith('Execution')).click();await settle(5);
 assert.match($('.mw-panel-body').textContent,/LIVE-TEST.*partially filled.*0\.1 @ 1\.10/);
 const workspacePanel=(name)=>$$('.mw-panel-heading').find(b=>b.querySelector('strong').textContent===name);
 workspacePanel('Market data').click();await settle(3);
 assert.equal(workspacePanel('Execution').getAttribute('aria-expanded'),'true');
 assert.equal(workspacePanel('Market data').getAttribute('aria-expanded'),'true');
 assert.equal(new URL(w.location.href).searchParams.get('section'),'workspace-1');
 assert.equal(new URL(w.location.href).searchParams.get('panels'),'market,execution');
 workspacePanel('Market data').click();await settle(3);
 assert.equal(workspacePanel('Execution').getAttribute('aria-expanded'),'true');
 assert.equal(workspacePanel('Market data').getAttribute('aria-expanded'),'false');
 w.history.back();await settle(5);
 assert.equal(workspacePanel('Market data').getAttribute('aria-expanded'),'true');
 assert.equal(workspacePanel('Execution').getAttribute('aria-expanded'),'true');
 w.history.forward();await settle(5);
 assert.equal(workspacePanel('Market data').getAttribute('aria-expanded'),'false');

 const beforeTicket=calls.filter(c=>c.method==='POST'&&c.url.endsWith('/trading/orders')).length;
 button('Open order ticket').click();await settle(8);
 assert.equal($('[aria-label="Ticket mode"]').value,'BROKER');
 assert.equal($('[aria-label="Ticket account"]').value,'a2');
 assert.equal($('[aria-label="Ticket instrument"]').value,'i1');
 assert.equal(calls.filter(c=>c.method==='POST'&&c.url.endsWith('/trading/orders')).length,beforeTicket);
 await go('My Workspaces');
 button('+ New workspace').click();await settle(3);
 assert.ok(button('My Workspace 2'));
 fill($('.mw-scope label:nth-of-type(1) select'),'DEMO');await settle(4);
 assert.deepEqual([...$('.mw-scope label:nth-of-type(2) select').options].map(o=>o.value),['','a1']);
 button('Customize').click();await settle(3);
 fill($('.mw-custom input[maxlength]'),'Desk');
 button('Duplicate workspace').click();await settle(4);
 assert.ok(button('My Workspace 3 · Desk'));
 button('Remove workspace').click();await settle(4);
 button('+ New workspace').click();await settle(4);
 assert.ok(button('My Workspace 4'));
 button('Save workspaces').click();await settle(5);
 assert.deepEqual(savedWorkspaces.items.map(x=>[x.number,x.mode]),[[1,'LIVE'],[2,'DEMO'],[4,'PAPER']]);
 assert.equal(savedWorkspaces.items[0].source,'a1');
 assert.equal(savedWorkspaces.active,4);
 assert.equal(new URL(w.location.href).searchParams.get('section'),'workspace-4');
 button('My Workspace 1').click();await settle(4);
 assert.equal(workspacePanel('Execution').getAttribute('aria-expanded'),'true');
 const workspaceLink=w.location.href;
 const linked=new JSDOM('<div id="root"></div>',{url:workspaceLink,runScripts:'outside-only',pretendToBeVisual:true});
 linked.window.HTMLElement.prototype.scrollIntoView=function(){};
 linked.window.fetch=w.fetch;
 linked.window.eval(js);await settle(10);
 assert.equal(linked.window.document.querySelector('.mw-tabs [aria-current]').textContent,'My Workspace 1');
 assert.equal(linked.window.document.querySelector('.mw-panel-heading[aria-expanded="true"] strong').textContent,'Execution');
 linked.window.close();
 const deploymentPicker=$$('.mw-scope select').at(-1);
 fill(deploymentPicker,'dep-1');await settle(6);
 assert.match($('.mw-summary').textContent,/Bar evaluated · target unchanged/);
 assert.match(workspacePanel('Execution').textContent,/No order linked to this evaluation/);
 fill(deploymentPicker,'dep-2');await settle(6);
 assert.match($('.mw-summary').textContent,/Waiting for approval/);
 assert.match(workspacePanel('Execution').textContent,/No order for this decision/);
 fill($('.mw-summary select'),pendingWorkspaceDecision);await settle(4);
 assert.equal(new URL(w.location.href).searchParams.get('decision'),pendingWorkspaceDecision);
 button('Review selected decision').click();await settle(8);
 assert.ok(calls.some(c=>c.url.includes('intent_id='+pendingWorkspaceDecision)));
 await go('My Workspaces');
 console.log('PASS: unchanged evaluation stays separate from account activity and pending decisions open the exact existing approval queue.');
 console.log('PASS: workspace panels expand independently and copied workspace URLs restore the selected workspace and panels.');

 console.log('PASS: numbered workspace layouts, separate market and execution accounts, LIVE order evidence and scoped ticket handoff.');
  const scanStart=calls.length;
  await settle(4);
  $$('.mw-picker').forEach(p=>{p.open=true;});
  const pickers=$$('.mw-picker');
  pickers[0].querySelector('input[type=checkbox]').click();await settle(3);
  pickers[1].querySelector('input[type=checkbox]').click();await settle(3);
  assert.ok(button('Start scan').disabled,'unsaved scan settings cannot run');
  assert.ok($('.mw-mapping'),'workspace has a mapped deployment roster');
  assert.match($('.mw-mapping').textContent,/EMA_EXISTING.*EURUSD.*VTM_A/,'matching existing deployment is shown inside the desk');
  assert.match($('.mw-mapping').textContent,/APPROVE/,'actual execution handoff is shown');
  assert.ok(button('Inspect mapped deployment'),'exact deployment evidence can be opened in the same workspace');
  button('Save workspaces').click();await settle(6);
  assert.ok(!button('Start scan').disabled,'saved valid scan is available');
  button('Start scan').click();await settle(6);
  assert.match($('.mw-control').textContent,/QUEUED/);
  button('Stop scan').click();await settle(6);
  assert.match($('.mw-control').textContent,/CANCELLED/);
  button('Resume saved scan').click();await settle(6);
  assert.match($('.mw-control').textContent,/QUEUED/);
  button('Stop scan').click();await settle(6);
  const deskWrites=calls.slice(scanStart).filter(c=>c.method!=='GET');
  assert.ok(deskWrites.some(c=>c.url.includes('/workspaces/')&&c.url.endsWith('/scan')));
  assert.ok(deskWrites.every(c=>c.url.includes('/trading/workspaces')),'desk scan must not write other settings or runtime');
  console.log('PASS: saved desk scan start, stop and resume only touch workspace endpoints.');

 // Post-trade: breaks with their actions, execution quality.
 await go('Post-trade');
 assert.match($('.st-head').textContent,/Stage 8 · Post-trade/);
 assert.match($('.intel-kpis').textContent,/Open breaks1/);
 assert.match($('.intel-table').textContent,/3 cached deals checked/);
 assert.match($('.intel-table').textContent,/NEW_ACCOUNT.*never checked/i);
 button('1 · Review').click();await settle(6);
 assert.equal($('[aria-label="Break account"]').value,'a1');
 assert.match($('.intel-table').textContent,/position quantity differs.*EURUSD.*0\.3.*0\.4/i);
 assert.ok(calls.some(c=>c.url.includes('/posttrade/breaks?state=OPEN&account_id=a1')));
 assert.match($('.intel a').getAttribute('href'),/state=OPEN&account_id=a1/);
 promptAnswer='known difference, broker fixing';button('Accept as known').click();await settle(4);
 assert.deepEqual(calls.filter(c=>c.url.endsWith('/posttrade/breaks/b1')&&c.method==='POST')[0].body,{action:'ACCEPT',note:'known difference, broker fixing'});
 nav('Execution quality').click();await settle(6);
 assert.match($('.intel-table').textContent,/0\.19 bp.*140 · 210 · 230 ms/);
 fill($('[aria-label="TCA mode"]'),'BROKER');await settle(4);
 assert.match($('.intel a').getAttribute('href'),/mode=BROKER/);
 console.log('PASS: post-trade: per-account comparison coverage, scoped breaks, audited decisions and mode-aware TCA export.');

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
 // Stage 4 combines Stage 3 signals: the SIGNAL_BLEND recipe with weights, and the combined score worked out on the last bar.
 fill($('[aria-label="Backtest algorithm"]'),'SIGNAL_BLEND');await settle(4);
 assert.match($('.sig-blend').textContent,/combined = \(1 × EMA_TREND\) ÷ 1/);
 button('+ Add signal').click();await settle(2);
 fill($('[aria-label="Blend component 2"]'),'RSI_REVERSION');fill($('[aria-label="Blend weight 1"]'),'2');await settle(2);
 assert.match($('.sig-blend').textContent,/combined = \(2 × EMA_TREND \+ 1 × RSI_REVERSION\) ÷ 3/);
 fill($('[aria-label="Blend preview instrument"]'),'a1|EURUSD');await settle(2);
 button('Work it out on the last bar').click();await settle(4);
 assert.match($('.sig-blend .sig-detail').textContent,/÷ 3 = \+38\.3/);
 const prev=calls.find(c=>c.url.endsWith('/signal-library/blend-preview')).body;
 assert.deepEqual(prev.components.map(c=>[c.kind,c.weight]),[['EMA_TREND',2],['RSI_REVERSION',1]]);
 await submit($('.intel form'));
 const bt2=calls.filter(c=>c.url.endsWith('/trading/backtests')&&c.method==='POST').pop().body;
 assert.equal(bt2.algorithm,'SIGNAL_BLEND');assert.equal(bt2.parameters.components.length,2);assert.equal(bt2.parameters.entry,40);
 console.log('PASS: strategies: a signal-blend recipe combines library signals with weights and shows the combined score worked out.');

 // Market data and Market intelligence open the same way: every panel on one page.
 await go('Intelligence');
 ['Dashboard','Screener','Correlation','Rules & alerts','Indicators','Engine settings','Calculations & formulas'].forEach(t=>assert.ok($$('.ws-head b').some(b=>b.textContent===t),'intelligence panel '+t));
 await go('Market data');
 ['Dashboard','Collection plans','Quality','Backfill','Controls','Archive','Calculations & formulas'].forEach(t=>assert.ok($$('.ws-head b').some(b=>b.textContent===t),'market data panel '+t));
 console.log('PASS: market data and market intelligence: every panel on one workspace page too.');

 // Signals › Signal graphs: every library signal as a graph; each step with its formula and this bar's numbers (plain view without a canvas).
 await go('Signals');
 w.dispatchEvent(new w.CustomEvent('corei-goto',{detail:{view:'Signals',section:'graphs'}}));await settle(16);
 assert.ok($$('.cg-lib button').some(b=>/EMA trend/.test(b.textContent)),'the graph library lists the built-in');
 const steps=$('.cg-plain');assert.ok(steps,'the plain view lists the steps');
 assert.match(steps.textContent,/100 \* \(fast - slow\) \/ \(scale \* atr\)  =  100 \* \(/);
 assert.equal(calls.filter(c=>c.url.endsWith('/calc/preview')).pop().body.account_id,'a1');
 assert.match($('.cg-out').textContent,/Signal on the last bar/);
 promptAnswer='DESK_EMA';button('Copy to change').click();await settle(8);
 const cp=calls.find(c=>c.url.includes('/calc/graphs/SIG_EMA_TREND/copy'));assert.equal(cp.body.code,'DESK_EMA');
 console.log('PASS: signal graphs: the library signal is a graph with every step\'s numbers; a built-in is copied to change it.');

 // Calculation flow (n8n): the whole platform as one workflow, and every formula as its own graph worked out with numbers.
 await go('Flow');await settle(10);
 const plain=$('.pf-plain');assert.ok(plain,'the flow lists every stage where no canvas is drawn');
 assert.equal($$('.pf-plain h5').length,8);
 assert.equal($$('.pf-plain li').length,CALC.flow.nodes.length);
 assert.match(plain.textContent,/ATR\(14\).*0\.0912/s);
 $$('.pf-plain button').find(b=>b.textContent==='Sizing: % of equity at an ATR stop').click();await settle(4);
 assert.match($('.pf-side').textContent,/quantity = risk_money \/ \(stop_distance \* contract \* fx\)/);
 $$('.pf-side button').find(b=>b.textContent==='Try it with numbers').click();await settle(10);
 assert.ok(calls.some(c=>c.url.includes('/calc/formulas/rk_size_pct/evaluate')),'the formula is worked out by the server');
 assert.match($('.fg-plain').textContent,/risk_money = equity \* r_pct \/ 100 \* weight/);
 console.log('PASS: calculation flow: every calculation of the 8 stages in one workflow; each formula opens as inputs → steps → result with numbers.');

 // Strategy Studio keeps research, testing and version history in the same workspace.
 await go('Strategy Studio');
 const studio=$('.studio');
 const studioButton=text=>[...studio.querySelectorAll('button')].find(b=>!b.closest('[hidden]')&&b.textContent.trim()===text);
 const clickStudio=async text=>{await tick();const control=studioButton(text);assert.ok(control&&!control.disabled,text);control.click();await settle(3);};
 const studioStep=()=>studio.querySelector('.studio-steps [aria-current="step"]');
 const openStage=async label=>{
   const control=[...studio.querySelectorAll('.studio-steps button')].find(b=>b.querySelector('strong').textContent===label);
   assert.ok(control&&!control.disabled,label);
   control.click();await settle(3);
   assert.equal(control.getAttribute('aria-current'),control.closest('.studio-research-steps')?'page':'step');
 };
 const studioField=(label)=>[...studio.querySelectorAll('label')].find(item=>!item.closest('[hidden]')&&item.firstChild?.textContent?.trim()===label).querySelector('input,select');
 assert.equal(studioButton('Build strategy').getAttribute('aria-pressed'),'true');
 assert.deepEqual([...studio.querySelectorAll('.studio-steps button strong')].map(node=>node.textContent),
   ['Start','Build rules','Validate & preview','Save revision','Review & use']);
 assert.equal(studioStep().querySelector('strong').textContent,'Start');
 await openStage('Review & use');
 assert.ok(studioButton('Save a revision'));
 assert.equal(studioButton('Create calculation graph draft'),undefined);
 await openStage('Start');
 fill(studioField('Template'),'ema_trend_20_50');
 await clickStudio('Load into draft');
 assert.match(studio.querySelector('.studio-draft-title').textContent,/EMA research template/);
 await clickStudio('Add node');
 assert.equal(w.document.activeElement.type,'search');
 fill(studio.querySelector('input[type=search]'),'indicator');await tick();
 const nodeButton=studio.querySelector('.studio-node-list button');assert.match(nodeButton.textContent,/indicator.ema/);
 nodeButton.click();await settle(3);
 fill(studioField('period'),'35');
 await clickStudio('Next: validate & preview');
 assert.equal(studioStep().querySelector('strong').textContent,'Validate & preview');
 await clickStudio('Validate draft');
 assert.equal(calls.find(c=>c.url.endsWith('/studio/builder/validate')).body.nodes.at(-1).params.period,35);
 await clickStudio('Preview stored bars');
 assert.match(studio.textContent,/LONG on the last closed bar.*300 bars evaluated/);
 await clickStudio('Paste OHLC JSON');
 const bars=[{open:1,high:3,low:1,close:2},{open:2,high:4,low:1,close:3}];
 fill(studio.querySelector('textarea'),JSON.stringify(bars));
 await clickStudio('Preview closed bars');
 assert.deepEqual(calls.find(c=>c.url.endsWith('/studio/builder/preview')).body.bars,bars);
 assert.match(studioStep().textContent,/Checked/);
 await clickStudio('Next: save revision');
 await clickStudio('Save draft');
 assert.match(studio.querySelector('.studio-draft-title').textContent,/Saved research draft · v1/);
 assert.match(studioStep().textContent,/v1 saved/);
 await openStage('Build rules');
 await clickStudio('Workflow JSON');
 const json=studio.querySelector('#studio-workflow-json');
 fill(json,JSON.stringify({...JSON.parse(json.value),name:'Updated research draft'}));
 assert.doesNotMatch(studio.querySelector('.studio-steps').textContent,/Checked|v1 saved/);
 await openStage('Save revision');
 await clickStudio('Save new version');
 assert.equal(studioVersions.length,2);
 await openStage('Start');
 fill(studioField('Version'),'1');
 await clickStudio('Open version');
 assert.match(studio.querySelector('.studio-draft-title').textContent,/EMA research templateSaved research draft · v1/);
 assert.equal(studioStep().querySelector('strong').textContent,'Build rules');
 await openStage('Review & use');
 fill(studioField('Graph code'),'EMA_STUDIO');
 fill(studioField('Reason'),'Reviewed original draft');await tick();
 await submit(studio.querySelector('.studio-panel:not([hidden]) form'));
 assert.match(studio.textContent,/Created calculation graph draft EMA_STUDIO/);
 assert.match(studioStep().textContent,/Graph created/);
 assert.ok(studioButton('Use this rule in a strategy'));
 await clickStudio('Open graph for review');
 await settle(12);
 assert.equal($('[aria-label="View graph revision"]').value,'1');
 assert.match($('.cg-title').textContent,/EMA_STUDIO.*v1 approved/);
 assert.equal(calls.some(call=>call.url.endsWith('/calc/graphs/EMA_STUDIO/versions/2')),false);
 button('Use this rule in a strategy').click();await settle(12);
 assert.match($('.cfg-main').textContent,/EMA studio rule · v1/);
 const existing=$$('.cfg-table tbody tr').find(row=>row.textContent.includes('EMA_EXISTING'));
 existing.click();await settle(3);
 button('New version').click();await settle(4);
 assert.equal($('[aria-label="Strategy graph"]').value,'EMA_STUDIO');
 assert.equal($('[aria-label="Strategy graph version"]').value,'1');
 $('[aria-label="Close window"]').click();await settle(3);
 button('New strategy').click();await settle(3);
 assert.equal($('[aria-label="Name"]').value,'EMA studio rule');
 fill($('[aria-label="Strategy code"]'),'EMA_NEW');
 await submit($('.cfg-detail > .cfg-form'));await settle(5);
 assert.equal($('[aria-label="Strategy graph version"]').value,'1');
 fill($('[aria-label="Timeframe"]'),'H1');
 fill($('[aria-label="Direction"]'),'BOTH');await tick();
 await submit($('[aria-label="Version form"]'));
 const strategyVersion=calls.find(call=>call.url.endsWith('/config/strategies/new-strategy/versions')&&call.method==='POST');
 assert.equal(strategyVersion.body.algorithm,'GRAPH');
 assert.deepEqual(strategyVersion.body.parameters,{graph_code:'EMA_STUDIO',graph_version:1,timeframe:'H1',direction:'BOTH'});
 console.log('PASS: the Studio opens the exact graph revision, and setup carries the approved rule into existing and new strategies.');
 await go('Strategy Studio');
 await clickStudio('Signal scanner');
 assert.equal(studio.querySelector('.studio-research-steps [aria-current="page"] strong').textContent,'Scan by instrument');
 assert.ok(studioButton('Scan by instrument'));
 assert.ok(studioButton('Scan by strategy'));
 await openStage('Inspect instrument');
 assert.equal(studio.querySelector('.studio-research-steps [aria-current="page"] strong').textContent,'Inspect instrument');
 assert.match(studio.querySelector('[aria-label="Market context"]').textContent,/TREND_UP/);
 assert.equal(studioField('Instrument').value,'EURUSD');
 assert.equal(calls.filter(c=>c.url.endsWith('/studio/signal-scan')).length,0);
 await clickStudio('Open chart');await settle(4);
 assert.ok(calls.some(c=>c.url.includes('/intel/instrument/a1/EURUSD?timeframe=H1')));
 await go('Strategy Studio');
 await openStage('Runs & results');
 assert.match(studio.textContent,/Run a new scan or choose a saved run/);
 await openStage('Inspect instrument');
 await clickStudio('Open scanner for this instrument');
 assert.equal(w.document.activeElement.id,'studio-research-scope');
 assert.equal(studioButton('Scan by instrument').getAttribute('aria-current'),'page');
 const actSelection=async control=>{control.click();await tick();};
 const checks=label=>[...studio.querySelectorAll('fieldset.studio-strategy-filter')].find(field=>field.querySelector('legend').textContent.startsWith(label)).querySelectorAll('input[type=checkbox]');
 assert.ok(checks('Instruments')[0].checked);
 fill(studioField('Use instrument watchlist'),'test-watchlist');await tick();
 assert.match(studio.textContent,/Research FX applied · 1 instruments selected · 1 members excluded/);
 assert.match(studio.querySelector('[aria-label="Watchlist exclusions"]').textContent,/UNCOLLECTED.*Source is not in the collected universe/);
 await clickStudio('Check readiness');
 const quickReadinessScope=calls.findLast(call=>call.url.endsWith('/studio/scan-readiness')).body;
 await clickStudio('Run scan');
 assert.equal(w.document.activeElement.id,'studio-assessment');
 assert.equal(studio.querySelector('.studio-research-steps [aria-current="page"] strong').textContent,'Runs & results');
 const lastScan=()=>calls.filter(c=>c.url.endsWith('/studio/signal-scan')).at(-1).body;
 assert.deepEqual(lastScan(),{instrument_symbols:['EURUSD'],strategy_codes:['EMA_EXISTING','EMA_NEW'],entry_lookback:3,stop_atr:'1.5',reward_risk:'2'});
 assert.deepEqual(lastScan(),quickReadinessScope);
 const ranked=()=>studio.querySelector('[aria-label="Ranked signals"]');
 assert.match(ranked().textContent,/EMA_EXISTING v2.*BUY.*85.*1.0860.*1.0840.*1.0900/);
 assert.deepEqual([...studio.querySelectorAll('.studio-scan-summary dd')].map(el=>el.textContent),['2','1','1','0']);
 assert.match(studio.querySelector('[aria-label="Recorded signals"]').textContent,/STALE_PRICE/);
 await clickStudio('Back to scan');
 await actSelection(checks('Instruments')[1]);
 fill(studioField('Strategies to scan'),'selected');await tick();
 assert.ok(studioButton('Run scan').disabled);
 await actSelection(checks('Strategies')[0]);
 await clickStudio('Run scan');
 assert.deepEqual(lastScan(),{instrument_symbols:['EURUSD','XAUUSD'],strategy_codes:['EMA_EXISTING'],entry_lookback:3,stop_atr:'1.5',reward_risk:'2'});
 assert.match(studio.textContent,/Instrument · EURUSD, XAUUSD.*EMA_EXISTING/);
 assert.match(studio.querySelector('[aria-label="Best matches by selection"]').textContent,/EURUSD.*EMA_EXISTING.*XAUUSD.*EMA_EXISTING/);
 await clickStudio('Back to scan');
 await clickStudio('Scan by strategy');
 assert.ok(checks('Strategies')[0].checked);
 await actSelection(checks('Strategies')[1]);
 await clickStudio('Run scan');
 assert.deepEqual(lastScan(),{instrument_symbols:['EURUSD','XAUUSD'],strategy_codes:['EMA_EXISTING','EMA_NEW'],entry_lookback:3,stop_atr:'1.5',reward_risk:'2'});
 assert.match(studio.querySelector('[role="alert"]').textContent,/Market data temporarily unavailable/);
 assert.equal(studio.querySelector('.studio-research-steps [aria-current="page"] strong').textContent,'Scan by strategy');
 await openStage('Runs & results');
 assert.match(ranked().textContent,/XAUUSD/);
 assert.match(studio.textContent,/Instrument · EURUSD, XAUUSD/);
 await clickStudio('Back to scan');
 await clickStudio('Retry scan');
 assert.equal(studio.querySelector('.studio-research-steps [aria-current="page"] strong').textContent,'Runs & results');
 assert.match(studio.textContent,/Strategy · EMA_EXISTING/);
 assert.match(studio.querySelector('[aria-label="Best matches by selection"]').textContent,/EMA_EXISTING.*XAUUSD.*94.*EMA_NEW.*EURUSD.*80/);
 await actSelection(studio.querySelector('[aria-label="Best matches by selection"] button'));
 assert.equal(studioField('Results by strategy').value,'EMA_EXISTING');
 assert.match(studio.textContent,/Strongest match · XAUUSD · EMA_EXISTING · 94\/100/);
 assert.deepEqual([...ranked().querySelectorAll('tbody tr')].map(row=>row.cells[1].textContent),['XAUUSD','EURUSD']);
 fill(studioField('Results by strategy'),'EMA_NEW');await tick();
 assert.match(studio.querySelector('[aria-label="Scan checks"]').textContent,/XAUUSD.*No new entry trigger/);
 fill(studioField('Scan history'),'1');await tick();
 assert.match(studio.textContent,/Instrument · EURUSD, XAUUSD/);
 assert.match(ranked().textContent,/1.0860.*1.0840.*1.0900/);

 const researchLinks=[...studio.querySelectorAll('summary')].find(node=>node.textContent==='Runtime and research');
 researchLinks.click();
 assert.ok(researchLinks.parentElement.open);
 assert.ok(studioButton('Backtest'));
 largeScanCatalog=true;
 await clickStudio('Back to scan');
 await clickStudio('Refresh data');
 await clickStudio('Scan by instrument');
 fill(studioField('Strategies to scan'),'all');await tick();
 const searchInstruments=studio.querySelector('[aria-label="Search instruments"]');
 fill(searchInstruments,'BATCH');await tick();
 assert.equal(checks('Instruments').length,99);
 await clickStudio('Select all matching');
 assert.match(studio.querySelector('.studio-run-bar').textContent,/10,302 checks.*101 instruments × 102 strategies.*Background run/);
 fill(studioField('Instruments to scan'),'all');await tick();
 assert.equal(studio.querySelector('[aria-label="Instrument scope"] fieldset'),null);
 assert.match(studio.querySelector('.studio-run-bar').textContent,/10,302 checks/);
 await clickStudio('Check readiness');
 const readinessScope=calls.findLast(call=>call.url.endsWith('/studio/scan-readiness')).body;
 assert.match(studio.textContent,/10,302 requested · 10,201 ready · 101 blocked/);
 assert.match(studio.querySelector('[aria-label="Instrument readiness"]').textContent,/Strategy has no approved version \(101\)/);
 fill(studioField('Preset name'),'Full universe');await tick();
 await clickStudio('Save preset');
 assert.equal(savedProfile.name,'Full universe');
 fill(studioField('Run name'),'Market open');await tick();
 await clickStudio('Run scan');
 assert.equal(backgroundScanRequest.request.instrument_symbols.length,101);
 assert.equal(backgroundScanRequest.request.strategy_codes.length,102);
 assert.equal(backgroundScanRequest.name,'Market open');
 assert.deepEqual(backgroundScanRequest.request,readinessScope);
 assert.match(studio.querySelector('[aria-label="Run summary"]').textContent,/EURUSD.*EMA_EXISTING/);
 assert.match(studio.querySelector('[aria-label="Run results"]').textContent,/EURUSD.*EMA_EXISTING.*85/);
 assert.equal(studio.querySelector('[aria-label="Ranked signals"]'),null);
 assert.equal(studio.querySelector('progress[aria-label="Scan progress"]').max,10302);
 await actSelection(studio.querySelector('[aria-label="Run results"] tbody button'));
 assert.match(studio.querySelector('[aria-label="Scan result detail"]').textContent,/EMA rules matched.*1.0860.*1.0840.*1.0900/);
 assert.equal(studioButton('Open chart').disabled,false);
 assert.equal(studioButton('Open backtest').disabled,false);
 await clickStudio('Open backtest');
 assert.equal($('[aria-label="Backtest strategy"]').value,'existing-strategy');
 assert.equal($('[aria-label="Backtest version"]').value,'scan-version');
 assert.equal($('[aria-label="Backtest source"]').value,'a1|EURUSD');
 await go('Strategy Studio');await settle(6);
 await actSelection(studio.querySelector('[aria-label="Run results"] tbody button'));
 await clickStudio('Open chart');
 assert.ok(calls.some(c=>c.url.includes('/intel/instrument/a1/EURUSD?timeframe=H1')));
 await go('Strategy Studio');await settle(6);
 await clickStudio('Rerun with current data');
 assert.equal(savedRuns.length,2);
 assert.deepEqual(savedRuns[0].request,savedRuns[1].request);
 assert.match(studio.querySelector('[aria-label="Run comparison"]').textContent,/Market open.*Market open/);
 await clickStudio('Show quick scan results');
 assert.ok(studio.querySelector('[aria-label="Ranked signals"]'));
 beyondRegistryLimit=true;
 await clickStudio('Back to scan');
 await clickStudio('Refresh data');
 assert.match(studio.querySelector('[aria-label="Instrument scope"]').textContent,/1,201 registered active/);
 fill(studioField('Instruments to scan'),'selected');await tick();
 fill(studio.querySelector('[aria-label="Search instruments"]'),'REG1099');await tick();
 assert.equal(checks('Instruments').length,1);
 await actSelection(checks('Instruments')[0]);
 await clickStudio('Browse broker catalogue');
 fill(studioField('Broker account'),'a1');await settle(6);
 assert.match(studio.textContent,/1,256 broker symbols/);
 await clickStudio('Next symbols');
 assert.ok(calls.some(call=>call.url.includes('/md/catalog/a1?')&&call.url.includes('offset=25')));
 fill(studio.querySelector('[aria-label="Search broker symbols"]'),'BROKER1255');await settle(7);
 assert.match(studio.querySelector('[aria-label="Broker symbol results"]').textContent,/BROKER1255.*Needs a collection plan/);
 await clickStudio('Set up and select');await settle(6);
 assert.ok(brokerSymbolCollected);
 assert.match(studio.querySelector('[aria-label="Broker symbol results"]').textContent,/BROKER1255.*ACTIVE/);
 await clickStudio('Backfill bars');
 await clickStudio('Queue backfill');
 assert.equal(calls.findLast(call=>call.url.endsWith('/md/jobs')&&call.method==='POST').body.plan_id,'broker-plan');
 await clickStudio('Check readiness');
 const expandedScope=calls.findLast(call=>call.url.endsWith('/studio/scan-readiness')).body;
 assert.ok(expandedScope.instrument_symbols.includes('BROKER1255'));
 assert.ok(expandedScope.instrument_symbols.includes('REG1099'));
 await clickStudio('Run scan');
 assert.deepEqual(backgroundScanRequest.request,expandedScope);
 console.log('PASS: broker discovery beyond 1,000 symbols, setup, backfill, and identical readiness and execution scope.');
 console.log('PASS: Strategy Studio build and research workflows, all and selected strategies, instrument ranking, full signal levels, and retry.');

 // Home: the system map — eight stages in order, live figures, the user's path.
 await go('Overview');await settle(6);
 assert.equal($$('.sysmap-flow li').length,8);
 assert.match($('.sysmap-flow').textContent,/Signal intelligence.*3 signals today/);
 assert.ok($$('.sysmap-path button.done').length>=3,'done steps are ticked');
 assert.deepEqual(errors.filter(e=>!/Not mocked/.test(e)),[]);
 console.log('PASS: home shows how COREI works: eight stages with live figures and the user\'s path.');
 w.close();
})().catch(e=>{console.error(String(e&&e.stack||e).slice(0,4000));process.exit(1)});
