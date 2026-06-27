// R-F2061 — WhatsApp: ARIA responds ONLY when called (her name is mentioned).
//
// Operator rule (2026-06-27): "aria should only react when her name is mentioned …
// the rule of thumb is for her to only respond when she is called." The acute
// symptom: a photo shared in a group was reviewed UNINVITED. Root fix gates the
// media REVIEW paths (image + document) and the keyword auto-response on a name
// mention, code-enforced so the live WA_LISTENER_AUTO_RESPOND=true secret can't
// re-open the uninvited path.
//
// The listener module can't be imported standalone (its `redis` dep lives in the
// aria-wa app, not the repo root), so this is a source-contract + predicate guard:
// it fails if any gate is removed, and it pins the called-vs-not-called rule.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const SRC = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'services', 'wa-listener', 'aria_wa_listener.mjs'),
  'utf8',
);

test('the called-gate is defined from the name-mention patterns', () => {
  assert.match(SRC, /const _ariaCalled = MENTIONS_RE\.some\(\(p\) => p\.test\(text \|\| ''\)\)/);
});

test('image review is gated on a mention (no uninvited photo review)', () => {
  const img = SRC.slice(SRC.indexOf('if (imgMsg) {'));
  const dl = img.indexOf('downloadMediaMessage');
  const gate = img.indexOf('if (!_ariaCalled) continue');
  assert.ok(gate !== -1, 'image block must have the !_ariaCalled gate');
  assert.ok(gate < dl, 'the gate must come BEFORE the media download (skip uninvited images)');
});

test('document review is gated on a mention', () => {
  const doc = SRC.slice(SRC.indexOf('if (docMsg) {'));
  const dl = doc.indexOf('downloadMediaMessage');
  const gate = doc.indexOf('if (!_ariaCalled) continue');
  assert.ok(gate !== -1 && gate < dl, 'document block must gate on !_ariaCalled before download');
});

test('keyword auto-response is decoupled from the live AUTO_RESPOND secret', () => {
  // must NOT fire on the legacy WA_LISTENER_AUTO_RESPOND flag alone…
  assert.doesNotMatch(SRC, /if \(AUTO_RESPOND && !_isFromMe\) \{/);
  // …it now needs the explicit, default-OFF flag.
  assert.match(SRC, /const KEYWORD_AUTO_RESPONSE = \(process\.env\.WA_KEYWORD_AUTO_RESPONSE \|\| 'false'\)/);
  assert.match(SRC, /if \(KEYWORD_AUTO_RESPONSE && !_isFromMe\) \{/);
});

test('the mention predicate classifies "called" vs "not called" correctly', () => {
  // mirrors MENTIONS_RE in the listener (line ~1835)
  const MENTIONS_RE = [/\bar[iy]{1,3}a\b/i, /@ar[iy]{1,3}a/i, /^ar[iy]{1,3}a[,:]/i];
  const called = (t) => MENTIONS_RE.some((p) => p.test(t || ''));

  // CALLED — she should respond
  assert.equal(called('Aria, review this NDA'), true);
  assert.equal(called('@aria what do you think of this image'), true);
  assert.equal(called('aria: summarise the contract'), true);

  // NOT CALLED — she must stay silent (incl. the photo + compliance-keyword cases)
  assert.equal(called('check out this photo'), false);
  assert.equal(called(''), false);                              // bare image, no caption
  assert.equal(called('we need an export license for this deal'), false); // keyword, no name
  assert.equal(called('please review the agreement'), false);  // doc reference, no name
});
