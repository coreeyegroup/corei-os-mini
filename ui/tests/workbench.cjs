const {JSDOM}=require('jsdom');
const fs=require('fs');
const path=require('path');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'../..');
const js=fs.readFileSync(path.join(root,'ui/dist/assets',fs.readdirSync(path.join(root,'ui/dist/assets')).find(x=>x.endsWith('.js'))),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,40));
(async()=>{
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.fetch=async()=>({ok:true,json:async()=>({project_id:'corei-os-mini',contract_version:1,release:'test',execution_mode:'PAPER',database_connected:false,demo_enabled:false,capabilities:[]})});
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
 assert.deepEqual(errors,[]);console.log('PASS: production bundle mounts; four groups persist; keyboard toggles; accessible resizing; filtered palette action; two-row layout; help closes; no runtime errors.');w.close();
})().catch(e=>{console.error(e);process.exit(1)});
