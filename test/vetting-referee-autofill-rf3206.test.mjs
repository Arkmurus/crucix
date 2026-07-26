/**
 * R-F3206 — the share dialog made the officer TYPE a referee the applicant had
 * already nominated on the application form.
 *
 * Re-keying data the file already holds is how a referee link goes to the wrong
 * address, and a referee link exposes one engagement — so that is a disclosure,
 * not a typo.
 *
 * These tests exercise the auto-fill RULE extracted from public/vetting.html.
 * The rule that matters is the one that is easy to get wrong: it must never
 * overwrite something the officer typed. A gap period has no nominated referee,
 * the officer names one by hand, and changing period afterwards must not wipe it.
 */
import { test } from 'node:test';
import assert from 'node:assert/strict';

/** Mirrors the `apply` closure in public/vetting.html (R-F3206). */
function makeAutofill(refByEntry) {
  const state = { name: '', to: '' };            // what WE last wrote
  return function apply(sel, nameEl, toEl, kind) {
    if (kind !== 'REFEREE') return;
    const nom = refByEntry[sel.value];
    if (!nom) return;
    if (!nameEl.value || nameEl.value === state.name) {
      nameEl.value = nom.name; state.name = nom.name;
    }
    if (!toEl.value || toEl.value === state.to) {
      toEl.value = nom.to; state.to = nom.to;
    }
  };
}

const NOMS = {
  e1: { name: 'Dana Okafor', to: 'dana@acme.example', title: 'Line manager' },
  e2: { name: 'Sam Reyes', to: 'sam@globex.example', title: 'HR' },
  gap: { name: '', to: '' },                     // unemployment: nobody nominated
  nocontact: { name: 'Pat Lee', to: '' },        // named, but unreachable
};

test('selecting a period fills the recipient from the nomination', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'e1' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, 'Dana Okafor');
  assert.equal(toEl.value, 'dana@acme.example');
});

test('changing period replaces a value WE filled', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'e1' }, nameEl, toEl, 'REFEREE');
  apply({ value: 'e2' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, 'Sam Reyes');
  assert.equal(toEl.value, 'sam@globex.example');
});

test('NEVER clobbers what the officer typed', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'e1' }, nameEl, toEl, 'REFEREE');
  nameEl.value = 'Manually Named Referee';       // gap referee, typed by hand
  toEl.value = 'manual@example.com';
  apply({ value: 'e2' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, 'Manually Named Referee',
    'R-F3206 REGRESSION: a hand-typed referee was overwritten by the auto-fill');
  assert.equal(toEl.value, 'manual@example.com');
});

test('a period with no nominated referee clears our value, leaving the box empty', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'e1' }, nameEl, toEl, 'REFEREE');
  apply({ value: 'gap' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, '', 'a gap period must not carry the previous referee over');
  assert.equal(toEl.value, '');
});

test('a nomination with no contact fills the name and leaves the address empty', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'nocontact' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, 'Pat Lee');
  assert.equal(toEl.value, '', 'no address on file means nothing to send to');
});

test('applicant links are never auto-filled with a referee', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: '' }, toEl = { value: '' };
  apply({ value: 'e1' }, nameEl, toEl, 'APPLICANT');
  assert.equal(nameEl.value, '');
  assert.equal(toEl.value, '', 'an applicant link must not be addressed to a referee');
});

test('an unknown period id is a no-op', () => {
  const apply = makeAutofill(NOMS);
  const nameEl = { value: 'x' }, toEl = { value: 'y' };
  apply({ value: 'does-not-exist' }, nameEl, toEl, 'REFEREE');
  assert.equal(nameEl.value, 'x');
  assert.equal(toEl.value, 'y');
});
