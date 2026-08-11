// test/download-and-share-hardening-rf3837.test.mjs
//
// R-F3837 — the DD PDF download filename is built from an unsanitised runId.
// R-F3838 — the /s/:token share link can never redeem, and reviving it exposes
//           two unvalidated href sinks on an UNAUTHENTICATED page.
//
// Run: node --test test/download-and-share-hardening-rf3837.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

import { safeExternalUrl } from '../lib/util/safeUrl.mjs';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const src = () => fs.readFileSync(path.join(repoRoot(), 'server.mjs'), 'utf8');

/**
 * Source with `//` comments removed.
 *
 * These assertions extract the SHIPPED regex out of the handler, and the fix's
 * comment necessarily quotes the old broken one to explain it. Matching raw
 * source found the quoted regex first — a false failure whose obvious "fix"
 * is deleting the explanation.
 */
const codeOnly = (s) => s.replace(/(^|[^:])\/\/.*$/gm, '$1');

// ─────────────────────────────────────────────────────────────────────────────
// R-F3837 — Content-Disposition filename
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3837 the PDF filename cannot be spoofed through runId', () => {
  const handler = () => {
    const s = src();
    const at = s.indexOf("app.get('/api/aria/dd/report/:run_id/pdf'");
    assert.ok(at > -1, 'handler not found');
    return s.slice(at, at + 2600);
  };

  it('runId is sanitised for the filename, exactly as entity already was', () => {
    const b = handler();
    assert.ok(!/filename="ARIA_DD_\$\{entity\}_\$\{runId\}\.pdf"/.test(b),
      'the raw runId is back in the Content-Disposition header');
    assert.ok(/safeRunId|runIdSafe/.test(b),
      'the filename must use a sanitised run id');
  });

  it('the sanitiser is the same transform used for entity', () => {
    const b = handler();
    const uses = (b.match(/replace\(\/\[\^\\w\\-\]\+\/g, '_'\)/g) || []).length;
    assert.ok(uses >= 2,
      `entity and runId must both be sanitised the same way (found ${uses})`);
  });

  it('the UPSTREAM fetch and docRef still use the RAW runId', () => {
    const b = handler();
    assert.ok(b.includes('encodeURIComponent(runId)'),
      'sanitising the id before the brain lookup would break real downloads — '
      + 'only the filename needs it');
    assert.ok(/docRef:\s*runId/.test(b),
      'the document reference printed in the PDF must stay the true run id');
  });

  it('a quote in a run id cannot break out of the filename', () => {
    // Node rejects CRLF in a header value, so this is filename SPOOFING via a
    // quote, not header injection — the sanitiser closes it either way.
    const sanitise = (s) => String(s).replace(/[^\w\-]+/g, '_').slice(0, 60);
    for (const evil of [
      'x"; filename="evil.exe', 'a\r\nX-Injected: 1', '../../etc/passwd',
      'run id with spaces', 'run;id', 'a"b',
    ]) {
      const out = sanitise(evil);
      assert.ok(!/["\r\n;/\\]/.test(out), `sanitiser left a dangerous char: ${out}`);
    }
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// R-F3838 — the share-link guard, and what reviving it exposes
// ─────────────────────────────────────────────────────────────────────────────
describe('R-F3838 the share token guard matches what is actually minted', () => {
  const MINT = () => Buffer.from(
    Uint8Array.from({ length: 24 }, (_, i) => (i * 37 + 11) % 256),
  ).toString('base64url');

  it('a real minted token is 32 chars of base64url', () => {
    const t = MINT();
    assert.equal(t.length, 32);
    assert.match(t, /^[A-Za-z0-9_-]+$/);
  });

  it('the OLD guard rejected every token it was given — the feature was dead', () => {
    assert.ok(!/^[a-z0-9]{20,30}$/.test(MINT()),
      'if the old regex matched, the premise of this fix is wrong');
  });

  it('the shipped guard accepts a real token', () => {
    const s = src();
    const at = s.indexOf("app.get('/s/:token'");
    assert.ok(at > -1);
    const body = codeOnly(s.slice(at, at + 1200));
    const m = body.match(/\/\^\[[^\]]+\]\{\d+,\d+\}\$\//);
    assert.ok(m, 'no token guard found on /s/:token');
    const re = new RegExp(m[0].slice(1, -1));
    assert.ok(re.test(MINT()), `the shipped guard still rejects a real token: ${m[0]}`);
  });

  it('the guard still refuses prototype keys and path characters', () => {
    const s = src();
    const at = s.indexOf("app.get('/s/:token'");
    const body = codeOnly(s.slice(at, at + 1200));
    const re = new RegExp(body.match(/\/\^\[[^\]]+\]\{\d+,\d+\}\$\//)[0].slice(1, -1));
    for (const bad of [
      '__proto__', 'constructor', 'prototype', '../../etc/passwd',
      'a/b', 'a.b', 'short', '', 'a'.repeat(200),
    ]) {
      assert.equal(re.test(bad), false, `guard accepted ${JSON.stringify(bad)}`);
    }
  });

  it('the store lookup does not trust inherited properties', () => {
    const s = src();
    const at = s.indexOf('function _shareGet');
    assert.ok(at > -1);
    const body = codeOnly(s.slice(at, at + 500));
    assert.ok(/hasOwnProperty|Object\.create\(null\)|Object\.hasOwn/.test(body),
      'a bare all[token] lookup can resolve through the prototype chain — the '
      + 'length bound blocks the known keys, but the lookup should not depend on it');
  });
});

describe('R-F3838 reviving the public page does not ship an XSS', () => {
  it('safeExternalUrl passes ordinary http(s) links through', () => {
    for (const ok of [
      'https://gov.uk/tender/1', 'http://example.com/a?b=c#d',
      'HTTPS://Example.COM/x',
    ]) {
      assert.equal(safeExternalUrl(ok), ok, `${ok} is a legitimate link`);
    }
  });

  it('safeExternalUrl refuses every script-bearing scheme', () => {
    for (const bad of [
      'javascript:alert(1)',
      'JaVaScRiPt:alert(1)',
      '  javascript:alert(1)',
      'java\tscript:alert(1)',
      'java\nscript:alert(1)',
      'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
      'vbscript:msgbox(1)',
      'file:///etc/passwd',
      'blob:https://x/y',
      'javascript:alert(1)',
    ]) {
      assert.equal(safeExternalUrl(bad), '', `${JSON.stringify(bad)} must not be rendered as a link`);
    }
  });

  it('safeExternalUrl handles junk without throwing', () => {
    for (const bad of [undefined, null, 42, {}, [], '', '   ', 'not a url']) {
      assert.equal(typeof safeExternalUrl(bad), 'string');
    }
  });

  it('both href sinks on the share page route through it', () => {
    const s = src();
    const at = s.indexOf("app.get('/s/:token'");
    const body = codeOnly(s.slice(at, s.indexOf('function escHtml')));
    const hrefs = [...body.matchAll(/href="\$\{([^}]+)\}"/g)].map((m) => m[1]);
    assert.ok(hrefs.length >= 2, `expected the portalUrl and tender url sinks, found ${hrefs.length}`);
    for (const h of hrefs) {
      assert.ok(h.includes('safeExternalUrl'),
        `href sink is not scheme-validated: \${${h}} — escHtml stops attribute `
        + 'breakout but javascript: needs no quote to fire');
    }
  });

  it('external links on the public page carry rel="noopener noreferrer"', () => {
    const s = src();
    const at = s.indexOf("app.get('/s/:token'");
    const body = codeOnly(s.slice(at, s.indexOf('function escHtml')));
    const blanks = (body.match(/target="_blank"/g) || []).length;
    const rels = (body.match(/rel="noopener noreferrer"/g) || []).length;
    assert.equal(rels, blanks,
      `${blanks} target="_blank" links but ${rels} carry rel — the opened page can `
      + 'navigate this one via window.opener');
  });
});
