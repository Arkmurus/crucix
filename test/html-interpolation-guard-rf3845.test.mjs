// test/html-interpolation-guard-rf3845.test.mjs
//
// R-F3845 — closed the C-18 XSS residual on the three audited pages.
// R-F3850 — extends the same guard to EVERY page under public/.
//
// The invariant, for all 32 served pages: every `${…}` inside an HTML-producing
// template literal, and every concatenation operand adjacent to markup, is
// either escaped or justified BY NAME here.
//
// ── WHY A GUARD AND NOT JUST A FIX ───────────────────────────────────────────
// The fix is 246 escapes. Without this test the next one someone adds is
// unescaped again and nobody finds out — which is how the R-F1919 inline-handler
// migration came to miss two handlers, and how the audit's item 10 existed at
// all. Measuring once is not a control; measuring on every run is.
//
// ── THE TWO STYLES ───────────────────────────────────────────────────────────
// These pages do not agree on how they build HTML. aria-brain/explorer/vetting
// use template literals; dd-reports/sources/leads concatenate strings; several
// do both. A template-only analyser reports zero on dd-reports and looks like a
// pass — the "guard whose universe is empty always certifies" failure CLAUDE.md
// §16 records for route_audit. Both styles are classified.
//
// ── AND THE THREE ESCAPING CONVENTIONS ───────────────────────────────────────
// `escHtml` is global via js/app.js; dd-reports/watchlist define escText/escAttr;
// vetting/leads/design-partners define `esc`; aria-brain/explorer/account/news
// define `escapeHtml`. The analyser knows all of them — omitting one does not
// weaken the guard, it makes it report ALREADY-escaped sinks and a fixer driven
// off that double-escapes them into `&amp;lt;` for the user.
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

/**
 * EVERY page this server serves, discovered — not a hand-kept list.
 *
 * R-F3850 widened the guard from the three audited files to all of public/.
 * R-F3852 added the shared JS modules (js/app.js, js/network.js, js/sidebar.js).
 * Those build HTML too, and skipping them is how an unescaped `${msg}` sat in
 * the Toast component EVERY page uses.
 * A hardcoded list is the same failure as a classifier with an empty universe:
 * the page someone adds next month is not in it, so it is never checked.
 * `pelican/` and `vendor/` are vendored third-party themes and are excluded by
 * name (see the R-F3840 tests for how their jQuery is bounded separately).
 */
const VENDORED = new Set(['pelican', 'vendor']);
function allPages() {
  const out = [];
  (function walk(dir) {
    for (const e of fs.readdirSync(path.join(repoRoot(), dir), { withFileTypes: true })) {
      const rel = `${dir}/${e.name}`;
      if (e.isDirectory()) { if (!VENDORED.has(e.name)) walk(rel); continue; }
      if (/\.(html|js)$/i.test(e.name)) out.push(rel);
    }
  }('public'));
  return out.sort();
}
const PAGES = allPages();

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

