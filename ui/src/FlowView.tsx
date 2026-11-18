/* The Flow workspace (n8n-style): the whole platform as one workflow, every formula as its own graph, and the
 * executable bar-by-bar graphs — one place where every calculation, formula and equation is present.
 * Why: the owner wants every calculation visible as nodes and wires and followable from the market to the broker.
 * Purpose: FlowView — Platform flow (Overview), Every formula (Work), Bar-by-bar graphs (Work).
 * Must never: compute anything (the backend computes; these screens draw). */
import StageShell, { type Section } from './StageShell';
import PlatformFlow from './calc/PlatformFlow';
import { FormulaLibrary } from './calc/FormulaGraph';
import GraphWorkbench from './calc/GraphWorkbench';

export default function FlowView() {
  const sections: Section[] = [
    { id: 'flow', label: 'Platform flow', group: 'Overview', hint: 'The whole platform as one workflow: Market data → Features → Signals → Strategy → Risk → Portfolio → Execution → Post-trade, every calculation a node, live values on one instrument.', render: () => <PlatformFlow /> },
    { id: 'formulas', label: 'Every formula', group: 'Work', hint: 'Every calculation of every stage as its own graph: inputs → steps → result; type numbers into the inputs and see each step worked out.', render: () => <FormulaLibrary /> },
    { id: 'graphs', label: 'Bar-by-bar graphs', group: 'Work', hint: 'Executable graphs that run on stored bars: every node\'s value on any bar; copy, change, check, propose, approve, put to use.', render: ctx => <GraphWorkbench ctx={ctx} section="graphs" /> },
  ];
  return <StageShell view="Flow" sections={sections} title="Calculation flow" allPanels={false} />;
}
