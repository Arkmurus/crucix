// test/web-security-rf2101.test.mjs
// R-F2101 — regression locks for the REAL items from ARIA's aria-web DD (the
// majority of her findings were false positives — mounts have internal-token
// auth, chat/stream already has timeout + r.ok; these 3 were genuine).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'server.mjs'), 'utf-8');

describe('R-F2101 — ARIA web DD real fixes hold', () => {
  it('extract-document is gated by requireAuth (was any-Bearer → server-token extraction)', () => {
    assert.ok(/app\.post\('\/api\/aria\/extract-document',\s*requireAuth/.test(SRC),
      'extract-document must use requireAuth');
  });
  it('zoom proxy is gated by requireAuth (open internal proxy)', () => {
    assert.ok(/app\.use\('\/api\/zoom',\s*requireAuth,\s*zoomProxy\)/.test(SRC),
      'zoom proxy must be requireAuth');
  });
  it('reportOutcome retries once on failure (§25 proprioception blind-spot)', () => {
    const i = SRC.indexOf('function reportOutcome(');
    const fn = SRC.slice(i, i + 1200);
    assert.ok(/attempt < 1\)\s*setTimeout\(\(\) => _send\(attempt \+ 1\)/.test(fn),
      'reportOutcome must retry once before giving up');
  });
});
