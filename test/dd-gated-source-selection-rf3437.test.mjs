// R-F3437 — the New DD form must let the operator pre-select metered/gated sources,
// and must actually SEND that selection.
//
// THE DEFECT: the backend has had elections/waivers since R-F3406/R-F3408/R-F3411, and
// public/dd-reports.html contained ZERO references to dd_scope, elections or waivers.
// Every selection surface existed except the one a human touches, so the operator could
// not choose anything and every run silently used the full default scope. "Wired but
// unreachable" reads exactly like "not built" from the outside.
//
// These tests execute the page's REAL inline script in a vm with stubbed globals, then
// drive the real loader and the real scope builder — not a copy of the logic.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = readFileSync(new URL('../public/dd-reports.html', import.meta.url), 'utf8');

const OPTIONS_FIXTURE = {
  ok: true,
  entity_type: 'company',
  tier: 'STANDARD',
  standard_version: '1.0.0',
  questions_in_scope: 19,
  options: [
    {
      source_id: 'registry_trust', name: 'Registry Trust / TrustOnline (CCJ register)',
      access: 'PAID_PER_SEARCH', available: false, built: false,
      unavailable_reason: 'no adapter and metered spend not approved',
      decision: 'BLOCKING — these questions cannot be answered without it',
      required: true,
      required_for: [{ question_id: 'IS-17b', fundamental: 17, text: 'CCJs' }],
      enhances: [],
    },
    {
      source_id: 'sanctions', name: 'OpenSanctions consolidated screening',
      access: 'QUOTA_LIMITED', available: true, built: true, unavailable_reason: '',
      decision: 'REQUIRED — usable now; select to search, decline to waive',
      required: true,
      required_for: [{ question_id: 'IS-13', fundamental: 13, text: 'sanctions' },
                     { question_id: 'IS-13b', fundamental: 13, text: 'officers' }],
      enhances: [],
    },
    {
      source_id: 'find_case_law', name: 'Find Case Law (National Archives)',
      access: 'LICENCE_REQUIRED', available: false, built: false,
      unavailable_reason: 'no adapter and the licence question is unanswered',
      decision: 'OPTIONAL — unavailable, and something else covers these',
      required: false, required_for: [],
      enhances: [{ question_id: 'IS-17a', fundamental: 17, text: 'judgments' }],
    },
  ],
};

