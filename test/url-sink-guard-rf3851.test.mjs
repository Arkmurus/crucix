// test/url-sink-guard-rf3851.test.mjs
//
// R-F3851 — the second DOM-XSS surface: URLs.
//
// R-F3845/R-F3850 proved every interpolation into HTML is escaped. Escaping is
// the wrong tool for a URL: `javascript:alert(1)` contains no `<`, `>`, `&` or
// quote, so it passes through every escaper unchanged and fires on click — or
// immediately, when assigned to `window.location.href`.
//
// This guard covers the three sink shapes across every first-party page:
//   href="${…}" / src="${…}"   in a template literal
//   href=' + x                 in a concatenation
//   el.href = x / el.src = x   direct DOM property assignment
//
// Each must route through a SCHEME ALLOWLIST — `safeHref` (public/js/app.js:566,
// http/https/mailto else '#') client-side, or `safeExternalUrl`
// (lib/util/safeUrl.mjs) server-side — or be justified below by name.
//
// ── WHAT THIS FOUND ──────────────────────────────────────────────────────────
// Two live sinks, both navigations, both reached from server data:
//   * explorer.html — `window.location.href = a.href` where `a` is an entry from
//     the ACTIONS API payload, not a DOM anchor. The property name made it look
//     like a resolved anchor href; it is attacker-reachable data.
//   * account.html ×2 — the checkout/portal URL from our billing API assigned
//     straight to window.location.href.
// Assigning a `javascript:` URL to window.location.href EXECUTES it, so neither
// was cosmetic.
//
// Run: node --test test/url-sink-guard-rf3851.test.mjs

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

/** Length-preserving comment blanking, so this file's own prose is not scanned. */
const blankComments = (s) => s.replace(/(^|[^:])(\/\/[^\n]*)/g, (_, p, c) => p + ' '.repeat(c.length));

