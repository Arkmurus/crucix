import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const html = readFileSync('public/watchlist.html', 'utf8');

function functionSource(name) {
  const asyncStart = html.indexOf(`async function ${name}(`);
  const start = asyncStart >= 0 ? asyncStart : html.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} must exist in the shipped page`);
  const brace = html.indexOf(') {', start) + 2;
  assert.ok(brace > 1, `Could not find the body of ${name}`);
  let depth = 0;
  for (let index = brace; index < html.length; index += 1) {
    if (html[index] === '{') depth += 1;
    if (html[index] === '}') depth -= 1;
    if (depth === 0) return html.slice(start, index + 1);
  }
  throw new Error(`Could not extract ${name}`);
}

const context = {};
vm.createContext(context);
vm.runInContext(
  `${functionSource('normaliseWatchlistCandidates')}\n`
    + `${functionSource('loadWatchlistCandidates')}\n`
    + `${functionSource('resolveWatchlistCandidate')}`,
  context,
);

test('R-F3235 Add Entity is Type-first and has no free-text enrollment path', () => {
  const sourcePosition = html.indexOf('id="wl-add-source"');
  const entityPosition = html.indexOf('id="wl-add-entity"');
  assert.ok(sourcePosition > 0 && entityPosition > sourcePosition);
  assert.doesNotMatch(html, /id="wl-add-name"/);
  assert.doesNotMatch(html, /placeholder="e\.g\. Assan Group"/);
  assert.match(html, /<option value="dd_report">DD Reports<\/option>/);
  assert.match(html, /<option value="vetting_case">Vetting<\/option>/);
  assert.match(html, /authed\('\/api\/aria\/dd\/reports\?limit=200'\)/);
  assert.match(html, /authed\('\/api\/aria\/vetting\/cases'\)/);
});

test('R-F3235 normalizes only stored DD reports and vetting cases', () => {
  const candidates = context.normaliseWatchlistCandidates([
    {
      run_id: 'run-new', entity_name: 'Acme Defence', entity_type: 'company',
      jurisdiction: 'gb', canonical_entity_id: 'acme-1',
    },
    {
      run_id: 'run-old', entity_name: 'Acme Defence', entity_type: 'company',
      jurisdiction: 'gb', canonical_entity_id: 'acme-1',
    },
    { run_id: '', entity_name: 'Injected without a report' },
    { run_id: 'run-blank', entity_name: '' },
  ], [
    { case_id: 'case-1', applicant_name: 'Ada Lovelace' },
    { case_id: '', applicant_name: 'Injected without a case' },
    { case_id: 'case-blank', applicant_name: '  ' },
  ]);

  assert.equal(candidates.length, 2);
  assert.deepEqual(
    JSON.parse(JSON.stringify(candidates.map(({ source, source_ref, name }) => ({ source, source_ref, name })))),
    [
      { source: 'dd_report', source_ref: 'run-new', name: 'Acme Defence' },
      { source: 'vetting_case', source_ref: 'case-1', name: 'Ada Lovelace' },
    ],
  );
});

test('R-F3235 preselection is source-bound and rejects arbitrary names', () => {
  const candidates = context.normaliseWatchlistCandidates(
    [{ run_id: 'run-1', entity_name: 'Shared Name', canonical_entity_id: 'dd-1' }],
    [{ case_id: 'case-1', applicant_name: 'Shared Name' }],
  );

  assert.equal(
    context.resolveWatchlistCandidate(candidates, {
      source: 'vetting_case', source_ref: 'case-1', name: 'Shared Name',
    }).source_ref,
    'case-1',
  );
  assert.equal(
    context.resolveWatchlistCandidate(candidates, {
      source: 'dd_report', source_ref: 'case-1', name: 'Shared Name',
    }).source_ref,
    'run-1',
  );
  assert.equal(
    context.resolveWatchlistCandidate(candidates, {
      source: 'dd_report', source_ref: 'forged', name: 'Not in reports',
    }),
    null,
  );
});

test('R-F3235 fails one source closed without hiding valid entities from the other', async () => {
  context.authed = async (path) => path.includes('/dd/reports')
    ? { ok: false, status: 503 }
    : { ok: true, json: async () => ({ cases: [{ case_id: 'case-live', applicant_name: 'Grace Hopper' }] }) };

  const loaded = await context.loadWatchlistCandidates();
  assert.equal(loaded.available.dd_report, false);
  assert.equal(loaded.available.vetting_case, true);
  assert.equal(loaded.candidates.length, 1);
  assert.equal(loaded.candidates[0].source_ref, 'case-live');
});
