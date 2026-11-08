import {useEffect,useRef,type ReactNode} from 'react';
export default function RecordEditor({editing,close,children}:{editing:boolean;close:()=>void;children:ReactNode}){
 const ref=useRef<HTMLDialogElement>(null);
 useEffect(()=>{const dialog=ref.current;if(dialog&&!dialog.open){if(typeof dialog.showModal==='function')dialog.showModal();else dialog.setAttribute('open','');}return()=>{if(dialog?.open&&typeof dialog.close==='function')dialog.close();};},[]);
 return <dialog ref={ref} className="registry-editor" aria-label={editing?'Edit record':'Create record'} onCancel={e=>{e.preventDefault();close();}} onClick={e=>{if(e.target===e.currentTarget)close();}}>{children}</dialog>;
}
