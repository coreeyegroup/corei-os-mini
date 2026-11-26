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
 const click=(text)=>{const b=buttons().find(b=>b.textContent.trim()===text||b.querySelector(':scope > span:nth-child(2)')?.textContent===text);assert.ok(b,'button '+text);b.click();};
 assert.match(w.document.body.textContent,/Overview/);
 assert.ok(w.document.querySelector('.explorer.sidebar'),'side bar open by default');assert.ok(w.document.querySelector('.bottom.toolbox'),'toolbox open by default');
 click('Window');await tick();click('Four groups');await tick();
 assert.equal(w.document.querySelectorAll('.group').length,4);
 assert.equal(JSON.parse(w.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups.length,4);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'b',ctrlKey:true,bubbles:true}));await tick();assert.equal(w.document.querySelector('.explorer'),null);
 w.document.querySelector('nav.activity button[aria-current=page]').click();await tick();assert.ok(w.document.querySelector('.explorer'),'domain rail reopens the Explorer');
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
   click('View');await tick();click('Color theme: '+theme);await tick();
   assert.equal(w.document.documentElement.dataset.theme,theme);
   assert.equal(JSON.parse(w.localStorage.getItem('corei-os-mini.appearance.v1')).theme,theme);
 }
 click('View');await tick();click('Zoom in (text size)');await tick();assert.equal(w.document.documentElement.style.getPropertyValue('--ui-size'),'14px');
 click('View');await tick();click('Status bar');await tick();assert.equal(w.document.querySelector('footer'),null);
 click('View');await tick();click('Status bar');await tick();assert.ok(w.document.querySelector('footer'));
 click('View');await tick();click('Wrapped tabs');await tick();assert.ok(w.document.querySelector('.wrap-tabs'));
 const firstTab=w.document.querySelector('.tab');firstTab.dispatchEvent(new w.MouseEvent('contextmenu',{bubbles:true,clientX:20,clientY:20}));await tick();
 click('Pin tab');await tick();assert.ok(w.document.querySelector('[aria-label="Close Overview in group 1"]').disabled);
 assert.deepEqual(JSON.parse(w.localStorage.getItem('corei-os-mini.pins.v1')),['Overview']);
 firstTab.dispatchEvent(new w.MouseEvent('contextmenu',{bubbles:true}));await tick();click('Unpin tab');await tick();assert.equal(w.document.querySelector('[aria-label="Close Overview in group 1"]').disabled,false);
 click('Go');await tick();click('Open Strategies');await tick();
 click('Window');await tick();click('Close active tab');await tick();assert.equal(w.document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(),'Home');
 click('Window');await tick();click('Reopen last closed tab');await tick();assert.ok([...w.document.querySelectorAll('[role="tab"]')].some(t=>t.textContent==='Strategies'&&t.getAttribute('aria-selected')==='true'));
 console.log('PASS: five themes persist, font size, status toggle, wrapped tabs, tab context menu, pin protection, unpin and reopen.');

 const opened=[];w.open=(...args)=>{opened.push(args);return null;};
 w.document.querySelector('[aria-label="Open Strategies in new tab"]').click();
 w.document.querySelector('[aria-label="Open Strategies in new window"]').click();
 assert.equal(opened.length,2);assert.equal(new URL(opened[0][0]).searchParams.get('view'),'Strategies');
 assert.match(opened[0][2],/noopener/);assert.match(opened[1][2],/popup=yes/);
 // Plain Ctrl+P remains available to the browser.
 const printEvent=new w.KeyboardEvent('keydown',{key:'p',ctrlKey:true,bubbles:true,cancelable:true});w.dispatchEvent(printEvent);assert.equal(printEvent.defaultPrevented,false);
 // Removed views are gone from navigation; Configuration replaces them.
 click('Go');await tick();const navigate=w.document.body.textContent;assert.ok(!/Open Providers|Open Accounts|Open Instruments/.test(navigate));click('Open Configuration');await tick();
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
 // A panel link opens that panel alone in a detached window.
 const dp=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/?view=Risk&section=limits&detached=1',runScripts:'outside-only',pretendToBeVisual:true});
 const pw=dp.window;pw.HTMLElement.prototype.scrollIntoView=function(){};
 pw.fetch=async(url)=>url.includes('operator/session')?{ok:true,status:200,json:async()=>({username:'u',roles:['ADMIN']})}:{ok:true,status:200,json:async()=>({items:[],project_id:'corei-os-mini',contract_version:2,release:'t',database_connected:false,demo_enabled:false,capabilities:[],kinds:{}})};
 pw.eval(js);for(let i=0;i<8;i++)await tick();
 assert.equal(pw.document.querySelector('.cfg-nav [aria-current="page"]').textContent,'Limits','the linked panel opens alone');
 assert.equal(pw.document.querySelectorAll('.group').length,1);dp.window.close();
 console.log('PASS: window actions, detached Configuration workspace, isolated layout and safe fallback from removed views; a panel link opens that panel alone.');dw.close();ow.close();

 // Shell: live status chips, each screen its own workspace tab (as in VS Code), Ctrl+K reaches a section, attention list links.
 const sh=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/',runScripts:'outside-only',pretendToBeVisual:true});const sw=sh.window;sw.HTMLElement.prototype.scrollIntoView=function(){};
 const shErrors=[];sw.addEventListener('error',e=>shErrors.push(e.message));
 const overview={user:{username:'yatheesh',roles:['ADMIN']},feeds:{active_plans:1,collectors:1,live:0,problems:1,paused:false},order_sending:[{environment:'LIVE',enabled:false}],approvals:2,
   attention:[{severity:'CRITICAL',area:'Market data',title:'Broker refused the connector version',detail:'Update the SDK.',goto:{view:'Market data',section:'live'}}]};
 const prefPuts=[];let loggedOut=0;
 sw.fetch=async(url,o={})=>{const j=b=>({ok:true,status:200,json:async()=>b});
   if(url.includes('/config/overview'))return j(overview);
   if(url.includes('/trading/topbar'))return j({kill_switches:{ACCOUNT:1},desks:{RUNNING:2,PAUSED:1},unknown_orders:1,approvals_waiting:0,server_time:'2026-11-26T05:00:00Z'});
   if(url.includes('/operator/prefs')){if(o.method==='PUT')prefPuts.push([url.split('/prefs/')[1],JSON.parse(o.body).value]);return j({});}
   if(url.includes('/operator/logout')){loggedOut++;return j({});}
   if(url.includes('operator/session'))return j({username:'yatheesh',roles:['ADMIN','TRADING_OPS'],must_change_password:false});
   if(url.includes('/api/workstation'))return j({project_id:'corei-os-mini',contract_version:2,release:'t',database_connected:true,demo_enabled:false,capabilities:[]});
   return j({items:[],counts:{},execution_controls:[],connections:[],accounts:[],controls:[],checklist:[],summary:{accounts:0,data_ready:0,trading_ready:0,with_warnings:0},pending_approvals:0,failed_alerts:0,four_eyes_live_changes:true,network:{}});};
 sw.eval(js);for(let i=0;i<6;i++)await tick();
 const shownS=()=>[...sw.document.querySelectorAll('.group.focused .view > div:not([hidden])')][0]||sw.document;
 const shownSI=()=>shownS().querySelector('.sw-pane.focused .sw-item:not([hidden])');
 const sq=s=>(shownSI()&&shownSI().querySelector(s))||shownS().querySelector(s)||sw.document.querySelector(s),sqa=s=>{const i=shownSI();const b=i?[...i.querySelectorAll(s)]:[];if(b.length)return b;const a=[...shownS().querySelectorAll(s)];return a.length?a:[...sw.document.querySelectorAll(s)];};
 assert.ok(sq('.status-chips').textContent.includes('Feeds 0/1 · 1 problem'));assert.ok(sq('.status-chips').textContent.includes('Approvals 2'));
 assert.equal(sq('nav.activity [aria-label="Market data"] .rail-badge').textContent,'1');
 assert.ok(sqa('.dense-table tr').some(r=>r.textContent.includes('Broker refused')),'problems on Overview');
 assert.ok(sqa('.sb-row.sb-problem').some(r=>r.textContent.includes('Broker refused')),'problems in the side bar');
 assert.ok(sq('.tb-tabs').textContent.includes('PROBLEMS1'),'toolbox counts problems');
 assert.ok(sq('.tabs [aria-label="Open Overview in new window"]'),'the workspace tab bar with its actions is always shown');
 sq('nav.activity [aria-label="Configuration"]').click();for(let i=0;i<4;i++)await tick();
 sq('nav.activity [aria-label="Infrastructure"]').click();for(let i=0;i<4;i++)await tick();
 assert.deepEqual(JSON.parse(sw.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups[0],['Overview','Configuration','Infrastructure'],'every screen opens as its own workspace tab');
 sq('nav.activity [aria-label="Configuration"]').click();for(let i=0;i<4;i++)await tick();
 assert.equal(JSON.parse(sw.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups[0].length,3,'an open workspace is focused, not opened twice');
 assert.equal(sw.document.querySelector('.tabs [role="tab"][aria-selected="true"]').textContent,'Setup');
 sw.dispatchEvent(new sw.KeyboardEvent('keydown',{key:'k',ctrlKey:true,bubbles:true}));await tick();
 const si=sq('[aria-label="Search commands"]');Object.getOwnPropertyDescriptor(sw.HTMLInputElement.prototype,'value').set.call(si,'collection plans');si.dispatchEvent(new sw.Event('input',{bubbles:true}));await tick();
 const first=sqa('[role="option"]')[0];assert.ok(first.textContent.includes('Market data › Collection plans'));first.click();for(let i=0;i<6;i++)await tick();
 assert.equal(sq('nav.activity [aria-current="page"]').getAttribute('aria-label'),'Market data');
 assert.equal(sq('.cfg-nav [aria-current="page"]').textContent,'Collection plans');
 assert.equal(new URL(sw.location.href).searchParams.get('view'),'Market data');
 assert.equal(new URL(sw.location.href).searchParams.get('section'),'plans');
 const copied=sw.location.href;
 const linked=new JSDOM('<div id="root"></div>',{url:copied,runScripts:'outside-only',pretendToBeVisual:true});
 linked.window.fetch=sw.fetch; linked.window.HTMLElement.prototype.scrollIntoView=function(){};
 linked.window.eval(js);for(let i=0;i<6;i++)await tick();
 assert.equal(linked.window.document.querySelector('.cfg-nav [aria-current="page"]').textContent,'Collection plans');
 assert.equal(new URL(linked.window.location.href).searchParams.get('detached'),null);
 linked.window.close();

 assert.ok(sqa('.sb-row.selected').some(r=>r.textContent==='Collection plans'),'side bar highlights the open section');
 assert.ok(sqa('.sb-pane header').some(h=>h.textContent.includes('Market watch')),'Market watch in the side bar');
 // The trading flow: eight stages on the rail, each a screen with its components and build state.
 const stages=['Market data','Intelligence','Signals','Strategies','Risk','Portfolio','Execution','Post-trade'];
 stages.forEach((v,i)=>assert.match(sq(`nav.activity [aria-label="${v}"]`).title,new RegExp(`^${i+1} · `),"stage number in the tooltip"));
 sq('nav.activity [aria-label="Risk"]').click();for(let i=0;i<4;i++)await tick();
 const riskNav=[...sqa('.cfg-nav button')].map(b=>b.textContent);
 ['Dashboard','Kill switches','Limits','Risk checks','Exposure','Stage map'].forEach(t=>assert.ok(riskNav.includes(t),t));
 assert.deepEqual([...sqa('.cfg-nav .cfg-nav-group')].map(g=>g.textContent),['Overview','Work','Monitor','Configure'],'every stage: the same four groups');
 // With the side bar open (which hides the section bar) the side bar lists the stage's sections.
 assert.ok(sqa('.sb-pane header').some(h=>h.textContent.includes('Risk intelligence')),'the stage\'s own pane in the side bar');
 sqa('.sb-row').find(r=>r.textContent==='Kill switches').click();for(let i=0;i<4;i++)await tick();
 assert.equal(sq('.cfg-nav [aria-current="page"]').textContent,'Kill switches','side bar opens a stage section');
 assert.ok(sqa('.sb-row.selected').some(r=>r.textContent==='Kill switches'));
 sqa('.cfg-nav button').find(b=>b.textContent==='Stage map').click();for(let i=0;i<4;i++)await tick();
 assert.equal(sqa('.pl-flow .pl-node').length,8);assert.ok(sq('.pl-node.on').textContent.includes('Risk')&&sq('.pl-head').textContent.includes('Risk intelligence'));
 assert.ok(sqa('.pl-table tbody tr').some(r=>r.textContent.includes('Constraint engine')&&r.textContent.includes('built')));
 assert.ok(sqa('.sb-row.selected').some(r=>r.textContent==='Stage map'),'the side bar lists the stage\'s own items, the open one selected');
 assert.ok(!sqa('.sb-title').some(x=>/Trading flow/i.test(x.textContent)),'the main workspaces are not repeated in a stage\'s side bar');
 assert.equal(new URL(sw.location.href).searchParams.get('section'),'stage');
 const historySize=sw.history.length;
 sw.history.back();for(let i=0;i<5;i++)await tick();
 assert.equal(sq('.cfg-nav [aria-current="page"]').textContent,'Kill switches');
 sw.history.forward();for(let i=0;i<5;i++)await tick();
 assert.equal(sq('.cfg-nav [aria-current="page"]').textContent,'Stage map');
 assert.equal(sw.history.length,historySize);
 console.log('PASS: address bar records main pages and sections, a pasted page URL restores its section, and Back/Forward restore navigation.');

 // Market data: its side bar lists its own items with the same names and order as its sub-tabs.
 sq('nav.activity [aria-label="Market data"]').click();for(let i=0;i<8;i++)await tick();
 const mdSide=[...sw.document.querySelectorAll('.sb-pane')].find(p=>p.querySelector('.sb-title')?.textContent==='Market data');
 assert.deepEqual([...mdSide.querySelectorAll('.sb-row')].map(b=>b.textContent.trim()),
   [...sw.document.querySelectorAll('.view > div:not([hidden]) .sw-tab button[role=tab]')].map(b=>b.textContent),'side bar = sub-tabs');
 assert.ok(![...sw.document.querySelectorAll('.sb-title')].some(t=>/Trading flow|Problems/.test(t.textContent)));
 // Every stage: its side bar and its sub-tabs list the same items in the same order.
 for(const v of ['Signals','Strategies','Risk','Portfolio','Execution','Post-trade']){
   sq(`nav.activity [aria-label="${v}"]`).click();for(let i=0;i<8;i++)await tick();
   const side=[...sw.document.querySelectorAll('.sb-pane')][0];
   if(v==='Strategies'){
     assert.deepEqual([...sw.document.querySelectorAll('.view > div:not([hidden]) .sw-tab button[role=tab]')].map(b=>b.textContent),
       ['Dashboard','Runtime','Strategy signals','Trade decisions','All panels']);
     for(const name of ['Backtest','Optimise','Calculation graphs','Stage map']){
       assert.ok([...side.querySelectorAll('.sb-row')].some(b=>b.textContent.trim()===name));
     }
     continue;
   }
   assert.deepEqual([...side.querySelectorAll('.sb-row')].map(b=>b.textContent.trim()),
     [...sw.document.querySelectorAll('.view > div:not([hidden]) .sw-tab button[role=tab]')].map(b=>b.textContent),`${v}: side bar = sub-tabs`);
 }
 sqa('.tabs-mini button').find(b=>b.textContent.startsWith('Build plan')).click();await tick();
 assert.equal(sqa('.pl-step').length,7);
 sqa('.pl-node').find(n=>n.textContent.includes('Post-trade')).click();for(let i=0;i<4;i++)await tick();
 assert.equal(sq('nav.activity [aria-current="page"]').getAttribute('aria-label'),'Post-trade');
 // Activity bar: every area has its own icon (no shape shared by two areas).
 const iconPaths=[...sw.document.querySelectorAll('nav.activity button svg path')].map(p=>p.getAttribute('d'));
 assert.equal(new Set(iconPaths).size,iconPaths.length,'each activity icon is distinct');
 assert.equal(sw.document.querySelectorAll('nav.activity .rail-n').length,0,'no tiny stage digits under icons');
 // Top bar: kill switches and UNKNOWN orders always shown; VS Code-style layout toggles, customise menu, bell, gear, account menu.
 const chips=sw.document.querySelector('[aria-label="Live status"]').textContent;
 assert.match(chips,/Kill account 1/);assert.match(chips,/Unknown orders 1/);assert.match(chips,/FX.*NSE/,'market clock');
 const tb=sw.document.querySelector('.tb-right');const lb=sw.document.querySelector('[aria-label="Layout"]');
 const sideBtn=lb.querySelector('[aria-label="Toggle side bar (Ctrl+B)"]');const sbBefore=sideBtn.getAttribute('aria-pressed');sideBtn.click();await tick();
 assert.notEqual(lb.querySelector('[aria-label="Toggle side bar (Ctrl+B)"]').getAttribute('aria-pressed'),sbBefore,'side bar toggles from the top bar');
 lb.querySelector('[aria-label="Toggle side bar (Ctrl+B)"]').click();await tick();
 tb.querySelector('[aria-label="Customize layout"]').click();await tick();
 const feedsBox=[...tb.querySelectorAll('[role="menu"] label')].find(l=>l.textContent==='Feeds').querySelector('input');feedsBox.click();await tick();
 assert.doesNotMatch(sw.document.querySelector('[aria-label="Live status"]').textContent,/Feeds/,'feeds chip hidden from the customise menu');
 assert.match(sw.document.querySelector('[aria-label="Live status"]').textContent,/Kill account 1/,'kill switch never hidden');
 [...tb.querySelectorAll('[role="menu"] label')].find(l=>l.textContent==='Feeds').querySelector('input').click();await tick();
 sw.document.dispatchEvent(new sw.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
 tb.querySelector('[aria-label^="Notifications"]').click();await tick();
 assert.match(tb.querySelector('[role="menu"]').textContent,/Broker refused the connector version/);
 sw.document.dispatchEvent(new sw.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
 // Settings: VS Code style — search, categories, scope labels, modified marker, reset; account settings saved to the server.
 tb.querySelector('[aria-label="Settings"]').click();for(let i=0;i<4;i++)await tick();
 const st=sw.document.querySelector('[aria-label="Settings"].set');assert.ok(st,'settings view');
 assert.deepEqual([...st.querySelectorAll('.set-nav button')].map(b=>b.firstChild.textContent),['Appearance','Layout','Time & region','Trading','Startup','Top bar','Keyboard shortcuts','Firm settings','About']);
 const setv=(el,v)=>{Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el),'value').set.call(el,v);el.dispatchEvent(new sw.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const search=st.querySelector('[aria-label="Search settings"]');setv(search,'time zone');await tick();
 const tz=st.querySelector('select[aria-label="Time zone"]');assert.ok(tz,'search finds the time zone');
 setv(tz,'Asia/Kolkata');await tick();
 assert.ok(st.querySelector('#setting-region\\.timezone.modified'),'modified marker');
 await new Promise(r=>setTimeout(r,700));
 assert.equal(prefPuts.at(-1)[0],'settings.v1');assert.equal(prefPuts.at(-1)[1]['region.timezone'],'Asia/Kolkata','saved to the account');
 [...st.querySelectorAll('#setting-region\\.timezone button')].find(b=>b.textContent==='Reset').click();await tick();
 assert.equal(st.querySelector('select[aria-label="Time zone"]').value,'LOCAL');
 setv(search,'');await tick();
 [...st.querySelectorAll('.set-nav button')].find(b=>/Firm settings/.test(b.textContent)).click();await tick();
 assert.match(st.textContent,/Kill switches.*Changed by: Risk approver/);
 [...st.querySelectorAll('.set-nav button')].find(b=>/Keyboard shortcuts/.test(b.textContent)).click();await tick();
 assert.match(st.textContent,/SettingsViewCtrl\+,/);
 // Settings: the side bar is its table of contents.
 const sbSet=[...sw.document.querySelectorAll('.sb-pane')].find(x=>x.querySelector('.sb-title')?.textContent==='Settings');
 assert.ok(sbSet,'settings side bar pane');[...sbSet.querySelectorAll('.sb-row')].find(b=>b.textContent==='Trading').click();for(let i=0;i<3;i++)await tick();
 assert.ok(st.querySelector('select[aria-label="Confirm orders"]'),'side bar opens the Trading category');
 // Menus: View, Go, Window, Help (standard names), with groups and check marks; compact mode folds them into one button.
 assert.deepEqual([...sw.document.querySelectorAll('.menubar > .menu-wrap > button')].map(b=>b.textContent),['View','Go','Window','Help']);
 [...sw.document.querySelectorAll('.menubar > .menu-wrap > button')][0].click();await tick();
 assert.deepEqual([...sw.document.querySelectorAll('.menu-popup .menu-group')].map(g=>g.textContent),['Commands','Show','Appearance','Color theme']);
 assert.equal(sw.document.querySelector('.menu-popup [role="menuitemcheckbox"][aria-checked="true"]')?.textContent.replace(/Ctrl.*$/,''),'✓Side bar');
 sw.document.dispatchEvent(new sw.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));sw.document.querySelector('.menubar').dispatchEvent(new sw.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));await tick();
 const menuSel=st.querySelector('select[aria-label="Menu bar"]')||(()=>{[...sbSet.querySelectorAll('.sb-row')].find(b=>b.textContent==='Layout').click();return null;})();
 for(let i=0;i<3;i++)await tick();setv(st.querySelector('select[aria-label="Menu bar"]'),'COMPACT');await tick();
 assert.ok(sw.document.querySelector('.menubar.compact [aria-label="Application menu"]'),'compact menu');
 sw.document.querySelector('[aria-label="Application menu"]').click();await tick();
 [...sw.document.querySelectorAll('.menu-tabs button')].find(b=>b.textContent==='Window').click();await tick();
 assert.ok([...sw.document.querySelectorAll('.menu-compact button')].some(b=>/Two columns/.test(b.textContent)));
 setv(st.querySelector('select[aria-label="Menu bar"]'),'CLASSIC');await tick();
 // Secondary side bar: attention, accounts, open positions, working orders, watchlist.
 if(!sw.document.querySelector('aside.inspector'))lb.querySelector('[aria-label="Toggle secondary side bar (Ctrl+Alt+B)"]').click();await tick();await tick();
 assert.deepEqual([...sw.document.querySelectorAll('aside.inspector .sb-title')].map(t=>t.textContent),['Attention','Accounts','Open positions','Working orders','Watchlist']);
 // Help text: explanations behind ⓘ; "Always shown" puts them inline.
 sq('nav.activity [aria-label="Risk"]').click();for(let i=0;i<6;i++)await tick();
 const ib=sw.document.querySelector('.group.focused .stage-head .info-btn');assert.ok(ib,'stage header has ⓘ');assert.equal(sw.document.querySelector('.group.focused .st-question'),null,'no always-on stage text');
 ib.click();await tick();assert.match(sw.document.querySelector('.info-pop').textContent,/This stage answers:/);
 assert.ok(sw.document.querySelector('.app.help-icon'));
 tb.querySelector('[aria-label="Account yatheesh"]').click();await tick();
 [...tb.querySelectorAll('[role="menu"] button')].find(b=>b.textContent==='Sign out').click();await tick();await tick();
 assert.equal(loggedOut,1,'sign out from the account menu');
 assert.deepEqual(shErrors,[]);
 console.log('PASS: top bar: kill switches and unknown orders always shown, market clock, layout toggles, customise menu, notifications, account menu; Settings: search, categories, scopes, modified, reset, saved to the account, side bar contents; menus View/Go/Window/Help and compact ☰; secondary side bar; ⓘ help.');
 console.log('PASS: shell shows live status chips and badges, each screen opens as its own workspace tab, Ctrl+K goes straight to a section, attention list, trading flow stages with build plan.');sw.close();
})().catch(e=>{console.error(String(e&&e.message||e).slice(0,3000));process.exit(1)});
