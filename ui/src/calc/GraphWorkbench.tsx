/* Calculation graphs workbench (DEC-93): every calculation as nodes and wires, n8n-style — the graph library on the
 * left, the canvas in the middle with each node's live value on the bar you choose, the selected node's formula with
 * its numbers on the right, the result underneath. In edit mode the left side becomes the node palette: add nodes,
 * drag wires between ports (an impossible wire is refused at once and named), set parameters, check, preview, save the
 * draft, propose; a second person approves; an approved signal graph can be put to use for a signal definition.
 * Why: the owner wants every formula visible and changeable without code, the same way everywhere.
 * Purpose: GraphWorkbench (canvas or plain view), openGraph (open a graph from any screen).
 * Must never: decide anything — the server checks, versions, approves and runs; built-ins are read-only (copy them). */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { applyNodeChanges, Handle, Position, type Connection, type Edge, type Node, type NodeChange, type NodeProps } from '@xyflow/react';
import './graph.css';
import FlowCanvas from './FlowCanvas';
import { api } from '../Configuration';
import { setupStrategyFromRule } from '../nav';
import { useViewVisible } from '../visibility';
import { pill, useData, type Ctx, type Row } from '../StageShell';
import { MiniSpark, SourcePick, TfPick } from '../SignalLibrary';
import { compatible, FAMILY_ICON, FAMILY_LABEL, fmt, isSeries, newId, openGraph, OUTPUT_OF, portsOf, takeGraphSelection, PURPOSE_LABEL, type Definition, type GNode, type NodeType, type Param, type Port } from './ports';


type Trace = Record<string, { type: string; label: string; formula?: string; inputs?: Row; outputs?: Row; explain?: string; error?: string; ms: number }>;
type Preview = Row & { ok: boolean; valid: boolean; errors: any; outputs?: Row; times?: string[]; trace?: Trace; trace_bar?: number; trace_time?: string; bars?: number; ms?: number; warmup?: number };
const STATUS_TONE: Record<string, string> = { APPROVED: 'ok', DRAFT: 'warn', PROPOSED: 'warn', RETIRED: 'off' };
const canvasAvailable = () => typeof window !== 'undefined' && 'ResizeObserver' in window;

/* ---------- the node on the canvas ---------- */

type NodeData = { nt?: NodeType; g: GNode; inputs: Port[]; outputs: Port[]; trace?: Trace[string]; editing: boolean };

function CalcNode({ data, selected }: NodeProps<Node<NodeData>>) {
  const { nt, g, inputs, outputs, trace } = data;
  const fam = nt?.family ?? 'maths';
  const outVals = trace?.outputs ?? {}, inVals = trace?.inputs ?? {};
  return <div className={`cg-node fam-${fam}${selected ? ' sel' : ''}${trace?.error ? ' err' : ''}`} title={nt?.doc}>
    <div className="cg-head"><span className="cg-ico" aria-hidden>{FAMILY_ICON[fam]}</span><b>{g.label || nt?.label || g.type}</b>
      <small>{g.id}</small></div>
    <div className="cg-body">
      <div className="cg-col">{inputs.map(p => <div key={p.name} className="cg-port in" title={`${p.name}: ${p.type}${p.optional ? ' (optional)' : ''}`}>
        <Handle type="target" position={Position.Left} id={p.name} className={`cg-h t-${p.type.replace(/[^a-z]/g, '')}`} />
        <span>{p.name}</span>{p.name in inVals && <em>{fmt(inVals[p.name])}</em>}</div>)}</div>
      <div className="cg-col out">{outputs.map(p => <div key={p.name} className="cg-port out" title={`${p.name}: ${p.type}`}>
        {p.name in outVals && <em>{fmt(outVals[p.name])}</em>}<span>{p.name}</span>
        <Handle type="source" position={Position.Right} id={p.name} className={`cg-h t-${p.type.replace(/[^a-z]/g, '')}`} /></div>)}
        {!outputs.length && trace?.outputs && Object.entries(trace.outputs).filter(([k]) => k !== 'inputs').slice(0, 3).map(([k, v]) =>
          <div key={k} className="cg-port res"><span>{k}</span><em>{fmt(v)}</em></div>)}</div>
    </div>
    {trace?.error ? <div className="cg-err">{trace.error}</div> : <div className="cg-formula">{trace?.formula ?? nt?.formula}</div>}
  </div>;
}
const NODE_TYPES = { calc: CalcNode };

/* ---------- parameter editor ---------- */

