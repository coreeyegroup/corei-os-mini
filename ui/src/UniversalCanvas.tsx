import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Handle, Position,
  type Connection, type Edge, type Node, type NodeProps,
} from '@xyflow/react';
import FlowCanvas from './calc/FlowCanvas';

type WorkflowNode = { id: string; type: string; params?: Record<string, number | string>; position?: [number, number] | null };
type WorkflowEdge = { source: string; source_port: string; target: string; target_port: string };
type Workflow = { schema_version: string; name: string; nodes: WorkflowNode[]; edges: WorkflowEdge[] };
export type NodeSpec = {
  type: string; inputs: Record<string, string>; outputs: Record<string, string>;
  parameters: Record<string, [number, number] | string[]>;
};
type Point = { x: number; y: number };
type BlockData = { id: string; type: string; inputs: string[]; outputs: string[] };
type Props = { draft: string; onChange: (text: string) => void; catalog: NodeSpec[]; onOpenJson: () => void; onRefreshCatalog: () => void };

export function readWorkflow(draft: string): Workflow | null {
  try {
    const value = JSON.parse(draft);
    if (!Array.isArray(value?.nodes) || !Array.isArray(value?.edges)) return null;
    if (typeof value.name !== 'string') return null;
    if (!value.nodes.every((node: WorkflowNode) => typeof node?.id === 'string' && typeof node?.type === 'string'
      && (node.position == null || (Array.isArray(node.position) && node.position.length === 2 && node.position.every(Number.isFinite)))
      && (node.params === undefined || (node.params && typeof node.params === 'object' && !Array.isArray(node.params)
        && Object.values(node.params).every(param => typeof param === 'string' || typeof param === 'number'))))) return null;
    if (!value.edges.every((edge: WorkflowEdge) => edge && typeof edge.source === 'string'
      && typeof edge.target === 'string' && typeof edge.source_port === 'string' && typeof edge.target_port === 'string')) return null;
    if (new Set(value.nodes.map((node: WorkflowNode) => node.id)).size !== value.nodes.length) return null;
    return value;
  } catch {
    return null;
  }
}

function Block({ data, selected }: NodeProps<Node<BlockData>>) {
  return <div className="studio-node" data-selected={selected}>
    <header><strong>{data.id}</strong><small>{data.type}</small></header>
    <div className="studio-ports">
      <div>{data.inputs.map(port => <div key={port}>
        <Handle type="target" position={Position.Left} id={port} />{port}
      </div>)}</div>
      <div>{data.outputs.map(port => <div key={port}>
        {port}<Handle type="source" position={Position.Right} id={port} />
      </div>)}</div>
    </div>
  </div>;
}
const nodeTypes = { universal: Block };

function arrange(workflow: Workflow): Map<string, Point> {
  const dependencies = new Map(workflow.nodes.map(node => [node.id, new Set<string>()]));
  for (const edge of workflow.edges) {
    if (dependencies.has(edge.source)) dependencies.get(edge.target)?.add(edge.source);
  }
  const positions = new Map<string, Point>();
  let column = 0;
  let remaining = workflow.nodes;
  while (remaining.length) {
    const ready = remaining.filter(node => [...(dependencies.get(node.id) ?? [])].every(id => positions.has(id)));
    const layer = ready.length ? ready : remaining;
    const ids = new Set(layer.map(node => node.id));
    layer.forEach((node, index) => positions.set(node.id, { x: column * 245, y: index * 155 }));
    remaining = remaining.filter(node => !ids.has(node.id));
    column++;
  }
  return positions;
}

