// test/html-interpolation-guard-rf3845.test.mjs
//
// R-F3845 — closes the last open item from the C-18 Node-tier security audit.
//
// C-18 shipped with an honest caveat: aria-brain.html still interpolated ~230
// values that were "reviewed as a class, not proven one by one". This test
// replaces that sentence with a measurement, and — more importantly — keeps it
// true. Every `${…}` inside an HTML-producing template literal on the three
// audited pages must be either escaped or on a NAMED raw-markup list.
//
// ── WHY A GUARD AND NOT JUST A FIX ───────────────────────────────────────────
// The fix is 143 escapes. Without this test the 144th sink someone adds next
// month is unescaped again and nobody finds out — which is how the R-F1919
// inline-handler migration came to miss two handlers in this very file, and how
// the audit's item 10 came to exist at all. Measuring once is not a control;
// measuring on every run is.
//
// ── THE RAW LIST IS DELIBERATELY EXPLICIT ────────────────────────────────────
// A handful of interpolations legitimately emit markup, built earlier in the
// same function from already-escaped parts. Escaping them double-encodes and the
// user reads `&lt;strong&gt;`. They are enumerated below by name: adding one is
// a deliberate edit to this list, not a silent default. The first draft of this
// analysis escaped `sens` and broke the sensor banner — caught by the render
// tests, which is why those are named as the counterweight to this one.
//
// Run: node --test test/html-interpolation-guard-rf3845.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import {
  classifyHtmlInterpolations, classifyConcatOperands, declarationOf,
} from './helpers/html_interpolations.mjs';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');

/** The three files the audit sampled but did not clear. */
const PAGES = ['public/aria-brain.html', 'public/dd-reports.html', 'public/dashboard.html'];

/**
 * Interpolations that MUST stay raw, with why.
 *
 * Every entry is a value assembled from already-escaped parts. If you add one,
 * prove the parts are escaped at their own source first.
 */
const EXPECTED_RAW = {
  'public/aria-brain.html': new Set([
    'critBadge',                 // ' <span …>[CRIT]</span>' or ''
    'dots',                      // pts.map(...) -> <circle> elements
    'fw',                        // ' · <span class="neutral">${escapeHtml(...)}/…</span>'
    'issueNote',                 // '<div …>${escapeHtml(check)}…</div>'
    'note',                      // static '<div …>' advisory block
    'sens',                      // sensor triple; every value inside is escaped
    'unmapped',                  // '<div class="warn">⚠ ${escapeHtml(join)}…</div>'
    "memBits.join(' · ')",       // array of pushed <span> fragments
    "seg('green','#16a34a','healthy')",
    "seg('amber','#ca8a04','degraded')",
    "seg('red','#dc2626','broken')",
    "seg('grey','#dcd8cf','no sensor')",
    "chips || '<span class=\"neutral\">none</span>'",
    "providerChips || '<span class=\"neutral\">No LLM providers configured</span>'",
    'resilience.local_brain_ready ? \'<span class="value good">READY</span>\' : \'<span class="value bad">OFF</span>\'',
    'mem.redis_reachable ? \'<span class="value good">up</span>\' : \'<span class="value bad">down</span>\'',
    "arr.map(_card).join('')",    // the eco-card grid; each card escapes its own fields
  ]),
};

/**
 * Multi-line raw renderers, matched by a STABLE PREFIX.
 *
 * Pasting a 5-line template into a Set makes the allowlist churn on every
 * whitespace change. The prefix is enough to identify the renderer, and the
 * nested-interpolation recursion in the analyser still checks the values INSIDE
 * it — which is how `${g.tier}` was caught unescaped in exactly this expression.
 */
const EXPECTED_RAW_PREFIX = {
  'public/aria-brain.html': [
    'gaps.gaps.slice(0, 12).map(g =>',   // capability-gap chips; g.* all escaped
  ],
};

describe('R-F3845 no unescaped interpolation survives on the audited pages', () => {
  for (const page of PAGES) {
    it(`${page} — every HTML interpolation is escaped or named raw`, () => {
      const r = classifyHtmlInterpolations(read(page));
      const detail = r.unescaped
        .map((u) => `    line ${u.line}: \${${u.expr.slice(0, 80)}}`)
        .join('\n');
      assert.equal(r.unescaped.length, 0,
        `${page} has ${r.unescaped.length} UNESCAPED interpolation(s) inside HTML:\n${detail}\n`
        + '  Wrap each in escapeHtml() (escHtml() on dashboard.html), or — if the value\n'
        + '  is genuinely markup assembled from already-escaped parts — add it to\n'
        + '  EXPECTED_RAW in this file with a one-line justification.');
    });
  }

  it('aria-brain.html actually has a large escaped population (the guard can SEE)', () => {
    // A classifier whose universe is empty always certifies — the failure mode
    // CLAUDE.md §16 records for route_audit. Prove it is still reading the file.
    const r = classifyHtmlInterpolations(read('public/aria-brain.html'));
    assert.ok(r.escaped > 150,
      `only ${r.escaped} escaped interpolations found — the analyser has gone blind`);
  });
});

