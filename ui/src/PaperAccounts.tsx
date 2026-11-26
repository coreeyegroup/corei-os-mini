/* Paper accounts and FX rates.
 *
 * Why it exists: paper trading needed a broker account, one firm-wide amount and one currency, and charged no cost.
 * Here a person creates as many paper accounts as needed, each with its own currency (USD, INR, EUR … any ISO code),
 * starting amount, market scope (any · forex · India · custom), costs (per lot, % of value, minimum), slippage and price
 * source, optionally with risk limits sized on the amount. Each account can then run any strategy in a workspace or a
 * deployment. Funds can be changed; "Start again" makes a fresh account with the same settings and archives the old
 * one with its history. FxRates keeps the rates a person enters for currencies no collected pair converts.
 * Must never: send money values as binary floats (amounts, costs and rates are sent as typed text); delete history.
 * Each row opens the account's own page (PaperAccountDetail): balance, equity, positions, orders, history,
 * performance and an order ticket, as a broker terminal shows a real account.
 * Dependencies: Configuration.api, StageShell helpers, nav.goto, PaperAccountDetail, userSettings. Consumers: Configuration (sections "paper", "fx").
 * Architectural position: UI over /api/trading/paper-accounts and /api/trading/fx-rates.
 */
import { useMemo, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import './my-workspaces.css';
import { PaperAccountDetail } from './PaperAccountDetail';
import { Kpis, Msg, useData, when, type Row } from './StageShell';
import { Info } from './Info';
import { fmtMoney, fmtNum, setting, useSettings } from './userSettings';

const SCOPES: [string, string][] = [['ANY', 'Any instrument'], ['FOREX', 'Forex & metals'], ['INDIA', 'Indian markets (NSE · BSE · MCX · INR)'], ['CUSTOM', 'Custom list']];
const CLASSES = ['FX', 'METAL', 'INDEX', 'COMMODITY', 'CRYPTO', 'EQUITY', 'FUTURE', 'OPTION'];
const PROFILES: [string, string][] = [['NONE', 'No limits now'], ['RETAIL', 'Retail limits'], ['PROFESSIONAL', 'Professional limits'], ['INSTITUTIONAL', 'Institutional limits']];
const DEC = /^\d+(\.\d{1,10})?$/;
const words = (v?: string) => String(v ?? '').toLowerCase().replaceAll('_', ' ');
const blank = { code: '', name: '', currency: 'USD', amount: '100000', scope: 'ANY', asset_classes: [] as string[], markets: '', instruments: [] as string[],
  commission_per_lot: '0', commission_pct: '0', min_commission: '0', slippage_ticks: '', price_source_id: '', risk_profile: 'NONE', notes: '' };
type Form = typeof blank;

function body(f: Form) {
  const custom = f.scope === 'CUSTOM';
  return { name: f.name.trim(), scope: f.scope, asset_classes: custom ? f.asset_classes : [], markets: custom ? f.markets.split(',').map(x => x.trim().toUpperCase()).filter(Boolean) : [],
    instruments: custom ? f.instruments : [], commission_per_lot: f.commission_per_lot || '0', commission_pct: f.commission_pct || '0', min_commission: f.min_commission || '0',
    slippage_ticks: f.slippage_ticks === '' ? null : Number(f.slippage_ticks), price_source_id: f.price_source_id || null, notes: f.notes };
}

export function PaperAccounts({ canEdit }: { canEdit: boolean }) {
  useSettings();
  const [archived, setArchived] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [data, error, reload] = useData<Row>(`trading/paper-accounts${archived ? '?archived=true' : ''}`, 15_000, [archived]);
  const [ins] = useData<Row>('config/instruments', 120_000);
  const [conns] = useData<Row>('config/connections', 120_000);
  const [form, setForm] = useState<Form | null>(null);
  const [editing, setEditing] = useState<Row | null>(null);
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [busy, setBusy] = useState(false);
  const items: Row[] = data?.items ?? [];
  const currencies: string[] = data?.currencies ?? ['USD', 'INR', 'EUR', 'GBP', 'JPY'];
  const sources = (conns?.items ?? []).filter((c: Row) => c.purpose !== 'TRADING' && c.environment !== 'PAPER');
  const bad = form ? (['commission_per_lot', 'commission_pct', 'min_commission'] as const).filter(k => form[k] && !DEC.test(form[k])) : [];
  const run = async (f: () => Promise<Row>, ok: (r: Row) => string) => {
    setBusy(true); setMsg({ error: '', notice: '' });
    try { const r = await f(); setMsg({ error: '', notice: ok(r) }); reload(); return r; } catch (e) { setMsg({ error: (e as Error).message, notice: '' }); return null; } finally { setBusy(false); }
  };
  const open = (a?: Row) => {
    setEditing(a ?? null);
    setForm(a ? { ...blank, code: a.code, name: a.name, currency: a.currency, amount: a.starting_amount, scope: a.scope, asset_classes: a.asset_classes ?? [],
      markets: (a.markets ?? []).join(', '), instruments: a.instruments ?? [], commission_per_lot: a.commission_per_lot, commission_pct: a.commission_pct,
      min_commission: a.min_commission, slippage_ticks: a.slippage_ticks ?? '', price_source_id: a.price_source_id ?? '', notes: a.notes ?? '' } : { ...blank });
  };
  const save = async () => {
    if (!form) return;
    if (editing) {
      const reason = window.prompt('Reason for changing this paper account (audit log)', 'Paper account settings')?.trim();
      if (!reason || reason.length < 5) return;
      const r = await run(() => api(`trading/paper-accounts/${editing.id}`, 'PUT', { ...body(form), version: editing.version, reason }), x => `${x.code} saved.`);
      if (r) setForm(null);
    } else {
      const r = await run(() => api('trading/paper-accounts', 'POST', { ...body(form), code: form.code.trim().toUpperCase(), currency: form.currency.trim().toUpperCase(),
        amount: form.amount.trim(), risk_profile: form.risk_profile, reason: `New paper account ${form.code.trim().toUpperCase()}` }),
        x => `${x.code} created with ${x.starting_amount} ${x.currency}. Use it in a workspace (Paper) or a paper deployment.`);
      if (r) setForm(null);
    }
  };
  const def = setting<string>('trading.paperAccount');
  const sorted = [...items].sort((x, y) => Number(y.code === def) - Number(x.code === def));
  const live = items.filter(a => a.status !== 'ARCHIVED');
  const byCcy = Object.entries(live.reduce((m: Record<string, number>, a) => ({ ...m, [a.currency]: (m[a.currency] ?? 0) + 1 }), {}));
  const formEl = form && <form className="pa-form" aria-label={editing ? `Edit ${editing.code}` : 'New paper account'} onSubmit={e => { e.preventDefault(); if (!bad.length) void save(); }}>
      <h3>{editing ? `Settings · ${editing.code}` : 'New paper account'}<button type="button" className="link" aria-label="Close form" onClick={() => setForm(null)}>✕</button></h3>
      <fieldset><legend>Account</legend><div className="dk-grid">
        <label>Code <input required disabled={!!editing} pattern="[A-Za-z][A-Za-z0-9_]{1,31}" value={form.code} onChange={e => setForm({ ...form, code: e.target.value.toUpperCase() })} placeholder="PAPER_USD_1" /></label>
        <label>Name <input required maxLength={120} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} placeholder="Forex practice" /></label>
        <label>Currency <input aria-label="Currency" required disabled={!!editing} list="pa-ccy" pattern="[A-Za-z]{3}" value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value.toUpperCase() })} />
          <datalist id="pa-ccy">{currencies.map(c => <option key={c} value={c} />)}</datalist></label>
        <label>Starting amount <input aria-label="Starting amount" required disabled={!!editing} inputMode="decimal" pattern="\d+(\.\d{1,2})?" value={form.amount} onChange={e => setForm({ ...form, amount: e.target.value })} /></label>
        <label>Markets <select aria-label="Scope" value={form.scope} onChange={e => setForm({ ...form, scope: e.target.value })}>{SCOPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
        {!editing && <label>Risk limits <select aria-label="Risk limits" value={form.risk_profile} onChange={e => setForm({ ...form, risk_profile: e.target.value })}>{PROFILES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>}
      </div>
      {form.scope === 'CUSTOM' && <div className="pa-custom">
        <div className="dk-chips" aria-label="Asset classes">{CLASSES.map(c => <label key={c} className="dk-chip"><input type="checkbox" checked={form.asset_classes.includes(c)}
          onChange={() => setForm({ ...form, asset_classes: form.asset_classes.includes(c) ? form.asset_classes.filter(x => x !== c) : [...form.asset_classes, c] })} />{words(c)}</label>)}</div>
        <label>Markets / exchanges <small>(comma separated, e.g. NSE, MCX; blank: any)</small><input aria-label="Markets" value={form.markets} onChange={e => setForm({ ...form, markets: e.target.value })} /></label>
        <InstrumentPick all={ins?.items ?? []} value={form.instruments} onChange={v => setForm({ ...form, instruments: v })} />
      </div>}</fieldset>
      <fieldset><legend>Costs & fills</legend><div className="dk-grid">
        <label>Commission per lot <small>({form.currency || 'account currency'})</small><input aria-label="Commission per lot" inputMode="decimal" aria-invalid={bad.includes('commission_per_lot')} value={form.commission_per_lot} onChange={e => setForm({ ...form, commission_per_lot: e.target.value })} /></label>
        <label>Commission % of value <small>(e.g. 0.03 for Indian brokerage)</small><input aria-label="Commission percent" inputMode="decimal" aria-invalid={bad.includes('commission_pct')} value={form.commission_pct} onChange={e => setForm({ ...form, commission_pct: e.target.value })} /></label>
        <label>Minimum per fill <small>({form.currency || 'account currency'})</small><input aria-label="Minimum commission" inputMode="decimal" aria-invalid={bad.includes('min_commission')} value={form.min_commission} onChange={e => setForm({ ...form, min_commission: e.target.value })} /></label>
        <label>Slippage (ticks) <small>(blank: firm setting)</small><input aria-label="Slippage ticks" type="number" min={0} max={1000} value={form.slippage_ticks} onChange={e => setForm({ ...form, slippage_ticks: e.target.value })} /></label>
        <label>Price source <select aria-label="Price source" value={form.price_source_id} onChange={e => setForm({ ...form, price_source_id: e.target.value })}>
          <option value="">Freshest feed of each instrument</option>{sources.map((c: Row) => <option key={c.id} value={c.id}>{c.code} · {words(c.environment)}</option>)}</select></label>
        <label>Notes <input maxLength={1000} value={form.notes} onChange={e => setForm({ ...form, notes: e.target.value })} /></label>
      </div></fieldset>
      {bad.length > 0 && <p className="cfg-error" role="alert">Use plain numbers for costs, e.g. 7 or 0.03.</p>}
      <span className="mw-actions"><button type="button" onClick={() => setForm(null)}>Cancel</button><button className="primary" disabled={busy || bad.length > 0}>{editing ? 'Save' : 'Create paper account'}</button></span>
    </form>;
  if (selected) return <div className="cfg-pane pa">
    {formEl}
    <PaperAccountDetail id={selected} canTrade={canEdit} back={() => { setSelected(null); reload(); }} edit={a => open(a)} />
  </div>;
  return <div className="cfg-pane pa" aria-label="Paper accounts">
    <div className="intel-bar pa-toolbar">
      {canEdit && <button className="primary" onClick={() => open()}>+ New paper account</button>}
      <label className="dk-radio"><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} /> Show archived</label>
      <Info label="About paper accounts">Simulated accounts fill at the live price of each instrument's feed plus slippage and costs; nothing is ever sent to a broker. Open an account to see it as a broker terminal shows it and to place paper orders. Run strategies on them from a workspace (environment Paper) or a paper deployment.</Info>
    </div>
    <Kpis items={[['Accounts', live.length, byCcy.map(([c, n]) => `${n} ${c}`).join(' · ') || 'none yet'],
      ['Open positions', live.reduce((n, a) => n + Number(a.open_positions ?? 0), 0)],
      ['Strategy runs', live.reduce((n, a) => n + Number(a.runs ?? 0), 0)],
      ['Simulated fills', live.reduce((n, a) => n + Number(a.fills ?? 0), 0)]]} />
    <Msg error={msg.error || error} notice={msg.notice} />
    {formEl}
    {items.length > 0 && <table className="cfg-table pa-table"><thead><tr><th>Account</th><th className="num">Balance</th><th className="num">Equity</th><th className="num">Return</th><th className="num">Open</th><th className="num">Runs · fills</th><th>Markets</th><th>Costs</th><th /></tr></thead>
      <tbody>{sorted.map(a => <tr key={a.id} className={`pa-row ${a.status === 'ARCHIVED' ? 'archived' : ''}`} tabIndex={0} aria-label={`Open ${a.code}`}
        onClick={e => { if (!(e.target as Element).closest('button')) setSelected(a.id); }} onKeyDown={e => { if (e.key === 'Enter' && e.target === e.currentTarget) setSelected(a.id); }}>
        <td><span className="pa-ccy">{a.currency}</span><b>{a.code}</b>{a.code === def && <span className="pd-badge">default</span>}<br /><small>{a.name}{a.cloned_from_code ? ` · from ${a.cloned_from_code}` : ''}{a.status === 'ARCHIVED' ? ' · archived' : ''}</small></td>
        <td className="num">{fmtMoney(a.starting_amount)}</td>
        <td className="num"><b>{fmtMoney(a.equity)}</b>{!a.complete && <><br /><small className="dk-warn" title="Add a rate under FX rates">no rate for {a.missing_fx.join(', ') || 'a holding'}</small></>}</td>
        <td className={`num ${String(a.return_pct).startsWith('-') ? 'neg' : Number(a.return_pct) ? 'pos' : ''}`}>{fmtNum(a.return_pct)}%<br /><small>dd {fmtNum(a.drawdown_pct)}%</small></td>
        <td className="num">{a.open_positions}</td><td className="num">{a.runs} · {a.fills}</td>
        <td><small>{SCOPES.find(s => s[0] === a.scope)?.[1]}{a.scope === 'CUSTOM' ? `: ${[...(a.asset_classes ?? []), ...(a.markets ?? [])].join(', ') || `${(a.instruments ?? []).length} instrument(s)`}` : ''}</small></td>
        <td><small>{Number(a.commission_per_lot) ? `${a.commission_per_lot}/lot ` : ''}{Number(a.commission_pct) ? `${a.commission_pct}% ` : ''}{Number(a.min_commission) ? `min ${a.min_commission} ` : ''}{a.slippage_ticks != null ? `· ${a.slippage_ticks} tick slip` : ''}{!Number(a.commission_per_lot) && !Number(a.commission_pct) && !Number(a.min_commission) ? 'none' : ''}</small></td>
        <td className="pa-actions"><button className="primary" onClick={() => setSelected(a.id)}>Open</button>{canEdit && a.status !== 'ARCHIVED' && <>
          <button onClick={() => open(a)}>Settings</button>
          <button onClick={() => { const v = window.prompt(`New amount for ${a.code} (${a.currency}). Equity becomes this amount plus its realised and open P&L.`, a.starting_amount)?.trim();
            if (v && /^\d+(\.\d{1,2})?$/.test(v)) void run(() => api(`trading/paper-accounts/${a.id}/funds`, 'PUT', { amount: v, reason: `Funds set to ${v} ${a.currency}` }), x => `${x.code}: amount ${x.starting_amount} ${x.currency}.`);
            else if (v) setMsg({ error: 'Use a plain amount, e.g. 50000 or 50000.50.', notice: '' }); }}>Funds…</button>
          <button onClick={() => { const code = window.prompt(`Start again: code for the new account (${a.code} is archived with its history; settings are copied).`, `${a.code.slice(0, 28)}_2`)?.trim().toUpperCase();
            if (!code) return; const amount = window.prompt(`Starting amount (${a.currency})`, a.starting_amount)?.trim();
            if (!amount || !/^\d+(\.\d{1,2})?$/.test(amount)) return;
            void run(() => api(`trading/paper-accounts/${a.id}/clone`, 'POST', { code, amount, reason: `Start again from ${a.code}` }), x => `${x.code} started with ${x.starting_amount} ${x.currency}; ${a.code} archived.`); }}>Start again…</button>
          <button onClick={() => { const r = window.prompt(`Archive ${a.code}? Its history stays. Reason:`)?.trim(); if (r && r.length >= 5) void run(() => api(`trading/paper-accounts/${a.id}/archive`, 'POST', { reason: r }), x => `${x.code} archived.`); }}>Archive</button>
        </>}</td></tr>)}</tbody></table>}
    {data && !items.length && !form && <div className="pa-empty"><b>No paper account yet</b><p>Create one per idea — for example a USD forex account and an INR account for NSE and MCX. Each has its own currency, amount, markets and costs.</p>
</div>}
  </div>;
}

