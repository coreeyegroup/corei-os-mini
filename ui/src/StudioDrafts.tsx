import { useEffect, useRef, useState } from 'react';
import { api } from './Configuration';
import { openGraph } from './calc/ports';
import { goto, setupStrategyFromRule } from './nav';
import { useData } from './StageShell';
import { readWorkflow } from './UniversalCanvas';

export type DraftVersion = {
  draft_id: string; name: string; version: number; created_by: string; created_at: string;
  promoted_graph_code?: string | null; promoted_graph_version?: number | null;
};
type DraftSummary = { id: string; name: string; version: number; created_at: string };
type OpenDraft = { revision: DraftVersion; text: string };
type Props = {
  draft: string;
  opened: OpenDraft | null;
  onOpen: (value: OpenDraft) => void;
  section: 'templates' | 'drafts' | 'review' | null;
  onResume: () => void;
  onBusy: (busy: boolean) => void;
};

export default function StudioDrafts({ draft, opened, onOpen, section, onBusy, onResume }: Props) {
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null);
  const [selected, setSelected] = useState('');
  const [versions, setVersions] = useState<DraftVersion[]>([]);
  const [number, setNumber] = useState(0);
  const [code, setCode] = useState('');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [feedbackStep, setFeedbackStep] = useState<Props['section']>(null);
  const pendingNew = useRef<{ id: string; text: string } | null>(null);
  const workflow = readWorkflow(draft);
  const changed = draft !== opened?.text;
  const activeVersion = opened?.revision;
  const latest = drafts?.find(item => item.id === activeVersion?.draft_id);
  const historical = Boolean(latest && activeVersion && latest.version > activeVersion.version);
  const draftReady = Boolean(workflow?.name?.trim());
  const graphCode = !changed ? activeVersion?.promoted_graph_code : null;
  const graphVersion = activeVersion?.promoted_graph_version;
  const graphPath = section === 'review' && graphCode && graphVersion != null
    ? `calc/graphs/${encodeURIComponent(graphCode)}/versions/${graphVersion}` : null;
  const [graph, graphError, refreshGraph] = useData<{ code: string; version: number; status: string; name: string }>(graphPath);
  const [session] = useData<{ roles?: string[] }>(graphPath ? 'operator/session' : null, 0);
  const reviewedGraph = graph && graph.code === graphCode && graph.version === graphVersion && !graphError ? graph : null;

  function reviewGraph() {
    if (!graphCode) return;
    openGraph(graphCode, graphVersion ?? undefined);
    goto('Strategies', 'graphs');
  }

  async function withRequest(label: string, work: () => Promise<void>, locksDraft = false) {
    setBusy(label);
    setFeedbackStep(section);
    setError('');
    setNotice('');
    if (locksDraft) onBusy(true);
    try {
      await work();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      if (locksDraft) onBusy(false);
      setBusy('');
    }
  }

  async function listDrafts() {
    const result = await api('trading/studio/drafts');
    setDrafts(result.items);
  }

  async function listVersions(id: string, preferred?: number) {
    if (!id) {
      setVersions([]);
      setNumber(0);
      return;
    }
    const result = await api('trading/studio/drafts/' + id + '/versions');
    setVersions(result.items);
    setNumber(preferred ?? result.items[0]?.version ?? 0);
  }

  useEffect(() => {
    if ((section === 'templates' || section === 'drafts') && drafts === null && !busy && !(error && feedbackStep === section)) void withRequest('Loading drafts…', listDrafts);
  }, [section, drafts, busy, error, feedbackStep]);

  async function save(asNew: boolean) {
    if (!workflow || !draftReady || busy) return;
    await withRequest('Saving draft…', async () => {
      let id = activeVersion?.draft_id ?? '';
      if (asNew || !id) {
        if (!pendingNew.current || pendingNew.current.text !== draft) pendingNew.current = { id: crypto.randomUUID(), text: draft };
        id = pendingNew.current.id;
      }
      const revision = await api('trading/studio/drafts/' + id, 'PUT', {
        expected_version: asNew ? 0 : activeVersion?.version ?? 0,
        workflow,
      });
      pendingNew.current = null;
      onOpen({ revision, text: draft });
      setSelected(id);
      setVersions([]);
      setNumber(0);
      setNotice('Saved version ' + revision.version + '.');
      await Promise.all([listDrafts(), listVersions(id, revision.version)]);
    }, true);
  }

  async function openVersion() {
    if (!selected || !number || busy) return;
    if (changed && !window.confirm('Open this saved version and replace edits in the current editor?')) return;
    await withRequest('Opening version…', async () => {
      const revision = await api('trading/studio/drafts/' + selected + '/versions/' + number);
      onOpen({ revision, text: JSON.stringify(revision.workflow, null, 2) });
      setCode('');
      setReason('');
      setNotice('Opened version ' + revision.version + '.');
      onResume();
    }, true);
  }

  async function promote() {
    if (!activeVersion || changed || busy) return;
    await withRequest('Creating calculation graph draft…', async () => {
      const revision = await api('trading/studio/drafts/' + activeVersion.draft_id + '/versions/' +
        activeVersion.version + '/promote', 'POST', { code, reason: reason.trim() });
      onOpen({ revision, text: draft });
      setNotice('Created calculation graph draft ' + revision.promoted_graph_code + '.');
    }, true);
  }

  return <div hidden={!section} className="studio-stack studio-draft-panels">
    {section === feedbackStep && error && <p role="alert" className="cfg-error-text studio-inline-notice">{error}</p>}
    {section === feedbackStep && (busy || notice) && <p role="status" className="cfg-note studio-inline-notice">{busy || notice}</p>}
    <section className="studio-panel" hidden={section !== 'drafts'}>
      <div className="studio-section-title">
        <div>
          <h3>Current draft</h3>
          <p className="cfg-note">{activeVersion
            ? 'Version ' + activeVersion.version + (changed ? ' · Unsaved edits' : ' · Saved')
            : 'Save the current workflow to start a version history.'}</p>
        </div>
        <div className="studio-actions">
          {activeVersion && <button className="primary" disabled={!changed || !draftReady || historical || !!busy}
            onClick={() => void save(false)}>Save new version</button>}
          <button className={activeVersion ? undefined : 'primary'} disabled={!draftReady || !!busy}
            onClick={() => void save(true)}>{activeVersion ? 'Save as new draft' : 'Save draft'}</button>
        </div>
      </div>
      {!draftReady && <p className="cfg-note">Enter a name and valid workflow JSON before saving.</p>}
      {historical && <p className="cfg-note">A newer version exists. Open the latest version or save this workflow as a new draft.</p>}
    </section>
    <section className="studio-panel" hidden={section !== 'templates'}>
      <h3>Open saved version</h3>
      <div className="studio-actions">
        <label>Draft
          <select value={selected} disabled={!!busy} onChange={event => {
            const id = event.target.value;
            setSelected(id);
            setVersions([]);
            setNumber(0);
            void withRequest('Loading versions…', () => listVersions(id));
          }}>
            <option value="">{drafts?.length === 0 ? 'No saved drafts' : 'Select a draft'}</option>
            {drafts?.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.version}</option>)}
          </select>
        </label>
        <label>Version
          <select value={number} disabled={!versions.length || !!busy} onChange={event => setNumber(Number(event.target.value))}>
            {!versions.length && <option value={0}>Select a version</option>}
            {versions.map(item => <option key={item.version} value={item.version}>
              v{item.version} · {new Date(item.created_at).toLocaleString()} · {item.created_by}
            </option>)}
          </select>
        </label>
        <button disabled={!selected || !number || !!busy} onClick={() => void openVersion()}>Open version</button>
        <button disabled={!!busy} onClick={() => void withRequest('Refreshing drafts…', async () => {
          await Promise.all([listDrafts(), listVersions(selected)]);
        })}>Refresh drafts</button>
      </div>
    </section>
    <section className="studio-panel" hidden={section !== 'review'}>
      <h3>{graphCode ? 'Calculation graph' : 'Hand off for approval'}</h3>
      {graphCode ? <>
        <div className="studio-section-title">
          <div>
            <strong>{graphCode} · v{graphVersion}</strong>
            <p className="cfg-note" role="status">{reviewedGraph ? reviewedGraph.status.toLowerCase() : graphError ? 'Approval status unavailable' : 'Checking approval status…'}</p>
          </div>
          <div className="studio-actions">
            {graphError && <button onClick={refreshGraph}>Refresh review status</button>}
            <button className={reviewedGraph?.status === 'APPROVED' ? undefined : 'primary'} onClick={reviewGraph}>Open graph for review</button>
            {reviewedGraph?.status === 'APPROVED' && session?.roles?.includes('TRADING_OPS') &&
              <button className="primary" onClick={() => setupStrategyFromRule(reviewedGraph)}>Use this rule in a strategy</button>}
          </div>
        </div>
        {reviewedGraph?.status === 'DRAFT' && <p className="cfg-note">Open the graph and select Propose to send it for approval.</p>}
        {reviewedGraph?.status === 'PROPOSED' && <p className="cfg-note">A different user with the Risk approver role can approve this revision.</p>}
      </> : activeVersion && !changed ? <form className="studio-form-row" onSubmit={event => {
        event.preventDefault();
        void promote();
      }}>
        <label>Graph code
          <input value={code} disabled={!!busy} onChange={event => setCode(event.target.value.toUpperCase())}
            pattern="[A-Z][A-Z0-9_]{1,59}" maxLength={60} required />
        </label>
        <label>Reason
          <input value={reason} disabled={!!busy} onChange={event => setReason(event.target.value)}
            minLength={5} maxLength={500} required />
        </label>
        <button className="primary" type="submit" disabled={!!busy || reason.trim().length < 5}>Create calculation graph draft</button>
      </form> : <p className="cfg-note">Save the current workflow as a revision before handing it off.</p>}
      <p className="cfg-note">In the strategy rule, warm-up and conflicting conditions map to HOLD. Two false conditions map to FLAT.</p>
    </section>
  </div>;
}