describe('R-F3845 the raw list is exactly what was justified, no more', () => {
  it('aria-brain.html emits no raw interpolation that is not on the list', () => {
    const r = classifyHtmlInterpolations(read('public/aria-brain.html'));
    const allowed = EXPECTED_RAW['public/aria-brain.html'];
    const prefixes = EXPECTED_RAW_PREFIX['public/aria-brain.html'] || [];
    const unexpected = [...new Set(r.raw)]
      .filter((e) => !allowed.has(e) && !prefixes.some((p) => e.startsWith(p)));
    assert.deepEqual(unexpected, [],
      'these interpolations emit RAW markup and are not justified in EXPECTED_RAW:\n  '
      + unexpected.map((e) => e.slice(0, 90)).join('\n  '));
  });

  it('every justified raw value is assembled from escaped parts', () => {
    // The list is only safe if its members really are built from escaped inputs.
    // Spot-checks the three that carry API-derived text.
    const src = read('public/aria-brain.html');
    for (const ident of ['fw', 'issueNote', 'unmapped']) {
      const decl = declarationOf(src, ident);
      assert.ok(decl.includes('escapeHtml('),
        `${ident} is on the raw list but its declaration escapes nothing — `
        + 'it is a raw sink, not a safe fragment');
    }
  });

  it('sens and critBadge contain no unescaped API value', () => {
    const src = read('public/aria-brain.html');
    // sens interpolates counts only, each escaped; critBadge is a static literal.
    assert.ok(declarationOf(src, 'sens').includes('escapeHtml('),
      'sens must escape the counts it renders');
    assert.ok(!/\$\{(?!escapeHtml)/.test(declarationOf(src, 'critBadge')),
      'critBadge must stay a static fragment with no interpolation');
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// The OTHER half. dd-reports.html builds every row by string concatenation
// (`'<div>' + escText(x) + '</div>'`) and uses no template literals for markup at
// all — so the guard above passes it with nothing to say. A guard whose universe
// is empty always certifies (CLAUDE.md §16, route_audit). This covers it.
//
// Each entry below was resolved to its declaration and triaged by hand. NONE was
// a live sink; the value of the list is that the 26th operand fails the build.
// ─────────────────────────────────────────────────────────────────────────────
const CONCAT_JUSTIFIED = {
  'public/dd-reports.html': new Set([
    // numbers and formatted dates — cannot carry markup
    'L.count_skipped', '_cnt', 'resp.status', 'sec.subcalls',
    'fmtDate(created)', 'lastRun',
    // internal constant maps, never caller data
    'typeIcon', 'm', '_si.banner', 'label',
    // markup assembled from escaped parts
    'chips', 'sharedBadge', '_gaps.map', '_extraGaps.map', 'sv.next_actions.map',
    // `<`-escaped inline on the following line (weaker than escText; text position)
    "String(rep.error || 'see report')",
  ]),
  'public/dashboard.html': new Set([
    '_wlSize', 'n', 'dateStr',              // Number()/count/toLocaleDateString
    'names',                                 // .map(escHtml).join(', ')
    'why', 'emptyMsg', 'icon',               // static literals or escHtml-built
  ]),
  'public/aria-brain.html': new Set([
    'confClass',                             // ternary over CSS class literals
    "missing.join('</code>, <code>')",       // joins an internal key list into markup
  ]),
};

describe('R-F3845 concatenation-built HTML is gated too', () => {
  for (const page of PAGES) {
    it(`${page} — every concat operand next to markup is escaped or justified`, () => {
      const r = classifyConcatOperands(read(page));
      const allowed = CONCAT_JUSTIFIED[page] || new Set();
      const novel = [...new Set(r.unescaped.map((u) => u.expr))].filter((e) => !allowed.has(e));
      const where = r.unescaped
        .filter((u) => novel.includes(u.expr))
        .map((u) => `    line ${u.line}: ${u.expr.slice(0, 80)}`)
        .join('\n');
      assert.deepEqual(novel, [],
        `${page} concatenates ${novel.length} unjustified operand(s) into markup:\n${where}\n`
        + '  Wrap in escText()/escHtml(), or add to CONCAT_JUSTIFIED with a reason.');
    });
  }

  it('the concat analyser can SEE dd-reports.html (it uses no HTML templates)', () => {
    const r = classifyConcatOperands(read('public/dd-reports.html'));
    assert.ok(r.escaped > 60,
      `only ${r.escaped} escaped concat operands found — the analyser has gone blind on `
      + 'the one page that builds all its markup this way');
  });
});

describe('R-F3845 the analyser itself is not fooled', () => {
  it('a `;` inside a CSS string does not truncate a declaration', () => {
    // The bug that made critBadge look plain: scanning to the terminating `;`
    // without skipping string literals stops inside `color:#dc2626;font-size:…`.
    const src = "const x = flag ? ' <span style=\"color:#dc2626;font-size:9px\">Y</span>' : '';";
    assert.ok(declarationOf(src, 'x').includes('</span>'),
      'the declaration must be read past a semicolon inside a string literal');
  });

  it('an array built by .push() is resolved from its pushes, not its []', () => {
    const src = "const bits = [];\nbits.push(`<span class=\"v\">up</span>`);";
    assert.ok(/<span/.test(declarationOf(src, 'bits')),
      'a const bits = [] declaration says nothing — the pushes decide');
  });

  it('a plain value is NOT mistaken for markup', () => {
    const src = "const name = user.display_name || '';";
    assert.ok(!/<\s*\/?\s*[a-zA-Z][^>]*>/.test(declarationOf(src, 'name')));
  });

  it('an unescaped interpolation in an HTML template is detected', () => {
    const bad = '<script>el.innerHTML = `<div>${user.name}</div>`;</script>';
    const r = classifyHtmlInterpolations(bad);
    assert.equal(r.unescaped.length, 1, 'the guard must flag a bare property read');
    assert.equal(r.unescaped[0].expr, 'user.name');
  });

  it('an escaped one is not flagged, and a non-HTML template is ignored', () => {
    assert.equal(
      classifyHtmlInterpolations('`<div>${escapeHtml(user.name)}</div>`').unescaped.length, 0);
    // A fetch URL is a template literal but not HTML — escaping it would corrupt it.
    assert.equal(
      classifyHtmlInterpolations('fetch(`/api/x?id=${id}&q=${q}`)').unescaped.length, 0,
      'only templates containing markup are in scope');
  });
});
