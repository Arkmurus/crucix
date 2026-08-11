// test/escaper-equivalence-rf3863.test.mjs
//
// R-F3863 — six escaper names, one job.
//
//   esc          design-partners.html, leads.html, js/network.js
//   escHtml      dd-reports, sources, vault, wa-connections, js/app.js  (global)
//   escapeHtml   account, aria-brain, explorer, news
//   escText      dd-reports, vls-chain, watchlist
//   escAttr      dd-reports, watchlist
//   escapeText   js/sidebar.js
//
// ── WHY THIS IS A REAL PROBLEM AND NOT UNTIDINESS ────────────────────────────
// The sprawl caused TWO analyser defects in this series. `esc` missing from the
// classifier's name list produced 96 phantom findings on vetting.html, and
// `escapeText` missing produced more on sidebar.js — and in both cases a fixer
// driven off that reading would have DOUBLE-ESCAPED real call sites and printed
// `&amp;lt;` to users. It also hid a genuine weakness: dd-reports' `escAttr`
// escaped only `"` while watchlist's escaped the full set, so two functions with
// the same name behaved differently in different files.
//
// ── WHY EQUIVALENCE AND NOT CONVERGENCE ──────────────────────────────────────
// Deleting five names and rewriting ~15 files is a refactor, which the CURE
// freeze (CLAUDE.md §26) refuses, and it would touch every rendering path at
// once for a cosmetic gain. The HARM is not the count of names — it is that they
// can DIVERGE. This pins behaviour instead: every escaper must produce identical
// output for the same input, so a seventh copy, or a weakened existing one, fails
// here. Convergence stays available later as a deliberate, operator-approved
// cleanup; the danger is gone either way.
//
// Run: node --test test/escaper-equivalence-rf3863.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const PUBLIC = path.join(repoRoot(), 'public');
const VENDORED = new Set(['pelican', 'vendor']);
const NAMES = ['esc', 'escHtml', 'escapeHtml', 'escText', 'escAttr', 'escapeText'];

function sourceFiles() {
  const out = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const f = path.join(dir, e.name);
      if (e.isDirectory()) { if (!VENDORED.has(e.name)) walk(f); continue; }
      if (/\.(html|js)$/i.test(e.name)) out.push(f);
    }
  }(PUBLIC));
  return out.sort();
}

/** Brace-matched body of `function NAME(...)`, or null. */
function extract(src, name) {
  const m = new RegExp(`function\\s+${name}\\s*\\(`).exec(src);
  if (!m) return null;
  let i = src.indexOf('{', m.index);
  let depth = 0;
  for (let j = i; j < src.length; j += 1) {
    if (src[j] === '{') depth += 1;
    else if (src[j] === '}') {
      depth -= 1;
      if (depth === 0) return src.slice(m.index, j + 1);
    }
  }
  return null;
}

