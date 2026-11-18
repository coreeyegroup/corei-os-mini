/* The React Flow canvas behind FlowCanvas (loaded on first use). Fits the graph once per graph and size — not on
 * every data update — so values can change on the nodes without the view moving.
 * Must never: animate wires or refit while the viewer is looking at the same graph. */
import { useEffect, useRef, useState } from 'react';
import { applyNodeChanges, Background, Controls, MiniMap, ReactFlow, ReactFlowProvider, useNodesInitialized, useReactFlow, type Node, type NodeChange } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import './graph.css';
import type { FlowCanvasProps } from './FlowCanvas';

function Fit({ fitKey, count, only }: { fitKey?: string; count: number; only?: string[] }) {
  const rf = useReactFlow(), last = useRef(''), ready = useNodesInitialized();
  useEffect(() => {   // once the nodes are measured, and again only when the graph or the canvas size changes
    if (!count || !ready || last.current === fitKey) return;
    last.current = fitKey ?? '';
    void rf.fitView({ padding: 0.06, maxZoom: 1, duration: 0, ...(only?.length ? { nodes: only.map(id => ({ id })) } : {}) });
  }, [fitKey, count, ready, rf]); // eslint-disable-line react-hooks/exhaustive-deps
  return null;
}

/* Read-only use: the canvas keeps its own copy of the nodes (so React Flow can measure them) and takes new data from
 * the caller without losing those measurements — values change in place, nothing jumps. */
function useOwnNodes(nodes: Node[], controlled: boolean): [Node[], (c: NodeChange[]) => void] {
  const [own, setOwn] = useState<Node[]>(nodes);
  useEffect(() => { if (!controlled) setOwn(prev => nodes.map(n => { const o = prev.find(x => x.id === n.id); return o ? { ...n, measured: o.measured, width: o.width, height: o.height, selected: o.selected } : n; })); }, [nodes, controlled]);
  return [controlled ? nodes : own, (c: NodeChange[]) => setOwn(prev => applyNodeChanges(c, prev))];
}

export default function FlowCanvasImpl(p: FlowCanvasProps) {
  const [nodes, onOwn] = useOwnNodes(p.nodes, Boolean(p.onNodesChange));
  return <ReactFlowProvider><ReactFlow nodes={nodes} edges={p.edges} nodeTypes={p.nodeTypes} onNodesChange={p.onNodesChange ?? onOwn} onEdgesChange={p.onEdgesChange}
    onConnect={p.onConnect} onEdgesDelete={p.onEdgesDelete} onNodeClick={(_, n) => p.onNodeClick?.(n.id)} onNodeDoubleClick={(_, n) => p.onNodeDoubleClick?.(n.id)}
    onPaneClick={p.onPaneClick} nodesDraggable={Boolean(p.editable)} nodesConnectable={Boolean(p.editable)} edgesFocusable={Boolean(p.editable)}
    deleteKeyCode={p.editable ? ['Backspace', 'Delete'] : null} minZoom={0.1} maxZoom={2} proOptions={{ hideAttribution: true }}>
    <Fit fitKey={p.fitKey} count={p.nodes.length} only={p.fitNodes} />
    <Background gap={20} size={1} /><Controls showInteractive={false} position="bottom-left" />
    {p.minimap !== false && <MiniMap position="bottom-right" pannable zoomable className="cg-minimap" style={{ width: 150, height: 96 }} nodeColor="var(--line)" maskColor="rgba(0,0,0,.35)" />}
  </ReactFlow></ReactFlowProvider>;
}
