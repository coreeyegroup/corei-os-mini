/* Settings: every preference in one place, VS Code style — search, categories, a description per setting, "modified"
 * markers and reset, and where each setting is kept (this browser, my account, or the firm).
 *
 * Why it exists: Settings held a theme picker and a few layout switches. Time zone, number grouping, order
 * confirmation, start page, the top bar's contents and links to the firm's own settings were missing or scattered.
 * Purpose: browser settings (appearance, layout: passed in from the shell), account settings (userSettings: follow the
 * person to any browser), keyboard shortcuts (from the command list), firm settings (links to the pages that own
 * them, with who may change them), Software update (UpdatePanel: check GitHub, install with safety checks; 0.67.1) and
 * About (version, schema, server, Check for updates).
 * Must never: change trading state; change a firm setting here (firm settings open their own audited pages).
 * Dependencies: userSettings, nav.goto, StageShell.useData. Consumers: App (view "Settings").
 * Architectural position: UI preferences.
 */
import { isValidElement, useMemo, useState, type ReactNode } from 'react';
import { goto, useSection } from './nav';
import type { View } from './layout';
import { useData, type Row } from './StageShell';
import UpdatePanel from './UpdatePanel';
import PlatformMode from './PlatformMode';
import { defaultAppearance, type Appearance } from './appearance';
import { SETTINGS, isModified, resetAll, resetSetting, setSetting, setting, useSettings, type SettingDef } from './userSettings';

type LayoutLike = { left: boolean; right: boolean; bottom: boolean; leftWidth: number; rightWidth: number; bottomHeight: number };
type Shortcut = { section: string; label: string; shortcut?: string };
type Props = {
  appearance: Appearance; themes: readonly Appearance['theme'][]; setAppearance: (p: Partial<Appearance>) => void; resetAppearance: () => void;
  layout: LayoutLike; setLayout: (p: Partial<LayoutLike>) => void; resetLayout: () => void; shortcuts: Shortcut[]; roles: string[];
};

const FIRM: [string, string, View, string, string][] = [
  // label, description, view, section, who may change it
  ['Setup gates & overrides', 'How strictly each setup step holds actions per track; overrides.', 'Configuration', 'gates', 'Administrator or Risk approver'],
  ['Order sending', 'DEMO and LIVE order sending switches.', 'Configuration', 'controls', 'Administrator (four-eyes on LIVE)'],
  ['Governance profiles & risk limits', 'Strategy evidence and risk-minimum profiles; limits per account.', 'Risk', 'limits', 'Risk approver'],
  ['Kill switches', 'Engage or release firm, account, strategy and run kill switches.', 'Risk', 'kill', 'Risk approver'],
  ['Approvals (four-eyes)', 'Changes waiting for a second person.', 'Configuration', 'approvals', 'Administrator or Risk approver'],
  ['Alerts', 'E-mail, Telegram and webhook channels and rules.', 'Configuration', 'alerts', 'Administrator'],
  ['Users & roles', 'People, roles and sessions.', 'Configuration', 'users', 'Administrator'],
  ['Paper accounts', 'Simulated accounts: currency, amount, markets, costs (in Portfolio).', 'Portfolio', 'paper', 'Trading operations'],
  ['FX rates', 'Rates for currencies no collected pair converts.', 'Configuration', 'fx', 'Risk approver'],
  ['Market data policies', 'Collection plans, quality gates, backfill and archive.', 'Market data', 'plans', 'Trading operations'],
  ['VaR settings', 'Value-at-risk method, horizon and confidence.', 'Risk', 'var-settings', 'Risk approver'],
  ['Audit log', 'Every change with who, when and why.', 'Configuration', 'audit', 'Read: everyone'],
];
export const CATS = ['Appearance', 'Layout', 'Time & region', 'Trading', 'Startup', 'Top bar', 'Keyboard shortcuts', 'Firm settings', 'Platform mode', 'Software update', 'About'];
export const catId = (c: string) => c.toLowerCase().replace(/[^a-z]+/g, '-');
const SCOPE_LABEL = { BROWSER: 'this browser', ACCOUNT: 'my account', FIRM: 'firm' } as const;

function Row_({ label, description, scope, modified, onReset, children, id }: { label: string; description: string; scope: keyof typeof SCOPE_LABEL; modified?: boolean;
  onReset?: () => void; children: ReactNode; id: string }) {
  // One row per setting: title · control · description (VS Code density; the full description is on hover).
  const check = isValidElement(children) && children.type === 'input' && (children.props as { type?: string }).type === 'checkbox';
  return <div className={`set-row ${modified ? 'modified' : ''} ${check ? 'is-check' : ''}`} id={`setting-${id}`}>
    <div className="set-title"><b>{label}</b>{modified && <span className="set-dot" title="Changed from the default" aria-label="modified">●</span>}
      <span className={`set-scope ${scope.toLowerCase()}`} title={`Kept in ${SCOPE_LABEL[scope]}`}>{SCOPE_LABEL[scope].replace('this ', '').replace('my ', '')}</span></div>
    {check ? <label className="set-control set-check">{children}<span className="set-desc" title={description}>{description}</span></label>
      : <><div className="set-control">{children}</div><p className="set-desc" title={description}>{description}</p></>}
    {modified && onReset ? <button className="link set-reset" title="Reset to the default" onClick={onReset}>↺</button> : <span />}
  </div>;
}

