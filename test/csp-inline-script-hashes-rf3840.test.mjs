// test/csp-inline-script-hashes-rf3840.test.mjs
//
// R-F3840 — script-src no longer needs 'unsafe-inline'.
//
// ── THE STAKES ───────────────────────────────────────────────────────────────
// CSP treats hashes and 'unsafe-inline' as mutually exclusive: the moment ONE
// hash appears in script-src, browsers ignore 'unsafe-inline' completely. So a
// single MISSED inline block is not a partial weakening — it is a blank page in
// production. Coverage is the property under test, and it is asserted for every
// block in every served file, not sampled.
//
// The second hazard is byte-exactness. The browser hashes the exact bytes
// between `>` and `</script>`, so line endings count. These HTML files are CRLF
// on a Windows checkout and LF in the Linux image (no `*.html` rule in
// .gitattributes), which is why the hashes are computed at BOOT and why this
// test hardcodes none of them — a pinned hash would pass here and blank the site.
//
// Run: node --test test/csp-inline-script-hashes-rf3840.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { describe, it } from 'node:test';

import {
  computeInlineScriptHashes, inlineScriptBodies, sha256Source,
} from '../lib/http/cspHashes.mjs';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const PUBLIC_DIR = path.join(repoRoot(), 'public');

/** Independent extractor — deliberately NOT the shipped one, so a bug in the
 *  shipped regex cannot make this test agree with itself. */
function bodiesIndependently(buf) {
  const t = buf.toString('latin1');
  const out = [];
  let i = 0;
  for (;;) {
    const open = t.indexOf('<script', i);
    if (open === -1) break;
    const gt = t.indexOf('>', open);
    if (gt === -1) break;
    const tag = t.slice(open, gt + 1);
    const close = t.indexOf('</script>', gt);
    if (close === -1) break;
    if (!/\bsrc\s*=/i.test(tag)) {
      const body = t.slice(gt + 1, close);
      if (body.trim()) out.push(body);
    }
    i = close + 9;
  }
  return out;
}

describe('R-F3840 every inline script in every served page is covered', () => {
  const scan = computeInlineScriptHashes(PUBLIC_DIR);

  it('the scan actually found the pages — an empty scan silently fails open', () => {
    assert.ok(scan.files >= 20, `only ${scan.files} HTML files found — scan is not seeing public/`);
    assert.ok(scan.blocks >= 20, `only ${scan.blocks} inline blocks found`);
    assert.ok(scan.hashes.length > 0);
  });

  it('EVERY inline block in EVERY html file has its hash in the set', () => {
    const set = new Set(scan.hashes);
    const missing = [];
    const walk = (dir) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { walk(full); continue; }
        if (path.extname(e.name).toLowerCase() !== '.html') continue;
        const buf = fs.readFileSync(full);
        bodiesIndependently(buf).forEach((body, idx) => {
          if (!set.has(sha256Source(body))) {
            missing.push(`${path.relative(repoRoot(), full)} block#${idx}`);
          }
        });
      }
    };
    walk(PUBLIC_DIR);
    assert.deepEqual(missing, [],
      `these inline scripts would be BLOCKED, blanking the page: ${missing.join(', ')}`);
  });

  it('the shipped extractor agrees with an independent one on every file', () => {
    const walk = (dir) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { walk(full); continue; }
        if (path.extname(e.name).toLowerCase() !== '.html') continue;
        const buf = fs.readFileSync(full);
        assert.deepEqual(
          inlineScriptBodies(buf).map((b) => b.length),
          bodiesIndependently(buf).map((b) => b.length),
          `extractors disagree on ${path.relative(repoRoot(), full)}`,
        );
      }
    };
    walk(PUBLIC_DIR);
  });
});

describe('R-F3840 the hash is byte-exact', () => {
  it('CRLF and LF bodies hash DIFFERENTLY — the reason boot-time scanning exists', () => {
    const lf = sha256Source('var a = 1;\nvar b = 2;\n');
    const crlf = sha256Source('var a = 1;\r\nvar b = 2;\r\n');
    assert.notEqual(lf, crlf,
      'if these matched, a checked-in hash list would be safe — they do not, so '
      + 'the scan MUST run in the environment that serves the file');
  });

  it('matches an independently computed sha256 of the exact bytes', () => {
    const body = "console.log('héllo');\r\n";   // multi-byte char + CRLF
    const expected = "'sha256-" + createHash('sha256')
      .update(Buffer.from(body, 'latin1')).digest('base64') + "'";
    assert.equal(sha256Source(body), expected);
  });

  it('multi-byte UTF-8 survives the latin1 round trip unchanged', () => {
    const utf8 = Buffer.from('const s = "→ ✓ é";', 'utf8');
    const [body] = inlineScriptBodies(
      Buffer.concat([Buffer.from('<script>', 'latin1'), utf8, Buffer.from('</script>', 'latin1')]),
    );
    assert.equal(Buffer.from(body, 'latin1').toString('utf8'), 'const s = "→ ✓ é";',
      'byte preservation is what makes the hash match what the browser computes');
  });
});