/** Build a DOM stub good enough for the loader + builder, and run the page's script. */
function loadPage({ optionsResponse = OPTIONS_FIXTURE, ok = true, reject = false } = {}) {
  const nodes = new Map();
  const checkboxes = [];

  function mkEl(id) {
    const el = {
      id, value: '', innerHTML: '', textContent: '', disabled: false,
      style: { display: '' }, dataset: {}, classList: {
        add() {}, remove() {}, toggle() {}, contains: () => false,
      },
      addEventListener() {}, querySelector: () => null, querySelectorAll: () => [],
      focus() {}, remove() {}, getAttribute: () => null, setAttribute() {},
      closest: () => null,
    };
    nodes.set(id, el);
    return el;
  }

  ['dd-r-scope-wrap', 'dd-r-scope', 'dd-r-type', 'dd-r-mode', 'dd-r-name', 'dd-r-jur',
   'dd-r-reg', 'dd-r-url', 'dd-r-prod', 'dd-run-submit', 'dd-run-cancel',
   'dd-run-confirm'].forEach(mkEl);
  nodes.get('dd-r-type').value = 'company';
  nodes.get('dd-r-mode').value = 'standard';

  const document_ = {
    getElementById: (id) => nodes.get(id) || null,
    querySelectorAll: (sel) => {
      if (sel === '#dd-r-scope .dd-scope-src') return checkboxes;
      return [];
    },
    querySelector: () => null,
    addEventListener() {},
    createElement: () => mkEl('tmp'),
    body: mkEl('body'),
  };

  const authedCalls = [];
  const sandbox = {
    document: document_,
    window: {},
    console,
    setTimeout, clearTimeout, setInterval, clearInterval,
    fetch: async () => ({ ok: true, json: async () => ({}) }),
    authed: async (path) => {
      authedCalls.push(path);
      if (reject) throw new Error('network down');
      return { ok, json: async () => optionsResponse };
    },
    escText: (s) => String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'),
    _CURRENT_USER: { email: 'ops@arkmurus.com' },
    Toast: { show() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;

  // Pull only the functions under test out of the page, so an unrelated bootstrap
  // failure elsewhere in the file cannot mask or fake these results.
  const wanted = ['loadScopeOptions', 'ddBuildScope'];
  const src = extractFunctions(HTML, wanted);
  vm.createContext(sandbox);
  vm.runInContext(src, sandbox);

  return { sandbox, nodes, checkboxes, authedCalls };
}

/** Extract named function declarations (plus the consts they close over) from the page. */
function extractFunctions(html, names) {
  const script = html.slice(html.indexOf('<script'), html.lastIndexOf('</script>'));
  let out = '';
  const tierConst = script.match(/const DD_TIER_FOR_MODE = \{[^}]*\};/);
  assert.ok(tierConst, 'DD_TIER_FOR_MODE must exist on the page');
  out += tierConst[0] + '\nlet ddScopeOptions = null;\n';
  for (const name of names) {
    const start = script.indexOf(`function ${name}(`);
    const asyncStart = script.indexOf(`async function ${name}(`);
    const from = asyncStart !== -1 ? asyncStart : start;
    assert.notEqual(from, -1, `page must define ${name} — the form wiring is missing`);
    // brace-match to the end of the declaration
    let i = script.indexOf('{', from), depth = 0, end = -1;
    for (; i < script.length; i++) {
      if (script[i] === '{') depth++;
      else if (script[i] === '}') { depth--; if (depth === 0) { end = i + 1; break; } }
    }
    assert.notEqual(end, -1, `could not parse ${name}`);
    out += script.slice(from, end) + '\n';
  }
  return out;
}

test('the page actually references dd_scope — the wiring the operator needs', () => {
  assert.ok(HTML.includes('dd_scope'),
    'dd-reports.html must send dd_scope; without it every run uses the full default scope');
  assert.ok(HTML.includes('/api/aria/dd/scope-options'),
    'the form must load the per-subject gated-source list');
});

test('a REQUIRED and AVAILABLE source is pre-selected', async () => {
  const { sandbox, nodes } = loadPage();
  await sandbox.loadScopeOptions();
  const html = nodes.get('dd-r-scope').innerHTML;
  const sanctionsBlock = html.slice(html.indexOf('data-source="sanctions"'));
  assert.ok(sanctionsBlock.startsWith('data-source="sanctions" checked'),
    'a required, usable source must be ticked by default');
});

test('a REQUIRED but UNAVAILABLE source is never pre-ticked', async () => {
  const { sandbox, nodes } = loadPage();
  await sandbox.loadScopeOptions();
  const html = nodes.get('dd-r-scope').innerHTML;
  const rt = html.slice(html.indexOf('data-source="registry_trust"'));
  assert.ok(!rt.startsWith('data-source="registry_trust" checked'),
    'pre-ticking an unusable source promises a search that cannot happen');
  // R-F4002 (C-81) — was `assert.ok(rt.includes('disabled'))`, and that asserted a
  // design the product deliberately replaced. R-F3465 made an unavailable source
  // TICKABLE on purpose: ticking it ORDERS the search, and the report then records
  // the section as ordered-but-not-searched, names the blocker, and excludes it
  // from anything chargeable. The modal says exactly that to the user
  // ("Tick to order it anyway"), and §18 requires it for the CCJ / Registry Trust
  // case — an elected search that cannot run must record a data gap naming the env
  // var, never a clean line.
  //
  // Disabling the box would REMOVE the ability to order a blocked register, which
  // is a capability the operator relies on. Greening this test by "fixing" the
  // code would have been a real regression dressed as a repair.
  //
  // The surviving intent is narrower and is what the test title always said: an
  // unavailable source must never be PRE-TICKED (asserted above), and the blocker
  // must be visible rather than a silent grey-out (asserted below).
  assert.ok(/order it anyway/i.test(rt),
    'an unavailable source must offer the explicit order-anyway path, not be silently disabled');
  // Assert the PROPERTY (a blocking source is visibly flagged), not one exact wording:
  // R-F3278 bans em dashes in displayed copy, so pinning the literal string would make
  // this guard fight a copy rule and lose.
  // R-F4002 (C-81) — was /REQUIRED[^<]*UNAVAILABLE/, case-SENSITIVE and pinned to
  // shouty wording the copy no longer uses: the pill now reads
  // "Required &middot; not yet available". The comment two lines up already warned
  // that pinning a literal string would make this guard fight a copy rule and
  // lose — it then lost to a copy change anyway, because case and phrasing were
  // still literal.
  //
  // The PROPERTY is what matters: both facts must be visible in the same flag —
  // that the source is required, and that it cannot run yet. Asserted
  // case-insensitively and against either phrasing, so a copy pass cannot rot it
  // again while a silent grey-out still fails.
  // Scoped to registry_trust's own <label>, not the whole list. Asserting over
  // `html` would pass whenever ANY source is required and ANY OTHER is
  // unavailable — two true facts about different rows, which is not the claim.
  const rtRow = rt.slice(0, rt.indexOf('</label>') + 1 || rt.length);
  assert.ok(/required/i.test(rtRow) && /(not yet available|unavailable)/i.test(rtRow),
    'a blocking source must be visibly flagged as required AND unavailable, not quietly greyed out');
});

test('ticking a source ELECTS every question it unlocks', async () => {
  const { sandbox, checkboxes } = loadPage();
  await sandbox.loadScopeOptions();
  checkboxes.push({ checked: true, dataset: { source: 'sanctions' } });
  const scope = sandbox.ddBuildScope();
  // Array.from re-homes the value into THIS realm: the vm context has its own
  // Array.prototype, so deepStrictEqual fails on prototype identity alone even when the
  // contents match exactly.
  const elected = Array.from(scope.elections.map((e) => e.question_id)).sort();
  assert.deepEqual(elected, ['IS-13', 'IS-13b']);
  assert.equal(scope.elections[0].elected_by, 'ops@arkmurus.com');
  assert.equal(scope.tier, 'STANDARD');
});

test('unticking a source produces a waiver that NAMES who and why', async () => {
  // The backend deliberately ignores an anonymous waiver and screens anyway, so a waiver
  // without who+why silently fails to conserve the metered allowance it was meant to save.
  const { sandbox, checkboxes } = loadPage();
  await sandbox.loadScopeOptions();
  checkboxes.push({ checked: false, dataset: { source: 'sanctions' } });
  const scope = sandbox.ddBuildScope();
  assert.equal(scope.elections.length, 0);
  assert.equal(scope.waivers.length, 2);
  for (const w of scope.waivers) {
    assert.equal(w.waived_by, 'ops@arkmurus.com');
    assert.ok(w.reason && w.reason.length > 0, 'a waiver must state a reason');
  }
});

test('a failed options load warns instead of rendering an empty clean list', async () => {
  for (const bad of [{ ok: false }, { reject: true },
                     { optionsResponse: { ok: false, options: null } }]) {
    const { sandbox, nodes } = loadPage(bad);
    await sandbox.loadScopeOptions();
    const box = nodes.get('dd-r-scope');
    assert.ok(box.innerHTML.includes('Could not load'),
      `a failure must be stated, not silently empty (case ${JSON.stringify(bad)})`);
    assert.equal(nodes.get('dd-r-scope-wrap').style.display, '',
      'the warning must be VISIBLE — hiding it is the false-clean shape');
    assert.equal(sandbox.ddBuildScope(), null,
      'a failed load must not fabricate a scope');
  }
});

test('the tier follows the depth control', async () => {
  const { sandbox, nodes, authedCalls } = loadPage();
  nodes.get('dd-r-mode').value = 'deep';
  await sandbox.loadScopeOptions();
  assert.ok(authedCalls.some((p) => p.includes('tier=ENHANCED')),
    `deep mode must request the ENHANCED slice: ${authedCalls.join(', ')}`);
});

test('the entity type follows the person/company control', async () => {
  const { sandbox, nodes, authedCalls } = loadPage();
  nodes.get('dd-r-type').value = 'person';
  await sandbox.loadScopeOptions();
  assert.ok(authedCalls.some((p) => p.includes('entity_type=person')),
    `a person subject must request the person slice: ${authedCalls.join(', ')}`);
});
