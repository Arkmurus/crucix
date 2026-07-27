import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/vetting.html', 'utf8');

// R-F3250 — the BS 7858 scope statement governs how everything below it should
// be read, so it sits directly under the title and above the case queue. At the
// foot of the page it was a footnote a user reaches only after forming a view.

test('the scope statement sits above the case queue, not at the foot', () => {
  const title = page.indexOf('class="vt-title"');
  const standard = page.indexOf('id="standard"');
  const queue = page.indexOf('id="queue"');
  const cases = page.indexOf('id="cases"');

  assert.ok(title >= 0 && standard >= 0 && queue >= 0 && cases >= 0);
  assert.ok(title < standard, 'scope statement must follow the page title');
  assert.ok(standard < queue, 'scope statement must precede the queue summary');
  assert.ok(standard < cases, 'scope statement must precede the case list');
});

test('there is exactly one scope statement', () => {
  assert.equal((page.match(/id="standard"/g) || []).length, 1);
});

test('the collapsed line carries the count AND the scope limits together', () => {
  // A coverage figure read on its own reads as completeness. The limit count
  // must not be one click away from the clause count.
  assert.match(page, /id="standard-headline"/);
  assert.match(page, /clauses implemented.*scope limits/);
});

test('the register loads eagerly, so the collapsed line is never a placeholder', () => {
  // Lazily loading it would leave "loading…" under the title until someone
  // happened to expand it, and an unread scope limit is no scope limit.
  assert.match(page, /Promise\.all\(\[loadPacks\(\), loadDocTypes\(\), loadStandard\(\)\]\)/);
  assert.doesNotMatch(page, /addEventListener\('toggle', function once/);
});

test('a failed read says so rather than implying coverage', () => {
  assert.match(page, /coverage unavailable — could not be read/);
});