describe('R-F3850 no unescaped interpolation survives on ANY page in public/', () => {
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
  'public/account.html': new Set([
    '(cap || 0)',
    "(f.on ? 'check-circle-fill' : 'dash-circle')",
    "(warn ? ' warn' : '')",
    '_sym(t.currency)',
    'ctaBtn',
    'f.txt',
    'label',
    't.label',
    't.priceAmount',
    'value',
  ]),
  'public/admin.html': new Set([
    'fmtDateTime(u.lastLogin)',
    'initials',
    'r',
  ]),
  'public/aria-brain.html': new Set([
    'confClass',
  ]),
  'public/aria.html': new Set([
    "(thinkMode ? 'Analysing…' : 'Thinking…')",
    'c',
    'cells(row)',
    'code.trim()',
    'fmtTime(tsOf(c))',
    'footHtml',
    'html',
    'ico',
    "l.replace(/^[-•] /, '')",
    "l.replace(/^\\d+\\. /, '')",
    'statusHtml',
  ]),
  'public/bd-intelligence.html': new Set([
    "a.steps.map(function(s,i){return (i+1)+'. '+escHtml(s);})",
    'col',
    'label',
    'lbl',
    'sl.patternsObserved.map(function(p){return escHtml(p);})',
    'val',
  ]),
  'public/dashboard.html': new Set([
    '(Number(o.score) || 0)',
    '_wlSize',
    'dateStr',
    'emptyMsg',
    'icon',
    'n',
    'names',
    'why',
  ]),
  'public/dd-reports.html': new Set([
    'L.count_skipped',
    "String(rep.error || 'see report')",
    '_cnt',
    '_si.banner',
    'chips',
    'fmtDate(created)',
    'label',
    'lastRun',
    'm',
    'resp.status',
    'sec.subcalls',
    'sharedBadge',
    'typeIcon',
  ]),
  'public/design-partners.html': new Set([
    'applied',
    'fmtDate(acct.credentialIssuedAt)',
    'fmtDate(e.created_at)',
    'qualified',
    's',
  ]),
  'public/js/app.js': new Set([
    "(f.html || escHtml(f.value || ''))",
    '(f.options || [])',
    'bodyHtml',
    'fields.map(fieldHtml)',
    'icon',
    'input',
  ]),
  'public/js/sidebar.js': new Set([
    'resp.status',
    'ts',
  ]),
  'public/lead-verify.html': new Set([
    'html',
  ]),
  'public/leads.html': new Set([
    'fmtDate(l.created_at)',
    'fmtDate(lead.verification.expires_at)',
    'fmtDate(lead.verified_at)',
  ]),
  'public/news.html': new Set([
    '(data.articles_new || 0)',
    'count',
    'formatTime(a.detected_at || a.published)',
    'known',
    'n',
  ]),
  'public/sources.html': new Set([
    'badgeClass',
    'bigPct',
    'c',
    'count',
    'empty',
    'filled',
    'label',
    'lastTs',
    'n',
    'pct',
    'pending',
    's.filled',
    's.total',
    'state.badgeClass',
    'state.badgeLabel',
    'stateLabel',
    'total',
    'trendHtml',
  ]),
  'public/vault.html': new Set([
    'Object.values(counts)',
    'colorHint',
  ]),
  'public/vetting-portal.html': new Set([
    "(isReferee ? 'Confirm an engagement' : 'Upload your documents')",
    'o.still_needed',
  ]),
  'public/vls-chain.html': new Set([
    'r.status',
    'total',
    'v',
  ]),
  'public/wa-connections.html': new Set([
    "(data.account?.status === 'connected' ? 'Already connected. No QR needed' : 'No QR code yet. Try again in a few seconds.')",
    'html',
  ]),
  'public/watchlist.html': new Set([
    'changes',
    'errs',
    'label',
    'new',
    'r.status',
    'scanned',
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

// ─────────────────────────────────────────────────────────────────────────────
// WHY "raw" IS NOT A HOLE.
//
// `EXPECTED_RAW` enumerates the raw expressions on aria-brain.html, the most
// complex page. It does NOT enumerate the ~127 raw classifications across the
// other pages, and that is deliberate rather than an omission: the classifier
// only calls something raw on POSITIVE PROOF that it is markup — the expression
// contains a tag, or resolves to a variable/function whose body contains one.
//
// The obvious worry is a markup-emitting helper that interpolates an unescaped
// value inside itself: `function statusHtml(s){ return '<span>'+s+'</span>' }`.
// The CALL is raw, so the call site says nothing about `s`. What makes that safe
// is that both classifiers scan the WHOLE FILE — including function bodies — so
// the helper's own sink is reported at its DEFINITION. These tests pin that,
// because the argument is what the missing allowlist rests on.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3845 a raw call does not hide the sink inside the helper', () => {
  it('an unescaped value inside a markup-returning function IS reported (concat)', () => {
    const src = `<script>
      function statusHtml(s) { return '<span class="pill">' + s + '</span>'; }
      el.innerHTML = '<div>' + statusHtml(x) + '</div>';
    </script>`;
    const r = classifyConcatOperands(src);
    assert.ok(r.raw.includes('statusHtml(x)'),
      'the call itself is markup and must classify raw');
    assert.deepEqual(r.unescaped.map((u) => u.expr), ['s'],
      'the unescaped operand INSIDE the helper must still be reported');
  });

  it('an unescaped value inside a markup-returning function IS reported (template)', () => {
    const src = '<script>'
      + 'const card = (v) => `<div class="c">${v.name}</div>`;'
      + 'el.innerHTML = `<ul>${items.map(card).join("")}</ul>`;'
      + '</script>';
    const r = classifyHtmlInterpolations(src);
    assert.deepEqual(r.unescaped.map((u) => u.expr), ['v.name'],
      'the helper body is scanned even though the call site is raw');
    assert.ok(r.raw.some((e) => e.includes('items.map(card)')),
      'an ARROW helper that emits markup must classify raw, like a function declaration');
  });

  it('raw requires PROOF of markup — a plain helper is never raw', () => {
    const src = "<script>function fmt(d){ return String(d).trim(); }"
      + " el.innerHTML = '<td>' + fmt(x) + '</td>';</script>";
    const r = classifyConcatOperands(src);
    assert.deepEqual(r.raw, [], 'fmt() emits no markup, so it must not be excused as raw');
    assert.deepEqual(r.unescaped.map((u) => u.expr), ['fmt(x)']);
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
