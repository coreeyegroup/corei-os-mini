/* Every formula as an n8n graph: inputs → steps → result, worked out for the numbers you type.
 * Why: every calculation of every stage — not only the bar-series ones — must be present as nodes and wires and be
 * checkable with numbers.
 * Purpose: FormulaLibrary (every formula of every stage, searchable, grouped by stage) and FormulaCanvas (one formula:
 * input nodes you can type into, a node per step showing its expression, the expression with the numbers put in and
 * its value; the result highlighted). Links to the executable bar-by-bar graph where one exists.
 * Must never: compute anything itself — the server works out every step (POST /api/calc/formulas/{id}/evaluate). */
import { useEffect, useMemo, useRef, useState } from 'react';
import { Handle, Position, type Edge, type Node, type NodeProps } from '@xyflow/react';
import './graph.css';
import { api } from '../Configuration';
import { goto } from '../nav';
import { useData, type Row } from '../StageShell';
import FlowCanvas from './FlowCanvas';
import { fmt, openGraph } from './ports';

export type Formula = { id: string; name: string; stage: string; inputs: Row[]; steps: Row[]; feeds: string[]; graph: string | null; code: string; note: string };
const PICK = 'corei.formula.open';
export function openFormula(id: string) {
  try { sessionStorage.setItem(PICK, id); } catch { /* per-viewer convenience */ }
  window.dispatchEvent(new CustomEvent('corei-open-formula', { detail: id }));
}

type InData = { f: Row; value: unknown; set: (v: unknown) => void };
function InputNode({ data }: NodeProps<Node<InData>>) {
  const { f, value, set } = data;
  return <div className="cg-node fam-sources fg-in" title={f.doc}>
    <div className="cg-head"><span className="cg-ico" aria-hidden>◉</span><b>{f.name}</b><small>input</small></div>
    <div className="fg-in-body">{f.kind === 'bool'
      ? <label className="nodrag"><input type="checkbox" aria-label={f.name} checked={Boolean(value)} onChange={e => set(e.target.checked)} /> {value ? 'true' : 'false'}</label>
      : <input className="nodrag" aria-label={f.name} inputMode="decimal" value={String(value ?? '')} onChange={e => set(e.target.value)} />}
      <small>{f.doc}</small></div>
    <Handle type="source" position={Position.Right} id="out" className="cg-h t-float" />
  </div>;
}

type StepData = { s: Row; value: unknown; worked?: string; result: boolean };
function StepNode({ data }: NodeProps<Node<StepData>>) {
  const { s, value, worked, result } = data;
  return <div className={`cg-node fam-${result ? 'outputs' : s.kind === 'bool' ? 'logic' : 'maths'} fg-step${result ? ' fg-result' : ''}`}>
    <Handle type="target" position={Position.Left} id="in" className="cg-h t-float" />
    <div className="cg-head"><span className="cg-ico" aria-hidden>{result ? '◎' : s.kind === 'bool' ? '⋀' : 'ƒ'}</span><b>{s.label}</b><small>{s.name}</small></div>
    <div className="fg-expr"><code>{s.name} = {s.expr}</code>{worked && <code className="fg-worked">= {worked}</code>}</div>
    <div className="fg-value">{fmt(value)}</div>
    <Handle type="source" position={Position.Right} id="out" className="cg-h t-float" />
  </div>;
}
const TYPES = { fin: InputNode, fstep: StepNode };

