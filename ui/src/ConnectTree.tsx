/*
 * Connectivity, in the order institutional OMS/EMS set-ups use (counterparty first):
 *   1 Provider   the counterparty you have the legal relationship with: broker, exchange, ECN, prime broker,
 *                liquidity provider, feed, data vendor or data centre (legal entity, regulator, contacts).
 *   2 Server / session   how COREI reaches it: technology (MT5, FIX, API …), environment, role, and the
 *                endpoint (MT5 server name with its addresses, FIX host + CompIDs, API base URL). App-level
 *                secrets live here (API key, FIX logon, bridge token).
 *   3 Account    the account held at that provider on that server (MT5 login, FIX Account <1>, Upstox client ID),
 *                with its own login. An MT5 account can move to another server of the same broker.
 * Then: test the login, pick symbols to collect (Market data), compare (Market intelligence).
 * Left: the tree (keyboard: arrows, Enter). Right: the selected item and its next step. The same tree can be
 * grouped by technology or by provider type. Data: GET /api/config/tree.
 */
import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react';
import { AccountAddresses, api, BLOCKERS, Choice, Env, envOfServer, Field, FIX_BLANK, FixFields, pendingMessage, PURPOSE_LABEL, ROLE_OPTIONS,
  toPayload, type Navigate, type Row } from './Configuration';
import { BrokerLogin, BrokerRead } from './ConfigControls';
import { goto } from './nav';

type Node = { key: string; kind: 'tech' | 'type' | 'who' | 'conn' | 'acct' | 'none' | 'new'; label: string; meta?: string; dot?: string; state?: string;
  children: Node[]; ref?: Row };
const PREF = 'corei-os-mini.connect-tree.v2';
const readPref = (): Row => { try { return JSON.parse(localStorage.getItem(PREF) || '{}'); } catch { return {}; } };
const writePref = (v: Row) => { try { localStorage.setItem(PREF, JSON.stringify(v)); } catch { /* preference only */ } };
const why = (code: string) => BLOCKERS[code] ?? code.replace(/_/g, ' ').toLowerCase();
const SHORT_ROLE: Record<string, string> = { MARKET_DATA: 'data', TRADING: 'exec', BOTH: 'data+exec' };
const STATE_LABEL: Record<string, string> = { BUILT: 'built', PARTIAL: 'partly built', PLANNED: 'planned' };
const codeOf = (name: string) => (name.toUpperCase().replace(/[^A-Z0-9]+/g, '_').replace(/^[^A-Z]+/, '').replace(/_+$/, '') || 'P').slice(0, 20);

