/*
 * Connectivity: one tree for every source of prices and every trading account.
 *   1 Technology (MT5, MT4, cTrader, FIX, broker & exchange APIs, fast feeds, exchange direct)
 *   2 Who (the broker, exchange, ECN, prime broker, feed or data centre) › its server, session or API
 *   3 Accounts
 * Left: the tree (keyboard: arrows, Enter). Right: the selected item with what can be done there.
 * The same tree can be grouped by provider type instead. Data: GET /api/config/tree.
 */
import { useEffect, useMemo, useState, type FormEvent, type KeyboardEvent } from 'react';
import { api, BLOCKERS, BridgeFields, Choice, Env, envOfServer, Field, FIX_BLANK, FixFields, pendingMessage, PURPOSE_LABEL, ROLE_OPTIONS,
  type Navigate, type Row } from './Configuration';
import { BrokerLogin, BrokerRead } from './ConfigControls';

type Node = { key: string; kind: 'tech' | 'type' | 'who' | 'conn' | 'acct' | 'none'; label: string; meta?: string; dot?: string; state?: string;
  children: Node[]; ref?: Row };
const PREF = 'corei-os-mini.connect-tree';
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

function connNode(c: Row, withTech = false): Node {
  return { key: 'c:' + c.id, kind: 'conn', ref: c, dot: connDot(c), label: where(c) ?? c.code,
    meta: `${withTech ? c.platform + ' · ' : ''}${SHORT_ROLE[c.purpose] ?? c.purpose} · ${c.environment}`,
    children: c.accounts.map((a: Row) => ({ key: 'a:' + a.id, kind: 'acct' as const, ref: a, dot: acctDot(a), label: a.external_account_id || a.code,
      meta: `${a.name}${a.trading_permission === 'READ_ONLY' ? ' · read only' : a.trading_permission === 'DISABLED' ? ' · disabled' : ''}`, children: [] })) };
}

