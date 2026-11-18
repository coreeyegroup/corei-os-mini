/* The platform as one n8n workflow: Market data → Features → Signals → Strategy → Risk → Portfolio → Execution →
 * Post-trade, each stage a group holding every calculation it makes, wired where one calculation feeds another.
 * Why: the owner wants one place where every calculation, formula and equation of the platform is present and its path
 * from the market to the broker can be followed.
 * Purpose: PlatformFlow — stage groups (expand / collapse each, or all), a node per calculation with its result and
 * equation, live values for one instrument (features and signal scores on its newest stored bar), search that lights
 * up matching nodes, and a side panel for the selected calculation (its steps, where it comes from and goes to, try it
 * with numbers, open its bar-by-bar graph).
 * Must never: compute anything itself (formulas come from /api/calc/flow and /api/calc/formulas; live values from
 * /api/calc/flow/live). */
import { useEffect, useMemo, useState } from 'react';
import { Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react';
import './graph.css';
import { api } from '../Configuration';
import { goto } from '../nav';
import { useData, type Row } from '../StageShell';
import { SourcePick, TfPick } from '../SignalLibrary';
import FlowCanvas from './FlowCanvas';
import { FormulaCanvas, openFormula, type Formula } from './FormulaGraph';
import { fmt, openGraph } from './ports';

const COLS = 2, NODE_W = 220, NODE_H = 74, GAP = 12, PAD = 16, HEAD = 40, STAGE_GAP = 70;
const STAGE_ICON: Record<string, string> = { 'Market data': '◉', Intelligence: '∿', Signals: '⚑', Strategies: '♟', Risk: '⛨', Portfolio: '▦', Execution: '➜', 'Post-trade': '✓' };

type GroupData = { name: string; no: number; count: number; open: boolean; toggle: () => void; hits: number };
function StageGroup({ data }: NodeProps<Node<GroupData>>) {
  return <div className={`pf-group st-${data.no}${data.open ? '' : ' closed'}`}>
    <Handle type="target" position={Position.Left} id="in" className="cg-h" />
    <div className="pf-group-head" onDoubleClick={data.toggle}><span className="pf-no">{data.no}</span><b>{STAGE_ICON[data.name]} {data.name}</b>
      <small>{data.count} calculations{data.hits ? ` · ${data.hits} match` : ''}</small>
      <button className="nodrag linkish" onClick={data.toggle}>{data.open ? 'collapse' : 'expand'}</button></div>
    <Handle type="source" position={Position.Right} id="out" className="cg-h" />
  </div>;
}

type CalcData = { n: Row; live?: Row; hit: boolean; dim: boolean; sel: boolean };
function CalcNodeView({ data }: NodeProps<Node<CalcData>>) {
  const { n, live, hit, dim, sel } = data;
  return <div className={`pf-calc${hit ? ' hit' : ''}${dim ? ' dim' : ''}${sel ? ' sel' : ''}`} title={n.equation}>
    <Handle type="target" position={Position.Left} id="in" className="cg-h" />
    <div className="pf-calc-head"><b>{n.name}</b>{n.graph && <small className="cg-tag" title="Also runs as a bar-by-bar graph">graph</small>}</div>
    <div className="pf-calc-eq"><code>{n.equation}</code></div>
    {live && live.value != null && <div className="pf-live" title={live.label}>{live.label}: <b>{fmt(live.value)}</b></div>}
    <Handle type="source" position={Position.Right} id="out" className="cg-h" />
  </div>;
}
const TYPES = { stage: StageGroup, calc: CalcNodeView };

export default function PlatformFlow() {
  const [flow, error] = useData<Row>('calc/flow', 0);
  const [cat] = useData<Row>('calc/formulas', 0);
  const [open, setOpen] = useState<Record<string, boolean>>(() => { try { return JSON.parse(localStorage.getItem('corei.flow.open') ?? '{}'); } catch { return {}; } });
  const [q, setQ] = useState(''), [sel, setSel] = useState<string | null>(null), [src, setSrc] = useState(''), [tf, setTf] = useState('H1'), [live, setLive] = useState<Row | null>(null), [liveErr, setLiveErr] = useState('');
  const [sources] = useData<Row>('trading/sources', 0);
  useEffect(() => { const it: Row[] = sources?.items ?? []; if (!src && it.length) setSrc(`${it[0].account_id}|${it[0].broker_symbol}`); }, [sources]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!src) return;
    const [a, s] = src.split('|');
    let on = true;
    const load = () => void api(`calc/flow/live?account_id=${a}&symbol=${encodeURIComponent(s)}&timeframe=${tf}`).then(d => { if (on) { setLive(d); setLiveErr(''); } })
      .catch(x => { if (on) { setLive(null); setLiveErr((x as Error).message); } });
    load();
    const t = setInterval(load, 60_000);
    return () => { on = false; clearInterval(t); };
  }, [src, tf]);
  const isOpen = (s: string) => open[s] !== false;
  const toggle = (s: string) => setOpen(o => { const n = { ...o, [s]: !(o[s] !== false) }; try { localStorage.setItem('corei.flow.open', JSON.stringify(n)); } catch { /* convenience */ } return n; });
  const setAll = (v: boolean) => { const n = Object.fromEntries((flow?.stages ?? []).map((s: Row) => [s.name, v])); setOpen(n); try { localStorage.setItem('corei.flow.open', JSON.stringify(n)); } catch { /* convenience */ } };
  const ql = q.trim().toLowerCase();
  const matches = (n: Row) => Boolean(ql) && `${n.name} ${n.equation} ${n.code}`.toLowerCase().includes(ql);

  const { nodes, edges } = useMemo(() => {
    const ns: Node[] = [], es: Edge[] = [];
    if (!flow) return { nodes: ns, edges: es };
    let x = 0;
    const where: Record<string, string> = {};
    for (const st of flow.stages as Row[]) {
      const items: Row[] = flow.nodes.filter((n: Row) => n.stage === st.name);
      const o = isOpen(st.name);
      const rows = Math.ceil(items.length / COLS);
      const w = o ? PAD * 2 + COLS * NODE_W + (COLS - 1) * GAP : 240, h = o ? HEAD + PAD + rows * (NODE_H + GAP) : 70;
      const gid = `stage:${st.name}`;
      ns.push({ id: gid, type: 'stage', position: { x, y: 0 }, style: { width: w, height: h }, draggable: false, selectable: false,
        data: { name: st.name, no: st.no, count: st.count, open: o, toggle: () => toggle(st.name), hits: items.filter(matches).length } });
      items.forEach((n, i) => {
        where[n.id] = o ? n.id : gid;
        if (!o) return;
        ns.push({ id: n.id, type: 'calc', parentId: gid, extent: 'parent', draggable: false,
          position: { x: PAD + (i % COLS) * (NODE_W + GAP), y: HEAD + Math.floor(i / COLS) * (NODE_H + GAP) }, style: { width: NODE_W },
          data: { n, live: live?.values?.[n.id], hit: matches(n), dim: Boolean(ql) && !matches(n), sel: sel === n.id } });
      });
      x += w + STAGE_GAP;
    }
    const seen = new Set<string>();
    (flow.stages as Row[]).slice(1).forEach((st, i) => { const a = `stage:${flow.stages[i].name}`, b = `stage:${st.name}`;
      es.push({ id: `${a}->${b}`, source: a, target: b, sourceHandle: 'out', targetHandle: 'in', className: 'pf-stage-edge' }); });
    for (const e of flow.edges as Row[]) {
      const a = where[e.from], b = where[e.to];
      if (!a || !b || a === b) continue;
      const id = `${a}->${b}`;
      if (seen.has(id)) continue;
      seen.add(id);
      const on = sel && (e.from === sel || e.to === sel);
      es.push({ id, source: a, target: b, sourceHandle: 'out', targetHandle: 'in', className: `pf-edge${on ? ' on' : ''}${sel && !on ? ' faint' : ''}` });
    }
    return { nodes: ns, edges: es };
  }, [flow, open, live, ql, sel]); // eslint-disable-line react-hooks/exhaustive-deps

  const formula: Formula | undefined = sel ? (Object.values(cat?.items ?? {}) as Formula[][]).flat().find(f => f.id === sel) : undefined;
  const fnode: Row | undefined = flow?.nodes?.find((n: Row) => n.id === sel);
  const from = sel ? (flow?.edges ?? []).filter((e: Row) => e.to === sel).map((e: Row) => flow!.nodes.find((n: Row) => n.id === e.from)) : [];
  const to = sel ? (flow?.edges ?? []).filter((e: Row) => e.from === sel).map((e: Row) => flow!.nodes.find((n: Row) => n.id === e.to)) : [];
  const total = flow?.nodes?.length ?? 0;
  return <section className="intel pf">
    <div className="intel-bar">
      <input aria-label="Search the flow" placeholder={`Search ${total} calculations…`} value={q} onChange={e => setQ(e.target.value)} />
      <SourcePick value={src} change={setSrc} label="Flow instrument" /><TfPick value={tf} change={setTf} label="Flow timeframe" />
      <button onClick={() => setAll(true)}>Expand all</button><button onClick={() => setAll(false)}>Collapse all</button>
    </div>
    <div className="cg-status">{error || liveErr ? <span className="cfg-error-text">{error || liveErr}</span>
      : <span className="cfg-note">{total} calculations in 8 stages. {live?.bar_time ? `Live values on the ${tf} bar of ${new Date(live.bar_time).toISOString().slice(0, 16).replace('T', ' ')} (close ${fmt(Number(live.close))}).` : 'Choose an instrument for live values.'} Click a calculation for its steps; double-click a stage to fold it.</span>}</div>
    <div className="pf-main">
      <FlowCanvas label="Platform flow" nodes={nodes} edges={edges} nodeTypes={TYPES} height={700} fitKey={`flow|${Object.values(open).join()}`}
        fitNodes={(flow?.stages ?? []).slice(0, 3).map((s: Row) => `stage:${s.name}`)}
        onNodeClick={id => { if (!id.startsWith('stage:')) setSel(id); }} onNodeDoubleClick={id => { if (id.startsWith('stage:')) toggle(id.slice(6)); }} onPaneClick={() => setSel(null)}>
        <PlainFlow flow={flow} live={live} pick={setSel} />
      </FlowCanvas>
      <aside className="cg-right pf-side">{fnode ? <>
        <h4>{fnode.name}</h4>
        <small className="cfg-note">{fnode.stage} · {fnode.code}</small>
        {formula && <ol className="pf-steps">{formula.steps.map(s => <li key={s.name}><code>{s.name} = {s.expr}</code><small> {s.label}</small></li>)}</ol>}
        {live?.values?.[fnode.id] && <p>Now ({live.values[fnode.id].label}): <b>{fmt(live.values[fnode.id].value)}</b></p>}
        {from.length > 0 && <p className="cfg-note">From: {from.filter(Boolean).map((n: Row) => <button key={n.id} className="linkish" onClick={() => setSel(n.id)}>{n.name}</button>)}</p>}
        {to.length > 0 && <p className="cfg-note">Feeds: {to.filter(Boolean).map((n: Row) => <button key={n.id} className="linkish" onClick={() => setSel(n.id)}>{n.name}</button>)}</p>}
        <div className="cfg-actions"><button className="primary" onClick={() => { openFormula(fnode.id); goto('Flow', 'formulas'); }}>Try it with numbers</button>
          {fnode.graph && <button onClick={() => { openGraph(fnode.graph); goto('Flow', 'graphs'); }}>Bar-by-bar graph</button>}</div>
        {formula && <div className="pf-mini"><FormulaCanvas f={formula} height={300} /></div>}
      </> : <>
        <h4>How to read it</h4>
        <p className="cfg-note">Left to right is the path from the market to the broker: each box is a stage, each card inside one calculation of it, with its last steps written out. Lines show where one calculation feeds another.</p>
        <p className="cfg-note">Cards marked <span className="cg-tag">graph</span> also run as a bar-by-bar graph with every node's value on any bar.</p>
      </>}</aside>
    </div>
  </section>;
}

/* The same flow as a list, in order (used where no canvas can be drawn). */
function PlainFlow({ flow, live, pick }: { flow: Row | null; live: Row | null; pick: (id: string) => void }) {
  if (!flow) return <>Loading…</>;
  return <div className="pf-plain">{(flow.stages as Row[]).map(st => <div key={st.name}><h5>{st.no} · {st.name}</h5>
    <ul>{flow.nodes.filter((n: Row) => n.stage === st.name).map((n: Row) => <li key={n.id}><button className="linkish" onClick={() => pick(n.id)}>{n.name}</button>
      {live?.values?.[n.id]?.value != null && <b> {fmt(live.values[n.id].value)}</b>} <small><code>{n.equation}</code></small></li>)}</ul></div>)}</div>;
}
