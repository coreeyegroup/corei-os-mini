import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import { createResearchBrief, type BriefInput } from './researchBriefFormat';

interface Props {
  accountId: string;
  brokerSymbol: string;
  symbol: string;
  provider?: string;
  timeframe: string;
}

export default function ResearchBrief({ accountId, symbol, brokerSymbol, provider, timeframe }: Props) {
  const [brief, setBrief] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);

  async function generate() {
    setBusy(true);
    setError('');
    setStatus('');
    setBrief('');
    try {
      const [instrument, screen] = await Promise.all([
        api(`intel/instrument/${encodeURIComponent(accountId)}/${encodeURIComponent(brokerSymbol)}?timeframe=${timeframe}&limit=10`),
        api(`intel/screener?timeframe=${timeframe}`),
      ]);
      if (!active.current) return;
      const snapshots: BriefInput['snapshots'] = instrument.snapshots;
      if (!snapshots?.length) throw new Error('No closed-bar snapshots are available for this instrument and source.');
      const row = screen.items?.find((item: { account_id: string; broker_symbol: string }) =>
        item.account_id === accountId && item.broker_symbol === brokerSymbol);
      setBrief(createResearchBrief({ symbol, brokerSymbol, provider, timeframe, capturedAt: new Date(), snapshots,
        context: row ? { market: row, session: screen.session?.label } : undefined }));
      setStatus('Research brief ready.');
    } catch (e) {
      if (active.current) setError((e as Error).message);
    } finally {
      if (active.current) setBusy(false);
    }
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(brief);
      setStatus('Copied. Paste the brief into CORE-AI Chat for an optional review.');
    } catch {
      setStatus('Clipboard unavailable. Select the brief text or download the Markdown file.');
    }
  }

  function download() {
    const a = document.createElement('a');
    const url = URL.createObjectURL(new Blob([brief], { type: 'text/markdown;charset=utf-8' }));
    a.href = url;
    a.download = `${symbol.replace(/[^a-zA-Z0-9_-]/g, '_')}-${timeframe}-research.md`;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  return <details className="intel-research-brief">
    <summary>Research brief</summary>
    <p className="cfg-note">Capture closed-bar features, the latest reported quote, and calendar context for this source. Review here or copy to CORE-AI.</p>
    <div className="intel-bar">
      <button type="button" disabled={busy} onClick={() => void generate()}>{busy ? 'Loading snapshot…' : brief ? 'Refresh research brief' : 'Generate research brief'}</button>
      {brief && <>
        <button type="button" onClick={() => void copy()}>Copy for CORE-AI</button>
        <button type="button" onClick={download}>Download Markdown</button>
      </>}
    </div>
    {error && <p className="cfg-error" role="alert">{error}</p>}
    {status && <p className="cfg-note" role="status">{status}</p>}
    {brief && <textarea aria-label="Research brief preview" readOnly value={brief} rows={14} />}
  </details>;
}
