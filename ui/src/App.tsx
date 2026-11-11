import { useEffect, useRef, useState } from "react";
import Configuration from "./Configuration";
import Infrastructure, { Health, SignIn } from "./Infrastructure";
import MarketData from "./MarketData";
import WorkbenchIcon from "./WorkbenchIcon";
import ErrorBoundary from "./ErrorBoundary";
import { api } from "./Configuration";
import { DESTINATIONS, goto, type Overview as OverviewData } from "./nav";
import { initialLayout, launchContext, workspaceUrl } from "./workspaceLinks";
import { themes, appearanceKey, defaultAppearance, restoreAppearance, type Appearance } from "./appearance";
import { MenuBar, CommandPalette, ResizeHandle, type Command } from "./WorkbenchControls";
import {
  defaults,
  key,
  restore,
  views,
  type Layout,
  type View,
} from "./layout";

type Runtime = {
  project_id: string;
  contract_version: number;
  release: string;
  database_connected: boolean;
  schema_ready?: boolean;
  demo_enabled: boolean;
  capabilities: {
    id: string;
    label: string;
    available: boolean;
    reason: string;
  }[];
};
const descriptions: Record<View, string> = {
  Overview: "Workspace readiness and capability map",
  "Market data": "Collection plans, live prices, feed quality and broker comparison, backfill, archive and backups",
  Configuration: "Providers, connections, accounts, instruments, symbol mappings and order-sending controls",
  Strategies: "Strategy versions, instrument universes and assignments",
  Execution: "Account allocation, order lifecycle and reconciliation",
  Portfolio: "Positions, balances, fees and shared exposure",
  Operations: "Connection health, service status and operational records",
  Infrastructure: "Server and database health, PostgreSQL tables (view and edit), SQL console and edit log",
  Settings: "Workspace layout and navigation preferences",
};
const activityLabels:Record<View,string>={Overview:'Home','Market data':'Market',Configuration:'Setup',Strategies:'Strategies',Execution:'Orders',Portfolio:'Portfolio',Operations:'Monitor',Infrastructure:'System',Settings:'Settings'};
/* The rail, in the order a trader works: home, the markets, trading, watching, setting up. */
const RAIL:(View|'-')[]=['Overview','-','Market data','Strategies','Execution','Portfolio','-','Operations','-','Configuration','Infrastructure'];
const viewTitle:Record<View,string>={Overview:'Home','Market data':'Market data',Configuration:'Setup',Strategies:'Strategies',Execution:'Orders',Portfolio:'Portfolio',Operations:'Monitor',Infrastructure:'System',Settings:'Settings'};
const iconIndex:Record<View,number>={Overview:0,'Market data':1,Configuration:2,Strategies:5,Execution:6,Portfolio:7,Operations:8,Infrastructure:10,Settings:9};
function stored() {
  try {
    return initialLayout(localStorage.getItem(key));
  } catch {
    return defaults();
  }
}
export default function App() {
  const launch = launchContext(window.location.search);
  function openWorkspace(view: View, newWindow: boolean) {
    window.open(workspaceUrl(view), '_blank', newWindow ? 'popup=yes,width=1440,height=960,noopener,noreferrer' : 'noopener,noreferrer');
  }
  const [appearance,setAppearance]=useState<Appearance>(()=>{try{return restoreAppearance(localStorage.getItem(appearanceKey));}catch{return {...defaultAppearance};}});
  const [pinned,setPinned]=useState<View[]>(()=>{try{const x=JSON.parse(localStorage.getItem('corei-os-mini.pins.v1')||'[]');return Array.isArray(x)?views.filter(v=>x.includes(v)):[];}catch{return [];}});
  const [closed,setClosed]=useState<View[]>([]);
  const [context,setContext]=useState<{view:View;group:number;x:number;y:number}|null>(null);
  const [appearanceError,setAppearanceError]=useState('');
  const appearancePatch=(p:Partial<Appearance>)=>setAppearance(a=>({...a,...p}));
  useEffect(()=>{
    document.documentElement.dataset.theme=appearance.theme;
    document.documentElement.style.setProperty('--ui-size',appearance.fontSize+'px');
    try{localStorage.setItem(appearanceKey,JSON.stringify(appearance));localStorage.setItem('corei-os-mini.pins.v1',JSON.stringify(pinned));setAppearanceError('');}catch{setAppearanceError('Appearance preferences could not be saved.');}
  },[appearance,pinned]);
  useEffect(()=>{const dismiss=()=>setContext(null);window.addEventListener('resize',dismiss);return ()=>window.removeEventListener('resize',dismiss);},[]);
  function togglePin(v:View){setPinned(p=>p.includes(v)?p.filter(x=>x!==v):[...p,v]);}
  function closeOthers(v:View, group:number){if(!allowLayoutChange())return;setLayout(x=>({...x,groups:x.groups.map((g,i)=>i===group?g.filter(t=>t===v||pinned.includes(t)):g),active:x.active.map((a,i)=>i===group?v:a)}));}
  const [layout, setLayout] = useState<Layout>(stored),
    [runtime, setRuntime] = useState<Runtime | null>(null),
    [error, setError] = useState(""),
    [checking, setChecking] = useState(false),
    [events, setEvents] = useState<string[]>([]),
    [palette, setPalette] = useState(false),
    [help, setHelp] = useState(false),
    [collapsed, setCollapsed] = useState(false),
    [filter, setFilter] = useState(""),
    [bottomTab, setBottomTab] = useState("Activity"),
    [storageError, setStorageError] = useState("");
  const [checkedAt,setCheckedAt]=useState<number|null>(null);
  const alive = useRef(true),
    pending = useRef<AbortController | null>(null);
  const log = (message: string) =>
    setEvents((previous) =>
      [`${new Date().toLocaleTimeString()}  ${message}`, ...previous].slice(
        0,
        100,
      ),
    );
  const patch = (p: Partial<Layout>) => setLayout((x) => ({ ...x, ...p }));
  useEffect(() => {
    try {
      if (launch.detached) return; // Detached workspaces must not overwrite the main layout.
      localStorage.setItem(key, JSON.stringify(layout));
      localStorage.setItem(
        `${key}.${layout.workspace}`,
        JSON.stringify(layout),
      );
      setStorageError("");
    } catch {
      setStorageError("Layout could not be saved in this browser.");
    }
  }, [layout]);
  async function refresh() {
    pending.current?.abort();
    const controller = new AbortController();
    pending.current = controller;
    const timer = setTimeout(() => controller.abort(), 8000);
    setChecking(true);
    setRuntime(null);
    setError("");
    try {
      const response = await fetch("/api/workstation", {
        signal: controller.signal,
        cache: "no-store",
      });
      if (!response.ok)
        throw new Error(`Backend returned HTTP ${response.status}`);
      const data = await response.json();
      if (
        data.project_id !== "corei-os-mini" ||
        data.contract_version !== 2 ||
        typeof data.database_connected !== "boolean" ||
        typeof data.demo_enabled !== "boolean" ||
        typeof data.release !== "string" ||
        !Array.isArray(data.capabilities) ||
        !data.capabilities.every(
          (c: Runtime["capabilities"][number]) =>
            typeof c.id === "string" &&
            typeof c.label === "string" &&
            typeof c.available === "boolean" &&
            typeof c.reason === "string",
        )
      )
        throw new Error(
          "Backend identity or capability contract does not match this workstation.",
        );
      if (alive.current && pending.current === controller) {
        setRuntime(data);
        setCheckedAt(Date.now());
        log("Backend identity verified");
      }
    } catch (e) {
      if (alive.current && pending.current === controller) {
        setError(
          controller.signal.aborted
            ? "Backend request timed out or was cancelled."
            : String(e instanceof Error ? e.message : e),
        );
        log("Backend status unavailable");
      }
    } finally {
      clearTimeout(timer);
      if (alive.current && pending.current === controller) setChecking(false);
    }
  }
  useEffect(() => {
    alive.current = true;
    void refresh();
    const interval=setInterval(()=>{if(document.visibilityState!=='hidden')void refresh();},30000);
    return () => {
      clearInterval(interval);
      alive.current = false;
      pending.current?.abort();
    };
  }, []);
  const [overview,setOverview]=useState<OverviewData|null>(null),[signedOut,setSignedOut]=useState(false);
  async function loadOverview(){
    try{const d=await api('config/overview');
      // Show status only from a well-formed answer; anything else leaves the last good one.
      if(d&&d.feeds&&Array.isArray(d.order_sending)&&Array.isArray(d.attention)){setOverview(d);setSignedOut(false);}}
    catch(e){if((e as {status?:number}).status===401){setSignedOut(true);setOverview(null);}}
  }
  useEffect(()=>{void loadOverview();const t=setInterval(()=>{if(document.visibilityState!=='hidden')void loadOverview();},15000);
    const onSession=()=>void loadOverview();window.addEventListener('corei-session',onSession);
    return ()=>{clearInterval(t);window.removeEventListener('corei-session',onSession);};},[]);
  useEffect(()=>{const on=(e:Event)=>{const d=(e as CustomEvent).detail as {view:View};if(views.includes(d.view))go(d.view);};
    window.addEventListener('corei-goto',on);return ()=>window.removeEventListener('corei-goto',on);});
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const typing = e.target instanceof Element && e.target.closest('input,textarea,select,[contenteditable="true"]');
      if (mod && e.shiftKey && e.key.toLowerCase() === 'p') { e.preventDefault(); setPalette(x=>!x); return; }
      if (mod && !e.shiftKey && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette(x=>!x); return; }
      if (e.key === 'Escape') { setPalette(false); setHelp(false); setContext(null); }
      if (typing || palette || document.querySelector('dialog[open]')) return;
      if (mod && e.key.toLowerCase() === 'b') { e.preventDefault(); patch(e.altKey?{right:!layout.right}:{left:!layout.left}); }
      if (mod && e.key.toLowerCase() === 'j') { e.preventDefault(); patch({bottom:!layout.bottom}); }
      if (mod && e.key === '\\') { e.preventDefault(); split(); }
      if (mod && ['1','2','3','4'].includes(e.key)) {e.preventDefault(); const i=Number(e.key)-1;if(i<layout.groups.length) patch({focus:i});}
      if (e.altKey && ['ArrowLeft','ArrowRight'].includes(e.key)) {e.preventDefault(); cycle(e.key==='ArrowRight'?1:-1);}
    };
    window.addEventListener('keydown',onKey);
    return ()=>window.removeEventListener('keydown',onKey);
  }, [layout, palette]);
  function cycle(direction:number) {
    const group=layout.groups[layout.focus];
    open(group[(group.indexOf(layout.active[layout.focus])+direction+group.length)%group.length]);
  }
  function preset(count:number, axis:Layout['axis']) {
    if(!allowLayoutChange())return;
    const selected:View[]=['Overview','Market data','Execution','Operations'];
    patch({groups:selected.slice(0,count).map(v=>[v]),active:selected.slice(0,count),focus:0,axis,maximized:false});
  }
  function open(view: View, group = layout.focus) {
    setLayout((x) => {
      const groups = x.groups.map((g) => [...g]),
        active = [...x.active];
      const target = Math.min(group, groups.length - 1);
      if (!groups[target].includes(view)) groups[target].push(view);
      active[target] = view;
      return { ...x, groups, active, focus: target, activity: view };
    });
    setPalette(false);

  }
  /* Show a view: focus it where it is already open; otherwise show it in place of the
   * current (unpinned) view of the focused group, so tabs do not pile up. */
  function go(view: View) {
    const at = layout.groups.findIndex(g => g.includes(view));
    if (at >= 0) { open(view, at); return; }
    const current = layout.active[layout.focus];
    if (pinned.includes(current) || !allowLayoutChange()) { open(view); return; }
    setLayout(x => {
      const groups = x.groups.map(g => [...g]), active = [...x.active];
      groups[x.focus] = groups[x.focus].map(v => v === current ? view : v);
      active[x.focus] = view;
      return { ...x, groups, active, activity: view };
    });
    setPalette(false);
  }
  function close(view: View, group: number) {
    if(!allowLayoutChange())return;
    if(pinned.includes(view)) return;
    setClosed(x=>[view,...x].slice(0,20));
    setLayout((x) => {
      const groups = x.groups.map((g) => [...g]),
        active = [...x.active];
      groups[group] = groups[group].filter((v) => v !== view);
      if (!groups[group].length) {
        if (groups.length === 1) {
          groups[0] = ["Overview"];
          active[0] = "Overview";
        } else {
          groups.splice(group, 1);
          active.splice(group, 1);
        }
      } else if (active[group] === view) active[group] = groups[group][0];
      return {
        ...x,
        groups,
        active,
        focus: Math.min(x.focus, groups.length - 1),
        maximized: false,
      };
    });
  }
  function split() {
    setLayout((x) =>
      x.groups.length >= 4
        ? { ...x, axis: x.axis === "row" ? "column" : "row" }
        : {
            ...x,
            groups: [...x.groups, [x.active[x.focus]]],
            active: [...x.active, x.active[x.focus]],
            focus: x.groups.length,
            maximized: false,
          },
    );
  }
  function moveTab(view: View, from: number, to: number) {
    if(!allowLayoutChange())return;
    if (from === to) {
      setLayout((x) => ({
        ...x,
        groups: x.groups.map((g, i) =>
          i === to ? [...g.filter((v) => v !== view), view] : g,
        ),
      }));
      return;
    }
    setLayout((x) => {
      const groups = x.groups.map((g) => [...g]),
        active = [...x.active];
      groups[from] = groups[from].filter((v) => v !== view);
      if (!groups[to].includes(view)) groups[to].push(view);
      active[to] = view;
      if (!groups[from].length) {
        groups[from] = ["Overview"];
        active[from] = "Overview";
      } else if (active[from] === view) active[from] = groups[from][0];
      return { ...x, groups, active, focus: to };
    });
  }
  const active = layout.active[layout.focus];
  function allowLayoutChange(){return window.dispatchEvent(new Event('corei-before-layout-change',{cancelable:true}));}
  function switchWorkspace(name:string){
    if(!allowLayoutChange())return;
    let saved = defaults(name);
    try { const raw = localStorage.getItem(`${key}.${name}`); if (raw) saved = { ...restore(raw), workspace: name }; } catch {}
    setLayout(saved); log(`Layout switched to ${name}`);
  }

  function body(view: View) {
    if (view === "Configuration") return <Configuration/>;
    if (view === "Infrastructure") return <Infrastructure/>;
    if (view === "Market data") return <MarketData/>;
    if (view === "Settings")
      return (
        <div className="content">
          <div className="eyebrow">PERSONAL WORKSPACE</div>
          <h1>Arrange your workstation</h1>
          <p>
            Preferences are saved in this browser. Layout changes do not change
            trading state.
          </p>
          <div className="appearance-settings">
            <h2>Color theme</h2><div className="theme-picker">{themes.map(theme=><button key={theme} aria-pressed={appearance.theme===theme} onClick={()=>appearancePatch({theme})}><span className="theme-swatch" data-swatch={theme}/>{theme}{appearance.theme===theme?' ✓':''}</button>)}</div>
            <label>Interface text size <input aria-label="Interface text size" type="range" min="11" max="16" value={appearance.fontSize} onChange={e=>appearancePatch({fontSize:Number(e.target.value)})}/><output>{appearance.fontSize}px</output></label>
            <label>Density <select aria-label="Interface density" value={appearance.density} onChange={e=>appearancePatch({density:e.target.value as Appearance['density']})}><option>Compact</option><option>Comfortable</option></select></label>
            {(['activity','status','wrapTabs'] as const).map(k=><label key={k}><input type="checkbox" checked={appearance[k]} onChange={e=>appearancePatch({[k]:e.target.checked})}/>{k==='activity'?'Activity bar':k==='status'?'Status bar':'Wrap workspace tabs'}</label>)}
            <button onClick={()=>setAppearance({...defaultAppearance})}>Reset appearance</button>
          </div>
          <div className="settings">
            {(["left", "right", "bottom"] as const).map((k) => (
              <label key={k}>
                <span>
                  {k === "left"
                    ? "Explorer"
                    : k === "right"
                      ? "Inspector"
                      : "Bottom panel"}
                </span>
                <input
                  type="checkbox"
                  checked={layout[k]}
                  onChange={(e) => patch({ [k]: e.target.checked })}
                />
              </label>
            ))}
            {(
              [
                ["leftWidth", "Explorer width", 160, 380],
                ["rightWidth", "Inspector width", 200, 440],
                ["bottomHeight", "Bottom height", 120, 400],
              ] as const
            ).map(([k, label, min, max]) => (
              <label key={k}>
                {label}
                <input
                  aria-label={label}
                  type="range"
                  min={min}
                  max={max}
                  value={layout[k]}
                  onChange={(e) => patch({ [k]: Number(e.target.value) })}
                />
                <output>{layout[k]} px</output>
              </label>
            ))}
            <button
              onClick={() => {
                if(!allowLayoutChange())return;
                patch({ ...defaults(layout.workspace) });
                log("Layout reset");
              }}
            >
              Reset layout
            </button>
          </div>
        </div>
      );
    if (view === "Overview") return <Home overview={overview} signedOut={signedOut} runtime={runtime} error={error} refresh={()=>{void refresh();void loadOverview();}}/>;
    if (view === "Operations") return <Monitor overview={overview} signedOut={signedOut}/>;
    const planned:Record<string,{what:string;now:[string,View,string?][]}>={
      Strategies:{what:'Running strategies, their signals and performance.',now:[['Set up strategies and versions','Configuration','strategies'],['Assign a strategy to an account (deployments)','Configuration','deployments']]},
      Execution:{what:'The order blotter: working orders, fills, rejections and reconciliation with the broker.',now:[['Order sending switch and policies','Configuration','controls'],['Check an order against broker limits (account › symbol limits)','Configuration','accounts']]},
      Portfolio:{what:'Positions, balances, margin and exposure across accounts.',now:[['Account facts read from the broker','Configuration','accounts']]},
    };
    const p=planned[view];
    return (
      <div className="content home">
        <h1>{viewTitle[view]}</h1>
        <div className="notice">Not built yet. {p?.what}</div>
        {p && <div className="home-section"><h2>Available now</h2><div className="quick">{p.now.map(([label,v,sec])=><button key={label} onClick={()=>goto(v,sec)}>{label} →</button>)}</div></div>}
      </div>
    );
  }
  const commands:Command[] = [
    ...DESTINATIONS.map(d=>({section:'Go to',label:`${d.group} › ${d.label}`,keywords:d.keywords,run:()=>{goto(d.view,d.section);go(d.view);}})),
    ...views.map(v=>({section:'Navigate',label:`Open ${v}`,run:()=>open(v)})),
    {section:'Workspace',label:'Go to… (search everything)',shortcut:'Ctrl+K',run:()=>setPalette(true)},
    ...["Research","Execution","Operations"].map(name=>({section:'Workspace',label:`Saved layout: ${name}`,run:()=>switchWorkspace(name)})),
    {section:'Workspace',label:'Command palette',shortcut:'Ctrl+Shift+P',run:()=>setPalette(true)},
    {section:'Workspace',label:'Close active tab',run:()=>close(active,layout.focus)},
    {section:'Workspace',label:'Close other tabs in group',run:()=>closeOthers(active,layout.focus)},
    {section:'Workspace',label:'Reopen last closed tab',run:()=>{if(closed.length){open(closed[0]);setClosed(x=>x.slice(1));}}},
    {section:'Workspace',label:pinned.includes(active)?'Unpin active tab':'Pin active tab',run:()=>togglePin(active)},
    ...themes.map(theme=>({section:'Appearance',label:`Color theme: ${theme}`,run:()=>appearancePatch({theme})})),
    {section:'Appearance',label:'Increase text size',run:()=>appearancePatch({fontSize:Math.min(16,appearance.fontSize+1)})},
    {section:'Appearance',label:'Decrease text size',run:()=>appearancePatch({fontSize:Math.max(11,appearance.fontSize-1)})},
    {section:'Appearance',label:'Toggle activity bar',run:()=>appearancePatch({activity:!appearance.activity})},
    {section:'Appearance',label:'Toggle status bar',run:()=>appearancePatch({status:!appearance.status})},
    {section:'Appearance',label:'Toggle wrapped tabs',run:()=>appearancePatch({wrapTabs:!appearance.wrapTabs})},
    {section:'Appearance',label:'Appearance settings',run:()=>open('Settings')},
    {section:'Workspace',label:'Refresh backend status',run:()=>void refresh()},
    {section:'Workspace',label:'Reset current workspace layout',run:()=>{if(allowLayoutChange())setLayout(defaults(layout.workspace));}},
    {section:'View',label:'Toggle explorer',shortcut:'Ctrl+B',run:()=>patch({left:!layout.left})},
    {section:'View',label:'Toggle inspector',shortcut:'Ctrl+Alt+B',run:()=>patch({right:!layout.right})},
    {section:'View',label:'Toggle bottom panel',shortcut:'Ctrl+J',run:()=>patch({bottom:!layout.bottom})},
    {section:'View',label:'Toggle focus mode',run:()=>patch({maximized:!layout.maximized})},
    {section:'View',label:'Layout settings',run:()=>open('Settings')},
    {section:'Layout',label:'Single group',run:()=>preset(1,'row')},
    {section:'Layout',label:'Two columns',run:()=>preset(2,'row')},
    {section:'Layout',label:'Two rows',run:()=>preset(2,'column')},
    {section:'Layout',label:'Three columns',run:()=>preset(3,'row')},
    {section:'Layout',label:'Four groups',run:()=>preset(4,'row')},
    {section:'Layout',label:'Add workspace group (up to four)',shortcut:'Ctrl+\\',run:split},
    {section:'Layout',label:'Change split orientation',run:()=>patch({axis:layout.axis==='row'?'column':'row'})},
    {section:'Navigate',label:'Next tab',shortcut:'Alt+Right',run:()=>cycle(1)},
    {section:'Navigate',label:'Previous tab',shortcut:'Alt+Left',run:()=>cycle(-1)},
    {section:'Layout',label:'Open active view in other group',run:()=>{if(layout.groups.length>1)open(active,(layout.focus+1)%layout.groups.length);}},
    {section:'Help',label:'Keyboard shortcuts & workbench guide',run:()=>setHelp(true)},
  ];
  const badge=(v:View)=>!overview?null:v==='Market data'&&overview.feeds.problems?{n:overview.feeds.problems,tone:'bad'}
    :v==='Configuration'&&overview.approvals?{n:overview.approvals,tone:'warn'}
    :v==='Operations'&&overview.attention.some(a=>a.severity==='CRITICAL')?{n:overview.attention.filter(a=>a.severity==='CRITICAL').length,tone:'bad'}:null;
  const live=overview?.order_sending.filter(o=>o.enabled).map(o=>o.environment)??[];
  const multi=layout.groups.length>1;
  return (
    <div className={`app density-${appearance.density.toLowerCase()} ${appearance.wrapTabs?"wrap-tabs":""}`}>
      <header className="top">
        <div className="brand"><b>◈</b> COREI<span>OS MINI</span></div>
        <MenuBar commands={commands}/>
        <button className="command" onClick={() => setPalette(true)} aria-label="Go to anything">
          <span>Go to anything…</span><kbd>Ctrl K</kbd>
        </button>
        <div className="status-chips" aria-label="Live status">
          {signedOut ? <button className="chip" onClick={()=>go('Overview')}>Sign in</button> : overview && <>
            <button className={`chip ${overview.feeds.problems?'bad':overview.feeds.live?'ok':''}`} title="Market-data feeds" onClick={()=>{goto('Market data','live');go('Market data');}}>
              <span className="chip-dot"/>Feeds {overview.feeds.live}/{overview.feeds.collectors}{overview.feeds.problems?` · ${overview.feeds.problems} problem${overview.feeds.problems>1?'s':''}`:''}</button>
            <button className={`chip ${live.includes('LIVE')?'live':''}`} title="Order sending (firm-wide switch)" onClick={()=>{goto('Configuration','controls');go('Configuration');}}>
              Orders {live.length?`ON · ${live.join(', ')}`:'OFF'}</button>
            {overview.approvals>0 && <button className="chip warn" onClick={()=>{goto('Configuration','approvals');go('Configuration');}}>Approvals {overview.approvals}</button>}
          </>}
        </div>
        {overview && <span className="top-user" title={overview.user.roles.join(', ')}>{overview.user.username}</span>}
      </header>
      <div className="shell">
        {appearance.activity && <nav className="activity" aria-label="Activities">
          {RAIL.map((v,i) => v==='-' ? <span key={'sep'+i} className="rail-sep" aria-hidden="true"/> : (
            <button key={v} title={`${viewTitle[v]} — ${descriptions[v]}`} aria-label={v}
              aria-current={active === v ? "page" : undefined}
              onClick={(e) => { if (e.ctrlKey || e.metaKey) open(v); else go(v); }}>
              <WorkbenchIcon index={iconIndex[v]}/><small>{activityLabels[v]}</small>
              {(()=>{const b=badge(v);return b?<span className={`rail-badge ${b.tone}`}>{b.n}</span>:null;})()}
            </button>
          ))}
          <span className="rail-spacer"/>
          <button title="Settings — appearance and layout" aria-label="Settings" aria-current={active==='Settings'?'page':undefined} onClick={()=>go('Settings')}>
            <WorkbenchIcon index={iconIndex.Settings}/><small>Settings</small></button>
        </nav>}
        {layout.left && !layout.maximized && (
          <aside className="explorer" style={{ width: layout.leftWidth }}>
            <div className="panel-heading">
              NAVIGATOR <button title="Collapse or expand" aria-expanded={!collapsed} onClick={()=>setCollapsed(!collapsed)}>{collapsed?"▸":"▾"}</button>
              <button title="Hide navigator (Ctrl+B)" onClick={() => patch({ left: false })}>×</button>
            </div>
            <input aria-label="Filter explorer" placeholder="Filter…" value={filter} onChange={(e) => setFilter(e.target.value)} />
            {(multi || layout.groups[0].length > 1) && <details className="open-editors" open><summary>OPEN · {layout.groups.reduce((n,g)=>n+g.length,0)}</summary>
              {layout.groups.map((g,i)=>g.map(v=><button key={i+v} onClick={()=>open(v,i)} className={layout.focus===i&&active===v?'selected':''}><span>{viewTitle[v]}</span>{multi&&<small>Group {i+1}</small>}</button>))}
            </details>}
            {!collapsed && <div className="tree">
              {[...new Set(DESTINATIONS.map(d=>d.group))].map(group=>{
                const items=DESTINATIONS.filter(d=>d.group===group&&(d.label+' '+d.group+' '+(d.keywords??'')).toLowerCase().includes(filter.toLowerCase()));
                return items.length?<div key={group}><div className="nav-section-label">{group.toUpperCase()}</div>
                  {items.map(d=><button key={d.label} className={active===d.view&&!d.section?'selected':''} onClick={()=>{goto(d.view,d.section);go(d.view);}}><span>{d.label}</span></button>)}</div>:null;})}
            </div>}
          </aside>
        )}
        {layout.left && !layout.maximized && <ResizeHandle label="Resize explorer" value={layout.leftWidth} min={160} max={380} change={leftWidth=>patch({leftWidth})}/>}
        <main>
          <div className={`groups count-${layout.groups.length}`} style={{ flexDirection: layout.axis }}>
            {layout.groups.map((group, i) => (
              <section hidden={layout.maximized && layout.focus!==i} className={`group ${layout.focus === i ? "focused" : ""}`} key={i}
                onFocus={() => { if (layout.focus !== i) patch({ focus: i }); }}>
                {(multi || group.length > 1 || group.some(v=>pinned.includes(v))) && <div className="tabs" role="tablist" aria-label={`Workspace group ${i + 1}`}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => { e.preventDefault(); try { const d = JSON.parse(e.dataTransfer.getData("text/plain"));
                    if (views.includes(d.view) && Number.isInteger(d.from) && layout.groups[d.from]?.includes(d.view)) moveTab(d.view, d.from, i); } catch { /* unrelated drag */ } }}>
                  {[...group].sort((a,b)=>Number(pinned.includes(b))-Number(pinned.includes(a))).map((v) => (
                    <div className={`tab ${layout.active[i] === v ? "active" : ""}`} key={v}
                      onContextMenu={e=>{e.preventDefault();setContext({view:v,group:i,x:Math.max(8,Math.min(e.clientX,window.innerWidth-240)),y:Math.max(8,Math.min(e.clientY,window.innerHeight-180))});}}
                      draggable onDragStart={(e) => e.dataTransfer.setData("text/plain", JSON.stringify({ view: v, from: i }))}>
                      <button role="tab" aria-selected={layout.active[i] === v} tabIndex={layout.active[i]===v?0:-1}
                        onKeyDown={e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();const tabs=[...e.currentTarget.closest('.tabs')!.querySelectorAll<HTMLButtonElement>('[role="tab"]')];const at=tabs.indexOf(e.currentTarget);const next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(at+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].click();tabs[next].focus();}}}
                        onClick={() => open(v, i)}>{pinned.includes(v)?"◆ ":""}{viewTitle[v]}</button>
                      <button disabled={pinned.includes(v)} title={pinned.includes(v)?"Unpin this tab to close it":"Close tab"} aria-label={`Close ${v} in group ${i + 1}`} onClick={() => close(v, i)}>×</button>
                    </div>
                  ))}
                  <span className="tab-tools">
                    <button aria-label={`Open ${layout.active[i]} in new tab`} title="Open this view in a new browser tab" onClick={()=>openWorkspace(layout.active[i],false)}>↗</button>
                    <button aria-label={`Open ${layout.active[i]} in new window`} title="Open this view in a separate window (e.g. a second monitor)" onClick={()=>openWorkspace(layout.active[i],true)}>▣</button>
                  </span>
                </div>}
                <div className="view" role="tabpanel" aria-label={layout.active[i]}>
                  {group.map(v=><div key={v} hidden={layout.active[i]!==v}><ErrorBoundary name={viewTitle[v]}>{body(v)}</ErrorBoundary></div>)}
                </div>
              </section>
            ))}
          </div>
          {layout.bottom && !layout.maximized && (
            <section className="bottom" style={{ height: layout.bottomHeight }}>
              <ResizeHandle label="Resize bottom panel" horizontal reverse value={layout.bottomHeight} min={120} max={400} change={bottomHeight=>patch({bottomHeight})}/>
              <div className="bottom-tabs"><button className="active">Activity</button>
                <button className="push" title="Hide bottom panel (Ctrl+J)" onClick={() => patch({ bottom: false })}>×</button></div>
              <div className="event-log">{events.length ? events.map((e, i) => <div key={i}>{e}</div>) : "No activity recorded in this session."}</div>
            </section>
          )}
        </main>
        {layout.right && !layout.maximized && <ResizeHandle label="Resize inspector" reverse value={layout.rightWidth} min={200} max={440} change={rightWidth=>patch({rightWidth})}/>}
        {layout.right && !layout.maximized && (
          <aside className="inspector" style={{ width: layout.rightWidth }}>
            <div className="panel-heading">ATTENTION <button title="Hide (Ctrl+Alt+B)" onClick={() => patch({ right: false })}>×</button></div>
            <div className="inspector-body">
              {overview ? (overview.attention.length ? overview.attention.map((a,n)=><button key={n} className={`attention-item ${a.severity}`} onClick={()=>{goto(a.goto.view,a.goto.section);go(a.goto.view);}}>
                <strong>{a.title}</strong><span>{a.detail}</span></button>) : <p className="muted">Nothing needs attention.</p>) : <p className="muted">{signedOut?'Sign in to see live status.':'Loading…'}</p>}
            </div>
          </aside>
        )}
      </div>
      {appearance.status && <footer>
        <span><span className={`dot ${runtime ? "ready" : ""}`} />{runtime ? (runtime.database_connected ? "Server and database OK" : "Database unavailable") : checking ? "Connecting…" : "Server unavailable"}</span>
        {overview && <button className="footer-link" onClick={()=>{goto('Market data','live');go('Market data');}}>
          {overview.feeds.paused ? 'Market data paused' : overview.feeds.collectors ? `Market data: ${overview.feeds.live} of ${overview.feeds.collectors} feeds live${overview.feeds.problems?` · ${overview.feeds.problems} with a problem`:''}` : `Market data: ${overview.feeds.active_plans} active plan${overview.feeds.active_plans===1?'':'s'}`}</button>}
        {overview && <button className="footer-link" onClick={()=>{goto('Configuration','controls');go('Configuration');}}>Order sending {live.length?`ON (${live.join(', ')})`:'OFF'}</button>}
        {overview && overview.attention.length>0 && <button className="footer-link" onClick={()=>go('Operations')}>{overview.attention.length} item{overview.attention.length>1?'s need':' needs'} attention</button>}
        <span className="push">{appearanceError || storageError || (multi ? `${layout.groups.length} groups · ${layout.workspace} layout` : runtime?.release ?? '')}</span>
      </footer>}
      {context && <div className="context-overlay" onPointerDown={()=>setContext(null)}><div className="tab-context" style={{left:context.x,top:context.y}} role="dialog" aria-label="Tab actions" onPointerDown={e=>e.stopPropagation()} onKeyDown={e=>{if(e.key==='Escape')setContext(null);}}>
        <button autoFocus onClick={()=>{togglePin(context.view);setContext(null);}}>{pinned.includes(context.view)?'Unpin tab':'Pin tab'}</button>
        <button disabled={pinned.includes(context.view)} onClick={()=>{close(context.view,context.group);setContext(null);}}>Close tab</button>
        <button onClick={()=>{closeOthers(context.view,context.group);setContext(null);}}>Close other unpinned tabs</button>
        <button disabled={layout.groups.length<2} onClick={()=>{moveTab(context.view,context.group,(context.group+1)%layout.groups.length);setContext(null);}}>Move to next group</button>
        <button onClick={()=>{openWorkspace(context.view,true);setContext(null);}}>Open in separate window</button>
        <button onClick={()=>setContext(null)}>Dismiss · Esc</button>
      </div></div>}
      {palette && <CommandPalette commands={commands} dismiss={()=>setPalette(false)}/>}
      {help && <div className="overlay" onClick={()=>setHelp(false)}><div className="palette guide" role="dialog" aria-modal="true" aria-label="Workbench guide" onClick={e=>e.stopPropagation()}><h2>Working faster</h2><button autoFocus onClick={()=>setHelp(false)}>Close guide · Esc</button>
        <p><b>Ctrl K</b> goes to any screen or section by typing a few letters. The rail on the left shows the main areas; a red number means something there needs attention. Click a status chip at the top to go straight to it.</p>
        <p>Side by side: Layout › Two columns, then drag a tab to the other group. Ctrl-click a rail icon to open it as an extra tab. Right-click a tab to pin it or open it in a separate window (second monitor).</p>
        {commands.filter(c=>c.shortcut).map(c=><div className="shortcut-row" key={c.label}><span>{c.label}</span><kbd>{c.shortcut}</kbd></div>)}
        <p>Ctrl+1–4 focuses a group. Alt+←/→ switches tabs.</p></div></div>}
    </div>
  );
}

