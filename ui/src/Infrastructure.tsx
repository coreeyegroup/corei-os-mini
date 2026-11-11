/* Infrastructure: health of everything COREI runs on, and a spreadsheet-style PostgreSQL
 * view and editor. Health is open to every operator; the database is for administrators.
 * Every change asks for a reason and (within the policy window) the password again, and is
 * logged before and after. Secret columns are never shown or editable. */
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { addReauthHandler, api, Field } from './Configuration';
import { ReauthDialog } from './ConfigControls';
import { useSection } from './nav';
import './configuration.css';
import './infrastructure.css';

type Row = Record<string, any>;
type Session = { username: string; roles: string[] };
type Tab = 'health' | 'tables' | 'history' | 'sql' | 'database' | 'edits';
const TABS: { id: Tab; label: string; admin?: boolean; hint: string }[] = [
  { id: 'health', label: 'Health', hint: 'Database, services, host, clock, network and broker tools.' },
  { id: 'tables', label: 'Tables', admin: true, hint: 'View, filter, sort and edit PostgreSQL tables like a spreadsheet.' },
  { id: 'history', label: 'History', admin: true, hint: 'Archived market data (Parquet files with fingerprints), read-only with DuckDB.' },
  { id: 'sql', label: 'SQL console', admin: true, hint: 'Read-only queries: one statement, 5-second limit.' },
  { id: 'database', label: 'Database', admin: true, hint: 'Server facts and live sessions.' },
  { id: 'edits', label: 'Edit log', admin: true, hint: 'Every row changed from this console, before and after. Cannot be edited.' },
];
const when = (v?: string | null) => v ? new Date(v).toLocaleString() : '—';
const bytes = (n: number) => n > 1e9 ? `${(n / 1e9).toFixed(1)} GB` : n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1e3))} kB`;

export default function Infrastructure() {
  const [user, setUser] = useState<Session | null | undefined>(undefined), [tab, setTab] = useSection<Tab>('Infrastructure', 'health', TABS.map(t => t.id));
  const [reauth, setReauth] = useState<((ok: boolean) => void) | null>(null);
  useEffect(() => addReauthHandler(() => new Promise<boolean>(resolve => setReauth(() => resolve))), []);
  useEffect(() => {
    const check = () => void api('operator/session').then(s => setUser(s?.username ? s : null)).catch(() => setUser(null));
    check();
    window.addEventListener('corei-session', check);
    return () => window.removeEventListener('corei-session', check);
  }, []);
  if (user === undefined) return <div className="content"><p>Checking sign-in…</p></div>;
  if (user === null) return <SignIn done={setUser} />;
  const admin = user.roles.includes('ADMIN');
  const tabs = TABS.filter(t => !t.admin || admin), current = tabs.find(t => t.id === tab) ?? tabs[0];
  return <div className="cfg infra">
    <div className="cfg-topbar"><span className="eyebrow">INFRASTRUCTURE</span>
      <nav className="cfg-nav" aria-label="Infrastructure sections">{tabs.map(t =>
        <button key={t.id} aria-current={current.id === t.id ? 'page' : undefined} onClick={() => setTab(t.id)}><span>{t.label}</span></button>)}</nav>
      <div className="cfg-user">{user.username}</div></div>
    <section className="cfg-main">
      <header className="cfg-head"><h1>{current.label}</h1><p>{current.hint}</p></header>
      {!admin && <p className="cfg-note">The database views are for administrators.</p>}
      {current.id === 'health' ? <div className="cfg-pane"><Health /></div>
        : current.id === 'tables' ? <Tables />
        : current.id === 'history' ? <div className="cfg-pane"><History /></div>
        : current.id === 'sql' ? <div className="cfg-pane"><Console /></div>
        : current.id === 'database' ? <div className="cfg-pane"><Database /></div>
        : <div className="cfg-pane"><Edits /></div>}
    </section>
    {reauth && <ReauthDialog done={ok => { reauth(ok); setReauth(null); }} />}
  </div>;
}

export function SignIn({ done, title = "INFRASTRUCTURE" }: { done: (s: Session) => void; title?: string }) {
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState('');
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    try { done(await api('operator/login', 'POST', { username, password })); } catch (err) { setError((err as Error).message); }
  }
  return <div className="content cfg-signin"><div className="eyebrow">{title}</div><h1>Sign in</h1>
    <form className="cfg-form" onSubmit={submit}><Field label="Username"><input required autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></Field>
      <Field label="Password"><input required type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></Field>
      {error && <p role="alert" className="cfg-error">{error}</p>}<div className="cfg-actions"><button className="primary">Sign in</button></div></form></div>;
}

/* ---------- health ---------- */

export function Health() {
  const [report, setReport] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const load = async () => { setBusy(true); try { setReport(await api('infra/health')); setError(''); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };
  useEffect(() => { void load(); const t = window.setInterval(() => void load(), 30_000); return () => window.clearInterval(t); }, []);
  const groups = useMemo(() => {
    const out: Record<string, Row[]> = {};
    for (const c of report?.checks ?? []) (out[c.group] ??= []).push(c);
    return out;
  }, [report]);
  return <>
    <div className="infra-bar"><span className={`infra-dot ${report?.status ?? ''}`} />
      <strong>{report ? ({ OK: 'All checks pass', WARN: 'Some checks need attention', FAIL: 'Something is failing' } as Row)[report.status] : 'Checking…'}</strong>
      <small>{report && `checked ${when(report.generated_at)} · refreshes every 30 s`}</small>
      <button type="button" disabled={busy} onClick={() => void load()}>{busy ? 'Checking…' : 'Check now'}</button></div>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    <div className="infra-groups">{Object.entries(groups).map(([group, checks]) => <section key={group} className="cfg-panel">
      <header><strong>{group}</strong></header>
      <ul className="infra-checks">{checks.map(c => <li key={c.name}><span className={`infra-dot ${c.status}`} title={c.status} />
        <span className="infra-check-name">{c.name}</span><span className="infra-check-detail">{c.detail}</span></li>)}</ul>
    </section>)}</div>
    <p className="cfg-note">Services are shown, not restarted, from here: the web service runs without root rights. To restart, on the server run <code>sudo systemctl restart corei-os-mini-workstation</code> (or <code>caddy</code>, <code>postgresql</code>).</p>
  </>;
}

/* ---------- tables (spreadsheet) ---------- */

type Table = { name: string; rows: number; bytes: number; mode: 'EDIT' | 'READ_ONLY'; why: string; group: string };
type Column = { name: string; type: string; nullable: boolean; default: string | null; generated: boolean; key: boolean; secret: boolean };
type Page = { table: string; mode: string; why: string; columns: Column[]; key: string[]; total: number; offset: number; limit: number; rows: Row[] };
type Cell = { r: number; c: number };
const NEW = -1;

function Tables() {
  const [tables, setTables] = useState<Table[] | null>(null), [find, setFind] = useState(''), [name, setName] = useState<string | null>(null);
  const [error, setError] = useState('');
  const load = async () => { try { setTables((await api('infra/database')).tables); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  const shown = (tables ?? []).filter(t => t.name.includes(find.toLowerCase()));
  const groups = [...new Set(shown.map(t => t.group))];
  return <div className="infra-tables">
    <aside className="infra-list" aria-label="Tables">
      <input className="cfg-search" aria-label="Find table" placeholder={`Find among ${tables?.length ?? 0} tables`} value={find} onChange={e => setFind(e.target.value)} />
      {groups.map(g => <div key={g}><div className="infra-list-group">{g}</div>
        {shown.filter(t => t.group === g).map(t => <button key={t.name} aria-current={name === t.name ? 'true' : undefined} title={t.why} onClick={() => setName(t.name)}>
          <span>{t.mode === 'READ_ONLY' ? '🔒 ' : ''}{t.name}</span><small>{t.rows.toLocaleString()} · {bytes(t.bytes)}</small></button>)}</div>)}
      {error && <p role="alert" className="cfg-error">{error}</p>}
    </aside>
    {name ? <Sheet key={name} table={name} changed={() => void load()} /> : <div className="infra-empty"><p>Choose a table on the left.</p>
      <p className="cfg-note">🔒 tables are read-only here: logs and secrets always; configuration tables while the policy <em>infra_edit_controlled_tables</em> is off, so changes keep going through four-eyes and readiness checks on the Config screens.</p></div>}
  </div>;
}

function Sheet({ table, changed }: { table: string; changed: () => void }) {
  const [page, setPage] = useState<Page | null>(null), [offset, setOffset] = useState(0), [limit, setLimit] = useState(100);
  const [sort, setSort] = useState<{ col: string; desc: boolean } | null>(null), [filters, setFilters] = useState<Record<string, string>>({}), [q, setQ] = useState('');
  const [sel, setSel] = useState<Cell | null>(null), [editing, setEditing] = useState<{ cell: Cell; value: string } | null>(null);
  const [pending, setPending] = useState<Record<number, Record<string, string | null>>>({}), [newRow, setNewRow] = useState<Record<string, string> | null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState(''), [saving, setSaving] = useState<null | 'save' | 'insert' | 'delete'>(null);
  const [reason, setReason] = useState(''), [busy, setBusy] = useState(false);
  const grid = useRef<HTMLDivElement>(null);
  const dirty = Object.keys(pending).length > 0 || newRow !== null;

  async function load(next = offset) {
    setBusy(true);
    const params = new URLSearchParams({ offset: String(next), limit: String(limit) });
    if (sort) { params.set('sort', sort.col); params.set('desc', String(sort.desc)); }
    if (q) params.set('q', q);
    for (const [k, v] of Object.entries(filters)) if (v) params.set('f.' + k, v);
    try { setPage(await api(`infra/tables/${table}?${params}`)); setOffset(next); setError(''); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  useEffect(() => { const t = window.setTimeout(() => void load(0), 250); return () => window.clearTimeout(t); }, [sort, filters, q, limit]);
  // Unsaved cells stop the layout from switching away silently.
  useEffect(() => {
    if (!dirty) return;
    const guard = (e: Event) => { if (!window.confirm('Discard unsaved changes in ' + table + '?')) e.preventDefault(); };
    window.addEventListener('corei-before-layout-change', guard); return () => window.removeEventListener('corei-before-layout-change', guard);
  }, [dirty, table]);

  const cols = page?.columns ?? [], rows = page?.rows ?? [], editable = page?.mode === 'EDIT';
  const value = (r: number, c: Column) => r in pending && c.name in pending[r] ? pending[r][c.name] : rows[r]?.[c.name];
  const canEditCell = (c: Column) => editable && !c.secret && !c.generated;

  function commit(cell: Cell, text: string | null) {
    const c = cols[cell.c];
    if (cell.r === NEW) { setNewRow({ ...(newRow ?? {}), [c.name]: text ?? '' }); return; }
    const original = rows[cell.r][c.name];
    const next = { ...(pending[cell.r] ?? {}) };
    if (text === original) delete next[c.name]; else next[c.name] = text;
    const all = { ...pending };
    if (Object.keys(next).length) all[cell.r] = next; else delete all[cell.r];
    setPending(all);
  }
  function startEdit(cell: Cell, initial?: string) {
    const c = cols[cell.c];
    if (!c || (cell.r !== NEW && !canEditCell(c))) return;
    const current = cell.r === NEW ? newRow?.[c.name] ?? '' : value(cell.r, c);
    setEditing({ cell, value: initial ?? (current ?? '') });
  }
  function move(dr: number, dc: number) {
    if (!sel) { setSel({ r: 0, c: 0 }); return; }
    setSel({ r: Math.max(0, Math.min(rows.length - 1, sel.r + dr)), c: Math.max(0, Math.min(cols.length - 1, sel.c + dc)) });
  }
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (editing) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); move(1, 0); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1, 0); }
    else if (e.key === 'ArrowRight' || (e.key === 'Tab' && !e.shiftKey)) { e.preventDefault(); move(0, 1); }
    else if (e.key === 'ArrowLeft' || (e.key === 'Tab' && e.shiftKey)) { e.preventDefault(); move(0, -1); }
    else if (sel && (e.key === 'Enter' || e.key === 'F2')) { e.preventDefault(); startEdit(sel); }
    else if (sel && (e.key === 'Delete' || e.key === 'Backspace') && sel.r !== NEW && canEditCell(cols[sel.c])) { e.preventDefault(); commit(sel, cols[sel.c].nullable ? null : ''); }
    else if (sel && (e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') { const v = sel.r === NEW ? newRow?.[cols[sel.c].name] : value(sel.r, cols[sel.c]); void navigator.clipboard?.writeText(v ?? ''); }
    else if (sel && e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); startEdit(sel, e.key); }
  }
  function finishEdit(save: boolean, step?: [number, number]) {
    if (!editing) return;
    if (save) commit(editing.cell, editing.value);
    setEditing(null);
    if (step && editing.cell.r !== NEW) { setSel(editing.cell); window.setTimeout(() => move(step[0], step[1]), 0); }
    grid.current?.focus();
  }

  async function run() {
    if (!page) return;
    setError(''); setNotice('');
    try {
      if (saving === 'save') {
        let n = 0;
        for (const [r, changes] of Object.entries(pending)) {
          const row = rows[Number(r)];
          const key = Object.fromEntries(page.key.map(k => [k, row[k]]));
          const original = Object.fromEntries(Object.keys(changes).map(k => [k, row[k]]));
          await api(`infra/tables/${table}/rows`, 'PATCH', { key, changes, original, reason });
          n++;
        }
        setPending({}); setNotice(`${n} row${n === 1 ? '' : 's'} saved and logged.`);
      } else if (saving === 'insert' && newRow) {
        await api(`infra/tables/${table}/rows`, 'POST', { values: newRow, reason });
        setNewRow(null); setNotice('Row added and logged.');
      } else if (saving === 'delete' && sel && sel.r !== NEW) {
        const row = rows[sel.r];
        await api(`infra/tables/${table}/rows/delete`, 'POST', { key: Object.fromEntries(page.key.map(k => [k, row[k]])), reason });
        setSel(null); setNotice('Row deleted and logged.');
      }
      setSaving(null); setReason(''); await load(); changed();
    } catch (e) { setError((e as Error).message); setSaving(null); await load(); }
  }

  function exportCsv() {
    const esc = (v: unknown) => { const s = v == null ? '' : String(v); const safe = /^[=+\-@\t\r]/.test(s) ? "'" + s : s; return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe; };
    const text = [cols.map(c => esc(c.name)).join(','), ...rows.map(r => cols.map(c => esc(r[c.name])).join(','))].join('\n');
    const link = document.createElement('a'); link.href = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
    link.download = `${table}-${offset + 1}-${offset + rows.length}.csv`; link.click(); URL.revokeObjectURL(link.href);
  }

  const selCol = sel ? cols[sel.c] : null;
  const selValue = sel && selCol ? (sel.r === NEW ? newRow?.[selCol.name] ?? '' : value(sel.r, selCol)) : null;
  const changes = Object.values(pending).reduce((n, r) => n + Object.keys(r).length, 0);
  return <div className="infra-sheet">
    <div className="infra-toolbar">
      <strong>{table}</strong>
      <span className={`cfg-pill ${editable ? 'ok' : 'off'}`} title={page?.why}>{editable ? 'Editable' : 'Read-only'}</span>
      <input className="cfg-search" aria-label="Search all columns" placeholder="Search all columns" value={q} onChange={e => setQ(e.target.value)} />
      {Object.values(filters).some(Boolean) && <button type="button" onClick={() => setFilters({})}>Clear filters</button>}
      <span className="infra-spacer" />
      {editable && <button type="button" disabled={newRow !== null} onClick={() => { setNewRow({}); setSel({ r: NEW, c: 0 }); }}>+ Row</button>}
      {editable && sel && sel.r !== NEW && <button type="button" className="danger" onClick={() => setSaving('delete')}>Delete row</button>}
      <button type="button" onClick={exportCsv} disabled={!rows.length}>Export CSV</button>
      <button type="button" onClick={() => void load()} disabled={busy}>{busy ? 'Loading…' : 'Reload'}</button>
    </div>
    {page && !editable && <p className="cfg-note infra-why">🔒 {page.why}</p>}
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {notice && <p role="status" className="cfg-ok">{notice}</p>}
    <div className="infra-grid" ref={grid} tabIndex={0} onKeyDown={onKey} role="grid" aria-label={`${table} rows`} aria-rowcount={page?.total}>
      <table>
        <thead>
          <tr><th className="infra-rownum">#</th>{cols.map(c => <th key={c.name} title={`${c.type}${c.nullable ? '' : ' · required'}${c.default ? ' · default ' + c.default : ''}`}
            aria-sort={sort?.col === c.name ? (sort.desc ? 'descending' : 'ascending') : undefined}>
            <button type="button" disabled={c.secret} onClick={() => setSort(sort?.col === c.name ? (sort.desc ? null : { col: c.name, desc: true }) : { col: c.name, desc: false })}>
              {c.key ? '🔑 ' : c.secret ? '🔒 ' : ''}{c.name}{sort?.col === c.name ? (sort.desc ? ' ▼' : ' ▲') : ''}</button>
            <small>{c.type}</small></th>)}</tr>
          <tr className="infra-filters"><th />{cols.map(c => <th key={c.name}>{!c.secret && <input aria-label={`Filter ${c.name}`} placeholder="filter"
            value={filters[c.name] ?? ''} onChange={e => setFilters({ ...filters, [c.name]: e.target.value })} />}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, r) => <tr key={page!.key.map(k => row[k]).join('|') || r} className={r in pending ? 'infra-dirty' : ''}>
            <td className="infra-rownum">{offset + r + 1}</td>
            {cols.map((c, ci) => {
              const isSel = sel?.r === r && sel.c === ci, v = value(r, c), changedCell = r in pending && c.name in pending[r];
              return <td key={c.name} role="gridcell" aria-selected={isSel} className={`${isSel ? 'infra-sel' : ''} ${changedCell ? 'infra-changed' : ''} ${v == null ? 'infra-null' : ''} ${!canEditCell(c) ? 'infra-locked' : ''}`}
                title={v ?? 'NULL'} onClick={() => setSel({ r, c: ci })} onDoubleClick={() => startEdit({ r, c: ci })}>
                {editing && editing.cell.r === r && editing.cell.c === ci
                  ? <input className="infra-cell-input" autoFocus aria-label={`Edit ${c.name}`} value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })}
                      onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); finishEdit(true, [1, 0]); } else if (e.key === 'Escape') finishEdit(false); else if (e.key === 'Tab') { e.preventDefault(); finishEdit(true, [0, e.shiftKey ? -1 : 1]); } }}
                      onBlur={() => finishEdit(true)} />
                  : v == null ? 'NULL' : v}</td>;
            })}</tr>)}
          {newRow && <tr className="infra-new"><td className="infra-rownum">new</td>{cols.map((c, ci) => {
            const isSel = sel?.r === NEW && sel.c === ci;
            return <td key={c.name} className={`${isSel ? 'infra-sel' : ''} ${c.secret || c.generated ? 'infra-locked' : ''}`} onClick={() => setSel({ r: NEW, c: ci })}
              onDoubleClick={() => !c.secret && !c.generated && startEdit({ r: NEW, c: ci })}>
              {editing && editing.cell.r === NEW && editing.cell.c === ci
                ? <input className="infra-cell-input" autoFocus aria-label={`New ${c.name}`} value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })}
                    onKeyDown={e => { if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); finishEdit(true); setSel({ r: NEW, c: Math.min(cols.length - 1, ci + 1) }); } else if (e.key === 'Escape') finishEdit(false); }}
                    onBlur={() => finishEdit(true)} />
                : newRow[c.name] || <span className="infra-placeholder">{c.default ? 'default' : c.nullable ? '' : 'required'}</span>}</td>;
          })}</tr>}
          {page && rows.length === 0 && !newRow && <tr><td colSpan={cols.length + 1} className="infra-placeholder">No rows{Object.values(filters).some(Boolean) || q ? ' match the filters' : ''}.</td></tr>}
        </tbody>
      </table>
    </div>
    <div className="infra-footer">
      {sel && selCol && <div className="infra-inspector">
        <span><strong>{selCol.name}</strong> <small>{selCol.type}{selCol.key ? ' · key' : ''}{selCol.secret ? ' · secret, never shown' : ''}</small></span>
        <textarea aria-label="Cell value" readOnly={!(sel.r === NEW ? !selCol.secret && !selCol.generated : canEditCell(selCol))} value={selValue ?? ''} placeholder={selValue == null ? 'NULL' : ''}
          onChange={e => commit(sel, e.target.value)} rows={/^(json|jsonb)$/.test(selCol.type) || (selValue ?? '').length > 80 ? 4 : 1} />
        {sel.r !== NEW && canEditCell(selCol) && selCol.nullable && <button type="button" onClick={() => commit(sel, null)}>Set NULL</button>}
        {sel.r !== NEW && rows[sel.r] && pending[sel.r]?.[selCol.name] !== undefined && <button type="button" onClick={() => commit(sel, rows[sel.r][selCol.name])}>Undo cell</button>}
      </div>}
      <div className="infra-pager">
        <span>{page ? `${page.total === 0 ? 0 : offset + 1}–${offset + rows.length} of ${page.total.toLocaleString()}` : ''}</span>
        <button type="button" disabled={offset === 0 || dirty} onClick={() => void load(Math.max(0, offset - limit))}>‹ Prev</button>
        <button type="button" disabled={!page || offset + rows.length >= page.total || dirty} onClick={() => void load(offset + limit)}>Next ›</button>
        <select aria-label="Rows per page" value={limit} onChange={e => setLimit(Number(e.target.value))}>{[50, 100, 250, 500].map(n => <option key={n} value={n}>{n} rows</option>)}</select>
        <span className="infra-spacer" />
        {dirty && <>
          <span className="infra-dirty-count">{changes ? `${changes} unsaved change${changes === 1 ? '' : 's'}` : ''}{newRow ? `${changes ? ' · ' : ''}new row` : ''}</span>
          <button type="button" onClick={() => { setPending({}); setNewRow(null); }}>Discard</button>
          {changes > 0 && <button type="button" className="primary" onClick={() => setSaving('save')}>Save changes…</button>}
          {newRow && <button type="button" className="primary" onClick={() => setSaving('insert')}>Add row…</button>}
        </>}
      </div>
    </div>
    {saving && <div className="cfg-modal" role="dialog" aria-modal="true" aria-label="Reason for this change">
      <form className="cfg-form cfg-modal-box" onSubmit={e => { e.preventDefault(); void run(); }}>
        <h3>{saving === 'save' ? `Save ${changes} change${changes === 1 ? '' : 's'} to ${table}` : saving === 'insert' ? `Add a row to ${table}` : `Delete this row from ${table}`}</h3>
        <p className="cfg-note">Written straight to the database and logged with your name, the values before and after, and this reason.{saving === 'delete' ? ' Rows other records depend on cannot be deleted.' : ''}</p>
        <Field label="Reason"><textarea aria-label="Reason" required minLength={3} maxLength={500} autoFocus value={reason} onChange={e => setReason(e.target.value)} /></Field>
        <div className="cfg-actions"><button className={saving === 'delete' ? 'danger' : 'primary'}>{saving === 'delete' ? 'Delete' : 'Save'}</button>
          <button type="button" onClick={() => setSaving(null)}>Cancel</button></div>
      </form></div>}
  </div>;
}

/* ---------- history (Parquet via DuckDB) ---------- */

function History() {
  const [files, setFiles] = useState<Row[] | null>(null), [error, setError] = useState('');
  useEffect(() => { void api('infra/history/files').then(d => setFiles(d.items)).catch(e => setError((e as Error).message)); }, []);
  const total = (files ?? []).reduce((n, f) => n + Number(f.bytes || 0), 0);
  return <>
    <Console path="infra/history/query" storageKey="corei-os-mini.infra.history-sql" initial={"SELECT account, broker_symbol, day, count(*) AS prices, min(bid) AS low, max(bid) AS high\nFROM ticks GROUP BY ALL ORDER BY day DESC LIMIT 100"}
      note="Read-only DuckDB · views ticks and bars (with day and account from the folders) · 10 s limit · only the archive folder is readable." />
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {files && <section className="cfg-panel"><header><strong>Files ({files.length}, {bytes(total)})</strong></header>
      <table className="cfg-table compact"><thead><tr><th>Day</th><th>Kind</th><th>Account</th><th>Symbol</th><th>Rows</th><th>Size</th><th>Fingerprint</th><th>Check</th></tr></thead>
        <tbody>{files.map(f => <tr key={f.id}><td>{f.day}</td><td>{f.kind.toLowerCase()}</td><td>{f.account_code ?? '—'}</td><td>{f.broker_symbol ?? '—'}</td>
          <td>{f.rows != null ? Number(f.rows).toLocaleString() : '—'}</td><td>{bytes(Number(f.bytes))}</td><td title={f.sha256}><code>{String(f.sha256).slice(0, 12)}…</code></td>
          <td>{f.verify_status.toLowerCase()} · {when(f.verified_at)}</td></tr>)}
          {!files.length && <tr><td colSpan={8} className="infra-placeholder">Nothing archived yet.</td></tr>}</tbody></table></section>}
  </>;
}

/* ---------- SQL console ---------- */

function Console({ path = 'infra/query', storageKey = 'corei-os-mini.infra.sql', initial = 'SELECT * FROM cfg_accounts LIMIT 50',
  note = 'Read-only transaction · 5 s limit · up to 1000 rows · secret columns refused. Change data in Tables.' }:
  { path?: string; storageKey?: string; initial?: string; note?: string }) {
  const [text, setText] = useState(() => { try { return localStorage.getItem(storageKey) || initial; } catch { return initial; } });
  const [result, setResult] = useState<Row | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function run(e?: FormEvent) {
    e?.preventDefault(); setBusy(true); setError('');
    try { localStorage.setItem(storageKey, text); } catch { /* convenience only */ }
    try { setResult(await api(path, 'POST', { sql: text })); } catch (err) { setError((err as Error).message); setResult(null); } finally { setBusy(false); }
  }
  return <>
    <form className="infra-console" onSubmit={run}>
      <textarea aria-label="SQL" spellCheck={false} value={text} onChange={e => setText(e.target.value)}
        onKeyDown={e => { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); void run(); } }} />
      <div className="cfg-actions"><button className="primary" disabled={busy}>{busy ? 'Running…' : 'Run (Ctrl+Enter)'}</button>
        <span className="cfg-note">{note}</span></div>
    </form>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {result && <><p className="cfg-note">{result.rows.length} row{result.rows.length === 1 ? '' : 's'}{result.truncated ? ' (first 1000)' : ''} · {result.ms} ms</p>
      <div className="infra-grid"><table><thead><tr><th className="infra-rownum">#</th>{result.columns.map((c: string) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{result.rows.map((r: Row, i: number) => <tr key={i}><td className="infra-rownum">{i + 1}</td>{result.columns.map((c: string) =>
          <td key={c} className={r[c] == null ? 'infra-null' : ''} title={r[c] == null ? 'NULL' : String(typeof r[c] === 'object' ? JSON.stringify(r[c]) : r[c])}>
            {r[c] == null ? 'NULL' : typeof r[c] === 'object' ? JSON.stringify(r[c]) : String(r[c])}</td>)}</tr>)}</tbody></table></div></>}
  </>;
}

/* ---------- database ---------- */

function Database() {
  const [data, setData] = useState<Row | null>(null), [error, setError] = useState(''), [cancel, setCancel] = useState<Row | null>(null), [reason, setReason] = useState('');
  const load = async () => { try { setData(await api('infra/database')); } catch (e) { setError((e as Error).message); } };
  useEffect(() => { void load(); }, []);
  async function stop(e: FormEvent) {
    e.preventDefault();
    try { await api(`infra/database/sessions/${cancel!.pid}/cancel`, 'POST', { reason }); setCancel(null); setReason(''); await load(); } catch (err) { setError((err as Error).message); }
  }
  const s = data?.server;
  return <>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {s && <section className="cfg-panel"><header><strong>Server</strong><button type="button" onClick={() => void load()}>Reload</button></header>
      <dl className="cfg-facts">{([['Database', s.database], ['Role', s.role], ['Schema', s.schema], ['Version', s.version], ['Size', s.size], ['Time zone', s.timezone], ['Started', when(s.started_at)]] as const)
        .map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></section>}
    {data && <section className="cfg-panel"><header><strong>Live sessions ({data.sessions.length})</strong></header>
      <table className="cfg-table compact"><thead><tr><th>PID</th><th>Role</th><th>Application</th><th>State</th><th>Running</th><th>Query</th><th /></tr></thead>
        <tbody>{data.sessions.map((a: Row) => <tr key={a.pid}><td>{a.pid}</td><td>{a.role}</td><td>{a.application_name || '—'}</td><td>{a.state || '—'}</td><td>{a.running_for || '—'}</td>
          <td className="infra-query" title={a.query}>{a.query}</td><td>{a.state === 'active' && <button type="button" onClick={() => setCancel(a)}>Cancel query</button>}</td></tr>)}</tbody></table></section>}
    {data && <section className="cfg-panel"><header><strong>Tables ({data.tables.length})</strong></header>
      <table className="cfg-table compact"><thead><tr><th>Table</th><th>Rows</th><th>Size</th><th>Here</th><th>Last auto-vacuum</th></tr></thead>
        <tbody>{data.tables.map((t: Row) => <tr key={t.name}><td>{t.name}</td><td>{t.rows.toLocaleString()}</td><td>{bytes(t.bytes)}</td><td title={t.why}>{t.mode === 'EDIT' ? 'Editable' : 'Read-only'}</td><td>{when(t.last_autovacuum)}</td></tr>)}</tbody></table></section>}
    {cancel && <div className="cfg-modal" role="dialog" aria-modal="true" aria-label="Cancel query"><form className="cfg-form cfg-modal-box" onSubmit={stop}>
      <h3>Cancel the running query of session {cancel.pid}?</h3><p className="cfg-note infra-query">{cancel.query}</p>
      <Field label="Reason"><input aria-label="Cancel reason" required minLength={3} autoFocus value={reason} onChange={e => setReason(e.target.value)} /></Field>
      <div className="cfg-actions"><button className="danger">Cancel query</button><button type="button" onClick={() => setCancel(null)}>Keep running</button></div></form></div>}
  </>;
}

/* ---------- edit log ---------- */

function Edits() {
  const [items, setItems] = useState<Row[] | null>(null), [error, setError] = useState('');
  useEffect(() => { void api('infra/edits').then(d => setItems(d.items)).catch(e => setError((e as Error).message)); }, []);
  const show = (side: Row | null, k: string) => !side ? '∅' : side[k] == null ? 'NULL' : typeof side[k] === 'string' ? side[k] : JSON.stringify(side[k]);
  const diff = (e: Row) => {
    const keys = [...new Set([...Object.keys(e.before ?? {}), ...Object.keys(e.after ?? {})])];
    return keys.filter(k => JSON.stringify(e.before?.[k]) !== JSON.stringify(e.after?.[k]))
      .map(k => `${k}: ${show(e.before, k)} → ${show(e.after, k)}`);
  };
  return <>
    {error && <p role="alert" className="cfg-error">{error}</p>}
    {items && (items.length ? <table className="cfg-table compact"><thead><tr><th>Time</th><th>Who</th><th>Table</th><th>Action</th><th>Row</th><th>Change</th><th>Reason</th></tr></thead>
      <tbody>{items.map(e => <tr key={e.id}><td>{when(e.edited_at)}</td><td>{e.actor}</td><td>{e.table_name}</td><td>{e.action}</td><td>{Object.values(e.row_key ?? {}).join(', ')}</td>
        <td className="infra-diff">{diff(e).map(d => <div key={d}>{d}</div>)}</td><td>{e.reason}</td></tr>)}</tbody></table> : <p className="cfg-note">No edits yet.</p>)}
  </>;
}
