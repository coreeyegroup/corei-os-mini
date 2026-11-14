/*
 * Setup › Administration › Database: every table of COREI's database. Administrators add, edit and delete rows;
 * add, rename, retype and drop columns; create and drop tables. Every change needs a reason (kept in the audit log)
 * and the password re-entered recently. Secrets (encrypted logins, password and session hashes) stay hidden and are
 * changed from their own screens. API: /api/admin/db.
 */
import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { api } from './Configuration';

type Row = Record<string, any>;
type Col = { name: string; data_type: string; udt_name: string; nullable: boolean; default: string | null; primary_key: boolean; secret: boolean };
const PAGE_SIZES = [50, 100, 250, 500, 1000];

const show = (v: any) => v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);
const asInput = (v: any) => v === null || v === undefined ? '' : Array.isArray(v) ? `{${v.map(x => JSON.stringify(x)).join(',')}}` : typeof v === 'object' ? JSON.stringify(v) : String(v);

export default function DatabaseEditor() {
  const [tables, setTables] = useState<Row[]>([]), [types, setTypes] = useState<string[]>([]);
  const [filter, setFilter] = useState(''), [table, setTable] = useState('');
  const [data, setData] = useState<{ columns: Col[]; rows: Row[]; total: number } | null>(null);
  const [q, setQ] = useState(''), [order, setOrder] = useState(''), [desc, setDesc] = useState(false), [limit, setLimit] = useState(100), [offset, setOffset] = useState(0);
  const [reason, setReason] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<{ row: number; col: string; value: string } | null>(null);
  const [adding, setAdding] = useState<Row | null>(null), [colForm, setColForm] = useState<Row | null>(null);

  async function loadTables() {
    try { const r = await api('admin/db/tables'); setTables(r.items); setTypes(r.types); } catch (e) { setError((e as Error).message); }
  }
  async function loadRows(t = table) {
    if (!t) return;
    try {
      const p = new URLSearchParams({ limit: String(limit), offset: String(offset), q, ...(order ? { order, desc: String(desc) } : {}) });
      setData(await api(`admin/db/tables/${encodeURIComponent(t)}/rows?${p}`)); setError('');
    } catch (e) { setData(null); setError((e as Error).message); }
  }
  useEffect(() => { void loadTables(); }, []);
  useEffect(() => { void loadRows(); }, [table, order, desc, limit, offset]);

  const pk = useMemo(() => (data?.columns ?? []).filter(c => c.primary_key), [data]);
  const keyOf = (r: Row) => Object.fromEntries(pk.map(c => [c.name, r[c.name]]));
  function needReason(): boolean {
    if (reason.trim().length >= 5) return true;
    setError('Write a reason first (at least 5 characters); it is kept in the audit log with every change.'); return false;
  }
  async function run(label: string, fn: () => Promise<any>, reloadTables = false) {
    if (!needReason()) return;
    setBusy(true); setError(''); setNotice('');
    try { await fn(); setNotice(label); await loadRows(); if (reloadTables) await loadTables(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const T = encodeURIComponent(table);

  async function saveCell(e?: FormEvent, setNull = false) {
    e?.preventDefault(); if (!editing || !data) return;
    const r = data.rows[editing.row];
    await run(`Saved ${editing.col}.`, () => api(`admin/db/tables/${T}/rows`, 'PATCH', { key: keyOf(r), values: { [editing.col]: setNull ? null : editing.value }, reason: reason.trim() }));
    setEditing(null);
  }
  async function deleteRow(r: Row) {
    if (!window.confirm(`Delete this row from ${table}?\n${JSON.stringify(keyOf(r))}\nThis cannot be undone.`)) return;
    await run('Row deleted.', () => api(`admin/db/tables/${T}/rows/delete`, 'POST', { key: keyOf(r), reason: reason.trim() }));
  }
  async function insertRow(e: FormEvent) {
    e.preventDefault(); if (!adding) return;
    const values = Object.fromEntries(Object.entries(adding).filter(([, v]) => v !== '' && v !== undefined).map(([k, v]) => [k, v === '∅' ? null : v]));
    await run('Row added.', () => api(`admin/db/tables/${T}/rows`, 'POST', { values, reason: reason.trim() }));
    setAdding(null);
  }
  async function saveColumn(e: FormEvent) {
    e.preventDefault(); if (!colForm) return;
    if (colForm.mode === 'add') await run(`Column ${colForm.name} added.`, () => api(`admin/db/tables/${T}/columns`, 'POST',
      { name: colForm.name, type: colForm.type, nullable: colForm.nullable, default: colForm.default || null, reason: reason.trim() }));
    else await run(`Column ${colForm.column} changed.`, () => api(`admin/db/tables/${T}/columns/${encodeURIComponent(colForm.column)}`, 'PATCH', {
      rename_to: colForm.name !== colForm.column ? colForm.name : null, type: colForm.type || null,
      nullable: colForm.nullable === colForm.was_nullable ? null : colForm.nullable, default: colForm.default || null, drop_default: !!colForm.drop_default, reason: reason.trim() }));
    setColForm(null); await loadTables();
  }
  async function dropColumn(name: string) {
    const typed = window.prompt(`Drop column ${table}.${name} and all its values? This cannot be undone.\nType ${table}.${name} to confirm:`);
    if (typed === null) return;
    await run(`Column ${name} dropped.`, () => api(`admin/db/tables/${T}/columns/${encodeURIComponent(name)}/drop`, 'POST', { confirm: typed.trim(), reason: reason.trim() }), true);
    setColForm(null);
  }
  async function createTable() {
    const name = window.prompt('New table name (lower-case letters, digits, _). It starts with id and created_at; add columns after:');
    if (!name) return;
    await run(`Table ${name} created.`, async () => { await api('admin/db/tables', 'POST', { name: name.trim(), reason: reason.trim() }); setTable(name.trim()); }, true);
  }
  async function dropTable() {
    const typed = window.prompt(`Drop table ${table} with all its rows? This cannot be undone and COREI may stop working if it uses this table.\nType ${table} to confirm:`);
    if (typed === null) return;
    await run(`Table ${table} dropped.`, async () => { await api(`admin/db/tables/${T}/drop`, 'POST', { confirm: typed.trim(), reason: reason.trim() }); setTable(''); setData(null); }, true);
  }

  const shown = tables.filter(t => !filter || t.name.includes(filter.toLowerCase()));
  const cols = data?.columns ?? [];
  return <div className="dbx">
    <aside className="dbx-tables">
      <div className="dbx-tools"><input aria-label="Filter tables" placeholder={`Filter ${tables.length} tables…`} value={filter} onChange={e => setFilter(e.target.value)} />
        <button onClick={() => void createTable()} title="Create a table">+ Table</button></div>
      <ul role="listbox" aria-label="Tables">{shown.map(t => <li key={t.name} role="option" aria-selected={t.name === table}
        onClick={() => { setTable(t.name); setOffset(0); setOrder(''); setQ(''); setEditing(null); setAdding(null); setColForm(null); }}>
        <span>{t.name}</span><small>{Number(t.rows).toLocaleString()} rows · {t.columns} cols</small></li>)}</ul>
    </aside>
    <section className="dbx-main">
      <div className="dbx-tools">
        <input className="dbx-reason" aria-label="Reason for changes" placeholder="Reason for changes (audit log) — required to change anything" value={reason} onChange={e => setReason(e.target.value)} />
        {table && <>
          <form onSubmit={e => { e.preventDefault(); setOffset(0); void loadRows(); }}><input aria-label="Search rows" placeholder="Search all columns…" value={q} onChange={e => setQ(e.target.value)} /></form>
          <button onClick={() => setAdding(Object.fromEntries(cols.filter(c => !c.secret).map(c => [c.name, ''])))} disabled={busy}>+ Row</button>
          <button onClick={() => setColForm({ mode: 'add', name: '', type: 'text', nullable: true, default: '' })} disabled={busy}>+ Column</button>
          <button className="danger" onClick={() => void dropTable()} disabled={busy}>Drop table…</button>
          <select aria-label="Rows per page" value={limit} onChange={e => { setLimit(Number(e.target.value)); setOffset(0); }}>{PAGE_SIZES.map(n => <option key={n} value={n}>{n} / page</option>)}</select>
          <span className="dbx-page">{data ? `${data.total ? offset + 1 : 0}–${Math.min(offset + limit, data.total)} of ${data.total.toLocaleString()}` : ''}</span>
          <button aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>‹</button>
          <button aria-label="Next page" disabled={!data || offset + limit >= data.total} onClick={() => setOffset(offset + limit)}>›</button>
        </>}
      </div>
      {error && <p role="alert" className="cfg-error">{error}</p>}
      {notice && <p role="status" className="cfg-ok">{notice}</p>}
      {!table && <p className="cfg-note">Choose a table. Changes need the Administrator role, a reason and your password re-entered; secrets stay hidden.
        Structure changes made here are not in COREI's migration files — note them for the next update.</p>}
      {colForm && <form className="dbx-colform" onSubmit={saveColumn} aria-label="Column">
        <b>{colForm.mode === 'add' ? 'Add column' : `Column ${colForm.column}`}</b>
        <input aria-label="Column name" required value={colForm.name} onChange={e => setColForm({ ...colForm, name: e.target.value })} placeholder="name" />
        <select aria-label="Column type" value={colForm.type ?? ''} onChange={e => setColForm({ ...colForm, type: e.target.value })}>
          {colForm.mode !== 'add' && <option value="">keep type ({colForm.current_type})</option>}{types.map(t => <option key={t}>{t}</option>)}</select>
        <label><input type="checkbox" checked={colForm.nullable} onChange={e => setColForm({ ...colForm, nullable: e.target.checked })} /> empty allowed (NULL)</label>
        <input aria-label="Column default" value={colForm.default ?? ''} onChange={e => setColForm({ ...colForm, default: e.target.value })} placeholder="default (optional)" />
        {colForm.mode !== 'add' && <label><input type="checkbox" checked={!!colForm.drop_default} onChange={e => setColForm({ ...colForm, drop_default: e.target.checked })} /> remove default</label>}
        <button className="primary" disabled={busy}>{colForm.mode === 'add' ? 'Add column' : 'Save column'}</button>
        {colForm.mode !== 'add' && <button type="button" className="danger" onClick={() => void dropColumn(colForm.column)}>Drop column…</button>}
        <button type="button" onClick={() => setColForm(null)}>Cancel</button>
      </form>}
      {data && <div className="dbx-grid"><table className="cfg-table">
        <thead><tr><th />{cols.map(c => <th key={c.name} aria-sort={order === c.name ? (desc ? 'descending' : 'ascending') : undefined}>
          <button className="cfg-sort" onClick={() => { if (order === c.name) setDesc(!desc); else { setOrder(c.name); setDesc(false); } }}
            title={`${c.data_type}${c.nullable ? '' : ' · required'}${c.default ? ` · default ${c.default}` : ''}`}>{c.primary_key ? '🔑 ' : ''}{c.name}</button>
          <small>{c.udt_name}{c.secret ? ' · hidden' : ''}</small>
          {!c.secret && <button className="dbx-colbtn" aria-label={`Edit column ${c.name}`} onClick={() => setColForm({ mode: 'edit', column: c.name, name: c.name, type: '', current_type: c.data_type,
            nullable: c.nullable, was_nullable: c.nullable, default: '' })}>⋯</button>}</th>)}</tr></thead>
        <tbody>
          {adding && <tr className="dbx-new"><td><form id="dbx-add" onSubmit={insertRow}><button className="primary" disabled={busy}>Add</button>
            <button type="button" onClick={() => setAdding(null)}>✕</button></form></td>
            {cols.map(c => <td key={c.name}>{c.secret ? <small>hidden</small> : <input form="dbx-add" aria-label={`New ${c.name}`} value={adding[c.name] ?? ''}
              placeholder={c.default ? 'default' : c.nullable ? 'empty' : 'required'} onChange={e => setAdding({ ...adding, [c.name]: e.target.value })} />}</td>)}</tr>}
          {data.rows.map((r, i) => <tr key={i}>
            <td><button className="dbx-del" aria-label="Delete row" title={pk.length ? 'Delete this row' : 'No primary key: rows cannot be deleted one by one'} disabled={!pk.length || busy} onClick={() => void deleteRow(r)}>🗑</button></td>
            {cols.map(c => <td key={c.name} className={c.secret ? 'dbx-secret' : ''} onDoubleClick={() => !c.secret && pk.length && setEditing({ row: i, col: c.name, value: asInput(r[c.name]) })}>
              {editing && editing.row === i && editing.col === c.name
                ? <form onSubmit={saveCell} className="dbx-cell"><input autoFocus aria-label={`Edit ${c.name}`} value={editing.value} onChange={e => setEditing({ ...editing, value: e.target.value })}
                    onKeyDown={e => { if (e.key === 'Escape') setEditing(null); }} />
                  <button className="primary" disabled={busy}>Save</button>{c.nullable && <button type="button" onClick={() => void saveCell(undefined, true)} title="Set to empty (NULL)">∅</button>}
                  <button type="button" onClick={() => setEditing(null)}>✕</button></form>
                : r[c.name] === null ? <span className="dbx-null">∅</span> : <span title={show(r[c.name])}>{show(r[c.name])}</span>}</td>)}</tr>)}
        </tbody></table>
        <p className="cfg-note">Double-click a cell to edit it (Enter saves, Esc cancels, ∅ sets it empty). In a new row, leave a cell blank for its default; type ∅ for empty.</p>
      </div>}
    </section>
  </div>;
}