export function FormulaCanvas({ f, height = 560 }: { f: Formula; height?: number }) {
  const [vals, setVals] = useState<Record<string, unknown>>({}), [res, setRes] = useState<Row | null>(null), [err, setErr] = useState('');
  useEffect(() => { setVals(Object.fromEntries(f.inputs.map(i => [i.name, i.example]))); setRes(null); }, [f.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const seq = useRef(0);
  useEffect(() => {
    if (!Object.keys(vals).length) return;
    const n = ++seq.current;
    const t = setTimeout(() => {
      const values = Object.fromEntries(f.inputs.map(i => [i.name, i.kind === 'bool' ? Boolean(vals[i.name]) : Number(vals[i.name])]).filter(([, v]) => typeof v === 'boolean' || Number.isFinite(v)));
      void api(`calc/formulas/${f.id}/evaluate`, 'POST', { values }).then(r => { if (n === seq.current) { setRes(r); setErr(''); } })
        .catch(x => { if (n === seq.current) setErr((x as Error).message); });
    }, 200);
    return () => clearTimeout(t);
  }, [vals, f]);
  // Layout: inputs in the first column; each step one column right of the deepest name it uses.
  const { nodes, edges } = useMemo(() => {
    const depth: Record<string, number> = Object.fromEntries(f.inputs.map(i => [i.name, 0]));
    const rows: Record<number, number> = {};
    const ns: Node[] = [], es: Edge[] = [];
    f.inputs.forEach((i, k) => { ns.push({ id: `i:${i.name}`, type: 'fin', position: { x: 0, y: k * 96 }, data: { f: i, value: vals[i.name], set: (v: unknown) => setVals(o => ({ ...o, [i.name]: v })) } }); });
    f.steps.forEach((s, k) => {
      const d = 1 + Math.max(0, ...(s.uses ?? []).map((u: string) => depth[u] ?? 0));
      depth[s.name] = d;
      const r = rows[d] = (rows[d] ?? -1) + 1;
      ns.push({ id: `s:${s.name}`, type: 'fstep', position: { x: d * 290, y: r * 132 }, data: { s, value: res?.steps?.[s.name], worked: res?.worked?.[s.name], result: k === f.steps.length - 1 } });
      for (const u of s.uses ?? []) {
        const from = f.inputs.some(i => i.name === u) ? `i:${u}` : `s:${u}`;
        es.push({ id: `${from}->${s.name}`, source: from, target: `s:${s.name}`, sourceHandle: 'out', targetHandle: 'in', className: s.kind === 'bool' ? 'cg-edge t-seriesbool' : 'cg-edge' });
      }
    });
    return { nodes: ns, edges: es };
  }, [f, vals, res]);
  return <div className="fg">
    <div className="fg-title"><b>{f.name}</b> <small className="cfg-note">{f.stage} · {f.code}</small>
      {f.graph && <button className="linkish" onClick={() => { openGraph(f.graph!); goto('Flow', 'graphs'); }}>open the bar-by-bar graph ↗</button>}
      <button className="linkish" onClick={() => setVals(Object.fromEntries(f.inputs.map(i => [i.name, i.example])))}>reset the example</button></div>
    {f.note && <p className="cfg-note">{f.note}</p>}
    <div className="cg-status">{err ? <span className="cfg-error-text">{err}</span> : <span className="cfg-note">Type into the input boxes; every step is worked out again. Result: <b>{f.steps[f.steps.length - 1]?.label} = {fmt(res?.steps?.[f.steps[f.steps.length - 1]?.name])}</b></span>}</div>
    <FlowCanvas label={`Formula ${f.name}`} nodes={nodes} edges={edges} nodeTypes={TYPES} height={height} fitKey={f.id} minimap={false}>
      <FormulaPlain f={f} res={res} />
    </FlowCanvas>
  </div>;
}

/* The same formula as a list (used where no canvas can be drawn, and printed beside it for reading). */
export function FormulaPlain({ f, res }: { f: Formula; res: Row | null }) {
  return <table className="cfg-table compact intel-table fg-plain" aria-label={`Steps of ${f.name}`}><tbody>
    {f.inputs.map(i => <tr key={'i' + i.name}><td>input</td><td><code>{i.name}</code></td><td>{fmt(res?.inputs?.[i.name] ?? i.example)}</td><td><small>{i.doc}</small></td></tr>)}
    {f.steps.map(s => <tr key={'s' + s.name}><td>step</td><td><code>{s.name} = {s.expr}</code>{res?.worked?.[s.name] && <><br /><small>= {res.worked[s.name]}</small></>}</td><td><b>{fmt(res?.steps?.[s.name])}</b></td><td><small>{s.label}</small></td></tr>)}
  </tbody></table>;
}

export function FormulaLibrary({ stage }: { stage?: string }) {
  const [data, error] = useData<Row>('calc/formulas', 0);
  const [q, setQ] = useState(''), [pick, setPick] = useState<string>(() => { try { return sessionStorage.getItem(PICK) ?? ''; } catch { return ''; } });
  useEffect(() => { const on = (e: Event) => setPick((e as CustomEvent).detail); window.addEventListener('corei-open-formula', on); return () => window.removeEventListener('corei-open-formula', on); }, []);
  const stages: string[] = stage ? [stage] : data?.stages ?? [];
  const all: Formula[] = stages.flatMap(s => data?.items?.[s] ?? []);
  const shown = all.filter(f => !q || `${f.name} ${f.steps.map(s => s.expr + ' ' + s.label).join(' ')}`.toLowerCase().includes(q.toLowerCase()));
  const f = all.find(x => x.id === pick) ?? shown[0];
  return <section className="intel fg-lib">
    <div className="intel-bar"><input aria-label="Search formulas" placeholder={`Search ${all.length} formulas…`} value={q} onChange={e => setQ(e.target.value)} />
      <small className="cfg-note">Every calculation {stage ? `of ${stage}` : 'of every stage'} as nodes and wires; click one, type numbers into its inputs.</small></div>
    {error && <p className="cfg-error">{error}</p>}
    <div className="fg-main">
      <aside className="cg-left fg-list">{stages.map(s => { const items = shown.filter(x => x.stage === s); if (!items.length) return null;
        return <div key={s}><div className="fg-stage">{s} <small>{items.length}</small></div>
          {items.map(x => <button key={x.id} className={x.id === f?.id ? 'on' : ''} onClick={() => setPick(x.id)} title={x.steps.map(st => `${st.name} = ${st.expr}`).join('\n')}>
            <span>{x.name}</span>{x.graph && <small className="cg-tag" title="Also runs bar by bar">graph</small>}</button>)}</div>; })}</aside>
      <div className="fg-canvas">{f ? <FormulaCanvas f={f} /> : <p className="cfg-note">{data ? 'No formula matches.' : 'Loading…'}</p>}</div>
    </div>
  </section>;
}
