// test/no-raw-browser-dialogs-rf3602.test.mjs
//
// R-F3602 — no page may use window.confirm/alert/prompt.
//
// R-F2293 removed them once and R-F3169 removed the last window.prompt from the
// vetting flow, and three had crept back: a prompt() in admin.html and two
// alert()s in design-partners.html. A one-time cleanup that nothing enforces is
// a cleanup that gets undone.
//
// Why ban them rather than tolerate them:
//   - they render as OS chrome with the site's URL printed above the message,
//     which reads like a phishing prompt on a compliance product
//   - they cannot be styled, so they ignore the design system entirely
//   - they block the whole tab
//   - browsers SUPPRESS repeats after the first in a session, so a second
//     failure can vanish with no trace at all
//   - prompt() takes one unlabelled string and cannot validate anything
//
// THE SCAN IS THE HARD PART, and getting it wrong is how this session produced a
// false finding. A first pass counted `confirm()` inside COMMENTS and counted
// `Modal.confirm(...)` member calls, and reported three already-correct pages as
// broken. A second pass matched the word "alert" inside the message string
// `Toast.show('Could not delete alert (HTTP ' + ...)` — where the character
// immediately before is a SPACE and the quote opened 25 characters earlier, so a
// one-character lookbehind is not enough. Comments and string literals are both
// removed before matching, and the scan is asserted non-vacuous below.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const PUBLIC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'public');
const DIALOGS = ['confirm', 'alert', 'prompt'];

function sourceFiles() {
  const out = [];
  for (const f of fs.readdirSync(PUBLIC_DIR)) {
    if (f.endsWith('.html')) out.push(path.join(PUBLIC_DIR, f));
  }
  const js = path.join(PUBLIC_DIR, 'js');
  if (fs.existsSync(js)) {
    for (const f of fs.readdirSync(js)) {
      if (f.endsWith('.js')) out.push(path.join(js, f));
    }
  }
  return out;
}

/** Remove HTML comments, block comments, line comments and STRING CONTENTS. */
function stripStringsAndComments(src) {
  let out = src
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '');

  out = out
    .split(/\r?\n/)
    .filter((line) => {
      const t = line.trim();
      return !t.startsWith('//') && !t.startsWith('*');
    })
    .join('\n');

  // Blank the CONTENTS of string literals, keeping the delimiters so the
  // surrounding syntax still reads correctly.
  out = out
    .replace(/'(?:[^'\\\n]|\\.)*'/g, "''")
    .replace(/"(?:[^"\\\n]|\\.)*"/g, '""')
    .replace(/`(?:[^`\\]|\\.)*`/g, '``');

  return out;
}

function rawDialogCalls(src) {
  const code = stripStringsAndComments(src);
  const hits = [];
  for (const name of DIALOGS) {
    // A preceding '.' means a MEMBER call (Modal.confirm) — a different thing.
    const re = new RegExp('(^|[^.\\w])' + name + '\\s*\\(', 'g');
    for (const m of code.matchAll(re)) {
      const before = code.slice(Math.max(0, m.index - 60), m.index + 1);
      if (/\b(function|async)\s*$/.test(before)) continue;  // a DEFINITION named confirm()
      if (/[,{]\s*$/.test(before)) continue;                 // an object method: confirm({...}) {
      hits.push({ name, line: code.slice(0, m.index).split('\n').length });
    }
  }
  return hits;
}

test('R-F3602 no public page uses window.confirm/alert/prompt', () => {
  const offenders = [];
  for (const file of sourceFiles()) {
    const hits = rawDialogCalls(fs.readFileSync(file, 'utf8'));
    if (hits.length) {
      offenders.push(`${path.basename(file)}: ${hits.map((h) => `${h.name}() line ${h.line}`).join(', ')}`);
    }
  }
  assert.deepEqual(offenders, [],
    'raw browser dialogs found:\n  ' + offenders.join('\n  ')
    + '\nUse the shared Modal in public/js/app.js — Modal.confirm for a decision, '
    + 'Modal.info for a message, Modal.form for input.');
});

test('R-F3602 the scan detects real calls (not vacuous)', () => {
  // A scan that finds nothing passes everything. Skipping this check is how the
  // false finding got reported in the first place.
  const hits = rawDialogCalls('function go(){ if (confirm(1)) alert(2); prompt(3); }');
  assert.equal(hits.length, 3, `expected 3 detections, got ${JSON.stringify(hits)}`);
});

test('R-F3602 the scan ignores comments, strings and member calls', () => {
  const benign = [
    '// we removed the primitive confirm() dialog',
    '/* no confirm() or alert() here */',
    '<!-- prompt() was replaced -->',
    'const ok = await Modal.confirm({ title: 1 });',
    "Toast.show('Could not delete alert (HTTP ' + r.status + ')', 'error');",
    'const msg = "click confirm(yes) to proceed";',
  ].join('\n');
  assert.deepEqual(rawDialogCalls(benign), [],
    'the scan is matching comments, strings or member calls — precisely how three '
    + 'already-correct pages were reported as broken');
});

test('R-F3602 the shared Modal offers a replacement for each of the three', () => {
  // Banning them is only fair if there is somewhere to go.
  const app = fs.readFileSync(path.join(PUBLIC_DIR, 'js', 'app.js'), 'utf8');
  for (const method of ['confirm(', 'info(', 'form(']) {
    assert.ok(app.includes('  ' + method), `Modal.${method.replace('(', '')} is missing`);
  }
});

test('R-F3602 the pages that were fixed actually load the shared helper', () => {
  // Replacing a dialog with Modal.x on a page that never loads app.js swaps a
  // working primitive box for a ReferenceError.
  for (const page of ['admin.html', 'design-partners.html']) {
    const src = fs.readFileSync(path.join(PUBLIC_DIR, page), 'utf8');
    assert.match(src, /js\/app\.js/, `${page} uses Modal but does not load app.js`);
  }
});