function Home({ overview, signedOut, runtime, error, refresh }: { overview: OverviewData | null; signedOut: boolean; runtime: Runtime | null; error: string; refresh: () => void }) {
  if (signedOut) return <SignIn title="COREI-OS-MINI" done={() => window.dispatchEvent(new Event('corei-session'))} />;
  const live = overview?.order_sending.filter(o => o.enabled).map(o => o.environment) ?? [];
  const actions: [string, View, string?][] = [['New collection plan', 'Market data', 'plans'], ['Live prices', 'Market data', 'live'], ['Add or check an MT5 account', 'Configuration', 'accounts'],
    ['Review approvals', 'Configuration', 'approvals'], ['Order sending switch', 'Configuration', 'controls'], ['Database tables', 'Infrastructure', 'tables']];
  return <div className="content home">
    <div className="home-head"><h1>Today</h1><span className="muted">{overview ? `Signed in as ${overview.user.username}` : 'Loading…'}</span>
      <button onClick={refresh}>Refresh</button></div>
    {error && <div role="alert" className="notice danger">{error}</div>}
    <div className="home-tiles">
      <button className={`tile ${overview?.feeds.problems ? 'bad' : overview?.feeds.live ? 'ok' : ''}`} onClick={() => goto('Market data', 'live')}>
        <small>MARKET DATA</small><strong>{overview ? (overview.feeds.paused ? 'Paused' : `${overview.feeds.live} of ${overview.feeds.collectors} live`) : '—'}</strong>
        <span>{overview ? `${overview.feeds.active_plans} active plan${overview.feeds.active_plans === 1 ? '' : 's'}${overview.feeds.problems ? ` · ${overview.feeds.problems} problem${overview.feeds.problems > 1 ? 's' : ''}` : ''}` : ''}</span></button>
      <button className={`tile ${live.includes('LIVE') ? 'live' : ''}`} onClick={() => goto('Configuration', 'controls')}>
        <small>ORDER SENDING</small><strong>{live.length ? `ON · ${live.join(', ')}` : 'OFF'}</strong><span>Firm-wide switch per environment</span></button>
      <button className={`tile ${overview?.approvals ? 'warn' : ''}`} onClick={() => goto('Configuration', 'approvals')}>
        <small>APPROVALS</small><strong>{overview ? overview.approvals : '—'}</strong><span>Changes waiting for a second person</span></button>
      <button className={`tile ${runtime && !runtime.database_connected ? 'bad' : runtime ? 'ok' : ''}`} onClick={() => goto('Infrastructure', 'health')}>
        <small>SYSTEM</small><strong>{runtime ? (runtime.database_connected ? 'Running' : 'Database down') : 'Checking'}</strong><span>{runtime ? `Release ${runtime.release}` : ''}</span></button>
    </div>
    <div className="home-section"><h2>Needs attention</h2>
      {overview ? (overview.attention.length ? <div className="attention">{overview.attention.map((a, n) =>
        <div key={n} className={`attention-item ${a.severity}`}><div><span className="attention-area">{a.area}</span><strong>{a.title}</strong><p>{a.detail}</p></div>
          <button onClick={() => goto(a.goto.view, a.goto.section)}>Open →</button></div>)}</div>
        : <p className="muted">Nothing needs attention.</p>) : <p className="muted">Loading…</p>}
    </div>
    <div className="home-section"><h2>Quick actions</h2>
      <div className="quick">{actions.map(([label, v, sec]) => <button key={label} onClick={() => goto(v, sec)}>{label} →</button>)}
        <button onClick={() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', ctrlKey: true }))}>Search everything <kbd>Ctrl K</kbd></button></div>
    </div>
  </div>;
}

function Monitor({ overview, signedOut }: { overview: OverviewData | null; signedOut: boolean }) {
  if (signedOut) return <SignIn title="MONITOR" done={() => window.dispatchEvent(new Event('corei-session'))} />;
  return <div className="content home monitor">
    <div className="home-head"><h1>Monitor</h1><span className="muted">Everything that needs attention, and the health of every part of the system</span></div>
    <div className="home-section"><h2>Needs attention</h2>
      {overview ? (overview.attention.length ? <div className="attention">{overview.attention.map((a, n) =>
        <div key={n} className={`attention-item ${a.severity}`}><div><span className="attention-area">{a.area}</span><strong>{a.title}</strong><p>{a.detail}</p></div>
          <button onClick={() => goto(a.goto.view, a.goto.section)}>Open →</button></div>)}</div> : <p className="muted">Nothing needs attention.</p>) : <p className="muted">Loading…</p>}
    </div>
    <div className="home-section"><h2>System health</h2><Health /></div>
  </div>;
}
