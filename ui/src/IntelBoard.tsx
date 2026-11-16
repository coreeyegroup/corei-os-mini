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
const trend = (f: Row) => f.ema20 == null || f.ema50 == null ? '–'
  : `${f.ema20 > f.ema50 ? '▲' : '▼'} 20/50${f.ema200 != null ? ` · ${f.close > f.ema200 ? 'above' : 'below'} 200` : ''}`;

export function Screener({ openInstrument, canRefresh }: { openInstrument: (r: Row) => void; canRefresh: boolean }) {
  const [tf, setTf] = useState(() => { try { return localStorage.getItem('corei.intel.tf') || 'H1'; } catch { return 'H1'; } });
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [regimeF, setRegimeF] = useState(''), [volF, setVolF] = useState(''), [q, setQ] = useState('');
  const [srcF, setSrcF] = useState(''), [classF, setClassF] = useState(''), [wlF, setWlF] = useState(''), [lists, setLists] = useState<Row[]>([]);
  useEffect(() => { void api('intel/watchlists').then(d => setLists(d.items)).catch(() => undefined); }, []);
  const wl = lists.find(w => w.id === wlF);
  const [sort, setSort] = useState<[string, number]>(['rank', 1]);
  const load = async () => { try { setData(await api(`intel/screener?timeframe=${tf}`)); setError(''); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { try { localStorage.setItem('corei.intel.tf', tf); } catch { /* no storage */ } void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, [tf]);
  const recompute = async () => { setBusy(true); try { await api('intel/refresh', 'POST'); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  const rows = useMemo(() => {
    const key = (r: Row): any => ({ rank: r.rank, symbol: r.broker_symbol, adx: r.features.adx14, rsi: r.features.rsi14, atr: r.features.atr_pct,
      ret: r.features.ret_20_pct, quality: r.quality, attention: r.attention, conf: r.confidence } as Row)[sort[0]];
    return (data?.items ?? []).filter((r: Row) => (!regimeF || r.regime === regimeF) && (!volF || r.volatility === volF)
      && (!srcF || r.account_id === srcF) && (!classF || (r.asset_class ?? 'OTHER') === classF)
      && (!wl || wl.items.some((x: Row) => x.account_id === r.account_id && x.broker_symbol === r.broker_symbol))
      && (!q || `${r.broker_symbol} ${r.instrument ?? ''} ${r.account_code} ${r.provider_name}`.toLowerCase().includes(q.toLowerCase())))
      .sort((a: Row, b: Row) => { const x = key(a), y = key(b); return x == null ? 1 : y == null ? -1 : (x < y ? -1 : x > y ? 1 : 0) * sort[1]; });
  }, [data, regimeF, volF, q, sort, srcF, classF, wlF, lists]);
  const sources = Array.from(new Map((data?.items ?? []).map((r: Row) => [r.account_id, `${r.provider_name} · ${r.account_code}`])).entries());
  const classes = Array.from(new Set((data?.items ?? []).map((r: Row) => r.asset_class ?? 'OTHER'))) as string[];
  const th = (id: string, label: string, title?: string) => <th title={title} className="sortable" onClick={() => setSort([id, sort[0] === id ? -sort[1] : (id === 'rank' || id === 'symbol' ? 1 : -1)])}>
    {label}{sort[0] === id ? (sort[1] > 0 ? ' ▲' : ' ▼') : ''}</th>;
  const st = data?.engine;
  const counts = (data?.items ?? []).reduce((m: Row, r: Row) => ({ ...m, [r.regime]: (m[r.regime] ?? 0) + 1 }), {});
  return <section className="intel">
    <div className="intel-bar">
      <span className="intel-tfs" role="group" aria-label="Timeframe">{TFS.map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
      <select aria-label="Regime" value={regimeF} onChange={e => setRegimeF(e.target.value)}>{REGIMES.map(([v, l]) => <option key={v} value={v}>{l}{v && counts[v] ? ` (${counts[v]})` : ''}</option>)}</select>
      <select aria-label="Volatility" value={volF} onChange={e => setVolF(e.target.value)}><option value="">All volatility</option><option value="HIGH">High</option><option value="NORMAL">Normal</option><option value="LOW">Low</option></select>
      <select aria-label="Source" value={srcF} onChange={e => setSrcF(e.target.value)}><option value="">All brokers & accounts</option>{sources.map(([id, l]) => <option key={id as string} value={id as string}>{l as string}</option>)}</select>
      <select aria-label="Asset class" value={classF} onChange={e => setClassF(e.target.value)}><option value="">All asset classes</option>{classes.map(c => <option key={c}>{c}</option>)}</select>
      <select aria-label="Watchlist" value={wlF} onChange={e => setWlF(e.target.value)}><option value="">No watchlist</option>{lists.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select>
      <input aria-label="Search" placeholder="Symbol, source…" value={q} onChange={e => setQ(e.target.value)} />
      {data?.session && <span className="cfg-pill demo" title="Session now (UTC-based map; see Sessions)">{data.session.label}</span>}
      {canRefresh && <button onClick={() => void recompute()} disabled={busy}>{busy ? 'Computing…' : 'Recompute now'}</button>}
      <small className="cfg-note">{st?.last_run_at ? `Engine: ${when(st.last_run_at)} · ${st.instruments} instruments · ${st.duration_ms} ms${st.errors?.length ? ` · ${st.errors.length} errors` : ''}` : 'Engine: runs every minute'}</small>
    </div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {st?.errors?.length ? <p className="cfg-error">{st.errors[0]}</p> : null}
    {data && !data.items.length && <p className="cfg-note">No features yet. The screener lists every instrument being collected (Market data › plans) once it has bars; the engine runs every minute.
      Higher timeframes need history: backfill bars on the source (Market data › source › Backfill).</p>}
    {rows.length > 0 && <div className="cfg-scroll"><table className="cfg-table compact intel-table"><thead><tr>
      {th('rank', '#')}{th('symbol', 'Instrument')}<th>Source</th><th>Last</th><th title="Spread now, and as % of ATR">Spread</th>{th('quality', 'Quality', 'Feed quality score, last 60 min')}
      {th('conf', 'Regime')}<th>Vol.</th>{th('adx', 'ADX')}{th('rsi', 'RSI')}{th('atr', 'ATR %')}<th>Trend (EMA)</th>{th('ret', '20-bar %')}<th title="Close within the 20-bar range (0 low, 100 high)">Range pos.</th>
      {th('attention', 'Attention', 'Rank 0–100: quality × spread vs ATR × (movement + direction)')}<th title="Timeframes agreeing: trend up minus trend down over those past warming up">Align</th><th title="High-impact event or holiday for its currencies within the window">Event</th><th>Bars</th><th>Bar (UTC)</th></tr></thead><tbody>
      {rows.map((r: Row) => { const f = r.features; const mid = r.bid != null && r.ask != null ? (r.bid + r.ask) / 2 : f.close;
        return <tr key={r.account_id + r.broker_symbol} className="clickable" onClick={() => openInstrument(r)} title={(r.reasons ?? []).join(' · ')}>
          <td>{r.rank}</td><td><strong>{r.broker_symbol}</strong>{r.instrument && r.instrument !== r.broker_symbol ? <small className="cfg-note"> {r.instrument}</small> : null}</td>
          <td><small>{r.provider_name} · {r.account_code}</small></td><td>{price(mid)}</td>
          <td>{price(r.spread)}{r.spread_atr_pct != null ? <small className="cfg-note"> ({r.spread_atr_pct}%)</small> : null}</td>
          <td>{num(r.quality, 0)}</td><td>{pill(r.regime)} <small className="cfg-note">{r.regime !== 'WARMING_UP' ? `${Math.round(100 * r.confidence)}%` : ''}</small></td>
          <td>{r.volatility ? r.volatility.toLowerCase() : '–'}</td><td>{num(f.adx14, 1)}</td><td>{num(f.rsi14, 1)}</td><td>{num(f.atr_pct, 3)}</td><td>{trend(f)}</td>
          <td className={f.ret_20_pct > 0 ? 'up' : f.ret_20_pct < 0 ? 'down' : ''}>{num(f.ret_20_pct, 2)}</td><td>{num(f.range_pos20, 0)}</td>
          <td title={Object.entries(r.attention_parts ?? {}).map(([k, v]) => `${k} ${v}`).join(' · ')}><strong>{num(r.attention, 0)}</strong></td>
          <td title={r.alignment ? `${r.alignment.up} up, ${r.alignment.down} down of ${r.alignment.of}` : ''}><small>{r.alignment?.label ?? '–'}</small></td>
          <td>{r.event_risk?.length ? <span className="cfg-pill live" title={r.event_risk.map((e: Row) => `${e.currency} ${e.title} ${new Date(e.at).toLocaleTimeString()}`).join(' · ')}>event risk</span>
            : r.next_event ? <small className="cfg-note" title={r.next_event.title}>{r.next_event.currency} {new Date(r.next_event.at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</small> : '–'}</td>
          <td><small>{r.bars} · {r.bars_source === 'BROKER' ? 'broker' : 'from M1'}</small></td><td><small>{new Date(r.bar_time).toISOString().slice(5, 16).replace('T', ' ')}</small></td>
        </tr>; })}
    </tbody></table></div>}
    <p className="cfg-note">Regime rules (v1): trend = ADX ≥ 25 and efficiency ≥ 0.30 with EMA20/50 and +DI/−DI agreeing; range = ADX &lt; 20; volatility = ATR% rank among the last 100 bars (high ≥ 80, low ≤ 20). Closed bars only. Hover a row for its reasons; click it for every timeframe.</p>
  </section>;
}

export function InstrumentView({ pick, items, setPick }: { pick: Row | null; items: Row[]; setPick: (r: Row) => void }) {
  const [tf, setTf] = useState('H1'), [data, setData] = useState<Row | null>(null), [error, setError] = useState('');
  useEffect(() => {
    if (!pick) return;
    let stop = false;
    const load = () => api(`intel/instrument/${pick.account_id}/${encodeURIComponent(pick.broker_symbol)}?timeframe=${tf}&limit=200`)
      .then(d => { if (!stop) { setData(d); setError(''); } }).catch(e => { if (!stop) setError((e as Error).message); });
    void load(); const t = window.setInterval(load, 30_000); return () => { stop = true; window.clearInterval(t); };
  }, [pick?.account_id, pick?.broker_symbol, tf]);
  const csv = () => {
    const keys = ['close', 'ema20', 'ema50', 'ema200', 'rsi14', 'atr14', 'atr_pct', 'adx14', 'plus_di', 'minus_di', 'bb_upper', 'bb_lower', 'er20', 'rv20_pct', 'ret_20_pct'];
    const lines = ['bar_time,regime,' + keys.join(','), ...(data?.history ?? []).map((h: Row) => [h.bar_time, h.regime, ...keys.map(k => h.features[k] ?? '')].join(','))];
    const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([lines.join('\n')], { type: 'text/csv' }));
    a.download = `${pick?.broker_symbol}-${tf}-features.csv`; a.click();
  };
  return <section className="intel">
    <div className="intel-bar">
      <select aria-label="Instrument" value={pick ? pick.account_id + '|' + pick.broker_symbol : ''} onChange={e => { const r = items.find(x => x.account_id + '|' + x.broker_symbol === e.target.value); if (r) setPick(r); }}>
        <option value="">Choose an instrument…</option>
        {items.map(r => <option key={r.account_id + r.broker_symbol} value={r.account_id + '|' + r.broker_symbol}>{r.broker_symbol} · {r.provider_name} · {r.account_code}</option>)}
      </select>
      <span className="intel-tfs" role="group" aria-label="Chart timeframe">{TFS.filter(t => t !== 'M1').map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
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
    </>}
  </section>;
}

function FeatureChart({ history, tf }: { history: Row[]; tf: string }) {
  if (!history?.length) return <p className="cfg-note">No {tf} history yet: it builds as bars close (or backfill the source's history).</p>;
  const W = 900, H = 260, R = 80, pad = 6;
  const pts = history.map(h => h.features);
  const vals = pts.flatMap(f => [f.close, f.bb_upper, f.bb_lower, f.ema20, f.ema50].filter((v: any) => v != null)) as number[];
  const lo = Math.min(...vals), hi = Math.max(...vals), x = (i: number) => pad + (W - 2 * pad) * i / Math.max(1, pts.length - 1);
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (v - lo) / (hi - lo || 1));
  const line = (k: string) => pts.map((f, i) => f[k] == null ? '' : `${x(i)},${y(f[k])}`).filter(Boolean).join(' ');
  const ry = (v: number) => H + 10 + (R - 10) * (1 - v / 100);
  const colour: Row = { TREND_UP: 'var(--ready)', TREND_DOWN: 'var(--danger)', RANGE: 'var(--accent)', TRANSITION: 'var(--cfg-warn)', WARMING_UP: 'var(--line)' };
  return <figure className="intel-chart">
    <svg viewBox={`0 0 ${W} ${H + R + 14}`} role="img" aria-label={`${tf} close with EMA 20/50, Bollinger bands, regime and RSI`}>
      <polyline points={line('bb_upper')} className="bb" /><polyline points={line('bb_lower')} className="bb" />
      <polyline points={line('ema50')} className="ema50" /><polyline points={line('ema20')} className="ema20" /><polyline points={line('close')} className="close" />
      {history.map((h, i) => <rect key={i} x={x(i) - (W / pts.length) / 2} y={H - 4} width={Math.max(1, W / pts.length)} height={4} fill={colour[h.regime] ?? 'var(--line)'} />)}
      <line x1={pad} x2={W - pad} y1={ry(70)} y2={ry(70)} className="guide" /><line x1={pad} x2={W - pad} y1={ry(30)} y2={ry(30)} className="guide" />
      <polyline points={pts.map((f, i) => f.rsi14 == null ? '' : `${x(i)},${ry(f.rsi14)}`).filter(Boolean).join(' ')} className="rsi" />
    </svg>
    <figcaption className="cfg-note">{tf} · {history.length} closed bars · close (bold), EMA20, EMA50, Bollinger 20/2 (dotted) · bottom strip: regime per bar (green trend up, red trend down, blue range, amber transition) · lower panel: RSI 14 with 30/70.</figcaption>
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
    <p className="cfg-note">FX week: Sunday 17:00 to Friday 17:00 New York. Daylight-saving changes follow each centre's time zone. Exchange holidays and economic events are the next component (calendar).</p>
  </section>;
}

/* Dashboard: coverage, regime mix, what needs history (one click), the top of the screen, the biggest moves, and the
 * engine's controls (pause, cycle, timeframes, instruments left out), each change with a reason and audited. */
export function IntelDashboard({ openInstrument, canEdit, gotoScreener }: { openInstrument: (r: Row) => void; canEdit: boolean; gotoScreener: () => void }) {
  const [tf, setTf] = useState('H1'), [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [form, setForm] = useState<Row | null>(null), [reason, setReason] = useState(''), [busy, setBusy] = useState('');
  const [all, setAll] = useState<Row[]>([]);
  const load = async () => { try { const d = await api(`intel/overview?timeframe=${tf}`); setData(d); setError(''); if (!form) setForm(null); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, [tf]);
  useEffect(() => { void api('intel/screener?timeframe=H1').then(d => setAll(d.items)).catch(() => undefined); }, [data?.covered]);
  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label); setError(''); setNotice('');
    try { await fn(); setNotice(label); await load(); } catch (e) { setError((e as Error).message); } finally { setBusy(''); }
  };
  if (!data) return error ? <p role="alert" className="cfg-error">{error}</p> : <p>Loading…</p>;
  const st = data.engine ?? {}, s = data.settings ?? {}, mix = data.by_timeframe?.[tf] ?? { regime: {}, volatility: {}, total: 0 };
  const age = st.last_run_at ? Math.round((Date.now() - Date.parse(st.last_run_at)) / 1000) : null;
  const f = form ?? { enabled: s.enabled, interval_seconds: s.interval_seconds, timeframes: s.timeframes ?? TFS, excluded: s.excluded ?? [],
    retention_days: s.retention_days ?? 365, event_window_minutes: s.event_window_minutes ?? 30 };
  const isOut = (r: Row) => (f.excluded ?? []).some((x: Row) => x.account_id === r.account_id && x.broker_symbol === r.broker_symbol);
  const kpi = (label: string, value: any, hint?: string, cls = '') => <div className={`intel-kpi ${cls}`} title={hint}><small>{label}</small><b>{value}</b></div>;
  const small = (rows: Row[], cols: [string, (r: Row) => any][]) => rows.length ? <table className="cfg-table compact intel-table"><thead><tr>{cols.map(c => <th key={c[0]}>{c[0]}</th>)}</tr></thead>
    <tbody>{rows.map(r => <tr key={r.account_id + r.broker_symbol} className="clickable" onClick={() => openInstrument(r)} title={(r.reasons ?? []).join(' · ')}>{cols.map(c => <td key={c[0]}>{c[1](r)}</td>)}</tr>)}</tbody></table>
    : <p className="cfg-note">Nothing yet.</p>;
  return <section className="intel">
    <div className="intel-bar">
      <span className="intel-tfs" role="group" aria-label="Timeframe">{TFS.map(t => <button key={t} aria-pressed={t === tf} onClick={() => setTf(t)}>{t}</button>)}</span>
      <span className="cfg-pill demo">{data.session?.label}</span>
      <button onClick={gotoScreener}>Open the screener</button>
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
      {kpi('Stale', data.stale, 'Snapshots not recomputed for 10 minutes', data.stale ? 'warn' : '')}
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
        {data.warming_up.length ? <>
          <p className="cfg-note">Fewer than 60 closed {tf} bars. <em>Get history</em> backfills 14 days of M1 (builds M5–H1) and a year of H4 and D1 from the broker.</p>
          {canEdit && <input aria-label="Reason for history" placeholder="Reason (e.g. warm up features)" value={reason} onChange={e => setReason(e.target.value)} />}
          <table className="cfg-table compact intel-table"><tbody>{data.warming_up.map((r: Row) => <tr key={r.account_id + r.broker_symbol}>
            <td><strong>{r.broker_symbol}</strong> <small className="cfg-note">{r.provider_name} · {r.account_code}</small></td><td><small>{r.bars} bars</small></td>
            <td>{canEdit && r.plan_id && <button disabled={!!busy || reason.trim().length < 5} title={reason.trim().length < 5 ? 'Enter a reason first' : ''}
              onClick={() => void act(`History requested for ${r.broker_symbol}.`, () => api('intel/history', 'POST', { plan_id: r.plan_id, reason }))}>Get history</button>}</td></tr>)}</tbody></table>
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
    <details className="cfg-more intel-engine" open={!s.enabled}><summary>Engine controls</summary>
      <div className="cfg-form">
        <label><input type="checkbox" disabled={!canEdit} checked={!!f.enabled} onChange={e => setForm({ ...f, enabled: e.target.checked })} /> Engine on (recomputes every cycle)</label>
        <label>Cycle (seconds) <input type="number" min={15} max={3600} disabled={!canEdit} value={f.interval_seconds} onChange={e => setForm({ ...f, interval_seconds: Number(e.target.value) })} /></label>
        <label>Keep feature history (days) <input type="number" min={7} max={3650} disabled={!canEdit} value={f.retention_days} onChange={e => setForm({ ...f, retention_days: Number(e.target.value) })} /></label>
        <label>Event-risk window (± minutes) <input type="number" min={0} max={720} disabled={!canEdit} value={f.event_window_minutes} onChange={e => setForm({ ...f, event_window_minutes: Number(e.target.value) })} /></label>
        <div>Timeframes {TFS.map(t => <label key={t} className="intel-check"><input type="checkbox" disabled={!canEdit} checked={(f.timeframes ?? []).includes(t)}
          onChange={e => setForm({ ...f, timeframes: e.target.checked ? TFS.filter(x => x === t || (f.timeframes ?? []).includes(x)) : (f.timeframes ?? []).filter((x: string) => x !== t) })} /> {t}</label>)}</div>
        <div>Instruments in the screen {all.length === 0 && <small className="cfg-note">none yet</small>}
          <div className="intel-checks">{all.map(r => <label key={r.account_id + r.broker_symbol} className="intel-check"><input type="checkbox" disabled={!canEdit} checked={!isOut(r)}
            onChange={e => setForm({ ...f, excluded: e.target.checked ? (f.excluded ?? []).filter((x: Row) => !(x.account_id === r.account_id && x.broker_symbol === r.broker_symbol))
              : [...(f.excluded ?? []), { account_id: r.account_id, broker_symbol: r.broker_symbol }] })} /> {r.broker_symbol} <small className="cfg-note">{r.account_code}</small></label>)}</div></div>
        {canEdit && <><input aria-label="Reason for the change" placeholder="Reason for the change" value={reason} onChange={e => setReason(e.target.value)} />
          <div className="cfg-actions"><button className="primary" disabled={!!busy || !form || reason.trim().length < 5}
            onClick={() => void act('Engine settings saved.', async () => { await api('intel/settings', 'PUT', { ...f, version: s.version, reason }); setForm(null); })}>Save engine settings</button>
            {form && <button onClick={() => setForm(null)}>Undo changes</button>}</div></>}
        <p className="cfg-note">Last changed {when(s.updated_at)} by {s.updated_by}. Every change is in the audit log with its reason.</p>
      </div>
    </details>
  </section>;
}