function InstrumentPick({ all, value, onChange }: { all: Row[]; value: string[]; onChange: (v: string[]) => void }) {
  const [q, setQ] = useState('');
  const shown = useMemo(() => all.filter(i => !q || `${i.symbol} ${i.name} ${i.market ?? ''}`.toLowerCase().includes(q.toLowerCase())).slice(0, 200), [all, q]);
  return <div className="dk-field"><span>Instruments <small>{value.length} chosen (blank: any)</small></span>
    <input type="search" aria-label="Search instruments" placeholder="Search…" value={q} onChange={e => setQ(e.target.value)} />
    <div className="dk-list">{shown.map(i => <label key={i.id} className="dk-radio"><input type="checkbox" checked={value.includes(i.id)}
      onChange={() => onChange(value.includes(i.id) ? value.filter(x => x !== i.id) : [...value, i.id])} />{i.symbol} <small>{words(i.asset_class)}{i.market ? ` · ${i.market}` : ''} · {i.quote_currency}</small></label>)}</div></div>;
}

export function FxRates({ canEdit }: { canEdit: boolean }) {
  const [data, error, reload] = useData<Row>('trading/fx-rates', 60_000);
  const [f, setF] = useState({ base: 'USD', quote: 'INR', rate: '', note: '' });
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const ok = /^\d+(\.\d{1,12})?$/.test(f.rate) && Number(f.rate) > 0 && f.base !== f.quote;
  return <div className="cfg-pane" aria-label="FX rates">
    <p className="cfg-note">Accounts are valued in their own currency. A rate comes first from a collected FX pair (direct or inverse), then a cross through USD or EUR,
      and only then from a rate entered here. Holdings that no rate converts are shown as not valued, never guessed.</p>
    <Msg error={msg.error || error} notice={msg.notice} />
    {canEdit && <form className="dk-grid" aria-label="Set a rate" onSubmit={e => { e.preventDefault(); if (!ok) return;
      void api('trading/fx-rates', 'PUT', { base: f.base.toUpperCase(), quote: f.quote.toUpperCase(), rate: f.rate, note: f.note })
        .then(r => { setMsg({ error: '', notice: `1 ${r.base} = ${r.rate} ${r.quote} saved.` }); setF({ ...f, rate: '', note: '' }); reload(); })
        .catch(x => setMsg({ error: (x as Error).message, notice: '' })); }}>
      <label>1 unit of <input aria-label="Base currency" pattern="[A-Za-z]{3}" value={f.base} onChange={e => setF({ ...f, base: e.target.value.toUpperCase() })} /></label>
      <label>equals <input aria-label="Rate" inputMode="decimal" aria-invalid={!!f.rate && !ok} value={f.rate} onChange={e => setF({ ...f, rate: e.target.value })} placeholder="84.25" /></label>
      <label>of <input aria-label="Quote currency" pattern="[A-Za-z]{3}" value={f.quote} onChange={e => setF({ ...f, quote: e.target.value.toUpperCase() })} /></label>
      <label>Source / note <input aria-label="Rate note" maxLength={200} value={f.note} onChange={e => setF({ ...f, note: e.target.value })} placeholder="RBI reference rate" /></label>
      <button className="primary" disabled={!ok}>Save rate</button></form>}
    <table className="cfg-table compact"><thead><tr><th>Pair</th><th>Rate</th><th>Note</th><th>Set</th><th /></tr></thead>
      <tbody>{(data?.items ?? []).map((r: Row) => <tr key={r.base + r.quote}><td>{r.base}/{r.quote}</td><td>{r.rate}</td><td>{r.note || '—'}</td>
        <td>{when(r.set_at)} · {r.set_by}</td>
        <td>{canEdit && <button onClick={() => { if (window.confirm(`Remove the ${r.base}/${r.quote} rate?`)) void api(`trading/fx-rates/${r.base}/${r.quote}`, 'DELETE').then(() => { setMsg({ error: '', notice: 'Removed.' }); reload(); }).catch(x => setMsg({ error: (x as Error).message, notice: '' })); }}>Remove</button>}</td></tr>)}</tbody></table>
    {data && !data.items.length && <p className="cfg-note">No rate entered. An INR account trading USD-quoted instruments needs a USD/INR rate if no USDINR feed is collected.</p>}
  </div>;
}
