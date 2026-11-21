/* Calculation Graphs in the browser: the shapes of the node catalogue and graphs, the ports of a node for its
 * parameters (mirroring calc/nodes on the server for the few nodes whose ports follow their parameters), and the
 * wiring rule.
 * Why: the editor shows the right handles the moment a parameter changes and refuses an impossible wire at once; the
 * server still checks everything again (POST /api/calc/validate) before anything is saved or run.
 * Must never: be the authority — the server's check decides. */

export type Port = { name: string; type: string; doc?: string; optional?: boolean };
export type Param = { name: string; kind: string; default: unknown; min?: number; max?: number; choices?: string[]; doc?: string };
export type NodeType = { type: string; family: string; label: string; doc: string; inputs: Port[]; outputs: Port[]; params: Param[]; dynamic_ports: boolean; formula: string; purpose?: string };
export type GNode = { id: string; type: string; params: Record<string, unknown>; position: [number, number]; label?: string; note?: string };
export type GEdge = { from: string; to: string };
export type GParam = { name: string; kind: 'int' | 'float'; default: number; min?: number | null; max?: number | null; doc?: string };
export type Definition = { purpose: string; nodes: GNode[]; edges: GEdge[]; parameters: GParam[]; notes: string };

const SERIES: Record<string, string> = { float: 'series<float>', bool: 'series<bool>', side: 'series<side>' };
export const compatible = (src: string, dst: string) => src === dst || SERIES[src] === dst;
export const isSeries = (t: string) => t.startsWith('series<');

const names = (v: unknown) => String(v ?? '').split(',').map(s => s.trim()).filter(Boolean);
const count = (p: Record<string, unknown>, d = 2) => Math.max(1, Math.min(8, Number(p.count ?? d) || d));
const ins = (n: number, t = 'series<float>') => Array.from({ length: n }, (_, i) => ({ name: `in${i + 1}`, type: t }));

/** The ports of a node with these parameters (graph-parameter references count as their defaults). */
export function portsOf(nt: NodeType | undefined, params: Record<string, unknown>): { inputs: Port[]; outputs: Port[] } {
  if (!nt) return { inputs: [], outputs: [] };
  const p = { ...Object.fromEntries(nt.params.map(x => [x.name, x.default])), ...params };
  switch (nt.type) {
    case 'math.expression': return { inputs: (names(p.inputs).length ? names(p.inputs) : ['a']).map(n => { const [name, kind] = n.split(':').map(x => x.trim());
        return { name, type: kind === 'bool' ? 'series<bool>' : 'series<float>' }; }),
      outputs: [{ name: 'out', type: p.output === 'true/false' ? 'series<bool>' : 'series<float>' }] };
    case 'combine.weighted_sum': case 'combine.vote': case 'combine.consensus':
      return { inputs: ins(count(p)), outputs: [{ name: 'out', type: 'series<float>' }] };
    case 'combine.rank': return { inputs: ins(count(p)), outputs: ins(count(p)).map((x, i) => ({ ...x, name: `rank${i + 1}` })) };
    case 'output.signal': return { inputs: [{ name: 'score', type: 'series<float>' }, ...names(p.show).map(n => ({ name: n, type: 'series<float>', optional: true }))], outputs: [] };
    case 'output.allocation': return { inputs: ins(count(p), 'float'), outputs: [] };
    case 'convert.to_decimal': return { inputs: [{ name: 'x', type: 'float' }], outputs: [{ name: 'out', type: `decimal:${p.kind ?? 'price'}` }] };
    default: return { inputs: nt.inputs, outputs: nt.outputs };
  }
}

export const FAMILY_LABEL: Record<string, string> = { sources: 'Sources', indicators: 'Indicators', series: 'Series', maths: 'Maths', logic: 'Logic', combine: 'Combine', convert: 'Convert', outputs: 'Outputs' };
export const FAMILY_ICON: Record<string, string> = { sources: '◉', indicators: '∿', series: '⇢', maths: 'ƒ', logic: '⋀', combine: 'Σ', convert: '⇄', outputs: '◎' };
export const PURPOSE_LABEL: Record<string, string> = { FEATURE: 'Feature', SIGNAL: 'Signal', STRATEGY_RULE: 'Strategy rule', SIZING: 'Sizing', PROTECTIVE: 'Stop / target',
  RISK_CHECK: 'Risk check', ALLOCATION: 'Allocation', MEASURE: 'Measure' };
export const OUTPUT_OF: Record<string, string> = { FEATURE: 'output.feature', SIGNAL: 'output.signal', STRATEGY_RULE: 'output.rule', SIZING: 'output.sizing',
  PROTECTIVE: 'output.protective', RISK_CHECK: 'output.risk_check', ALLOCATION: 'output.allocation', MEASURE: 'output.measure' };

export function fmt(v: unknown): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return Math.abs(v) >= 1e6 || (Math.abs(v) < 1e-4 && v !== 0) ? v.toExponential(3) : String(Number(v.toPrecision(6)));
  if (typeof v === 'boolean') return v ? 'true' : 'false';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

export function newId(def: Definition, base: string): string {
  const stem = base.replace(/[^A-Za-z0-9]/g, '').replace(/^[^A-Za-z]+/, '') || 'n';
  let i = 1;
  while (def.nodes.some(n => n.id === `${stem}${i}`)) i++;
  return `${stem}${i}`.slice(0, 40);
}

const PENDING = 'corei.calc.open';
type GraphSelection = { code: string; version?: number };

export function takeGraphSelection(strategyWorkbench: boolean): GraphSelection | null {
  try {
    const saved = sessionStorage.getItem(PENDING);
    if (!saved) return null;
    const selection: GraphSelection = saved.startsWith('{') ? JSON.parse(saved) : { code: saved };
    if (selection.version != null && !strategyWorkbench) return null;
    sessionStorage.removeItem(PENDING);
    return selection;
  } catch { return null; }
}

/** Open a graph in the workbench from another screen. */
export function openGraph(code: string, version?: number) {
  const detail = { code, version };
  try { sessionStorage.setItem(PENDING, JSON.stringify(detail)); } catch { /* private mode: the event still carries it */ }
  window.dispatchEvent(new CustomEvent('corei-open-graph', { detail }));
}
