import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import { goto } from './nav';

type Source = { account_id: string; account_code: string; provider_name: string };
type SymbolRow = {
  broker_symbol: string; display_symbol: string; description: string; asset_class: string;
  exchange: string; suggested_instrument?: string; plan_id?: string; plan_status?: string;
  linked_instrument?: string; linked_instrument_status?: string;
};
type Catalogue = {
  items: SymbolRow[]; total: number; catalogue_size: number;
  facets?: { asset_class: { k: string }[] };
};
const pageSize = 25;
const frames = ['M1', 'M5', 'M15', 'M30', 'H1', 'H4', 'D1'];
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

export default function StudioBrokerCatalogue({ id, add, disabled }: {
  id: string; add: (symbols: string[]) => Promise<void>; disabled: boolean;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [sourceLoading, setSourceLoading] = useState(true);
  const [account, setAccount] = useState('');
  const [query, setQuery] = useState('');
  const [assetClass, setAssetClass] = useState('');
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<Catalogue | null>(null);
  const [picked, setPicked] = useState<SymbolRow[]>([]);
  const [backfill, setBackfill] = useState<SymbolRow | null>(null);
  const [timeframe, setTimeframe] = useState('H1');
  const [from, setFrom] = useState(() => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10));
  const [through, setThrough] = useState(() => new Date().toISOString().slice(0, 10));
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [lookupError, setLookupError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    panel.current?.focus();
    let live = true;
    Promise.all([api('md/sources'), api('operator/session')]).then(([result, session]) => {
      if (!live) return;
      setSources(result.items ?? []);
      setCanEdit((session.roles ?? []).includes('TRADING_OPS'));
    }).catch(error => { if (live) setLookupError(errorText(error)); })
      .finally(() => { if (live) setSourceLoading(false); });
    return () => { live = false; };
  }, []);

  useEffect(() => {
    if (!account) return;
    let live = true;
    setLoading(true);
    setLookupError('');
    const params = new URLSearchParams({ q: query.trim(), asset_class: assetClass, offset: String(offset), limit: String(pageSize) });
    const timer = window.setTimeout(() => {
      api(`md/catalog/${account}?${params}`).then(result => { if (live) setData(result); })
        .catch(error => { if (live) { setData(null); setLookupError(errorText(error)); } })
        .finally(() => { if (live) setLoading(false); });
    }, query ? 180 : 0);
    return () => { live = false; window.clearTimeout(timer); };
  }, [account, query, assetClass, offset, revision]);

  async function act(work: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try { await work(); }
    catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function collect(items: SymbolRow[]) {
    await act(async () => {
      const result = await api(`md/catalog/${account}/collect`, 'POST', {
        items: items.map(row => ({ broker_symbol: row.broker_symbol, instrument: row.suggested_instrument || null })),
        purpose: 'RESEARCH', data_types: ['BARS'], bar_timeframes: ['M1'], link_instruments: true, start: true,
        reason: 'Selected for a Strategy Studio scan',
      });
      const created = (result.created ?? []) as { broker_symbol: string; active_instrument?: string; note?: string }[];
      const names = created.flatMap(row => row.active_instrument ? [row.active_instrument] : []);
      const problems = [
        ...(result.errors ?? []).map((row: { broker_symbol: string; error: string }) => `${row.broker_symbol} · ${row.error}`),
        ...created.flatMap(row => row.note ? [`${row.broker_symbol} · ${row.note}`] : []),
        ...(result.skipped ?? []).map((row: { broker_symbol: string }) => `${row.broker_symbol} already has a plan. Use its refreshed row.`),
      ];
      setPicked(previous => previous.filter(row => !created.some(item => item.broker_symbol === row.broker_symbol)));
      setRevision(value => value + 1);
      setNotice(`${created.length} collection plans created · ${result.started ?? 0} started · ${names.length} selected for the scan. Check readiness after bars arrive or queue a backfill below.`);
      if (problems.length) setError(problems.join(' · '));
      if (names.length) await add(names);
    });
  }

  const locked = disabled || busy;
  const selected = new Set(picked.map(row => row.broker_symbol));
  return <div id={id} ref={panel} className="studio-broker" role="region" aria-label="Broker catalogue" tabIndex={-1}>
    <div className="studio-actions">
      <label>Broker account<select value={account} disabled={locked || sourceLoading} onChange={event => {
        setAccount(event.target.value); setData(null); setOffset(0); setPicked([]); setBackfill(null);
        setAssetClass(''); setNotice(''); setError(''); setLookupError('');
      }}>
        <option value="">{sourceLoading ? 'Loading accounts…' : 'Choose a broker account'}</option>
        {sources.map(source => <option key={source.account_id} value={source.account_id}>{source.provider_name} · {source.account_code}</option>)}
      </select></label>
      {account && <label>Broker symbol<input type="search" aria-label="Search broker symbols" value={query} maxLength={64}
        disabled={locked} onChange={event => { setQuery(event.target.value); setOffset(0); }} /></label>}
      {account && <label>Asset class<select value={assetClass} disabled={locked} onChange={event => { setAssetClass(event.target.value); setOffset(0); }}>
        <option value="">All asset classes</option>
        {(data?.facets?.asset_class ?? []).map(item => <option key={item.k}>{item.k}</option>)}
      </select></label>}
      {account && canEdit && <button disabled={locked} onClick={() => void act(async () => {
        await api(`md/catalog/${account}/refresh`, 'POST', {});
        setOffset(0); setRevision(value => value + 1);
      })}>Refresh broker symbols</button>}
    </div>
    {!sourceLoading && !sources.length && <p className="cfg-note">No broker accounts are available. <button onClick={() => goto('Configuration', 'brokers')}>Open account setup</button></p>}
    {lookupError && <p role="alert" className="cfg-error-text">{lookupError}</p>}
    {error && <p role="alert" className="cfg-error-text">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {loading && <p role="status">Loading broker symbols…</p>}
    {data && !loading && <>
      <div className="studio-actions">
        <span>{data.total.toLocaleString()} matches · {data.catalogue_size.toLocaleString()} broker symbols</span>
        {canEdit && <button disabled={locked || !picked.length} onClick={() => void collect(picked)}>Set up {picked.length} selected</button>}
        {!!picked.length && <button disabled={locked} onClick={() => setPicked([])}>Clear broker selection</button>}
        <button onClick={() => goto('Market data', 'dashboard')}>Collection settings</button>
      </div>
      {canEdit && <p className="cfg-note">Set up and select starts M1 bar collection for research. Backfill history for the strategy timeframe as needed.</p>}
      {picked.length === 500 && <p role="status">500 selected. Set these up before selecting more.</p>}
      <div className="cfg-scroll studio-table" role="region" aria-label="Broker symbol results" tabIndex={0}>
        <table className="cfg-table compact">
          <thead><tr><th scope="col">Symbol</th><th scope="col">Market</th><th scope="col">Data setup</th><th scope="col">Actions</th></tr></thead>
          <tbody>{data.items.map(row => {
            const linked = row.linked_instrument_status === 'ACTIVE' ? row.linked_instrument : undefined;
            return <tr key={row.broker_symbol}>
              <td>{canEdit && !row.plan_id && <input type="checkbox" aria-label={`Select ${row.broker_symbol}`}
                disabled={locked || (picked.length >= 500 && !selected.has(row.broker_symbol))} checked={selected.has(row.broker_symbol)} onChange={event => setPicked(previous =>
                  event.target.checked ? [...previous, row] : previous.filter(item => item.broker_symbol !== row.broker_symbol))} />}
                <strong>{row.display_symbol || row.broker_symbol}</strong><br /><small>{row.description}</small></td>
              <td>{[row.asset_class, row.exchange].filter(Boolean).join(' · ')}</td>
              <td>{linked ? `${linked} · ${row.plan_status ?? 'Mapped'}` : row.plan_id ? 'Plan needs an active COREI instrument' : 'Needs a collection plan'}</td>
              <td><div className="studio-actions">
                {linked && <button disabled={locked} onClick={() => void act(async () => { await add([linked]); setNotice(`${linked} selected for the scan.`); })}>Add to scan</button>}
                {canEdit && !row.plan_id && <button disabled={locked} onClick={() => void collect([row])}>Set up and select</button>}
                {canEdit && linked && row.plan_id && <button disabled={locked} onClick={() => setBackfill(row)}>Backfill bars</button>}
                {row.plan_id && (!linked || row.plan_status !== 'ACTIVE') && <button onClick={() => goto('Market data', 'plans')}>Review plan</button>}
              </div></td>
            </tr>;
          })}
          {!data.items.length && <tr><td colSpan={4}>{data.catalogue_size ? 'No symbols match these filters.' : 'Refresh broker symbols to load this catalogue.'}</td></tr>}
          </tbody>
        </table>
      </div>
      {data.total > pageSize && <div className="studio-actions">
        <button disabled={locked || !offset} onClick={() => setOffset(value => value - pageSize)}>Previous symbols</button>
        <span>{offset + 1}–{Math.min(offset + pageSize, data.total)} of {data.total.toLocaleString()}</span>
        <button disabled={locked || offset + pageSize >= data.total} onClick={() => setOffset(value => value + pageSize)}>Next symbols</button>
      </div>}
    </>}
    {backfill && <form className="studio-actions" aria-label="Broker symbol backfill" onSubmit={event => {
      event.preventDefault();
      void act(async () => {
        await api('md/jobs', 'POST', { plan_id: backfill.plan_id, timeframe,
          range_from: `${from}T00:00:00Z`, range_to: `${through}T00:00:00Z`, reason: 'Prepare history for a Strategy Studio scan' });
        setNotice(`Backfill queued for ${backfill.broker_symbol}. Check readiness after it finishes.`);
        setBackfill(null);
      });
    }}>
      <strong>{backfill.broker_symbol}</strong>
      <label>Timeframe<select value={timeframe} onChange={event => setTimeframe(event.target.value)}>{frames.map(frame => <option key={frame}>{frame}</option>)}</select></label>
      <label>From<input type="date" required value={from} onChange={event => setFrom(event.target.value)} /></label>
      <label>Until (UTC)<input type="date" required value={through} onChange={event => setThrough(event.target.value)} /></label>
      <button className="primary" disabled={locked || !from || !through || from >= through}>Queue backfill</button>
      <button type="button" onClick={() => setBackfill(null)}>Close</button>
      <button type="button" onClick={() => goto('Market data', 'backfill')}>Backfill progress</button>
    </form>}
  </div>;
}
