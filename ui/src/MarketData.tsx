/* Market data: what is collected, from where, how it is kept, how good it is.
 * Live prices and collectors · Plans (matrix + form with size estimate) · Quality and
 * broker comparison · Events · Backfill · Controls · Archive and backups. */
import { FormEvent, useEffect, useMemo, useState } from 'react';
import { addReauthHandler, api, Field } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import FloatingWindow from './FloatingWindow';
import { SignIn } from './Infrastructure';
import './configuration.css';
import './infrastructure.css';
import './marketdata.css';

type Row = Record<string, any>;
type Tab = 'live' | 'plans' | 'quality' | 'events' | 'backfill' | 'controls' | 'archive';
const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: 'live', label: 'Live', hint: 'Latest price per account and symbol, its age and quality, and each collector.' },
  { id: 'plans', label: 'Collection plans', hint: 'What is collected, from which account, when, and how long it is kept.' },
  { id: 'quality', label: 'Quality', hint: 'Latency, gaps, frozen prices, spreads, spikes and deviation from other sources — compare brokers.' },
  { id: 'events', label: 'Events', hint: 'Outages, gaps, feed switches, backfills, archive, fingerprint checks, backups and disk.' },
  { id: 'backfill', label: 'Backfill', hint: 'Fetch bars from the broker’s history. Live bars are never overwritten.' },
  { id: 'controls', label: 'Controls', hint: 'Pause collection for everything or one account. Separate from order sending.' },
  { id: 'archive', label: 'Archive', hint: 'History files with fingerprints, and backups. Query history under Infrastructure › History.' },
];
const FRAMES = ['S1', 'M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const DAYS = [[1, 'Mon'], [2, 'Tue'], [3, 'Wed'], [4, 'Thu'], [5, 'Fri'], [6, 'Sat'], [7, 'Sun']] as const;
const when = (v?: string | null) => v ? new Date(v).toLocaleString() : '—';
const ago = (v?: number | string | null) => { const s = v == null || v === '' ? null : Number(v); return s == null || !Number.isFinite(s) ? '—' : s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : s < 3600 ? `${Math.round(s / 60)} min` : `${Math.round(s / 3600)} h`; };
const num = (v: any, d: any = 5) => v == null || v === '' || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(Number(d) || 0);
const scoreClass = (v?: number | string | null) => { const s = v == null ? null : Number(v); return s == null || !Number.isFinite(s) ? '' : s >= 80 ? 'ok' : s >= 50 ? 'warn' : 'bad'; };

export default function MarketData() {
  const [user, setUser] = useState<Row | null | undefined>(undefined), [tab, setTab] = useState<Tab>('live');
  const [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => { void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null)); }, []);
  if (user === undefined) return <div className="content"><p>Checking sign-in…</p></div>;
  if (user === null) return <SignIn title="MARKET DATA" done={s => setUser(s)} />;
  const canEdit = (user.roles ?? []).includes('TRADING_OPS');
  const current = TABS.find(t => t.id === tab)!;
  return <div className="cfg md">
    <div className="cfg-topbar"><span className="eyebrow">MARKET DATA</span>
      <nav className="cfg-nav" aria-label="Market data sections">{TABS.map(t =>
        <button key={t.id} aria-current={tab === t.id ? 'page' : undefined} onClick={() => setTab(t.id)}><span>{t.label}</span></button>)}</nav>
      <div className="cfg-user">{user.username}</div></div>
    <section className="cfg-main">
      <header className="cfg-head"><h1>{current.label}</h1><p>{current.hint}</p></header>
      <div className="cfg-pane">
        {tab === 'live' ? <Live /> : tab === 'plans' ? <Plans canEdit={canEdit} /> : tab === 'quality' ? <Quality /> : tab === 'events' ? <Events />
          : tab === 'backfill' ? <Backfill canEdit={canEdit} /> : tab === 'controls' ? <Controls canEdit={canEdit} /> : <Archive />}
      </div>
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

function useLoad<T>(path: string, every = 0): [T | null, string, () => Promise<void>] {
  const [data, setData] = useState<T | null>(null), [error, setError] = useState('');
  const load = async () => { try { setData(await api(path)); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); if (!every) return; const t = window.setInterval(() => void load(), every); return () => window.clearInterval(t); }, [path]);
  return [data, error, load];
}

/* ---------- live ---------- */

