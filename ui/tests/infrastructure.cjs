// Infrastructure workspace: health board, spreadsheet editing with reason, read-only tables, SQL console.
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
 const layout={version:1,workspace:'Research',activity:'Infrastructure',groups:[['Infrastructure']],active:['Infrastructure'],focus:0,left:false,right:false,bottom:false,axis:'row',leftWidth:220,rightWidth:270,bottomHeight:180,maximized:false};
 const dom=new JSDOM('<html><body><div id="root"></div></body></html>',{url:'https://corei-os-mini.coreeye.in',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 w.HTMLElement.prototype.scrollIntoView=function(){};
 w.localStorage.setItem('corei-os-mini.workstation.layout.v1',JSON.stringify(layout));
 w.confirm=()=>true;
 const ok=(body,status=200)=>({ok:true,status,json:async()=>body});
 const fail=(status,detail)=>({ok:false,status,json:async()=>({detail})});
 let signedIn=false,needReauth=true;const patches=[],inserts=[],queries=[];
 const rows=[{id:'1',key:'theme',value:'"dark"',username:'risk-ops'},{id:'2',key:'density',value:'"compact"',username:'risk-ops'}];
 const columns=[{name:'id',type:'bigint',nullable:false,default:"nextval('x')",generated:false,key:true,secret:false},
   {name:'key',type:'text',nullable:false,default:null,generated:false,key:false,secret:false},
   {name:'value',type:'jsonb',nullable:true,default:null,generated:false,key:false,secret:false},
   {name:'username',type:'text',nullable:false,default:null,generated:false,key:false,secret:false}];
 const tables=[{name:'cfg_user_prefs',rows:2,bytes:16384,mode:'EDIT',why:'Administrators can edit',group:'Configuration'},
   {name:'cfg_audit_log',rows:50,bytes:65536,mode:'READ_ONLY',why:'Audit history is append-only.',group:'Configuration'}];
 w.fetch=async(url,options={})=>{
   const method=options.method||'GET';const body=options.body?JSON.parse(options.body):undefined;
   if(url==='/api/workstation')return ok({project_id:'corei-os-mini',contract_version:2,release:'test',database_connected:true,demo_enabled:false,capabilities:[]});
   const me={username:'risk-ops',display_name:'risk-ops',roles:['ADMIN','TRADING_OPS'],must_change_password:false};
   if(url.endsWith('/operator/session'))return signedIn?ok(me):fail(401,'Sign in');
   if(url.endsWith('/operator/login')){signedIn=true;return ok(me);}
   if(url.endsWith('/operator/reauth')){needReauth=false;return ok({minutes:5});}
   if(url.endsWith('/infra/health'))return ok({generated_at:new Date().toISOString(),status:'WARN',checks:[
     {group:'Database',name:'PostgreSQL',status:'OK',detail:'18.1 · up 2 days'},{group:'Host',name:'Clock (NTP)',status:'FAIL',detail:'NOT synchronised'},
     {group:'Services',name:'caddy',status:'OK',detail:'active'}]});
   if(url.endsWith('/infra/database'))return ok({server:{database:'corei',role:'corei',schema:'public',version:'18.1',size:'9 MB',timezone:'UTC',started_at:new Date().toISOString()},sessions:[],tables});
   if(url.includes('/infra/tables/cfg_audit_log?'))return ok({table:'cfg_audit_log',mode:'READ_ONLY',why:'Audit history is append-only.',columns:[columns[0]],key:['id'],total:0,offset:0,limit:100,rows:[]});
   if(url.includes('/infra/tables/cfg_user_prefs?')){const q=new URL('https://x'+url.slice(4)).searchParams;const f=q.get('f.key');
     const shown=rows.filter(r=>!f||r.key.includes(f));return ok({table:'cfg_user_prefs',mode:'EDIT',why:'',columns,key:['id'],total:shown.length,offset:0,limit:100,rows:shown});}
   if(url.endsWith('/infra/tables/cfg_user_prefs/rows')&&method==='PATCH'){if(needReauth)return fail(428,'Confirm with your password');
     patches.push(body);const r=rows.find(x=>x.id===body.key.id);Object.assign(r,body.changes);return ok(r);}
   if(url.endsWith('/infra/tables/cfg_user_prefs/rows')&&method==='POST'){inserts.push(body);rows.push({id:'3',...body.values});return ok(rows[2],201);}
   if(url.endsWith('/infra/query')){queries.push(body.sql);return body.sql.includes('password')?fail(403,'Queries that name secret columns or secret tables are refused here.')
     :ok({columns:['username'],rows:[{username:'risk-ops'}],truncated:false,ms:1.2});}
   if(url.endsWith('/infra/edits'))return ok({items:[{id:1,actor:'risk-ops',table_name:'cfg_user_prefs',action:'UPDATE',row_key:{id:'1'},before:{value:'"dark"'},after:{value:'"light"'},reason:'Fix theme',edited_at:new Date().toISOString()}]});
   return fail(404,'Not mocked: '+url);
 };
 w.eval(js);await tick();await tick();
 const $=s=>w.document.querySelector(s),$$=s=>[...w.document.querySelectorAll(s)];
 const fill=(el,value)=>{const proto=el.tagName==='TEXTAREA'?w.HTMLTextAreaElement.prototype:el.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(el,value);el.dispatchEvent(new w.Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 const nav=label=>$$('.cfg-nav button').find(b=>b.querySelector('span').textContent===label);
 const button=text=>$$('button').find(b=>b.textContent.trim()===text);
 const submit=async form=>{form.dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();};
 const key=(el,k,extra={})=>el.dispatchEvent(new w.KeyboardEvent('keydown',{key:k,bubbles:true,...extra}));

 // Left bar has the new item; signing in opens the health board.
 assert.ok($$('nav.activity button').some(b=>b.getAttribute('aria-label')==='Infrastructure'));
 const form=$('.cfg-signin form');fill(form.querySelector('[autocomplete="username"]'),'risk-ops');fill(form.querySelector('[type="password"]'),'x');await submit(form);
 assert.ok($('.infra-bar').textContent.includes('Some checks need attention'));
 assert.equal($$('.infra-groups .cfg-panel').length,3);
 assert.ok($('.infra-checks .infra-dot.FAIL'));
 console.log('PASS: Infrastructure in the left bar; health grouped with status per check.');

 nav('Tables').click();await tick();await tick();
 const list=$$('.infra-list button');assert.equal(list.length,2);assert.ok(list[1].textContent.includes('🔒'));
 list[1].click();await tick();await tick();await tick();await tick();await tick();await tick();await tick();await tick();
 assert.ok($('.infra-why').textContent.includes('append-only'));assert.equal(button('+ Row'),undefined);
 $$('.infra-list button')[0].click();await tick();await tick();await tick();await tick();await tick();await tick();await tick();await tick();
 assert.equal($$('.infra-grid tbody tr').length,2);
 // Filter like a spreadsheet column filter.
 fill($('[aria-label="Filter key"]'),'dens');await tick();await tick();await tick();await tick();await tick();await tick();await tick();await tick();
 assert.equal($$('.infra-grid tbody tr').length,1);
 fill($('[aria-label="Filter key"]'),'');await tick();await tick();await tick();await tick();await tick();await tick();await tick();await tick();
 // Select a cell, type to edit, Enter commits and moves down; the cell is marked unsaved.
 const cell=$$('.infra-grid tbody tr')[0].querySelectorAll('td')[3];cell.click();await tick();
 key($('.infra-grid'),'"');await tick();
 const input=$('.infra-cell-input');fill(input,'"light"');key(input,'Enter');await tick();await tick();
 assert.ok($$('.infra-grid tbody tr')[0].querySelectorAll('td')[3].classList.contains('infra-changed'));
 assert.ok($('.infra-dirty-count').textContent.includes('1 unsaved change'));
 // Save asks for a reason, then the password (428), then retries.
 button('Save changes…').click();await tick();
 fill($('[aria-label="Reason"]'),'Fix theme');await submit($('[aria-label="Reason for this change"] form'));
 const reauth=$('[aria-label="Confirm with your password"] form');assert.ok(reauth);
 fill(reauth.querySelector('[aria-label="Your password"]'),'x');await submit(reauth);await tick();await tick();await tick();
 assert.deepEqual(patches[0],{key:{id:'1'},changes:{value:'"light"'},original:{value:'"dark"'},reason:'Fix theme'});
 assert.ok($('.cfg-ok').textContent.includes('1 row saved'));
 // Add a row: blank cells take the default.
 button('+ Row').click();await tick();
 const newCells=$('.infra-new').querySelectorAll('td');newCells[2].dispatchEvent(new w.MouseEvent('dblclick',{bubbles:true}));await tick();
 const ni=$('.infra-cell-input');fill(ni,'layout');key(ni,'Enter');await tick();
 button('Add row…').click();await tick();fill($('[aria-label="Reason"]'),'Seed');await submit($('[aria-label="Reason for this change"] form'));await tick();
 assert.deepEqual(inserts[0],{values:{key:'layout'},reason:'Seed'});
 console.log('PASS: spreadsheet grid filters, edits in place with keyboard, saves with reason and password, adds rows; read-only tables locked.');

 nav('SQL console').click();await tick();await tick();
 fill($('[aria-label="SQL"]'),'SELECT username FROM cfg_users');await submit($('.infra-console'));
 assert.equal($$('.infra-grid tbody tr').length,1);
 fill($('[aria-label="SQL"]'),'SELECT password_hash FROM cfg_users');await submit($('.infra-console'));
 assert.ok($('[role="alert"]').textContent.includes('refused'));
 nav('Edit log').click();await tick();await tick();
 assert.ok($('.infra-diff').textContent.includes('"dark"'));
 console.log('PASS: read-only SQL console and the edit log.');
 assert.deepEqual(errors,[]);
 w.close();
})().catch(e=>{console.error(e);process.exit(1)});
