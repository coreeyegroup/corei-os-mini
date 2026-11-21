/* One canvas for every graph on the workstation (n8n-style nodes and wires), built so it never flickers or jumps.
 * Why: a React Flow canvas mounted while hidden (a background sub-tab, a closed panel, an off-screen panel of "All
 * panels") measures itself as 0 × 0 and draws at a wrong zoom; several hidden canvases also cost time on every update.
 * Purpose: FlowCanvas — a fixed-height box that mounts the canvas only while it is on screen (IntersectionObserver),
 * fits the graph when it first appears and when its size changes, never animates wires, and keeps its height
 * constant so nothing around it moves.
 * Must never: change the page layout while it loads or updates (the box has a fixed height). */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Edge, Node, NodeChange, NodeTypes, Connection, EdgeChange, OnBeforeDelete, OnDelete } from '@xyflow/react';

import Canvas from './FlowCanvasImpl';

export type FlowCanvasProps = {
  nodes: Node[]; edges: Edge[]; nodeTypes: NodeTypes; height?: number | string; label: string;
  onNodesChange?: (c: NodeChange[]) => void; onEdgesChange?: (c: EdgeChange[]) => void; onConnect?: (c: Connection) => void;
  onEdgesDelete?: (e: Edge[]) => void; onNodeClick?: (id: string) => void; onNodeDoubleClick?: (id: string) => void; onPaneClick?: () => void;
  onBeforeDelete?: OnBeforeDelete; onDelete?: OnDelete;
  editable?: boolean; fitKey?: string; minimap?: boolean; children?: ReactNode; fitNodes?: string[];
};

export default function FlowCanvas(props: FlowCanvasProps) {
  const box = useRef<HTMLDivElement>(null);
  const [shown, setShown] = useState(false), [size, setSize] = useState('');
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    if (typeof IntersectionObserver === 'undefined' || typeof ResizeObserver === 'undefined') return;
    const io = new IntersectionObserver(es => setShown(es.some(e => e.isIntersecting && e.boundingClientRect.width > 0)), { threshold: 0.01 });
    const ro = new ResizeObserver(es => { const r = es[0]?.contentRect; if (r && r.width > 0 && r.height > 0) setSize(`${Math.round(r.width / 40)}x${Math.round(r.height / 40)}`); });
    io.observe(el); ro.observe(el);
    return () => { io.disconnect(); ro.disconnect(); };
  }, []);
  return <div ref={box} className="cg-canvas" aria-label={props.label} style={{ height: props.height ?? 640 }}>
    {shown ? <Canvas {...props} fitKey={`${props.fitKey ?? ''}|${size}`} />
      : <div className="cg-canvas-wait">{props.children ?? 'The canvas draws when it is on screen.'}</div>}
  </div>;
}
