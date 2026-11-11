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
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async()=>({ok:true,json:async()=>({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:false,demo_enabled:false,capabilities:[]})});
 w.eval(js);await tick();
 const buttons=()=>[...w.document.querySelectorAll('button')];
 const click=(text)=>{const b=buttons().find(b=>b.textContent.trim()===text);assert.ok(b,'button '+text);b.click();};
 assert.match(w.document.body.textContent,/Overview/);
 assert.ok(w.document.querySelector('.explorer.sidebar'),'side bar open by default');assert.ok(w.document.querySelector('.bottom.toolbox'),'toolbox open by default');
 click('Layout');await tick();click('Four groups');await tick();
 assert.equal(w.document.querySelectorAll('.group').length,4);
 assert.equal(JSON.parse(w.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups.length,4);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'b',ctrlKey:true,bubbles:true}));await tick();assert.equal(w.document.querySelector('.explorer'),null);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'b',ctrlKey:true,bubbles:true}));await tick();assert.ok(w.document.querySelector('.explorer'));
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'j',ctrlKey:true,bubbles:true}));await tick();assert.equal(w.document.querySelector('.bottom.toolbox'),null);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'j',ctrlKey:true,bubbles:true}));await tick();assert.ok(w.document.querySelector('.bottom.toolbox'));
 const sep=w.document.querySelector('[aria-label="Resize explorer"]');const before=Number(sep.getAttribute('aria-valuenow'));sep.dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();assert.equal(Number(sep.getAttribute('aria-valuenow')),before+10);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'p',ctrlKey:true,shiftKey:true,bubbles:true}));await tick();assert.ok(w.document.querySelector('[aria-label="Command palette"]'));
 const input=w.document.querySelector('[aria-label="Search commands"]');Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').set.call(input,'Two rows');input.dispatchEvent(new w.Event('input',{bubbles:true}));await tick();
 const opts=w.document.querySelectorAll('[role="option"]');assert.equal(opts.length,1);opts[0].click();await tick();assert.equal(w.document.querySelectorAll('.group').length,2);assert.equal(w.document.querySelector('.groups').style.flexDirection,'column');
 assert.equal(w.document.querySelector('[aria-label="Command palette"]'),null);
 click('Help');await tick();click('Keyboard shortcuts & workbench guide');await tick();assert.ok(w.document.querySelector('[aria-label="Workbench guide"]'));
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();assert.equal(w.document.querySelector('[aria-label="Workbench guide"]'),null);

 for(const theme of ['Black','Dark Blue','White','Standard','COREI Orbit']) {
   click('Appearance');await tick();click('Color theme: '+theme);await tick();
   assert.equal(w.document.documentElement.dataset.theme,theme);
   assert.equal(JSON.parse(w.localStorage.getItem('corei-os-mini.appearance.v1')).theme,theme);
 }
 click('Appearance');await tick();click('Increase text size');await tick();assert.equal(w.document.documentElement.style.getPropertyValue('--ui-size'),'13px');
 click('Appearance');await tick();click('Toggle status bar');await tick();assert.equal(w.document.querySelector('footer'),null);
 click('Appearance');await tick();click('Toggle status bar');await tick();assert.ok(w.document.querySelector('footer'));
 click('Appearance');await tick();click('Toggle wrapped tabs');await tick();assert.ok(w.document.querySelector('.wrap-tabs'));
 const firstTab=w.document.querySelector('.tab');firstTab.dispatchEvent(new w.MouseEvent('contextmenu',{bubbles:true,clientX:20,clientY:20}));await tick();
 click('Pin tab');await tick();assert.ok(w.document.querySelector('[aria-label="Close Overview in group 1"]').disabled);
 assert.deepEqual(JSON.parse(w.localStorage.getItem('corei-os-mini.pins.v1')),['Overview']);
 firstTab.dispatchEvent(new w.MouseEvent('contextmenu',{bubbles:true}));await tick();click('Unpin tab');await tick();assert.equal(w.document.querySelector('[aria-label="Close Overview in group 1"]').disabled,false);
 click('Navigate');await tick();click('Open Strategies');await tick();
 click('Workspace');await tick();click('Close active tab');await tick();assert.equal(w.document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(),'Home');
 click('Workspace');await tick();click('Reopen last closed tab');await tick();assert.ok([...w.document.querySelectorAll('[role="tab"]')].some(t=>t.textContent==='Strategies'&&t.getAttribute('aria-selected')==='true'));
 console.log('PASS: five themes persist, font size, status toggle, wrapped tabs, tab context menu, pin protection, unpin and reopen.');

 const opened=[];w.open=(...args)=>{opened.push(args);return null;};
 w.document.querySelector('[aria-label="Open Strategies in new tab"]').click();
 w.document.querySelector('[aria-label="Open Strategies in new window"]').click();
 assert.equal(opened.length,2);assert.equal(new URL(opened[0][0]).searchParams.get('view'),'Strategies');
 assert.match(opened[0][2],/noopener/);assert.match(opened[1][2],/popup=yes/);
 // Plain Ctrl+P remains available to the browser.
 const printEvent=new w.KeyboardEvent('keydown',{key:'p',ctrlKey:true,bubbles:true,cancelable:true});w.dispatchEvent(printEvent);assert.equal(printEvent.defaultPrevented,false);
 // Removed views are gone from navigation; Configuration replaces them.
 click('Navigate');await tick();const navigate=w.document.body.textContent;assert.ok(!/Open Providers|Open Accounts|Open Instruments/.test(navigate));click('Open Configuration');await tick();
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
 assert.deepEqual(errors,[]);console.log('PASS: production bundle mounts; four groups persist; keyboard toggles; accessible resizing; filtered palette action; two-row layout; help closes; no runtime errors.');w.close();
 const detached=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/?view=Configuration&detached=1',runScripts:'outside-only',pretendToBeVisual:true});
 const dw=detached.window;dw.HTMLElement.prototype.scrollIntoView=function(){};
 dw.localStorage.setItem('corei-os-mini.workstation.layout.v1','main-layout-sentinel');
 dw.fetch=async(url)=>url.includes('operator/session')?{ok:false,status:401,json:async()=>({detail:'Sign in'})}:{ok:true,json:async()=>({project_id:'corei-os-mini',contract_version:2,release:'t',database_connected:false,demo_enabled:false,capabilities:[]})};
 dw.eval(js);await tick();await tick();
 assert.equal(dw.document.querySelectorAll('.group').length,1);
 assert.match(dw.document.querySelector('.view').textContent,/CONFIGURATION/);
 assert.equal(dw.localStorage.getItem('corei-os-mini.workstation.layout.v1'),'main-layout-sentinel');
 assert.equal(dw.document.querySelector('.explorer'),null);
 // A layout saved by an older release that still names removed views falls back to defaults.
 const old=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/',runScripts:'outside-only',pretendToBeVisual:true});const ow=old.window;ow.HTMLElement.prototype.scrollIntoView=function(){};
 ow.localStorage.setItem('corei-os-mini.workstation.layout.v1',JSON.stringify({version:1,workspace:'Research',activity:'Providers',groups:[['Providers']],active:['Providers'],focus:0,left:true,right:false,bottom:false,axis:'row',leftWidth:220,rightWidth:270,bottomHeight:180,maximized:false}));
 ow.fetch=dw.fetch;ow.eval(js);await tick();await tick();assert.ok(ow.document.querySelector('nav.activity [aria-label="Overview"][aria-current="page"]'));
 console.log('PASS: window actions, detached Configuration workspace, isolated layout and safe fallback from removed views.');dw.close();ow.close();

 // Shell: live status chips, rail shows a screen in place (no tab pile-up), Ctrl+K reaches a section, attention list links.
 const sh=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/',runScripts:'outside-only',pretendToBeVisual:true});const sw=sh.window;sw.HTMLElement.prototype.scrollIntoView=function(){};
 const shErrors=[];sw.addEventListener('error',e=>shErrors.push(e.message));
 const overview={user:{username:'yatheesh',roles:['ADMIN']},feeds:{active_plans:1,collectors:1,live:0,problems:1,paused:false},order_sending:[{environment:'LIVE',enabled:false}],approvals:2,
   attention:[{severity:'CRITICAL',area:'Market data',title:'Broker refused the connector version',detail:'Update the SDK.',goto:{view:'Market data',section:'live'}}]};
 sw.fetch=async(url)=>{const j=b=>({ok:true,status:200,json:async()=>b});
   if(url.includes('/config/overview'))return j(overview);
   if(url.includes('operator/session'))return j({username:'yatheesh',roles:['ADMIN','TRADING_OPS'],must_change_password:false});
   if(url.includes('/api/workstation'))return j({project_id:'corei-os-mini',contract_version:2,release:'t',database_connected:true,demo_enabled:false,capabilities:[]});
   return j({items:[],counts:{},execution_controls:[],connections:[],accounts:[],controls:[],checklist:[],summary:{accounts:0,data_ready:0,trading_ready:0,with_warnings:0},pending_approvals:0,failed_alerts:0,four_eyes_live_changes:true,network:{}});};
 sw.eval(js);for(let i=0;i<6;i++)await tick();
 const sq=s=>sw.document.querySelector(s),sqa=s=>[...sw.document.querySelectorAll(s)];
 assert.ok(sq('.status-chips').textContent.includes('Feeds 0/1 · 1 problem'));assert.ok(sq('.status-chips').textContent.includes('Approvals 2'));
 assert.equal(sq('nav.activity [aria-label="Market data"] .rail-badge').textContent,'1');
 assert.ok(sqa('.dense-table tr').some(r=>r.textContent.includes('Broker refused')),'problems on Overview');
 assert.ok(sqa('.sb-row.sb-problem').some(r=>r.textContent.includes('Broker refused')),'problems in the side bar');
 assert.ok(sq('.tb-tabs').textContent.includes('PROBLEMS1'),'toolbox counts problems');
 assert.equal(sq('.tabs'),null,'one view: no tab bar');
 sq('nav.activity [aria-label="Configuration"]').click();for(let i=0;i<4;i++)await tick();
 sq('nav.activity [aria-label="Infrastructure"]').click();for(let i=0;i<4;i++)await tick();
 assert.equal(JSON.parse(sw.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups[0].length,1,'rail shows in place');
 sw.dispatchEvent(new sw.KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));await tick();
 const si=sq('[aria-label="Search commands"]');Object.getOwnPropertyDescriptor(sw.HTMLInputElement.prototype,'value').set.call(si,'collection plans');si.dispatchEvent(new sw.Event('input',{bubbles:true}));await tick();
 const first=sqa('[role="option"]')[0];assert.ok(first.textContent.includes('Market data › Collection plans'));first.click();for(let i=0;i<6;i++)await tick();
 assert.equal(sq('nav.activity [aria-current="page"]').getAttribute('aria-label'),'Market data');
 assert.equal(sq('.cfg-nav [aria-current="page"]').textContent,'Collection plans');
 assert.ok(sqa('.sb-row.selected').some(r=>r.textContent==='Collection plans'),'side bar highlights the open section');
 assert.ok(sqa('.sb-pane header').some(h=>h.textContent.includes('Market watch')),'Market watch in the side bar');
 // The trading flow: eight stages on the rail, each a screen with its components and build state.
 const stages=['Market data','Intelligence','Signals','Strategies','Risk','Portfolio','Execution','Post-trade'];
 stages.forEach((v,i)=>assert.equal(sq(`nav.activity [aria-label="${v}"] .rail-n`).textContent,String(i+1)));
 sq('nav.activity [aria-label="Risk"]').click();for(let i=0;i<4;i++)await tick();
 assert.equal(sqa('.pl-flow .pl-node').length,8);assert.ok(sq('.pl-node.on').textContent.includes('Risk')&&sq('.pl-head').textContent.includes('Risk intelligence'));
 assert.ok(sqa('.pl-table tbody tr').some(r=>r.textContent.includes('Constraint engine')&&r.textContent.includes('next')));
 assert.ok(sqa('.sb-row.selected').some(r=>r.textContent.includes('Risk intelligence')),'flow list highlights the stage');
 sqa('.tabs-mini button').find(b=>b.textContent.startsWith('Build plan')).click();await tick();
 assert.equal(sqa('.pl-step').length,7);
 sqa('.pl-node').find(n=>n.textContent.includes('Post-trade')).click();for(let i=0;i<4;i++)await tick();
 assert.equal(sq('nav.activity [aria-current="page"]').getAttribute('aria-label'),'Post-trade');
 assert.deepEqual(shErrors,[]);
 console.log('PASS: shell shows live status chips and badges, rail opens screens in place, Ctrl+K goes straight to a section, attention list, trading flow stages with build plan.');sw.close();
})().catch(e=>{console.error(e);process.exit(1)});