const SAFE = /(safeHref|safeUrl|safeExternalUrl|encodeURIComponent)\s*\(/;

/**
 * Sinks that do NOT need a scheme allowlist, with why.
 *
 * Keyed by file, valued by the expression text. Each was resolved to its source
 * before being listed — a URL sink is justified only by knowing where the URL
 * comes from, never by how it looks.
 */
const JUSTIFIED = {
  'public/account.html': new Set([
    'fr.result',            // FileReader data: URL of the user's OWN chosen file (avatar preview)
  ]),
  'public/aria.html': new Set([
    'url',                  // URL.createObjectURL(blob) — a blob: download link
  ]),
  'public/dd-reports.html': new Set([
    'url',                  // URL.createObjectURL(blob) — report download
  ]),
  'public/vetting.html': new Set([
    'url',                  // URL.createObjectURL(blob) — subject-access export
  ]),
  'public/model-card.html': new Set([
    'frame.dataset.src',    // static data-src written in this page's own markup
  ]),
  'public/signin.html': new Set([
    // ternary between two literal paths (/set-password.html : /dashboard.html)
    '(result.data.user && result.data.user.mustChangePassword)',
  ]),
  'public/js/network.js': new Set([
    'esc(user.avatarUrl)',  // <img src>: a javascript: URL does not execute in img/src,
                            // and CSP img-src is 'self' data: blob: so it cannot even load
    'fr.result',            // FileReader data: URL of the user's own file
  ]),
  'public/js/sidebar.js': new Set([
    // link() parameter; every caller (sidebar.js:319+) passes a literal
    // '/page.html'. R-F3852 additionally HTML-escapes it for the attribute; that
    // is the correct tool for attribute-breakout but not for schemes, hence the
    // justification here rather than a safeHref() wrap.
    'escapeText(href)',
  ]),
};

function scan(file) {
  const src = blankComments(fs.readFileSync(file, 'utf8'));
  const hits = [];
  const add = (kind, expr, idx) => hits.push({
    kind, expr: expr.trim(), line: src.slice(0, idx).split('\n').length,
  });
  for (const m of src.matchAll(/\b(href|src)\s*=\s*["']?\$\{([^}]{1,150})\}/g)) {
    if (!SAFE.test(m[2])) add('template', m[2], m.index);
  }
  for (const m of src.matchAll(/\b(href|src)\s*=\s*(?:\\?["'])\s*\+\s*([A-Za-z_$][\w$.]*(?:\([^()]*\))?)/g)) {
    if (!SAFE.test(m[2])) add('concat', m[2], m.index);
  }
  for (const m of src.matchAll(/\.(href|src)\s*=\s*([^;\n]{1,120})/g)) {
    const v = m[2].trim();
    if (/^['"`]/.test(v) || SAFE.test(v) || /^(location|window|document)\b/.test(v)) continue;
    add('dom', v, m.index);
  }
  return hits;
}

function allFiles() {
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

describe('R-F3851 every data-derived URL sink is scheme-checked or justified', () => {
  for (const file of allFiles()) {
    const rel = 'public/' + path.relative(PUBLIC, file).split(path.sep).join('/');
    it(`${rel}`, () => {
      const allowed = JUSTIFIED[rel] || new Set();
      const novel = scan(file).filter((h) => !allowed.has(h.expr));
      const detail = novel.map((h) => `    line ${h.line} (${h.kind}): ${h.expr.slice(0, 80)}`).join('\n');
      assert.deepEqual(novel.map((h) => h.expr), [],
        `${rel} has ${novel.length} URL sink(s) with no scheme allowlist:\n${detail}\n`
        + '  Wrap in safeHref() — escaping does NOT stop `javascript:` — or add to\n'
        + '  JUSTIFIED in this file, naming where the URL comes from.');
    });
  }
});

describe('R-F3851 the two live sinks stay fixed', () => {
  const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');

  it('explorer.html routes the actions-payload href through safeHref', () => {
    const s = blankComments(read('public/explorer.html'));
    assert.ok(!/window\.location\.href\s*=\s*a\.href\s*;/.test(s),
      'the raw assignment is back — `a` is API data, not a DOM anchor');
    assert.ok(/window\.location\.href\s*=\s*safeHref\(a\.href\)/.test(s));
  });

  it('account.html routes both billing redirects through safeHref', () => {
    const s = blankComments(read('public/account.html'));
    assert.equal((s.match(/window\.location\.href\s*=\s*safeHref\(data\.url\)/g) || []).length, 2,
      'both the checkout and the portal redirect must be scheme-checked');
    assert.ok(!/window\.location\.href\s*=\s*data\.url\s*;/.test(s));
  });

  it('both pages can actually reach safeHref', () => {
    // Wrapping in a function the page does not load is a ReferenceError, i.e. a
    // broken redirect rather than a safe one.
    for (const p of ['public/account.html', 'public/explorer.html']) {
      assert.ok(read(p).includes('js/app.js'), `${p} must load js/app.js for safeHref`);
    }
    assert.ok(read('public/js/app.js').includes('function safeHref'),
      'the shared client-side allowlist must still exist');
  });
});

describe('R-F3851 safeHref is a real allowlist', () => {
  // Behaviour is asserted against the SHIPPED source, extracted and evaluated,
  // rather than a reimplementation.
  const src = fs.readFileSync(path.join(PUBLIC, 'js/app.js'), 'utf8');
  const at = src.indexOf('function safeHref');
  const body = src.slice(at, src.indexOf('\n}', at) + 2);
  // eslint-disable-next-line no-new-func
  const safeHref = new Function(`${body}; return safeHref;`)();

  it('passes ordinary links through unchanged', () => {
    for (const ok of ['https://gov.uk/x', 'http://a.b/c?d=e#f', 'mailto:a@b.com', 'HTTPS://X.Y']) {
      assert.equal(safeHref(ok), ok);
    }
  });

  it('refuses every script-bearing scheme, failing closed to #', () => {
    for (const bad of [
      'javascript:alert(1)', 'JaVaScRiPt:alert(1)', '  javascript:alert(1)',
      'java\tscript:alert(1)', 'java\nscript:alert(1)', ' javascript:alert(1)',
      'data:text/html,<script>alert(1)</script>', 'vbscript:msgbox(1)',
      'file:///etc/passwd', 'blob:https://x/y',
    ]) {
      assert.equal(safeHref(bad), '#', `${JSON.stringify(bad)} must not survive`);
    }
  });

  it('handles junk without throwing', () => {
    for (const bad of [undefined, null, 0, {}, [], '', '   ']) {
      assert.equal(typeof safeHref(bad), 'string');
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// THE VENDORED SURFACE, bounded rather than hand-waved.
//
// public/pelican/ is a third-party marketing theme (jQuery 2.1.1, bootstrap,
// owl-carousel) loaded by exactly one page: public/index.html, the public
// landing page. jQuery 2.1.1 carries known XSS CVEs (2015-9251, 2020-11022/23)
// — every one of which needs UNTRUSTED INPUT to reach jQuery's HTML PARSER.
//
// Measured 2026-08-11: no such path exists. index.html has no inline script and
// makes no fetch of its own; the theme's only dynamic surface is the lead form,
// whose success and error messages — including `xhr.responseJSON.error` from our
// own API — are written with jQuery `.text()`, which sets textContent and parses
// nothing.
//
// These tests keep that true. They do NOT claim jQuery 2.1.1 is safe in general.
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3852 the vendored theme has no untrusted-input path into jQuery', () => {
  const THEME_JS = ['public/pelican/assets/js/custom.js', 'public/pelican/assets/js/plugins.js'];
  const readIf = (f) => {
    const p = path.join(repoRoot(), f);
    return fs.existsSync(p) ? blankComments(fs.readFileSync(p, 'utf8')) : null;
  };

  it('only index.html loads the theme', () => {
    const users = [];
    for (const f of allFiles()) {
      if (!/\.html$/i.test(f)) continue;
      if (/pelican\/assets/.test(fs.readFileSync(f, 'utf8'))) {
        users.push('public/' + path.relative(PUBLIC, f).split(path.sep).join('/'));
      }
    }
    assert.deepEqual(users, ['public/index.html'],
      'another page now loads the vendored theme — re-assess its jQuery exposure');
  });

  it('index.html introduces no dynamic data of its own', () => {
    const s = blankComments(fs.readFileSync(path.join(PUBLIC, 'index.html'), 'utf8'));
    assert.ok(!/fetch\s*\(|XMLHttpRequest|\$\.(ajax|get|post)\s*\(/.test(s),
      'index.html now fetches data; anything rendered from it must avoid jQuery .html()');
    assert.ok(!/<script(?![^>]*\bsrc=)[^>]*>\s*\S/.test(s),
      'index.html now has an inline script — it must be hashed for CSP (R-F3840) and reviewed here');
  });

  it('the SITE GLUE never passes dynamic data to jQuery .html()', () => {
    // Scoped to custom.js, the theme code this project wrote. plugins.js is the
    // minified owl-carousel/bootstrap bundle and calls .html() on its OWN
    // internals (`c.navText`, `this._templates`) — rewriting a vendor bundle is
    // not the control here. What bounds THAT is the test above: index.html feeds
    // it nothing dynamic, so its parser never sees untrusted input.
    const s = readIf('public/pelican/assets/js/custom.js');
    if (s === null) return;
    // `.html()` with no argument is a getter and harmless; with one it parses.
    const setters = [...s.matchAll(/\.html\(\s*[^)\s]/g)].map((m) => m[0]);
    assert.deepEqual(setters, [],
      'custom.js calls jQuery .html(value); with jQuery 2.1.1 that is the documented '
      + 'XSS sink, and this file is where API responses are handled. Use .text().');
  });

  it('the lead form still reports through .text(), not .html()', () => {
    const s = readIf('public/pelican/assets/js/custom.js');
    if (s === null) return;
    assert.ok(/\$response\.addClass\('is-error'\)\.text\(/.test(s),
      'the server error message must be written with .text() — it is the one place '
      + 'API-controlled text reaches this page');
    assert.ok(/\$response\.addClass\('is-success'\)\.text\(/.test(s));
  });
});

describe('R-F3851 no inline event handler survives anywhere first-party', () => {
  // CSP sets script-src-attr 'none', so an on*= attribute is BOTH an injection
  // sink and dead code. R-F3839 removed two on aria-brain.html; this catches the
  // next one, on any page.
  it('public/** contains no on*= attribute outside vendored themes', () => {
    const offenders = [];
    for (const f of allFiles()) {
      const s = blankComments(fs.readFileSync(f, 'utf8'));
      for (const m of s.matchAll(
        /\bon(click|change|input|submit|load|error|focus|blur|keyup|keydown|mouseover|mouseout|dblclick)\s*=\s*["']/g)) {
        offenders.push(`${path.relative(repoRoot(), f)}:${s.slice(0, m.index).split('\n').length}`);
      }
    }
    assert.deepEqual(offenders, [],
      `inline handlers are blocked by CSP script-src-attr 'none' — these are dead `
      + `code as well as sinks:\n  ${offenders.join('\n  ')}`);
  });
});
