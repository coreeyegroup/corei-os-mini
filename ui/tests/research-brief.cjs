const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
const { JSDOM } = require('jsdom');
const React = require('react');
const { createRoot } = require('react-dom/client');

function load(name, imports = {}) {
  const file = path.join(__dirname, '../src', name);
  const result = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const module = { exports: {} };
  new Function('require', 'module', 'exports', result.outputText)(
    id => Object.hasOwn(imports, id) ? imports[id] : require(id), module, module.exports);
  return module.exports;
}

const briefModule = load('researchBriefFormat.ts');
const now = '2026-11-20T12:00:00Z';
const snapshot = { timeframe: 'H1', regime: 'TREND_UP', bar_time: '2026-11-20T11:00:00Z',
  computed_at: now, bars: 300, bars_source: 'BROKER', features: { close: '1.08010', rsi14: 0, atr_pct: 0.12 } };

test('preserves observed quotes and separates missing values from numeric zero', () => {
  const brief = briefModule.createResearchBrief({ symbol: 'EURUSD', brokerSymbol: 'EURUSD.r', timeframe: 'H1',
    capturedAt: new Date('2026-11-20T12:00:19Z'), snapshots: [snapshot],
    context: { market: { bid: '1.08010', ask: '1.08012', price_at: now, spread: 0.00002, quality: 0,
      event_risk: [{ at: now, currency: 'USD', title: 'Test event\n# Second line' }] } } });
  assert.match(brief, /1\.08010 \| Not reported \| 0 \| 0\.12/);
  assert.match(brief, /Bid: 1\.08010/);
  assert.match(brief, /Quote age at capture \(seconds\): 19/);
  assert.match(brief, /Feed quality score \(last 60 minutes\): 0/);
  assert.match(brief, /Test event \\# Second line/);
  assert.match(brief, /stored calendar flagged an event window/);
});

test('labels absent market context and stale or warming-up features', () => {
  const brief = briefModule.createResearchBrief({ symbol: 'XAUUSD', brokerSymbol: 'XAUUSD', timeframe: 'H1',
    capturedAt: new Date('2026-11-20T12:20:00Z'), snapshots: [{ ...snapshot, regime: 'WARMING_UP', bar_time: 'invalid' }] });
  assert.match(brief, /warming up/);
  assert.match(brief, /no verifiable bar timestamp/);
  assert.match(brief, /more than ten minutes ago/);
  assert.match(brief, /Calendar context unavailable/);
  assert.match(brief, /Bid: Not reported/);
});

test('generates and copies a brief using GETs for the selected broker source', async () => {
  const dom = new JSDOM('<div id="root"></div>', { url: 'https://workstation.example/' });
  const prior = Object.fromEntries(['window', 'document', 'navigator', 'IS_REACT_ACT_ENVIRONMENT']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  for (const [key, value] of Object.entries({ window: dom.window, document: dom.window.document,
    navigator: dom.window.navigator, IS_REACT_ACT_ENVIRONMENT: true })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  const calls = [];
  let copied = '';
  Object.defineProperty(dom.window.navigator, 'clipboard', { value: { writeText: async text => { copied = text; } } });
  const api = async (...args) => {
    calls.push(args);
    if (args[0].includes('/instrument/')) return { snapshots: [snapshot] };
    return { session: { label: 'London' }, items: [
      { account_id: 'other-account', broker_symbol: 'EURUSD.r', bid: '9.9' },
      { account_id: 'private-account', broker_symbol: 'EURUSD.r', bid: '1.08010', ask: '1.08012', price_at: now,
        quality: 94, account_code: 'private-account-code', server_name: 'private-server' },
    ] };
  };
  const { default: ResearchBrief } = load('ResearchBrief.tsx', { './Configuration': { api }, './researchBriefFormat': briefModule });
  const root = createRoot(document.getElementById('root'));
  function button(name) { return [...document.querySelectorAll('button')].find(b => b.textContent === name); }
  try {
    await React.act(async () => root.render(React.createElement(ResearchBrief, {
      accountId: 'private-account', symbol: 'EURUSD', brokerSymbol: 'EURUSD.r', timeframe: 'H1',
    })));
    await React.act(async () => button('Generate research brief').click());
    assert.deepEqual(calls, [['intel/instrument/private-account/EURUSD.r?timeframe=H1&limit=10'], ['intel/screener?timeframe=H1']]);
    const preview = document.querySelector('textarea[aria-label="Research brief preview"]').value;
    assert.match(preview, /Bid: 1\.08010/);
    assert.doesNotMatch(preview, /Bid: 9\.9|private-account|private-server/);
    await React.act(async () => button('Copy for CORE-AI').click());
    assert.equal(copied, preview);
    assert.equal(calls.length, 2);
  } finally {
    await React.act(async () => root.unmount());
    dom.window.close();
    for (const [key, descriptor] of Object.entries(prior)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor);
      else delete globalThis[key];
    }
  }
});
