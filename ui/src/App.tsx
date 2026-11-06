import { useEffect, useRef, useState } from "react";
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
  execution_mode: string;
  database_connected: boolean;
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
  "Market data": "Feed selection, source quality and assessment history",
  Providers: "Broker and exchange connections",
  Accounts: "Account groups and eligibility assignments",
  Instruments: "Canonical instruments and provider symbol mappings",
  Strategies: "Strategy versions, instrument universes and assignments",
  Execution: "Account allocation, order lifecycle and reconciliation",
  Portfolio: "Positions, balances, fees and shared exposure",
  Operations: "Connection health, service status and operational records",
  Settings: "Workspace layout and navigation preferences",
};
const icons = ["◈", "≋", "⇄", "▣", "⌕", "ƒ", "↗", "▤", "◎", "⚙"];
const scopeItems: Record<string, string[]> = {
  Providers: ["Connections", "Data sources", "Execution routes"],
  Accounts: ["Individual accounts", "Account groups", "Eligibility"],
  Instruments: ["Canonical instruments", "Symbol mappings", "Universes"],
  Strategies: ["Strategy registry", "Versions", "Assignments"],
};
function stored() {
  try {
    return restore(localStorage.getItem(key));
  } catch {
    return defaults();
  }
}
export default function App() {
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
    [storageError, setStorageError] = useState(""),
    [recordId, setRecordId] = useState(""),
    [record, setRecord] = useState<unknown>(null),
    [recordError, setRecordError] = useState(""),
    [recordBusy, setRecordBusy] = useState(false);
  const alive = useRef(true),
    pending = useRef<AbortController | null>(null),
    recordRequest = useRef<AbortController | null>(null);
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
    setRecord(null);
    recordRequest.current?.abort();
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
        data.contract_version !== 1 ||
        data.execution_mode !== "PAPER" ||
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
        log("Backend identity verified · paper mode");
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
    return () => {
      alive.current = false;
      pending.current?.abort();
      recordRequest.current?.abort();
    };
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      const typing = e.target instanceof Element && e.target.closest('input,textarea,select,[contenteditable="true"]');
      if (mod && e.key.toLowerCase() === 'p') { e.preventDefault(); setPalette(x=>!x); return; }
      if (e.key === 'Escape') { setPalette(false); setHelp(false); }
      if (typing || palette) return;
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
  function close(view: View, group: number) {
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
  async function loadRecord() {
    if (!runtime) return;
    setRecord(null);
    setRecordError("");
    if (
      !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
        recordId,
      )
    ) {
      setRecordError("Enter a complete assessment UUID.");
      return;
    }
    recordRequest.current?.abort();
    const controller = new AbortController();
    recordRequest.current = controller;
    const timer = setTimeout(() => controller.abort(), 8000);
    setRecordBusy(true);
    try {
      const r = await fetch(
        `/api/market-data/assessments/${encodeURIComponent(recordId)}`,
        { signal: controller.signal, cache: "no-store" },
      );
      if (!r.ok)
        throw new Error(
          r.status === 404
            ? "Assessment not found."
            : `Assessment unavailable (HTTP ${r.status}).`,
        );
      const data = await r.json();
      if (alive.current && recordRequest.current === controller) {
        setRecord(data);
        log("Historical assessment loaded");
      }
    } catch (e) {
      if (alive.current && recordRequest.current === controller)
        setRecordError(e instanceof Error ? e.message : String(e));
    } finally {
      clearTimeout(timer);
      if (alive.current && recordRequest.current === controller)
        setRecordBusy(false);
    }
  }
  const active = layout.active[layout.focus];
  function body(view: View) {
    if (view === "Settings")
      return (
        <div className="content">
          <div className="eyebrow">PERSONAL WORKSPACE</div>
          <h1>Arrange your workstation</h1>
          <p>
            Preferences are saved in this browser. Layout changes do not change
            trading state.
          </p>
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
                patch({ ...defaults(layout.workspace) });
                log("Layout reset");
              }}
            >
              Reset layout
            </button>
          </div>
        </div>
      );
    if (view === "Overview" || view === "Operations")
      return (
        <div className="content">
          <div className="eyebrow">
            {layout.workspace.toUpperCase()} / WORKSTATION
          </div>
          <h1>
            {view === "Overview"
              ? "Workspace overview"
              : "Operational status"}
          </h1>
          <p>Observe → understand → decide → act → reconcile.</p>
          <div className="metrics">
            <article>
              <small>BACKEND</small>
              <strong>
                {checking ? "Checking" : runtime ? "Connected" : "Unavailable"}
              </strong>
              <span>
                {runtime
                  ? `Release ${runtime.release}`
                  : "Identity must be verified"}
              </span>
            </article>
            <article>
              <small>OPERATING MODE</small>
              <strong>{runtime ? "Paper" : "Unverified"}</strong>
              <span>Live execution unavailable</span>
            </article>
            <article>
              <small>DATABASE CONNECTION</small>
              <strong>
                {runtime
                  ? runtime.database_connected
                    ? "Connected"
                    : "Unavailable"
                  : "Unknown"}
              </strong>
              <span>Connection check only; schema readiness not verified</span>
            </article>
          </div>
          <div className="section-title">
            <h2>Platform capabilities</h2>
            <button disabled={checking} onClick={() => void refresh()}>
              Refresh status
            </button>
          </div>
          {error && (
            <div role="alert" className="notice danger">
              {error}
            </div>
          )}
          {runtime ? (
            <div className="capabilities">
              {runtime.capabilities.map((c) => (
                <article key={c.id}>
                  <span className={`dot ${c.available ? "ready" : ""}`} />
                  <div>
                    <strong>{c.label}</strong>
                    <p>{c.reason}</p>
                  </div>
                  <span className="tag">
                    {c.available ? "AVAILABLE" : "NOT IMPLEMENTED"}
                  </span>
                </article>
              ))}
            </div>
          ) : (
            <div className="empty">
              Capability state is unavailable until the backend is verified.
            </div>
          )}
          <div className="section-title">
            <h2>Start from any dimension</h2>
          </div>
          <div className="launchers">
            {(
              ["Providers", "Accounts", "Instruments", "Strategies"] as View[]
            ).map((v) => (
              <button key={v} onClick={() => open(v)}>
                <strong>{v} ↗</strong>
                <span>{descriptions[v]}</span>
              </button>
            ))}
          </div>
        </div>
      );
    if (view === "Market data")
      return (
        <div className="content">
          <div className="eyebrow">MARKET INTELLIGENCE</div>
          <h1>Sources & quality</h1>
          <p>
            Data-source selection remains independent from execution routing.
          </p>
          <div className="notice">
            Provider streaming is not implemented. No live prices or feed
            latency measurements are available.
          </div>
          <div className="section-title">
            <h2>Retrieve a saved assessment</h2>
            <span className="tag">HISTORICAL INPUT</span>
          </div>
          <p>
            Load an assessment already persisted by this repository's demo API.
            Its timestamps describe the supplied batch, not the current market.
          </p>
          <form
            className="record-form"
            onSubmit={(e) => {
              e.preventDefault();
              void loadRecord();
            }}
          >
            <input
              aria-label="Assessment UUID"
              placeholder="Assessment UUID"
              value={recordId}
              onChange={(e) => setRecordId(e.target.value)}
            />
            <button disabled={!runtime || recordBusy}>
              {recordBusy ? "Loading…" : "Load record"}
            </button>
          </form>
          {recordError && (
            <p role="alert" className="danger">
              {recordError}
            </p>
          )}
          {record ? (
            <pre className="record">{JSON.stringify(record, null, 2)}</pre>
          ) : (
            <div className="empty">No assessment loaded.</div>
          )}
        </div>
      );
    return (
      <div className="content">
        <div className="eyebrow">{view.toUpperCase()}</div>
        <h1>{descriptions[view]}</h1>
        <div className="notice">
          This capability is planned and has no operational implementation in
          the current repository.
        </div>
        <div className="empty">
          <span className="empty-icon">{icons[views.indexOf(view)]}</span>
          <h2>{view} workspace</h2>
          <p>
            {view === "Execution"
              ? "No order submission controls are enabled. Final account risk and execution lifecycle integration are required."
              : "Registries and provider-backed state will appear here as their backend capability is implemented."}
          </p>
          <button disabled title="Requires backend implementation">
            {view === "Execution"
              ? "Submit order unavailable"
              : `Configure ${view.toLowerCase()} unavailable`}
          </button>
        </div>
        <div className="principle">
          Provider · connection · account · instrument · strategy
          <br />
          <span>
            Explicit eligibility connects these dimensions. Selecting a view
            does not activate trading.
          </span>
        </div>
      </div>
    );
  }
  const commands:Command[] = [
    ...views.map(v=>({section:'Navigate',label:`Open ${v}`,run:()=>open(v)})),
    {section:'Workspace',label:'Command palette',shortcut:'Ctrl+Shift+P',run:()=>setPalette(true)},
    {section:'Workspace',label:'Close active tab',run:()=>close(active,layout.focus)},
    {section:'Workspace',label:'Close other tabs in group',run:()=>patch({groups:layout.groups.map((g,i)=>i===layout.focus?[active]:g)})},
    {section:'Workspace',label:'Refresh backend status',run:()=>void refresh()},
    {section:'Workspace',label:'Reset current workspace layout',run:()=>setLayout(defaults(layout.workspace))},
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
    {section:'Help',label:'Keyboard shortcuts & workbench guide',run:()=>setHelp(true)},
  ];
  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <b>◈</b> COREI<span>OS MINI</span>
        </div>
        <select
          aria-label="Workspace"
          value={layout.workspace}
          onChange={(e) => {
            const name = e.target.value;
            let saved = defaults(name);
            try {
              const raw = localStorage.getItem(`${key}.${name}`);
              if (raw) saved = { ...restore(raw), workspace: name };
            } catch {}
            setLayout(saved);
            log(`Workspace switched to ${name}`);
          }}
        >
          {["Research", "Execution", "Operations"].map((x) => (
            <option key={x}>{x}</option>
          ))}
        </select>
        <button className="command" onClick={() => setPalette(true)}>
          Search views and commands <kbd>Ctrl ⇧ P</kbd>
        </button>
        <span className="tag paper">PAPER WORKSTATION</span>
        <button
          title="Toggle explorer"
          aria-pressed={layout.left}
          onClick={() => patch({ left: !layout.left })}
        >
          ◧
        </button>
        <button
          title="Toggle bottom panel"
          aria-pressed={layout.bottom}
          onClick={() => patch({ bottom: !layout.bottom })}
        >
          ⬒
        </button>
        <button
          title="Toggle inspector"
          aria-pressed={layout.right}
          onClick={() => patch({ right: !layout.right })}
        >
          ◨
        </button>
      </header>
      <MenuBar commands={commands}/>
      <div className="shell">
        <nav className="activity" aria-label="Activities">
          {views.map((v, i) => (
            <button
              key={v}
              title={v}
              aria-label={v}
              aria-current={layout.activity === v ? "page" : undefined}
              onClick={() => {
                patch({ activity: v, left: true });
                open(v);
              }}
            >
              <span aria-hidden="true">{icons[i]}</span>
            </button>
          ))}
        </nav>
        {layout.left && !layout.maximized && (
          <aside className="explorer" style={{ width: layout.leftWidth }}>
            <div className="panel-heading">
              EXPLORER <button title="Collapse or expand explorer" aria-expanded={!collapsed} onClick={()=>setCollapsed(!collapsed)}>{collapsed?"▸":"▾"}</button>
              <button
                title="Hide explorer"
                onClick={() => patch({ left: false })}
              >
                ×
              </button>
            </div>
            <input
              aria-label="Filter explorer"
              placeholder="Filter views…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <details className="open-editors" open><summary>OPEN VIEWS · {layout.groups.reduce((n,g)=>n+g.length,0)}</summary>
              {layout.groups.map((g,i)=>g.map(v=><button key={i+v} onClick={()=>open(v,i)} className={layout.focus===i&&active===v?'selected':''}><span>▤ {v}</span><small>Group {i+1}</small></button>))}
            </details>
            <div className="tree">
              <div className="tree-project">⌄ COREI-OS-MINI</div>
              {!collapsed && views
                .filter((v) =>
                  (v + " " + descriptions[v])
                    .toLowerCase()
                    .includes(filter.toLowerCase()),
                )
                .map((v) => (
                  <div key={v}>
                    <button
                      className={active === v ? "selected" : ""}
                      onClick={() => open(v)}
                    >
                      <span>
                        {scopeItems[v] ? "⌄" : "·"} {v}
                      </span>
                      <span className="muted">
                        {v === "Overview"
                          ? ""
                          : v === "Settings"
                            ? "local"
                            : "—"}
                      </span>
                    </button>
                    {scopeItems[v] &&
                      layout.activity === v &&
                      scopeItems[v].map((s) => (
                        <div className="tree-child" key={s}>
                          {s}
                          <small>Not configured</small>
                        </div>
                      ))}
                  </div>
                ))}
            </div>
            <div className="explorer-note">
              MULTI-DIMENSIONAL MODEL
              <p>
                Independent registries.
                <br />
                Explicit assignments.
              </p>
            </div>
          </aside>
        )}
        {layout.left && !layout.maximized && <ResizeHandle label="Resize explorer" value={layout.leftWidth} min={160} max={380} change={leftWidth=>patch({leftWidth})}/>}
        <main>
          <div className="workspace-toolbar">
            <span>
              {layout.workspace} <span className="muted">/ {active}</span>
            </span>
            <div>
              <button
                onClick={split}
                title="Split workspace or change split direction"
              >
                {layout.groups.length === 1
                  ? "Split workspace"
                  : "Change split"}
              </button>
              <button onClick={() => patch({ maximized: !layout.maximized })}>
                {layout.maximized ? "Restore groups" : "Focus group"}
              </button>
              <button onClick={() => open("Settings")}>Layout</button>
            </div>
          </div>
          <div className={`groups count-${layout.groups.length}`} style={{ flexDirection: layout.axis }}>
            {layout.groups.map(
              (group, i) =>
                (!layout.maximized || layout.focus === i) && (
                  <section
                    className={`group ${layout.focus === i ? "focused" : ""}`}
                    key={i}
                    onFocus={() => {
                      if (layout.focus !== i) patch({ focus: i });
                    }}
                  >
                    <div
                      className="tabs"
                      role="tablist"
                      aria-label={`Workspace group ${i + 1}`}
                      onDragOver={(e) => e.preventDefault()}
                      onDrop={(e) => {
                        e.preventDefault();
                        try {
                          const d = JSON.parse(
                            e.dataTransfer.getData("text/plain"),
                          );
                          if (
                            views.includes(d.view) &&
                            Number.isInteger(d.from) &&
                            layout.groups[d.from]?.includes(d.view)
                          )
                            moveTab(d.view, d.from, i);
                        } catch {
                          /* Ignore unrelated dragged data. */
                        }
                      }}
                    >
                      {group.map((v) => (
                        <div
                          className={`tab ${layout.active[i] === v ? "active" : ""}`}
                          key={v}
                          draggable
                          onDragStart={(e) =>
                            e.dataTransfer.setData(
                              "text/plain",
                              JSON.stringify({ view: v, from: i }),
                            )
                          }
                        >
                          <button
                            role="tab"
                            aria-selected={layout.active[i] === v}
                            onClick={() => open(v, i)}
                          >
                            {v}
                          </button>
                          <button
                            aria-label={`Close ${v} in group ${i + 1}`}
                            onClick={() => close(v, i)}
                          >
                            ×
                          </button>
                        </div>
                      ))}
                    </div>
                    <div className="group-actions"><span>GROUP {i+1} / {layout.active[i]}</span><button title="Close other tabs" onClick={()=>patch({groups:layout.groups.map((g,j)=>j===i?[layout.active[i]]:g)})}>Keep active</button><button title="Move active tab to next group" disabled={layout.groups.length<2} onClick={()=>moveTab(layout.active[i],i,(i+1)%layout.groups.length)}>Move →</button></div>
                    <div
                      className="view"
                      role="tabpanel"
                      aria-label={layout.active[i]}
                    >
                      {body(layout.active[i])}
                    </div>
                  </section>
                ),
            )}
          </div>
          {layout.bottom && !layout.maximized && (
            <section className="bottom" style={{ height: layout.bottomHeight }}>
              <ResizeHandle label="Resize bottom panel" horizontal reverse value={layout.bottomHeight} min={120} max={400} change={bottomHeight=>patch({bottomHeight})}/>
              <div className="bottom-tabs">
                {[
                  "Activity",
                  "Orders",
                  "Fills",
                  "Signals",
                  "Alerts",
                  "Reconciliation",
                ].map((t) => (
                  <button
                    className={bottomTab === t ? "active" : ""}
                    key={t}
                    onClick={() => setBottomTab(t)}
                  >
                    {t}
                  </button>
                ))}
                <button
                  className="push"
                  title="Hide bottom panel"
                  onClick={() => patch({ bottom: false })}
                >
                  ×
                </button>
              </div>
              {bottomTab === "Activity" ? (
                <div className="event-log">
                  {events.length
                    ? events.map((e, i) => <div key={i}>{e}</div>)
                    : "No activity recorded in this session."}
                </div>
              ) : (
                <div className="panel-empty">
                  {bottomTab} integration is not implemented. No operational
                  records are available in this view.
                </div>
              )}
            </section>
          )}
        </main>
        {layout.right && !layout.maximized && <ResizeHandle label="Resize inspector" reverse value={layout.rightWidth} min={200} max={440} change={rightWidth=>patch({rightWidth})}/>}
        {layout.right && !layout.maximized && (
          <aside className="inspector" style={{ width: layout.rightWidth }}>
            <div className="panel-heading">
              CONTEXT{" "}
              <button
                title="Hide inspector"
                onClick={() => patch({ right: false })}
              >
                ×
              </button>
            </div>
            <div className="inspector-body">
              <div className="eyebrow">ACTIVE VIEW</div>
              <h2>{active}</h2>
              <p>{descriptions[active]}</p>
              <dl>
                <dt>Workspace</dt>
                <dd>{layout.workspace}</dd>
                <dt>Provider</dt>
                <dd>None selected</dd>
                <dt>Account / group</dt>
                <dd>None selected</dd>
                <dt>Instrument</dt>
                <dd>None selected</dd>
                <dt>Strategy</dt>
                <dd>None selected</dd>
              </dl>
              <div className="notice">
                Execution destination is unset. Navigation does not authorise an
                order.
              </div>
              <h3>Workspace controls</h3>
              <button
                className="wide"
                disabled={layout.groups.length < 2}
                title="Split the workspace first to open another group"
                onClick={() =>
                  open(
                    active,
                    (layout.focus + 1) % layout.groups.length,
                  )
                }
              >
                Open in other group
              </button>
              <button className="wide" onClick={split}>
                Split / change orientation
              </button>
              <button className="wide" onClick={() => open("Settings")}>
                Resize panels
              </button>
              <div className="release">
                FOUNDATION RELEASE
                <br />
                No provider adapters configured
              </div>
            </div>
          </aside>
        )}
      </div>
      <footer>
        <span>
          <span className={`dot ${runtime ? "ready" : ""}`} />
          {runtime
            ? "Backend verified"
            : checking
              ? "Connecting…"
              : "Backend unavailable"}
        </span>
        <span>
          {storageError || "Local layout · no trading state stored in browser"}
        </span>
        <span className="push">
          Trading unavailable · {layout.groups.length} workspace group
          {layout.groups.length > 1 ? "s" : ""}
        </span>
      </footer>
      {palette && <CommandPalette commands={commands} dismiss={()=>setPalette(false)}/>}
      {help && <div className="overlay" onClick={()=>setHelp(false)}><div className="palette guide" role="dialog" aria-modal="true" aria-label="Workbench guide" onClick={e=>e.stopPropagation()}><h2>Workbench controls</h2><button autoFocus onClick={()=>setHelp(false)}>Close guide · Esc</button><p>Drag the explorer, inspector and bottom-panel edges to resize. Drag tabs between groups. Workspace selection saves each layout separately.</p>{commands.filter(c=>c.shortcut).map(c=><div className="shortcut-row" key={c.label}><span>{c.label}</span><kbd>{c.shortcut}</kbd></div>)}<p>Ctrl+1–4 focuses a group. Separator handles also support arrow keys. Layout presets replace the open groups. Browser-reserved shortcuts may vary.</p><div className="notice">Shell controls work locally. Broker connections, orders, positions and strategies still require their backend implementation.</div></div></div>}
    </div>
  );
}
