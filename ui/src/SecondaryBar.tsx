/* Secondary side bar (right), as in VS Code: views a trader keeps beside any screen.
 *
 * Why it exists: the right side bar showed only the attention list, so it was rarely opened. VS Code uses its
 * secondary side bar for views that stay useful whatever the editor shows (Outline, Timeline, Chat); for a trader those
 * are what needs attention, the accounts' equity and day P&L, open positions, working orders and a watchlist.
 * Purpose: stacked, collapsible views (each remembered open or closed): Attention, Accounts, Open positions,
 * Working orders, Watchlist. Each row opens the page that owns it.
 * Must never: place, change or cancel an order; values are exact decimal strings formatted for display only.
 * Dependencies: Sidebar (Pane, Problems, MarketWatch), StageShell.useData, userSettings formatters, nav.goto.
 * Consumers: App (aside.inspector).
 * Architectural position: UI shell.
 */
import { goto, type Overview } from './nav';
import { MarketWatch, Pane, Problems } from './Sidebar';
import { useData, type Row } from './StageShell';
import { fmtNum, useSettings } from './userSettings';

const tone = (v?: string | null) => v == null ? '' : String(v).startsWith('-') ? 'down' : Number(v) ? 'up' : '';

export function SecondaryBar({ overview, signedOut }: { overview: Overview | null; signedOut: boolean }) {
  useSettings();
  const on = !!overview && !signedOut;
  const [acc] = useData<Row>(on ? 'trading/portfolio/accounts' : null, 15_000);
  const [pos] = useData<Row>(on ? 'trading/portfolio/positions' : null, 10_000);
  const [ord] = useData<Row>(on ? 'trading/orders?open_only=true&limit=50' : null, 10_000);
  if (!on) return <p className="sb-empty">{signedOut ? 'Sign in to see live status.' : 'Loading…'}</p>;
  const accounts: Row[] = acc?.items ?? [], positions: Row[] = pos?.items ?? [], orders: Row[] = ord?.items ?? [];
  return <>
    <Pane title="Attention" count={overview.attention.length || undefined}><Problems overview={overview} /></Pane>
    <Pane title="Accounts" count={accounts.length || undefined}>
      {accounts.length ? <table className="sb-table"><thead><tr><th>Account</th><th className="num">Equity</th><th className="num">Day</th></tr></thead>
        <tbody>{accounts.map(a => <tr key={a.account_id} title={`${a.account_code} · ${a.currency}`} onClick={() => goto('Portfolio')}>
          <td>{a.account_code}</td><td className="num">{fmtNum(a.equity, 2)}</td><td className={`num ${tone(a.day_pnl)}`}>{fmtNum(a.day_pnl, 2)}</td></tr>)}</tbody></table>
        : <div className="sb-empty">No account with capital or positions.</div>}
    </Pane>
    <Pane title="Open positions" count={positions.length || undefined}>
      {positions.length ? <table className="sb-table"><thead><tr><th>Symbol</th><th className="num">Qty</th><th className="num">P&L</th></tr></thead>
        <tbody>{positions.slice(0, 40).map(p => <tr key={`${p.account_id}${p.instrument_id}${p.deployment_id ?? ''}`} title={`${p.account_code} · ${p.strategy_code ?? 'manual'} · ${p.account_currency}`} onClick={() => goto('Portfolio')}>
          <td>{p.symbol}<span className="sb-meta"> {p.account_code}</span></td><td className={`num ${tone(p.quantity)}`}>{fmtNum(p.quantity)}</td>
          <td className={`num ${tone(p.unrealised_account)}`}>{p.unrealised_account != null ? fmtNum(p.unrealised_account, 2) : '—'}</td></tr>)}</tbody></table>
        : <div className="sb-empty">No open position.</div>}
    </Pane>
    <Pane title="Working orders" count={orders.length || undefined} initiallyOpen={false}>
      {orders.length ? <table className="sb-table"><tbody>{orders.slice(0, 40).map(o => <tr key={o.id} title={`${o.state} · ${o.order_type}`} onClick={() => goto('Execution')}>
          <td className={o.side === 'BUY' ? 'up' : 'down'}>{o.side}</td><td>{o.symbol}</td><td className="num">{fmtNum(o.quantity)}</td><td className={o.state === 'UNKNOWN' ? 'down' : ''}>{o.state}</td></tr>)}</tbody></table>
        : <div className="sb-empty">No working order.</div>}
    </Pane>
    <Pane title="Watchlist"><MarketWatch /></Pane>
  </>;
}
