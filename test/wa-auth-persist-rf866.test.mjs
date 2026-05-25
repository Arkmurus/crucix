// test/wa-auth-persist-rf866.test.mjs
//
// R-F866 — WhatsApp auth must persist across restarts. The listener
// (aria_wa_listener.mjs) reads WA_LISTENER_AUTH_DIR and falls back to an
// EPHEMERAL ./wa-listener-auth when it's unset. Dockerfile.wa was setting a
// DIFFERENT variable (WHATSAPP_AUTH_DIR), so the auth dir was never on the
// /data volume → wiped every restart → perpetual QR-rescan loop (408). This
// test pins that the Dockerfile env var NAME matches what the listener reads
// and points at the persistent /data volume.
//
// Run: node test/wa-auth-persist-rf866.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const DOCKERFILE = readFileSync(join(root, 'Dockerfile.wa'), 'utf8');
const LISTENER = readFileSync(join(root, 'services', 'wa-listener', 'aria_wa_listener.mjs'), 'utf8');

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.log(`  ✗ ${label}`); failures++; }
}

console.log('R-F866 — WhatsApp auth persistence (env var name match)\n');

// Which var does the listener actually read for the auth dir?
const m = LISTENER.match(/const AUTH_DIR\s*=\s*process\.env\.(\w+)/);
check('listener reads an auth-dir env var', !!m);
const listenerVar = m ? m[1] : null;
check(`listener var is WA_LISTENER_AUTH_DIR (got ${listenerVar})`, listenerVar === 'WA_LISTENER_AUTH_DIR');

// The Dockerfile must set THAT SAME var (not the old WHATSAPP_AUTH_DIR) …
check('Dockerfile.wa sets the var the listener reads',
  new RegExp(`ENV\\s+${listenerVar}=`).test(DOCKERFILE) || new RegExp(`\\b${listenerVar}=`).test(DOCKERFILE));
// … and point it at the persistent /data volume (not an ephemeral path).
check('auth dir is on the /data volume', /WA_LISTENER_AUTH_DIR=\/data\//.test(DOCKERFILE));
// Regression: the old mismatched var must not be the one carrying the auth path.
check('does not rely on the mismatched WHATSAPP_AUTH_DIR for auth',
  !/ENV\s+WHATSAPP_AUTH_DIR=/.test(DOCKERFILE));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
