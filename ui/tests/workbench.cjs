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

 const opened=[];w.open=(...args)=>{opened.push(args);return null;};
 w.document.querySelector('[aria-label="Open Strategies in new tab"]').click();
 w.document.querySelector('[aria-label="Open Strategies in new window"]').click();
 assert.equal(opened.length,2);assert.equal(new URL(opened[0][0]).searchParams.get('view'),'Strategies');
 assert.match(opened[0][2],/noopener/);assert.match(opened[1][2],/popup=yes/);
 let registered=[]; let profiles=[];let authenticated=false;
 w.fetch=async(url,options={})=>{
   if(url==='/install/manifest.json')return {ok:true,json:async()=>({project_id:'corei-os-mini',version:'0.6.2',status:'LOCAL_DIAGNOSTICS_ONLY',sha256:'a'.repeat(64)})};
   if(url.endsWith('/operator/session'))return authenticated?{ok:true,json:async()=>({username:'test-operator'})}:{ok:false,status:401,json:async()=>({detail:'Sign in'})};
   if(url.endsWith('/operator/login')){authenticated=true;return {ok:true,json:async()=>({username:'test-operator'})};}
   if(url.includes('/registry/connector-profiles')) {
     if(options.method==='POST'||options.method==='PUT') {
       const body=JSON.parse(options.body);
       const row={id:'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',...body,adapter_status:'NOT_IMPLEMENTED',session_status:'NOT_CONNECTED',data_health:'NOT_OBSERVED',account_verified:false,execution_available:false};
       profiles=[row]; return {ok:true,json:async()=>row};
     }
     return {ok:true,json:async()=>({items:profiles,total:profiles.length,offset:0,limit:50})};
   }
   if(url.includes('/registry/')) {
     if(options.method==='POST'){const body=JSON.parse(options.body);assert.equal(body.enabled,false);registered.push({id:'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',...body});return {ok:true,json:async()=>registered[0]};}
     const query=new URL(url,w.location.origin).searchParams.get('q')||'';const rows=url.includes('/providers')?registered.filter(r=>r.name.toLowerCase().includes(query.toLowerCase())):[];return {ok:true,json:async()=>({items:rows,total:rows.length,offset:0,limit:50})};
   }
   throw Error('Unexpected test request '+url);
 };
 click('Navigate');await tick();click('Open Providers');await tick();await tick();
 assert.match(w.document.body.textContent,/Operator sign in/);
 const form=w.document.querySelector('.registry-form');
 const fill=(el,value)=>{Object.getOwnPropertyDescriptor(w.HTMLInputElement.prototype,'value').set.call(el,value);el.dispatchEvent(new w.Event('input',{bubbles:true}));};
 fill(form.querySelector('input[autocomplete="username"]'),'test-operator');
 fill(form.querySelector('input[type="password"]'),'a-long-test-password');await tick();
 form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.match(w.document.body.textContent,/Operator: test-operator/);
 click('New provider');await tick();
 const registration=w.document.querySelector('.registry-form');fill(registration.querySelector('input'),'Test broker');await tick();registration.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.equal(registered.length,1);assert.match(w.document.querySelector('.registry-table').textContent,/Test broker/);
 assert.equal(w.document.querySelector('[aria-label="Create record"]'),null);
 const search=w.document.querySelector('input[aria-label="Search all records"]');
 fill(search,'nonmatching');await new Promise(r=>setTimeout(r,350));assert.match(w.document.body.textContent,/No matching records/);
 fill(search,'Test broker');await new Promise(r=>setTimeout(r,350));assert.match(w.document.querySelector('.registry-table').textContent,/Test broker/);
 console.log('PASS: registry login, side-panel create, successful close, server search and record list (mock API).');
 // Dirty editor must not close on Cancel; an accepted discard clears it.
 click('New provider');await tick();
 const popup=w.document.querySelector('.registry-editor');
 const initialWidth=parseFloat(popup.style.width);w.document.querySelector('[aria-label="Resize record window"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();assert.equal(parseFloat(popup.style.width),initialWidth+10);
 const initialLeft=parseFloat(popup.style.left);w.document.querySelector('[aria-label="Move record window"]').dispatchEvent(new w.KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));await tick();assert.equal(parseFloat(popup.style.left),initialLeft+10);
 const reference=w.document.querySelector('.registry-reference');assert.equal(reference.open,false);assert.ok(w.document.querySelector('.registry-table').compareDocumentPosition(reference)&w.Node.DOCUMENT_POSITION_FOLLOWING);
 const draftForm=w.document.querySelector('.registry-editor .registry-form');fill(draftForm.querySelector('input'),'Unsaved broker');await tick();
 w.confirm=()=>false;w.document.querySelector('[aria-label="Close record editor"]').click();await tick();
 assert.ok(w.document.querySelector('.registry-editor'));assert.match(w.document.querySelector('.registry-editor').textContent,/UNSAVED CHANGES/);
 const blocked=new w.Event('corei-before-layout-change',{cancelable:true});assert.equal(w.dispatchEvent(blocked),false);
 w.confirm=()=>true;w.document.querySelector('[aria-label="Close record editor"]').click();await tick();assert.equal(w.document.querySelector('.registry-editor'),null);
 assert.equal(w.document.querySelector('.registry-tabs').textContent.includes('Accounts'),false);
 // Plain Ctrl+P remains available to the browser.
 const printEvent=new w.KeyboardEvent('keydown',{key:'p',ctrlKey:true,bubbles:true,cancelable:true});w.dispatchEvent(printEvent);assert.equal(printEvent.defaultPrevented,false);
 console.log('PASS: dirty-form cancellation/discard, guarded layout changes, domain-scoped navigation, browser print shortcut.');
 click('Connector profiles');await tick();await tick();
 assert.ok([...w.document.querySelectorAll('[role=tab][aria-selected=true]')].some(t=>t.textContent==='Connector profiles'));
 click('New connector profile');await tick();
 let pf=w.document.querySelector('.registry-form');
 const field=(label)=>[...pf.querySelectorAll('.registry-field')].find(x=>x.querySelector('label').textContent.startsWith(label)).querySelector('select')||[...pf.querySelectorAll('.registry-field')].find(x=>x.querySelector('label').textContent.startsWith(label)).querySelector('input');
 fill(pf.querySelector('[aria-label="Connection internal ID"]'),'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa');
 fill(field('Profile name'),'Demo terminal');fill(field('Adapter identifier'),'mt5_ea');
 fill(field('Broker server name'),'Example-Demo');await tick();
 pf.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.equal(profiles.length,1);assert.equal(profiles[0].runtime,'WINDOWS_TERMINAL');
 assert.match(w.document.querySelector('.registry-table').textContent,/NOT_IMPLEMENTED/);
 click('Edit profile');await tick();pf=w.document.querySelector('.registry-form');
 assert.ok(field('Connection').disabled);
 const select=field('Connector family');select.value='BROKER_API';select.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
 assert.equal(field('Runtime location').value,'SERVER_API');
 w.document.querySelector('[aria-label="Information about connector_type"]').click();await tick();
 assert.match(w.document.querySelector('[role="note"]').textContent,/bridge in the terminal/);
 fill(field('Adapter identifier'),'upstox');await tick();
 pf.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.equal(profiles[0].connector_type,'BROKER_API');assert.equal(profiles[0].adapter_key,'upstox');
 assert.equal(profiles[0].execution_available,false);
 w.document.querySelector('[aria-label="Open Providers in new tab"]').click();
 assert.equal(new URL(opened.at(-1)[0]).searchParams.get('registry'),'connector-profiles');
 click('Install terminal files');await tick();await tick();
 const install=w.document.querySelector('[aria-label="Terminal installation"]');assert.ok(install);
 assert.match(install.textContent,/VPS bridge pending/);
 assert.equal(install.querySelector('a[download]').getAttribute('href'),'/install/corei-os-mini-terminal-kit-0.6.2.zip');
 const platformSelect=install.querySelector('select');platformSelect.value='MT4';platformSelect.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
 assert.ok(install.querySelector('a[href$=".mq4"]'));
 const hostSelect=install.querySelectorAll('select')[1];hostSelect.value='Ubuntu / Wine';hostSelect.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();
 assert.match(install.querySelector('pre').textContent,/bash install-terminal.sh MT4/);
 click('Close installation');await tick();assert.equal(w.document.querySelector('[aria-label="Terminal installation"]'),null);
 console.log('PASS: terminal installer panel, verified manifest gating, source downloads and platform-specific instructions.');
 w.dispatchEvent(new w.StorageEvent('storage',{key:'corei-os-mini.operator-signout',newValue:'test'}));await tick();
 assert.match(w.document.body.textContent,/Operator sign in/);
 console.log('PASS: connector profile create/edit, immutable connection ownership, family/runtime selection and truthful unavailable status (mock API).');
 assert.deepEqual(errors,[]);console.log('PASS: production bundle mounts; four groups persist; keyboard toggles; accessible resizing; filtered palette action; two-row layout; help closes; no runtime errors.');w.close();
 const detached=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/?view=Providers&detached=1&registry=connector-profiles',runScripts:'outside-only',pretendToBeVisual:true});
 const dw=detached.window;dw.HTMLElement.prototype.scrollIntoView=function(){};
 dw.localStorage.setItem('corei-os-mini.workstation.layout.v1','main-layout-sentinel');
 dw.fetch=async(url)=>({ok:true,json:async()=>url.includes('operator/session')?{username:'operator'}:url.includes('registry/')?{items:[],total:0,offset:0,limit:50}:{project_id:'corei-os-mini',capabilities:[]}});
 dw.eval(js);await tick();await tick();
 assert.equal(dw.document.querySelectorAll('.group').length,1);
 assert.match(dw.document.querySelector('h1').textContent,/Connector profiles/);
 assert.equal(dw.localStorage.getItem('corei-os-mini.workstation.layout.v1'),'main-layout-sentinel');
 assert.equal(dw.document.querySelector('.explorer'),null);
 console.log('PASS: workspace tab/window actions, selected registry deep links, field help and isolated detached layout.');dw.close();
})().catch(e=>{console.error(e);process.exit(1)});
