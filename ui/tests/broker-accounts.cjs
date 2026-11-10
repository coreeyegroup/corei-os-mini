const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const dist=path.resolve(__dirname,'../dist');
const entry=fs.readFileSync(path.join(dist,'index.html'),'utf8').match(/src="([^" ]+\.js)"/)[1].replace(/^\//,'');
const js=fs.readFileSync(path.join(dist,entry),'utf8');
const tick=()=>new Promise(r=>setTimeout(r,60));
(async()=>{
 const dom=new JSDOM('<div id="root"></div>',{url:'https://corei-os-mini.coreeye.in/?view=Accounts&detached=1',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window;w.HTMLElement.prototype.scrollIntoView=function(){};w.confirm=()=>true;
 const errors=[];w.addEventListener('error',e=>errors.push(e.message));
 let rows=[],requests=[];
 const point={id:'11111111-1111-4111-8111-111111111111',server_name:'Broker-Demo',name:'Access Point 1',host:'2001:db8::1',port:443};
 const ok=data=>({ok:true,json:async()=>data});
 w.fetch=async(url,options={})=>{
  if(url.includes('/operator/session'))return ok({username:'operator'});
  if(url.endsWith('/server-catalog')){assert.ok(options.body instanceof w.File);return ok({items:[point,{...point,id:'other',server_name:'Other-Live'}]});}
  if(url.includes('/registry/broker-accounts')){
   if(['POST','PUT'].includes(options.method)){
    const body=JSON.parse(options.body);requests.push(body);
    rows=[{...rows[0],...body,id:'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',access_point_name:point.name,host:point.host,port:point.port,enabled:false,version:2}];
    delete rows[0].password;return ok(rows[0]);
   }
   return ok({items:rows,total:rows.length,legacy_count:0,credential_storage_ready:true});
  }
  return ok({project_id:'corei-os-mini',capabilities:[]});
 };
 w.eval(js);await tick();await tick();await tick();await tick();
 const click=text=>{const b=[...w.document.querySelectorAll('button')].find(x=>x.textContent.trim()===text);assert.ok(b,text);b.click();};
 const form=()=>w.document.querySelector('[aria-label="Broker account editor"] form');
 const field=label=>{const node=[...form().querySelectorAll('label')].find(x=>x.textContent.startsWith(label));assert.ok(node,label);return node.querySelector('input,select');};
 const fill=(label,value)=>{const element=field(label);const proto=element.tagName==='SELECT'?w.HTMLSelectElement.prototype:w.HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(proto,'value').set.call(element,value);element.dispatchEvent(new w.Event(element.tagName==='SELECT'?'change':'input',{bubbles:true}));};
 click('Add broker account');await tick();
 assert.ok(form());assert.doesNotMatch(form().textContent,/Connection label|Provider organisation|Adapter code|Terminal computer/);
 fill('Account label','Primary demo');fill('Broker name','Broker');fill('Broker login','123');fill('Broker password','test-secret-123');fill('Broker-provided server name','Wrong-Server');await tick();
 const file=field('Browse and analyse');Object.defineProperty(file,'files',{value:[new w.File(['x'.repeat(40)],'servers.dat')]});file.dispatchEvent(new w.Event('change',{bubbles:true}));await tick();await tick();
 assert.match(form().textContent,/No exact match/);assert.equal(field('Access point').options.length,1);
 fill('Broker-provided server name','Broker-Demo');await tick();
 assert.equal(field('Access point').options.length,2);assert.match(field('Access point').options[1].textContent,/\[2001:db8::1\]:443/);
 fill('Access point',point.id);await tick();form().dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();await tick();await tick();
 assert.equal(requests.length,1);assert.equal(requests[0].server_name,'Broker-Demo');assert.equal(requests[0].access_point_id,point.id);
 assert.equal(requests[0].password,'test-secret-123');assert.equal(requests[0].connection_id,undefined);
 assert.equal(form(),null);assert.match(w.document.body.textContent,/Saved · Not connected/);
 assert.ok(!JSON.stringify(w.localStorage).includes('test-secret-123'));
 click('Edit / replace password');await tick();
 assert.equal(field('New password').value,'');assert.ok(field('Broker login').readOnly);
 fill('Account label','Renamed');await tick();form().dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));await tick();await tick();
 assert.equal(requests[1].password,undefined);assert.equal(requests[1].version,2);
 click('Add broker account');await tick();fill('Broker password','unsaved-secret');await tick();
 w.dispatchEvent(new w.StorageEvent('storage',{key:'corei-os-mini.operator-signout',newValue:'logout'}));await tick();
 assert.equal(form(),null);assert.match(w.document.body.textContent,/Operator sign in/);assert.deepEqual(errors,[]);
 w.close();console.log('PASS: one-form account save, exact server/access-point matching, IPv6 display, password omission on edit, secret clearing on sign-out.');
})().catch(e=>{console.error(e);process.exitCode=1;});
