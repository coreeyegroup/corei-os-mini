/* Draft-only visual editor for the universal workflow schema.
 * Existing approved Calculation Graph workbench remains the production authoring path.
 */
import { useMemo, useState } from 'react';
import { Handle, Position, applyEdgeChanges,
  type Connection, type Edge, type Node, type NodeProps } from '@xyflow/react';
import FlowCanvas from './calc/FlowCanvas';

type WNode = { id: string; type: string; params?: Record<string, number | string> };
type WEdge = { source: string; source_port: string; target: string; target_port: string };
type Workflow = { schema_version: string; name: string; nodes: WNode[]; edges: WEdge[] };
type Ports = { type: string; inputs: Record<string, string>; outputs: Record<string, string>; parameters: Record<string, unknown> };
type Props = { draft: string; onChange: (text: string) => void; catalog: Ports[] };

function Block({ data }: NodeProps<Node<{ label: string; inputs: string[]; outputs: string[] }>>) {
  return <div style={{background:'var(--surface, #202633)',color:'var(--text, #fff)',border:'1px solid #6b7280',borderRadius:8,minWidth:170,padding:10}}>
    <strong style={{fontSize:12}}>{data.label}</strong>
    {data.inputs.map(p=><div key={p} style={{position:'relative',fontSize:11,marginTop:8}}>
      <Handle type="target" position={Position.Left} id={p} style={{top:'50%'}}/>{p}
    </div>)}
    {data.outputs.map(p=><div key={p} style={{position:'relative',fontSize:11,marginTop:8,textAlign:'right'}}>
      {p}<Handle type="source" position={Position.Right} id={p} style={{top:'50%'}}/>
    </div>)}
  </div>;
}
const types = { universal: Block };

