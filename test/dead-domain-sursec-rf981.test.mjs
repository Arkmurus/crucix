// test/dead-domain-sursec-rf981.test.mjs
//
// R-F981 — the intel.sursec.co.uk domain was retired (operator confirmed
// 2026-05-28: "intel.sursec.co.uk does not exist anymore"). Any FUNCTIONAL
// reference to it is now a dead link. R-F972 already fixed the served
// public/bd-intelligence.html share footer; R-F981 fixes the remaining
// functional defaults that fell back to the dead host:
//   - lib/auth/email.mjs APP_URL default (password-reset / verification links)
//   - lib/whatsapp/ariaWhatsApp.mjs webhook_url (operator setup string)
//   - frontend/src bd-intelligence share footers (Angular source)
//
// Historical CODE COMMENTS that mention the old host (server.mjs,
// learning_store.mjs) are intentionally left as architecture history.
//
// Run: node test/dead-domain-sursec-rf981.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const read = (...p) => readFileSync(join(__dirname, '..', ...p), 'utf8');

let failures = 0;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) failures++; };

const DEAD = 'intel.sursec.co.uk';
const LIVE = 'intel.arkmurus.com';

console.log('R-F981 — dead sursec domain removed from functional code\n');

const EMAIL = read('lib', 'auth', 'email.mjs');
check('email.mjs has no reference to the dead domain', !EMAIL.includes(DEAD));
check('email.mjs APP_URL default points at the live host',
  /APP_URL\s*=\s*process\.env\.APP_URL\s*\|\|\s*'https:\/\/intel\.arkmurus\.com'/.test(EMAIL));

const WA = read('lib', 'whatsapp', 'ariaWhatsApp.mjs');
check('ariaWhatsApp.mjs has no reference to the dead domain', !WA.includes(DEAD));
check('ariaWhatsApp.mjs webhook_url points at the live host',
  WA.includes(`webhook_url: 'https://${LIVE}/api/whatsapp/incoming'`));

const NG = read('frontend', 'src', 'app', 'dashboard', 'bd-intelligence', 'bd-intelligence.component.ts');
check('Angular bd-intelligence source has no reference to the dead domain', !NG.includes(DEAD));
check('Angular bd-intelligence source uses the live host', NG.includes(LIVE));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
