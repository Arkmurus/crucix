// test/node-brain-signal-rf900.test.mjs
//
// R-F900 (P0-4) — the Node tier reports its own failures to the brain.
// Pre-R-F900: server.mjs's /api/brain/signal relay forwarded to a dead
// /api/brain/signal (404) with no auth header, keyed off an often-unset
// BRAIN_URL, and returned a FALSE {status:"queued"}; errorTracker never told
// the brain anything. Now: relay → /api/aria/brain/signal (R-F887) + auth +
// honest error; errorTracker escalates significant failures to the brain.
//
// Run: node test/node-brain-signal-rf900.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRV = readFileSync(join(__dirname, '..', 'server.mjs'), 'utf8');
const ET  = readFileSync(join(__dirname, '..', 'lib', 'observability', 'errorTracker.mjs'), 'utf8');

let failures = 0;
const check = (label, cond) => { console.log((cond ? '  ✓ ' : '  ✗ ') + label); if (!cond) failures++; };

console.log('R-F900 — Node tier reports failures to brain\n');

console.log('server.mjs /api/brain/signal relay:');
check('forwards to the REAL /api/aria/brain/signal endpoint (R-F887)',
  /fetch\(`\$\{brainBase\}\/api\/aria\/brain\/signal`/.test(SRV));
check('no longer forwards to the dead /api/brain/signal',
  !/fetch\(`\$\{BRAIN_URL\}\/api\/brain\/signal`/.test(SRV));
check('sends an Authorization Bearer header',
  /Authorization.*Bearer \$\{brainTok\}/.test(SRV));
check('uses ARIA_SERVICE_URL (the URL aria-web actually has)',
  /brainBase\s*=\s*process\.env\.ARIA_SERVICE_URL/.test(SRV));
check('returns an HONEST error (not a fake "queued") on forward failure',
  /brain signal forward failed/.test(SRV) && /brain signal unreachable/.test(SRV));

console.log('\nerrorTracker.mjs:');
check('has _reportToBrain', /_reportToBrain\(entry\)/.test(ET));
check('record() calls _reportToBrain', /this\._reportToBrain\(entry\)\.catch/.test(ET));
check('reports to /api/aria/brain/signal', /\/api\/aria\/brain\/signal/.test(ET));
check('only escalates CRITICAL/AUTH/STRUCTURAL (not transient noise)',
  /SEVERITY\.CRITICAL,\s*SEVERITY\.AUTH,\s*SEVERITY\.STRUCTURAL/.test(ET));
check('uses a "fail"-bearing signal_type so it routes to capability_gaps',
  /node_tier_failure_/.test(ET));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
