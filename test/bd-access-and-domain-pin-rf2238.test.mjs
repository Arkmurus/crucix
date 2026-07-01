// test/bd-access-and-domain-pin-rf2238.test.mjs
//
// CAPABILITY test for R-F2238 — closes the security-auditor's two findings:
//  (1) BD-intelligence / brain-pipeline / share routes were requireAuth-only over a
//      GLOBAL store → any self-serve viewer could read/mutate/publicly-share the
//      operator's confidential BD intel. Now requireAdmin.
//  (2) The DD user_email_domain pin was fail-OPEN (set only, no delete) → a
//      client-supplied ?user_email_domain could survive when no domain was derivable
//      → conditional cross-tenant DD read/DELETE. Now fail-CLOSED (delete-before-set,
//      unconditional).
// Source-contract locks (same approach as proxy-idor-pin-rf2211) so a future edit
// can't silently re-open either hole.
//
// Run: node test/bd-access-and-domain-pin-rf2238.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SERVER = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');
let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── Finding 1: operator-internal BD routes are admin-gated (not requireAuth) ──
const ADMIN_ROUTES = [
  '/api/bd-intelligence',
  '/api/bd-intelligence/pipeline',
  '/api/bd-intelligence/pipeline/:id/stage',
  '/api/bd-intelligence/pipeline/:id/outcome',
  '/api/bd-intelligence/feedback',
  '/api/share/brief',
  '/api/brain/pipeline/summary',
  '/api/brain/pipeline/deal/:id',
  '/api/brain/pipeline/create',
  '/api/brain/pipeline/advance',
];
for (const r of ADMIN_ROUTES) {
  check(`${r} is requireAdmin-gated`, SERVER.includes(`'${r}', requireAdmin`));
  check(`${r} is NOT requireAuth-only`, !SERVER.includes(`'${r}', requireAuth`));
}

// ── Finding 2: the domain pin is fail-CLOSED (delete before the derive try) ──
// Both pin blocks (the /dd/reports handler + _ddPinUserParams) must strip a
// client-supplied domain unconditionally, BEFORE the findUserById try (so a throw
// can't leave a forged domain in place). The inserted pattern is:
//   params.set('user_id', userId);
//   params.delete('user_email_domain'); ...
//   try { ... }
const failClosed = (SERVER.match(/params\.set\('user_id', userId\);\s*\n\s*params\.delete\('user_email_domain'\)/g) || []).length;
check('both pin blocks delete user_email_domain right after pinning user_id (fail-closed ×2)',
  failClosed === 2);
// and the delete must precede every set of user_email_domain (no set without a prior delete in-block)
const firstDelete = SERVER.indexOf("params.delete('user_email_domain')");
const firstSet = SERVER.indexOf("params.set('user_email_domain'");
check('the client-domain delete exists and precedes the derived-domain set',
  firstDelete !== -1 && firstDelete < firstSet);

console.log(failures === 0 ? '\nR-F2238 tests: PASS' : `\nR-F2238 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