function ParamInput({ p, value, change, disabled, graphParams }: { p: Param; value: unknown; change: (v: unknown) => void; disabled: boolean; graphParams: string[] }) {
  const ref = typeof value === 'string' && /^\{\{\s*[a-z0-9_]+\s*\}\}$/.test(value);
  const label = `${p.name}${p.doc ? ` — ${p.doc}` : ''}`;
  const refPick = (p.kind === 'int' || p.kind === 'float') && graphParams.length > 0 && !disabled &&
    <select aria-label={`${p.name} from a graph parameter`} value={ref ? String(value).replace(/[{} ]/g, '') : ''} onChange={e => change(e.target.value ? `{{${e.target.value}}}` : p.default)}>
      <option value="">fixed</option>{graphParams.map(n => <option key={n} value={n}>{`{{${n}}}`}</option>)}</select>;
  let input: ReactNode;
  if (p.kind === 'bool') input = <input type="checkbox" aria-label={p.name} disabled={disabled} checked={Boolean(value)} onChange={e => change(e.target.checked)} />;
  else if (p.kind === 'choice') input = <select aria-label={p.name} disabled={disabled} value={String(value ?? '')} onChange={e => change(e.target.value)}>{(p.choices ?? []).map(c => <option key={c}>{c}</option>)}</select>;
  else if (ref) input = <code className="cg-ref">{String(value)}</code>;
  else if (p.kind === 'expr') input = <textarea aria-label={p.name} rows={2} disabled={disabled} value={String(value ?? '')} onChange={e => change(e.target.value)} spellCheck={false} />;
  else input = <input aria-label={p.name} disabled={disabled} value={value === null || value === undefined ? '' : String(value)}
    onChange={e => change(p.kind === 'int' || p.kind === 'float' ? (e.target.value === '' || e.target.value === '-' ? e.target.value : Number.isNaN(Number(e.target.value)) ? e.target.value : Number(e.target.value)) : e.target.value)} />;
  return <label className="cg-param" title={label}><span>{p.name}{p.min != null || p.max != null ? <small> {p.min ?? ''}…{p.max ?? ''}</small> : null}</span>
    <span className="cg-param-in">{input}{refPick}</span>{p.doc && <small className="cfg-note">{p.doc}</small>}</label>;
}

/* ---------- the workbench ---------- */

type WbProps = { ctx: Ctx; purposes?: string[]; intro?: string; section?: string };

/* In "All panels" (every item of a domain on one page) the workbench shows a light summary with Open: a full canvas,
 * its previews and polling per panel would make that page heavy and jumpy. Its own sub-tab shows the whole workbench. */
export default function GraphWorkbench(props: WbProps) {
  const ref = useRef<HTMLDivElement>(null), [inAll, setInAll] = useState<boolean | null>(null);
  useLayoutEffect(() => { setInAll(Boolean(ref.current?.closest('.ws-panel'))); }, []);
  return <div ref={ref} className="cg-host">{inAll === null ? null : inAll ? <GraphSummary {...props} /> : <Workbench {...props} />}</div>;
}

function GraphSummary({ ctx, purposes, section = 'graphs' }: WbProps) {
  const [lib] = useData<Row>('calc/graphs', 0);
  const graphs: Row[] = (lib?.items ?? []).filter((g: Row) => !purposes || purposes.includes(g.purpose));
  return <section className="intel cg-summary">
    <p className="cfg-note">{graphs.length} graph{graphs.length === 1 ? '' : 's'} here, each drawn as nodes and wires with live numbers. <button className="primary" onClick={() => ctx.open(section)}>Open the graph canvas</button></p>
    <div className="cg-chips">{graphs.slice(0, 24).map(g => <button key={g.code} onClick={() => { openGraph(g.code); ctx.open(section); }} title={g.description}>{g.name}</button>)}</div>
  </section>;
}

