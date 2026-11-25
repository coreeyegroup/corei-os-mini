/* Workspace control desk: one shared workspace with Input → Processing → Output, Start / Pause / Stop, and its results.
 *
 * Why it exists: a desk must run what it shows. The saved layouts could only view and launch a read-only scan.
 * Purpose: list every workspace with its state and today's figures; edit one desk's three columns; Validate (every check
 * with why and a fix), Test run (a read-only scan), Start (confirm which existing deployments to adopt), Pause, Resume,
 * Stop (keep / cancel working orders / close positions, the last confirmed), Emergency stop; staged changes on a running
 * desk with review and apply; results by tab (runs, signals, decisions, orders, positions, log).
 * Must never: send, cancel or close anything itself (the server does it through the OMS and risk check); switch order
 * sending on; act on a run another desk controls.
 * Dependencies: Configuration.api, StageShell helpers, nav.goto, workspaceLinks. Consumers: MyWorkspaces.
 * Architectural position: UI over /api/trading/desks.
 */
import { useEffect, useMemo, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import { Msg, ago, pill, statePill, useData, when, type Row } from './StageShell';
import { usePublishRoute, launchContext } from './workspaceLinks';

const TIMEFRAMES = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const ENVS: [string, string, string][] = [
  ['OBSERVE', 'Observe', 'Signals only. Nothing is ordered.'],
  ['PAPER', 'Paper', 'Simulated by COREI at live prices; nothing reaches a broker.'],
  ['DEMO', 'Broker DEMO', 'Real orders to a demo account (needs DEMO order sending on).'],
  ['LIVE', 'LIVE', 'Real money. Needs LIVE order sending on (owner control) and a second person to start.'],
];
const HANDOFFS: [string, string][] = [['SIGNAL_ONLY', 'Signal only'], ['APPROVE', 'Ask me to approve'], ['AUTO', 'Automatic (risk-checked)']];
const STOPS: [string, string][] = [
  ['KEEP', 'Stop new decisions; keep working orders and positions'],
  ['CANCEL_ORDERS', "Stop and cancel this workspace's working orders"],
  ['CLOSE_POSITIONS', "Stop, cancel orders and close this workspace's positions (asks again)"],
];
const TEMPLATES: [string, string][] = [['CUSTOM', 'Custom'], ['INSTRUMENT', 'Instrument desk'], ['SCANNER', 'Strategy scanner'],
  ['MULTI_STRATEGY', 'Multi-strategy'], ['ACCOUNT', 'Account desk'], ['RESEARCH', 'Research (no orders)'], ['EXECUTION', 'Execution desk']];
const TABS = ['runs', 'signals', 'decisions', 'orders', 'positions', 'log'] as const;
const STATE_CLS: Record<string, string> = { RUNNING: 'ok', PAUSED: 'warn', STOPPED: 'off' };
const MARK: Record<string, string> = { PASS: '✓', WARN: '!', FAIL: '✕' };

type Cfg = {
  input: { sources: string[]; instruments: string[]; timeframes: string[] };
  processing: { strategies: { strategy_id: string; version_id: string | null }[]; signal_expiry_bars: number;
    sizing: { method: string; quantity?: string | null; risk_pct?: string | null; atr_multiple?: string | null; notional?: string | null };
    protect: { stop_atr?: string | null; take_atr?: string | null } };
  output: { environment: string; handoff: string; accounts: string[]; order_type: string; approval_minutes: number; stop_policy: string };
};
const blank = (): Cfg => ({
  input: { sources: [], instruments: [], timeframes: [] },
  processing: { strategies: [], signal_expiry_bars: 2, sizing: { method: 'FIXED_QTY', quantity: '1' }, protect: {} },
  output: { environment: 'OBSERVE', handoff: 'APPROVE', accounts: [], order_type: 'MARKET', approval_minutes: 10, stop_policy: 'KEEP' },
});
const templateCfg = (t: string): Cfg => {
  const c = blank();
  if (t === 'RESEARCH') c.output.handoff = 'SIGNAL_ONLY';
  if (t === 'EXECUTION') { c.output.environment = 'PAPER'; c.output.handoff = 'AUTO'; }
  if (t === 'ACCOUNT' || t === 'MULTI_STRATEGY' || t === 'INSTRUMENT') c.output.environment = 'PAPER';
  return c;
};
const toggle = (list: string[], v: string) => list.includes(v) ? list.filter(x => x !== v) : [...list, v];
const plain = (v?: string) => String(v ?? '').toLowerCase().replaceAll('_', ' ');
const err = (e: unknown) => (e as Error).message;

export default function DeskControl({ onLayouts }: { onLayouts: () => void }) {
  const [list, listError, reloadList] = useData<{ items: Row[] }>('trading/desks', 10_000);
  const [cat] = useData<Row>('trading/desks/catalogue', 60_000);
  const routedKey = launchContext(window.location.search).section?.match(/^d-([0-9a-f]{32})$/)?.[1];
  const [selected, setSelected] = useState<string | null>(null);
  const [overview, setOverview] = useState(!routedKey);
  const items = list?.items ?? [];
  useEffect(() => {   // a copied link opens the same workspace (section d-<id without dashes>; the router allows 40 characters)
    if (routedKey && !selected && list) { const hit = list.items.find(d => d.id.replaceAll('-', '') === routedKey); if (hit) setSelected(hit.id); else setOverview(true); }
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps
  const current = items.find(d => d.id === selected) ?? null;
  usePublishRoute('My Workspaces', overview || !current ? 'desks' : `d-${current.id.replaceAll('-', '')}`, '', !!list);
  const [notice, setNotice] = useState(''), [error, setError] = useState('');
  const create = async (template: string) => {
    const label = TEMPLATES.find(t => t[0] === template)?.[1] ?? 'Workspace';
    const name = window.prompt('Name for the new workspace', `${label} ${items.length + 1}`)?.trim();
    if (!name) return;
    try {
      const d = await api('trading/desks', 'POST', { name, template, config: templateCfg(template) });
      setSelected(d.id); setOverview(false); setNotice(`Created “${d.name}”. Choose its input, processing and output, then Validate.`); setError('');
      reloadList();
    } catch (e) { setError(err(e)); }
  };
  return <div className="dk">
    <nav className="mw-tabs dk-tabs" aria-label="Workspaces">
      <button aria-current={overview ? 'page' : undefined} onClick={() => setOverview(true)}>All workspaces</button>
      {items.map(d => <button key={d.id} aria-current={!overview && d.id === selected ? 'page' : undefined}
        onClick={() => { setSelected(d.id); setOverview(false); setNotice(''); setError(''); }}>
        <span className={`dk-dot ${STATE_CLS[d.state] ?? ''}`} aria-hidden="true">●</span> {d.name}</button>)}
      <label className="dk-new">New <select aria-label="New workspace template" value="" onChange={e => { if (e.target.value) void create(e.target.value); }}>
        <option value="">+ New workspace…</option>{TEMPLATES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <button onClick={onLayouts}>Saved layouts</button>
    </nav>
    <Msg error={error || listError} notice={notice} />
    {!list ? <p role="status">Loading workspaces…</p>
      : overview || !current ? <AllDesks items={items} open={id => { setSelected(id); setOverview(false); }} onCreate={() => void create('CUSTOM')} />
        : <Desk key={current.id} desk={current} cat={cat} changed={reloadList} removed={() => { setOverview(true); setSelected(null); reloadList(); }} />}
  </div>;
}

function AllDesks({ items, open, onCreate }: { items: Row[]; open: (id: string) => void; onCreate: () => void }) {
  if (!items.length) return <section className="dk-empty">
    <h3>No workspaces yet</h3>
    <p>A workspace is one place to choose what to watch (input), how to decide (processing) and what to do (output), then Start, Pause or Stop it.
      Feeds, strategies, risk and brokers stay shared; a workspace starts and stops only its own runs.</p>
    <button className="primary" onClick={onCreate}>Create a workspace</button>
  </section>;
  return <table className="cfg-table dk-all"><caption>Every workspace has its own settings, state and results. Start, Pause and Stop act only on its own runs.</caption>
    <thead><tr><th>Workspace</th><th>State</th><th>Input</th><th>Processing</th><th>Output</th><th>Today</th><th>Attention</th><th /></tr></thead>
    <tbody>{items.map(d => { const c: Cfg = d.config, s = d.summary ?? {}; return <tr key={d.id}>
      <td><b>{d.name}</b><br /><small>{d.shared ? 'shared' : 'private'} · owner {d.owner}</small></td>
      <td>{pill(d.state, STATE_CLS[d.state])}{d.state_reason && <><br /><small>{d.state_reason}</small></>}</td>
      <td>{c.input.sources.length} source(s) · {c.input.instruments.length} instrument(s){c.input.timeframes.length ? ` · ${c.input.timeframes.join(', ')}` : ''}</td>
      <td>{c.processing.strategies.length} strateg{c.processing.strategies.length === 1 ? 'y' : 'ies'}</td>
      <td>{ENVS.find(e => e[0] === c.output.environment)?.[1]} · {plain(c.output.environment === 'OBSERVE' ? 'SIGNAL_ONLY' : c.output.handoff)} · {c.output.accounts.length} account(s)</td>
      <td>{s.signals_today ?? 0} signals · {s.orders_today ?? 0} orders · {s.open_positions ?? 0} open</td>
      <td>{d.attention?.length ? d.attention.join('; ') : '—'}</td>
      <td><button onClick={() => open(d.id)}>Open</button></td></tr>; })}</tbody></table>;
}

function Desk({ desk, cat, changed, removed }: { desk: Row; cat: Row | null; changed: () => void; removed: () => void }) {
  const editable: Cfg = desk.staged ?? desk.config;
  const [cfg, setCfg] = useState<Cfg>(editable);
  const [name, setName] = useState<string>(desk.name);
  const [shared, setShared] = useState<boolean>(desk.shared);
  const [dirty, setDirty] = useState(false);
  const [ver, setVer] = useState<number>(desk.version);   // follows every response, so a chained action never sends a stale version
  const [busy, setBusy] = useState('');
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [check, setCheck] = useState<Row | null>(desk.last_check ?? null);
  const [panel, setPanel] = useState<'' | 'start' | 'stop' | 'review'>('');
  const [review, setReview] = useState<Row | null>(null);
  const [adopt, setAdopt] = useState<string[]>([]);
  const [stopPolicy, setStopPolicy] = useState<string>(editable.output.stop_policy);
  const [confirmClose, setConfirmClose] = useState(false);
  const [tab, setTab] = useState<typeof TABS[number]>('runs');
  const [test] = useData<Row | null>(`trading/desks/${desk.id}/test`, 10_000, [busy]);
  useEffect(() => { setVer(v => Math.max(v, desk.version)); if (!dirty) { setCfg(desk.staged ?? desk.config); setName(desk.name); setShared(desk.shared); } }, [desk.version]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {   // show what is missing at once, without waiting for Validate (read-only check)
    if (!check) api('trading/desks/plan', 'POST', cfg).then(setCheck).catch(() => undefined);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {   // a live check of unsaved settings, so the readiness strip always matches what is on screen
    if (!dirty) return;
    const t = setTimeout(() => { api('trading/desks/plan', 'POST', cfg).then(setCheck).catch(() => undefined); }, 500);
    return () => clearTimeout(t);
  }, [cfg, dirty]);
  const edit = (f: (c: Cfg) => Cfg) => { setCfg(c => f(JSON.parse(JSON.stringify(c)) as Cfg)); setDirty(true); setMsg({ error: '', notice: '' }); };
  const run = async (label: string, f: () => Promise<any>, ok?: (r: any) => string) => {
    setBusy(label); setMsg({ error: '', notice: '' });
    try { const r = await f(); if (r && typeof r.version === 'number' && (r.id === desk.id || r.checks)) setVer(r.version); setMsg({ error: '', notice: ok ? ok(r) : '' }); changed(); return r; }
    catch (e) { setMsg({ error: err(e), notice: '' }); const p = (e as any)?.detail?.plan; if (p) setCheck(p); }
    finally { setBusy(''); }
  };
  /** Unsaved settings are saved first, so Validate, Test run and Start always act on what is on screen. */
  const ensureSaved = async (): Promise<Row | null> => {
    if (!dirty) return null;
    const r = await api(`trading/desks/${desk.id}`, 'PUT', { name, shared, template: desk.template, config: cfg, version: ver });
    setDirty(false); setVer(r.version); return r;
  };
  const save = () => run('save', async () => ensureSaved(), r => r?.staged ? 'Saved as staged changes. The running settings stay until you review and apply.' : 'Workspace saved.');
  const validate = () => {
    const wasDirty = dirty;
    return run('validate', async () => {
      const saved = await ensureSaved();
      const r = await api(`trading/desks/${desk.id}/validate${(saved ?? desk).staged ? '?staged=true' : ''}`, 'POST'); setCheck(r);
      return saved ? { ...r, version: saved.version } : r;
    }, r => `${wasDirty ? 'Saved. ' : ''}${r.ready ? 'Ready to start.' : `${r.checks.filter((c: Row) => c.status === 'FAIL').length} check(s) to fix before Start — see “Next step”.`}`);
  };
  /** A reason for the audit log; a cancelled prompt does nothing, a too-short one says so instead of failing silently. */
  const reason = (q: string) => {
    const r = window.prompt(q + ' (at least 5 characters)')?.trim();
    if (r !== undefined && r.length < 5) setMsg({ error: 'Nothing was done: please give a reason of at least 5 characters.', notice: '' });
    return r ?? '';
  };
  const startable = desk.state !== 'RUNNING';
  const adoptable = (check?.runs ?? []).filter((r: Row) => r.action === 'ADOPT');
  const env = cfg.output.environment;
  const accounts: Row[] = cat?.accounts ?? [];
  const usableAccount = (a: Row) => a.purpose !== 'MARKET_DATA' && (env === 'OBSERVE' || env === 'PAPER' || a.environment === env);
  const s = desk.summary ?? {};
  return <section className="dk-desk" aria-label={`Workspace ${desk.name}`}>
    <header className="dk-head">
      <div><h3>{desk.name} {pill(desk.state, STATE_CLS[desk.state])}</h3>
        <small>{desk.shared ? 'Shared' : 'Private'} · owner {desk.owner} · {desk.state_reason ? `${plain(desk.state)} by ${desk.state_by}: ${desk.state_reason} (${ago(desk.state_at)})` : 'never started'}</small></div>
      <span className="mw-actions">
        <button disabled={!dirty || !!busy} onClick={() => void save()}>{busy === 'save' ? 'Saving…' : 'Save'}</button>
        <button disabled={!!busy} title={dirty ? 'Saves your changes, then checks them' : 'Check every stage'} onClick={() => void validate()}>{busy === 'validate' ? 'Checking…' : 'Validate'}</button>
        <button disabled={!!busy} title="A read-only research scan: no runs, no orders"
          onClick={() => void run('test', async () => { await ensureSaved(); return api(`trading/desks/${desk.id}/test`, 'POST'); }, () => 'Test run queued: no runs are created and nothing is ordered. Results: Strategy Studio → Runs & results.')}>Test run</button>
        {desk.state === 'RUNNING' && <button disabled={!!busy} onClick={() => { const r = reason('Why pause this workspace?'); if (r.length >= 5) void run('pause', () => api(`trading/desks/${desk.id}/pause`, 'POST', { version: ver, reason: r }), () => 'Paused: its runs make no new decisions.'); }}>Pause</button>}
        {desk.state === 'PAUSED' && <button disabled={!!busy} onClick={() => { const r = reason('Why resume?'); if (r.length >= 5) void run('resume', () => api(`trading/desks/${desk.id}/resume`, 'POST', { version: ver, reason: r }), () => 'Resumed.'); }}>Resume</button>}
        {desk.state !== 'STOPPED' && <button disabled={!!busy} onClick={() => { setStopPolicy(desk.config.output.stop_policy); setConfirmClose(false); setPanel('stop'); }}>Stop…</button>}
        {startable && desk.state === 'STOPPED' && <button className="primary" disabled={!!busy || !!desk.staged}
          title={dirty ? 'Saves your changes, checks them, then asks you to confirm' : 'Checks every stage, then asks you to confirm'}
          onClick={() => { setPanel('start'); setAdopt([]); void validate(); }}>Start…</button>}
        <details className="dk-more"><summary>More</summary>
          <button className="danger" disabled={desk.state === 'STOPPED' || !!busy} onClick={() => {
            const r = reason('Emergency stop: every run of this workspace gets a kill switch and pauses. Releasing is done in Risk. Reason?');
            if (r.length >= 5) void run('emergency', () => api(`trading/desks/${desk.id}/emergency`, 'POST', { reason: r }), x => `Emergency stop: ${x.result.kill_switches.length} kill switch(es) engaged.`);
          }}>Emergency stop</button>
          <label><input type="checkbox" checked={shared} onChange={e => { setShared(e.target.checked); setDirty(true); }} /> Shared with other operators</label>
          <label>Name <input maxLength={80} value={name} onChange={e => { setName(e.target.value); setDirty(true); }} /></label>
          <button disabled={desk.state !== 'STOPPED' || !!busy} onClick={() => {
            if (window.confirm(`Delete “${desk.name}”? Its runs stay as paused deployments in Strategies.`))
              void run('delete', () => api(`trading/desks/${desk.id}?version=${ver}`, 'DELETE')).then(removed);
          }}>Delete workspace</button>
        </details>
      </span>
    </header>
    <Msg error={msg.error} notice={msg.notice || (dirty ? (desk.state === 'STOPPED' ? 'Unsaved changes' : 'Unsaved changes: saving stages them for review') : '')} />
    {desk.staged && <div className="dk-staged" role="status"><b>Changes staged</b> by {desk.staged_by}. Running settings are unchanged until you apply.
      <button onClick={() => void run('review', async () => { const r = await api(`trading/desks/${desk.id}/review`); setReview(r); setPanel('review'); return r; })}>Review & apply</button>
      <button onClick={() => { if (window.confirm('Discard the staged changes?')) void run('discard', () => api(`trading/desks/${desk.id}/discard`, 'POST', { version: ver, reason: 'Discard staged changes' }), () => 'Staged changes discarded.'); }}>Discard</button></div>}
    {panel === 'review' && review && <section className="dk-panel" aria-label="Review staged changes">
      <h4>Review staged changes</h4>
      <table className="cfg-table"><thead><tr><th>Setting</th><th>Running</th><th>Staged</th></tr></thead>
        <tbody>{review.changes.map((c: Row) => <tr key={c.setting}><td>{c.setting}</td><td><code>{JSON.stringify(c.running)}</code></td><td><code>{JSON.stringify(c.staged)}</code></td></tr>)}</tbody></table>
      {review.plan && <Checks plan={review.plan} />}
      <p className="cfg-note">Applying validates again. New runs start{desk.state === 'PAUSED' ? ' when you resume' : ' now'}; runs no longer in the settings are paused and released. Applying needs a Risk approver{env === 'LIVE' ? ' other than the person who staged the change' : ''}.</p>
      <span className="mw-actions"><button onClick={() => setPanel('')}>Cancel</button>
        <button className="primary" disabled={!review.plan?.ready || !!busy} onClick={() => { const r = reason('Reason for applying these changes?'); if (r.length >= 5)
          void run('apply', () => api(`trading/desks/${desk.id}/apply`, 'POST', { version: ver, reason: r, adopt: [] }), () => 'Changes applied.').then(x => { if (x) setPanel(''); }); }}>Apply changes</button></span>
    </section>}
    <Readiness check={check} dirty={dirty} />
    <div className="dk-cols">
      <fieldset className="dk-col" disabled={!!busy}><legend>01 · Input</legend>
        <Pick title="Data sources" hint="First is primary; others cover instruments it does not carry." items={(cat?.sources ?? []).map((r: Row) => [r.id, `${r.code} · ${plain(r.environment)}`, r.status !== 'ACTIVE' ? 'inactive' : ''])}
          value={cfg.input.sources} onChange={v => edit(c => { c.input.sources = v; return c; })} ordered />
        <Pick title="Instruments" search items={(cat?.instruments ?? []).filter((r: Row) => r.status === 'ACTIVE' || cfg.input.instruments.includes(r.id)).map((r: Row) => [r.id, r.symbol, r.asset_class ?? ''])}
          value={cfg.input.instruments} onChange={v => edit(c => { c.input.instruments = v; return c; })} />
        <div className="dk-field"><span>Timeframes <small>(empty: each strategy's own)</small></span><div className="dk-chips">
          {TIMEFRAMES.map(tf => <label key={tf} className="dk-chip"><input type="checkbox" checked={cfg.input.timeframes.includes(tf)}
            onChange={() => edit(c => { c.input.timeframes = toggle(c.input.timeframes, tf); return c; })} />{tf}</label>)}</div></div>
        <p className="cfg-note">Feeds come from Market data plans; one subscription per symbol is shared by every workspace. <button className="link" onClick={() => goto('Market data')}>Market data ↗</button></p>
      </fieldset>
      <fieldset className="dk-col" disabled={!!busy}><legend>02 · Processing</legend>
        <StrategyPick strategies={cat?.strategies ?? []} value={cfg.processing.strategies} timeframes={cfg.input.timeframes}
          onChange={v => edit(c => { c.processing.strategies = v; return c; })} />
        <div className="dk-grid">
          <label>Sizing <select value={cfg.processing.sizing.method} onChange={e => edit(c => { c.processing.sizing = e.target.value === 'FIXED_QTY' ? { method: 'FIXED_QTY', quantity: '1' }
            : e.target.value === 'RISK_PCT' ? { method: 'RISK_PCT', risk_pct: '0.5', atr_multiple: '2', quantity: null } : { method: 'FIXED_NOTIONAL', notional: '1000', quantity: null }; return c; })}>
            <option value="FIXED_QTY">Fixed quantity</option><option value="RISK_PCT">Risk % of equity</option><option value="FIXED_NOTIONAL">Fixed value</option></select></label>
          {cfg.processing.sizing.method === 'FIXED_QTY' && <Dec label="Quantity (lots)" v={cfg.processing.sizing.quantity} set={v => edit(c => { c.processing.sizing.quantity = v; return c; })} />}
          {cfg.processing.sizing.method === 'RISK_PCT' && <><Dec label="Risk % per trade" v={cfg.processing.sizing.risk_pct} set={v => edit(c => { c.processing.sizing.risk_pct = v; return c; })} />
            <Dec label="Stop distance (ATR)" v={cfg.processing.sizing.atr_multiple} set={v => edit(c => { c.processing.sizing.atr_multiple = v; return c; })} /></>}
          {cfg.processing.sizing.method === 'FIXED_NOTIONAL' && <Dec label="Value per order" v={cfg.processing.sizing.notional} set={v => edit(c => { c.processing.sizing.notional = v; return c; })} />}
          <Dec label="Stop (ATR, blank: none)" v={cfg.processing.protect.stop_atr} set={v => edit(c => { c.processing.protect.stop_atr = v || null; return c; })} />
          <Dec label="Target (ATR, blank: none)" v={cfg.processing.protect.take_atr} set={v => edit(c => { c.processing.protect.take_atr = v || null; return c; })} />
          <label>Decision expires after (bars) <input type="number" min={1} max={500} value={cfg.processing.signal_expiry_bars}
            onChange={e => edit(c => { c.processing.signal_expiry_bars = Math.max(1, Math.min(500, Number(e.target.value) || 1)); return c; })} /></label>
        </div>
        <p className="cfg-note">Evaluated on each bar close of the strategy's timeframe. Risk limits are inherited from Risk and can never be loosened here. <button className="link" onClick={() => goto('Strategy Studio')}>Strategy Studio ↗</button> <button className="link" onClick={() => goto('Risk')}>Risk ↗</button></p>
      </fieldset>
      <fieldset className="dk-col" disabled={!!busy}><legend>03 · Output</legend>
        <div className="dk-field" role="radiogroup" aria-label="Environment"><span>Environment</span>
          {ENVS.map(([v, l, note]) => { const off = (v === 'DEMO' || v === 'LIVE') && !cat?.controls?.[v]?.trading_enabled; return <label key={v} className="dk-radio">
            <input type="radio" name={`env-${desk.id}`} checked={env === v} onChange={() => edit(c => { c.output.environment = v; c.output.accounts = c.output.accounts.filter(id => { const a = accounts.find(x => x.id === id); return !a || v === 'OBSERVE' || v === 'PAPER' || a.environment === v; }); return c; })} />
            <b>{l}</b> <small>{note}{off ? ` · ${v} order sending is off` : ''}</small></label>; })}</div>
        {env !== 'OBSERVE' && <div className="dk-field" role="radiogroup" aria-label="What happens to a decision"><span>What happens to a decision</span>
          {HANDOFFS.map(([v, l]) => <label key={v} className="dk-radio"><input type="radio" name={`ho-${desk.id}`} checked={cfg.output.handoff === v}
            onChange={() => edit(c => { c.output.handoff = v; return c; })} /> {l}</label>)}</div>}
        <Pick title="Accounts" hint={env === 'OBSERVE' || env === 'PAPER' ? 'Runs are booked here; nothing is sent to the broker.' : `${env} accounts only.`}
          items={accounts.filter(a => usableAccount(a) || cfg.output.accounts.includes(a.id)).map(a => [a.id, `${a.code} · ${plain(a.environment)}`, a.trading_permission === 'DISABLED' ? 'trading off' : ''])}
          value={cfg.output.accounts} onChange={v => edit(c => { c.output.accounts = v; return c; })} />
        <div className="dk-grid">
          <label>Order type <select value={cfg.output.order_type} onChange={e => edit(c => { c.output.order_type = e.target.value; return c; })}>
            <option value="MARKET">Market</option><option value="LIMIT">Limit (marketable)</option></select></label>
          {cfg.output.handoff === 'APPROVE' && env !== 'OBSERVE' && <label>Approval window (min) <input type="number" min={1} max={1440} value={cfg.output.approval_minutes}
            onChange={e => edit(c => { c.output.approval_minutes = Math.max(1, Math.min(1440, Number(e.target.value) || 1)); return c; })} /></label>}
          <label>When you press Stop <select value={cfg.output.stop_policy} onChange={e => edit(c => { c.output.stop_policy = e.target.value; return c; })}>
            {STOPS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        </div>
        <p className="cfg-note">Execution algorithms, order-type overrides and account groups stay in Execution. <button className="link" onClick={() => goto('Execution')}>Execution ↗</button></p>
      </fieldset>
    </div>
    {panel === 'start' && <section className="dk-panel" aria-label="Start workspace">
      <h4>Start “{desk.name}”?</h4>
      {check ? <Checks plan={check} /> : <p role="status">Validating…</p>}
      {adoptable.length > 0 && <fieldset><legend>Existing deployments outside any workspace</legend>
        <p className="cfg-note">Tick the ones this workspace should control. Unticked ones are only watched here and are not changed.</p>
        {adoptable.map((r: Row) => <label key={r.deployment_id} className="dk-radio"><input type="checkbox" checked={adopt.includes(r.deployment_id)}
          onChange={() => setAdopt(a => toggle(a, r.deployment_id))} /> Adopt {r.strategy_code} v{r.version_number} × {r.symbol} × {r.account_code} ({plain(r.status)})
          {r.switch_mode && <b className="dk-warn"> · moves it from {plain(r.from_mode)} to {check?.mode === 'PAPER' ? 'paper' : `broker ${plain(check?.environment)}${check?.environment === 'DEMO' ? ' (real demo orders)' : ' (real money)'}`}</b>}</label>)}</fieldset>}
      {check && check.ready && adoptable.length > 0 && !adopt.length && !(check.coverage.create + check.coverage.keep) &&
        <p role="alert" className="cfg-error">Every run here already exists outside this workspace. Tick at least one to adopt, or Start has nothing to run.</p>}
      {check && <div className="dk-will"><b>What Start will do</b><ul>
        {check.coverage.create > 0 && <li>Create and activate {check.coverage.create} run(s) owned by this workspace.</li>}
        {check.coverage.keep > 0 && <li>Activate {check.coverage.keep} run(s) it already owns.</li>}
        {adopt.length > 0 && <li>Adopt and activate {adopt.length} existing deployment(s).</li>}
        {(check.coverage.watch + adoptable.length - adopt.length) > 0 && <li>Watch {check.coverage.watch + adoptable.length - adopt.length} run(s) without controlling them.</li>}
        <li>Evaluate on each bar close; {ENVS.find(e => e[0] === check.environment)?.[2]}</li>
        <li>Hand-off: {plain(check.handoff)}. Every order passes the risk check.</li>
        <li>When you press Stop: {STOPS.find(x => x[0] === desk.config.output.stop_policy)?.[1].toLowerCase()}. Stop never touches other workspaces, shared feeds or positions opened elsewhere.</li>
      </ul></div>}
      <p className="cfg-note">Starting needs the Risk approver role{env === 'LIVE' ? ', and on LIVE a person other than the one who last changed the settings' : ''}.</p>
      <span className="mw-actions"><button onClick={() => setPanel('')}>Cancel</button>
        <button className="primary" disabled={!check?.ready || !!busy} onClick={() => void run('start',
          () => api(`trading/desks/${desk.id}/start`, 'POST', { version: ver, adopt, reason: 'Started from the workspace' }),
          r => `Started: ${r.result.activated.length} run(s) active${r.result.held.length ? `, ${r.result.held.length} held (${r.result.held.map((h: Row) => h.why).join('; ')})` : ''}${r.result.watched.length ? `, ${r.result.watched.length} watched` : ''}.`).then(x => { if (x) setPanel(''); })}>
          {check && !check.ready ? 'Fix the failed checks first' : 'Start'}</button></span>
    </section>}
    {panel === 'stop' && <section className="dk-panel" aria-label="Stop workspace">
      <h4>Stop “{desk.name}”</h4>
      {STOPS.map(([v, l]) => <label key={v} className="dk-radio"><input type="radio" name={`stop-${desk.id}`} checked={stopPolicy === v} onChange={() => setStopPolicy(v)} /> {l}</label>)}
      {stopPolicy === 'CLOSE_POSITIONS' && <label className="dk-radio"><input type="checkbox" checked={confirmClose} onChange={e => setConfirmClose(e.target.checked)} />
        I confirm: send closing orders for this workspace's open positions ({s.open_positions ?? 0}). Each passes the risk check.</label>}
      <p className="cfg-note">Orders with an unknown outcome are never cancelled blind; they are listed for resolution with the broker. Trailing stops and reconciliation keep running.</p>
      <span className="mw-actions"><button onClick={() => setPanel('')}>Cancel</button>
        <button className="primary" disabled={!!busy || (stopPolicy === 'CLOSE_POSITIONS' && !confirmClose)} onClick={() => { const r = reason('Why stop this workspace?'); if (r.length >= 5)
          void run('stop', () => api(`trading/desks/${desk.id}/stop`, 'POST', { version: ver, reason: r, policy: stopPolicy, confirm_close: confirmClose }),
            x => `Stopped. ${x.result.cancelled.length} order(s) cancelled, ${x.result.close_orders.length} closing order(s)${x.result.unknown.length ? `, ${x.result.unknown.length} order(s) with an unknown outcome need resolving in Execution` : ''}${x.result.cancel_failed.length + x.result.close_failed.length ? `, ${x.result.cancel_failed.length + x.result.close_failed.length} could not be done (see the log)` : ''}.`).then(x => { if (x) setPanel(''); }); }}>Stop</button></span>
    </section>}
    <div className="dk-today"><span>Runs {s.active ?? 0} active / {s.controlled ?? 0} controlled{s.watched ? ` · ${s.watched} watched` : ''}</span>
      <span>Today: {s.signals_today ?? 0} signals · {s.orders_today ?? 0} orders</span><span>{s.open_positions ?? 0} open position(s)</span>
      {s.approvals_waiting ? <button className="link" onClick={() => goto('Execution')}>{s.approvals_waiting} approval(s) waiting ↗</button> : null}
      {s.unknown_orders ? <button className="link" onClick={() => goto('Execution')}>{s.unknown_orders} unknown order(s) ↗</button> : null}
      {test && <span>Test run: {plain(test.status)} · {test.total} combination(s) <button className="link" onClick={() => goto('Strategy Studio')}>results ↗</button></span>}</div>
    <nav className="mw-tabs" aria-label="Results">{TABS.map(t => <button key={t} aria-current={tab === t ? 'page' : undefined} onClick={() => setTab(t)}>{t[0].toUpperCase() + t.slice(1)}</button>)}</nav>
    <Results desk={desk} tab={tab} />
  </section>;
}

function Readiness({ check, dirty }: { check: Row | null; dirty: boolean }) {
  if (!check) return <p className="dk-ready">Readiness: checking…</p>;
  const c = check.coverage;
  const next = check.checks.find((x: Row) => x.status === 'FAIL');
  return <div className="dk-ready" aria-label="Readiness">
    <p className={`dk-next ${next ? 'fail' : 'pass'}`} role="status"><b>Next step:</b>{' '}
      {next ? <>{next.title}{next.detail ? ` — ${next.detail}` : ''}{next.fix ? ` ${next.fix}` : ''} {next.view && <button className="link" onClick={() => goto(next.view)}>{next.view} ↗</button>}</>
        : dirty ? 'Settings look ready. Press Start… (your changes are saved first).' : check.ready ? 'Ready. Press Start… to confirm and run.' : 'Validate to check again.'}</p>
    {check.checks.map((x: Row, i: number) => <span key={i} className={`dk-check ${x.status.toLowerCase()}`} title={[x.detail, x.fix].filter(Boolean).join(' — ')}>
      {MARK[x.status]} {x.stage} · {x.title}</span>)}
    <span className="dk-cover">{c.instruments[0]}/{c.instruments[1]} instruments × {c.strategies[0]}/{c.strategies[1]} strategies × {c.accounts[0]}/{c.accounts[1]} accounts
      {c.timeframes.length ? ` · ${c.timeframes.join(', ')}` : ''} → {c.create + c.keep + c.adopt} run(s){c.watch ? `, ${c.watch} watched` : ''}{c.skip ? `, ${c.skip} skipped` : ''}</span>
  </div>;
}

function Checks({ plan }: { plan: Row }) {
  return <ul className="dk-checks">{plan.checks.map((x: Row, i: number) => <li key={i} className={x.status.toLowerCase()}>
    <b>{MARK[x.status]} {x.stage} · {x.title}</b>{x.detail && <div>{x.detail}</div>}
    {x.fix && <div className="cfg-note">Fix: {x.fix} {x.view && <button className="link" onClick={() => goto(x.view)}>{x.view} ↗</button>}</div>}</li>)}</ul>;
}

function Pick({ title, hint, items, value, onChange, search, ordered }: {
  title: string; hint?: string; items: [string, string, string][]; value: string[]; onChange: (v: string[]) => void; search?: boolean; ordered?: boolean;
}) {
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const f = q.trim().toLowerCase();
    const list = f ? items.filter(i => i[1].toLowerCase().includes(f)) : items;
    return [...list.filter(i => value.includes(i[0])), ...list.filter(i => !value.includes(i[0])).slice(0, 200)];
  }, [items, q, value]);
  return <div className="dk-field"><span>{title} <small>{value.length} selected{hint ? ` · ${hint}` : ''}</small></span>
    {search && <input type="search" aria-label={`Search ${title.toLowerCase()}`} placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />}
    <div className="dk-list">{shown.length ? shown.map(([id, label, note]) => <label key={id} className="dk-radio">
      <input type="checkbox" checked={value.includes(id)} onChange={() => onChange(toggle(value, id))} />
      {label}{ordered && value.includes(id) ? <small> · {value.indexOf(id) === 0 ? 'primary' : `backup ${value.indexOf(id)}`}</small> : null}
      {note && <small> · {note}</small>}</label>) : <small>None available.</small>}</div></div>;
}

function StrategyPick({ strategies, value, timeframes, onChange }: {
  strategies: Row[]; value: Cfg['processing']['strategies']; timeframes: string[]; onChange: (v: Cfg['processing']['strategies']) => void;
}) {
  const [q, setQ] = useState('');
  const f = q.trim().toLowerCase();
  const shown = strategies.filter(s => s.status === 'ACTIVE' || value.some(v => v.strategy_id === s.id))
    .filter(s => !f || `${s.code} ${s.name}`.toLowerCase().includes(f))
    .sort((a, b) => Number(!a.approved_version) - Number(!b.approved_version));
  const drafts = strategies.filter(s => s.status === 'ACTIVE' && !s.approved_version).length;
  return <div className="dk-field"><span>Strategies <small>{value.length} selected · approved versions run</small></span>
    {drafts > 0 && <small className="cfg-note">{drafts} strateg{drafts === 1 ? 'y is' : 'ies are'} still draft. A Risk approver approves a version in <button className="link" onClick={() => goto('Strategies')}>Strategies ↗</button> (after a backtest or paper run), then it can run here.</small>}
    <input type="search" aria-label="Search strategies" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
    <div className="dk-list">{shown.map(s => { const pick = value.find(v => v.strategy_id === s.id);
      const chosen = pick?.version_id ? (s.versions ?? []).find((v: Row) => v.id === pick.version_id) : null;
      const tf = chosen?.timeframe ?? s.timeframe; const out = !!pick && !!timeframes.length && tf && !timeframes.includes(tf);
      return <div key={s.id} className="dk-strat"><label className="dk-radio"><input type="checkbox" checked={!!pick}
        onChange={() => onChange(pick ? value.filter(v => v.strategy_id !== s.id) : [...value, { strategy_id: s.id, version_id: null }])} />
        <b>{s.code}</b> <small>{s.name}{s.approved_version ? ` · v${s.approved_version} approved · ${s.timeframe ?? 'H1'}` : ''}</small>
        {!s.approved_version && <small className="dk-warn"> · draft: cannot run until a version is approved</small>}</label>
        {pick && <select aria-label={`${s.code} version`} value={pick.version_id ?? ''} onChange={e => onChange(value.map(v => v.strategy_id === s.id ? { ...v, version_id: e.target.value || null } : v))}>
          <option value="">Latest approved</option>{(s.versions ?? []).map((v: Row) => <option key={v.id} value={v.id} disabled={v.state !== 'APPROVED'}>v{v.number} · {plain(v.state)} · {v.timeframe ?? 'H1'}</option>)}</select>}
        {out && <small className="dk-warn"> runs on {tf}: add it to the timeframes or it is left out</small>}</div>; })}
      {!shown.length && <small>No strategies. <button className="link" onClick={() => goto('Strategies')}>Strategies ↗</button></small>}</div></div>;
}

function Dec({ label, v, set }: { label: string; v?: string | null; set: (v: string) => void }) {
  const bad = !!v && !/^\d+(\.\d{1,12})?$/.test(v);   // exact decimals as text: never a float
  return <label>{label} <input inputMode="decimal" value={v ?? ''} aria-invalid={bad} onChange={e => set(e.target.value.trim())} />{bad && <small className="dk-warn">a plain number, e.g. 0.5</small>}</label>;
}

const COLS: Record<string, [string, (r: Row) => any][]> = {
  runs: [['Run', r => `${r.strategy_code} v${r.version_number}`], ['Instrument', r => r.symbol], ['TF', r => r.timeframe ?? 'H1'], ['Account', r => `${r.account_code} · ${plain(r.environment)}`],
    ['Role', r => pill(r.role, r.role === 'WATCH' ? 'off' : 'ok')], ['Status', r => statePill(r.status)], ['Hand-off', r => plain(r.handoff)],
    ['Last bar', r => r.last_bar_time ? when(r.last_bar_time) : '—'], ['Last result', r => r.last_error ? <span className="dk-warn">{r.last_error}</span> : plain(r.last_evaluation?.outcome) || '—']],
  signals: [['At', r => when(r.at)], ['Instrument', r => r.symbol], ['TF', r => r.timeframe], ['Strategy', r => r.strategy_code], ['Side', r => r.side], ['Strength', r => r.strength],
    ['State', r => statePill(r.state)], ['Why', r => (r.explanation ?? []).concat(r.reasons ?? []).join('; ') || '—'], ['Account', r => r.account_code]],
  decisions: [['At', r => when(r.at)], ['Instrument', r => r.symbol], ['Strategy', r => r.strategy_code], ['Outcome', r => statePill(r.outcome)], ['Hand-off', r => plain(r.handoff)],
    ['Side · qty', r => `${r.side ?? '—'} ${r.quantity ?? ''}`], ['Why', r => (r.reasons ?? []).join('; ') || r.sizing_note || '—'], ['Account', r => r.account_code]],
  orders: [['At', r => when(r.at)], ['Instrument', r => r.symbol], ['Strategy', r => r.strategy_code ?? '—'], ['Side', r => r.side], ['Qty · filled', r => `${r.quantity} · ${r.filled_qty}`],
    ['State', r => statePill(r.state)], ['Mode', r => plain(r.mode)], ['Risk', r => (r.risk_reasons ?? []).join('; ') || 'passed'], ['Account', r => r.account_code]],
  positions: [['Instrument', r => r.symbol], ['Strategy', r => r.strategy_code], ['Mode', r => plain(r.mode)], ['Qty', r => r.quantity], ['Avg price', r => r.avg_price],
    ['Realised', r => `${r.realised_pnl ?? '0'} ${r.currency ?? ''}`], ['Stop · target', r => `${r.stop_loss ?? '—'} · ${r.take_profit ?? '—'}`], ['Account', r => r.account_code]],
  log: [['At', r => when(r.occurred_at)], ['Who', r => r.actor], ['Action', r => plain(r.action)], ['On', r => r.entity], ['Reason', r => r.reason || '—']],
};

function Results({ desk, tab }: { desk: Row; tab: string }) {
  const [data, error] = useData<{ items: Row[] }>(`trading/desks/${desk.id}/results/${tab}`, 10_000, [desk.version]);
  const rows = data?.items ?? [];
  return <div className="dk-results"><Msg error={error} />
    <table className="cfg-table"><thead><tr>{COLS[tab].map(([h]) => <th key={h}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={r.id ?? r.deployment_id ?? i}>{COLS[tab].map(([h, f]) => <td key={h}>{f(r)}</td>)}</tr>)}</tbody></table>
    {!data ? <p role="status">Loading…</p> : !rows.length && <p className="cfg-note">{tab === 'runs' ? 'No runs yet: Start creates them.' : `No ${tab} yet for this workspace's runs.`}</p>}
  </div>;
}
