// test/web-security-rf2094.test.mjs
// R-F2094 — DD security cluster regression locks (read of server.mjs source).
// The 2026-06-28 full DD found self-serve signup (auto-approved viewers) had
// reached endpoints that must be admin-only or crypto-strong. These assert the
// fixes so they can't silently revert.
//
// Run: node --test test/web-security-rf2094.test.mjs
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const SRC = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'server.mjs'), 'utf-8');

describe('R-F2094 — DD security fixes hold', () => {
  it('send-email is admin-only (was an open email relay)', () => {
    assert.ok(/app\.post\('\/api\/aria\/send-email',\s*requireAdmin/.test(SRC),
      'POST /api/aria/send-email must be requireAdmin');
    assert.ok(!/app\.post\('\/api\/aria\/send-email',\s*requireAuth\b/.test(SRC),
      'send-email must NOT be requireAuth');
  });
  it('send-whatsapp is admin-only (was WA impersonation via operator session)', () => {
    assert.ok(/app\.post\('\/api\/aria\/send-whatsapp',\s*requireAdmin/.test(SRC),
      'POST /api/aria/send-whatsapp must be requireAdmin');
    assert.ok(!/app\.post\('\/api\/aria\/send-whatsapp',\s*requireAuth\b/.test(SRC),
      'send-whatsapp must NOT be requireAuth');
  });
  it('env-check is admin-only (was leaking secret-presence + token fingerprint)', () => {
    assert.ok(/app\.get\('\/api\/admin\/env-check',\s*requireAdmin/.test(SRC),
      'GET /api/admin/env-check must be requireAdmin');
  });
  it('share/brief token is crypto-strong (was Math.random, predictable)', () => {
    assert.ok(/randomBytes\(24\)\.toString\('base64url'\)/.test(SRC),
      'share/brief token must use crypto randomBytes');
    assert.ok(!/Math\.random\(\)\.toString\(36\)\[2\]/.test(SRC),
      'share/brief must NOT use Math.random for the token');
  });
});