function Workbench({ ctx, purposes, intro }: WbProps) {
  const [cat] = useData<Row>('calc/nodes', 0);
  const [lib, libErr, reloadLib] = useData<Row>('calc/graphs', 0);
  const [bindings, , reloadBind] = useData<Row>('calc/bindings', 0);
  const types: Record<string, NodeType> = useMemo(() => Object.fromEntries((cat?.items ?? []).map((n: NodeType) => [n.type, n])), [cat]);
  const graphs: Row[] = useMemo(() => (lib?.items ?? []).filter((g: Row) => !purposes || purposes.includes(g.purpose)), [lib, purposes]);
  const [code, setCode] = useState<string>(''), [ver, setVer] = useState<number | null>(null);
  const [version, setVersion] = useState<Row | null>(null), [def, setDef] = useState<Definition | null>(null), [dirty, setDirty] = useState(false);
  const [editing, setEditing] = useState(false), [selected, setSelected] = useState<string | null>(null);
  const [sources] = useData<Row>('trading/sources', 0);
  const [src, setSrcRaw] = useState(() => { try { return localStorage.getItem('corei.calc.src') ?? ''; } catch { return ''; } }), [tf, setTf] = useState('H1'), [nbars, setNbars] = useState(300), [traceBar, setTraceBar] = useState<number | null>(null);
  const [over, setOver] = useState<Record<string, number>>({}), [preview, setPreview] = useState<Preview | null>(null), [running, setRunning] = useState(false);
  const [msg, setMsg] = useState(''), [err, setErr] = useState(''), [reason, setReason] = useState(''), [q, setQ] = useState('');
  const [view, setView] = useState<'canvas' | 'plain'>(canvasAvailable() ? 'canvas' : 'plain'), [wide, setWide] = useState(false);
  const [rfNodes, setRfNodes] = useState<Node<NodeData>[]>([]);
  const [fresh, setFresh] = useState<Row | null>(null);
  const runSeq = useRef(0), openSeq = useRef(0);
  const setSrc = (v: string) => { setSrcRaw(v); try { localStorage.setItem('corei.calc.src', v); } catch { /* per-viewer convenience only */ } };
  useEffect(() => { // the first collected instrument until the viewer chooses one, so every graph opens with live numbers
    const items: Row[] = sources?.items ?? [];
    if (items.length && !items.some(s => `${s.account_id}|${s.broker_symbol}` === src)) setSrcRaw(`${items[0].account_id}|${items[0].broker_symbol}`);
  }, [sources]); // eslint-disable-line react-hooks/exhaustive-deps

  const visible = useViewVisible();
  const strategyWorkbench = purposes?.includes('STRATEGY_RULE') ?? false;

  /* open a graph: from the list, from another screen, or the first one */
  const open = useCallback(async (c: string, n: number | null = null) => {
    const seq = ++openSeq.current;
    setErr(''); setMsg('');
    try {
      const g = await api(`calc/graphs/${c}`);
      const last = n ?? (g.versions?.length ? g.versions[g.versions.length - 1].version : 1);
      const v = await api(`calc/graphs/${c}/versions/${last}`);
      if (seq !== openSeq.current) return;
      setCode(c); setVer(last); setVersion({ ...v, graph: g }); setDef(v.definition); setDirty(false); setEditing(false); setSelected(null); setOver({}); setPreview(null); setTraceBar(null);
    } catch (x) { if (seq === openSeq.current) setErr((x as Error).message); }
  }, []);
  useEffect(() => {
    if (!visible) return;
    const pending = takeGraphSelection(strategyWorkbench);
    if (pending) void open(pending.code, pending.version);
    const on = (e: Event) => {
      const selection = (e as CustomEvent<ReturnType<typeof takeGraphSelection>>).detail;
      if (!selection || (!strategyWorkbench && selection.version != null)) return;
      takeGraphSelection(strategyWorkbench);
      void open(selection.code, selection.version);
    };
    window.addEventListener('corei-open-graph', on);
    return () => window.removeEventListener('corei-open-graph', on);
  }, [open, visible, strategyWorkbench]);
  useEffect(() => { if (visible && !code && graphs.length && !openSeq.current) void open(graphs[0].code); }, [code, graphs, open, visible]);

  /* run on the chosen instrument whenever the graph, its parameters or the bar change (debounced) */
  const run = useCallback(async () => {
    if (!def || !src) return;
    const [account_id, symbol] = src.split('|');
    const seq = ++runSeq.current; setRunning(true);
    try {
      const r = await api('calc/preview', 'POST', { definition: def, parameters: over, account_id, symbol, timeframe: tf, bars: nbars, tail: 200, ...(traceBar != null ? { trace_bar: traceBar } : {}) });
      if (seq === runSeq.current) { setPreview(r); setErr(''); }
    } catch (x) { if (seq === runSeq.current) { setPreview(null); setErr((x as Error).message); } }
    finally { if (seq === runSeq.current) setRunning(false); }
  }, [def, src, tf, nbars, traceBar, over]);
  useEffect(() => { const t = setTimeout(() => void run(), 350); return () => clearTimeout(t); }, [run]);

  /* the canvas follows the definition and the trace */
  const trace: Trace = preview?.valid ? preview.trace ?? {} : {};
  useEffect(() => {
    if (!def) { setRfNodes([]); return; }
    setRfNodes(prev => def.nodes.map(g => {
      const nt = types[g.type], io = portsOf(nt, g.params), old = prev.find(p => p.id === g.id);
      return { id: g.id, type: 'calc', position: { x: g.position?.[0] ?? 0, y: g.position?.[1] ?? 0 }, selected: g.id === selected || old?.selected,
        data: { nt, g, inputs: io.inputs, outputs: io.outputs, trace: trace[g.id], editing } };
    }));
  }, [def, types, preview, editing, selected]); // eslint-disable-line react-hooks/exhaustive-deps
  const rfEdges: Edge[] = useMemo(() => (def?.edges ?? []).map(e => {
    const [s, sh] = e.from.split('.'), [t, th] = e.to.split('.');
    const nt = types[def!.nodes.find(n => n.id === s)?.type ?? ''];
    const typ = portsOf(nt, def!.nodes.find(n => n.id === s)?.params ?? {}).outputs.find(p => p.name === sh)?.type ?? '';
    return { id: `${e.from}->${e.to}`, source: s, sourceHandle: sh, target: t, targetHandle: th, className: `cg-edge t-${typ.replace(/[^a-z]/g, '')}` };
  }), [def, types]);

  const change = (fn: (d: Definition) => Definition) => { if (!def) return; setDef(fn(structuredClone(def))); setDirty(true); };
  const onNodesChange = (changes: NodeChange<Node<NodeData>>[]) => {
    setRfNodes(ns => applyNodeChanges(changes, ns));
    for (const c of changes) {
      if (c.type === 'select' && c.selected) setSelected(c.id);
      if (c.type === 'position' && c.position && !c.dragging && editing) change(d => ({ ...d, nodes: d.nodes.map(n => n.id === c.id ? { ...n, position: [Math.round(c.position!.x), Math.round(c.position!.y)] } : n) }));
      if (c.type === 'remove' && editing) removeNode(c.id);
    }
  };
  const portType = (node: string, port: string, side: 'in' | 'out') => {
    const g = def?.nodes.find(n => n.id === node); const io = portsOf(types[g?.type ?? ''], g?.params ?? {});
    return (side === 'in' ? io.inputs : io.outputs).find(p => p.name === port)?.type;
  };
  const onConnect = (c: Connection) => {
    if (!editing || !c.source || !c.target || !c.sourceHandle || !c.targetHandle) return;
    const st = portType(c.source, c.sourceHandle, 'out'), dt = portType(c.target, c.targetHandle, 'in');
    if (!st || !dt) return;
    if (!compatible(st, dt)) {
      setErr(`${c.source}.${c.sourceHandle} (${st}) cannot feed ${c.target}.${c.targetHandle} (${dt}).` +
        (dt.startsWith('decimal') ? ' Money, prices and quantities need a To decimal node.' : isSeries(st) && !isSeries(dt) ? ' Use a Last value node to take the value on the last bar.' : ''));
      return;
    }
    setErr('');
    const to = `${c.target}.${c.targetHandle}`;
    change(d => ({ ...d, edges: [...d.edges.filter(e => e.to !== to), { from: `${c.source}.${c.sourceHandle}`, to }] }));
  };
  const removeNode = (id: string) => { change(d => ({ ...d, nodes: d.nodes.filter(n => n.id !== id), edges: d.edges.filter(e => !e.from.startsWith(id + '.') && !e.to.startsWith(id + '.')) })); setSelected(null); };
  const addNode = (nt: NodeType) => {
    if (!def) return;
    const id = newId(def, nt.type.split('.')[1] ?? 'n');
    const maxX = Math.max(0, ...def.nodes.map(n => n.position?.[0] ?? 0));
    change(d => ({ ...d, nodes: [...d.nodes, { id, type: nt.type, params: {}, position: [maxX + 40, 40 + (d.nodes.length % 6) * 70], label: '' }] }));
    setSelected(id);
  };

  /* life cycle */
  const isBuiltin = Boolean(version?.builtin), status = version?.status as string | undefined;
  const editable = !isBuiltin && (status === 'DRAFT' || status === 'APPROVED' || status === 'RETIRED') && (ctx.canOps || ctx.canRisk);
  const act = async (what: string, fn: () => Promise<any>, after?: (r: any) => void) => {
    setErr(''); setMsg('');
    try { const r = await fn(); setMsg(what); reloadLib(); reloadBind(); after?.(r); } catch (x) { setErr((x as Error).message); }
  };
  const needReason = reason.trim().length < 5;
  const check = () => act('Checked: no errors.', async () => { const r = await api('calc/validate', 'POST', { definition: def, parameters: over }); if (!r.ok) throw new Error(r.errors.map((e: Row) => e.message).join(' · ')); return r; });
  const save = () => act('Draft saved.', () => api(`calc/graphs/${code}/draft`, 'PUT', { definition: def, notes: def?.notes ?? '', reason }), r => { setDirty(false); void open(code, r.version); });
  const lifecycle = (a: string) => act(`${a[0].toUpperCase()}${a.slice(1)}d.`, () => api(`calc/graphs/${code}/versions/${ver}/${a}`, 'POST', { reason }), () => void open(code, ver));
  const copy = () => {
    const nc = window.prompt('Code of the copy (capitals, digits, _):', `DESK_${code.replace(/^SIG_/, '')}`.slice(0, 60)); if (!nc) return;
    void act(`Copied as ${nc}: a draft you can change.`, () => api(`calc/graphs/${code}/copy`, 'POST', { code: nc, name: `${version?.name ?? code} (desk)`, version: ver, reason: reason.trim().length >= 5 ? reason : `copy of ${code}` }),
      () => { void open(nc).then(() => setEditing(true)); });
  };
  const parity = () => act('', async () => {
    const [account_id, symbol] = src.split('|');
    const r = await api('calc/parity', 'POST', { code, version: ver, account_id, symbol, timeframe: tf, bars: nbars, parameters: over });
    setTimeout(() => setMsg(r.ok ? `Parity: equal to ${r.reference} on all ${r.bars} bars (largest difference ${r.max_difference}).`
      : `Not equal to ${r.reference}: largest difference ${r.max_difference} at bar ${r.at_bar}${r.warmup_mismatches ? `, ${r.warmup_mismatches} bars differ in warm-up` : ''}.`), 0);
    return r;
  });
  const bindTargets: string[] = bindings?.targets?.SIGNAL_DEFINITION ?? [];
  const [bindTarget, setBindTarget] = useState('');
  const bind = () => act(`${bindTarget} is now scored by ${code} v${ver}.`, () => api('calc/bindings', 'POST', { kind: 'SIGNAL_DEFINITION', target: bindTarget, code, version: ver, reason }));
  const unbind = (b: Row) => act(`${b.target} is back on its library formula.`, () => api(`calc/bindings/${b.id}?reason=${encodeURIComponent(reason.trim().length >= 5 ? reason : 'back to the library formula')}`, 'DELETE'));
  const createNew = async () => {
    if (!fresh) return;
    const out = OUTPUT_OF[fresh.purpose];
    const d: Definition = { purpose: fresh.purpose, nodes: [{ id: 'bars', type: 'source.bars', params: {}, position: [0, 80] }, { id: 'out', type: out, params: fresh.purpose === 'RISK_CHECK' ? { reason: 'describe the refusal' } : {}, position: [600, 80] }],
      edges: [], parameters: [], notes: '' };
    await act(`${fresh.code} created as a draft: add nodes from the palette and wire them to the output.`, async () => {
      // A new graph must pass the check, so it starts with the smallest complete graph of its purpose.
      const starter = starterFor(fresh.purpose, d);
      return api('calc/graphs', 'POST', { code: fresh.code, name: fresh.name, purpose: fresh.purpose, description: '', definition: starter, reason });
    }, () => { setFresh(null); void open(fresh.code).then(() => setEditing(true)); });
  };

  /* graph parameters */
  const gparams = def?.parameters ?? [];
  const setGParam = (i: number, k: string, v: unknown) => change(d => ({ ...d, parameters: d.parameters.map((p, j) => j === i ? { ...p, [k]: v } : p) }));

  /* rendering */
  const sel = def?.nodes.find(n => n.id === selected), selType = sel ? types[sel.type] : undefined, selTrace = sel ? trace[sel.id] : undefined;
  const score: (number | null)[] = preview?.outputs?.score ?? preview?.outputs?.value ?? preview?.outputs?.strength ?? [];
  const lastTarget = Array.isArray(preview?.outputs?.target) ? preview!.outputs!.target[preview!.outputs!.target.length - 1] : null;
  const validationErrors: Row[] = preview && !preview.valid ? preview.errors ?? [] : [];
  const nodeErrors = preview?.valid ? Object.entries(preview.errors ?? {}) : [];
  const myBindings: Row[] = (bindings?.items ?? []).filter((b: Row) => b.graph_code === code);
  const families = Object.keys(FAMILY_LABEL);
  const palette = (cat?.items ?? []).filter((n: NodeType) => (!q || `${n.label} ${n.type} ${n.doc}`.toLowerCase().includes(q.toLowerCase()))
    && (n.family !== 'outputs' || n.purpose === def?.purpose));

  return <section className="intel cg">
    {intro && <p className="cfg-note">{intro}</p>}
    <div className="intel-bar cg-bar">
      <SourcePick value={src} change={setSrc} label="Graph instrument" /><TfPick value={tf} change={setTf} label="Graph timeframe" />
      <label className="cg-inline">bars <input aria-label="Bars" type="number" min={30} max={5000} value={nbars} onChange={e => setNbars(Math.max(30, Math.min(5000, Number(e.target.value) || 300)))} /></label>
      {preview?.bars ? <label className="cg-inline cg-slider">bar <input aria-label="Trace bar" type="range" min={0} max={preview.bars - 1} value={preview.trace_bar ?? preview.bars - 1}
        onChange={e => setTraceBar(Number(e.target.value) === preview.bars! - 1 ? null : Number(e.target.value))} /><small>{preview.trace_time ? new Date(preview.trace_time).toISOString().slice(0, 16).replace('T', ' ') : ''}</small></label> : null}
      <button onClick={() => void run()} disabled={!src || !def}>{running ? 'Running…' : 'Run'}</button>
      <span className="cg-sp" />
      <button onClick={() => setWide(x => !x)} title="Hide the library and the inspector to give the canvas the whole width">{wide ? 'Show panels' : 'Wide canvas'}</button>
      {canvasAvailable() && <span className="cg-toggle" role="group" aria-label="Graph view">
        <button className={view === 'canvas' ? 'on' : ''} onClick={() => setView('canvas')}>Canvas</button><button className={view === 'plain' ? 'on' : ''} onClick={() => setView('plain')}>Plain</button></span>}
    </div>
    <div className="cg-status" role="status">{err || libErr ? <span className="cfg-error-text">{err || libErr}</span> : msg ? <span className="cg-ok">{msg}</span>
      : !src ? <span className="cfg-note">Choose an instrument to see every node's value on its stored bars.</span> : running ? <span className="cfg-note">Working out every node…</span>
      : <span className="cfg-note">{preview?.valid ? `Values on the bar ${preview.trace_time ? new Date(preview.trace_time).toISOString().slice(0, 16).replace('T', ' ') : ''} · ${preview.bars} bars · ${preview.ms} ms` : ' '}</span>}</div>
    <div className={`cg-main${wide ? ' wide' : ''}`}>
      <aside className="cg-left">
        {!editing ? <>
          <div className="cg-left-head"><b>Graphs</b>{(ctx.canOps || ctx.canRisk) && <button onClick={() => setFresh({ code: '', name: '', purpose: purposes?.[0] ?? 'SIGNAL' })}>+ New</button>}</div>
          {fresh && <div className="cg-new">
            <input aria-label="New graph code" placeholder="CODE" value={fresh.code} onChange={e => setFresh({ ...fresh, code: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '') })} />
            <input aria-label="New graph name" placeholder="Name" value={fresh.name} onChange={e => setFresh({ ...fresh, name: e.target.value })} />
            <select aria-label="New graph purpose" value={fresh.purpose} onChange={e => setFresh({ ...fresh, purpose: e.target.value })}>{Object.entries(PURPOSE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            <div className="cfg-actions"><button className="primary" disabled={fresh.code.length < 2 || !fresh.name || needReason} onClick={() => void createNew()} title={needReason ? 'Write a reason (top right) first' : ''}>Create draft</button>
              <button onClick={() => setFresh(null)}>Cancel</button></div>
            {needReason && <small className="cfg-note">Write a reason in the reason box first.</small>}</div>}
          <ul className="cg-lib">{graphs.map((g: Row) => { const last = g.versions?.[g.versions.length - 1];
            return <li key={g.code}><button className={g.code === code ? 'on' : ''} onClick={() => void open(g.code)} title={g.description}>
              <span><b>{g.name}</b><small>{g.code}</small></span>
              <span className="cg-tags">{g.builtin ? <small className="cg-tag">built-in</small> : null}{last && pill(`v${last.version} ${last.status.toLowerCase()}`, STATUS_TONE[last.status] ?? '')}
                {Number(g.bound) > 0 && <small className="cg-tag on">in use</small>}</span></button></li>; })}
            {lib && !graphs.length && <li className="cfg-note">No graph of this kind yet. + New starts one; built-ins of other stages arrive with their phase.</li>}</ul>
        </> : <>
          <div className="cg-left-head"><b>Add a node</b><button onClick={() => setEditing(false)}>Done</button></div>
          <input aria-label="Search nodes" placeholder="Search nodes…" value={q} onChange={e => setQ(e.target.value)} />
          <div className="cg-palette">{families.map(f => { const items = palette.filter((n: NodeType) => n.family === f); if (!items.length) return null;
            return <details key={f} open={Boolean(q) || f === 'sources' || f === 'maths'}><summary>{FAMILY_ICON[f]} {FAMILY_LABEL[f]} <small>{items.length}</small></summary>
              {items.map((n: NodeType) => <button key={n.type} className={`cg-pal fam-${f}`} onClick={() => addNode(n)} title={n.doc}><b>{n.label}</b><small>{n.formula}</small></button>)}</details>; })}</div>
        </>}
      </aside>

      <div className="cg-center">
        {version && <div className="cg-title">
          <b>{version.name}</b> <code>{code}</code> {pill(`v${ver} ${String(status).toLowerCase()}`, STATUS_TONE[status ?? ''] ?? '')}
          {isBuiltin && <small className="cg-tag">built-in · equal to {version.parity_ref ?? 'its code'}</small>}
          <label className="cg-inline">View graph revision <select aria-label="View graph revision" value={ver ?? ''} onChange={e => void open(code, Number(e.target.value))}>
            {(version.graph?.versions ?? []).map((v: Row) => <option key={v.version} value={v.version}>v{v.version} {v.status.toLowerCase()}{v.approved_by ? ` · ${v.approved_by}` : ''}</option>)}</select></label>
          <span className="cg-sp" />{dirty && <small className="cg-dirty">unsaved changes</small>}
          <small className="cfg-note">{def?.nodes.length ?? 0} nodes · {def?.edges.length ?? 0} wires{preview?.warmup != null ? ` · warm-up ${preview.warmup} bars` : ''}{preview?.ms != null ? ` · ${preview.ms} ms` : ''}</small>
        </div>}
        {def && view === 'canvas' ? <FlowCanvas label="Graph canvas" nodes={rfNodes} edges={rfEdges} nodeTypes={NODE_TYPES} height={wide ? 760 : 640}
            onNodesChange={c => onNodesChange(c as NodeChange<Node<NodeData>>[])} onConnect={onConnect} editable={editing} fitKey={`${code}|${ver}|${wide}`}
            onEdgesDelete={es => editing && change(d => ({ ...d, edges: d.edges.filter(e => !es.some(x => x.id === `${e.from}->${e.to}`)) }))}
            onPaneClick={() => setSelected(null)} />
          : def ? <PlainView def={def} types={types} trace={trace} select={setSelected} selected={selected} /> : <p className="cfg-note">Choose a graph.</p>}
        <div className="cg-result">
          {validationErrors.length > 0 && <div className="cg-errors" role="alert"><b>The graph has errors</b><ul>{validationErrors.map((e, i) => <li key={i}>{e.node ? <button className="linkish" onClick={() => setSelected(e.node)}>{e.node}</button> : null} {e.message}</li>)}</ul></div>}
          {nodeErrors.length > 0 && <div className="cg-errors"><b>Nodes that could not run</b><ul>{nodeErrors.map(([n, m]) => <li key={n}><button className="linkish" onClick={() => setSelected(n)}>{n}</button> {String(m)}</li>)}</ul></div>}
          {preview?.valid && preview.ok && <div className="cg-out">
            <span><b>{PURPOSE_LABEL[def?.purpose ?? '']}</b> on the last bar: <b className="cg-big">{lastTarget ? `${lastTarget} · strength ${fmt(score[score.length - 1])}` : fmt(Array.isArray(score) ? score[score.length - 1] : score)}</b></span>
            {Array.isArray(score) && score.length > 1 && <MiniSpark values={score as (number | null)[]} threshold={def?.purpose === 'SIGNAL' ? 20 : undefined} />}
            {preview.outputs && !Array.isArray(score) && <small>{Object.entries(preview.outputs).map(([k, v]) => `${k} ${fmt(v)}`).join(' · ')}</small>}
            <small className="cfg-note">{preview.bars} bars · run #{preview.run_id} recorded</small></div>}
        </div>
      </div>

      <aside className="cg-right">
        <div className="cg-actions">
          <input aria-label="Reason" placeholder="Reason (kept in the audit log)" value={reason} onChange={e => setReason(e.target.value)} />
          {isBuiltin && (ctx.canOps || ctx.canRisk) && <button onClick={copy}>Copy to change</button>}
          {src && (isBuiltin || /copy of /.test(String(version?.description ?? ''))) && <button onClick={() => void parity()} title="Compare with the code it draws, bar by bar, on this instrument">Check parity</button>}
          {!isBuiltin && editable && !editing && <button className="primary" onClick={() => setEditing(true)}>Edit</button>}
          {editing && <><button onClick={() => void check()}>Check</button><button className="primary" disabled={!dirty || needReason} onClick={() => void save()}>{status === 'DRAFT' ? 'Save draft' : 'Save as new draft'}</button></>}
          {!isBuiltin && status === 'DRAFT' && !dirty && <button disabled={needReason} onClick={() => void lifecycle('propose')}>Propose</button>}
          {!isBuiltin && status === 'PROPOSED' && ctx.canRisk && <><button className="primary" disabled={needReason} onClick={() => void lifecycle('approve')} title="Four-eyes: not the person who proposed it">Approve</button>
            <button disabled={needReason} onClick={() => void lifecycle('reject')}>Reject</button></>}
          {!isBuiltin && status === 'APPROVED' && ctx.canRisk && <button disabled={needReason} onClick={() => void lifecycle('retire')}>Retire</button>}
          {status === 'APPROVED' && def?.purpose === 'STRATEGY_RULE' && ctx.canOps && version && ver != null &&
            <button className="primary" disabled={dirty} onClick={() => setupStrategyFromRule({ code, version: ver, name: version.name })}>
              Use this rule in a strategy
            </button>}
          {status === 'APPROVED' && def?.purpose === 'SIGNAL' && ctx.canRisk && <div className="cg-bind"><select aria-label="Use for signal" value={bindTarget} onChange={e => setBindTarget(e.target.value)}>
            <option value="">Use for signal…</option>{bindTargets.map(t => <option key={t}>{t}</option>)}</select>
            <button disabled={!bindTarget || needReason} onClick={() => void bind()}>Put to use</button></div>}
          {myBindings.map(b => <div key={b.id} className="cg-bound"><small>Scores <b>{b.target}</b> · v{b.version} · since {String(b.created_at).slice(0, 10)}</small>
            {ctx.canRisk && <button onClick={() => void unbind(b)}>Stop</button>}</div>)}
          {needReason && (editing || status === 'DRAFT' || status === 'PROPOSED') && <small className="cfg-note">Saving, proposing and approving need a reason.</small>}
        </div>

        {gparams.length > 0 && <div className="cg-panel"><h4>Parameters</h4>
          {gparams.map((p, i) => <div key={p.name} className="cg-gparam">
            <span title={p.doc}><code>{p.name}</code>{p.doc && <small> {p.doc}</small>}</span>
            {editing ? <span className="cg-gparam-ed"><input aria-label={`${p.name} default`} value={p.default} onChange={e => setGParam(i, 'default', Number(e.target.value))} />
              <small>{p.min ?? ''}…{p.max ?? ''}</small></span>
              : <input aria-label={`${p.name} for this run`} type="number" step="any" placeholder={String(p.default)} value={over[p.name] ?? ''}
                onChange={e => setOver(o => { const n = { ...o }; if (e.target.value === '') delete n[p.name]; else n[p.name] = Number(e.target.value); return n; })} />}</div>)}
          {!editing && <small className="cfg-note">Change a value to try it on this run; the graph is not changed.</small>}</div>}
        {editing && <AddParam def={def} change={change} />}

        <div className="cg-panel">{sel ? <>
          <h4>{FAMILY_ICON[selType?.family ?? 'maths']} {sel.label || selType?.label} <small><code>{sel.id}</code> · {sel.type}</small></h4>
          <p className="cfg-note">{selType?.doc}</p>
          {selTrace?.explain && <div className="cg-explain"><small>On the bar {preview?.trace_time ? new Date(preview.trace_time).toISOString().slice(0, 16).replace('T', ' ') : ''}:</small><code>{selTrace.explain}</code></div>}
          {!selTrace?.explain && <code className="cg-explain">{selType?.formula}</code>}
          {selTrace?.error && <p className="cfg-error">{selTrace.error}</p>}
          {selTrace && <table className="cfg-table compact cg-io"><tbody>
            {Object.entries(selTrace.inputs ?? {}).map(([k, v]) => <tr key={'i' + k}><td>in</td><td><code>{k}</code></td><td>{fmt(v)}</td></tr>)}
            {Object.entries(selTrace.outputs ?? {}).filter(([k]) => k !== 'inputs').map(([k, v]) => <tr key={'o' + k}><td>out</td><td><code>{k}</code></td><td><b>{fmt(v)}</b></td></tr>)}
            <tr><td colSpan={3}><small className="cfg-note">{selTrace.ms} ms</small></td></tr></tbody></table>}
          {editing && <><label className="cg-param"><span>label</span><input aria-label="Node label" value={sel.label ?? ''} onChange={e => change(d => ({ ...d, nodes: d.nodes.map(n => n.id === sel.id ? { ...n, label: e.target.value } : n) }))} /></label></>}
          {(selType?.params ?? []).map(p => <ParamInput key={p.name} p={p} value={sel.params[p.name] ?? p.default} disabled={!editing} graphParams={gparams.map(x => x.name)}
            change={v => change(d => ({ ...d, nodes: d.nodes.map(n => n.id === sel.id ? { ...n, params: { ...n.params, [p.name]: v } } : n) }))} />)}
          <WiresOf def={def!} id={sel.id} editing={editing} change={change} />
          {editing && <button className="danger" onClick={() => removeNode(sel.id)}>Remove node</button>}
        </> : <>
          <h4>How it works</h4>
          <p className="cfg-note">{def?.notes || 'Click a node to see its formula with the numbers of the chosen bar.'}</p>
          <p className="cfg-note">Each box is one step; wires carry values left to right. Numbers on the ports are the values on the chosen bar (drag the bar slider to go back in time).
            {editing ? ' Drag from an output (right) to an input (left) to wire; Delete removes the selected node or wire.' : isBuiltin ? ' Built-ins are read-only: Copy to change.' : ''}</p>
        </>}</div>
      </aside>
    </div>
  </section>;
}

function starterFor(purpose: string, d: Definition): Definition {
  const out = d.nodes[1];
  const wire = (from: string, to: string) => ({ from, to });
  switch (purpose) {
    case 'SIGNAL': return { ...d, nodes: [d.nodes[0], { id: 'raw', type: 'math.expression', params: { inputs: 'c', expression: '0 * c' }, position: [300, 80], label: 'your formula' }, out],
      edges: [wire('bars.close', 'raw.c'), wire('raw.out', 'out.score')] };
    case 'FEATURE': case 'MEASURE': return { ...d, edges: [wire('bars.close', 'out.value')] };
    case 'STRATEGY_RULE': return { ...d, nodes: [d.nodes[0], { id: 'side', type: 'source.constant_side', params: { value: 'FLAT' }, position: [300, 80] }, out], edges: [wire('side.value', 'out.target')] };
    case 'RISK_CHECK': return { ...d, nodes: [d.nodes[0], { id: 'never', type: 'source.constant_bool', params: { value: false }, position: [300, 80] }, out], edges: [wire('never.value', 'out.refuse')] };
    case 'SIZING': return { ...d, nodes: [d.nodes[0], { id: 'qty', type: 'source.constant', params: { value: 0 }, position: [200, 80] },
      { id: 'dec', type: 'convert.to_decimal', params: { kind: 'quantity', places: 2, rounding: 'DOWN' }, position: [400, 80] }, out], edges: [wire('qty.value', 'dec.x'), wire('dec.out', 'out.quantity')] };
    case 'PROTECTIVE': return { ...d, nodes: [d.nodes[0], { id: 'last', type: 'convert.last_float', params: {}, position: [200, 80] },
      { id: 'dec', type: 'convert.to_decimal', params: { kind: 'price', places: 5 }, position: [400, 80] }, out], edges: [wire('bars.low', 'last.x'), wire('last.out', 'dec.x'), wire('dec.out', 'out.stop')] };
    case 'ALLOCATION': return { ...d, nodes: [{ id: 'w', type: 'source.constant', params: { value: 1 }, position: [200, 80] }, { ...out, params: { count: 2 } }], edges: [wire('w.value', 'out.in1'), wire('w.value', 'out.in2')] };
    default: return d;
  }
}

function AddParam({ def, change }: { def: Definition | null; change: (fn: (d: Definition) => Definition) => void }) {
  const [n, setN] = useState(''), [v, setV] = useState('1');
  if (!def) return null;
  return <div className="cg-panel"><h4>Add a parameter</h4><div className="cg-gparam">
    <input aria-label="Parameter name" placeholder="name" value={n} onChange={e => setN(e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, ''))} />
    <input aria-label="Parameter default" value={v} onChange={e => setV(e.target.value)} />
    <button disabled={!/^[a-z][a-z0-9_]*$/.test(n) || def.parameters.some(p => p.name === n) || Number.isNaN(Number(v))}
      onClick={() => { change(d => ({ ...d, parameters: [...d.parameters, { name: n, kind: Number.isInteger(Number(v)) ? 'int' : 'float', default: Number(v), doc: '' }] })); setN(''); }}>Add</button></div>
    <small className="cfg-note">Then pick it as {'{{name}}'} in a node's setting: one knob, changed per use without a new graph.</small></div>;
}

function WiresOf({ def, id, editing, change }: { def: Definition; id: string; editing: boolean; change: (fn: (d: Definition) => Definition) => void }) {
  const ws = def.edges.filter(e => e.from.startsWith(id + '.') || e.to.startsWith(id + '.'));
  if (!ws.length) return null;
  return <div className="cg-wires"><small className="cfg-note">Wires</small>{ws.map(e => <div key={e.from + e.to}><code>{e.from} → {e.to}</code>
    {editing && <button className="linkish" onClick={() => change(d => ({ ...d, edges: d.edges.filter(x => !(x.from === e.from && x.to === e.to)) }))}>remove</button>}</div>)}</div>;
}

/* The plain view: the same graph as a list in the order it runs — every step, its formula, inputs and value. */
function PlainView({ def, types, trace, select, selected }: { def: Definition; types: Record<string, NodeType>; trace: Trace; select: (id: string) => void; selected: string | null }) {
  const order = useMemo(() => {
    const deps: Record<string, string[]> = Object.fromEntries(def.nodes.map(n => [n.id, def.edges.filter(e => e.to.startsWith(n.id + '.')).map(e => e.from.split('.')[0])]));
    const done: string[] = [], seen = new Set<string>();
    const visit = (id: string) => { if (seen.has(id)) return; seen.add(id); (deps[id] ?? []).forEach(visit); done.push(id); };
    [...def.nodes].sort((a, b) => a.id.localeCompare(b.id)).forEach(n => visit(n.id));
    return done.map(id => def.nodes.find(n => n.id === id)!).filter(Boolean);
  }, [def]);
  return <div className="cfg-scroll"><table className="cfg-table compact intel-table cg-plain" aria-label="Graph steps"><thead><tr><th>#</th><th>Step</th><th>Formula with this bar's numbers</th><th>Inputs from</th><th>Value</th></tr></thead><tbody>
    {order.map((n, i) => { const nt = types[n.type], t = trace[n.id];
      const from = def.edges.filter(e => e.to.startsWith(n.id + '.')).map(e => `${e.to.split('.')[1]} ← ${e.from}`).join(', ');
      return <tr key={n.id} className={n.id === selected ? 'sel' : ''} onClick={() => select(n.id)}><td>{i + 1}</td>
        <td><b>{FAMILY_ICON[nt?.family ?? 'maths']} {n.label || nt?.label || n.type}</b><br /><small><code>{n.id}</code> · {n.type}</small></td>
        <td><code className="sig-eq">{t?.explain ?? nt?.formula}</code>{t?.error && <div className="cfg-error-text">{t.error}</div>}</td>
        <td><small>{from || '—'}</small></td>
        <td>{t?.outputs ? Object.entries(t.outputs).filter(([k]) => k !== 'inputs').slice(0, 4).map(([k, v]) => <div key={k}><small>{k}</small> <b>{fmt(v)}</b></div>) : '—'}</td></tr>; })}
  </tbody></table></div>;
}
