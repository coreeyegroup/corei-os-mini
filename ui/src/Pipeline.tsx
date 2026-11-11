/* One screen per trading stage: the flow strip (all eight stages with progress), what this
 * stage answers and feeds, and its components with build step, state and a link to the
 * part that already works. "Build plan" shows every component across stages by step. */
import { useState } from 'react';
import type { View } from './layout';
import { goto } from './nav';
import { PIPELINE, STEPS, STATE_LABEL, progress, stageOf, type Component } from './flow';

export function FlowStrip({ view, open }: { view?: View; open: (v: View) => void }) {
  return <div className="pl-flow" role="list" aria-label="Trading flow">
    {PIPELINE.map(s => { const p = progress(s); return (
      <button key={s.view} role="listitem" className={'pl-node' + (s.view === view ? ' on' : '')} onClick={() => open(s.view)} title={s.question}>
        <span className="pl-n">{s.n}</span><span className="pl-t">{s.short ?? s.title}</span>
        <span className="pl-bar"><i style={{ width: p.pct + '%' }} /></span><span className="pl-c">{p.built}/{p.total}</span>
      </button>); })}
  </div>;
}

function Row({ c, stage }: { c: Component; stage?: string }) {
  return <tr className={'pl-' + c.state.toLowerCase()}>
    <td className="num">{c.step}</td>
    {stage && <td>{stage}</td>}
    <td>{c.name}</td>
    <td className="muted">{c.does}</td>
    <td><span className={'pl-state ' + c.state.toLowerCase()}>{STATE_LABEL[c.state]}</span></td>
    <td>{c.where ? <button className="link" onClick={() => goto(c.where![0], c.where![1])}>open →</button> : <span className="muted">—</span>}</td>
  </tr>;
}

export default function Stage({ view, open }: { view: View; open: (v: View) => void }) {
  const [plan, setPlan] = useState(false);
  const s = stageOf(view);
  if (!s) return null;
  const p = progress(s), i = PIPELINE.indexOf(s), prev = PIPELINE[i - 1], next = PIPELINE[i + 1];
  return <div className="content dense-page pl">
    <FlowStrip view={view} open={open} />
    <div className="pl-head">
      <div><b>{s.n}. {s.title}</b> <span className="muted">— {s.question}</span></div>
      <div className="pl-io">
        <span>in: {prev ? <button className="link" onClick={() => open(prev.view)}>{prev.title}</button> : 'brokers and vendors'}</span>
        <span>out: {s.feeds}{next ? <> → <button className="link" onClick={() => open(next.view)}>{next.title}</button></> : ''}</span>
        <span>{p.built} built · {p.part} partly · {p.total - p.built - p.part} to build</span>
      </div>
      <div className="tabs-mini">
        <button aria-pressed={!plan} onClick={() => setPlan(false)}>This stage</button>
        <button aria-pressed={plan} onClick={() => setPlan(true)}>Build plan (all stages)</button>
      </div>
    </div>
    {!plan ? <table className="dense-table pl-table">
      <thead><tr><th>Step</th><th>Component</th><th>What it does</th><th>State</th><th></th></tr></thead>
      <tbody>{[...s.components].sort((a, b) => a.step - b.step).map(c => <Row key={c.name} c={c} />)}</tbody>
    </table> : null}
    {!plan && <><h4 className="pl-sub">End-to-end flow</h4><table className="dense-table pl-table pl-overview">
      <thead><tr><th>#</th><th>Stage</th><th>Answers</th><th>Hands to the next stage</th><th>Next to build</th><th>Progress</th></tr></thead>
      <tbody>{PIPELINE.map(st => { const q = progress(st), nx = st.components.find(c => c.state === 'NEXT');
        return <tr key={st.view} className={st.view === view ? 'on' : ''} onClick={() => open(st.view)}>
          <td className="num">{st.n}</td><td>{st.title}</td><td>{st.question}</td><td>{st.feeds}</td>
          <td>{nx ? nx.name : <span className="muted">—</span>}</td>
          <td className="pl-prog"><span className="pl-bar"><i style={{ width: q.pct + '%' }} /></span> {q.built}/{q.total}</td></tr>; })}</tbody>
    </table></>}
    {plan && <table className="dense-table pl-table">
      <thead><tr><th>Step</th><th>Stage</th><th>Component</th><th>What it does</th><th>State</th><th></th></tr></thead>
      {STEPS.map(([n, name, what]) => <tbody key={n}>
        <tr className="pl-step"><td colSpan={6}><b>Step {n} · {name}</b> <span className="muted">— {what}</span></td></tr>
        {PIPELINE.flatMap(st => st.components.filter(c => c.step === n).map(c => <Row key={st.view + c.name} c={c} stage={st.title} />))}
      </tbody>)}
    </table>}
  </div>;
}

/* Side bar pane: the eight stages, each with its built count. */
export function FlowList({ view, open }: { view: View; open: (v: View) => void }) {
  return <>{PIPELINE.map(s => { const p = progress(s); return (
    <button key={s.view} className={'sb-row' + (s.view === view ? ' selected' : '')} onClick={() => open(s.view)} title={s.question}>
      <span className="sb-meta pl-n">{s.n}</span><span className="sb-grow">{s.title}</span>
      <span className="pl-bar sm"><i style={{ width: p.pct + '%' }} /></span><span className="sb-meta">{p.built}/{p.total}</span>
    </button>); })}</>;
}
