/* "Start here": the trading stages are empty until a strategy runs. This panel shows on every stage screen while no
 * deployment is active and starts a paper strategy on a collected instrument in one step.
 * Why: a new user should see signals, risk decisions, orders, positions and post-trade fill up within minutes, then
 * change what was made in Setup.
 * Purpose: pick a collected source, a starter rule set and a bar period; the backend creates the strategy, version,
 * mapping and PAPER deployment through the same rules as Setup (/api/trading/quickstart).
 * Must never: create real (broker) trading — that stays a deliberate step in Setup. */
import { useEffect, useState } from 'react';
import { api, Choice, Field } from './Configuration';
import { goto } from './nav';

type Row = Record<string, any>;

export function StartHere({ ctx }: { ctx: { canOps: boolean; canRisk: boolean } }) {
  const [opts, setOpts] = useState<Row | null>(null), [n, setN] = useState(0);
  const [f, setF] = useState<Row>({ source: '', preset: 'EMA_FAST', timeframe: 'M5', quantity: '' });
  const [busy, setBusy] = useState(false), [err, setErr] = useState(''), [done, setDone] = useState<Row | null>(null);
  const [hidden, setHidden] = useState(() => { try { return sessionStorage.getItem('corei.starthere.hidden') === '1'; } catch { return false; } });
  useEffect(() => { let live = true; void api('trading/quickstart').then(o => live && setOpts(o)).catch(() => live && setOpts(null)); return () => { live = false; }; }, [n]);
  if (!opts || (!done && Number(opts.active) > 0) || hidden) return null;
  const ready = (opts.sources ?? []).filter((s: Row) => s.ready), notReady = (opts.sources ?? []).filter((s: Row) => !s.ready);
  const preset = (opts.presets ?? []).find((p: Row) => p.id === f.preset);
  const start = async () => { setErr(''); setBusy(true);
    try { const [account_id, broker_symbol] = (f.source || `${ready[0].account_id}|${ready[0].broker_symbol}`).split('|');
      setDone(await api('trading/quickstart', 'POST', { account_id, broker_symbol, preset: f.preset, timeframe: f.timeframe, quantity: f.quantity || null })); setN(x => x + 1); }
    catch (x) { setErr((x as Error).message); } finally { setBusy(false); } };
  const hide = () => { try { sessionStorage.setItem('corei.starthere.hidden', '1'); } catch { /* private window */ } setHidden(true); };
  return <section className="st-start" aria-label="Start here">
    {done ? <>
      <h3>{done.activated ? 'Running on paper' : 'Created'}: {done.strategy.code}</h3>
      <p className="cfg-note">{done.steps.join(' → ')}.</p>
      <p>{done.next}</p>
      <div className="cfg-actions"><button className="primary" onClick={() => goto('Strategies', 'runtime')}>Watch it run (Strategies › Runtime)</button>
        <button onClick={() => goto('Strategies', 'backtest')}>Backtest it</button><button onClick={() => { setDone(null); hide(); }}>Close</button></div>
    </> : <>
      <h3>Nothing is running yet — start a paper strategy in one step</h3>
      <p className="cfg-note">Signals, risk decisions, orders, positions and post-trade fill up once a strategy runs. This makes one on an instrument you already collect,
        on <b>paper</b> (COREI fills at live prices; nothing goes to a broker). Change or stop it any time in Setup › Strategies and Deployments.</p>
      {!ready.length ? <>
        <p className="cfg-error">No collected instrument is ready{notReady.length ? `: ${notReady.map((s: Row) => `${s.broker_symbol} (${s.why_not})`).join('; ')}` : '.'}</p>
        <div className="cfg-actions"><button className="primary" onClick={() => goto('Market data', 'plans')}>Collect an instrument (Market data › Plans)</button></div>
      </> : ctx.canOps ? <>
        <div className="cfg-pair">
          <Field label="Instrument (collected)"><select aria-label="Start source" value={f.source || `${ready[0].account_id}|${ready[0].broker_symbol}`} onChange={e => setF({ ...f, source: e.target.value })}>
            {ready.map((s: Row) => <option key={s.account_id + s.broker_symbol} value={`${s.account_id}|${s.broker_symbol}`}>{s.instrument} · {s.broker_symbol} on {s.account_code} ({s.environment.toLowerCase()})</option>)}</select></Field>
          <Field label="Rules"><select aria-label="Start preset" value={f.preset} onChange={e => setF({ ...f, preset: e.target.value })}>
            {(opts.presets ?? []).map((p: Row) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></Field>
          <Field label="Bar"><Choice label="Start timeframe" value={f.timeframe} options={[['M1', '1 min'], ['M5', '5 min'], ['M15', '15 min'], ['H1', '1 hour'], ['H4', '4 hours'], ['D1', '1 day']]} onChange={v => setF({ ...f, timeframe: v })} /></Field>
          <Field label="Size" hint="Blank: the smallest tradable size"><input aria-label="Start quantity" inputMode="decimal" value={f.quantity} onChange={e => setF({ ...f, quantity: e.target.value })} /></Field>
        </div>
        {preset && <p className="cfg-note">{preset.why}</p>}
        {!ctx.canRisk && <p className="cfg-note">You can create it; a Risk approver approves the version and activates it.</p>}
        <div className="cfg-actions"><button className="primary" disabled={busy} onClick={() => void start()}>{busy ? 'Starting…' : 'Start on paper'}</button>
          <button onClick={() => goto('Configuration', 'strategies')}>Set it up by hand (Setup)</button><button onClick={hide}>Not now</button></div>
        {err && <p role="alert" className="cfg-error">{err}</p>}
      </> : <p className="cfg-note">Trading operations start strategies.</p>}
    </>}
  </section>;
}
