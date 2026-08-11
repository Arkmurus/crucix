// test/xss-sinks-rf3839.test.mjs
//
// R-F3839 — the XSS pass the audit explicitly did NOT clear: public/dd-reports.html
// (69 innerHTML sinks), public/aria-brain.html (76) and public/dashboard.html (24).
//
// ── WHAT WAS FOUND ───────────────────────────────────────────────────────────
// dd-reports.html: clean. Every interpolation already routes through an escaper.
//
// aria-brain.html (ADMIN-only page, lib/auth/operatorPages.mjs:49):
//   * eco-card built an INLINE onclick by string concatenation, with `n.id`
//     interpolated raw and the label stripped only of `'`. Two defects in one:
//     a `'` in an id escaped the JS string, AND — because CSP sets
//     script-src-attr 'none' (middleware/rateLimiter.mjs:267) — the handler had
//     never fired in production at all. R-F1919 migrated every page's inline
//     handlers to delegated listeners and missed this one.
//   * `dom` (blocked-domain table), the DLQ `error` text, the fetch-failure
//     `reason`, node labels and audit titles all went raw into innerHTML.
//
// dashboard.html: the fetch-failure banner rendered the failure `reason` raw,
// and this page is NOT behind the operator gate.
//
// ── WHAT IS NOT CLAIMED ──────────────────────────────────────────────────────
// aria-brain.html still interpolates ~230 values that are the brain's OWN
// telemetry — counts, percentages, CSS class names, enum states, internal module
// identifiers. Those were reviewed as a class, not proven one by one, and this
// test does not assert they are safe. Blanket-escaping them would break the many
// sinks that deliberately emit HTML fragments (`chips`, `providerChips`, `seg()`,
// `arr.map(_card).join('')`). Recorded honestly rather than reported as cleared.
//
// Run: node --test test/xss-sinks-rf3839.test.mjs

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
const read = (f) => fs.readFileSync(path.join(repoRoot(), f), 'utf8');
/** Source with `//` comments removed — the fixes quote the old code to explain it. */
const codeOnly = (s) => s.replace(/(^|[^:])\/\/.*$/gm, '$1');

describe('R-F3839 no page ships an inline event handler', () => {
  // CSP blocks these outright (script-src-attr 'none'), so one is BOTH a latent
  // injection sink and a feature that silently does not work.
  const PAGES = ['public/dd-reports.html', 'public/aria-brain.html', 'public/dashboard.html'];

  for (const page of PAGES) {
    it(`${page} builds no on*= attribute from data`, () => {
      const src = codeOnly(read(page));
      // An on*= handler INSIDE a JS template literal, i.e. generated from data.
      const generated = [...src.matchAll(/\bon(click|error|load|mouseover|focus|input|change)\s*=\s*"[^"]*\$\{/g)];
      assert.deepEqual(generated.map((m) => m[0]), [],
        'CSP script-src-attr \'none\' blocks inline handlers, so this is dead code '
        + 'AND an injection sink — use a delegated listener with data-* attributes');
    });
  }
});

describe('R-F3839 aria-brain eco-card', () => {
  const src = () => codeOnly(read('public/aria-brain.html'));

  it('drill-down uses escaped data-* attributes, not a concatenated handler', () => {
    const s = src();
    assert.ok(!/onclick="ecoDrill\(/.test(s), 'the inline handler is back');
    assert.ok(/data-eco-id="\$\{escapeHtml\(n\.id\)\}"/.test(s),
      'the node id must be an escaped data attribute');
    assert.ok(/data-eco-nm="\$\{escapeHtml\(nm\)\}"/.test(s));
  });

  it('a delegated listener actually restores the click', () => {
    const s = src();
    assert.ok(/addEventListener\('click'/.test(s) && /data-eco-id/.test(s),
      'removing the handler without a replacement would leave the card dead');
    assert.ok(/closest\('\.eco-card\[data-eco-id\]'\)/.test(s),
      'the delegated handler must resolve the card from the event target');
  });

  it('the card label and tooltip are escaped', () => {
    const s = src();
    assert.ok(/title="\$\{escapeHtml\(tip\)\}"/.test(s),
      'the tooltip stripped only `"` — use the real escaper');
    assert.ok(/class="eco-nm">\$\{escapeHtml\(nm\)\}</.test(s),
      'the node label went raw into innerHTML');
  });
});

describe('R-F3839 externally-sourced strings are escaped', () => {
  it('aria-brain: blocked-domain table, DLQ errors, fetch reasons, node labels', () => {
    const s = codeOnly(read('public/aria-brain.html'));
    for (const [re, what] of [
      [/<td>\$\{escapeHtml\(dom\)\}<\/td>/, 'blocked DOMAIN (scraped)'],
      [/\$\{escapeHtml\(e\.error\?\.slice\(0,100\)\)\}/, 'dead-letter ERROR text'],
      [/\$\{escapeHtml\(r\)\}\)<\/span>/, 'fetch-failure REASON'],
      [/<strong>\$\{escapeHtml\(nd\.node\.label\)\}<\/strong>/, 'ecosystem node label'],
      [/title="\$\{escapeHtml\(a\.title\|\|''\)\}"/, 'audit-ref title attribute'],
      [/\$\{escapeHtml\(h\.reason\)\}/, 'load-mode transition reason'],
      [/<td>\$\{escapeHtml\(j\.jurisdiction\)\}<\/td>/, 'DD jurisdiction'],
      [/<td>\$\{escapeHtml\(tags\)\}<\/td>/, 'model-authored confidence tags'],
    ]) {
      assert.ok(re.test(s), `${what} is not escaped`);
    }
  });

  it('dashboard: the fetch-failure banner is escaped', () => {
    const s = codeOnly(read('public/dashboard.html'));
    assert.ok(/\$\{escHtml\(r\)\}/.test(s),
      'this page is NOT operator-gated and rendered the failure reason raw');
    assert.ok(/\$\{escHtml\(p\)\}/.test(s));
  });

  it('dashboard can actually reach escHtml', () => {
    const page = read('public/dashboard.html');
    assert.ok(page.includes('js/app.js'),
      'escHtml is global via js/app.js:557 — without that script the page throws');
    assert.ok(read('public/js/app.js').includes('function escHtml'),
      'the shared escaper must still exist');
    // ...and app.js must load BEFORE the inline block that calls it.
    assert.ok(page.indexOf('js/app.js') < page.lastIndexOf('escHtml('),
      'app.js must be loaded before the inline script that uses escHtml');
  });
});

describe('R-F3839 dd-reports.html stays clean', () => {
  it('every template interpolation routes through an escaper or a formatter', () => {
    const s = codeOnly(read('public/dd-reports.html'));
    const SAFE = /\b(esc|escHtml|escapeHtml|escAttr|safeUrl|encodeURIComponent|Number|parseInt|parseFloat|toFixed|length|JSON\.stringify)\b/;
    const bare = [];
    for (const m of s.matchAll(/\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}/g)) {
      const e = m[1].trim();
      if (!e || SAFE.test(e)) continue;
      if (/^[\d\s.+\-*/']+$/.test(e)) continue;
      if (e.includes('?') && e.includes("'")) continue;   // ternary yielding literals
      bare.push(e.slice(0, 80));
    }
    assert.deepEqual(bare, [],
      `dd-reports.html was clean at audit time and must stay clean: ${bare.join(' | ')}`);
  });
});
