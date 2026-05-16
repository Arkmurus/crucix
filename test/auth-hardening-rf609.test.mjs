// test/auth-hardening-rf609.test.mjs
//
// R-F609 (2026-05-16) — auth hardening capability + static-source guard.
//
// Closes three concrete gaps from the 2026-05-16 web/UI audit:
//   1. /api/auth/reset-password used `user.resetCode !== String(code)` —
//      timing-unsafe AND not throttled. 6-digit code = 1M-key space,
//      practically brute-forceable behind the global TIERS.standard
//      150 req / 15 min limit.
//   2. /api/auth/forgot-password and /api/auth/2fa/authenticate were NOT
//      in the TIERS.auth rate-limit list (only login / register /
//      verify-2fa / reset-password / recovery-reset were).
//   3. Successful password reset emitted no notification to the owner —
//      account takeover via a stolen code was silent.
//
// Static-source checks (no live server). For the live tests run the
// existing auth-recovery-reset-rf425.test.mjs spawn fixture against a
// patched server build.
//
// Run: node test/auth-hardening-rf609.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..');
const SERVER = readFileSync(join(ROOT, 'server.mjs'), 'utf8');
const LIMITER = readFileSync(join(ROOT, 'middleware', 'rateLimiter.mjs'), 'utf8');
const EMAIL = readFileSync(join(ROOT, 'lib', 'auth', 'email.mjs'), 'utf8');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

console.log('R-F609 auth-hardening static guards\n');

// ── 1. reset-password is timing-safe ────────────────────────────────────
console.log('1. reset-password code compare is timing-safe');
{
  // The legacy `!==` form is gone from the reset-password handler.
  // Extract the handler body so we don't false-positive on other unrelated
  // `!==` usages in the file.
  const start = SERVER.indexOf("app.post('/api/auth/reset-password'");
  const end = SERVER.indexOf("\napp.post", start + 1);
  const body = SERVER.slice(start, end > 0 ? end : SERVER.length);

  check(
    'no `user.resetCode !==` in the reset-password handler',
    !/user\.resetCode\s*!==/.test(body),
    'legacy timing-unsafe compare still present',
  );
  check(
    'uses crypto.timingSafeEqual on the code',
    /crypto\.timingSafeEqual\s*\(/.test(body),
    'timing-safe compare missing',
  );
  check(
    'records failed attempt via the throttle helper',
    /_resetThrottleRecordFailure\(/.test(body),
  );
  check(
    'pre-checks the throttle BEFORE user lookup',
    body.indexOf('_resetThrottleCheck(') < body.indexOf('findUserByEmail('),
    'throttle gate must run before findUserByEmail to avoid timing oracle',
  );
  check(
    'clears throttle on successful reset',
    /_resetThrottleClear\(/.test(body),
  );
}

// ── 2. throttle is bounded ──────────────────────────────────────────────
console.log('\n2. reset-attempt throttle is bounded');
{
  // The throttle constants must be present and within a sane range.
  // The audit asked for ~5 attempts / window with ~1h lockout.
  const maxMatch = SERVER.match(/_RESET_MAX_ATTEMPTS\s*=\s*(\d+)/);
  const windowMatch = SERVER.match(/_RESET_WINDOW_MS\s*=\s*([^;]+);/);
  const lockoutMatch = SERVER.match(/_RESET_LOCKOUT_MS\s*=\s*([^;]+);/);
  check('_RESET_MAX_ATTEMPTS defined', !!maxMatch);
  check('_RESET_WINDOW_MS defined',    !!windowMatch);
  check('_RESET_LOCKOUT_MS defined',   !!lockoutMatch);
  if (maxMatch) {
    const v = parseInt(maxMatch[1], 10);
    check(`max-attempts bounded (1..10): got ${v}`, v >= 1 && v <= 10);
  }
}

// ── 3. post-reset notification fires ────────────────────────────────────
console.log('\n3. post-reset notification email');
{
  check(
    'server.mjs imports sendPasswordChangedNotification',
    /sendPasswordChangedNotification[\s,]/.test(SERVER),
  );
  check(
    'reset-password handler calls sendPasswordChangedNotification',
    /sendPasswordChangedNotification\s*\(/.test(SERVER),
  );
  check(
    'email helper exports sendPasswordChangedNotification',
    /export\s+async\s+function\s+sendPasswordChangedNotification/.test(EMAIL),
  );
  check(
    'email body warns "If you didn\'t do this"',
    /If you didn(?:'|&#39;)t do this/.test(EMAIL),
  );
}

// ── 4. rate limiter covers forgot-password + 2fa/authenticate ──────────
console.log('\n4. rate limiter wired for forgot-password + 2fa/authenticate');
{
  check(
    'forgot-password mounted on TIERS.auth limiter',
    /app\.use\(['"]\/api\/auth\/forgot-password['"][^)]+TIERS\.auth/.test(LIMITER),
  );
  check(
    '2fa/authenticate mounted on TIERS.auth limiter',
    /app\.use\(['"]\/api\/auth\/2fa\/authenticate['"][^)]+TIERS\.auth/.test(LIMITER),
  );
  // Regression guards: prior limiters still present.
  for (const route of [
    '/api/auth/login',
    '/api/auth/register',
    '/api/auth/reset-password',
    '/api/auth/recovery-reset',
  ]) {
    check(
      `legacy limiter on ${route} still mounted`,
      LIMITER.includes(`'${route}'`),
    );
  }
}

console.log(`\nR-F609 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