describe('R-F3840 the extractor does not over- or under-match', () => {
  const B = (s) => Buffer.from(s, 'latin1');

  it('skips blocks with a src attribute — those are covered by \'self\'', () => {
    assert.deepEqual(inlineScriptBodies(B('<script src="js/app.js"></script>')), []);
    assert.deepEqual(inlineScriptBodies(B('<script  src = "a.js" defer></script>')), []);
  });

  it('captures blocks with other attributes', () => {
    assert.deepEqual(inlineScriptBodies(B('<script type="module">let a=1;</script>')), ['let a=1;']);
    assert.deepEqual(inlineScriptBodies(B('<SCRIPT>let a=1;</script>')), ['let a=1;']);
  });

  it('handles several blocks in one file, and ignores empty ones', () => {
    assert.deepEqual(
      inlineScriptBodies(B('<script>a</script><script src="x.js"></script><script>  </script><script>b</script>')),
      ['a', 'b'],
    );
  });

  it('an unterminated tag does not throw or swallow the rest', () => {
    assert.doesNotThrow(() => inlineScriptBodies(B('<script>never closed')));
  });
});

describe('R-F3840 the policy is wired and fails open', () => {
  const src = () => fs.readFileSync(path.join(repoRoot(), 'middleware/rateLimiter.mjs'), 'utf8');

  it('scriptSrc no longer hardcodes unsafe-inline', () => {
    const s = src();
    assert.ok(!/scriptSrc:\s*\["'self'", "'unsafe-inline'"\]/.test(s),
      "script-src still hardcodes 'unsafe-inline'");
    assert.ok(/scriptSrc:\s*_scriptSrc/.test(s));
  });

  it('keeps unsafe-inline when the scan finds nothing — a blank UI is worse', () => {
    const s = src();
    assert.ok(/scan\.hashes\.length > 0/.test(s),
      'the fail-open branch is what stops an unreadable public/ blanking every page');
  });

  it('the operator escape hatch exists', () => {
    assert.ok(src().includes('ARIA_CSP_ALLOW_INLINE_SCRIPT'),
      'there must be a way to restore the old behaviour without a code change');
  });

  it('scriptSrcAttr stays \'none\' — R-F1919 must not be traded away', () => {
    assert.ok(/scriptSrcAttr:\s*\["'none'"\]/.test(src()));
  });

  it('no FIRST-PARTY page or script creates a <script> element dynamically', () => {
    // The one pattern hashes cannot cover: a script element built at runtime has
    // no hash in the policy, and unlike 'unsafe-inline' a hash list will not
    // admit it. Zero in our own code at the time of the change; asserted so it
    // stays zero, because the first one would fail silently in the browser.
    const offenders = [];
    const walk = (dir) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        // Vendored third-party bundles. `pelican/` is the landing-page theme
        // (jQuery 2.1.1 + bootstrap + owl-carousel) and is covered by the
        // dedicated assertion below; `vendor/` holds an orphaned jquery.min.js
        // that no page references (asserted separately, so it cannot quietly
        // come back into use).
        if (e.isDirectory()) { if (e.name !== 'pelican' && e.name !== 'vendor') walk(full); continue; }
        if (!/\.(html|js)$/i.test(e.name)) continue;
        const t = fs.readFileSync(full, 'utf8');
        if (/createElement\(\s*['"]script/i.test(t)) offenders.push(path.relative(repoRoot(), full));
      }
    };
    walk(PUBLIC_DIR);
    assert.deepEqual(offenders, [],
      `a dynamically created <script> is not covered by any hash: ${offenders.join(', ')}`);
  });

  it('the vendored jQuery caveat on index.html is bounded, not ignored', () => {
    // public/index.html (the PUBLIC LANDING PAGE) loads jQuery 2.1.1, whose
    // globalEval() evaluates script by creating a <script> element and setting
    // .text — an inline script with no hash, which this policy blocks where
    // 'unsafe-inline' would have allowed it.
    //
    // That path is only reached when jQuery is handed MARKUP CONTAINING a
    // <script> tag (via .html()/.append()/etc). Checked at the time of the
    // change: no such markup exists in the page or its theme scripts. This test
    // pins that, so introducing one is a test failure rather than a blank
    // landing page discovered by a visitor.
    // public/vendor/jquery.min.js is a SECOND copy and is referenced by no page.
    // If that changes, its globalEval becomes reachable and must be assessed.
    const vendorRefs = [];
    const scanRefs = (dir) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, e.name);
        if (e.isDirectory()) { if (e.name !== 'vendor') scanRefs(full); continue; }
        if (!/\.html$/i.test(e.name)) continue;
        if (/vendor\/jquery/i.test(fs.readFileSync(full, 'utf8'))) {
          vendorRefs.push(path.relative(repoRoot(), full));
        }
      }
    };
    scanRefs(PUBLIC_DIR);
    assert.deepEqual(vendorRefs, [],
      `public/vendor/jquery.min.js is now loaded by ${vendorRefs.join(', ')} — assess its `
      + 'globalEval path against the hash-only script-src before shipping');

    const suspects = [
      'public/index.html',
      'public/pelican/assets/js/custom.js',
      'public/pelican/assets/js/plugins.js',
    ];
    for (const rel of suspects) {
      const full = path.join(repoRoot(), rel);
      if (!fs.existsSync(full)) continue;
      const buf = fs.readFileSync(full);
      // Only JavaScript can hand markup to jQuery. For an .html file that means
      // its inline <script> bodies — scanning the raw HTML instead matches every
      // quoted attribute in the document and reports the page against itself.
      const js = /\.html$/i.test(rel)
        ? inlineScriptBodies(buf).join('\n')
        : buf.toString('utf8');
      const injected = js.match(/['"`][^'"`\n]*<script\b[^'"`]*['"`]/i);
      assert.equal(injected, null,
        `${rel} builds markup containing a <script> tag (${injected && injected[0].slice(0, 60)}). `
        + 'jQuery would evaluate it via globalEval, which a hash-only script-src blocks. '
        + 'Externalise it, or set ARIA_CSP_ALLOW_INLINE_SCRIPT=1 and re-open R-F3840.');
    }
  });
});
