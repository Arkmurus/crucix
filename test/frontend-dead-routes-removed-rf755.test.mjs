// test/frontend-dead-routes-removed-rf755.test.mjs
//
// R-F755 — dead frontend routes `/pages/coming-soon` and
// `/auth/lock-screen` removed.
//
// Audit on 2026-05-20 found these two Angular routes registered but
// unreachable: no navbar item, no router.navigate(), no link
// anywhere in the app pointed at them. coming-soon was the standard
// scaffold "coming soon" page; lock-screen was a placeholder for a
// session-lock UX that was never wired (the real flow today is
// auto-logout via auth.interceptor.ts on 401).
//
// This test enforces the removal so a future scaffold pass can't
// re-introduce them without a deliberate R-number.
//
// Run: node test/frontend-dead-routes-removed-rf755.test.mjs

import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

console.log('R-F755 capability tests — dead Angular routes removed\n');

// 1. Component directories must be gone.
check(
  'frontend/src/app/auth/lock-screen/ directory removed',
  !existsSync(resolve(ROOT, 'frontend/src/app/auth/lock-screen')),
  'directory still on disk — components/styles/template not deleted'
);
check(
  'frontend/src/app/pages/coming-soon/ directory removed',
  !existsSync(resolve(ROOT, 'frontend/src/app/pages/coming-soon')),
  'directory still on disk — components/styles/template not deleted'
);

// 2. Routing files must not reference the dead routes.
const authRouting = readFileSync(resolve(ROOT, 'frontend/src/app/auth/auth-routing.module.ts'), 'utf8');
check(
  'auth-routing.module.ts: no LockScreenComponent import',
  !/LockScreenComponent/.test(authRouting),
  'import or reference to LockScreenComponent remains — Angular build will fail'
);
check(
  "auth-routing.module.ts: no path: 'lock-screen' route",
  !/path:\s*['"]lock-screen['"]/.test(authRouting),
  'route registration remains'
);

const pagesRouting = readFileSync(resolve(ROOT, 'frontend/src/app/pages/pages-routing.module.ts'), 'utf8');
check(
  'pages-routing.module.ts: no ComingSoonComponent import',
  !/ComingSoonComponent/.test(pagesRouting),
  'import or reference to ComingSoonComponent remains — Angular build will fail'
);
check(
  "pages-routing.module.ts: no path: 'coming-soon' route",
  !/path:\s*['"]coming-soon['"]/.test(pagesRouting),
  'route registration remains'
);

// 3. The auth module declarations must not list LockScreenComponent
//    (would break Angular compilation given the file is gone).
const authModule = readFileSync(resolve(ROOT, 'frontend/src/app/auth/auth.module.ts'), 'utf8');
check(
  'auth.module.ts: LockScreenComponent dropped from declarations',
  !/LockScreenComponent/.test(authModule),
  'declarations still lists removed component — Angular will fail to compile'
);

console.log(`\n${failures === 0 ? '✓ All R-F755 capability checks passed' : `✗ ${failures} check(s) failed`}`);
process.exit(failures === 0 ? 0 : 1);