function Live() {
  const [latest, error] = useLoad<Row>('md/latest', 2000);
  const [collectors] = useLoad<Row>('md/collectors', 5000);
  const [plans] = useLoad<Row>('md/plans', 10000);
  const byKey = useMemo(() => Object.fromEntries((plans?.items ?? []).map((p: Row) => [`${p.account_id}|${p.broker_symbol}`, p])), [plans]);
  return <>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="md-collectors">{(collectors?.items ?? []).map((c: Row) => <div key={c.account_id} className={`md-collector ${c.state}`}>
      <span className={`infra-dot ${c.state === 'CONNECTED' ? 'OK' : c.state === 'RECONNECTING' ? 'WARN' : c.state === 'FAILED' ? 'FAIL' : ''}`} />
      <strong>{c.account_code}</strong><span>{c.state.toLowerCase()}</span>
      <small>{c.host ? `${c.host}:${c.port}` : ''}{c.lag_ms != null ? ` · lag ${Math.round(Number(c.lag_ms))} ms` : ''} · {c.subscribed} symbols · {Number(c.ticks_total).toLocaleString()} prices
        {c.backlog ? ` · ${c.backlog} waiting` : ''} · heartbeat {ago(c.heartbeat_age_seconds)} ago</small>
      {c.last_error && <small className="md-error">{c.last_error}</small>}</div>)}
      {collectors && !collectors.items.length && <p className="cfg-note">No collector has reported yet. Start a collection plan; the collector service picks it up within a few seconds.</p>}</div>
    <table className="cfg-table compact md-live"><thead><tr><th>Symbol</th><th>Account</th><th>Role</th><th className="num">Bid</th><th className="num">Ask</th>
      <th className="num">Spread</th><th className="num">Age</th><th className="num">Score</th></tr></thead>
      <tbody>{(latest?.items ?? []).map((l: Row) => { const p = byKey[`${l.account_id}|${l.broker_symbol}`] ?? {};
        const stale = Number(l.age_seconds) > (p.stale_seconds ?? 10);
        return <tr key={`${l.account_id}${l.broker_symbol}`}><td><strong>{l.broker_symbol}</strong>{p.instrument_symbol && p.instrument_symbol !== l.broker_symbol ? <small> · {p.instrument_symbol}</small> : null}</td>
          <td>{l.account_code}</td><td>{p.feed_role ? <span className={`cfg-pill ${p.active_feed ? 'ok' : ''}`}>{p.feed_role.toLowerCase()}{p.active_feed ? ' · active' : ''}</span> : '—'}</td>
          <td className="num">{num(l.bid, l.digits)}</td><td className="num">{num(l.ask, l.digits)}</td>
          <td className="num">{l.bid && l.ask ? ((Number(l.ask) - Number(l.bid)) * 10 ** Number(l.digits ?? 5)).toFixed(1) : '—'}</td>
          <td className={`num ${stale ? 'md-bad' : ''}`}>{ago(l.age_seconds)}</td>
          <td className="num"><span className={`md-score ${scoreClass(l.score)}`}>{l.score ?? '—'}</span></td></tr>; })}
        {latest && !latest.items.length && <tr><td colSpan={8} className="infra-placeholder">No prices yet.</td></tr>}</tbody></table>
  </>;
}

/* ---------- plans ---------- */

const BLANK: Row = { code: '', account_id: '', broker_symbol: '', instrument_id: '', purpose: 'TRADING_FEED', feed_role: 'PRIMARY', data_types: ['QUOTES', 'BARS'],
  bar_timeframes: ['M1'], depth_levels: 0, schedule: 'ALWAYS', window_start: '', window_end: '', window_days: [1, 2, 3, 4, 5], conflate_ms: 0, hot_days: 14,
  bars_hot_days: 365, archive: true, backfill_from: '', backfill_per_minute: 30, max_latency_ms: 1500, max_gap_seconds: 60, max_spread_points: '',
  stale_seconds: 10, on_breach: 'ALERT' };

function body(f: Row): Row {
  const out: Row = { ...f };
  for (const k of ['instrument_id', 'window_start', 'window_end', 'backfill_from', 'max_spread_points']) if (out[k] === '' || out[k] == null) out[k] = null;
  for (const k of ['depth_levels', 'conflate_ms', 'hot_days', 'bars_hot_days', 'backfill_per_minute', 'max_latency_ms', 'max_gap_seconds', 'stale_seconds']) out[k] = Number(out[k]);
  if (out.max_spread_points != null) out.max_spread_points = Number(out.max_spread_points);
  for (const k of ['id', 'version', 'status', 'status_reason', 'created_by', 'updated_by', 'created_at', 'updated_at', 'account_code', 'account_name', 'external_account_id',
    'connection_code', 'environment', 'platform', 'instrument_symbol', 'bid', 'ask', 'last_price_at', 'score', 'collector_state', 'collector_heartbeat_at', 'active_feed', 'pending_changes', 'digits']) delete out[k];
  return out;
}