export default function UniversalCanvas({ draft, onChange, catalog, onOpenJson, onRefreshCatalog }: Props) {
  const [error, setError] = useState('');
  const [showCatalog, setShowCatalog] = useState(false);
  const [query, setQuery] = useState('');
  const catalogButton = useRef<HTMLButtonElement>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (showCatalog) searchInput.current?.focus();
  }, [showCatalog]);
  const [activeId, setActiveId] = useState('');
  const [dragPositions, setDragPositions] = useState<Record<string, Point>>({});
  const [measurements, setMeasurements] = useState<Record<string, { width: number; height: number }>>({});
  const [layoutRevision, setLayoutRevision] = useState(0);
  const workflow = useMemo(() => readWorkflow(draft), [draft]);
  const layout = useMemo(() => workflow ? arrange(workflow) : new Map<string, Point>(), [workflow]);
  const lookup = useMemo(() => new Map(catalog.map(spec => [spec.type, spec])), [catalog]);

  if (!workflow) return <div className="studio-empty">
    <p role="alert" className="cfg-error-text">The canvas needs workflow JSON with unique node IDs and well-formed nodes and edges.</p>
    <button onClick={onOpenJson}>Open JSON editor</button>
  </div>;

  const active = workflow.nodes.find(node => node.id === activeId);
  const activeSpec = active && lookup.get(active.type);
  const matches = catalog.filter(spec => spec.type.toLowerCase().includes(query.trim().toLowerCase()));
  const nodes: Node<BlockData>[] = workflow.nodes.map(node => ({
    id: node.id, type: 'universal', position: dragPositions[node.id]
      ?? (node.position ? { x: node.position[0], y: node.position[1] } : layout.get(node.id) ?? { x: 0, y: 0 }),
    measured: measurements[node.id], selected: node.id === activeId, ariaLabel: node.id + ' · ' + node.type,
    data: {
      id: node.id, type: node.type, inputs: Object.keys(lookup.get(node.type)?.inputs ?? {}),
      outputs: Object.keys(lookup.get(node.type)?.outputs ?? {}),
    },
  }));
  const edges: Edge[] = workflow.edges.map(edge => ({
    id: JSON.stringify([edge.source, edge.source_port, edge.target, edge.target_port]), source: edge.source, target: edge.target,
    sourceHandle: edge.source_port, targetHandle: edge.target_port,
  }));

  function save(next: Workflow) {
    onChange(JSON.stringify(next, null, 2));
    setError('');
  }

  function positioned(items: WorkflowNode[], points: Map<string, Point>): WorkflowNode[] {
    return items.map(node => {
      const point = points.get(node.id);
      return point ? { ...node, position: [point.x, point.y] } : node;
    });
  }

  function currentPositions() {
    return new Map(nodes.map(node => [node.id, node.position]));
  }

  function select(id: string) {
    setActiveId(id);
    setShowCatalog(false);
  }

  function connect(connection: Connection) {
    if (!workflow || !connection.sourceHandle || !connection.targetHandle) return;
    const source = workflow.nodes.find(node => node.id === connection.source);
    const target = workflow.nodes.find(node => node.id === connection.target);
    const output = lookup.get(source?.type ?? '')?.outputs[connection.sourceHandle];
    const input = lookup.get(target?.type ?? '')?.inputs[connection.targetHandle];
    if (!output || !input || output !== input) {
      setError('Connect ports with the same data type.');
      return;
    }
    if (workflow.edges.some(edge => edge.target === connection.target && edge.target_port === connection.targetHandle)) {
      setError('This input already has a connection.');
      return;
    }
    // Keep the current positions while wiring. Arrange explicitly to use the new topology.
    save({ ...workflow, nodes: positioned(workflow.nodes, currentPositions()), edges: [...workflow.edges, {
      source: connection.source, source_port: connection.sourceHandle,
      target: connection.target, target_port: connection.targetHandle,
    }] });
  }

  function add(spec: NodeSpec) {
    if (!workflow) return;
    const used = new Set(workflow.nodes.map(node => node.id));
    let index = 1;
    while (used.has('node' + index)) index++;
    const id = 'node' + index;
    const bottom = nodes.length ? Math.max(...nodes.map(node => node.position.y)) + 155 : 0;
    save({ ...workflow, nodes: [...positioned(workflow.nodes, currentPositions()), {
      id, type: spec.type, position: [0, bottom], params: Object.fromEntries(Object.entries(spec.parameters).map(([name, rule]) => [name, rule[0]])),
    }] });
    setQuery('');
    select(id);
    catalogButton.current?.focus();
  }

  function setParam(node: WorkflowNode, key: string, value: number | string) {
    if (!workflow) return;
    save({ ...workflow, nodes: workflow.nodes.map(item => item.id === node.id
      ? { ...item, params: { ...item.params, [key]: value } } : item) });
  }

  return <section aria-label="Universal workflow canvas">
    <div className="studio-canvas-toolbar">
      <small>{nodes.length} nodes · {edges.length} connections</small>
      <div className="studio-actions">
        <button disabled={!nodes.length} onClick={() => {
          setDragPositions({});
          save({ ...workflow, nodes: positioned(workflow.nodes, layout) });
          setLayoutRevision(value => value + 1);
        }}>Arrange nodes</button>
        <button className="primary" ref={catalogButton} aria-expanded={showCatalog} onClick={() => setShowCatalog(value => !value)}>
          {showCatalog ? 'Close node catalog' : 'Add node'}
        </button>
      </div>
    </div>
    {error && <p role="alert" className="cfg-error-text">{error}</p>}
    <div className="studio-editor" data-catalog={showCatalog}>
      <div className="studio-canvas">
        <FlowCanvas label="Universal strategy draft" nodes={nodes} edges={edges} nodeTypes={nodeTypes}
          height="100%" fitKey={nodes.length + '|' + layoutRevision} editable
          onConnect={connect} onNodeClick={select} onPaneClick={() => setActiveId('')}
          onNodesChange={changes => {
            const dimensions: typeof measurements = {};
            const moving: Record<string, Point> = {};
            const finished = new Map<string, Point>();
            const selected = changes.find(change => change.type === 'select' && change.selected);
            for (const change of changes) {
              if (change.type === 'dimensions' && change.dimensions) dimensions[change.id] = change.dimensions;
              if (change.type !== 'position' || !change.position) continue;
              if (change.dragging) moving[change.id] = change.position;
              else finished.set(change.id, change.position);
            }
            if (Object.keys(dimensions).length) setMeasurements(previous => ({ ...previous, ...dimensions }));
            if (Object.keys(moving).length || finished.size) {
              setDragPositions(previous => {
                const next = { ...previous, ...moving };
                for (const id of finished.keys()) delete next[id];
                return next;
              });
            }
            if (finished.size) save({ ...workflow, nodes: positioned(workflow.nodes, finished) });
            if (selected?.type === 'select') select(selected.id);
            else if (changes.some(change => change.type === 'select' && change.id === activeId)) setActiveId('');
          }}
          onBeforeDelete={async ({ nodes: removed }) => window.confirm(removed.length
            ? 'Remove the selected nodes and their connections from this draft?'
            : 'Remove the selected connections from this draft?')}
          onDelete={({ nodes: removedNodes, edges: removedEdges }) => {
            const removed = new Set(removedNodes.map(node => node.id));
            const disconnected = new Set(removedEdges.map(edge => edge.id));
            if (removed.has(activeId)) setActiveId('');
            if (removed.size) setMeasurements(previous => Object.fromEntries(
              Object.entries(previous).filter(([id]) => !removed.has(id))
            ));
            save({ ...workflow, nodes: positioned(workflow.nodes.filter(node => !removed.has(node.id)), currentPositions()),
              edges: workflow.edges.filter((_, index) => !disconnected.has(edges[index].id)) });
          }}
        />
      </div>
      <aside className="studio-inspector" aria-label={showCatalog ? 'Node catalog' : 'Node settings'}>
        {showCatalog ? <>
          <h3>Node catalog</h3>
          <label className="studio-field">Search node types
            <input ref={searchInput} type="search" value={query} onChange={event => setQuery(event.target.value)} />
          </label>
          <div className="studio-node-list">
            {matches.map(spec => <button key={spec.type} onClick={() => add(spec)}>{spec.type}<span aria-hidden="true">+</span></button>)}
            {!matches.length && <p className="cfg-note">No matching node types.</p>}
          </div>
          <button onClick={onRefreshCatalog}>Refresh node catalog</button>
          <details><summary>Catalog JSON</summary><pre>{JSON.stringify(catalog, null, 2)}</pre></details>
        </> : <>
          <h3>Node settings</h3>
          <label className="studio-field">Node
            <select value={active?.id ?? ''} onChange={event => select(event.target.value)}>
              <option value="">Select a node</option>
              {workflow.nodes.map(node => <option key={node.id} value={node.id}>{node.id} · {node.type}</option>)}
            </select>
          </label>
          {!active && <p className="cfg-note">Select a node to edit its parameters. Drag between matching ports to connect nodes.</p>}
          {active && !activeSpec && <p className="cfg-note">This node type is missing from the catalog.</p>}
          {active && activeSpec && <>
            {Object.entries(activeSpec.parameters).map(([name, rule]) => <label key={name} className="studio-field">
              {name}
              {typeof rule[0] === 'number' ? <input type="number" min={rule[0]} max={rule[1]}
                step={name === 'period' ? '1' : 'any'} value={active.params?.[name] ?? rule[0]}
                onChange={event => setParam(active, name, event.target.value === '' ? '' : Number(event.target.value))} />
                : <select value={String(active.params?.[name] ?? rule[0])}
                  onChange={event => setParam(active, name, event.target.value)}>
                  {rule.map(value => <option key={value}>{value}</option>)}
                </select>}
            </label>)}
            {!Object.keys(activeSpec.parameters).length && <p className="cfg-note">No configurable parameters.</p>}
          </>}
        </>}
      </aside>
    </div>
  </section>;
}
