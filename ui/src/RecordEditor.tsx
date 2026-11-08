import {useEffect,useRef,useState,type ReactNode,type PointerEvent} from 'react';
type Box={x:number;y:number;width:number;height:number};
function fit(b:Box):Box {const width=Math.min(Math.max(320,b.width),window.innerWidth-16),height=Math.min(Math.max(240,b.height),window.innerHeight-16);return {width,height,x:Math.max(8,Math.min(b.x,window.innerWidth-width-8)),y:Math.max(8,Math.min(b.y,window.innerHeight-height-8))};}
function initial():Box {const width=Math.min(660,window.innerWidth-32),height=Math.min(640,window.innerHeight-48);return fit({width,height,x:(window.innerWidth-width)/2,y:(window.innerHeight-height)/2});}
export default function RecordEditor({editing,close,children}:{editing:boolean;close:()=>void;children:ReactNode}){
 const ref=useRef<HTMLDialogElement>(null),drag=useRef<{x:number;y:number;box:Box;resize:boolean}|null>(null);
 const [box,setBox]=useState(initial);
 function reset(){const dialog=ref.current;if(!dialog){setBox(initial());return;}const content=dialog.querySelector('.editor-body');const natural=Array.from(content?.children??[]).reduce((sum,e)=>{const style=getComputedStyle(e);return sum+(e as HTMLElement).offsetHeight+(parseFloat(style.marginTop)||0)+(parseFloat(style.marginBottom)||0);},0);const b=initial();b.height=Math.min(b.height,Math.max(280,natural+112));b.y=(window.innerHeight-b.height)/2;setBox(fit(b));}

 useEffect(()=>{const dialog=ref.current;if(dialog&&!dialog.open){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}reset();const resize=()=>setBox(b=>fit(b));window.addEventListener('resize',resize);return()=>{window.removeEventListener('resize',resize);if(dialog?.open&&typeof dialog.close==='function')dialog.close();};},[]);
 function start(e:PointerEvent<HTMLElement>,resize=false){if(e.button!==0||(!resize&&(e.target as HTMLElement).closest('button')))return;e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);drag.current={x:e.clientX,y:e.clientY,box,resize};}
 function move(e:PointerEvent<HTMLElement>){const d=drag.current;if(!d)return;setBox(fit(d.resize?{...d.box,width:d.box.width+e.clientX-d.x,height:d.box.height+e.clientY-d.y}:{...d.box,x:d.box.x+e.clientX-d.x,y:d.box.y+e.clientY-d.y}));}
 function end(){drag.current=null;}
 return <dialog ref={ref} className="registry-editor floating-editor" style={{left:box.x,top:box.y,width:box.width,height:box.height}} aria-label={editing?'Edit record':'Create record'} onCancel={e=>{e.preventDefault();close();}}>
 <div className="editor-windowbar" tabIndex={0} aria-label="Move record window" onPointerDown={e=>start(e)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={e=>{if(e.target!==e.currentTarget)return;if(e.key.startsWith('Arrow')){e.preventDefault();setBox(b=>fit({...b,x:b.x+(e.key==='ArrowRight'?10:e.key==='ArrowLeft'?-10:0),y:b.y+(e.key==='ArrowDown'?10:e.key==='ArrowUp'?-10:0)}));}}}><span>⠿ {editing?'Edit':'Add'} record · drag to move</span><button type="button" onClick={reset}>Centre / reset size</button></div>
 <div className="editor-body">{children}</div>
 <div className="editor-windowfoot"><span>Resize from the corner · Esc closes</span><button type="button" aria-label="Resize record window" title="Drag to resize; arrow keys adjust width and height" onPointerDown={e=>start(e,true)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={e=>{if(e.key.startsWith('Arrow')){e.preventDefault();setBox(b=>fit({...b,width:b.width+(e.key==='ArrowRight'?10:e.key==='ArrowLeft'?-10:0),height:b.height+(e.key==='ArrowDown'?10:e.key==='ArrowUp'?-10:0)}));}}}>◢</button></div>
 </dialog>;
}
