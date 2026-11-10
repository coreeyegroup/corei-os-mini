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
 assert.match(w.document.body.textContent,/Workspace overview/);
 click('Layout');await tick();click('Four groups');await tick();
 assert.equal(w.document.querySelectorAll('.group').length,4);
 assert.equal(JSON.parse(w.localStorage.getItem('corei-os-mini.workstation.layout.v1')).groups.length,4);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'b',ctrlKey:true,bubbles:true}));await tick();assert.equal(w.document.querySelector('.explorer'),null);
 w.dispatchEvent(new w.KeyboardEvent('keydown',{key:'b',ctrlKey:true,bubbles:true}));await tick();assert.ok(w.document.querySelector('.explorer'));
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
 click('Workspace');await tick();click('Close active tab');await tick();assert.equal(w.document.querySelector('[role="tab"][aria-selected="true"]').textContent.trim(),'Overview');
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
 ow.fetch=dw.fetch;ow.eval(js);await tick();assert.match(ow.document.body.textContent,/Workspace overview/);
 console.log('PASS: window actions, detached Configuration workspace, isolated layout and safe fallback from removed views.');dw.close();ow.close();
})().catch(e=>{console.error(e);process.exit(1)});