/** Every (file, name, callable) escaper defined under public/. */
function allEscapers() {
  const found = [];
  for (const f of sourceFiles()) {
    const src = fs.readFileSync(f, 'utf8');
    const rel = 'public/' + path.relative(PUBLIC, f).split(path.sep).join('/');
    for (const n of NAMES) {
      const body = extract(src, n);
      if (!body) continue;
      // dd-reports' escAttr delegates to escText, so it needs that in scope.
      const dep = /\bescText\s*\(/.test(body) && n !== 'escText' ? extract(src, 'escText') : '';
      let fn;
      try {
        // eslint-disable-next-line no-new-func
        fn = new Function(`${dep}\n${body}\nreturn ${n};`)();
      } catch {
        fn = null;
      }
      found.push({ rel, name: n, body, fn });
    }
  }
  return found;
}

const ESCAPERS = allEscapers();

/** The characters that decide whether a value can break out of HTML. */
const VECTORS = [
  '<script>alert(1)</script>',
  '" onerror="alert(1)',
  "' onerror='alert(1)",
  'a & b',
  '<img src=x onerror=alert(1)>',
  '</textarea><svg onload=alert(1)>',
  '&lt;already escaped&gt;',
  '',
  'plain text',
];

describe('R-F3863 every escaper under public/ is discovered', () => {
  it('finds all six names across the tree', () => {
    const names = new Set(ESCAPERS.map((e) => e.name));
    assert.deepEqual([...names].sort(), [...NAMES].sort(),
      `expected all six escaper names; found ${[...names].join(', ')}`);
  });

  it('every one of them is evaluable (none is a broken copy)', () => {
    const dead = ESCAPERS.filter((e) => typeof e.fn !== 'function')
      .map((e) => `${e.rel}:${e.name}`);
    assert.deepEqual(dead, [], `these escaper definitions could not be evaluated: ${dead.join(', ')}`);
  });

  it('the guard can SEE — a classifier with an empty universe certifies everything', () => {
    assert.ok(ESCAPERS.length >= 15,
      `only ${ESCAPERS.length} escaper definitions found; the scan has gone blind`);
  });
});

describe('R-F3863 all escapers agree, character for character', () => {
  for (const vector of VECTORS) {
    it(`same output for ${JSON.stringify(vector.slice(0, 34))}`, () => {
      const results = new Map();
      for (const e of ESCAPERS) {
        if (typeof e.fn !== 'function') continue;
        const out = e.fn(vector);
        if (!results.has(out)) results.set(out, []);
        results.get(out).push(`${e.rel}:${e.name}`);
      }
      assert.equal(results.size, 1,
        'escapers disagree on this input — two functions with the same job producing '
        + 'different output is how a weak copy hides:\n'
        + [...results.entries()]
          .map(([out, who]) => `    ${JSON.stringify(out.slice(0, 60))}  <- ${who.join(', ')}`)
          .join('\n'));
    });
  }

  it('all five HTML-significant characters are neutralised', () => {
    for (const e of ESCAPERS) {
      if (typeof e.fn !== 'function') continue;
      const out = e.fn(`&<>"'`);
      assert.equal(out, '&amp;&lt;&gt;&quot;&#39;',
        `${e.rel}:${e.name} does not escape the full set — it produced ${JSON.stringify(out)}`);
    }
  });

  it('null and undefined become the empty string, never "null"', () => {
    for (const e of ESCAPERS) {
      if (typeof e.fn !== 'function') continue;
      assert.equal(e.fn(null), '', `${e.rel}:${e.name} renders null literally`);
      assert.equal(e.fn(undefined), '', `${e.rel}:${e.name} renders undefined literally`);
    }
  });

  it('escAttr is no longer the weak one', () => {
    // dd-reports' escAttr escaped ONLY `"`. Every call site sits in a
    // double-quoted attribute so it was not exploitable, but it was one quote
    // style away from a breakout and left `&` raw.
    const attrs = ESCAPERS.filter((e) => e.name === 'escAttr' && typeof e.fn === 'function');
    assert.ok(attrs.length >= 2, 'expected escAttr in dd-reports and watchlist');
    for (const a of attrs) {
      assert.equal(a.fn(`x'y`), 'x&#39;y', `${a.rel}: escAttr must escape the single quote`);
      assert.equal(a.fn('a&b'), 'a&amp;b', `${a.rel}: escAttr must escape the ampersand`);
    }
  });
});

describe('R-F3863 the analyser knows every name that exists', () => {
  it('ESCAPER_NAMES covers every escaper defined under public/', async () => {
    // The omission that started this: a name the classifier does not know is
    // reported as an UNESCAPED sink, and a fixer acting on that double-escapes a
    // call site that was already correct.
    const mod = await import('./helpers/html_interpolations.mjs');
    const known = new Set();
    // ESCAPER_NAMES is module-private; probe it through observable behaviour.
    for (const n of NAMES) {
      const r = mod.classifyHtmlInterpolations(`x.innerHTML = \`<b>\${${n}(v)}</b>\`;`);
      if (r.unescaped.length === 0) known.add(n);
    }
    assert.deepEqual([...known].sort(), [...NAMES].sort(),
      `the analyser does not recognise: ${NAMES.filter((n) => !known.has(n)).join(', ')} `
      + '— those would be reported as unescaped sinks');
  });
});