function build(data: Row, group: string): Node[] {
  if (group === 'type') {
    const kinds = data.provider_kinds as Record<string, string>;
    return Object.entries(kinds).map(([k, label]) => {
      const who = (data.providers as Row[]).filter(p => p.kind === k).map(p => {
        const conns = (data.connections as Row[]).filter(c => c.provider_id === p.id).map(c => connNode(c, true));
        return { key: 'p:' + p.id, kind: 'who' as const, ref: p, label: p.name, meta: `${conns.length} server/session(s)`, dot: worst(conns.map(c => c.dot!)), children: conns };
      });
      return { key: 'k:' + k, kind: 'type' as const, label, meta: `${who.length}`, dot: worst(who.map(w => w.dot!)), children: who, ref: { kind: k, label } };
    });
  }
  const nodes: Node[] = (data.technologies as Row[]).map(t => {
    const who = (t.who as Row[]).map(w => {
      const conns = w.connections.map((c: Row) => connNode(c));
      return { key: `w:${t.id}:${w.provider.id}`, kind: 'who' as const, ref: { ...w.provider, technology: t.id }, label: w.provider.name,
        meta: `${data.provider_kinds[w.provider.kind] ?? w.provider.kind}`, dot: worst(conns.map((c: Node) => c.dot!)), children: conns };
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
  const [pref, setPref] = useState<Row>(() => ({ group: 'tech', open: ['t:MT5', 't:FIX', 't:API'], ...readPref() }));
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
          <button aria-pressed={pref.group === 'tech'} onClick={() => save({ group: 'tech' })} title="Technology › who › server › accounts">Technology</button>
          <button aria-pressed={pref.group === 'type'} onClick={() => save({ group: 'type' })} title="Broker, exchange, ECN, feed … › who › server › accounts">Provider type</button>
        </div>
        <input aria-label="Filter the tree" placeholder="Filter…" value={filter} onChange={e => setFilter(e.target.value)} />
        <button title="Expand all" aria-label="Expand all" onClick={() => save({ open: [...new Set([...open, ...index.keys()])] })}>+</button>
        <button title="Collapse all" aria-label="Collapse all" onClick={() => save({ open: [] })}>−</button>
      </div>
      <div className="ct-head"><span>{pref.group === 'tech' ? 'Technology › who › server › account' : 'Provider type › who › server › account'}</span></div>
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
      {!sel ? <Overview data={data} select={select} />
        : sel.node.kind === 'tech' ? <TechPanel key={sel.node.key} tech={sel.node.ref!} data={data} canEdit={canEdit} select={select} done={done} />
        : sel.node.kind === 'type' ? <TypePanel node={sel.node} select={select} navigate={navigate} canEdit={canEdit} />
        : sel.node.kind === 'who' ? <WhoPanel key={sel.node.key} provider={sel.node.ref!} data={data} canEdit={canEdit} select={select} navigate={navigate} done={done} />
        : sel.node.kind === 'conn' ? <ConnPanel key={sel.node.key} conn={sel.node.ref!} data={data} canEdit={canEdit} select={select} navigate={navigate} />
        : sel.node.kind === 'acct' ? <AcctPanel key={sel.node.key} account={sel.node.ref!} conn={sel.parents.at(-1)?.ref ?? {}} canEdit={canEdit} navigate={navigate} reload={() => void load()} />
        : <Overview data={data} select={select} />}
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

function Overview({ data, select }: { data: Row; select: (k: string) => void }) {
  return <>
    <Head level="Connectivity" title="Every source of prices and every trading account" />
    <p className="cfg-note">Pick a technology on the left, then <b>Add who</b> (the broker, exchange, ECN, prime broker, feed or data centre and its server or session), then <b>Add account</b> on that server. Prices are taken from <i>data source</i> connections; signals go to <i>execution</i> accounts.</p>
    <table className="cfg-table ct-grid"><thead><tr><th>Technology</th><th>State</th><th>Who</th><th>Servers / sessions</th><th>Accounts</th><th>Typical providers</th></tr></thead>
      <tbody>{(data.technologies as Row[]).map(t => <tr key={t.id} onClick={() => select('t:' + t.id)} tabIndex={0} className="ct-click">
        <td><b>{t.label}</b></td><td>{pill(t.state)}</td><td>{t.counts.who}</td><td>{t.counts.connections}</td><td>{t.counts.accounts}</td>
        <td className="cfg-note">{t.kinds.map((k: string) => data.provider_kinds[k]).join(', ')}</td></tr>)}</tbody></table>
    <Facts rows={[['Accounts', data.summary.accounts], ['Ready for data', data.summary.data_ready], ['Ready to trade', data.summary.trading_ready], ['With warnings', data.summary.with_warnings]]} />
  </>;
}

function TechPanel({ tech, data, canEdit, select, done }: { tech: Row; data: Row; canEdit: boolean; select: (k: string) => void; done: (k?: string) => Promise<void> }) {
  const [adding, setAdding] = useState(false);
  return <>
    <Head level="Technology" title={tech.label}>
      {tech.platform && canEdit && <button className="primary" onClick={() => setAdding(true)} disabled={adding}>Add who</button>}
      {pill(tech.state)}
    </Head>
    <p className="cfg-note">{tech.summary}</p>
    <Facts rows={[['Who', tech.who_hint], ['Account', tech.account], ['Provider types', tech.kinds.map((k: string) => data.provider_kinds[k]).join(', ')]]} />
    {adding && <AddWho tech={tech} data={data} cancel={() => setAdding(false)} done={async key => { setAdding(false); await done(key); }} />}
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
          <td>{w.connections.map((c: Row) => where(c)).join(', ')}</td><td>{w.connections.reduce((n: number, c: Row) => n + c.accounts.length, 0)}</td></tr>)}</tbody></table>}
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
  const built = (data.technologies as Row[]).filter(t => t.platform);
  const [tech, setTech] = useState<string>(provider.technology ?? '');
  const t = built.find(x => x.id === tech);
  return <>
    <Head level={`Who · ${data.provider_kinds[provider.kind] ?? provider.kind}`} title={provider.name}>
      {canEdit && <><Choice label="Add server or session through" value={tech} options={[['', 'Add server / session…'], ...built.map(b => [b.id, b.label] as [string, string])]} onChange={setTech} />
        {conns.some(c => c.platform === 'MT5') && <button onClick={() => navigate('accounts', 'new:MT5:' + conns.find(c => c.platform === 'MT5')!.id)}>Add MT5 account</button>}</>}
      <button onClick={() => navigate('providers', provider.id)}>Open record</button>
    </Head>
    <Facts rows={[['Code', provider.code], ['Legal entity', provider.legal_entity], ['Regulator', [provider.regulator, provider.licence_number].filter(Boolean).join(' · ')],
      ['Jurisdiction', provider.jurisdiction], ['Execution model', provider.execution_model !== 'UNKNOWN' ? provider.execution_model : ''],
      ['Exposure limit', provider.exposure_limit ? `${provider.exposure_limit} ${provider.exposure_currency}` : ''], ['Status', provider.status]]} />
    {t && <AddWho tech={t} data={data} provider={provider} cancel={() => setTech('')} done={async key => { setTech(''); await done(key); }} />}
    <h3 className="ct-sub">Servers, sessions and APIs ({conns.length})</h3>
    {conns.length === 0 ? <p className="cfg-empty">Not connected yet: choose a technology above.</p>
      : <table className="cfg-table"><thead><tr><th>Technology</th><th>Server / session</th><th>Role</th><th>Env</th><th>State</th><th>Accounts</th></tr></thead>
        <tbody>{conns.map(c => <tr key={c.id} className="ct-click" tabIndex={0} onClick={() => select('c:' + c.id)}><td>{c.platform}</td><td><b>{where(c)}</b> <small className="cfg-note">{c.code}</small></td>
          <td><span className={`cfg-role ${c.purpose}`}>{PURPOSE_LABEL[c.purpose]}</span></td><td><Env value={c.environment} /></td>
          <td><i className={`ct-dot ${connDot(c)}`} /> {c.live_status?.state ?? 'never connected'}</td><td>{c.accounts.length}</td></tr>)}</tbody></table>}
  </>;
}

function addAccountTarget(c: Row) {
  if (c.platform === 'MT5') return 'new:MT5:' + c.id;
  if (c.platform === 'REST_API' && /upstox/i.test(c.settings?.api_base_url ?? '')) return 'new:UPSTOX';
  return 'new:ACCOUNT:' + c.id;
}

function ConnPanel({ conn: c, data, canEdit, select, navigate }: { conn: Row; data: Row; canEdit: boolean; select: (k: string) => void; navigate: Navigate }) {
  const provider = (data.providers as Row[]).find(p => p.id === c.provider_id);
  const method = c.platform === 'MT5' ? (c.adapter === 'TERMINAL_BRIDGE' ? 'MT5 terminal bridge (fallback)' : 'Direct SDK') : c.platform === 'FIX' ? `${c.settings?.begin_string ?? 'FIX.4.4'} session` : 'Broker API';
  return <>
    <Head level={`Server / session · ${provider?.name ?? ''}`} title={where(c) ?? c.code}>
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
    {c.accounts.length === 0 ? <p className="cfg-empty">No account on this server yet.</p>
      : <table className="cfg-table"><thead><tr><th>Login / ID</th><th>Name</th><th>Permission</th><th>Data</th><th>Orders</th><th>Why not</th></tr></thead>
        <tbody>{c.accounts.map((a: Row) => <tr key={a.id} className="ct-click" tabIndex={0} onClick={() => select('a:' + a.id)}>
          <td><b>{a.external_account_id}</b></td><td>{a.name}</td><td>{a.trading_permission.replace('_', ' ').toLowerCase()}</td>
          <td>{a.data_blockers.length ? <span className="cfg-pill warn">not ready</span> : <span className="cfg-pill ok">ready</span>}</td>
          <td>{a.trading_permission !== 'TRADING' ? <span className="cfg-note">not used</span> : a.trading_blockers.length ? <span className="cfg-pill">{a.trading_blockers.length} blocker(s)</span> : <span className="cfg-pill ok">ready</span>}</td>
          <td className="cfg-reasons">{(a.data_blockers.length ? a.data_blockers : a.trading_permission === 'TRADING' ? a.trading_blockers : []).slice(0, 2).map(why).join(' · ')}</td></tr>)}</tbody></table>}
  </>;
}

function AcctPanel({ account: a, conn, canEdit, navigate, reload }: { account: Row; conn: Row; canEdit: boolean; navigate: Navigate; reload: () => void }) {
  const oauth = conn.platform === 'REST_API' && conn.settings?.auth_method === 'OAUTH2';
  return <>
    <Head level={`Account · ${where(conn) ?? ''}`} title={`${a.external_account_id} · ${a.name}`}>
      <button onClick={() => navigate('accounts', a.id)}>Open record</button>
    </Head>
    <Facts rows={[['Code', a.code], ['Holder', a.holder_name], ['Environment', <Env value={a.environment} />], ['Currency', a.base_currency],
      ['Permission', a.trading_permission.replace('_', ' ').toLowerCase()], ['Last login', a.last_login_at ? new Date(a.last_login_at).toLocaleString() : 'never']]} />
    <div className="ct-ready">
      <div><h3 className="ct-sub">Market data</h3>{a.data_blockers.length === 0 ? <p className="cfg-ok">Ready</p>
        : <ul>{a.data_blockers.map((b: string) => <li key={b}>{why(b)}</li>)}</ul>}</div>
      <div><h3 className="ct-sub">Orders</h3>{a.trading_permission !== 'TRADING' ? <p className="cfg-note">Not used ({a.trading_permission.replace('_', ' ').toLowerCase()})</p>
        : a.trading_blockers.length === 0 ? <p className="cfg-ok">Ready</p> : <ul>{a.trading_blockers.map((b: string) => <li key={b}>{why(b)}</li>)}</ul>}</div>
      {a.warnings?.length > 0 && <div><h3 className="ct-sub">Warnings</h3><ul>{a.warnings.map((w: string) => <li key={w}>{why(w)}</li>)}</ul></div>}
    </div>
    {oauth ? <BrokerLogin account={a} canEdit={canEdit} reload={reload} /> : <BrokerRead account={a} canEdit={canEdit} onRead={reload} platform={conn.platform} inTree />}
  </>;
}

/* ---------- add who: provider (existing or new) + its server / session / API, one step ---------- */

function AddWho({ tech, data, provider, cancel, done }: { tech: Row; data: Row; provider?: Row; cancel: () => void; done: (key?: string) => Promise<void> }) {
  const [f, setF] = useState<Row>({ provider_id: provider?.id ?? '', name: '', code: '', kind: tech.kinds[0], environment: 'DEMO', purpose: tech.id === 'FIX' ? 'MARKET_DATA' : 'BOTH',
    adapter: tech.id === 'MT5' ? 'MTAPI_DIRECT' : 'NATIVE', settings: tech.id === 'FIX' ? { ...FIX_BLANK } : tech.id === 'MT5' ? { server_name: '' } : { api_base_url: '', auth_method: 'API_KEY' },
    addresses: '' });
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const set = (patch: Row) => setF(o => ({ ...o, ...patch }));
  const providers = (data.providers as Row[]).filter(p => p.status !== 'ARCHIVED');
  const isNew = !provider && f.provider_id === '';
  const upstox = tech.id === 'API';
  function settings(): Row {
    if (tech.id === 'MT5') {
      const points = f.addresses.split(/[\s,;]+/).filter(Boolean).map((x: string) => { const m = x.match(/^(.+?)(?::(\d+))?$/)!; return { name: f.settings.server_name, host: m[1].replace(/^\[|\]$/g, ''), port: Number(m[2] ?? 443), enabled: true, source: 'MANUAL' }; });
      return { server_name: f.settings.server_name, access_points: points, ...(f.adapter === 'TERMINAL_BRIDGE' && f.settings.bridge ? { bridge: { ...f.settings.bridge, port: Number(f.settings.bridge.port || 9443) } } : {}) };
    }
    if (tech.id === 'FIX') { const s = f.settings; return { ...s, port: Number(s.port), heartbeat_seconds: Number(s.heartbeat_seconds), md_depth: Number(s.md_depth) }; }
    return { api_base_url: f.settings.api_base_url, auth_method: f.settings.auth_method, ...(f.settings.auth_method === 'OAUTH2' ? { oauth_redirect_url: f.settings.oauth_redirect_url } : {}) };
  }
  async function submit(e: FormEvent) {
    e.preventDefault(); setBusy(true); setError(''); setNotice('');
    try {
      const body = { ...(isNew ? { provider: { name: f.name, code: f.code || codeOf(f.name), kind: f.kind } } : { provider_id: provider?.id ?? f.provider_id }),
        connection: { platform: tech.platform, environment: f.environment, purpose: f.purpose, adapter: f.adapter, settings: settings() } };
      const r = await api('config/venues', 'POST', body);
      const pending = pendingMessage(r);
      if (pending) { setNotice(pending); await done(`w:${tech.id}:${r.provider.id}`); return; }
      await done('c:' + r.connection.id);
    } catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <form className="ct-add" onSubmit={submit} aria-label={`Add who through ${tech.label}`}>
    <h3 className="ct-sub">{provider ? `Add a ${tech.label} server or session for ${provider.name}` : `Add who · ${tech.label}`}</h3>
    {upstox && <p className="cfg-note">Upstox: use <b>Add account</b> on Upstox, or Accounts › Add Upstox account: it creates the broker, the API and the account in one step. Other broker and exchange APIs: describe the API here.</p>}
    {!provider && <div className="cfg-pair">
      <Field label="Who"><Choice label="Who" value={f.provider_id} options={[['', '+ New provider'], ...providers.map(p => [p.id, `${p.name} (${data.provider_kinds[p.kind] ?? p.kind})`] as [string, string])]} onChange={v => set({ provider_id: v })} /></Field>
      {isNew && <Field label="Type"><Choice label="Provider type" value={f.kind} options={Object.entries(data.provider_kinds as Record<string, string>)} onChange={v => set({ kind: v })} /></Field>}
    </div>}
    {isNew && <div className="cfg-pair">
      <Field label="Name" hint="e.g. VT Markets, LMAX Global, Equinix LD4"><input aria-label="Provider name" required maxLength={120} value={f.name} onChange={e => set({ name: e.target.value })} /></Field>
      <Field label="Code" hint="Short ID; filled in from the name"><input aria-label="Provider code" maxLength={32} placeholder={codeOf(f.name || 'Provider')} value={f.code} onChange={e => set({ code: e.target.value.toUpperCase() })} /></Field>
    </div>}
    {tech.id === 'MT5' && <>
      <div className="cfg-pair">
        <Field label="MT5 server name" hint="Exactly as in the terminal, e.g. VTMarkets-Demo"><input aria-label="MT5 server name" required maxLength={120} value={f.settings.server_name}
          onChange={e => { const env = envOfServer(e.target.value); set({ settings: { ...f.settings, server_name: e.target.value }, ...(env ? { environment: env } : {}) }); }} /></Field>
        <Field label="Method"><Choice label="Connection method" value={f.adapter} options={[['MTAPI_DIRECT', 'Direct SDK (recommended)'], ['TERMINAL_BRIDGE', 'MT5 terminal bridge (fallback)']]} onChange={v => set({ adapter: v })} /></Field>
      </div>
      <Field label="Access points" hint="host:port, one per line or comma-separated. Or use Add MT5 account to pick them from the broker's server list"><textarea aria-label="Access points" required rows={2} value={f.addresses} onChange={e => set({ addresses: e.target.value })} placeholder="mt5-demo.example.com:443" /></Field>
      {f.adapter === 'TERMINAL_BRIDGE' && <BridgeFields settings={f.settings} change={s => set({ settings: s })} />}
    </>}
    {tech.id === 'FIX' && <FixFields settings={f.settings} change={s => set({ settings: s })} />}
    {tech.id === 'API' && <div className="cfg-pair">
      <Field label="API base URL"><input aria-label="API base URL" required type="url" placeholder="https://api.example.com" value={f.settings.api_base_url} onChange={e => set({ settings: { ...f.settings, api_base_url: e.target.value } })} /></Field>
      <Field label="Login"><Choice label="API login method" value={f.settings.auth_method} options={[['API_KEY', 'API key and secret'], ['OAUTH2', 'OAuth (daily login)']]} onChange={v => set({ settings: { ...f.settings, auth_method: v } })} /></Field>
      {f.settings.auth_method === 'OAUTH2' && <Field label="Redirect URL"><input aria-label="OAuth redirect URL" required type="url" value={f.settings.oauth_redirect_url ?? ''} onChange={e => set({ settings: { ...f.settings, oauth_redirect_url: e.target.value } })} /></Field>}
    </div>}
    <div className="cfg-pair">
      <Field label="Environment"><Choice label="Environment" value={f.environment} options={[['DEMO', 'Demo'], ['LIVE', 'Live']]} onChange={v => set({ environment: v })} /></Field>
      <Field label="Role" hint="Data source: prices are taken from it. Execution: trading accounts"><Choice label="Role" value={f.purpose} options={ROLE_OPTIONS} onChange={v => set({ purpose: v })} /></Field>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="cfg-actions"><button type="button" onClick={cancel}>Cancel</button><button className="primary" disabled={busy}>{busy ? 'Adding…' : 'Add'}</button></div>
    <p className="cfg-note">Then select the new server and use <b>Add account</b>; save the {tech.id === 'FIX' ? 'session login (if the venue uses one)' : tech.id === 'MT5' ? (f.adapter === 'TERMINAL_BRIDGE' ? 'bridge token' : 'password with each account') : 'API key and secret'} under the record's Credential.</p>
  </form>;
}