function AccountSetting({ def }: { def: SettingDef }) {
  const v = setting<string | boolean>(def.id);
  const [draft, setDraft] = useState(String(v));
  const bad = def.type === 'text' && def.pattern && !new RegExp(def.pattern).test(draft);
  return <Row_ id={def.id} label={def.label} description={def.description} scope="ACCOUNT" modified={isModified(def.id)} onReset={() => { resetSetting(def.id); setDraft(String(def.default)); }}>
    {def.type === 'boolean' ? <input type="checkbox" aria-label={def.label} checked={!!v} onChange={e => setSetting(def.id, e.target.checked)} />
      : def.type === 'select' ? <select aria-label={def.label} value={String(v)} onChange={e => setSetting(def.id, e.target.value)}>{def.options!.map(([o, l]) => <option key={o} value={o}>{l}</option>)}</select>
        : <input aria-label={def.label} aria-invalid={!!bad} value={draft} onChange={e => setDraft(def.id === 'trading.paperAccount' ? e.target.value.toUpperCase() : e.target.value)}
          onBlur={() => { if (!bad) setSetting(def.id, draft); }} />}
  </Row_>;
}

export default function SettingsView(p: Props) {
  useSettings();
  const [q, setQ] = useState('');
  const [catSlug, setCatSlug] = useSection<string>('Settings', 'appearance', CATS.map(catId));
  const cat = CATS.find(c => catId(c) === catSlug) ?? CATS[0];
  const setCat = (c: string) => setCatSlug(catId(c));
  const [about] = useData<Row>('workstation', 0);
  const query = q.trim().toLowerCase();
  const hit = (...t: string[]) => !query || t.some(x => x.toLowerCase().includes(query));
  const a = p.appearance, l = p.layout;
  const browser: { cat: string; id: string; label: string; description: string; modified: boolean; reset: () => void; control: ReactNode }[] = [
    { cat: 'Appearance', id: 'theme', label: 'Color theme', description: 'Colours of the whole workstation.', modified: a.theme !== defaultAppearance.theme, reset: () => p.setAppearance({ theme: defaultAppearance.theme }),
      control: <div className="set-themes">{p.themes.map(t => <button key={t} aria-pressed={a.theme === t} onClick={() => p.setAppearance({ theme: t })}><span className="theme-swatch" data-swatch={t} />{t}</button>)}</div> },
    { cat: 'Appearance', id: 'size', label: 'Text size', description: 'Interface text size in pixels (11–16).', modified: a.fontSize !== defaultAppearance.fontSize, reset: () => p.setAppearance({ fontSize: defaultAppearance.fontSize }),
      control: <><input aria-label="Interface text size" type="range" min={11} max={16} value={a.fontSize} onChange={e => p.setAppearance({ fontSize: Number(e.target.value) })} /><output>{a.fontSize}px</output></> },
    { cat: 'Appearance', id: 'density', label: 'Density', description: 'Compact fits more rows; comfortable adds space.', modified: a.density !== defaultAppearance.density, reset: () => p.setAppearance({ density: defaultAppearance.density }),
      control: <select aria-label="Interface density" value={a.density} onChange={e => p.setAppearance({ density: e.target.value as Appearance['density'] })}><option>Compact</option><option>Comfortable</option></select> },
    ...([['activity', 'Activity bar', 'The icon bar on the far left.', true], ['status', 'Status bar', 'The bar along the bottom edge.', true], ['wrapTabs', 'Wrap tabs', 'Show every open tab on several rows instead of scrolling.', false]] as const)
      .map(([k, label, d, def]) => ({ cat: 'Layout', id: k, label, description: d, modified: a[k] !== def, reset: () => p.setAppearance({ [k]: def }),
        control: <input type="checkbox" aria-label={label} checked={a[k]} onChange={e => p.setAppearance({ [k]: e.target.checked })} /> })),
    ...([['left', 'Side bar', 'The explorer on the left (Ctrl+B).', true], ['right', 'Secondary side bar', 'Attention, accounts, open positions, working orders and a watchlist on the right (Ctrl+Alt+B).', false], ['bottom', 'Panel', 'Problems, events, journal and activity at the bottom (Ctrl+J).', true]] as const)
      .map(([k, label, d, def]) => ({ cat: 'Layout', id: k, label, description: d, modified: l[k] !== def, reset: () => p.setLayout({ [k]: def }),
        control: <input type="checkbox" aria-label={label} checked={l[k]} onChange={e => p.setLayout({ [k]: e.target.checked })} /> })),
    ...([['leftWidth', 'Side bar width', 160, 380, 280], ['rightWidth', 'Secondary side bar width', 200, 440, 270], ['bottomHeight', 'Panel height', 120, 400, 130]] as const)
      .map(([k, label, min, max, def]) => ({ cat: 'Layout', id: k, label, description: `${min}–${max} px.`, modified: l[k] !== def, reset: () => p.setLayout({ [k]: def }),
        control: <><input aria-label={label} type="range" min={min} max={max} value={l[k]} onChange={e => p.setLayout({ [k]: Number(e.target.value) })} /><output>{l[k]} px</output></> })),
  ];
  const counts = useMemo(() => Object.fromEntries(CATS.map(c => [c, c === 'Keyboard shortcuts' ? p.shortcuts.filter(s => s.shortcut).length : c === 'Firm settings' ? FIRM.length : c === 'About' || c === 'Software update' || c === 'Platform mode' ? 1
    : browser.filter(b => b.cat === c).length + SETTINGS.filter(s => s.category === c).length])), [p.shortcuts]); // eslint-disable-line react-hooks/exhaustive-deps
  const show = (c: string) => query ? true : c === cat;
  const modifiedCount = SETTINGS.filter(s => isModified(s.id)).length + browser.filter(b => b.modified).length;
  return <section className="set" aria-label="Settings">
    <header className="set-head">
      <h2>Settings</h2>
      <input type="search" className="set-search" aria-label="Search settings" placeholder="Search settings (e.g. time zone, theme, confirm)" value={q} onChange={e => setQ(e.target.value)} autoFocus />
      <span className="set-meta">{modifiedCount} changed · <span className="set-scope browser">this browser</span> <span className="set-scope account">my account</span> <span className="set-scope firm">firm</span></span>
    </header>
    <div className="set-body">
      <nav className="set-nav" aria-label="Setting categories">{CATS.map(c => <button key={c} aria-current={!query && c === cat ? 'page' : undefined} onClick={() => { setQ(''); setCat(c); }}>
        {c}<small>{counts[c]}</small></button>)}</nav>
      <div className="set-list">
        {CATS.filter(show).map(c => {
          const rows = [
            ...browser.filter(b => b.cat === c && hit(b.label, b.description, c)).map(b => <Row_ key={b.id} id={b.id} label={b.label} description={b.description} scope="BROWSER" modified={b.modified} onReset={b.reset}>{b.control}</Row_>),
            ...SETTINGS.filter(s => s.category === c && hit(s.label, s.description, c)).map(s => <AccountSetting key={s.id} def={s} />),
            ...(c === 'Keyboard shortcuts' ? p.shortcuts.filter(s => s.shortcut && hit(s.label, s.section, c)).map(s => <div key={s.label} className="set-row set-kbd"><span>{s.label}</span><small>{s.section}</small><kbd>{s.shortcut}</kbd></div>) : []),
            ...(c === 'Firm settings' ? FIRM.filter(f => hit(f[0], f[1], c)).map(([label, d, view, section, who]) =>
              <Row_ key={label} id={section} label={label} description={`${d} Changed by: ${who}.`} scope="FIRM"><button onClick={() => goto(view, section)}>Open ↗</button></Row_>) : []),
            ...(c === 'Platform mode' && hit('platform mode development production team single user solo live reset', c) ? [<div key="mode" className="set-update"><PlatformMode isAdmin={p.roles.includes('ADMIN')} /></div>] : []),
            ...(c === 'Software update' && hit('software update upgrade version github release', c) ? [<div key="update" className="set-update"><UpdatePanel isAdmin={p.roles.includes('ADMIN')} /></div>] : []),
            ...(c === 'About' && hit('about version schema server update', c) ? [<div key="about" className="set-row set-about"><div className="set-title"><b>COREI-OS-MINI</b></div>
              <p className="set-desc">Version {about?.release ?? '—'} · schema {about?.schema ?? '—'} · build {about?.commit ?? '—'}. Your roles: {p.roles.join(', ') || '—'}.</p>
              <button onClick={() => { setQ(''); setCat('Software update'); }}>Check for updates…</button></div>] : []),
          ];
          if (!rows.length) return null;
          const resettable = c === 'Appearance' ? p.resetAppearance : c === 'Layout' ? p.resetLayout : SETTINGS.some(s => s.category === c) ? () => resetAll(c) : null;
          return <section key={c} className="set-section" aria-label={c}><h3><span>{c}</span>{resettable && !query && <button className="link" onClick={resettable}>Reset all</button>}</h3>{rows}</section>;
        })}
        {query && !CATS.some(c => [...browser.filter(b => b.cat === c && hit(b.label, b.description, c)), ...SETTINGS.filter(s => s.category === c && hit(s.label, s.description, c))].length
          || (c === 'Firm settings' && FIRM.some(f => hit(f[0], f[1], c))) || (c === 'Keyboard shortcuts' && p.shortcuts.some(s => s.shortcut && hit(s.label, s.section, c)))) && <p className="cfg-note">No setting matches “{q}”.</p>}
      </div>
    </div>
  </section>;
}
