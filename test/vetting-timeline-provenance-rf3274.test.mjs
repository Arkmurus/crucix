import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/vetting.html', 'utf8');

// R-F3274 — the officer's side of documents reaching the timeline. The grid
// says WHICH months are covered; it cannot say which periods a human typed and
// which a model read off a scan. Once extraction can propose periods, that is
// the first question an officer has to be able to ask.

test('the periods the grid is computed from are rendered', () => {
  assert.match(page, /function periodsSection/);
  assert.match(page, /periodsSection\(a\)/, 'the section is defined but never painted');
  assert.match(page, /Declared periods/);
});

test('an extracted period is labelled as extracted, not passed off as typed', () => {
  // An unattributed period on a screening file is an assertion with nobody
  // behind it.
  const fn = page.match(/function periodsSection[\s\S]*?\n  \}/)[0];
  assert.match(fn, /EXTRACTED_FROM_DOCUMENT/);
  assert.match(fn, /not confirmed by a person/i);
});

test('the empty grid explains itself instead of just being blank', () => {
  // "0 verified · 0 declared · 61 uncovered" with nothing else on screen reads
  // as a broken page. It is a file with no periods on it.
  const fn = page.match(/function periodsSection[\s\S]*?\n  \}/)[0];
  assert.match(fn, /no career periods are on this file yet/i);
  assert.match(fn, /application form/i, 'it must say how to populate it');
});

test('no period state is rendered as plain "verified" when it is not', () => {
  const fn = page.match(/const PERIOD_STATE[\s\S]*?\n  \};/)[0];
  assert.match(fn, /EVIDENCE_RECEIVED[^\n]*not yet verified/,
    'evidence received must never read as verified');
  assert.match(fn, /VERIFICATION_FAILED/);
  assert.match(fn, /UNVERIFIED[^\n]*no evidence yet/);
});

test('the upload dialog no longer claims PDFs cannot be read', () => {
  // R-F3265 made that text false. A page asserting a limitation the product no
  // longer has is its own kind of dishonesty.
  assert.doesNotMatch(page, /Nothing here reads a PDF/);
  assert.match(page, /PDF, scan, photo, DOCX, or an email and/);
});

test('the upload result says what the document did to the timeline', () => {
  // A bare "stored" cannot be told apart from "filed and ignored".
  const fn = page.match(/async function upload\([\s\S]*?\n  \}/)[0];
  assert.match(fn, /body\.timeline/);
  // Matched case-insensitively, deliberately. The first cut pinned the exact
  // wording, and the R-F3278 copy sweep then legitimately rewrote a full stop
  // as a comma, which turned "Evidence" into "evidence" and failed a test
  // about SUBSTANCE on a question of typography. A guard must not make a copy
  // edit look like a regression.
  assert.match(fn, /declared period\(s\) read from it/i);
  assert.match(fn, /evidence against/i);
  assert.match(fn, /not applied to the timeline/i,
    'a document that changed nothing must say why');
});

test('the attach-to-period help reflects that matching is now automatic', () => {
  assert.match(page, /is matched to the/);
  assert.match(page, /overlapping periods on its own; naming one here overrides that/);
});
