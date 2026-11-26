/* Paper account page: one simulated account shown the way a broker terminal shows a real one.
 *
 * Why it exists: a paper account could be created but not opened. A trader expects what MT5, cTrader or a Zerodha
 * console shows for an account: balance, equity, open P&L, margin-style exposure, open positions with a close button,
 * working orders, trade history, performance and an equity curve — and a ticket to place an order on it.
 * Purpose: header KPIs and tabs (Positions, Orders, History, Performance, Runs, Activity) over
 * GET /trading/paper-accounts/{id}; an order ticket (POST /trading/orders, mode PAPER); close a position
 * (POST /trading/portfolio/flatten, mode PAPER) and cancel a working order.
 * Must never: send anything with mode BROKER (every request here is PAPER); send money, price or quantity as a binary
 * float (typed text is sent as-is and the server parses it as Decimal); resend an order — each ticket has its own key,
 * so a double click is one order.
 * Dependencies: Configuration.api, StageShell, userSettings formatters. Consumers: PaperAccounts.
 * Architectural position: UI over the paper account, OMS and portfolio APIs.
 */
import { useMemo, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';
import { Msg, useData, type Row } from './StageShell';
import { fmtMoney, fmtNum, fmtTime, setting, useSettings } from './userSettings';

const TABS = ['Positions', 'Orders', 'History', 'Performance', 'Runs', 'Activity'] as const;
type Tab = typeof TABS[number];
const key = () => `pt-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
const QTY = /^\d+(\.\d{1,12})?$/;
const sign = (v?: string | null) => v === null || v === undefined ? '' : String(v).startsWith('-') ? 'neg' : Number(v) ? 'pos' : '';

function Kpi({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: string }) {
  return <div className={`pd-kpi ${tone ?? ''}`}><span>{label}</span><b>{value}</b>{sub && <small>{sub}</small>}</div>;
}

/** Equity curve as a plain SVG line (display only: the values are already exact strings from the server). */
function Curve({ points, ccy }: { points: Row[]; ccy: string }) {
  const vals = points.map(p => Number(p.equity));
  if (vals.length < 2) return <p className="cfg-note">The curve appears after the first closed trade.</p>;
  const min = Math.min(...vals), max = Math.max(...vals), span = max - min || 1, W = 640, H = 160;
  const xy = vals.map((v, i) => [(i / (vals.length - 1)) * W, H - ((v - min) / span) * (H - 16) - 8]);
  const line = xy.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
  const up = vals[vals.length - 1] >= vals[0];
  return <figure className="pd-curve" aria-label="Equity curve">
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" role="img" aria-label={`Equity from ${points[0].equity} to ${points[points.length - 1].equity} ${ccy}`}>
      <path d={`${line} L${W},${H} L0,${H} Z`} className={`area ${up ? 'up' : 'down'}`} /><path d={line} className={`ln ${up ? 'up' : 'down'}`} />
    </svg>
    <figcaption><span>High {fmtMoney(points[vals.indexOf(max)].equity, ccy)}</span><span>Low {fmtMoney(points[vals.indexOf(min)].equity, ccy)}</span><span>{vals.length - 2} closing fill(s)</span></figcaption>
  </figure>;
}

function Ticket({ account, instruments, done }: { account: Row; instruments: Row[]; done: (m: string, err?: boolean) => void }) {
  const [t, setT] = useState(() => ({ instrument_id: '', side: 'BUY', quantity: setting<string>('trading.quantity') || '0.01', order_type: 'MARKET', limit_price: '', stop_price: '',
    stop_loss: '', take_profit: '', reason: '' }));
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  const [ticketKey, setTicketKey] = useState(key);
  const shown = useMemo(() => instruments.filter(i => !q || `${i.symbol} ${i.name ?? ''} ${i.market ?? ''}`.toLowerCase().includes(q.toLowerCase())).slice(0, 300), [instruments, q]);
  const price = (v: string) => !v || QTY.test(v);
  const ok = t.instrument_id && QTY.test(t.quantity) && Number(t.quantity) > 0 && price(t.limit_price) && price(t.stop_price) && price(t.stop_loss) && price(t.take_profit)
    && (t.order_type !== 'LIMIT' || t.limit_price) && (t.order_type !== 'STOP' || t.stop_price);
  const ins = instruments.find(i => i.id === t.instrument_id);
  const send = async () => {
    if (!ok || busy) return;
    if (setting('trading.confirm') === 'ALWAYS' && !window.confirm(`Paper order on ${account.code}: ${t.side} ${t.quantity} ${ins?.symbol} ${t.order_type}${t.limit_price ? ` @ ${t.limit_price}` : ''}${t.stop_price ? ` stop ${t.stop_price}` : ''}. Simulated — nothing goes to a broker.`)) return;
    setBusy(true);
    try {
      const r = await api('trading/orders', 'POST', { account_id: account.id, instrument_id: t.instrument_id, side: t.side, quantity: t.quantity, order_type: t.order_type,
        limit_price: t.limit_price || null, stop_price: t.stop_price || null, stop_loss: t.stop_loss || null, take_profit: t.take_profit || null, mode: 'PAPER',
        ticket_key: ticketKey, reason: t.reason || 'Paper ticket' });
      const o = r?.order ?? r?.orders?.[0] ?? r;
      done(`${t.side} ${t.quantity} ${ins?.symbol}: ${String(o?.state ?? 'sent').toLowerCase()}${o?.state === 'REJECTED' && o?.reasons ? ` — ${[].concat(o.reasons).map((x: Row | string) => typeof x === 'string' ? x : x.message ?? x.code).join('; ')}` : ''}.`, o?.state === 'REJECTED');
      setTicketKey(key());
    } catch (e) { done((e as Error).message, true); setTicketKey(key()); } finally { setBusy(false); }
  };
  return <form className="pd-ticket" aria-label="Order ticket" onSubmit={e => { e.preventDefault(); void send(); }}>
    <h3>New order <small>paper · {account.code}</small></h3>
    <label>Instrument<input type="search" aria-label="Find instrument" placeholder="Search symbol…" value={q} onChange={e => setQ(e.target.value)} />
      <select aria-label="Instrument" size={5} value={t.instrument_id} onChange={e => setT({ ...t, instrument_id: e.target.value })}>
        {shown.map(i => <option key={i.id} value={i.id}>{i.symbol} · {String(i.asset_class ?? '').toLowerCase()}{i.market ? ` · ${i.market}` : ''}</option>)}</select></label>
    <div className="pd-side" role="radiogroup" aria-label="Side">{(['BUY', 'SELL'] as const).map(s => <button type="button" key={s} role="radio" aria-checked={t.side === s}
      className={`side-${s.toLowerCase()} ${t.side === s ? 'on' : ''}`} onClick={() => setT({ ...t, side: s })}>{s === 'BUY' ? 'Buy' : 'Sell'}</button>)}</div>
    <div className="pd-row2">
      <label>Quantity <small>{ins?.contract_size ? `lot = ${fmtNum(ins.contract_size)}` : ''}</small><input aria-label="Quantity" inputMode="decimal" aria-invalid={!QTY.test(t.quantity)} value={t.quantity} onChange={e => setT({ ...t, quantity: e.target.value })} /></label>
      <label>Type<select aria-label="Order type" value={t.order_type} onChange={e => setT({ ...t, order_type: e.target.value })}><option>MARKET</option><option>LIMIT</option><option>STOP</option></select></label>
    </div>
    {t.order_type === 'LIMIT' && <label>Limit price<input aria-label="Limit price" inputMode="decimal" aria-invalid={!price(t.limit_price)} value={t.limit_price} onChange={e => setT({ ...t, limit_price: e.target.value })} /></label>}
    {t.order_type === 'STOP' && <label>Stop price<input aria-label="Stop price" inputMode="decimal" aria-invalid={!price(t.stop_price)} value={t.stop_price} onChange={e => setT({ ...t, stop_price: e.target.value })} /></label>}
    <div className="pd-row2">
      <label>Stop loss<input aria-label="Stop loss" inputMode="decimal" placeholder="optional" aria-invalid={!price(t.stop_loss)} value={t.stop_loss} onChange={e => setT({ ...t, stop_loss: e.target.value })} /></label>
      <label>Take profit<input aria-label="Take profit" inputMode="decimal" placeholder="optional" aria-invalid={!price(t.take_profit)} value={t.take_profit} onChange={e => setT({ ...t, take_profit: e.target.value })} /></label>
    </div>
    <label>Note<input aria-label="Order note" maxLength={200} placeholder="why (audit log)" value={t.reason} onChange={e => setT({ ...t, reason: e.target.value })} /></label>
    <button className={`primary side-${t.side.toLowerCase()}`} disabled={!ok || busy || account.status === 'ARCHIVED'}>{busy ? 'Sending…' : `${t.side === 'BUY' ? 'Buy' : 'Sell'} ${t.quantity || ''} ${ins?.symbol ?? ''}`}</button>
    <p className="pd-fine">Fills at the live price of the instrument's feed plus this account's slippage and costs. Risk checks and setup gates apply as for any order.</p>
  </form>;
}

export function PaperAccountDetail({ id, canTrade, back, edit }: { id: string; canTrade: boolean; back: () => void; edit: (a: Row) => void }) {
  useSettings();
  const [a, error, reload] = useData<Row>(`trading/paper-accounts/${id}`, 5_000, [id]);
  const [ins] = useData<Row>('config/instruments', 120_000);
  const [tab, setTab] = useState<Tab>('Positions');
  const [msg, setMsg] = useState({ error: '', notice: '' });
  const [ticket, setTicket] = useState(true);
  const say = (m: string, err?: boolean) => { setMsg(err ? { error: m, notice: '' } : { error: '', notice: m }); reload(); };
  const reason = (what: string) => { const r = window.prompt(`${what}. Reason (audit log):`, what)?.trim(); return r && r.length >= 5 ? r : null; };
  if (!a) return <div className="pd"><button className="link" onClick={back}>← Paper accounts</button><Msg error={error} notice="" />{!error && <p className="cfg-note">Loading account…</p>}</div>;
  const ccy = a.currency, p: Row = a.performance ?? {};
  const counts: Record<Tab, number | undefined> = { Positions: a.positions?.length, Orders: a.orders?.length, History: a.fills?.length, Performance: undefined, Runs: a.runs_list?.length, Activity: a.activity?.length };
  return <div className="pd" aria-label={`Paper account ${a.code}`}>
    <header className="pd-head">
      <div><button className="link" onClick={back}>← Paper accounts</button>
        <h2>{a.code} <span className="pd-badge">PAPER</span>{a.status === 'ARCHIVED' && <span className="pd-badge muted">ARCHIVED</span>}</h2>
        <p>{a.name} · {ccy} · {String(a.scope).toLowerCase()} markets · since {fmtTime(a.created_at)}</p></div>
      <div className="pd-head-actions">
        {canTrade && a.status !== 'ARCHIVED' && <button aria-pressed={ticket} onClick={() => setTicket(x => !x)}>{ticket ? 'Hide ticket' : 'New order'}</button>}
        <button onClick={() => edit(a)}>Account settings</button>
        <button onClick={() => goto('My Workspaces', 'desks')}>Run a strategy ↗</button>
        <button onClick={reload} aria-label="Refresh">↻</button>
      </div>
    </header>
    <div className="pd-kpis" aria-label="Account summary">
      <Kpi label="Balance" value={fmtMoney(a.balance, ccy)} sub={`start ${fmtMoney(a.starting_amount)}`} />
      <Kpi label="Equity" value={fmtMoney(a.equity, ccy)} sub={a.complete ? 'valued at live prices' : `no rate: ${(a.missing_fx ?? []).join(', ') || 'a holding'}`} tone={a.complete ? '' : 'warn'} />
      <Kpi label="Open P&L" value={fmtMoney(a.unrealised, ccy)} tone={sign(a.unrealised)} sub={`${a.open_positions ?? 0} position(s)`} />
      <Kpi label="Realised" value={fmtMoney(a.realised, ccy)} tone={sign(a.realised)} sub={`costs ${fmtMoney(p.costs)}`} />
      <Kpi label="Return" value={`${fmtNum(a.return_pct)}%`} tone={sign(a.return_pct)} sub={`drawdown ${fmtNum(a.drawdown_pct)}%`} />
      <Kpi label="Exposure" value={fmtMoney(a.exposure, ccy)} sub={a.leverage ? `leverage ${fmtNum(a.leverage)}×` : 'no open exposure'} />
      <Kpi label="Win rate" value={p.win_rate_pct ? `${fmtNum(p.win_rate_pct)}%` : '—'} sub={`${p.closed_trades ?? 0} closed · PF ${p.profit_factor ?? '—'}`} />
    </div>
    <Msg error={msg.error || error} notice={msg.notice} />
    <div className={`pd-main ${ticket && canTrade && a.status !== 'ARCHIVED' ? 'with-ticket' : ''}`}>
      <section className="pd-tabs">
        <div role="tablist" aria-label="Account tabs">{TABS.map(t => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}{counts[t] ? <small>{counts[t]}</small> : null}</button>)}</div>
        <div role="tabpanel" aria-label={tab}>
          {tab === 'Positions' && (a.positions?.length ? <table className="cfg-table pd-table"><thead><tr><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Avg price</th><th className="num">Mark</th>
            <th className="num">Open P&L ({ccy})</th><th className="num">Value ({ccy})</th><th>Strategy</th><th /></tr></thead>
            <tbody>{a.positions.map((x: Row) => { const long = !String(x.quantity).startsWith('-'); return <tr key={x.instrument_id + (x.deployment_id ?? '')}>
              <td><b>{x.symbol}</b><br /><small>{String(x.asset_class ?? '').toLowerCase()} · {x.quote_currency}</small></td>
              <td><span className={`pd-sidetag ${long ? 'buy' : 'sell'}`}>{long ? 'LONG' : 'SHORT'}</span></td>
              <td className="num">{fmtNum(String(x.quantity).replace('-', ''))}</td><td className="num">{fmtNum(x.avg_price)}</td>
              <td className="num">{x.mark ? fmtNum(x.mark) : <small className="dk-warn">no price</small>}</td>
              <td className={`num ${sign(x.unrealised_account)}`}>{x.unrealised_account != null ? fmtNum(x.unrealised_account, 2) : <small className="dk-warn">{String(x.valuation).toLowerCase().replace('_', ' ')}</small>}</td>
              <td className="num">{x.notional_account != null ? fmtNum(x.notional_account, 2) : '—'}</td>
              <td>{x.strategy_code ?? <small>manual</small>}</td>
              <td>{canTrade && <button onClick={() => { const r = reason(`Close ${x.symbol} on ${a.code}`); if (r) void api('trading/portfolio/flatten', 'POST', { account_id: a.id, instrument_id: x.instrument_id, mode: 'PAPER', reason: r, key: key() })
                .then(() => say(`${x.symbol}: close order sent.`)).catch(e => say((e as Error).message, true)); }}>Close</button>}</td></tr>; })}</tbody></table>
            : <p className="pd-empty">No open position. {canTrade && 'Place an order with the ticket, or run a strategy on this account.'}</p>)}
          {tab === 'Orders' && (a.orders?.length ? <table className="cfg-table pd-table"><thead><tr><th>Placed</th><th>Symbol</th><th>Side</th><th>Type</th><th className="num">Qty</th><th className="num">Filled</th><th className="num">Price</th><th>State</th><th /></tr></thead>
            <tbody>{a.orders.map((o: Row) => <tr key={o.id}><td>{fmtTime(o.created_at)}</td><td><b>{o.symbol}</b></td><td className={o.side === 'BUY' ? 'pos' : 'neg'}>{o.side}</td><td>{o.order_type}</td>
              <td className="num">{fmtNum(o.quantity)}</td><td className="num">{fmtNum(o.filled_qty ?? '0')}</td><td className="num">{o.limit_price || o.stop_price ? fmtNum(o.limit_price ?? o.stop_price) : 'market'}</td>
              <td><span className={`pd-state ${String(o.state).toLowerCase()}`}>{o.state}</span></td>
              <td>{canTrade && <button onClick={() => { const r = reason(`Cancel ${o.side} ${o.symbol}`); if (r) void api(`trading/orders/${o.id}/cancel`, 'POST', { reason: r }).then(() => say('Cancel requested.')).catch(e => say((e as Error).message, true)); }}>Cancel</button>}</td></tr>)}</tbody></table>
            : <p className="pd-empty">No working order.</p>)}
          {tab === 'History' && (a.fills?.length ? <table className="cfg-table pd-table"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th className="num">Qty</th><th className="num">Price</th><th className="num">Slippage</th><th className="num">Cost</th><th className="num">Realised</th><th>Strategy</th></tr></thead>
            <tbody>{a.fills.map((f: Row) => <tr key={f.id}><td>{fmtTime(f.at)}</td><td><b>{f.symbol}</b></td><td className={f.side === 'BUY' ? 'pos' : 'neg'}>{f.side}</td><td className="num">{fmtNum(f.quantity)}</td>
              <td className="num">{fmtNum(f.price)}</td><td className="num">{f.slippage != null ? fmtNum(f.slippage) : '—'}</td><td className="num">{fmtNum(f.commission ?? '0')} <small>{f.currency}</small></td>
              <td className={`num ${sign(f.realised_pnl)}`}>{Number(f.realised_pnl) ? <>{fmtNum(f.realised_pnl)} <small>{f.currency}</small></> : '—'}</td><td>{f.strategy_code ?? <small>manual</small>}</td></tr>)}</tbody></table>
            : <p className="pd-empty">No trade yet.</p>)}
          {tab === 'Performance' && <div className="pd-perf">
            <Curve points={a.equity_curve ?? []} ccy={ccy} />
            <dl className="pd-stats">{([['Closed trades', p.closed_trades], ['Wins / losses', `${p.wins ?? 0} / ${p.losses ?? 0}`], ['Win rate', p.win_rate_pct ? `${fmtNum(p.win_rate_pct)}%` : '—'],
              ['Profit factor', p.profit_factor ?? '—'], ['Gross profit', fmtMoney(p.gross_profit, ccy)], ['Gross loss', fmtMoney(p.gross_loss, ccy)], ['Average trade', fmtMoney(p.average, ccy)],
              ['Best trade', fmtMoney(p.best, ccy)], ['Worst trade', fmtMoney(p.worst, ccy)], ['Costs paid', fmtMoney(p.costs, ccy)], ['Max drawdown', `${fmtNum(a.drawdown_pct)}%`], ['Return', `${fmtNum(a.return_pct)}%`]] as [string, string][])
              .map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v ?? '—'}</dd></div>)}</dl>
            <p className="pd-fine">Trade results are gross per closing fill, converted to {ccy}; costs are shown apart. Balance and equity are net of costs.</p>
          </div>}
          {tab === 'Runs' && (a.runs_list?.length ? <table className="cfg-table pd-table"><thead><tr><th>Strategy</th><th>Version</th><th>Instrument</th><th>Timeframe</th><th>Status</th><th>Workspace</th></tr></thead>
            <tbody>{a.runs_list.map((r: Row) => <tr key={r.id}><td><b>{r.strategy_code}</b></td><td>v{r.version_number}</td><td>{r.symbol}</td><td>{r.timeframe ?? '—'}</td>
              <td><span className={`pd-state ${String(r.status).toLowerCase()}`}>{r.status}</span></td>
              <td>{r.workspace ? <button className="link" onClick={() => goto('My Workspaces', 'desks')}>{r.workspace} ↗</button> : <small>deployment</small>}</td></tr>)}</tbody></table>
            : <p className="pd-empty">No strategy runs on this account. Open <button className="link" onClick={() => goto('My Workspaces', 'desks')}>a workspace ↗</button>, choose Paper and this account.</p>)}
          {tab === 'Activity' && (a.activity?.length ? <table className="cfg-table pd-table"><thead><tr><th>Time</th><th>Who</th><th>Action</th><th>What</th><th>Reason</th></tr></thead>
            <tbody>{a.activity.map((r: Row, i: number) => <tr key={i}><td>{fmtTime(r.occurred_at)}</td><td>{r.actor}</td><td>{r.action}</td><td>{r.entity}</td><td>{r.reason || '—'}</td></tr>)}</tbody></table>
            : <p className="pd-empty">No activity yet.</p>)}
        </div>
      </section>
      {ticket && canTrade && a.status !== 'ARCHIVED' && <Ticket account={a} instruments={ins?.items ?? []} done={say} />}
    </div>
  </div>;
}
