/* Strategy evidence and risk-minimum profiles on screen.
 *
 * Why it exists: approval and order sending used to need no evidence and no limits. These panels show what a version
 * has proven (and what is missing, with how to fix it), let a risk approver approve on evidence or with a recorded
 * exception where the profile allows one, copy a strategy to another timeframe, choose the governance profiles, and
 * size an account's limits from a profile (preview first; existing tighter limits are kept).
 * Must never: approve, change a limit or a policy without the person pressing the button; send a float as a money value
 * (equity is sent as typed text).
 * Dependencies: Configuration.api, StageShell helpers, nav.goto. Consumers: Configuration (strategy versions), TradingViews (Risk › Limits).
 * Architectural position: UI over /api/trading/governance, /strategy-versions/{id}/evidence, /risk/presets, /strategies/clone-timeframe.
 */
import { useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import { Msg, useData, type Row } from './StageShell';

const PROFILES: [string, string][] = [['NONE', 'None (sandbox: no gate)'], ['RETAIL', 'Retail'], ['PROFESSIONAL', 'Professional'], ['INSTITUTIONAL', 'Institutional']];
const STAGES = ['DRAFT', 'BACKTESTED', 'PAPER_PROVEN', 'APPROVED'];
const words = (v?: string) => String(v ?? '').toLowerCase().replaceAll('_', ' ');

/** What one version has proven, and approval on evidence or by recorded exception. */
export function VersionEvidence({ versionId, version, canApprove, onDone }: { versionId: string; version: number; canApprove: boolean; onDone: (msg: string) => void }) {
  const [ev, error, reload] = useData<Row>(`trading/strategy-versions/${versionId}/evidence`, 60_000);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  if (!ev) return <div className="gv-panel"><Msg error={error} />{!error && <p role="status">Loading evidence…</p>}</div>;
  const approve = async (exception?: string) => {
    setBusy(true); setMsg({ error: '', notice: '' });
    try { await api(`config/strategy-versions/${versionId}/approve`, 'POST', { version, ...(exception ? { exception } : {}) }); onDone(`v${ev.number} approved${exception ? ' by recorded exception' : ' on evidence'}.`); }
    catch (e) { setMsg({ error: (e as Error).message, notice: '' }); reload(); } finally { setBusy(false); }
  };
  const row = (c: Row) => <li key={c.code} className={!c.applies ? 'na' : c.ok ? 'pass' : 'fail'}>
    <b>{!c.applies ? '–' : c.ok ? '✓' : '✕'} {words(c.code)}</b> · {c.found} <small>(need {c.need}{!c.applies ? '; not required by this profile' : ''})</small>
    {c.applies && !c.ok && c.fix && <div className="cfg-note">{c.fix}</div>}</li>;
  return <div className="gv-panel" aria-label={`Evidence of v${ev.number}`}>
    <ol className="gv-stages">{STAGES.map(s => <li key={s} className={s === ev.stage ? 'now' : STAGES.indexOf(s) < STAGES.indexOf(ev.stage) ? 'done' : ''}>{words(s)}</li>)}</ol>
    <p className="cfg-note">Profile <b>{words(ev.policy.profile)}</b> · {ev.timeframe}. {ev.backtest ? `Backtest ${ev.backtest.metrics.trades} trades, net ${ev.backtest.metrics.net_pnl} ${ev.backtest.metrics.currency ?? ''}, max drawdown ${ev.backtest.metrics.max_drawdown_pct}%.` : 'No backtest of these exact parameters yet.'}
      {' '}<button className="link" type="button" onClick={() => goto('Strategies')}>Backtest in Strategies ↗</button></p>
    <strong>To approve</strong><ul className="gv-checks">{ev.checks.map(row)}</ul>
    <strong>To run on LIVE (paper record)</strong><ul className="gv-checks">{ev.live_checks.map(row)}</ul>
    {ev.exception && <p className="cfg-note">Approved by exception: {ev.exception}</p>}
    <Msg error={msg.error || error} notice={msg.notice} />
    {ev.state === 'DRAFT' && canApprove && <div className="cfg-actions">
      <button type="button" className="primary" disabled={busy || !ev.ok_for_approval} onClick={() => { if (window.confirm(`Approve v${ev.number}? Its rules and parameters can no longer be changed.`)) void approve(); }}>
        {ev.ok_for_approval ? 'Approve on evidence' : 'Evidence incomplete'}</button>
      {!ev.ok_for_approval && ev.policy.allow_exception && <>
        <input aria-label="Exception reason" placeholder="Reason for approving without the evidence (recorded)" value={reason} onChange={e => setReason(e.target.value)} />
        <button type="button" disabled={busy || reason.trim().length < 5} onClick={() => void approve(reason.trim())}>Approve by exception</button></>}
      {!ev.ok_for_approval && !ev.policy.allow_exception && <small className="cfg-note">The {words(ev.policy.profile)} profile allows no exception.</small>}
    </div>}
  </div>;
}

/** Copy a version to another timeframe as a new draft strategy (its evidence starts from nothing). */
export async function copyToTimeframe(versionId: string, current: string): Promise<string | null> {
  const tf = window.prompt(`Copy to which timeframe? (now ${current}; one of M1, M5, M15, M30, H1, H4, D1). The copy is a new draft and needs its own backtest.`)?.trim().toUpperCase();
  if (!tf) return null;
  const out = await api('trading/strategies/clone-timeframe', 'POST', { version_id: versionId, timeframe: tf });
  return `${out.strategy.code} created as a draft on ${tf}.`;
}

/** Governance profiles and limit presets (Risk › Limits). */
export function RiskProfiles({ canRisk, onApplied }: { canRisk: boolean; onApplied: () => void }) {
  const [gov, error, reload] = useData<Row>('trading/governance', 60_000);
  const [accounts] = useData<Row>('config/accounts', 60_000);
  const [form, setForm] = useState({ profile: 'PROFESSIONAL', account_id: '', equity: '' });
  const [preview, setPreview] = useState<Row | null>(null);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const call = async (dry: boolean) => {
    setMsg({ error: '', notice: '' });
    const reason = dry ? 'preview' : window.prompt('Reason for applying these limits (audit log)', `${words(form.profile)} profile`)?.trim();
    if (!reason || reason.length < 5) { if (!dry) setMsg({ error: 'Nothing applied: a reason of at least 5 characters is needed.', notice: '' }); return; }
    try {
      const r = await api('trading/risk/presets', 'POST', { profile: form.profile, account_id: form.account_id, reason, dry_run: dry, ...(form.equity.trim() ? { equity: form.equity.trim() } : {}) });
      setPreview(r);
      if (!dry) { setMsg({ error: '', notice: `Applied to ${r.account}. Tighter limits that already existed were kept.` }); onApplied(); reload(); }
    } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); }
  };
  const setPolicy = async (key: string, profile: string) => {
    const reason = window.prompt(`Reason for changing the ${words(key)} profile to ${words(profile)} (audit log)`)?.trim();
    if (!reason || reason.length < 5) return;
    try { await api(`trading/governance/${key}`, 'PUT', { profile, overrides: {}, reason }); setMsg({ error: '', notice: `${words(key)}: ${words(profile)}.` }); reload(); }
    catch (e) { setMsg({ error: (e as Error).message, notice: '' }); }
  };
  const demo = gov?.demo_unmet ?? [];
  return <section className="gv-risk" aria-label="Risk profiles">
    <div className="gv-policies">
      {(['strategy_evidence', 'risk_minimum'] as const).map(k => <label key={k}>{k === 'strategy_evidence' ? 'Strategy evidence' : 'Risk minimum'}
        <select aria-label={`${k} profile`} disabled={!canRisk || !gov} value={gov?.[k]?.profile ?? ''} onChange={e => void setPolicy(k, e.target.value)}>
          {PROFILES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>)}
      <small className="cfg-note">Retail: lighter evidence and wider limits. Professional: costs and out-of-sample required. Institutional: strictest, no approval exceptions, DEMO limits enforced at sending too. Single values can be overridden.</small>
    </div>
    {demo.length > 0 && <p role="alert" className="cfg-error">DEMO accounts below the minimum: {demo.map((u: Row) => u.detail).join(' ')}</p>}
    {canRisk && <form className="gv-preset" onSubmit={e => { e.preventDefault(); void call(true); }}>
      <b>Size limits from a profile</b>
      <label>Account <select aria-label="Preset account" required value={form.account_id} onChange={e => { setForm({ ...form, account_id: e.target.value }); setPreview(null); }}>
        <option value="">Choose…</option>{(accounts?.items ?? []).map((a: Row) => <option key={a.id} value={a.id}>{a.code} · {words(a.environment ?? a.connection_environment ?? '')}</option>)}</select></label>
      <label>Profile <select aria-label="Preset profile" value={form.profile} onChange={e => { setForm({ ...form, profile: e.target.value }); setPreview(null); }}>
        {PROFILES.filter(p => p[0] !== 'NONE').map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      <label>Equity<input aria-label="Preset equity" inputMode="decimal" placeholder="blank: measured equity" value={form.equity} onChange={e => { setForm({ ...form, equity: e.target.value }); setPreview(null); }} /></label>
      <button disabled={!form.account_id}>Preview</button>
      <button type="button" className="primary" disabled={!preview} onClick={() => void call(false)}>Apply</button>
    </form>}
    <Msg error={msg.error || error} notice={msg.notice} />
    {preview && <table className="cfg-table compact"><caption>{preview.account}: equity {preview.equity} {preview.currency} ({preview.equity_source}) · {words(preview.profile)}</caption>
      <thead><tr><th>Limit</th><th>Profile value</th><th>Basis</th><th>Now</th><th>Result</th></tr></thead>
      <tbody>{preview.limits.map((l: Row) => <tr key={l.kind}><td>{words(l.kind)}</td><td><b>{l.value}</b></td><td>{l.basis}</td><td>{l.current ?? '—'}</td>
        <td>{l.action === 'KEEP_TIGHTER' ? 'kept (yours is tighter)' : words(l.action)}</td></tr>)}</tbody></table>}
  </section>;
}