function acctDot(a: Row) { return a.status !== 'ACTIVE' || a.trading_permission === 'DISABLED' ? 'off' : a.data_blockers?.length ? 'warn' : 'ok'; }
function connDot(c: Row) {
  const s = c.live_status?.state;
  return c.status !== 'ACTIVE' ? 'off' : s === 'CONNECTED' ? 'ok' : s === 'FAILED' ? 'bad' : c.accounts?.some((a: Row) => acctDot(a) === 'warn') ? 'warn' : 'idle';
}
const worst = (dots: string[]) => ['bad', 'warn', 'ok', 'idle', 'off'].find(d => dots.includes(d)) ?? 'idle';
const where = (c: Row) => c.platform === 'MT5' ? c.settings?.server_name : c.platform === 'FIX' ? `${c.settings?.host}:${c.settings?.port}` : c.settings?.api_base_url?.replace(/^https:\/\//, '');

const permission = (a: Row) => a.trading_permission === 'READ_ONLY' ? ' · read only' : a.trading_permission === 'DISABLED' ? ' · disabled' : '';
export const fastest = (a: Row) => { const first = a.routing?.addresses?.[0]; return first?.latency_ms != null ? `${Math.round(first.latency_ms)} ms` : ''; };

const TECH_SHORT: Record<string, string> = { MT5: 'MT5', FIX: 'FIX', REST_API: 'API' };

function acctNode(a: Row, c: Row): Node {
  // Under its server: the login, the account name and (MT5) the latency of the address it uses first.
  return { key: 'a:' + a.id, kind: 'acct', ref: a, dot: acctDot(a), label: a.external_account_id || a.code,
    meta: [a.name, c.platform === 'MT5' ? fastest(a) : ''].filter(Boolean).join(' · ') + permission(a), children: [] };
}

function connNode(c: Row, withTech = true): Node {
  return { key: 'c:' + c.id, kind: 'conn', ref: c, dot: connDot(c), label: where(c) ?? c.code,
    meta: `${withTech ? (TECH_SHORT[c.platform] ?? c.platform) + ' · ' : ''}${c.environment === 'LIVE' ? 'LIVE' : 'demo'} · ${SHORT_ROLE[c.purpose] ?? c.purpose}`,
    children: c.accounts.map((a: Row) => acctNode(a, c)) };
}

/** Every technology has the same three levels: provider › server / session › account. */
function whoChildren(conns: Row[], withTech = false): Node[] {
  return conns.map(c => connNode(c, withTech));
}

function providerNode(p: Row, data: Row): Node {
  const mine = (data.connections as Row[]).filter(c => c.provider_id === p.id), kids = mine.map(c => connNode(c, true));
  const n = mine.reduce((k, c) => k + c.accounts.length, 0);
  return { key: 'p:' + p.id, kind: 'who', ref: p, label: p.name,
    meta: mine.length ? `${data.provider_kinds[p.kind] ?? p.kind} · ${mine.length} server${mine.length === 1 ? '' : 's'} · ${n} acct` : `${data.provider_kinds[p.kind] ?? p.kind} · no server yet`,
    dot: mine.length ? worst(kids.map(c => c.dot!)) : 'off', children: kids };
}

function build(data: Row, group: string): Node[] {
  if (group === 'provider') return (data.providers as Row[]).filter(p => p.status !== 'ARCHIVED').map(p => providerNode(p, data));
  if (group === 'type') {
    const kinds = data.provider_kinds as Record<string, string>;
    return Object.entries(kinds).map(([k, label]) => {
      const who = (data.providers as Row[]).filter(p => p.kind === k && p.status !== 'ARCHIVED').map(p => providerNode(p, data));
      return { key: 'k:' + k, kind: 'type' as const, label, meta: `${who.length}`, dot: worst(who.map(w => w.dot!)), children: who, ref: { kind: k, label } };
    });
  }
  const nodes: Node[] = (data.technologies as Row[]).map(t => {
    const who = (t.who as Row[]).map(w => {
      const kids = whoChildren(w.connections);
      const n = w.connections.reduce((k: number, c: Row) => k + c.accounts.length, 0);
      return { key: `w:${t.id}:${w.provider.id}`, kind: 'who' as const, ref: { ...w.provider, technology: t.id }, label: w.provider.name,
        meta: `${w.connections.length} server${w.connections.length === 1 ? '' : 's'} · ${n} acct`, dot: worst(kids.map((c: Node) => c.dot!)), children: kids };
    });
    return { key: 't:' + t.id, kind: 'tech' as const, ref: t, label: t.label, state: t.state,
      meta: t.state === 'PLANNED' ? 'planned' : `${t.counts.who} who · ${t.counts.accounts} acct`, dot: who.length ? worst(who.map(w => w.dot!)) : 'off', children: who };
  });
  if ((data.unconnected as Row[]).length) nodes.push({ key: 'u:', kind: 'none', label: 'Not connected yet', meta: `${data.unconnected.length}`, children:
    (data.unconnected as Row[]).map(p => ({ key: 'p:' + p.id, kind: 'who' as const, ref: p, label: p.name, meta: data.provider_kinds[p.kind] ?? p.kind, dot: 'off', children: [] })) });
  return nodes;
}

export default function ConnectTree({ navigate, canEdit, changed }: { navigate: Navigate; canEdit: boolean; changed?: () => void }) {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState('');
  const [pref, setPref] = useState<Row>(() => ({ group: 'provider', open: [], ...readPref() }));
  const [selected, setSelected] = useState<string>(() => readPref().selected ?? ''), [filter, setFilter] = useState('');
  const save = (patch: Row) => setPref(p => { const next = { ...p, ...patch }; writePref({ ...next, selected }); return next; });
  const load = async () => { try { setData(await api('config/tree')); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, []);
  useEffect(() => { writePref({ ...pref, selected }); }, [selected]);
  const roots = useMemo(() => data ? build(data, pref.group) : [], [data, pref.group]);
  const index = useMemo(() => { const m = new Map<string, { node: Node; parents: Node[] }>();
    const walk = (ns: Node[], parents: Node[]) => ns.forEach(n => { m.set(n.key, { node: n, parents }); walk(n.children, [...parents, n]); }); walk(roots, []); return m; }, [roots]);
  const open = new Set<string>(pref.open);
  const q = filter.trim().toLowerCase();
  const matches = (n: Node): boolean => !q || `${n.label} ${n.meta ?? ''} ${n.ref?.code ?? ''}`.toLowerCase().includes(q) || n.children.some(matches);
  const visible: { node: Node; depth: number }[] = [];
  const walk = (ns: Node[], depth: number) => ns.filter(matches).forEach(n => { visible.push({ node: n, depth }); if (n.children.length && (open.has(n.key) || q)) walk(n.children, depth + 1); });
  walk(roots, 0);
  const toggle = (key: string, to?: boolean) => { const s = new Set(open); (to ?? !s.has(key)) ? s.add(key) : s.delete(key); save({ open: [...s] }); };
  const select = (key: string) => { setSelected(key); const parents = index.get(key)?.parents ?? []; if (parents.some(p => !open.has(p.key))) save({ open: [...new Set([...open, ...parents.map(p => p.key)])] }); };
  function onKey(e: KeyboardEvent) {
    const i = visible.findIndex(v => v.node.key === selected), cur = visible[i]?.node;
    if (e.key === 'ArrowDown') { e.preventDefault(); setSelected(visible[Math.min(visible.length - 1, i + 1)]?.node.key ?? ''); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setSelected(visible[Math.max(0, i - 1)]?.node.key ?? ''); }
    else if (e.key === 'ArrowRight' && cur?.children.length) { e.preventDefault(); toggle(cur.key, true); }
    else if (e.key === 'ArrowLeft' && cur) { e.preventDefault(); if (open.has(cur.key)) toggle(cur.key, false); else { const p = index.get(cur.key)?.parents.at(-1); if (p) setSelected(p.key); } }
  }
  if (error && !data) return <p role="alert" className="cfg-error">{error}</p>;
  if (!data) return <p className="cfg-note">Loading…</p>;
  const sel = index.get(selected);
  const done = async (key?: string) => { changed?.(); await load(); if (key) select(key); };
  return <div className="ct">
    <div className="ct-tree">
      <div className="ct-tools">
        <div className="ct-seg" role="group" aria-label="Group by">
          <button aria-pressed={pref.group === 'provider'} onClick={() => save({ group: 'provider' })} title="Provider › server / session › account (standard)">Provider</button>
          <button aria-pressed={pref.group === 'tech'} onClick={() => save({ group: 'tech' })} title="Technology › provider › server / session › account">Technology</button>
          <button aria-pressed={pref.group === 'type'} onClick={() => save({ group: 'type' })} title="Broker, exchange, ECN, feed … › provider › server / session › account">Provider type</button>
        </div>
        {canEdit && <button className="primary ct-addp" title="Step 1: add a provider (broker, exchange, ECN, feed …)" onClick={() => setSelected('new:provider')}>+ Provider</button>}
        <input aria-label="Filter the tree" placeholder="Filter…" value={filter} onChange={e => setFilter(e.target.value)} />
        <button title="Expand all" aria-label="Expand all" onClick={() => save({ open: [...new Set([...open, ...index.keys()])] })}>+</button>
        <button title="Collapse all" aria-label="Collapse all" onClick={() => save({ open: [] })}>−</button>
      </div>
      <div className="ct-head"><span>{pref.group === 'provider' ? 'Provider › server / session › account' : pref.group === 'tech' ? 'Technology › provider › server / session › account' : 'Provider type › provider › server / session › account'}</span></div>
      <ul role="tree" aria-label="Connectivity" tabIndex={0} onKeyDown={onKey} className="ct-list">
        {visible.map(({ node, depth }) => <li key={node.key} role="treeitem" aria-level={depth + 1} aria-selected={node.key === selected}
          aria-expanded={node.children.length ? open.has(node.key) || !!q : undefined}
          className={`ct-row ct-${node.kind}${node.key === selected ? ' sel' : ''}${node.state === 'PLANNED' ? ' planned' : ''}`} style={{ paddingLeft: 4 + depth * 14 }}
          onClick={() => setSelected(node.key)} onDoubleClick={() => node.children.length && toggle(node.key)}>
          <span className="ct-caret" onClick={e => { e.stopPropagation(); if (node.children.length) toggle(node.key); }}>{node.children.length ? (open.has(node.key) || q ? '▾' : '▸') : ''}</span>
          {node.dot && <i className={`ct-dot ${node.dot}`} />}
          <span className="ct-label">{node.label}</span><small className="ct-meta">{node.meta}</small>
        </li>)}
      </ul>
      <div className="ct-legend"><i className="ct-dot ok" />ready <i className="ct-dot warn" />needs action <i className="ct-dot bad" />failed <i className="ct-dot idle" />not connected <i className="ct-dot off" />off / planned</div>
    </div>
    <div className="ct-detail" aria-live="polite">
      {error && <p role="alert" className="cfg-error">{error}</p>}
      {selected === 'new:provider' ? <AddProvider data={data} cancel={() => setSelected('')} done={async key => { await done(key); }} />
        : !sel ? <Overview data={data} select={select} canEdit={canEdit} add={() => setSelected('new:provider')} />
        : sel.node.kind === 'tech' ? <TechPanel key={sel.node.key} tech={sel.node.ref!} data={data} canEdit={canEdit} select={select} done={done} navigate={navigate} />
        : sel.node.kind === 'type' ? <TypePanel node={sel.node} select={select} navigate={navigate} canEdit={canEdit} />
        : sel.node.kind === 'who' ? <WhoPanel key={sel.node.key} provider={sel.node.ref!} data={data} canEdit={canEdit} select={select} navigate={navigate} done={done} />
        : sel.node.kind === 'conn' ? <ConnPanel key={sel.node.key} conn={sel.node.ref!} data={data} canEdit={canEdit} select={select} navigate={navigate} />
        : sel.node.kind === 'acct' ? <AcctPanel key={sel.node.key} account={sel.node.ref!} conn={(data.connections as Row[]).find(c => c.id === sel.node.ref!.connection_id) ?? {}} data={data} canEdit={canEdit} navigate={navigate} reload={() => void load()} />
        : <Overview data={data} select={select} canEdit={canEdit} add={() => setSelected('new:provider')} />}
    </div>
  </div>;
}

/* ---------- right-hand panels ---------- */

function Head({ level, title, children }: { level: string; title: string; children?: React.ReactNode }) {
  return <header className="ct-dhead"><small>{level}</small><h2>{title}</h2><div className="ct-actions">{children}</div></header>;
}
function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return <dl className="ct-facts">{rows.filter(r => r[1] != null && r[1] !== '').map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>;
}
const pill = (v?: string | null) => v ? <span className={`cfg-pill ${v === 'BUILT' ? 'ok' : v === 'PARTIAL' ? 'warn' : 'off'}`}>{STATE_LABEL[v] ?? v.toLowerCase()}</span> : <span className="cfg-note">—</span>;

const FLOW: [string, string][] = [
  ['Provider', 'Who you deal with: broker, exchange, ECN, prime broker, liquidity provider, feed, data vendor, data centre. Legal entity, regulator, contacts.'],
  ['Server / session', 'How COREI reaches it: technology, environment (demo / live), role (data source, execution, both) and endpoint — MT5 server name, FIX host and CompIDs, or API URL.'],
  ['Account', 'The account you hold there, on that server: MT5 login, FIX account, Upstox client ID.'],
  ['Login', 'Save the account password (MT5: investor password is enough for prices) or the API key; Upstox: log in at the broker each day.'],
  ['Test', 'Read the account once: balance, server time and the provider\'s symbol list.'],
  ['Collect', 'Market data › Dashboard › the source › Available: pick symbols to collect. Then compare in Market intelligence.'],
];

function Overview({ data, select, canEdit, add }: { data: Row; select: (k: string) => void; canEdit: boolean; add: () => void }) {
  const providers = (data.providers as Row[]).filter(p => p.status !== 'ARCHIVED'), conns = data.connections as Row[];
  const accounts = conns.flatMap(c => c.accounts as Row[]);
  const counts = [providers.length, conns.length, accounts.length, accounts.filter(a => !a.data_blockers.some((b: string) => /CREDENTIAL|TOKEN|LOGIN/.test(b))).length,
    accounts.filter(a => a.last_login_at).length, data.summary.data_ready];
  return <>
    <Head level="Connectivity" title="Connect a provider, step by step">{canEdit && <button className="primary" onClick={add}>+ Provider</button>}</Head>
    <ol className="ct-flow">{FLOW.map(([t, d], i) => <li key={t}><b>{i + 1} · {t}</b><span className="cfg-note">{d}</span><small>{counts[i]} {i < 3 ? 'set up' : 'done'}</small></li>)}</ol>
    <p className="cfg-note">The tree on the left follows the same order. Select a provider to add its server or session; select a server to add an account; select an account to save its login, test it and collect.</p>
    <table className="cfg-table ct-grid"><thead><tr><th>Provider</th><th>Type</th><th>Servers / sessions</th><th>Accounts</th><th>Next step</th></tr></thead>
      <tbody>{providers.map(p => { const mine = conns.filter(c => c.provider_id === p.id), acc = mine.flatMap(c => c.accounts as Row[]);
        const next = !mine.length ? 'Add a server or session' : !acc.length ? 'Add an account' : acc.some(a => a.data_blockers.length) ? 'Fix account (see why)' : 'Collect symbols';
        return <tr key={p.id} onClick={() => select('p:' + p.id)} tabIndex={0} className="ct-click">
          <td><b>{p.name}</b> <small className="cfg-note">{p.code}</small></td><td>{data.provider_kinds[p.kind] ?? p.kind}</td>
          <td>{mine.map(c => `${TECH_SHORT[c.platform] ?? c.platform} ${where(c) ?? ''}`).join(', ') || '—'}</td><td>{acc.length}</td><td>{next}</td></tr>; })}</tbody></table>
    <Facts rows={[['Accounts', data.summary.accounts], ['Ready for data', data.summary.data_ready], ['Ready to trade', data.summary.trading_ready], ['With warnings', data.summary.with_warnings]]} />
  </>;
}

/** Step 1: the provider (counterparty) on its own. Its server or session is the next step. */
function AddProvider({ data, cancel, done }: { data: Row; cancel: () => void; done: (key?: string) => Promise<void> }) {
  const [f, setF] = useState<Row>({ name: '', code: '', kind: 'BROKER', legal_entity: '', regulator: '', licence_number: '', jurisdiction: '', website: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const set = (patch: Row) => setF(o => ({ ...o, ...patch }));
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError('');
    try { const r = await api('config/providers', 'POST', Object.fromEntries(Object.entries({ ...f, code: f.code || codeOf(f.name) }).filter(([, v]) => v !== '')));
      await done('p:' + r.id); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <form className="ct-add" onSubmit={submit} aria-label="Add provider">
    <Head level="Step 1 of 6 · Provider" title="Add provider" />
    <p className="cfg-note">The company you deal with. Next you add how COREI reaches it (its MT5 server, FIX session or API), then the account you hold there.</p>
    <div className="cfg-pair">
      <Field label="Name" hint="e.g. VT Markets, IC Markets, Upstox, LMAX Global"><input aria-label="Provider name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value })} /></Field>
      <Field label="Type"><Choice label="Provider type" value={f.kind} options={Object.entries(data.provider_kinds as Record<string, string>)} onChange={v => set({ kind: v })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="Code" hint="Short ID; filled in from the name"><input aria-label="Provider code" maxLength={32} placeholder={codeOf(f.name || 'Provider')} value={f.code} onChange={e => set({ code: e.target.value.toUpperCase() })} /></Field>
      <Field label="Legal entity" hint="Optional"><input aria-label="Legal entity" maxLength={200} value={f.legal_entity} onChange={e => set({ legal_entity: e.target.value })} /></Field>
    </div>
    <div className="cfg-pair">
      <Field label="Regulator" hint="Optional, e.g. FSCA, SEBI, FCA"><input aria-label="Regulator" maxLength={120} value={f.regulator} onChange={e => set({ regulator: e.target.value })} /></Field>
      <Field label="Licence number" hint="Optional"><input aria-label="Licence number" maxLength={80} value={f.licence_number} onChange={e => set({ licence_number: e.target.value })} /></Field>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="cfg-actions"><button type="button" onClick={cancel}>Cancel</button><button className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add provider · next: its server'}</button></div>
  </form>;
}

function TechPanel({ tech, data, canEdit, select, done, navigate }: { tech: Row; data: Row; canEdit: boolean; select: (k: string) => void; done: (k?: string) => Promise<void>; navigate: Navigate }) {
  const [adding, setAdding] = useState(false);
  return <>
    <Head level="Technology" title={tech.label}>
      {tech.platform && canEdit && <button className="primary" onClick={() => setAdding(true)} disabled={adding}>Add who</button>}
      {pill(tech.state)}
    </Head>
    <p className="cfg-note">{tech.summary}</p>
    <Facts rows={[['Who', tech.who_hint], ['Account', tech.account], ['Provider types', tech.kinds.map((k: string) => data.provider_kinds[k]).join(', ')]]} />
    {adding && <AddWho tech={tech} data={data} navigate={navigate} cancel={() => setAdding(false)} done={async key => { setAdding(false); await done(key); }} />}
    {!tech.platform && <p className="cfg-note ct-planned">Not built yet: nothing can be added here. {tech.id === 'FEED' ? 'A vendor that offers FIX can be added under FIX 4.4 / 4.2 now.' : tech.id === 'EXCHANGE' ? 'Exchanges and futures gateways that offer FIX can be added under FIX 4.4 / 4.2 now.' : ''}</p>}
    <h3 className="ct-sub">Adapters</h3>
    <table className="cfg-table"><thead><tr><th>Adapter</th><th>Prices</th><th>Orders</th><th>How it works</th><th>Login</th><th>Examples</th></tr></thead>
      <tbody>{tech.adapter_details.map((a: Row) => <tr key={a.id}><td>{a.label}</td><td>{pill(a.data)}</td><td>{pill(a.orders)}</td><td className="cfg-note">{a.how}</td>
        <td className="cfg-note">{a.credential}</td><td className="cfg-note">{a.examples}</td></tr>)}</tbody></table>
    <h3 className="ct-sub">Who ({tech.counts.who})</h3>
    {tech.counts.who === 0 ? <p className="cfg-empty">Nobody connected through {tech.label} yet.</p>
      : <table className="cfg-table"><thead><tr><th>Who</th><th>Type</th><th>Servers / sessions</th><th>Accounts</th></tr></thead>
        <tbody>{(data.technologies.find((t: Row) => t.id === tech.id)?.who ?? []).map((w: Row) => <tr key={w.provider.id} className="ct-click" tabIndex={0} onClick={() => select(`w:${tech.id}:${w.provider.id}`)}>
          <td><b>{w.provider.name}</b> <small className="cfg-note">{w.provider.code}</small></td><td>{data.provider_kinds[w.provider.kind]}</td>
          <td>{[...new Set(w.connections.map((c: Row) => where(c)))].join(', ')}</td><td>{w.connections.reduce((n: number, c: Row) => n + c.accounts.length, 0)}</td></tr>)}</tbody></table>}
  </>;
}

function TypePanel({ node, select, navigate, canEdit }: { node: Node; select: (k: string) => void; navigate: Navigate; canEdit: boolean }) {
  return <>
    <Head level="Provider type" title={node.label}>{canEdit && <button onClick={() => navigate('providers', 'new')}>New {node.label.toLowerCase()}</button>}</Head>
    {node.children.length === 0 ? <p className="cfg-empty">None yet. Add one from its technology (Add who), or as a record with New.</p>
      : <table className="cfg-table"><thead><tr><th>Who</th><th>Servers / sessions</th></tr></thead>
        <tbody>{node.children.map(w => <tr key={w.key} className="ct-click" tabIndex={0} onClick={() => select(w.key)}><td><b>{w.label}</b></td>
          <td className="cfg-note">{w.children.map(c => `${c.ref?.platform} · ${c.label}`).join(', ') || 'not connected yet'}</td></tr>)}</tbody></table>}
  </>;
}

function WhoPanel({ provider, data, canEdit, select, navigate, done }: { provider: Row; data: Row; canEdit: boolean; select: (k: string) => void; navigate: Navigate; done: (k?: string) => Promise<void> }) {
  const conns = (data.connections as Row[]).filter(c => c.provider_id === provider.id);
  const built = (data.technologies as Row[]).filter(t => t.platform && t.kinds.includes(provider.kind));
  const [tech, setTech] = useState<string>('');
  const t = (data.technologies as Row[]).find(x => x.id === tech);
  const accounts = conns.flatMap(c => c.accounts.map((a: Row) => ({ a, c })));
  return <>
    <Head level={`Step 1 · Provider · ${data.provider_kinds[provider.kind] ?? provider.kind}`} title={provider.name}>
      {canEdit && <Choice label="Add a server or session" value={tech} options={[['', '+ Add server / session…'], ...built.map(b => [b.id, b.id === 'MT5' ? 'MT5 server' : b.id === 'FIX' ? 'FIX session' : b.label] as [string, string])]} onChange={setTech} />}
      {canEdit && provider.kind === 'BROKER' && <button onClick={() => navigate('accounts', 'new:MT5P:' + provider.id)} title="Shortcut: an MT5 account and its server in one form">Add MT5 account</button>}
      <button onClick={() => navigate('providers', provider.id)}>Open record</button>
    </Head>
    {!conns.length && !t && <p className="cfg-ok">Next step: <b>Add server / session</b> — how COREI reaches {provider.name}{built.length ? ` (${built.map(b => b.id === 'MT5' ? 'MT5 server' : b.id === 'FIX' ? 'FIX session' : 'API').join(', ')})` : ''}.</p>}
    <Facts rows={[['Code', provider.code], ['Legal entity', provider.legal_entity], ['Regulator', [provider.regulator, provider.licence_number].filter(Boolean).join(' · ')],
      ['Jurisdiction', provider.jurisdiction], ['Execution model', provider.execution_model !== 'UNKNOWN' ? provider.execution_model : ''],
      ['Exposure limit', provider.exposure_limit ? `${provider.exposure_limit} ${provider.exposure_currency}` : ''], ['Status', provider.status]]} />
    {t && <AddWho tech={t} data={data} provider={provider} navigate={navigate} cancel={() => setTech('')} done={async key => { setTech(''); await done(key); }} />}
    <h3 className="ct-sub">Step 2 · Servers and sessions ({conns.length})</h3>
    {conns.length === 0 ? <p className="cfg-empty">None yet.</p>
      : <table className="cfg-table"><thead><tr><th>Technology</th><th>Server / session</th><th>Method</th><th>Role</th><th>Env</th><th>State</th><th>Accounts</th></tr></thead>
        <tbody>{conns.map(c => <tr key={c.id} className="ct-click" tabIndex={0} onClick={() => select('c:' + c.id)}><td>{TECH_SHORT[c.platform] ?? c.platform}</td>
          <td><b>{where(c)}</b> <small className="cfg-note">{c.code}</small></td>
          <td>{c.platform === 'MT5' ? (c.adapter === 'TERMINAL_BRIDGE' ? 'official terminal' : 'direct SDK') : c.platform === 'FIX' ? 'FIX session' : 'API'}</td>
          <td><span className={`cfg-role ${c.purpose}`}>{PURPOSE_LABEL[c.purpose]}</span></td><td><Env value={c.environment} /></td>
          <td><i className={`ct-dot ${connDot(c)}`} /> {c.live_status?.state ?? 'never connected'}</td><td>{c.accounts.length}</td></tr>)}</tbody></table>}
    <h3 className="ct-sub">Step 3 · Accounts ({accounts.length})</h3>
    {accounts.length === 0 ? <p className="cfg-empty">{conns.length ? 'Select a server above, then Add account.' : 'Add a server or session first.'}</p>
      : <table className="cfg-table"><thead><tr><th>Login / ID</th><th>Name</th><th>Server / session</th><th>Env</th><th>Route</th><th>Data</th></tr></thead>
        <tbody>{accounts.map(({ a, c }) => <tr key={a.id} className="ct-click" tabIndex={0} onClick={() => select('a:' + a.id)}>
          <td><b>{a.external_account_id}</b></td><td>{a.name}</td><td>{where(c)}</td><td><Env value={c.environment} /></td>
          <td>{c.platform === 'MT5' ? `${ROUTE_LABEL[a.access_mode] ?? a.access_mode}${fastest(a) ? ' · ' + fastest(a) : ''}` : '—'}</td>
          <td>{a.data_blockers.length ? <span className="cfg-pill warn" title={a.data_blockers.map(why).join(' · ')}>not ready</span> : <span className="cfg-pill ok">ready</span>}</td></tr>)}</tbody></table>}
  </>;
}

const ROUTE_LABEL: Record<string, string> = { FASTEST: 'fastest', SERVER: "server's order", ACCOUNT: 'pinned' };

function addAccountTarget(c: Row) {
  if (c.platform === 'MT5') return 'new:MT5:' + c.id;
  if (c.platform === 'REST_API' && /upstox/i.test(c.settings?.api_base_url ?? '')) return 'new:UPSTOX';
  return 'new:ACCOUNT:' + c.id;
}

function ConnPanel({ conn: c, data, canEdit, select, navigate }: { conn: Row; data: Row; canEdit: boolean; select: (k: string) => void; navigate: Navigate }) {
  const provider = (data.providers as Row[]).find(p => p.id === c.provider_id);
  const method = c.platform === 'MT5' ? (c.adapter === 'TERMINAL_BRIDGE' ? 'MT5 terminal bridge (fallback)' : 'Direct SDK') : c.platform === 'FIX' ? `${c.settings?.begin_string ?? 'FIX.4.4'} session` : 'Broker API';
  return <>
    <Head level={`Step 2 · Server / session · ${provider?.name ?? ''} · ${TECH_SHORT[c.platform] ?? c.platform}`} title={where(c) ?? c.code}>
      {canEdit && <button className="primary" onClick={() => navigate('accounts', addAccountTarget(c))}>Add account</button>}
      <button onClick={() => navigate('connections', c.id)}>Open record</button>
    </Head>
    <Facts rows={[['Code', c.code], ['Technology', c.platform], ['Method', method], ['Role', <span className={`cfg-role ${c.purpose}`}>{PURPOSE_LABEL[c.purpose]}</span>],
      ['Environment', <Env value={c.environment} />], ['State', <><i className={`ct-dot ${connDot(c)}`} /> {c.live_status?.state ?? 'never connected'}{c.live_status?.error ? ` · ${c.live_status.error}` : ''}</>],
      ['Endpoint', c.platform === 'MT5' ? `${c.settings?.access_points?.length ?? 0} address(es)${c.settings?.bridge ? ` · bridge ${c.settings.bridge.host}:${c.settings.bridge.port}` : ''}`
        : c.platform === 'FIX' ? `${c.settings?.host}:${c.settings?.port}${c.settings?.tls ? ' TLS' : ''} · ${c.settings?.sender_comp_id} → ${c.settings?.target_comp_id}${c.settings?.target_sub_id ? '/' + c.settings.target_sub_id : ''}` : c.settings?.api_base_url],
      ['Server clock', c.server_utc_offset_minutes == null ? 'not set (measured at first login)' : `UTC${c.server_utc_offset_minutes >= 0 ? '+' : ''}${c.server_utc_offset_minutes / 60}`],
      ['Order rate limit', c.max_orders_per_second == null ? 'none' : `${c.max_orders_per_second}/s`],
      ['Approval', c.pending_changes > 0 ? <span className="cfg-pill warn">change awaiting approval</span> : '']]} />
    <h3 className="ct-sub">Accounts ({c.accounts.length})</h3>
    {c.accounts.length === 0 ? <p className="cfg-ok">Next step: <b>Add account</b> — the {c.platform === 'MT5' ? 'MT5 login' : c.platform === 'FIX' ? 'FIX account' : 'client ID'} you hold on this {c.platform === 'FIX' ? 'session' : 'server'}.</p>
      : <table className="cfg-table"><thead><tr><th>Login / ID</th><th>Name</th><th>Permission</th><th>Data</th><th>Orders</th><th>Why not</th></tr></thead>
        <tbody>{c.accounts.map((a: Row) => <tr key={a.id} className="ct-click" tabIndex={0} onClick={() => select('a:' + a.id)}>
          <td><b>{a.external_account_id}</b></td><td>{a.name}</td><td>{a.trading_permission.replace('_', ' ').toLowerCase()}</td>
          <td>{a.data_blockers.length ? <span className="cfg-pill warn">not ready</span> : <span className="cfg-pill ok">ready</span>}</td>
          <td>{a.trading_permission !== 'TRADING' ? <span className="cfg-note">not used</span> : a.trading_blockers.length ? <span className="cfg-pill">{a.trading_blockers.length} blocker(s)</span> : <span className="cfg-pill ok">ready</span>}</td>
          <td className="cfg-reasons">{(a.data_blockers.length ? a.data_blockers : a.trading_permission === 'TRADING' ? a.trading_blockers : []).slice(0, 2).map(why).join(' · ')}</td></tr>)}</tbody></table>}
  </>;
}

function AcctPanel({ account: a, conn, data, canEdit, navigate, reload }: { account: Row; conn: Row; data: Row; canEdit: boolean; navigate: Navigate; reload: () => void }) {
  const provider = (data.providers as Row[]).find(p => p.id === conn.provider_id);
  const oauth = conn.platform === 'REST_API' && conn.settings?.auth_method === 'OAUTH2';
  return <>
    <Head level={`Step 3 · Account · ${provider?.name ?? ''} · ${where(conn) ?? ''}`} title={`${a.external_account_id} · ${a.name}`}>
      <button onClick={() => navigate('accounts', a.id)}>Open record</button>
    </Head>
    <AccountSteps account={a} conn={conn} />
    <Facts rows={[['Code', a.code], ['Holder', a.holder_name], ['Environment', <Env value={a.environment} />], ['Currency', a.base_currency],
      ['Permission', a.trading_permission.replace('_', ' ').toLowerCase()], ['Last login', a.last_login_at ? new Date(a.last_login_at).toLocaleString() : 'never']]} />
    <div className="ct-ready">
      <div><h3 className="ct-sub">Market data</h3>{a.data_blockers.length === 0 ? <p className="cfg-ok">Ready</p>
        : <ul>{a.data_blockers.map((b: string) => <li key={b}>{why(b)}</li>)}</ul>}</div>
      <div><h3 className="ct-sub">Orders</h3>{a.trading_permission !== 'TRADING' ? <p className="cfg-note">Not used ({a.trading_permission.replace('_', ' ').toLowerCase()})</p>
        : a.trading_blockers.length === 0 ? <p className="cfg-ok">Ready</p> : <ul>{a.trading_blockers.map((b: string) => <li key={b}>{why(b)}</li>)}</ul>}</div>
      {a.warnings?.length > 0 && <div><h3 className="ct-sub">Warnings</h3><ul>{a.warnings.map((w: string) => <li key={w}>{why(w)}</li>)}</ul></div>}
    </div>
    {conn.platform === 'MT5' && <ServerRoute key={a.id + a.version} account={a} conn={conn} provider={provider} canEdit={canEdit} reload={reload} />}
    {oauth ? <BrokerLogin account={a} canEdit={canEdit} reload={reload} /> : <BrokerRead account={a} canEdit={canEdit} onRead={reload} platform={conn.platform} inTree />}
  </>;
}

/** Steps 4–6 for one account: login saved, login tested, symbols collected. */
function AccountSteps({ account: a, conn }: { account: Row; conn: Row }) {
  const [src, setSrc] = useState<Row | null>(null);
  useEffect(() => { void api('md/sources').then(r => setSrc((r.items as Row[]).find(s => s.account_id === a.id) ?? {})).catch(() => setSrc({})); }, [a.id]);
  const missing = a.data_blockers.filter((b: string) => /CREDENTIAL|TOKEN|BRIDGE/.test(b));
  const daily = conn.platform === 'REST_API' && conn.settings?.auth_method === 'OAUTH2';
  const steps: [string, boolean, string][] = [
    ['4 · Login saved', !missing.length, missing.length ? missing.map(why).join(' · ') : daily ? 'API key saved; log in at the broker once a day (below)' : 'Saved (write-only, encrypted)'],
    ['5 · Login tested', !!a.last_login_at, a.last_login_at ? `Last login ${new Date(a.last_login_at).toLocaleString()}` : 'Use “Read account” / “Log in at broker” below'],
    ['6 · Collecting', !!src?.active_plans, src == null ? '…' : src.active_plans ? `${src.active_plans} symbol(s) · collector ${String(src.collector_state ?? 'not started').toLowerCase()}` : 'No symbol picked yet'],
  ];
  return <section className="ct-steps" aria-label="Account set-up steps">
    {steps.map(([t, ok, d]) => <div key={t} className={ok ? 'ok' : 'todo'}><b>{ok ? '✓' : '○'} {t}</b><span>{d}</span></div>)}
    <button onClick={() => goto('Market data', 'dashboard')} title="Market data › Dashboard › this source › Available">Pick symbols to collect →</button>
  </section>;
}

/* ---------- MT5: an account's server and the address it connects through ---------- */

function ServerRoute({ account: a, conn, provider, canEdit, reload }: { account: Row; conn: Row; provider?: Row; canEdit: boolean; reload: () => void }) {
  const [mode, setMode] = useState<string>(a.access_mode ?? 'FASTEST'), [own, setOwn] = useState<Row[]>(a.access_points ?? []);
  const [busy, setBusy] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [moving, setMoving] = useState(false), [round, setRound] = useState(0);
  const dirty = mode !== a.access_mode || JSON.stringify(own) !== JSON.stringify(a.access_points ?? []);
  const points: Row[] = conn.settings?.access_points ?? [];
  const bridge = conn.adapter === 'TERMINAL_BRIDGE';
  const first = a.routing?.addresses?.[0];
  async function run(label: string, fn: () => Promise<string | void>) {
    setBusy(label); setError(''); setNotice('');
    try { const msg = await fn(); if (msg) setNotice(msg); setRound(r => r + 1); reload(); } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  }
  const save = () => run('save', async () => {
    const rec = await api(`config/accounts/${a.id}`);
    const r = await api(`config/accounts/${a.id}`, 'PUT', { ...toPayload('accounts', rec), access_mode: mode, access_points: own.map(p => ({ host: p.host, port: Number(p.port) })), version: rec.version });
    return pendingMessage(r) || 'Saved.';
  });
  const measure = () => run('measure', async () => { await api(`config/accounts/${a.id}/mt5-latency`, 'POST', {}); return 'Every address measured again from the COREI server.'; });
  const refresh = () => run('refresh', async () => {
    const r = await api(`config/accounts/${a.id}/mt5-addresses/refresh`, 'POST', {});
    const parts = [`${r.published} address(es) published by the broker for ${r.server_name}`,
      r.added ? `${r.added} new added and measured` : r.awaiting_approval ? `${r.awaiting_approval} new waiting for approval (LIVE)` : 'none new',
      r.not_listed.length ? `${r.not_listed.length} on record no longer listed (${r.not_listed.map((p: Row) => `${p.host}:${p.port}`).join(', ')}): untick them on the server record if they stop answering` : ''];
    return parts.filter(Boolean).join('; ') + '.';
  });
  return <section className="ct-route" aria-label="Server and route">
    <div className="ct-route-head">
      <h3 className="ct-sub">Server &amp; route</h3>
      {canEdit && !bridge && <span className="ct-actions">
        <button onClick={() => void measure()} disabled={!!busy} title="TCP connect time to every enabled address, from the COREI server">{busy === 'measure' ? 'Measuring…' : 'Measure now'}</button>
        <button onClick={() => void refresh()} disabled={!!busy} title="Addresses the broker publishes now for this server (MT5 broker directory)">{busy === 'refresh' ? 'Asking broker…' : 'Refresh from broker'}</button>
        <button onClick={() => setMoving(m => !m)} disabled={!!busy} aria-expanded={moving}>Change server…</button></span>}
    </div>
    <Facts rows={[['Server', <b>{conn.settings?.server_name}</b>], ['Environment', <Env value={conn.environment} />],
      ['Method', bridge ? 'MT5 terminal bridge: the terminal picks the address' : 'Direct SDK'],
      ['Addresses', `${points.filter(p => p.enabled !== false).length} in use of ${points.length} on record`],
      ['Connects first to', first ? <span className="cfg-mono">{first.host}:{first.port} {fastest(a) && <small className="cfg-note">({fastest(a)}{first.measured_at ? `, ${new Date(first.measured_at).toLocaleString()}` : ''})</small>}</span> : '—'],
      ['Route', ROUTE_LABEL[a.access_mode] ?? a.access_mode]]} />
    {moving && <ChangeServer account={a} conn={conn} provider={provider} close={() => setMoving(false)} done={msg => { setMoving(false); setNotice(msg); reload(); }} />}
    {!bridge && <AccountAddresses key={round} mode={mode} own={own} serverPoints={points} connectionId={conn.id} routing={a.routing} change={(m, o) => { setMode(m); setOwn(o); }} />}
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    {canEdit && dirty && <div className="cfg-actions"><button onClick={() => { setMode(a.access_mode); setOwn(a.access_points ?? []); }}>Undo</button>
      <button className="primary" disabled={!!busy || (mode === 'ACCOUNT' && !own.length)} onClick={() => void save()}>{busy === 'save' ? 'Saving…' : 'Save route'}</button></div>}
  </section>;
}

/** Put the account on another server of its broker, picked from the broker's published list (or typed). */
function ChangeServer({ account: a, conn, provider, close, done }: { account: Row; conn: Row; provider?: Row; close: () => void; done: (msg: string) => void }) {
  const [query, setQuery] = useState((conn.settings?.server_name ?? '').split('-')[0] || provider?.name || '');
  const [catalog, setCatalog] = useState(''), [servers, setServers] = useState<Row[]>([]), [filter, setFilter] = useState('');
  const [picked, setPicked] = useState<Row | null>(null), [manual, setManual] = useState({ name: '', addresses: '' }), [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function search(e?: FormEvent) {
    e?.preventDefault(); setBusy(true); setError(''); setPicked(null);
    try { const found = await api('config/mt5/broker-search', 'POST', { query }); setCatalog(found.catalog_id);
      setServers((await api(`config/mt5/server-file/${found.catalog_id}/servers?limit=500`)).items); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function pick(name: string) {
    setBusy(true); setError('');
    try { setPicked(await api(`config/mt5/server-file/${catalog}/server?name=${encodeURIComponent(name)}`)); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const sameEnv = (name: string) => { const env = envOfServer(name); return !env || env === conn.environment; };
  async function move() {
    const server = picked ? { server_name: picked.server_name, access_points: picked.access_points.map((p: Row) => ({ name: p.name || picked.server_name, host: p.host, port: p.port, enabled: true, source: 'DIRECTORY' })) }
      : { server_name: manual.name.trim(), access_points: manual.addresses.split(/[\s,;]+/).filter(Boolean).map(x => { const m = x.match(/^(.+?)(?::(\d+))?$/)!; return { name: manual.name.trim(), host: m[1], port: Number(m[2] ?? 443), enabled: true, source: 'MANUAL' }; }) };
    setBusy(true); setError('');
    try { const r = await api(`config/accounts/${a.id}/mt5-server`, 'POST', { server, version: a.version, reason });
      done(pendingMessage(r) || `Moved to ${server.server_name}${r.connection_action === 'CREATED' ? ' (new server record)' : ''}. Its addresses are measured at the next login; use Measure now to see them.`); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  const shown = servers.filter(x => !filter || `${x.server_name} ${x.company_name ?? ''}`.toLowerCase().includes(filter.toLowerCase()));
  const target = picked?.server_name ?? manual.name.trim();
  return <div className="ct-add" role="group" aria-label="Change server">
    <h3 className="ct-sub">Change server of login {a.external_account_id}</h3>
    <p className="cfg-note">Use when the broker moved this login to another server. The account keeps its password, groups and history; {conn.environment === 'LIVE' ? 'LIVE moves need approval.' : 'demo moves apply at once.'} For faster access on the same server, choose addresses below instead.</p>
    <form className="ct-inline" onSubmit={search}><input aria-label="Broker to search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Broker name, e.g. VTMarkets" />
      <button disabled={busy || query.trim().length < 2}>{busy && !servers.length ? 'Searching…' : 'Find servers'}</button>
      {servers.length > 0 && <input aria-label="Filter servers" placeholder={`Filter ${servers.length} servers…`} value={filter} onChange={e => setFilter(e.target.value)} />}</form>
    {servers.length > 0 && <div className="ct-serverlist" role="listbox" aria-label="Broker servers">
      {shown.map(x => <button key={x.server_name} type="button" role="option" aria-selected={picked?.server_name === x.server_name} disabled={!sameEnv(x.server_name)}
        className={x.server_name === conn.settings?.server_name ? 'current' : ''} onClick={() => void pick(x.server_name)}
        title={sameEnv(x.server_name) ? '' : `A ${conn.environment.toLowerCase()} login cannot move to a ${envOfServer(x.server_name)?.toLowerCase()} server`}>
        <span>{x.server_name}</span><small>{x.access_point_count} addr{x.server_name === conn.settings?.server_name ? ' · current' : ''}</small></button>)}
    </div>}
    {!servers.length && <div className="cfg-pair">
      <Field label="Or type the server name"><input aria-label="New server name" value={manual.name} onChange={e => setManual({ ...manual, name: e.target.value })} placeholder="VTMarkets-Live 5" /></Field>
      <Field label="Addresses" hint="host:port, comma-separated"><input aria-label="New server addresses" value={manual.addresses} onChange={e => setManual({ ...manual, addresses: e.target.value })} /></Field>
    </div>}
    {picked && <p className="cfg-note">{picked.server_name}: {picked.access_points.length} address(es) from the broker directory{picked.company_name ? ` · ${picked.company_name}` : ''}.</p>}
    <Field label="Reason" hint="Kept in the audit log"><input aria-label="Reason for the move" maxLength={300} value={reason} onChange={e => setReason(e.target.value)} placeholder="Broker moved the login" /></Field>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="cfg-actions"><button type="button" onClick={close}>Cancel</button>
      <button className="primary" type="button" disabled={busy || !target || target === conn.settings?.server_name || (!picked && !manual.addresses.trim())} onClick={() => void move()}>{target ? `Move to ${target}` : 'Move'}</button></div>
  </div>;
}

/** MT5 servers of a broker, from the broker directory (servers.dat); or typed by hand. */
function Mt5ServerPicker({ start, picked, pick }: { start: string; picked: Row | null; pick: (s: Row | null) => void }) {
  const [query, setQuery] = useState(start.replace(/\s+/g, '')), [catalog, setCatalog] = useState(''), [servers, setServers] = useState<Row[]>([]), [filter, setFilter] = useState('');
  const [manual, setManual] = useState({ name: '', addresses: '' }), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function search() {
    setBusy(true); setError(''); pick(null);
    try { const found = await api('config/mt5/broker-search', 'POST', { query }); setCatalog(found.catalog_id);
      setServers((await api(`config/mt5/server-file/${found.catalog_id}/servers?limit=500`)).items); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  async function choose(name: string) {
    setBusy(true); setError('');
    try { pick(await api(`config/mt5/server-file/${catalog}/server?name=${encodeURIComponent(name)}`)); } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  function typed(name: string, addresses: string) {
    setManual({ name, addresses });
    const points = addresses.split(/[\s,;]+/).filter(Boolean).map(x => { const m = x.match(/^(.+?)(?::(\d+))?$/)!; return { name, host: m[1], port: Number(m[2] ?? 443), source: 'MANUAL' }; });
    pick(name.trim() && points.length ? { server_name: name.trim(), access_points: points } : null);
  }
  const shown = servers.filter(x => !filter || `${x.server_name} ${x.company_name ?? ''}`.toLowerCase().includes(filter.toLowerCase()));
  return <div className="ct-picker">
    <div className="ct-inline"><input aria-label="Broker to search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Broker name, e.g. VTMarkets"
      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void search(); } }} />
      <button type="button" disabled={busy || query.trim().length < 2} onClick={() => void search()}>{busy && !servers.length ? 'Searching…' : 'Find its MT5 servers'}</button>
      {servers.length > 0 && <input aria-label="Filter servers" placeholder={`Filter ${servers.length} servers…`} value={filter} onChange={e => setFilter(e.target.value)} />}</div>
    {servers.length > 0 && <div className="ct-serverlist" role="listbox" aria-label="Broker servers">
      {shown.map(x => <button key={x.server_name} type="button" role="option" aria-selected={picked?.server_name === x.server_name} onClick={() => void choose(x.server_name)}>
        <span>{x.server_name}</span><small>{x.access_point_count} addr · {envOfServer(x.server_name)?.toLowerCase() ?? '?'}</small></button>)}
    </div>}
    {!servers.length && <div className="cfg-pair">
      <Field label="Or type the server name"><input aria-label="MT5 server name" value={manual.name} onChange={e => typed(e.target.value, manual.addresses)} placeholder="e.g. Exness-MT5Real8" /></Field>
      <Field label="Addresses" hint="host:port, comma-separated"><input aria-label="MT5 server addresses" value={manual.addresses} onChange={e => typed(manual.name, e.target.value)} /></Field>
    </div>}
    {picked && <p className="cfg-ok">{picked.server_name}: {picked.access_points.length} address(es){picked.company_name ? ` · ${picked.company_name}` : ''}. Each account on it connects to the fastest address by default.</p>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
  </div>;
}

/* ---------- add who: provider (existing or new) + its server / session / API, one step ---------- */

function AddWho({ tech, data, provider, navigate, cancel, done }: { tech: Row; data: Row; provider?: Row; navigate: Navigate; cancel: () => void; done: (key?: string) => Promise<void> }) {
  const [f, setF] = useState<Row>({ provider_id: provider?.id ?? '', name: '', code: '', kind: tech.kinds[0], environment: 'DEMO', purpose: tech.id === 'FIX' ? 'MARKET_DATA' : 'BOTH',
    adapter: 'NATIVE', settings: tech.id === 'FIX' ? { ...FIX_BLANK } : { api_base_url: '', auth_method: 'API_KEY' } });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const set = (patch: Row) => setF(o => ({ ...o, ...patch }));
  const providers = (data.providers as Row[]).filter(p => p.status !== 'ARCHIVED');
  const isNew = !provider && f.provider_id === '';
  const upstox = tech.id === 'API';
  const [server, setServer] = useState<Row | null>(null);
  function settings(): Row {
    if (tech.id === 'MT5') return { server_name: server!.server_name, ...(server!.company_name ? { company_name: server!.company_name } : {}),
      access_points: server!.access_points.map((p: Row) => ({ name: p.name || server!.server_name, host: p.host, port: Number(p.port), enabled: true, source: p.source ?? 'DIRECTORY' })) };
    if (tech.id === 'FIX') { const s = f.settings; return { ...s, port: Number(s.port), heartbeat_seconds: Number(s.heartbeat_seconds), md_depth: Number(s.md_depth) }; }
    return { api_base_url: f.settings.api_base_url, auth_method: f.settings.auth_method, ...(f.settings.auth_method === 'OAUTH2' ? { oauth_redirect_url: f.settings.oauth_redirect_url } : {}) };
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setNotice('');
    if (tech.id === 'MT5' && !server) { setError('Pick the MT5 server from the broker list, or type its name and addresses.'); setBusy(false); return; }
    try {
      const body = { ...(isNew ? { provider: { name: f.name, code: f.code || codeOf(f.name), kind: f.kind } } : { provider_id: provider?.id ?? f.provider_id }),
        connection: { platform: tech.platform, environment: f.environment, purpose: f.purpose, adapter: tech.id === 'MT5' ? 'DEFAULT' : f.adapter, settings: settings() } };
      const r = await api('config/venues', 'POST', body);
      const pending = pendingMessage(r);
      if (pending) { setNotice(pending); await done(`w:${tech.id}:${r.provider.id}`); return; }
      await done('c:' + r.connection.id);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <form className="ct-add" onSubmit={submit} aria-label={`Add who through ${tech.label}`}>
    <h3 className="ct-sub">{provider ? `Step 2 · Add ${tech.id === 'MT5' ? 'an MT5 server' : tech.id === 'FIX' ? 'a FIX session' : 'an API'} for ${provider.name}` : `Add provider and its ${tech.id === 'MT5' ? 'MT5 server' : tech.label} `}</h3>
    {upstox && <p className="cfg-note">Upstox: use <b>Add account</b> on Upstox, or Accounts › Add Upstox account: it creates the broker, the API and the account in one step. Other broker and exchange APIs: describe the API here.</p>}
    {!provider && <div className="cfg-pair">
      <Field label="Who"><Choice label="Who" value={f.provider_id} options={[['', '+ New provider'], ...providers.map(p => [p.id, `${p.name} (${data.provider_kinds[p.kind] ?? p.kind})`] as [string, string])]} onChange={v => set({ provider_id: v })} /></Field>
      {isNew && tech.id !== 'MT5' && <Field label="Type"><Choice label="Provider type" value={f.kind} options={Object.entries(data.provider_kinds as Record<string, string>)} onChange={v => set({ kind: v })} /></Field>}
    </div>}
    {isNew && <div className="cfg-pair">
      <Field label="Name" hint="e.g. VT Markets, LMAX Global, Equinix LD4"><input aria-label="Provider name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value })} /></Field>
      <Field label="Code" hint="Short ID; filled in from the name"><input aria-label="Provider code" maxLength={32} placeholder={codeOf(f.name || 'Provider')} value={f.code} onChange={e => set({ code: e.target.value.toUpperCase() })} /></Field>
    </div>}
    {tech.id === 'MT5' && <Mt5ServerPicker start={provider?.name ?? f.name} picked={server} pick={x => { setServer(x); const env = x ? envOfServer(x.server_name) : null; if (env) set({ environment: env }); }} />}
    {tech.id === 'FIX' && <FixFields settings={f.settings} change={s => set({ settings: s })} />}
    {tech.id === 'API' && <div className="cfg-pair">
      <Field label="API base URL"><input aria-label="API base URL" required type="url" placeholder="https://api.example.com" value={f.settings.api_base_url} onChange={e => set({ settings: { ...f.settings, api_base_url: e.target.value } })} /></Field>
      <Field label="Login"><Choice label="API login method" value={f.settings.auth_method} options={[['API_KEY', 'API key and secret'], ['OAUTH2', 'OAuth (daily login)']]} onChange={v => set({ settings: { ...f.settings, auth_method: v } })} /></Field>
      {f.settings.auth_method === 'OAUTH2' && <Field label="Redirect URL"><input aria-label="OAuth redirect URL" required type="url" value={f.settings.oauth_redirect_url ?? ''} onChange={e => set({ settings: { ...f.settings, oauth_redirect_url: e.target.value } })} /></Field>}
    </div>}
    {<div className="cfg-pair">
      <Field label="Environment"><Choice label="Environment" value={f.environment} options={[['DEMO', 'Demo'], ['LIVE', 'Live']]} onChange={v => set({ environment: v })} /></Field>
      <Field label="Role" hint="Data source: prices are taken from it. Execution: trading accounts"><Choice label="Role" value={f.purpose} options={ROLE_OPTIONS} onChange={v => set({ purpose: v })} /></Field>
    </div>}
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="cfg-actions"><button type="button" onClick={cancel}>Cancel</button><button className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add · next: its account'}</button></div>
    <p className="cfg-note">Then select the new {tech.id === 'FIX' ? 'session' : 'server'} and use <b>Add account</b>.{tech.id === 'MT5' ? '' : ` Save the ${tech.id === 'FIX' ? 'session login (if the venue uses one)' : 'API key and secret'} under the record's Credential.`}</p>
  </form>;
}
