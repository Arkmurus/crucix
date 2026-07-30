import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/vetting.html', 'utf8');

// R-F3266 — the officer's side of the pack-upgrade path. A case pinned to an
// old pack version showed "Required documents: none defined" with nothing to
// do about it. The server can now re-pin it; this is the control that offers
// the move, and the properties it must not lose.

const grab = (name) => page.match(new RegExp(`function ${name}\\([\\s\\S]*?\\n  \\}`))[0];

test('the upgrade is offered when a newer pack version exists', () => {
  assert.match(page, /function newerPackFor/);
  assert.match(page, /data-migrate=/, 'no upgrade control is rendered');
  assert.match(page, /Update rules to v/);
});

test('the empty state is not a dead end: it says what to do about the pin', () => {
  // R-F3472 — asserts the PROPERTY, not the sentence.
  //
  // This pinned the exact markup `is available</strong> and does define`. R-F3466
  // reworded the empty state while making vetting BS-7858-only, so the assertion went
  // red although the property it exists to protect was intact — and, in fact,
  // strengthened: the new copy adds "Do not treat it as BS 7858 complete", which is a
  // never-false-clean statement the old wording did not make.
  //
  // The property R-F3266 owns is that a case pinned to an old pack is NOT a dead end:
  // the officer is told the pinned state is not completeness, and is given a route
  // forward. Both halves are asserted here, and the route itself is covered by the
  // data-migrate / "Update rules to v" assertions above, so a silent removal of the
  // upgrade control still fails this file.
  const empty = page.match(/This historical case[\s\S]{0,400}?<\/div>/);
  assert.ok(empty, 'the historical-case empty state is gone entirely');
  const text = empty[0].replace(/\s+/g, ' ');
  assert.match(text, /not treat it as BS ?7858 complete/i,
    'the empty state no longer warns that a pinned case is not completeness');
  assert.match(text, /bring it onto the active standard/i,
    'the empty state no longer states the route forward');
});

test('THE numeric-version property: 1.10.0 must beat 1.9.0', () => {
  // A string compare puts "1.9.0" above "1.10.0", so the upgrade would quietly
  // stop being offered at the tenth revision with nothing failing to say so.
  // That is R-F3175's bug, on the client this time.
  const f = new Function('PACKS',
    [grab('packVersionKey'), grab('packVersionNewer')].join('\n')
    + '; return packVersionNewer;')([]);
  assert.equal(f('1.10.0', '1.9.0'), true);
  assert.equal(f('1.3.0', '1.3.0'), false, 'equal is not newer');
  assert.equal(f('1.1.0', '1.3.0'), false);
});

test('it never offers a different framework as an upgrade', () => {
  // uk_bs7858 -> intl_baseline is a different standard, not a newer version.
  const PACKS = [
    { pack_id: 'uk_bs7858', version: '1.3.0' },
    { pack_id: 'intl_baseline', version: '9.9.9' },
  ];
  const f = new Function('PACKS',
    [grab('packVersionKey'), grab('packVersionNewer'), grab('newerPackFor')].join('\n')
    + '; return newerPackFor;')(PACKS);
  assert.equal(f({ pack: { pack_id: 'pt_generic', version: '0.1.0' } }), null);
  assert.equal(f({ pack: { pack_id: 'uk_bs7858', version: '1.3.0' } }), null,
    'a case already on the newest version must not be offered a move');
  assert.equal(f({}), null, 'missing pack info must not produce an offer');
});

test('only PRODUCTION packs can be offered', () => {
  // PACKS is filtered to PRODUCTION at load. A DRAFT pack has not been legally
  // reviewed for the jurisdiction it claims; the server refuses one, and the
  // UI must not put the button in front of the officer either.
  assert.match(page, /PACKS = .*filter\(\(p\) => p\.status === 'PRODUCTION'\)/);
});

test('the move requires a named person and states what it does', () => {
  const fn = grab('migratePack');
  assert.match(fn, /migrated_by/, 'the change must be attributable');
  assert.match(fn, /required: true/);
  assert.match(fn, /untouched/, 'the dialog must say evidence is not affected');
  assert.match(fn, /re-running|re-assess/i, 'the dialog must say the verdict goes stale');
  assert.match(fn, /cannot be undone|never moves backwards/i);
});

test('a refusal shows the served reason, not a generic failure', () => {
  // Every 409 from this route carries a sentence explaining the refusal.
  // Swallowing it would leave the officer with a button that does nothing.
  const fn = grab('migratePack');
  assert.match(fn, /detail\.message/);
});

test('it POSTs, and never migrates on a page load', () => {
  const fn = grab('migratePack');
  assert.match(fn, /pack\/migrate/);
  assert.match(fn, /method: 'POST'/);
  // Nothing may call it except the officer's click.
  const calls = page.match(/migratePack\(/g) || [];
  assert.equal(calls.length, 2, 'migratePack must be defined once and called once (the click)');
});

test('the queue is reloaded after a move so the card version is not stale', () => {
  assert.match(grab('migratePack'), /loadCases\(\)/);
});