function Plans({ canEdit }: { canEdit: boolean }) {
  const [plans, error, reload] = useLoad<Row>('md/plans', 10000);
  const [accounts] = useLoad<Row>('config/accounts');
  const [instruments] = useLoad<Row>('config/instruments');
  const [open, setOpen] = useState<Row | null>(null), [view, setView] = useState<'matrix' | 'list'>('matrix');
  const items: Row[] = plans?.items ?? [];
  const usedAccounts = [...new Map(items.map(p => [p.account_id, p.account_code])).entries()];
  const usedInstruments = [...new Map(items.map(p => [p.instrument_id ?? `sym:${p.broker_symbol}`, p.instrument_symbol ?? p.broker_symbol])).entries()];
  return <>
    <div className="infra-toolbar">
      <div className="md-seg" role="group" aria-label="View"><button aria-pressed={view === 'matrix'} onClick={() => setView('matrix')}>Matrix</button><button aria-pressed={view === 'list'} onClick={() => setView('list')}>List</button></div>
      <span className="infra-spacer" />
      {canEdit && <button className="primary" onClick={() => setOpen({ ...BLANK })}>New collection plan</button>}
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {view === 'matrix' ? <div className="infra-grid md-matrix"><table><thead><tr><th>Instrument / symbol</th>{usedAccounts.map(([id, code]) => <th key={id}>{code}</th>)}</tr></thead>
      <tbody>{usedInstruments.map(([key, label]) => <tr key={key}><td><strong>{label}</strong></td>{usedAccounts.map(([aid]) => {
        const p = items.find(x => x.account_id === aid && (x.instrument_id ?? `sym:${x.broker_symbol}`) === key);
        return <td key={aid} className={p ? 'md-cell' : 'infra-placeholder'} onClick={() => p && setOpen(p)}>{p ? <>
          <span className={`md-status ${p.status}`}>{p.status.toLowerCase()}</span> <span className="cfg-pill">{p.feed_role.toLowerCase()}{p.active_feed ? ' · active' : ''}</span>
          <small>{p.data_types.join(' · ').toLowerCase()}{p.data_types.includes('BARS') ? ` (${p.bar_timeframes.join(',')})` : ''}</small>
          {p.score != null && <span className={`md-score ${scoreClass(p.score)}`}>{p.score}</span>}</> : '—'}</td>; })}</tr>)}
        {!items.length && <tr><td className="infra-placeholder">No plans yet.</td></tr>}</tbody></table></div>
      : <table className="cfg-table"><thead><tr><th>Plan</th><th>Account</th><th>Symbol</th><th>Instrument</th><th>Purpose</th><th>Role</th><th>Data</th><th>When</th><th>Keep</th><th>Status</th></tr></thead>
        <tbody>{items.map(p => <tr key={p.id} onClick={() => setOpen(p)}><td>{p.code}</td><td>{p.account_code}</td><td>{p.broker_symbol}</td><td>{p.instrument_symbol ?? '—'}</td>
          <td>{p.purpose.replace('_', ' ').toLowerCase()}</td><td>{p.feed_role.toLowerCase()}</td><td>{p.data_types.join(', ').toLowerCase()}</td><td>{p.schedule.replace('_', ' ').toLowerCase()}</td>
          <td>{p.hot_days} d{p.archive ? ' → archive' : ''}</td><td><span className={`md-status ${p.status}`}>{p.status.toLowerCase()}</span>{p.pending_changes > 0 && <small> · awaiting approval</small>}</td></tr>)}</tbody></table>}
    {open && <PlanWindow plan={open} canEdit={canEdit} accounts={accounts?.items ?? []} instruments={instruments?.items ?? []}
      close={() => setOpen(null)} saved={p => { setOpen(p); void reload(); }} />}
  </>;
}

