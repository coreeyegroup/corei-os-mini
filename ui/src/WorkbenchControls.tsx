import { useEffect, useRef, useState } from 'react';

export type Command = { label: string; section: string; shortcut?: string; run: () => void };
export function MenuBar({ commands }: { commands: Command[] }) {
  const [menu, setMenu] = useState('');
  const root = useRef<HTMLElement>(null);
  useEffect(() => {
    const dismiss = (e: PointerEvent) => { if (!root.current?.contains(e.target as Node)) setMenu(''); };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, []);
  return <nav className="menubar" aria-label="Workbench menus" ref={root} onKeyDown={e => {if(e.key==='Escape') setMenu('');}}>
    {['Workspace', 'View', 'Layout', 'Appearance', 'Navigate', 'Help'].map(section => <div className="menu-wrap" key={section}>
      <button aria-expanded={menu===section} onClick={() => setMenu(menu===section?'':section)}>{section}</button>
      {menu===section && <div className="menu-popup" aria-label={`${section} commands`}>
        {commands.filter(c=>c.section===section).map(c=><button key={c.label} onClick={()=>{c.run();setMenu('');}}><span>{c.label}</span><kbd>{c.shortcut}</kbd></button>)}
      </div>}
    </div>)}
    <span className="menu-caption">TRADING WORKBENCH <b>0.4</b></span>
  </nav>;
}
export function CommandPalette({ commands, dismiss }: { commands: Command[]; dismiss: () => void }) {
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const previous = useRef(document.activeElement as HTMLElement | null);
  const filtered = commands.filter(c=>(c.section+' '+c.label).toLowerCase().includes(query.toLowerCase()));
  useEffect(()=>{input.current?.focus();return ()=>previous.current?.focus();},[]);
  const execute=(c:Command)=>{c.run();dismiss();};
  return <div className="overlay" onPointerDown={dismiss}><div className="palette" role="dialog" aria-modal="true" aria-label="Command palette" onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{
    if(e.key==='Escape'){e.preventDefault();dismiss();}
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();setSelected(i=>filtered.length?(i+(e.key==='ArrowDown'?1:-1)+filtered.length)%filtered.length:0);}
    if(e.key==='Enter' && filtered[selected]){e.preventDefault();execute(filtered[selected]);}
    if(e.key==='Tab'){e.preventDefault();input.current?.focus();}
  }}>
    <input ref={input} value={query} onChange={e=>{setQuery(e.target.value);setSelected(0);}} aria-label="Search commands" placeholder="> Search views, layout and commands" role="combobox" aria-expanded="true" aria-controls="command-results" aria-activedescendant={filtered.length?`command-${selected}`:undefined}/>
    <div className="command-results" id="command-results" role="listbox">
      {filtered.map((c,i)=><button id={`command-${i}`} role="option" aria-selected={selected===i} className={selected===i?'chosen':''} key={c.section+c.label} onMouseEnter={()=>setSelected(i)} onClick={()=>execute(c)} ref={el=>{if(selected===i)el?.scrollIntoView({block:'nearest'});}}><span><small>{c.section}: </small>{c.label}</span><kbd>{c.shortcut}</kbd></button>)}
      {!filtered.length && <p className="panel-empty">No matching commands.</p>}
    </div><div className="palette-hint">↑ ↓ Navigate &nbsp; Enter Select &nbsp; Esc Close</div>
  </div></div>;
}
export function ResizeHandle({ label, value, min, max, horizontal=false, reverse=false, change }: {label:string;value:number;min:number;max:number;horizontal?:boolean;reverse?:boolean;change:(n:number)=>void}) {
  const drag=useRef<{origin:number;value:number}|null>(null);
  const clamp=(n:number)=>Math.max(min,Math.min(max,n));
  return <div className={`resize-handle ${horizontal?'horizontal':'vertical'}`} role="separator" tabIndex={0} aria-label={label} aria-orientation={horizontal?'horizontal':'vertical'} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value}
    onPointerDown={e=>{e.currentTarget.setPointerCapture(e.pointerId);drag.current={origin:horizontal?e.clientY:e.clientX,value};e.preventDefault();}}
    onPointerMove={e=>{if(drag.current)change(clamp(drag.current.value+((horizontal?e.clientY:e.clientX)-drag.current.origin)*(reverse?-1:1)));}}
    onPointerUp={e=>{drag.current=null;e.currentTarget.releasePointerCapture(e.pointerId);}}
    onPointerCancel={()=>{drag.current=null;}} onLostPointerCapture={()=>{drag.current=null;}}
    onKeyDown={e=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(e.key)){e.preventDefault();change(e.key==='Home'?min:e.key==='End'?max:clamp(value+(['ArrowRight','ArrowDown'].includes(e.key)?10:-10)*(reverse?-1:1)));}}}/>;
}