export default function UniversalCanvas({draft,onChange,catalog}:Props) {
  const [error,setError] = useState('');
  const [selected,setSelected] = useState('');
  const [activeNode,setActiveNode] = useState('');
  const [positions,setPositions] = useState<Record<string,{x:number;y:number}>>({});
  let workflow: Workflow | null = null;
  try {
    const parsed = JSON.parse(draft);
    if (Array.isArray(parsed.nodes) && Array.isArray(parsed.edges)) workflow = parsed;
  } catch { /* editable JSON may temporarily be incomplete */ }
  const lookup = useMemo(()=>Object.fromEntries(catalog.map(n=>[n.type,n])),[catalog]);
  const nodes: Node[] = (workflow?.nodes ?? []).map((n,i)=>({
    id:n.id,type:'universal',position:positions[n.id] ?? {x:(i%3)*255,y:Math.floor(i/3)*180},
    data:{label:n.id+' · '+n.type,inputs:Object.keys(lookup[n.type]?.inputs ?? {}),
      outputs:Object.keys(lookup[n.type]?.outputs ?? {})}
  }));
  const edges: Edge[] = (workflow?.edges ?? []).map((e,i)=>({
    id:'edge-'+i,source:e.source,target:e.target,
    sourceHandle:e.source_port,targetHandle:e.target_port
  }));
  const save = (next: Workflow) => { onChange(JSON.stringify(next,null,2)); setError(''); };
  const connect = (c:Connection) => {
    if (!workflow || !c.source || !c.target || !c.sourceHandle || !c.targetHandle) return;
    const src = workflow.nodes.find(n=>n.id===c.source);
    const dst = workflow.nodes.find(n=>n.id===c.target);
    const out = lookup[src?.type ?? '']?.outputs[c.sourceHandle];
    const input = lookup[dst?.type ?? '']?.inputs[c.targetHandle];
    if (!out || !input || out!==input) { setError('Incompatible port types'); return; }
    if (workflow.edges.some(e=>e.target===c.target && e.target_port===c.targetHandle)) {
      setError('Input already connected'); return;
    }
    save({...workflow,edges:[...workflow.edges,{source:c.source,source_port:c.sourceHandle,target:c.target,target_port:c.targetHandle}]});
  };
  const add = (type:string) => {
    if (!workflow) return;
    const used = new Set(workflow.nodes.map(n=>n.id));
    let i=1; while(used.has('node'+i)) i++;
    const params:Record<string,number|string> = {};
    for (const [key,rule] of Object.entries(lookup[type]?.parameters ?? {})) {
      if (Array.isArray(rule)) params[key] = typeof rule[0]==='number' ? rule[0] : rule[0];
    }
    save({...workflow,nodes:[...workflow.nodes,{id:'node'+i,type,params}]});
  };
  const editNode = (id:string, params:Record<string,number|string>) => {
    if (!workflow) return;
    save({...workflow,nodes:workflow.nodes.map(n=>n.id===id?{...n,params}:n)});
  };
  const active = workflow?.nodes.find(n=>n.id===activeNode);
  if (!workflow) return <p role="alert">Enter valid workflow JSON before opening the canvas.</p>;
  return <section aria-label="Universal workflow canvas">
    <div className="intel-bar">
      <label>Add node <select aria-label="Node type to add" value={selected} onChange={e=>setSelected(e.target.value)}>
        <option value="">Select node type</option>{catalog.map(n=><option key={n.type} value={n.type}>{n.type}</option>)}
      </select></label>
      <button disabled={!selected} onClick={()=>add(selected)}>Add node</button>
      <small className="cfg-note">Connect matching ports; edit node parameters in JSON below. Draft only.</small>
    </div>
    {error && <p role="alert" className="cfg-error-text">{error}</p>}
    {active && <fieldset style={{border:'1px solid var(--line, #64748b)',padding:12,margin:'10px 0'}}>
      <legend>Parameters · {active.id} ({active.type})</legend>
      {Object.entries(lookup[active.type]?.parameters ?? {}).map(([key,rule])=>{
        const bounds = Array.isArray(rule) ? rule : [];
        const numeric = typeof bounds[0] === 'number';
        const current = active.params?.[key] ?? bounds[0] ?? '';
        return <label key={key} style={{display:'inline-flex',gap:8,alignItems:'center',marginRight:16}}>
          {key}
          {numeric ? <input aria-label={active.id+' '+key} type="number" min={bounds[0]} max={bounds[1]}
            step={key==='period'?'1':'any'} value={current}
            onChange={e=>editNode(active.id,{...active.params,[key]:Number(e.target.value)})}/>
            : <select aria-label={active.id+' '+key} value={String(current)}
              onChange={e=>editNode(active.id,{...active.params,[key]:e.target.value})}>
              {bounds.map(v=><option key={String(v)} value={String(v)}>{String(v)}</option>)}
            </select>}
        </label>;
      })}
      {!Object.keys(lookup[active.type]?.parameters ?? {}).length && <small>No configurable parameters for this node.</small>}
    </fieldset>}
    <FlowCanvas label="Universal strategy draft" nodes={nodes} edges={edges} nodeTypes={types} height={460}
      editable onConnect={connect}
      onNodeClick={setActiveNode}
      onNodesChange={changes=>{
        const moved: Record<string,{x:number;y:number}> = {};
        for (const change of changes) {
          if (change.type === 'position' && change.position) moved[change.id] = change.position;
        }
        if (Object.keys(moved).length) setPositions(old=>({...old,...moved}));
        // Position edits are visual only in schema v1. Deletion updates the draft.
        const removed = changes.filter(c=>c.type==='remove').map(c=>c.id);
        if (removed.length) save({...workflow!,nodes:workflow!.nodes.filter(n=>!removed.includes(n.id)),
          edges:workflow!.edges.filter(e=>!removed.includes(e.source)&&!removed.includes(e.target))});
      }}
      onEdgesChange={changes=>{
        const next=applyEdgeChanges(changes,edges);
        if (next.length!==edges.length) {
          const keep=new Set(next.map(e=>e.id));
          save({...workflow!,edges:workflow!.edges.filter((_,i)=>keep.has('edge-'+i))});
        }
      }}
    />
  </section>;
}
