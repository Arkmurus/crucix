/**
 * R-F3091 — the PDF must state entity scope, exactly as the online view does.
 *
 * LIVE DEFECT (Mitie, 2026-07-26). The registry layer described MITIE FACILITIES
 * MANAGEMENT LIMITED (02938041) — a subsidiary — while the press coverage, published
 * financials and procurement awards all described the listed parent. Neither surface
 * said which entity any layer covered.
 *
 * Both surfaces read the SAME persisted `entity_scope` object (computed once by
 * dd_schema._dd_entity_scope), so they cannot drift apart — the R-F3055 lesson, where
 * a mirrored-by-hand renderer under-reported adverse media on the download.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { ddEntityScopeLines, generateDueDiligencePDF } from '../lib/reports/pdf_generator.mjs';

const flat = (scope) => ddEntityScopeLines(scope).map(([k, v]) => k + ': ' + v).join(' | ');

const MITIE_SCOPE = {
  subject_name: 'MITIE FACILITIES MANAGEMENT LIMITED',
  subject_registration: '02938041',
  is_subsidiary: true,
  immediate_parent: {
    name: 'Mitie Treasury Management Limited',
    registration_number: '07351242',
    anchored: true,
  },
  ownership_chain_traced: [
    'MITIE FACILITIES MANAGEMENT LIMITED',
    'Mitie Treasury Management Limited',
  ],
  warnings: [
    'SCOPE: the registry subject is MITIE FACILITIES MANAGEMENT LIMITED (02938041), '
    + 'a subsidiary controlled by Mitie Treasury Management Limited. Findings resolved '
    + 'by registration number describe THIS legal entity; findings resolved by name '
    + 'search describe the BRAND and will usually be about the wider group.',
  ],
};

const baseReport = (extra = {}) => ({
  run_id: 'dd_test_rf3091',
  target: { name: 'MITIE FACILITIES MANAGEMENT LIMITED', type: 'company' },
  identity: { entity_name: 'MITIE FACILITIES MANAGEMENT LIMITED', registration_number: '02938041' },
  risk_classification: 'GREEN',
  ...extra,
});

test('R-F3091: a subsidiary report states its scope and names the controller', () => {
  const s = flat(MITIE_SCOPE);
  assert.ok(s.includes('MITIE FACILITIES MANAGEMENT LIMITED'), 'the registry subject');
  assert.ok(s.includes('02938041'), 'its registration number');
  assert.ok(s.includes('Mitie Treasury Management Limited'), 'the controller');
  assert.ok(s.includes('07351242'), 'the controller registration number');
  // R-F3220 — the label is now 'Ownership chain (registry-anchored)'. The arrow
  // rendering is reserved for a real control descent; the walk's node list is
  // reported separately as parties traversed, because joining sibling officers
  // with arrows asserted a control chain that did not exist (Rossi, 07101898).
  assert.ok(s.includes('Ownership chain (registry-anchored)'),
    'the control descent the registry actually anchored');
  assert.ok(!s.includes('Chain traced'),
    'the old label read as a control chain over an unordered node list');
});

test('R-F3220: officer nodes render as parties traversed, never as a chain', () => {
  const s = flat({
    subject_name: 'ROSSI FACILITY SERVICES LTD',
    subject_registration: '07101898',
    is_subsidiary: true,
    immediate_parent: {
      name: 'Rossi Support Services Ltd', registration_number: '14833360', anchored: true },
    ownership_chain_traced: ['ROSSI FACILITY SERVICES LTD', 'Rossi Support Services Ltd'],
    parties_traversed: [
      { hop: 0, relation: 'subject', names: ['ROSSI FACILITY SERVICES LTD'] },
      { hop: 1, relation: 'officers / PSCs of the subject',
        names: ['ALKSMANTAS, Ernestas', 'DIMITROV, Dimitar Stoyanov'] },
    ],
    warnings: [],
  });
  assert.ok(s.includes('Parties traversed - officers / PSCs of the subject'),
    'the relationship must be named');
  assert.ok(s.includes('ALKSMANTAS, Ernestas; DIMITROV, Dimitar Stoyanov'),
    'siblings are listed as siblings');
  assert.ok(!/ALKSMANTAS[^\n]*>[^\n]*DIMITROV/.test(s),
    'officers must never be joined by control arrows');
});

test('R-F3091: a standalone company gets no scope section (no added noise)', () => {
  assert.deepEqual(
    ddEntityScopeLines({ subject_name: 'Acme Ltd', is_subsidiary: false, warnings: [] }), [],
    'the block must not fire on every company');
});

test('R-F3091: an unanchored controller says the chain was not walked', () => {
  const s = flat({
    subject_name: 'Acme Ltd',
    is_subsidiary: true,
    immediate_parent: { name: 'Opaque Holdings SA', registration_number: null, anchored: false },
    ownership_chain_traced: [],
  });
  assert.ok(s.includes('Opaque Holdings SA'));
  assert.ok(s.includes('chain not walked'),
    'an untraceable controller must be labelled, never shown as a clean parent');
});

test('R-F3091: malformed or missing scope never breaks the renderer', () => {
  for (const bad of [null, undefined, {}, 'nonsense', 42, { is_subsidiary: true }]) {
    assert.doesNotThrow(() => ddEntityScopeLines(bad));
  }
});

test('R-F3091: the PDF renders end-to-end with the scope block', async () => {
  const buf = await generateDueDiligencePDF(baseReport({ entity_scope: MITIE_SCOPE }));
  assert.ok(Buffer.isBuffer(buf) && buf.length > 1000, 'PDF must render');
});

test('R-F3091: a report written before this change still renders', async () => {
  const buf = await generateDueDiligencePDF(baseReport());
  assert.ok(Buffer.isBuffer(buf) && buf.length > 1000,
    'a blob with no entity_scope must not break the download');
});
