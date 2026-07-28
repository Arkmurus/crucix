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

import { readFileSync, readdirSync } from 'node:fs';
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
// imaria.io rebrand (R-F2244): the canonical production host is now
// intel.imaria.io (server.mjs:6388 "canonical production host"), not the
// old intel.arkmurus.com. email.mjs:49 default was updated to match.
check('email.mjs APP_URL default points at the live host',
  /APP_URL\s*=\s*process\.env\.APP_URL\s*\|\|\s*'https:\/\/intel\.imaria\.io'/.test(EMAIL));

const WA = read('lib', 'whatsapp', 'ariaWhatsApp.mjs');
check('ariaWhatsApp.mjs has no reference to the dead domain', !WA.includes(DEAD));
check('ariaWhatsApp.mjs webhook_url points at the live host',
  WA.includes(`webhook_url: 'https://${LIVE}/api/whatsapp/incoming'`));

// R-F3340 — the two checks that used to live here read
//   frontend/src/app/dashboard/bd-intelligence/bd-intelligence.component.ts
// and had been throwing ENOENT ever since R-F2624 deleted frontend/ ("dead
// Seenode-era Angular SPA"). The tree is gone by decision, so asserting its
// contents asserts a structure that no longer exists — and the throw took the
// two checks ABOVE down with it, which are still live and still matter.
//
// Replaced with a SWEEP rather than another hardcoded path list. Naming three
// files was always the weak part of this guard: it could not see a fourth file
// introducing the dead host, and it broke the moment one of the three moved.
// Scanning what is actually on disk catches both.
//
// Comments are exempt, per this file's own header: historical mentions in
// server.mjs / learning_store.mjs are deliberate architecture history. Only
// FUNCTIONAL references count, so line and block comments are stripped first.

function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, '')          // block comments
    .replace(/^\s*\/\/.*$/gm, '')               // whole-line // comments
    .replace(/<!--[\s\S]*?-->/g, '');            // html comments
}

function sweep(dir, exts, out = []) {
  for (const entry of readdirSync(join(__dirname, '..', dir), { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue;
    const rel = join(dir, entry.name);
    if (entry.isDirectory()) sweep(rel, exts, out);
    else if (exts.some((e) => entry.name.endsWith(e))) out.push(rel);
  }
  return out;
}

const SCANNED = [...sweep('lib', ['.mjs', '.js']), ...sweep('public', ['.html', '.js'])];
const offenders = SCANNED.filter((f) => stripComments(read(f)).includes(DEAD));

check(`no functional reference to the dead domain in lib/ or public/ (${SCANNED.length} files scanned)`,
  offenders.length === 0);
if (offenders.length) console.log('    offenders: ' + offenders.join(', '));
check('the sweep actually reached files (not a vacuous pass)', SCANNED.length > 20);

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
