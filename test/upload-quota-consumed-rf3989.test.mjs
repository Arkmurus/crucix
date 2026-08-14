// test/upload-quota-consumed-rf3989.test.mjs
//
// R-F3989 (C-74) — the uploadsPerDay cap was defined, counted, and never consumed.
//
// THE DEFECT. Everything needed to enforce a per-day upload cap already existed:
// `tiers.mjs` defines `uploadsPerDay` (free 15, pro 30, proIntel 200),
// `quotas.mjs::_keyFor` mints `crucix:quota:upl:<user>:<utc-day>`,
// `_capForKind` maps 'upload' to `uploadsPerDay`, and `enforce.mjs` documents
// 'upload' as a supported kind. The only missing piece was a caller:
//
//     _quotaBlock(req, 'message')  ×2   (chat, chat/stream)
//     _quotaBlock(req, 'ddRun')    ×1   (dd/orchestrate)
//     _quotaBlock(req, 'upload')   ×0   ← nothing, ever
//
// So the limit shown to the customer bounded nothing on the path they use. This
// is the same shape as R-F2765 (caps DEFINED but never CHECKED) and as the three
// Phase A gates CLAUDE.md §1 records as "certified by an absence" — a mechanism
// that reads as present because every part of it exists except the one that acts.
//
// The third test is the one that generalises: it asserts that EVERY quota kind
// the tier table sells has at least one enforcement call site. Written as a
// specific "upload is enforced" assertion it would have passed the day after the
// fix and told us nothing about the fourth kind someone adds later.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

// R-F2858 — isolate the durable counter file, or this suite consumes real quota
// on a box where PERSIST_DIR=/data. Must be set BEFORE quotas.mjs is imported.
process.env.QUOTA_FILE_OVERRIDE = path.join(
  mkdtempSync(path.join(tmpdir(), 'quota-rf3989-')), 'quotas.json',
);
const { enforceQuota } = await import('../lib/billing/enforce.mjs');
const { TIERS } = await import('../lib/billing/tiers.mjs');

const serverSrc = fs.readFileSync(new URL('../server.mjs', import.meta.url), 'utf8');

// Drop whole-line `//` comments so a guard inspects CODE, not prose.
//
// DELIBERATELY NOT stripping block comments. The first version of this helper
// also ran a block-comment regex, which is not a parser: an open-comment token
// inside a string or regex starts a span running to the next close-comment
// token, taking real code with it. Measured on server.mjs it deleted 122,623
// characters — 38% of the source — and made the ddRun and upload call sites
// vanish, so the guard reported "no enforcement call site" for lanes that have
// one. A guard that mangles its input before reading it can fail either way for
// reasons unrelated to the code: the R-F3858 class exactly.
//
// This comment is a line comment for the same reason. Written as a block, the
// close-comment token in the prose above ended it early and the rest of the
// paragraph was parsed as code — the fragility being described, demonstrating
// itself.
//
// Line filtering cannot swallow code: a line either IS a comment or it is not.
// Splitting on /\r?\n/ matters too — this is a CRLF checkout (CLAUDE.md §16)
// and `.` does not match a carriage return, so an end-anchored stripper
// silently matches nothing.
function codeOf(src) {
  return src
    .split(/\r?\n/)
    .filter(l => !l.trim().startsWith('//'))
    .join('\n');
}

describe('R-F3989 — the upload cap is actually consumed', () => {

  it('the decision layer enforces uploadsPerDay (free = 15/day)', () => {
    // Proves the machinery was always capable — the gap was the caller.
    return (async () => {
      const uid = 'test-user-rf3989-upl';
      for (let i = 0; i < TIERS.free.uploadsPerDay; i++) {
        assert.equal(await enforceQuota(uid, 'free', 'upload'), null,
          `upload ${i + 1} should be allowed`);
      }
      const blocked = await enforceQuota(uid, 'free', 'upload');
      assert.ok(blocked && blocked.allowed === false, 'the 16th upload must be blocked');
      assert.equal(blocked.cap, TIERS.free.uploadsPerDay);
    })();
  });

  it('a paid tier gets its own higher upload allowance', async () => {
    const uid = 'test-user-rf3989-upl-paid';
    for (let i = 0; i < TIERS.free.uploadsPerDay + 1; i++) {
      assert.equal(await enforceQuota(uid, 'proIntel', 'upload'), null,
        'proIntel must not be bounded by the free allowance');
    }
  });

  it('EVERY quota kind the tier table sells has an enforcement call site', () => {
    // The generalised guard. 'upload' had zero call sites while being sold, and
    // nothing in the tree compared the set of kinds against the set of callers.
    const KIND_FOR_FIELD = {
      messagesPerDay: 'message',
      ddRunsPerMonth: 'ddRun',
      uploadsPerDay: 'upload',
    };
    const code = codeOf(serverSrc);
    for (const [field, kind] of Object.entries(KIND_FOR_FIELD)) {
      assert.ok(Object.hasOwn(TIERS.free, field),
        `${field} should exist in the tier table`);
      const called = new RegExp(`_quotaBlock\\(\\s*req\\s*,\\s*['"]${kind}['"]`).test(code)
        || new RegExp(`enforceQuota\\([^)]*['"]${kind}['"]`).test(code);
      assert.ok(called,
        `${field} is sold to customers but '${kind}' has no enforcement call site — `
        + 'a cap that is displayed and never checked bounds nothing');
    }
  });

  it('the upload route consumes the quota, and does so for real users only', () => {
    // Bounded source read: server.mjs boots a live app on import (R-F3618).
    const start = serverSrc.indexOf("app.post('/api/aria/extract-document'");
    assert.ok(start > 0, 'the extract-document route should exist');
    const body = codeOf(serverSrc.slice(start, start + 3400));
    assert.match(body, /_quotaBlock\(\s*req\s*,\s*'upload'\s*\)/,
      'the upload route must consume the upload quota');
    assert.match(body, /429/,
      'a consumed-out caller must get 429, not a silent pass');
  });

  it('_quotaBlock still exempts privileged and system callers (R-F3618 must not regress)', () => {
    // Adding a fourth metered route must not re-meter admins or the WA listener.
    const start = serverSrc.indexOf('async function _quotaBlock(');
    assert.ok(start > 0, '_quotaBlock should exist');
    const body = serverSrc.slice(start, serverSrc.indexOf('\n}', start));
    assert.match(body, /isPrivileged\(/, 'admins must not be metered');
    assert.match(body, /if \(!uid\) return null/, 'system/internal callers must stay exempt');
  });
});
