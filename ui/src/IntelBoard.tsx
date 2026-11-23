/* Stage 2 · Market intelligence screens.
 * Why: one place to see which collected instruments deserve attention and why, from the shared feature store.
 * Screener (ranked universe per timeframe: regime, volatility, trend, momentum, cost, feed quality, attention score
 * with its parts) · Instrument (every timeframe side by side, chart with EMA/Bollinger and regime strip, RSI, CSV) ·
 * Sessions (what is open now, next changes, hours). Reads /api/intel; changes nothing except "Recompute now".
 * Must never: show a figure without saying where it comes from (timeframe, bar time, bars source). */
import { useEffect, useMemo, useState } from 'react';
import { api } from './Configuration';

type Row = Record<string, any>;
const TFS = ['M1', 'M5', 'M15', 'H1', 'H4', 'D1'];
const REGIMES: [string, string][] = [['', 'All regimes'], ['TREND_UP', 'Trend up'], ['TREND_DOWN', 'Trend down'], ['RANGE', 'Range'], ['TRANSITION', 'Transition'], ['WARMING_UP', 'Warming up']];
const REGIME_NAME: Row = Object.fromEntries(REGIMES.filter(r => r[0]));
const num = (v: any, d = 2) => v == null || Number.isNaN(Number(v)) ? '–' : Number(v).toFixed(d);
const price = (v: any) => v == null ? '–' : Number(v) >= 100 ? Number(v).toFixed(2) : Number(v).toFixed(5);
const when = (t?: string) => t ? new Date(t).toLocaleString() : '–';
const pill = (r: string) => <span className={`cfg-pill ${r === 'TREND_UP' ? 'ok' : r === 'TREND_DOWN' ? 'live' : r === 'RANGE' ? 'demo' : r === 'WARMING_UP' ? 'off' : 'warn'}`}>{REGIME_NAME[r] ?? r}</span>;
// Is this bar the latest the market can have closed? (backend intel/engine.freshness) Plus the Stage 1 feed health.
const FRESH: Row = { FRESH: ['fresh', 'ok', 'The latest bar the market has closed'], CLOSED: ['market closed', 'demo', 'Older, but the market has been shut since it ended'],
  LAGGING: ['late', 'warn', 'Up to three bars behind: the feed or the engine is late'], STALE: ['stale', 'live', 'More than three bars behind: do not rely on these features'] };
const FEED: Row = { INVALID_SYMBOL: 'wrong symbol', ERROR: 'feed error', STALE: 'feed delayed', OFFLINE: 'source offline', DEGRADED: 'feed unreliable' };
const freshPill = (r: Row) => { const f = FRESH[r.freshness]; const feed = FEED[r.feed_health];
  return <>{f ? <span className={`cfg-pill ${f[1]}`} title={`${f[2]}. Bar ended ${when(r.bar_end)}.`}>{f[0]}</span> : <small className="cfg-note">–</small>}
    {feed && <> <span className="cfg-pill live" title={r.feed_detail ?? ''}>{feed}</span></>}</>; };
const SOURCE: Row = { BROKER: 'broker', M1: 'from M1', MIXED: 'broker, then M1' };
const trend = (f: Row) => f.ema20 == null || f.ema50 == null ? '–'
  : `${f.ema20 > f.ema50 ? '▲' : '▼'} 20/50${f.ema200 != null ? ` · ${f.close > f.ema200 ? 'above' : 'below'} 200` : ''}`;

/* Screener columns: every one can be shown or hidden (saved per browser); presets for common views. Feature keys that
 * are not listed here (custom indicators such as ema_9) appear automatically under "Custom". */