function PlanWindow({ plan, canEdit, accounts, instruments, close, saved }: { plan: Row; canEdit: boolean; accounts: Row[]; instruments: Row[];
  close: () => void; saved: (p: Row) => void }) {
  const [f, setF] = useState<Row>(() => ({ ...BLANK, ...plan, instrument_id: plan.instrument_id ?? '', max_spread_points: plan.max_spread_points ?? '',
    backfill_from: plan.backfill_from ?? '', window_start: plan.window_start ?? '', window_end: plan.window_end ?? '' }));
  const [est, setEst] = useState<Row | null>(null), [estError, setEstError] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [statusTo, setStatusTo] = useState<string | null>(null), [reason, setReason] = useState('');
  const isNew = !plan.id, locked = !canEdit || plan.status === 'ARCHIVED';
  const set = (k: string, v: unknown) => setF({ ...f, [k]: v });
  const toggle = (k: string, v: any) => set(k, f[k].includes(v) ? f[k].filter((x: any) => x !== v) : [...f[k], v]);
  useEffect(() => {
    if (!f.account_id || !f.broker_symbol || !f.code) { setEst(null); return; }
    const t = window.setTimeout(() => { void api('md/estimate', 'POST', body(f)).then(e => { setEst(e); setEstError(''); }).catch(e => { setEst(null); setEstError((e as Error).message); }); }, 400);
    return () => window.clearTimeout(t);
  }, [JSON.stringify(f)]);
  async function save(e: FormEvent) {
    e.preventDefault(); setError(''); setNotice('');
    try { const p = isNew ? await api('md/plans', 'POST', body(f)) : await api(`md/plans/${plan.id}`, 'PUT', { ...body(f), version: plan.version }); saved(p); setNotice('Saved.'); }
    catch (err) { setError((err as Error).message); }
  }
  async function changeStatus(e: FormEvent) {
    e.preventDefault(); setError('');
    try { const r = await api(`md/plans/${plan.id}/status`, 'POST', { status: statusTo, version: plan.version, reason });
      if (r?.change_request) { setNotice(`Sent for approval: ${r.change_request.summary}. A second person approves it under Config › Approvals.`); }
      else { saved(r); setNotice(`Plan ${String(statusTo).toLowerCase()}.`); }
      setStatusTo(null); setReason(''); }
    catch (err) { setError((err as Error).message); }
  }
  const account = accounts.find(a => a.id === f.account_id);
  return <FloatingWindow title={isNew ? 'New collection plan' : `Plan ${plan.code}`} subtitle={isNew ? 'What to collect, from where and how to keep it' : `${plan.status.toLowerCase()} · ${plan.account_code} · ${plan.broker_symbol}`}
    onClose={close} width={760} storageKey="corei-os-mini.window.md-plan"
    footer={<div className="cfg-actions">
      {!locked && <button className="primary" form="md-plan-form">{isNew ? 'Create (as draft)' : 'Save changes'}</button>}
      {!isNew && canEdit && plan.status !== 'ACTIVE' && plan.status !== 'ARCHIVED' && <button onClick={() => setStatusTo('ACTIVE')}>Start collecting…</button>}
      {!isNew && canEdit && plan.status === 'ACTIVE' && <button onClick={() => setStatusTo('PAUSED')}>Pause…</button>}
      {!isNew && canEdit && plan.status !== 'ARCHIVED' && <button className="danger" onClick={() => setStatusTo('ARCHIVED')}>Archive…</button>}
      <button onClick={close}>Close</button></div>}>
    <form id="md-plan-form" className="cfg-form md-plan" onSubmit={save}><fieldset disabled={locked} className="cfg-plain">
      <section className="cfg-section"><h4><span>1</span>What and from where</h4><div className="cfg-section-body">
        <div className="cfg-pair"><Field label="Plan code"><input aria-label="Plan code" required value={f.code} onChange={e => set('code', e.target.value.toUpperCase())} /></Field>
          <Field label="Account (source)"><select aria-label="Plan account" required disabled={!isNew} value={f.account_id} onChange={e => set('account_id', e.target.value)}>
            <option value="">Choose…</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.code} · {a.provider_code} {a.environment}</option>)}</select></Field></div>
        <div className="cfg-pair"><Field label="Broker symbol" hint="Exactly as the broker names it (e.g. XAUUSD.r)."><input aria-label="Broker symbol" required disabled={!isNew} value={f.broker_symbol} onChange={e => set('broker_symbol', e.target.value)} /></Field>
          <Field label="Instrument" hint="Needed for a trading feed; links sources of the same instrument for comparison and switching.">
            <select aria-label="Plan instrument" value={f.instrument_id} onChange={e => set('instrument_id', e.target.value)}><option value="">None</option>
              {instruments.map(i => <option key={i.id} value={i.id}>{i.symbol}</option>)}</select></Field></div>
        <div className="cfg-pair"><Field label="Purpose"><select aria-label="Plan purpose" value={f.purpose} onChange={e => set('purpose', e.target.value)}>
            {[['TRADING_FEED', 'Trading feed'], ['REFERENCE', 'Reference (comparison)'], ['RESEARCH', 'Research only'], ['MONITORING', 'Quality monitoring']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
          <Field label="Feed role"><select aria-label="Plan role" value={f.feed_role} onChange={e => set('feed_role', e.target.value)}>
            {[['PRIMARY', 'Primary'], ['BACKUP', 'Backup'], ['REFERENCE', 'Reference']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field></div>
        {account && account.platform !== 'MT5' && <p className="cfg-note">The collector for {account.platform} accounts is not built yet; the plan is kept and starts when it is.</p>}
      </div></section>
      <section className="cfg-section"><h4><span>2</span>Data</h4><div className="cfg-section-body">
        <Field label="Collect"><div className="cfg-checks">{[['QUOTES', 'Every price update (bid/ask)'], ['BARS', 'Bars'], ['DEPTH', 'Order-book depth']].map(([v, l]) =>
          <label key={v}><input type="checkbox" checked={f.data_types.includes(v)} onChange={() => toggle('data_types', v)} /> {l}</label>)}</div></Field>
        {f.data_types.includes('BARS') && <Field label="Bar timeframes"><div className="cfg-checks">{FRAMES.map(t => <label key={t}><input type="checkbox" checked={f.bar_timeframes.includes(t)} onChange={() => toggle('bar_timeframes', t)} /> {t}</label>)}</div></Field>}
        <div className="cfg-pair">{f.data_types.includes('DEPTH') && <Field label="Depth levels"><input aria-label="Depth levels" type="number" min={1} max={50} value={f.depth_levels} onChange={e => set('depth_levels', e.target.value)} /></Field>}
          <Field label="Store at most one price every (ms)" hint="0 = every update. Live prices and quality always use every update."><input aria-label="Conflate ms" type="number" min={0} max={60000} value={f.conflate_ms} onChange={e => set('conflate_ms', e.target.value)} /></Field></div>
      </div></section>
      <section className="cfg-section"><h4><span>3</span>When</h4><div className="cfg-section-body">
        <Field label="Schedule"><select aria-label="Plan schedule" value={f.schedule} onChange={e => set('schedule', e.target.value)}>
          {[['ALWAYS', 'Always'], ['SESSIONS', 'Trading days (Mon–Fri, provider holidays excluded)'], ['WHILE_DEPLOYED', 'Only while a deployment uses it'], ['WINDOW', 'A time window (UTC)']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
        {f.schedule === 'WINDOW' && <><div className="cfg-pair"><Field label="From (UTC)"><input aria-label="Window start" type="time" required value={f.window_start} onChange={e => set('window_start', e.target.value)} /></Field>
          <Field label="To (UTC)"><input aria-label="Window end" type="time" required value={f.window_end} onChange={e => set('window_end', e.target.value)} /></Field></div>
          <Field label="Days"><div className="cfg-checks">{DAYS.map(([d, l]) => <label key={d}><input type="checkbox" checked={f.window_days.includes(d)} onChange={() => toggle('window_days', d)} /> {l}</label>)}</div></Field></>}
      </div></section>
      <section className="cfg-section"><h4><span>4</span>Keep</h4><div className="cfg-section-body">
        <div className="cfg-pair"><Field label="Days of prices in the database"><input aria-label="Hot days" type="number" min={1} max={365} value={f.hot_days} onChange={e => set('hot_days', e.target.value)} /></Field>
          <Field label="Days of bars in the database"><input aria-label="Bars hot days" type="number" min={1} max={3650} value={f.bars_hot_days} onChange={e => set('bars_hot_days', e.target.value)} /></Field></div>
        <label className="cfg-check"><input type="checkbox" checked={f.archive} onChange={e => set('archive', e.target.checked)} /> Move older data to history files (Parquet, fingerprinted) instead of deleting it</label>
        <div className="cfg-pair"><Field label="Fetch history from (optional)"><input aria-label="Backfill from" type="date" value={f.backfill_from} onChange={e => set('backfill_from', e.target.value)} /></Field>
          <Field label="History requests per minute"><input aria-label="Backfill rate" type="number" min={1} max={600} value={f.backfill_per_minute} onChange={e => set('backfill_per_minute', e.target.value)} /></Field></div>
      </div></section>
      <section className="cfg-section"><h4><span>5</span>Quality rules</h4><div className="cfg-section-body">
        <div className="cfg-pair"><Field label="Max latency (ms)"><input aria-label="Max latency" type="number" min={1} value={f.max_latency_ms} onChange={e => set('max_latency_ms', e.target.value)} /></Field>
          <Field label="Max silence while open (s)"><input aria-label="Max gap" type="number" min={1} value={f.max_gap_seconds} onChange={e => set('max_gap_seconds', e.target.value)} /></Field></div>
        <div className="cfg-pair"><Field label="Price too old to trade after (s)"><input aria-label="Stale seconds" type="number" min={1} value={f.stale_seconds} onChange={e => set('stale_seconds', e.target.value)} /></Field>
          <Field label="Max spread (points, optional)"><input aria-label="Max spread" inputMode="decimal" value={f.max_spread_points} onChange={e => set('max_spread_points', e.target.value)} /></Field></div>
        <Field label="When a rule is broken"><select aria-label="On breach" value={f.on_breach} onChange={e => set('on_breach', e.target.value)}>
          <option value="ALERT">Alert</option><option value="SWITCH">Alert and switch to the backup feed (primary only)</option></select></Field>
      </div></section>
    </fieldset></form>
    <div className="cfg-panel md-estimate" aria-label="Size estimate"><header><strong>Size estimate</strong></header>
      {est ? <dl className="cfg-facts">
        <div><dt>Updates</dt><dd>{est.ticks_per_second}/s ({est.rate_source === 'MEASURED' ? 'measured' : 'typical'})</dd></div>
        <div><dt>Rows per day</dt><dd>{Number(est.rows_per_day).toLocaleString()}</dd></div>
        <div><dt>Database (steady)</dt><dd>{est.database_gb_steady} GB</dd></div>
        <div><dt>History per year</dt><dd>{est.archive_gb_per_year} GB</dd></div>
        <div><dt>Account symbols</dt><dd>{est.account_active_symbols} of {est.account_symbol_limit}</dd></div>
      </dl> : <p className="cfg-note">{estError || 'Fill in the code, account and symbol to see rows and storage per day.'}</p>}
      {est?.notes?.map((n: string) => <p key={n} className="cfg-note">{n}</p>)}</div>
    {plan.status_reason && <p className="cfg-note">Last status change: {plan.status_reason}</p>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {notice && <p role="status" className="cfg-ok">{notice}</p>}
    {statusTo && <div className="cfg-modal" role="dialog" aria-modal="true" aria-label="Change plan status"><form className="cfg-form cfg-modal-box" onSubmit={changeStatus}>
      <h3>{statusTo === 'ACTIVE' ? 'Start collecting' : statusTo === 'PAUSED' ? 'Pause collecting' : 'Archive this plan'}</h3>
      {statusTo === 'ACTIVE' && plan.environment === 'LIVE' && plan.purpose === 'TRADING_FEED' && <p className="cfg-note">A LIVE trading feed may need a second person’s approval.</p>}
      <Field label="Reason"><input aria-label="Status reason" required minLength={3} autoFocus value={reason} onChange={e => setReason(e.target.value)} /></Field>
      <div className="cfg-actions"><button className="primary">Confirm</button><button type="button" onClick={() => setStatusTo(null)}>Cancel</button></div></form></div>}
  </FloatingWindow>;
}

/* ---------- quality ---------- */

function Quality() {
  const [hours, setHours] = useState(24);
  const [data, error] = useLoad<Row>(`md/quality?hours=${hours}`, 30000);
  const [detail, setDetail] = useState<Row | null>(null), [minutes, setMinutes] = useState<Row[] | null>(null);
  useEffect(() => { if (!detail) return; void api(`md/quality/${detail.account_id}/${encodeURIComponent(detail.broker_symbol)}?minutes=120`).then(d => setMinutes(d.items)).catch(() => setMinutes([])); }, [detail]);
  const groups = useMemo(() => { const g: Record<string, Row[]> = {}; for (const r of data?.items ?? []) (g[r.instrument_symbol ?? r.broker_symbol] ??= []).push(r); return g; }, [data]);
  return <>
    <div className="infra-toolbar"><label>Period <select aria-label="Quality period" value={hours} onChange={e => setHours(Number(e.target.value))}>
      {[[1, 'Last hour'], [24, 'Last 24 hours'], [168, 'Last 7 days'], [720, 'Last 30 days']].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {Object.entries(groups).map(([name, rows]) => <section key={name} className="cfg-panel"><header><strong>{name}</strong><small>{rows.length > 1 ? 'best source first' : ''}</small></header>
      <table className="cfg-table compact"><thead><tr><th>Account</th><th>Connection</th><th className="num">Score</th><th className="num">Prices</th><th className="num">Latency p50 / p95</th>
        <th className="num">Spread avg / max</th><th className="num">Longest gap</th><th className="num">Spikes</th><th className="num">Deviation</th><th className="num">Empty min</th></tr></thead>
        <tbody>{rows.map(r => <tr key={r.account_id + r.broker_symbol} onClick={() => setDetail(r)} className={detail?.account_id === r.account_id && detail?.broker_symbol === r.broker_symbol ? 'selected' : ''}>
          <td>{r.account_code}</td><td>{r.connection_code}</td><td className="num"><span className={`md-score ${scoreClass(Number(r.score))}`}>{r.score}</span></td>
          <td className="num">{Number(r.ticks).toLocaleString()}</td><td className="num">{r.latency_p50_ms ?? '—'} / {r.latency_p95_ms ?? '—'} ms</td>
          <td className="num">{r.spread_avg ?? '—'} / {r.spread_max ?? '—'}</td><td className="num">{r.max_gap_seconds ?? '—'} s</td><td className="num">{r.spikes}</td>
          <td className="num">{r.deviation_bp != null ? `${r.deviation_bp} bp` : '—'}</td><td className="num">{r.empty_minutes}</td></tr>)}</tbody></table></section>)}
    {data && !data.items.length && <p className="cfg-note">No quality measured yet in this period.</p>}
    {detail && minutes && <section className="cfg-panel"><header><strong>{detail.account_code} · {detail.broker_symbol} · last 2 hours by minute</strong><button onClick={() => setDetail(null)}>Close</button></header>
      <div className="md-spark" aria-label="Score by minute">{[...minutes].reverse().map(m => <span key={m.minute} title={`${new Date(m.minute).toLocaleTimeString()} · score ${m.score} · ${m.ticks} prices`}
        className={`md-bar ${scoreClass(m.score)}`} style={{ height: `${Math.max(4, m.score)}%` }} />)}</div></section>}
  </>;
}

/* ---------- events ---------- */

const KINDS = ['', 'CONNECTED', 'DISCONNECTED', 'GAP', 'STALE', 'FROZEN', 'SPIKE', 'LATENCY', 'SPREAD', 'DEVIATION', 'FEED_SWITCH', 'FEED_RESTORED', 'BACKFILL', 'ARCHIVE', 'VERIFY', 'BACKUP', 'DISK', 'BACKLOG', 'ERROR'];

function Events() {
  const [kind, setKind] = useState('');
  const [data, error] = useLoad<Row>(`md/events${kind ? `?kind=${kind}` : ''}`, 5000);
  return <>
    <div className="infra-toolbar"><label>Kind <select aria-label="Event kind" value={kind} onChange={e => setKind(e.target.value)}>{KINDS.map(k => <option key={k} value={k}>{k ? k.replace('_', ' ').toLowerCase() : 'All'}</option>)}</select></label></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <table className="cfg-table compact"><thead><tr><th>Time</th><th>Severity</th><th>Kind</th><th>Account</th><th>Symbol</th><th>Detail</th></tr></thead>
      <tbody>{(data?.items ?? []).map((e: Row) => <tr key={e.id} className={`md-sev-${e.severity}`}><td>{when(e.at)}</td><td>{e.severity.toLowerCase()}</td><td>{e.kind.replace('_', ' ').toLowerCase()}</td>
        <td>{e.account_code ?? '—'}</td><td>{e.broker_symbol ?? '—'}</td>
        <td className="infra-diff">{Object.entries(e.detail ?? {}).map(([k, v]) => <span key={k} className="md-kv">{k.replace(/_/g, ' ')}: {typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>)}</td></tr>)}
        {data && !data.items.length && <tr><td colSpan={6} className="infra-placeholder">No events.</td></tr>}</tbody></table>
  </>;
}

/* ---------- backfill ---------- */

function Backfill({ canEdit }: { canEdit: boolean }) {
  const [data, error, reload] = useLoad<Row>('md/jobs', 5000);
  const [plans] = useLoad<Row>('md/plans');
  const [f, setF] = useState<Row>({ plan_id: '', timeframe: 'M1', range_from: '', range_to: '', reason: '' }), [msg, setMsg] = useState('');
  async function create(e: FormEvent) {
    e.preventDefault(); setMsg('');
    try { await api('md/jobs', 'POST', { ...f, range_from: new Date(f.range_from).toISOString(), range_to: new Date(f.range_to).toISOString() }); setF({ ...f, reason: '' }); await reload(); }
    catch (err) { setMsg((err as Error).message); }
  }
  return <>
    {canEdit && <form className="cfg-form cfg-panel md-backfill" onSubmit={create}><header><strong>Fetch history from the broker</strong></header>
      <div className="cfg-pair"><Field label="Plan"><select aria-label="Backfill plan" required value={f.plan_id} onChange={e => setF({ ...f, plan_id: e.target.value })}><option value="">Choose…</option>
        {(plans?.items ?? []).map((p: Row) => <option key={p.id} value={p.id}>{p.code} · {p.account_code} · {p.broker_symbol}</option>)}</select></Field>
        <Field label="Timeframe"><select aria-label="Backfill timeframe" value={f.timeframe} onChange={e => setF({ ...f, timeframe: e.target.value })}>{FRAMES.slice(1).map(t => <option key={t}>{t}</option>)}</select></Field></div>
      <div className="cfg-pair"><Field label="From"><input aria-label="Backfill start" type="datetime-local" required value={f.range_from} onChange={e => setF({ ...f, range_from: e.target.value })} /></Field>
        <Field label="To"><input aria-label="Backfill end" type="datetime-local" required value={f.range_to} onChange={e => setF({ ...f, range_to: e.target.value })} /></Field></div>
      <Field label="Reason"><input aria-label="Backfill reason" required minLength={3} value={f.reason} onChange={e => setF({ ...f, reason: e.target.value })} /></Field>
      <div className="cfg-actions"><button className="primary">Queue backfill</button><span className="cfg-note">Runs while the account’s collector is connected, at the plan’s request rate. Broker bars fill gaps only.</span></div>
      {msg && <p role="alert" className="cfg-error">{msg}</p>}</form>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <table className="cfg-table compact"><thead><tr><th>Created</th><th>Plan</th><th>Timeframe</th><th>Range</th><th>Progress</th><th>Status</th><th>By</th><th /></tr></thead>
      <tbody>{(data?.items ?? []).map((j: Row) => { const total = new Date(j.range_to).getTime() - new Date(j.range_from).getTime();
        const done = j.done_until ? Math.min(100, Math.round((new Date(j.done_until).getTime() - new Date(j.range_from).getTime()) / total * 100)) : 0;
        return <tr key={j.id}><td>{when(j.created_at)}</td><td>{j.plan_code} · {j.broker_symbol}</td><td>{j.timeframe}</td><td>{when(j.range_from)} → {when(j.range_to)}</td>
          <td><span className="md-progress"><span style={{ width: `${j.status === 'DONE' ? 100 : done}%` }} /></span> {j.bars_saved} bars</td>
          <td>{j.status.toLowerCase()}{j.error ? `: ${j.error}` : ''}</td><td>{j.requested_by}</td>
          <td>{canEdit && ['QUEUED', 'RUNNING'].includes(j.status) && <button onClick={() => void api(`md/jobs/${j.id}/cancel`, 'POST').then(reload)}>Cancel</button>}</td></tr>; })}</tbody></table>
  </>;
}

/* ---------- controls ---------- */

function Controls({ canEdit }: { canEdit: boolean }) {
  const [data, error, reload] = useLoad<Row>('md/controls');
  const [accounts] = useLoad<Row>('config/accounts');
  const [change, setChange] = useState<Row | null>(null), [reason, setReason] = useState(''), [msg, setMsg] = useState('');
  const all = (data?.items ?? []).find((c: Row) => c.scope === 'ALL');
  const scoped = Object.fromEntries((data?.items ?? []).filter((c: Row) => c.scope !== 'ALL').map((c: Row) => [c.scope, c]));
  async function apply(e: FormEvent) {
    e.preventDefault(); setMsg('');
    try { await api(`md/controls/${change!.scope}`, 'PUT', { paused: change!.paused, version: change!.version ?? 0, reason }); setChange(null); setReason(''); await reload(); }
    catch (err) { setMsg((err as Error).message); }
  }
  return <>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {all && <section className={`cfg-panel md-switch ${all.paused ? 'paused' : ''}`}><header><strong>All market-data collection</strong>
      <span className={`cfg-pill ${all.paused ? 'off' : 'ok'}`}>{all.paused ? 'PAUSED' : 'running'}</span>
      {canEdit && <button className={all.paused ? 'primary' : 'danger'} onClick={() => setChange({ scope: 'ALL', paused: !all.paused, version: all.version })}>{all.paused ? 'Resume…' : 'Pause everything…'}</button>}</header>
      <p className="cfg-note">{all.reason} · {all.updated_by} · {when(all.updated_at)}. Order sending has its own switch (Config › Order sending).</p></section>}
    <table className="cfg-table compact"><thead><tr><th>Account</th><th>Collection</th><th>Last change</th><th /></tr></thead>
      <tbody>{(accounts?.items ?? []).map((a: Row) => { const c = scoped[`account:${a.id}`];
        return <tr key={a.id}><td>{a.code}</td><td><span className={`cfg-pill ${c?.paused ? 'off' : 'ok'}`}>{c?.paused ? 'paused' : 'on'}</span></td>
          <td>{c ? `${c.reason} · ${c.updated_by} · ${when(c.updated_at)}` : '—'}</td>
          <td>{canEdit && <button onClick={() => setChange({ scope: `account:${a.id}`, paused: !c?.paused, version: c?.version ?? 0 })}>{c?.paused ? 'Resume…' : 'Pause…'}</button>}</td></tr>; })}</tbody></table>
    <p className="cfg-note">Disk guard, deviation limit, symbols per account, journal days and backups are policies under Config › Order sending › Policies.</p>
    {change && <div className="cfg-modal" role="dialog" aria-modal="true" aria-label="Collection switch"><form className="cfg-form cfg-modal-box" onSubmit={apply}>
      <h3>{change.paused ? 'Pause' : 'Resume'} {change.scope === 'ALL' ? 'all collection' : 'this account'}</h3>
      <Field label="Reason"><input aria-label="Switch reason" required minLength={3} autoFocus value={reason} onChange={e => setReason(e.target.value)} /></Field>
      {msg && <p role="alert" className="cfg-error">{msg}</p>}
      <div className="cfg-actions"><button className="primary">Confirm</button><button type="button" onClick={() => setChange(null)}>Cancel</button></div></form></div>}
  </>;
}

/* ---------- archive ---------- */

function Archive() {
  const [files, error] = useLoad<Row>('md/files');
  const [backups] = useLoad<Row>('md/backups');
  const size = (b: number) => b > 1e9 ? `${(b / 1e9).toFixed(2)} GB` : b > 1e6 ? `${(b / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1e3))} kB`;
  return <>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <section className="cfg-panel"><header><strong>Backups</strong></header>
      <table className="cfg-table compact"><thead><tr><th>Started</th><th>Kind</th><th>Result</th><th>Where</th><th className="num">Size / files</th></tr></thead>
        <tbody>{(backups?.items ?? []).map((b: Row) => <tr key={b.id}><td>{when(b.started_at)}</td><td>{b.kind.toLowerCase()}</td>
          <td><span className={`cfg-pill ${b.ok ? 'ok' : 'off'}`}>{b.ok ? 'ok' : 'failed'}</span>{b.error ? ` ${b.error}` : ''}</td><td className="infra-query" title={b.path}>{b.path ?? '—'}</td>
          <td className="num">{b.bytes ? size(b.bytes) : ''}{b.files != null ? ` ${b.files} files` : ''}</td></tr>)}
          {backups && !backups.items.length && <tr><td colSpan={5} className="infra-placeholder">No backup has run yet (nightly at 02:30 IST; set COREI_BACKUP_DIR on the server).</td></tr>}</tbody></table></section>
    <section className="cfg-panel"><header><strong>History files ({files?.items.length ?? 0})</strong></header>
      <table className="cfg-table compact"><thead><tr><th>Day</th><th>Kind</th><th>Account</th><th>Symbol</th><th className="num">Rows</th><th className="num">Size</th><th>Fingerprint</th><th>Checked</th></tr></thead>
        <tbody>{(files?.items ?? []).map((f: Row) => <tr key={f.id}><td>{f.day}</td><td>{f.kind.toLowerCase()}</td><td>{f.account_code ?? '—'}</td><td>{f.broker_symbol ?? '—'}</td>
          <td className="num">{f.rows != null ? Number(f.rows).toLocaleString() : '—'}</td><td className="num">{size(f.bytes)}</td>
          <td title={f.sha256}><code>{f.sha256.slice(0, 12)}…</code></td>
          <td><span className={`cfg-pill ${f.verify_status === 'OK' ? 'ok' : f.verify_status === 'RETIRED' ? '' : 'off'}`}>{f.verify_status.toLowerCase()}</span> {when(f.verified_at)}</td></tr>)}
          {files && !files.items.length && <tr><td colSpan={8} className="infra-placeholder">Nothing archived yet. Prices move here after their plan’s days in the database.</td></tr>}</tbody></table></section>
  </>;
}
