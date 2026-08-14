// test/web-hardening-rf2603-2608.test.mjs
// Regression locks for the aria-web prospector-DD hardening batch (2026-07-14):
// R-F2603 (credential), R-F2604 (CSP), R-F2605 (brain-wiring), R-F2606 (access
// control), R-F2607 (frontend href allowlist), R-F2608 (correctness). Source-parse
// style (mirrors web-security-rf2101) — no live services required.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (p) => readFileSync(join(ROOT, p), 'utf-8');
const SERVER = read('server.mjs');
const TELEGRAM = read('lib/alerts/telegram.mjs');
const RATELIMIT = read('middleware/rateLimiter.mjs');
const BILLING = read('lib/billing/routes.mjs');
const APPJS = read('public/js/app.js');

describe('R-F2603 — no committed credential; telegram dispatch not swallowed', () => {
  it('the hardcoded Crucix2026! password is gone', () => {
    assert.ok(!/Crucix2026!/.test(TELEGRAM), 'no hardcoded dashboard password may remain');
  });
  it('DASHBOARD_PASS default is a random per-boot secret', () => {
    assert.ok(/DASHBOARD_PASS\s*=\s*process\.env\.DASHBOARD_PASS\s*\|\|\s*randomBytes/.test(TELEGRAM),
      'DASHBOARD_PASS must fall back to randomBytes, not a literal');
  });
  it('poll-dispatch catch logs instead of swallowing silently', () => {
    assert.ok(/poll dispatch error/.test(TELEGRAM), 'the poll loop catch must log the error');
  });
});

describe('R-F2604 — CSP hardened; dead no-op removed', () => {
  it('CSP declares base-uri / frame-ancestors / form-action', () => {
    assert.ok(/baseUri:\s*\["'self'"\]/.test(RATELIMIT), 'baseUri directive missing');
    assert.ok(/frameAncestors:\s*\["'self'"\]/.test(RATELIMIT), 'frameAncestors directive missing');
    assert.ok(/frameSrc:\s*\["'self'"\]/.test(RATELIMIT), 'same-origin authenticated embeds must be permitted');
    assert.ok(/formAction:\s*\["'self'"\]/.test(RATELIMIT), 'formAction directive missing');
  });
  it('dead content-length-limit no-op middleware is gone', () => {
    // Strip full-line comments first — the removal note and a doc example both quote the
    // old string; only actual code should count.
    const codeOnly = RATELIMIT.replace(/^\s*\/\/.*$/gm, '');
    assert.ok(!/content-length-limit/.test(codeOnly),
      'the fabricated content-length-limit middleware must be removed from code');
  });
});

describe('R-F2605 — failures wired to the brain', () => {
  it('auth login wires lockout + handler-error to errorTracker', () => {
    assert.ok(/errorTracker\.record\('auth',\s*'login_throttle_lockout'/.test(SERVER), 'lockout not wired');
    assert.ok(/errorTracker\.record\('auth',\s*'login_handler_error'/.test(SERVER), 'login 500 not wired');
  });
  it('login logs mask the email (no raw PII)', () => {
    assert.ok(/function _maskEmail\(/.test(SERVER), '_maskEmail helper missing');
    assert.ok(!/login LOCKED email=\$\{email\}/.test(SERVER), 'raw email must not be logged on lockout');
  });
  it('boot listen error is wired before exit', () => {
    assert.ok(/errorTracker\.record\('boot',\s*'listen_error'/.test(SERVER), 'boot listen_error not wired');
  });
  it('billing failures are wired (webhook signature + payment_failed)', () => {
    assert.ok(/errorTracker/.test(BILLING), 'billing must import/use errorTracker');
    assert.ok(/webhook_signature_invalid/.test(BILLING), 'webhook signature failure not wired');
    assert.ok(/invoice_payment_failed/.test(BILLING), 'invoice.payment_failed not wired');
  });
  it('deep-search and entity-search failures are wired', () => {
    assert.ok(/errorTracker\.record\('deep_search'/.test(SERVER), 'deep_search not wired');
    assert.ok(/errorTracker\.record\('entity_search'/.test(SERVER), 'entity_search not wired');
  });
});

describe('R-F2606 — API-surface access control', () => {
  it('compliance audit + export are admin-only', () => {
    assert.ok(/app\.get\('\/api\/compliance\/audit',\s*requireAdmin/.test(SERVER),
      '/api/compliance/audit must be requireAdmin');
    assert.ok(/app\.get\('\/api\/compliance\/audit\/export',\s*requireAdmin/.test(SERVER),
      '/api/compliance/audit/export must be requireAdmin');
  });
  it('RAG proxy routes pin user_id from the JWT', () => {
    assert.ok(/function _pinBodyUserId\(req\)/.test(SERVER), '_pinBodyUserId helper missing');
    const ragSearch = SERVER.slice(SERVER.indexOf("'/api/aria/rag/search'"), SERVER.indexOf("'/api/aria/rag/search'") + 300);
    assert.ok(/_pinBodyUserId\(req\)/.test(ragSearch), 'rag/search must call _pinBodyUserId');
  });
  it('extract-document caps upload size', () => {
    // R-F3988 (C-75) — this asserted the LITERAL `25 * 1024 * 1024`, and that
    // literal was itself the defect: one hardcoded ceiling for every tier, so
    // free/pro (sold 5 MB) could send 25 MB and proIntel (sold 50 MB) was refused
    // at 25. The cap is now resolved from the caller's tier.
    //
    // Rewritten to the SURVIVING INTENT — the route must still refuse an
    // oversized upload before streaming it — rather than deleted. Deleting it
    // would drop the guard entirely, which is how R-F3859's reversed assertion
    // nearly disabled primary search: the quickest way to green a red test is
    // usually the one that removes the protection.
    const start = SERVER.indexOf("app.post('/api/aria/extract-document'");
    assert.ok(start > 0, 'the extract-document route must exist');
    const route = SERVER.slice(start, start + 3400);
    assert.ok(/uploadTooLarge\(\s*req\.headers\['content-length'\]/.test(route),
      'extract-document must measure Content-Length against the tier limit');
    assert.ok(/res\.status\(413\)/.test(route),
      'an oversized upload must still be refused with 413 before the body is streamed');
  });
});

describe('R-F2607 — frontend href scheme allowlist', () => {
  it('safeHref helper exists and allows only http(s)/mailto', () => {
    assert.ok(/function safeHref\(u\)/.test(APPJS), 'safeHref helper missing');
    assert.ok(/\^\(https\?:\|mailto:\)/.test(APPJS), 'safeHref must allowlist http(s)/mailto');
  });
});

describe('R-F2608 — correctness/robustness', () => {
  it('login throttle map is swept over a cap', () => {
    assert.ok(/_loginAttempts\.size\s*>\s*5000/.test(SERVER), 'throttle map sweep missing');
  });
  it('zoom proxy fetch is timeout-bounded', () => {
    const i = SERVER.indexOf('const zoomProxy');
    const fn = SERVER.slice(i, i + 600);
    assert.ok(/AbortSignal\.timeout\(/.test(fn), 'zoom proxy fetch must have an AbortSignal timeout');
  });
});