type Col = { id: string; label: string; group: string; title?: string; sort?: (r: Row) => any; cell: (r: Row) => any };
const F = (k: string, d = 2, label = k, group = 'Custom', title?: string): Col => ({ id: 'f:' + k, label, group, title, sort: r => r.features[k], cell: r => num(r.features[k], d) });
const COLS: Col[] = [
  { id: 'source', label: 'Source', group: 'Identity', cell: r => <small>{r.provider_name} · {r.account_code}</small> },
  { id: 'server', label: 'Server', group: 'Identity', cell: r => <small>{r.server_name ?? '–'}</small> },
  { id: 'class', label: 'Class', group: 'Identity', sort: r => r.asset_class, cell: r => <small>{r.asset_class ?? '–'}</small> },
  { id: 'last', label: 'Last', group: 'Price & cost', sort: r => r.features.close, cell: r => price(r.bid != null && r.ask != null ? (r.bid + r.ask) / 2 : r.features.close) },
  { id: 'spread', label: 'Spread', group: 'Price & cost', title: 'Spread now, and as % of ATR', sort: r => r.spread_atr_pct,
    cell: r => <>{price(r.spread)}{r.spread_atr_pct != null ? <small className="cfg-note"> ({r.spread_atr_pct}%)</small> : null}</> },
  F('spread_bar', 5, 'Bar spread', 'Price & cost', 'Average spread over the bar, from the broker'),
  { id: 'quality', label: 'Quality', group: 'Price & cost', title: 'Feed quality score, last 60 min', sort: r => r.quality, cell: r => num(r.quality, 0) },
  { id: 'regime', label: 'Regime', group: 'Regime', sort: r => r.confidence, cell: r => <>{pill(r.regime)} <small className="cfg-note">{r.regime !== 'WARMING_UP' ? `${Math.round(100 * r.confidence)}%` : ''}</small></> },
  { id: 'vol', label: 'Vol.', group: 'Regime', title: 'Volatility class: ATR% rank among the last 100 bars', sort: r => r.features.atr_pct_rank100, cell: r => r.volatility ? r.volatility.toLowerCase() : '–' },
  { id: 'align', label: 'Align', group: 'Regime', title: 'Timeframes agreeing: trend up minus trend down over those past warming up', sort: r => r.alignment?.score,
    cell: r => <small title={r.alignment ? `${r.alignment.up} up, ${r.alignment.down} down of ${r.alignment.of}` : ''}>{r.alignment?.label ?? '–'}</small> },
  { id: 'trend', label: 'Trend (EMA)', group: 'Trend', cell: r => trend(r.features) },
  F('adx14', 1, 'ADX', 'Trend'), F('plus_di', 1, '+DI', 'Trend'), F('minus_di', 1, '−DI', 'Trend'), F('er20', 2, 'Efficiency', 'Trend', 'Kaufman efficiency ratio, 20 bars: 0 noise, 1 straight line'),
  F('ema20', 5, 'EMA 20', 'Trend'), F('ema50', 5, 'EMA 50', 'Trend'), F('ema200', 5, 'EMA 200', 'Trend'),
  F('rsi14', 1, 'RSI', 'Momentum'), F('macd_hist', 6, 'MACD hist', 'Momentum'), F('stoch_k', 1, 'Stoch %K', 'Momentum'), F('zscore20', 2, 'Z-score', 'Momentum'),
  F('ret_1_pct', 3, '1-bar %', 'Momentum'),
  { id: 'f:ret_20_pct', label: '20-bar %', group: 'Momentum', sort: r => r.features.ret_20_pct, cell: r => <span className={r.features.ret_20_pct > 0 ? 'up' : r.features.ret_20_pct < 0 ? 'down' : ''}>{num(r.features.ret_20_pct, 2)}</span> },
  F('atr_pct', 3, 'ATR %', 'Volatility'), F('atr_pct_rank100', 0, 'ATR rank', 'Volatility'), F('bb_width_pct', 3, 'BB width %', 'Volatility'), F('rv20_pct', 1, 'Real. vol %', 'Volatility', 'Annualised realised volatility, 20 bars'),
  F('range_pos20', 0, 'Range pos.', 'Volatility', 'Close within the 20-bar range (0 low, 100 high)'),
  F('tick_volume', 0, 'Tick vol.', 'Volume', 'Price changes in the bar (every broker has it)'), F('volume', 0, 'Volume', 'Volume', 'Real traded volume (exchange instruments; blank for OTC FX)'),
  F('rel_volume20', 2, 'Rel. vol.', 'Volume', 'Volume against its 20-bar average (2 = twice normal)'), F('vwap_dev_pct', 3, 'vs VWAP %', 'Volume', 'Close against the 20-bar VWAP'),
  F('obv', 0, 'OBV', 'Volume', 'On-balance volume over the window'),
  F('vwap_session_dev_pct', 3, 'vs session VWAP %', 'Volume', 'Close against the VWAP since the session open (exchange markets: NSE, BSE, MCX)'),
  { id: 'attention', label: 'Attention', group: 'Ranking', title: 'Rank 0–100: quality × spread vs ATR × (movement + direction)', sort: r => r.attention,
    cell: r => <strong title={Object.entries(r.attention_parts ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')}>{num(r.attention, 0)}</strong> },
  { id: 'event', label: 'Event', group: 'Ranking', title: 'High-impact event or holiday for its currencies within the window',
    cell: r => r.event_risk?.length ? <span className="cfg-pill live" title={r.event_risk.map((e: Row) => `${e.currency} ${e.title} ${new Date(e.at).toLocaleTimeString()}`).join(' · ')}>event risk</span>
      : r.next_event ? <small className="cfg-note" title={r.next_event.title}>{r.next_event.currency} {new Date(r.next_event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small> : '–' },
  { id: 'fresh', label: 'Fresh', group: 'Data', title: 'Is the bar the latest the market has closed, and is its price feed healthy?', sort: r => r.freshness, cell: freshPill },
  { id: 'bars', label: 'Bars', group: 'Data', title: 'Bars used and where from: the broker\'s own, built from M1, or the broker\'s history continued from M1',
    sort: r => r.bars, cell: r => <small>{r.bars} · {SOURCE[r.bars_source] ?? r.bars_source}</small> },
  { id: 'bar', label: 'Bar (UTC)', group: 'Data', sort: r => r.bar_time, cell: r => <small>{new Date(r.bar_time).toISOString().slice(5, 16).replace('T', ' ')}</small> },
];
const PRESETS: Record<string, string[]> = {
  Standard: ['source', 'last', 'spread', 'quality', 'regime', 'vol', 'f:adx14', 'f:rsi14', 'f:atr_pct', 'trend', 'f:ret_20_pct', 'f:rel_volume20', 'attention', 'align', 'event', 'fresh', 'bars', 'bar'],
  Trend: ['source', 'last', 'regime', 'align', 'trend', 'f:adx14', 'f:plus_di', 'f:minus_di', 'f:er20', 'f:ret_20_pct', 'attention'],
  Momentum: ['source', 'last', 'regime', 'f:rsi14', 'f:macd_hist', 'f:stoch_k', 'f:zscore20', 'f:ret_1_pct', 'f:ret_20_pct', 'attention'],
  Volatility: ['source', 'last', 'spread', 'vol', 'f:atr_pct', 'f:atr_pct_rank100', 'f:bb_width_pct', 'f:rv20_pct', 'f:range_pos20', 'attention'],
  Volume: ['source', 'last', 'f:tick_volume', 'f:volume', 'f:rel_volume20', 'f:vwap_dev_pct', 'f:vwap_session_dev_pct', 'f:obv', 'f:spread_bar', 'regime', 'attention'],
  Cost: ['source', 'server', 'last', 'spread', 'f:spread_bar', 'quality', 'f:atr_pct', 'attention'],
};
const loadCols = () => { try { const v = JSON.parse(localStorage.getItem('corei.intel.cols') ?? 'null'); return Array.isArray(v) ? v as string[] : PRESETS.Standard; } catch { return PRESETS.Standard; } };

export function Screener({ openInstrument, canRefresh }: { openInstrument: (r: Row) => void; canRefresh: boolean }) {
  const [tf, setTf] = useState(() => { try { return localStorage.getItem('corei.intel.tf') || 'H1'; } catch { return 'H1'; } });
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [regimeF, setRegimeF] = useState(''), [volF, setVolF] = useState(''), [q, setQ] = useState('');
  const [srcF, setSrcF] = useState(''), [classF, setClassF] = useState(''), [wlF, setWlF] = useState(''), [lists, setLists] = useState<Row[]>([]);
  const [shown, setShown] = useState<string[]>(loadCols), [chooser, setChooser] = useState(false), [auto, setAuto] = useState(true);
  useEffect(() => { try { localStorage.setItem('corei.intel.cols', JSON.stringify(shown)); } catch { /* no storage */ } }, [shown]);
  useEffect(() => { void api('intel/watchlists').then(d => setLists(d.items)).catch(() => undefined); }, []);
  const wl = lists.find(w => w.id === wlF);
  const [sort, setSort] = useState<[string, number]>(['rank', 1]);
  const load = async () => { try { setData(await api(`intel/screener?timeframe=${tf}`)); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { try { localStorage.setItem('corei.intel.tf', tf); } catch { /* no storage */ } void load(); if (!auto) return; const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, [tf, auto]);
  const recompute = async () => { setBusy(true); try { await api('intel/refresh', 'POST'); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  // Columns for feature keys not listed above: custom indicators (ema_9, bollinger_20_2p5_upper…).
  const known = new Set(COLS.map(c => c.id));
  const extra = Array.from(new Set((data?.items ?? []).flatMap((r: Row) => Object.keys(r.features ?? {}))))
    .filter(k => !known.has('f:' + k) && !['close', 'bb_mid', 'bb_upper', 'bb_lower', 'macd', 'macd_signal', 'stoch_d', 'donchian_high20', 'donchian_low20', 'atr14', 'vwap20'].includes(k as string))
    .sort().map(k => F(k as string, 5, k as string));
  const allCols = [...COLS, ...extra], cols = shown.map(id => allCols.find(c => c.id === id)).filter(Boolean) as Col[];
  const rows = useMemo(() => {
    const key = (r: Row): any => sort[0] === 'rank' ? r.rank : sort[0] === 'symbol' ? r.broker_symbol : allCols.find(c => c.id === sort[0])?.sort?.(r);
    return (data?.items ?? []).filter((r: Row) => (!regimeF || r.regime === regimeF) && (!volF || r.volatility === volF)
      && (!srcF || r.account_id === srcF) && (!classF || (r.asset_class ?? 'OTHER') === classF)
      && (!wl || wl.items.some((x: Row) => x.account_id === r.account_id && x.broker_symbol === r.broker_symbol))
      && (!q || `${r.broker_symbol} ${r.instrument ?? ''} ${r.account_code} ${r.provider_name}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: Row, b: Row) => { const x = key(a), y = key(b); return x == null ? 1 : y == null ? -1 : (x < y ? -1 : x > y ? 1 : 0) * sort[1]; });
  }, [data, regimeF, volF, q, sort, srcF, classF, wlF, lists, shown]);
  const sources = Array.from(new Map((data?.items ?? []).map((r: Row) => [r.account_id, `${r.provider_name} · ${r.account_code}`])).entries());
  const classes = Array.from(new Set((data?.items ?? []).map((r: Row) => r.asset_class ?? 'OTHER'))) as string[];
  const th = (id: string, label: string, title?: string, sortable = true) => sortable
    ? <th key={id} title={title} className="sortable" onClick={() => setSort([id, sort[0] === id ? -sort[1] : (id === 'rank' || id === 'symbol' ? 1 : -1)])}>{label}{sort[0] === id ? (sort[1] > 0 ? ' ▲' : ' ▼') : ''}</th>
    : <th key={id} title={title}>{label}</th>;
  const st = data?.engine;
  const counts = (data?.items ?? []).reduce((m: Row, r: Row) => ({ ...m, [r.regime]: (m[r.regime] ?? 0) + 1 }), {});
  const filtered = !!(regimeF || volF || srcF || classF || wlF || q);
  const csv = () => {
    const head = ['rank', 'instrument', ...cols.map(c => c.id.replace(/^f:/, ''))];
    const val = (r: Row, c: Col) => c.id.startsWith('f:') ? r.features[c.id.slice(2)] ?? '' : c.id === 'source' ? `${r.provider_name} ${r.account_code}` : c.id === 'regime' ? r.regime
      : c.id === 'vol' ? r.volatility ?? '' : c.id === 'attention' ? r.attention ?? '' : c.id === 'spread' ? r.spread ?? '' : c.id === 'quality' ? r.quality ?? '' : c.id === 'bar' ? r.bar_time : c.id === 'bars' ? r.bars : '';
    const lines = [head.join(','), ...rows.map((r: Row) => [r.rank, r.broker_symbol, ...cols.map(c => String(val(r, c)).replace(/,/g, ' '))].join(','))];
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' })); a.download = `screener-${tf}.csv`; a.click();
  };
  const groups = Array.from(new Set(allCols.map(c => c.group)));
  return <section className="intel">
    <div className="intel-bar">
      <span className="intel-tfs" role="group" aria-label="Timeframe">{TFS.map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
      <select aria-label="Regime" value={regimeF} onChange={e => setRegimeF(e.target.value)}>{REGIMES.map(([v, l]) => <option key={v} value={v}>{l}{v && counts[v] ? ` (${counts[v]})` : ''}</option>)}</select>
      <select aria-label="Volatility" value={volF} onChange={e => setVolF(e.target.value)}><option value="">All volatility</option><option value="HIGH">High</option><option value="NORMAL">Normal</option><option value="LOW">Low</option></select>
      <select aria-label="Source" value={srcF} onChange={e => setSrcF(e.target.value)}><option value="">All brokers & accounts</option>{sources.map(([id, l]) => <option key={id as string} value={id as string}>{l as string}</option>)}</select>
      <select aria-label="Asset class" value={classF} onChange={e => setClassF(e.target.value)}><option value="">All asset classes</option>{classes.map(c => <option key={c}>{c}</option>)}</select>
      <select aria-label="Watchlist" value={wlF} onChange={e => setWlF(e.target.value)}><option value="">All instruments</option>{lists.map(w => <option key={w.id} value={w.id}>Watchlist: {w.name}</option>)}</select>
      <input aria-label="Search" placeholder="Symbol, source…" value={q} onChange={e => setQ(e.target.value)} />
      {filtered && <button onClick={() => { setRegimeF(''); setVolF(''); setSrcF(''); setClassF(''); setWlF(''); setQ(''); }}>Clear filters</button>}
    </div>
    <div className="intel-bar">
      <span className="intel-tfs" role="group" aria-label="Column preset">{Object.keys(PRESETS).map(p => <button key={p} aria-pressed={JSON.stringify(PRESETS[p]) === JSON.stringify(shown)} onClick={() => setShown(PRESETS[p])}>{p}</button>)}</span>
      <button aria-expanded={chooser} onClick={() => setChooser(!chooser)}>Columns ({cols.length})</button>
      <button onClick={csv} disabled={!rows.length}>Export CSV</button>
      <label className="intel-check"><input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-refresh 30 s</label>
      <button onClick={() => void load()}>Reload</button>
      {canRefresh && <button onClick={() => void recompute()} disabled={busy} title="Recompute every instrument now">{busy ? 'Computing…' : 'Recompute now'}</button>}
      {data?.session && <span className="cfg-pill demo" title="Session now (see Sessions)">{data.session.label}</span>}
      <small className="cfg-note">{rows.length} of {data?.items?.length ?? 0} · {st?.last_run_at ? `engine ${when(st.last_run_at)} · ${st.instruments} instruments · ${st.duration_ms} ms${st.errors?.length ? ` · ${st.errors.length} errors` : ''}` : 'engine runs every minute'}</small>
    </div>
    {chooser && <div className="intel-edit intel-colpick">
      {groups.map(g => <div key={g}><b>{g}</b>{allCols.filter(c => c.group === g).map(c => <label key={c.id} className="intel-check" title={c.title}>
        <input type="checkbox" checked={shown.includes(c.id)} onChange={e => setShown(e.target.checked ? [...shown, c.id] : shown.filter(x => x !== c.id))} /> {c.label}</label>)}</div>)}
      {!extra.length && <p className="cfg-note">Your own indicators (Configure › Indicators) appear here under Custom once computed.</p>}
    </div>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {st?.errors?.length ? <p className="cfg-error">{st.errors[0]}</p> : null}
    {data && !data.items.length && <p className="cfg-note">No features yet. The screener lists every instrument being collected (Market data › plans) once it has bars; the engine runs every minute.
      Higher timeframes need history: Dashboard › Needs history › Get history for all.</p>}
    {rows.length > 0 && <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr>
      {th('rank', '#')}{th('symbol', 'Instrument')}{cols.map(c => th(c.id, c.label, c.title, !!c.sort))}</tr></thead><tbody>
      {rows.map((r: Row) => <tr key={r.account_id + r.broker_symbol} className="clickable" onClick={() => openInstrument(r)} title={(r.reasons ?? []).join(' · ')}>
        <td>{r.rank}</td><td><strong>{r.broker_symbol}</strong>{r.instrument && r.instrument !== r.broker_symbol ? <small className="cfg-note"> {r.instrument}</small> : null}</td>
        {cols.map(c => <td key={c.id}>{c.cell(r)}</td>)}</tr>)}
    </tbody></table></div>}
    <p className="cfg-note">Regime rules (v1): trend = ADX ≥ 25 and efficiency ≥ 0.30 with EMA20/50 and +DI/−DI agreeing; range = ADX &lt; 20; volatility = ATR% rank among the last 100 bars (high ≥ 80, low ≤ 20). Closed bars only.
      Volume is the broker's real volume where it has one (exchange instruments), otherwise tick volume (OTC FX). Hover a row for its reasons; click it for every timeframe.</p>
  </section>;
}

export function InstrumentView({ pick, items, setPick }: { pick: Row | null; items: Row[]; setPick: (r: Row) => void }) {
  const [tf, setTf] = useState<string>(pick?.scanTimeframe ?? 'H1'), [data, setData] = useState<Row | null>(null), [error, setError] = useState('');
  const scanTimeframe = pick?.scanTimeframe;
  useEffect(() => { if (scanTimeframe) setTf(scanTimeframe); }, [pick, scanTimeframe]);
  const choices = pick && !items.some(row => row.account_id === pick.account_id && row.broker_symbol === pick.broker_symbol)
    ? [pick, ...items] : items;
  useEffect(() => {
    if (!pick) return;
    let stop = false;
    const load = () => api(`intel/instrument/${pick.account_id}/${encodeURIComponent(pick.broker_symbol)}?timeframe=${tf}&limit=200`)
      .then(d => { if (!stop) { setData(d); setError(''); } }).catch(e => { if (!stop) setError((e as Error).message); });
    void load(); const t = window.setInterval(load, 30_000); return () => { stop = true; window.clearInterval(t); };
  }, [pick?.account_id, pick?.broker_symbol, tf]);
  const latest = data?.history?.length ? data.history[data.history.length - 1].features : (data?.snapshots ?? []).find((x: Row) => x.timeframe === tf)?.features;
  const csv = () => {
    const keys = Array.from(new Set((data?.history ?? []).flatMap((h: Row) => Object.keys(h.features)))).sort() as string[];
    const lines = ['bar_time,regime,' + keys.join(','), ...(data?.history ?? []).map((h: Row) => [h.bar_time, h.regime, ...keys.map(k => h.features[k] ?? '')].join(','))];
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = `${pick?.broker_symbol}-${tf}-features.csv`; a.click();
  };
  return <section className="intel">
    <div className="intel-bar">
      <select aria-label="Instrument" value={pick ? pick.account_id + '|' + pick.broker_symbol : ''} onChange={e => { const r = choices.find(x => x.account_id + '|' + x.broker_symbol === e.target.value); if (r) setPick(r); }}>
        <option value="">Choose an instrument…</option>
        {choices.map(r => <option key={r.account_id + r.broker_symbol} value={r.account_id + '|' + r.broker_symbol}>{r.broker_symbol} · {[r.provider_name, r.account_code].filter(Boolean).join(' · ') || 'Scan source'}</option>)}
      </select>
      <span className="intel-tfs" role="group" aria-label="Chart timeframe">{TFS.filter(t => t !== 'M1' || scanTimeframe === 'M1').map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
      {data?.history?.length ? <button onClick={csv}>Export CSV</button> : null}
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {!pick && <p className="cfg-note">Pick an instrument here or click a row in the Screener.</p>}
    {data && <>
      <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr><th>TF</th><th>Regime</th><th>Vol.</th><th>ADX</th><th>RSI</th><th>ATR %</th><th>Trend (EMA)</th><th>BB width %</th><th>20-bar %</th><th>Session</th><th>Bars</th><th>Bar (UTC)</th><th>Why</th></tr></thead><tbody>
        {data.snapshots.map((s: Row) => { const f = s.features; return <tr key={s.timeframe} className={s.timeframe === tf ? 'selected' : ''}>
          <td><strong>{s.timeframe}</strong></td><td>{pill(s.regime)} <small className="cfg-note">{s.regime !== 'WARMING_UP' ? `${Math.round(100 * s.confidence)}%` : ''}</small></td>
          <td>{s.volatility?.toLowerCase() ?? '–'}</td><td>{num(f.adx14, 1)}</td><td>{num(f.rsi14, 1)}</td><td>{num(f.atr_pct, 3)}</td><td>{trend(f)}</td><td>{num(f.bb_width_pct, 3)}</td>
          <td>{num(f.ret_20_pct, 2)}</td><td><small>{s.session}</small></td><td><small>{s.bars} · {s.bars_source === 'BROKER' ? 'broker' : 'from M1'}</small></td>
          <td><small>{new Date(s.bar_time).toISOString().slice(5, 16).replace('T', ' ')}</small></td><td><small className="cfg-note">{(s.reasons ?? []).join(' · ')}</small></td></tr>; })}
      </tbody></table></div>
      <FeatureChart history={data.history} tf={tf} />
      {latest && <><h4>Every feature on {tf} (last closed bar)</h4><FeatureGrid f={latest} /></>}
    </>}
  </section>;
}

const FEATURE_GROUPS: [string, string[]][] = [
  ['Price & trend', ['close', 'ema20', 'ema50', 'ema200', 'adx14', 'plus_di', 'minus_di', 'er20', 'donchian_high20', 'donchian_low20']],
  ['Momentum', ['rsi14', 'macd', 'macd_signal', 'macd_hist', 'stoch_k', 'stoch_d', 'zscore20', 'ret_1_pct', 'ret_20_pct']],
  ['Volatility & range', ['atr14', 'atr_pct', 'atr_pct_rank100', 'bb_mid', 'bb_upper', 'bb_lower', 'bb_width_pct', 'rv20_pct', 'range_pos20']],
  ['Volume & cost', ['tick_volume', 'volume', 'rel_volume20', 'vwap20', 'vwap_dev_pct', 'obv', 'spread_bar']],
];
/* Every feature of one bar, grouped; anything not in a group (custom indicators) under "Custom". */
function FeatureGrid({ f }: { f: Row }) {
  const listed = new Set(FEATURE_GROUPS.flatMap(g => g[1]));
  const groups: [string, string[]][] = [...FEATURE_GROUPS, ['Custom', Object.keys(f).filter(k => !listed.has(k)).sort()]];
  const fmt = (v: any) => v == null ? '–' : Math.abs(Number(v)) >= 1000 ? Number(v).toFixed(0) : Math.abs(Number(v)) >= 10 ? Number(v).toFixed(2) : Number(v).toFixed(5);
  return <div className="intel-cols">{groups.filter(g => g[1].length).map(([g, keys]) => <div key={g}><b>{g}</b>
    <table className="cfg-table compact intel-table"><tbody>{keys.map(k => <tr key={k}><td><code>{k}</code></td><td>{fmt(f[k])}</td></tr>)}</tbody></table></div>)}
    {!Object.keys(f).some(k => !listed.has(k)) && <p className="cfg-note">No custom indicators on this timeframe yet (Configure › Indicators).</p>}</div>;
}

function FeatureChart({ history, tf }: { history: Row[]; tf: string }) {
  if (!history?.length) return <p className="cfg-note">No {tf} history yet: it builds as bars close (or backfill the source's history).</p>;
  const W = 900, H = 260, R = 80, V = 50, pad = 6;
  const pts = history.map(h => h.features);
  const vals = pts.flatMap(f => [f.close, f.bb_upper, f.bb_lower, f.ema20, f.ema50].filter((v: any) => v != null)) as number[];
  const lo = Math.min(...vals), hi = Math.max(...vals), x = (i: number) => pad + (W - 2 * pad) * i / Math.max(1, pts.length - 1);
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (v - lo) / (hi - lo || 1));
  const line = (k: string) => pts.map((f, i) => f[k] == null ? '' : `${x(i)},${y(f[k])}`).filter(Boolean).join(' ');
  const ry = (v: number) => H + 10 + (R - 10) * (1 - v / 100);
  const colour: Row = { TREND_UP: 'var(--ready)', TREND_DOWN: 'var(--danger)', RANGE: 'var(--accent)', TRANSITION: 'var(--cfg-warn)', WARMING_UP: 'var(--line)' };
  return <figure className="intel-chart">
    <svg viewBox={`0 0 ${W} ${H + R + V + 20}`} role="img" aria-label={`${tf} close with EMA 20/50, Bollinger bands, regime, RSI and volume`}>
      <polyline points={line('bb_upper')} className="bb" /><polyline points={line('bb_lower')} className="bb" />
      <polyline points={line('ema50')} className="ema50" /><polyline points={line('ema20')} className="ema20" /><polyline points={line('close')} className="close" />
      {history.map((h, i) => <rect key={i} x={x(i) - (W / pts.length) / 2} y={H - 4} width={Math.max(1, W / pts.length)} height={4} fill={colour[h.regime] ?? 'var(--line)'} />)}
      <line x1={pad} x2={W - pad} y1={ry(70)} y2={ry(70)} className="guide" /><line x1={pad} x2={W - pad} y1={ry(30)} y2={ry(30)} className="guide" />
      <polyline points={pts.map((f, i) => f.rsi14 == null ? '' : `${x(i)},${ry(f.rsi14)}`).filter(Boolean).join(' ')} className="rsi" />
      {(() => { const vol = pts.map(f => Number(f.volume ?? f.tick_volume ?? 0)), vmax = Math.max(1, ...vol), base = H + R + V + 16;
        return vol.map((v, i) => <rect key={'v' + i} x={x(i) - (W / pts.length) / 2} y={base - (V - 4) * v / vmax} width={Math.max(1, W / pts.length - 1)} height={(V - 4) * v / vmax}
          fill={i && pts[i].close < pts[i - 1].close ? 'var(--danger)' : 'var(--ready)'} opacity={0.55} />); })()}
    </svg>
    <figcaption className="cfg-note">{tf} · {history.length} closed bars · close (bold), EMA20, EMA50, Bollinger 20/2 (dotted) · bottom strip: regime per bar (green trend up, red trend down, blue range, amber transition) · middle panel: RSI 14 with 30/70 · bottom: volume per bar (real volume where the broker has it, else tick volume; green up bar, red down bar).</figcaption>
  </figure>;
}

export function SessionsView() {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState('');
  useEffect(() => { const load = () => api('intel/sessions').then(setData).catch(e => setError((e as Error).message)); void load(); const t = window.setInterval(load, 60_000); return () => window.clearInterval(t); }, []);
  if (error) return <p role="alert" className="cfg-error">{error}</p>;
  if (!data) return <p>Loading…</p>;
  return <section className="intel">
    <p className="intel-now"><span className={`cfg-pill ${data.now.market_open ? 'ok' : 'off'}`}>{data.now.label}</span>
      {data.now.sessions.length > 1 && <small className="cfg-note"> overlap: usually the most liquid hours</small>}
      {data.now.rollover && <small className="cfg-note"> rollover: spreads widen, swaps are booked</small>}</p>
    <div className="intel-cols">
      <table className="cfg-table compact"><thead><tr><th>Next change</th><th>Becomes</th></tr></thead><tbody>
        {data.next.map((n: Row) => <tr key={n.at}><td>{new Date(n.at).toLocaleString()}</td><td>{n.label}</td></tr>)}</tbody></table>
      <table className="cfg-table compact"><thead><tr><th>Session</th><th>Local hours</th><th>Today in UTC</th><th>Open now</th></tr></thead><tbody>
        {data.table.map((s: Row) => <tr key={s.name}><td>{s.name}</td><td>{s.local} <small className="cfg-note">{s.zone}</small></td><td>{s.utc_today}</td>
          <td>{data.now.sessions.includes(s.name) ? <span className="cfg-pill ok">open</span> : '–'}</td></tr>)}</tbody></table>
    </div>
    <h4>Exchanges</h4>
    <table className="cfg-table compact"><thead><tr><th>Market</th><th>Hours</th><th>Now</th><th>Next</th><th>Note</th></tr></thead><tbody>
      {(data.exchanges ?? []).map((m: Row) => <tr key={m.code}><td><b>{m.code}</b> <small className="cfg-note">{m.name}</small></td><td>{m.hours}</td>
        <td>{m.open ? <span className="cfg-pill ok">open</span> : <span className="cfg-pill off">closed</span>}</td>
        <td>{m.next ? <small>{m.next.event} {new Date(m.next.at).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</small> : '–'}</td>
        <td><small className="cfg-note">{m.note}</small></td></tr>)}</tbody></table>
    <p className="cfg-note">FX week: Sunday 17:00 to Friday 17:00 New York. Daylight-saving changes follow each centre's time zone. Exchange holidays come from the calendar
      (Monitor › Calendar: a HOLIDAY event in INR closes NSE, BSE and MCX that day; an NSE, BSE or MCX event closes that exchange — import them from Upstox there). Collection plans on <em>Market hours</em> follow these, and no stale-price alarm is raised while a market is closed.</p>
  </section>;
}

/* Dashboard: coverage, regime mix, what needs history (one click per instrument, or all), the top of the screen, the
 * biggest moves, events and fired rules. The engine's controls are in Configure › Engine settings. */
export function IntelDashboard({ openInstrument, canEdit, gotoScreener, gotoSettings }: { openInstrument: (r: Row) => void; canEdit: boolean; gotoScreener: () => void; gotoSettings: () => void }) {
  const [tf, setTf] = useState('H1'), [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [busy, setBusy] = useState('');
  const load = async () => { try { setData(await api(`intel/overview?timeframe=${tf}`)); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, [tf]);
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(''); setNotice('');
    try { await fn(); setNotice(n => n || label); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  if (!data) return error ? <p role="alert" className="cfg-error">{error}</p> : <p>Loading…</p>;
  const st = data.engine ?? {}, s = data.settings ?? {}, mix = data.by_timeframe?.[tf] ?? { regime: {}, volatility: {}, total: 0 };
  const age = st.last_run_at ? Math.round((Date.now() - Date.parse(st.last_run_at)) / 1000) : null;
  const kpi = (label: string, value: any, hint?: string, cls = '') => <div className={`intel-kpi ${cls}`} title={hint}><small>{label}</small><b>{value}</b></div>;
  const small = (rows: Row[] | undefined, cols: [string, (r: Row) => any][]) => rows?.length ? <table className="cfg-table compact intel-table"><thead><tr>{cols.map(c => <th key={c[0]}>{c[0]}</th>)}</tr></thead>
    <tbody>{rows.map(r => <tr key={r.account_id + r.broker_symbol} className="clickable" onClick={() => openInstrument(r)} title={(r.reasons ?? []).join(' · ')}>{cols.map(c => <td key={c[0]}>{c[1](r)}</td>)}</tr>)}</tbody></table>
    : <p className="cfg-note">Nothing yet.</p>;
  return <section className="intel">
    <div className="intel-bar">
      <span className="intel-tfs" role="group" aria-label="Timeframe">{TFS.map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
      <span className="cfg-pill demo">{data.session?.label}</span>
      <button onClick={gotoScreener}>Open the screener</button><button onClick={gotoSettings}>Engine settings</button>
      {canEdit && <button disabled={!!busy} onClick={() => void act('Recomputed every instrument.', () => api('intel/refresh', 'POST'))}>{busy.startsWith('Recomputed') ? 'Computing…' : 'Recompute now'}</button>}
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="intel-kpis">
      {kpi('Instruments covered', `${data.covered}/${data.collected}`, 'Instruments with features / instruments being collected')}
      {kpi('Trend up', mix.regime.TREND_UP ?? 0, `${tf}`, 'up')}{kpi('Trend down', mix.regime.TREND_DOWN ?? 0, tf, 'down')}
      {kpi('Range', mix.regime.RANGE ?? 0, tf)}{kpi('Transition', mix.regime.TRANSITION ?? 0, tf)}
      {kpi('Warming up', mix.regime.WARMING_UP ?? 0, 'Fewer than 60 closed bars on this timeframe', (mix.regime.WARMING_UP ?? 0) ? 'warn' : '')}
      {kpi('High volatility', mix.volatility.HIGH ?? 0, 'ATR% in the top 20% of its last 100 bars')}
      {kpi('Engine', st.paused || s.enabled === false ? 'Paused' : age == null ? 'Not run yet' : `${age} s ago`, st.last_run_at ? `${st.instruments} instruments · ${st.duration_ms} ms` : '', st.errors?.length ? 'warn' : '')}
      {kpi('Feature history', Number(data.history_rows ?? 0).toLocaleString(), data.history_since ? `since ${when(data.history_since)}` : '')}
      {kpi('Late or stale', data.stale, `Features whose bar is behind what the market has closed, or not looked at for 10 minutes${data.freshness ? ` · ${Object.entries(data.freshness).map(([k, n]) => `${String(k).toLowerCase()} ${n}`).join(', ')}` : ''}`, data.stale ? 'warn' : '')}
    </div>
    {st.errors?.length ? <p className="cfg-error">Engine errors: {st.errors.join(' · ')}</p> : null}
    <div className="intel-cols">
      <div><h4>Regime mix by timeframe</h4>
        <table className="cfg-table compact intel-table"><thead><tr><th>TF</th><th>Up</th><th>Down</th><th>Range</th><th>Transition</th><th>Warming</th><th>High vol</th><th>Low vol</th></tr></thead><tbody>
          {TFS.map(t => { const m = data.by_timeframe?.[t] ?? { regime: {}, volatility: {} }; return <tr key={t} className={t === tf ? 'selected clickable' : 'clickable'} onClick={() => setTf(t)}>
            <td><strong>{t}</strong></td><td>{m.regime.TREND_UP ?? 0}</td><td>{m.regime.TREND_DOWN ?? 0}</td><td>{m.regime.RANGE ?? 0}</td><td>{m.regime.TRANSITION ?? 0}</td>
            <td>{m.regime.WARMING_UP ?? 0}</td><td>{m.volatility.HIGH ?? 0}</td><td>{m.volatility.LOW ?? 0}</td></tr>; })}
        </tbody></table></div>
      <div><h4>Needs history ({tf})</h4>
        {data.warming_up?.length ? <>
          <p className="cfg-note">Fewer than 60 closed {tf} bars. <em>Get history</em> asks the broker for 14 days of M1 (builds M5–H1) and a year of H4 and D1;
            the jobs run in Market data › Jobs and features warm up within a few minutes. Each request is audited.</p>
          {canEdit && <div className="cfg-actions"><button className="primary" disabled={!!busy}
            onClick={() => void act('History requested for every warming-up instrument.', async () => { const r = await api('intel/history', 'POST', {});
              setNotice(`History requested: ${r.jobs.length} jobs for ${r.plans} instruments${r.skipped.length ? ` (${r.skipped.length} already queued)` : ''}.`); })}>Get history for all</button></div>}
          <table className="cfg-table compact intel-table"><tbody>{data.warming_up.map((r: Row) => <tr key={r.account_id + r.broker_symbol}>
            <td><strong>{r.broker_symbol}</strong> <small className="cfg-note">{r.provider_name} · {r.account_code}</small></td><td><small>{r.bars} bars</small></td>
            <td>{canEdit && r.plan_id && <button disabled={!!busy}
              onClick={() => void act(`History requested for ${r.broker_symbol}.`, () => api('intel/history', 'POST', { plan_id: r.plan_id }))}>Get history</button>}</td></tr>)}</tbody></table>
        </> : <p className="cfg-note">Every instrument has enough {tf} bars.</p>}</div>
    </div>
    <div className="intel-cols">
      <div><h4>Top attention ({tf})</h4>{small(data.top, [['Instrument', r => <strong>{r.broker_symbol}</strong>], ['Regime', r => pill(r.regime)], ['ADX', r => num(r.adx14, 1)], ['Attention', r => <b>{num(r.attention, 0)}</b>]])}</div>
      <div><h4>Biggest 20-bar moves ({tf})</h4>{small(data.movers, [['Instrument', r => <strong>{r.broker_symbol}</strong>], ['20-bar %', r => <span className={r.ret_20_pct > 0 ? 'up' : 'down'}>{num(r.ret_20_pct, 2)}</span>], ['RSI', r => num(r.rsi14, 1)], ['Regime', r => pill(r.regime)]])}</div>
    </div>
    <div className="intel-cols">
      <div><h4>High-impact events, next 24 h</h4>{(data.events_next_24h ?? []).filter((e: Row) => e.impact === 'HIGH' || e.impact === 'HOLIDAY').length
        ? <table className="cfg-table compact intel-table"><tbody>{data.events_next_24h.filter((e: Row) => e.impact === 'HIGH' || e.impact === 'HOLIDAY').map((e: Row) =>
          <tr key={e.id}><td>{new Date(e.at).toLocaleString()}</td><td><strong>{e.currency}</strong></td><td>{e.title}</td></tr>)}</tbody></table>
        : <p className="cfg-note">None in the calendar (Intelligence › Calendar).</p>}</div>
      <div><h4>Rules fired recently ({data.rules ?? 0} rules · {data.watchlists ?? 0} watchlists)</h4>{(data.recent_fires ?? []).length
        ? <table className="cfg-table compact intel-table"><tbody>{data.recent_fires.map((f: Row) => <tr key={f.id}><td><small>{when(f.fired_at)}</small></td><td>{f.rule_name}</td><td>{f.message}</td></tr>)}</tbody></table>
        : <p className="cfg-note">Nothing fired yet (Intelligence › Rules & alerts).</p>}</div>
    </div>
  </section>;
}

/* Engine settings: on or paused, cycle, timeframes, instruments in the screen, history retention, event-risk window.
 * Every change needs a reason and is audited; a stale version is refused (someone else changed it). */
export function EngineSettings({ canEdit }: { canEdit: boolean }) {
  const [s, setS] = useState<Row | null>(null), [st, setSt] = useState<Row>({}), [form, setForm] = useState<Row | null>(null), [reason, setReason] = useState('');
  const [all, setAll] = useState<Row[]>([]), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false), [q, setQ] = useState('');
  const load = async () => { try { const o = await api('intel/overview?timeframe=H1'); setS(o.settings); setSt(o.engine ?? {}); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); void api('intel/screener?timeframe=H1').then(d => setAll(d.items)).catch(() => undefined); }, []);
  if (!s) return error ? <p role="alert" className="cfg-error">{error}</p> : <p>Loading…</p>;
  const f = form ?? { enabled: s.enabled, interval_seconds: s.interval_seconds, timeframes: s.timeframes ?? TFS, excluded: s.excluded ?? [],
    retention_days: s.retention_days ?? 365, event_window_minutes: s.event_window_minutes ?? 30 };
  const isOut = (r: Row) => (f.excluded ?? []).some((x: Row) => x.account_id === r.account_id && x.broker_symbol === r.broker_symbol);
  const set = (patch: Row) => setForm({ ...f, ...patch });
  const save = async () => {
    setBusy(true); setError(''); setNotice('');
    try { await api('intel/settings', 'PUT', { ...f, version: s.version, reason }); setForm(null); setReason(''); setNotice('Engine settings saved.'); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };
  const shown = all.filter(r => !q || `${r.broker_symbol} ${r.account_code} ${r.provider_name}`.toLowerCase().includes(q.toLowerCase()));
  return <section className="intel">
    {error && <p role="alert" className="cfg-error">{error}</p>}{notice && <p role="status" className="cfg-ok">{notice}</p>}
    <p className="cfg-note">Engine: {f.enabled ? 'on' : 'paused'} · last run {when(st.last_run_at)} · {st.instruments ?? 0} instruments · {st.duration_ms ?? 0} ms{st.errors?.length ? ` · ${st.errors.length} errors` : ''}</p>
    <div className="cfg-form">
      <label><input type="checkbox" disabled={!canEdit} checked={!!f.enabled} onChange={e => set({ enabled: e.target.checked })} /> Engine on (recomputes every cycle; paused keeps the last values)</label>
      <label>Cycle (seconds, 15–3600) <input type="number" min={15} max={3600} disabled={!canEdit} value={f.interval_seconds} onChange={e => set({ interval_seconds: Number(e.target.value) })} /></label>
      <label>Keep feature history (days, 7–3650) <input type="number" min={7} max={3650} disabled={!canEdit} value={f.retention_days} onChange={e => set({ retention_days: Number(e.target.value) })} /></label>
      <label>Event-risk window (± minutes) <input type="number" min={0} max={720} disabled={!canEdit} value={f.event_window_minutes} onChange={e => set({ event_window_minutes: Number(e.target.value) })} /></label>
      <div>Timeframes computed {TFS.map(t => <label key={t} className="intel-check"><input type="checkbox" disabled={!canEdit} checked={(f.timeframes ?? []).includes(t)}
        onChange={e => set({ timeframes: e.target.checked ? TFS.filter(x => x === t || (f.timeframes ?? []).includes(x)) : (f.timeframes ?? []).filter((x: string) => x !== t) })} /> {t}</label>)}</div>
      <div>Instruments in the screen ({all.length - all.filter(isOut).length} of {all.length})
        <div className="intel-bar"><input aria-label="Filter instruments" placeholder="Filter…" value={q} onChange={e => setQ(e.target.value)} />
          {canEdit && <><button onClick={() => set({ excluded: (f.excluded ?? []).filter((x: Row) => !shown.some(r => r.account_id === x.account_id && r.broker_symbol === x.broker_symbol)) })}>Include shown</button>
            <button onClick={() => set({ excluded: [...(f.excluded ?? []), ...shown.filter(r => !isOut(r)).map(r => ({ account_id: r.account_id, broker_symbol: r.broker_symbol }))] })}>Leave out shown</button></>}</div>
        <div className="intel-checks">{shown.map(r => <label key={r.account_id + r.broker_symbol} className="intel-check"><input type="checkbox" disabled={!canEdit} checked={!isOut(r)}
          onChange={e => set({ excluded: e.target.checked ? (f.excluded ?? []).filter((x: Row) => !(x.account_id === r.account_id && x.broker_symbol === r.broker_symbol))
            : [...(f.excluded ?? []), { account_id: r.account_id, broker_symbol: r.broker_symbol }] })} /> {r.broker_symbol} <small className="cfg-note">{r.provider_name} · {r.account_code}</small></label>)}
          {all.length === 0 && <small className="cfg-note">none yet</small>}</div></div>
      {canEdit && <><label>Reason for the change <input aria-label="Reason for the change" placeholder="At least 5 characters, kept in the audit log" value={reason} onChange={e => setReason(e.target.value)} /></label>
        <div className="cfg-actions"><button className="primary" disabled={busy || !form || reason.trim().length < 5}
          title={!form ? 'Nothing changed yet' : reason.trim().length < 5 ? 'Enter a reason first' : ''} onClick={() => void save()}>Save engine settings</button>
          {form && <button onClick={() => setForm(null)}>Undo changes</button>}
          <button disabled={busy} onClick={() => void (async () => { setBusy(true); try { await api('intel/refresh', 'POST'); setNotice('Recomputed every instrument.'); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } })()}>Recompute now</button></div></>}
      <p className="cfg-note">Last changed {when(s.updated_at)} by {s.updated_by}. Every change is in the audit log with its reason.</p>
    </div>
  </section>;
}
