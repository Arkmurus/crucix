// test/protobufjs-ace-override-rf3554.test.mjs
//
// R-F3554 — the npm `overrides` entry that clears a CRITICAL is one line, in a
// file people edit for unrelated reasons, and its absence is silent: the tree
// simply resolves the vulnerable copy again and only `npm audit` notices.
//
// GHSA-xq3m-2v4x-88gg — arbitrary code execution in protobufjs <= 7.6.2.
// baileys@6.7.23 already depends on the patched protobufjs@7.6.5, but
// `libsignal` (a git-pinned dep) carried its own protobufjs@6.8.8, and the
// advisory range covers ALL of 6.x — there is no patched 6.x line. The override
// pins that nested copy to the same 7.6.5 baileys resolves, so the tree dedupes
// to ONE patched copy rather than a major-version fork.
//
// This is the LIVE WhatsApp tier: libsignal decodes Signal-protocol protobuf
// from untrusted remote input, which is exactly the reachable path.

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const lock = JSON.parse(readFileSync(join(root, 'package-lock.json'), 'utf8'));

let failures = 0;
function check(label, cond, detail = '') {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? '\n     ' + detail : ''}`); failures += 1; }
}

console.log('R-F3554 — protobufjs ACE override holds\n');

check('package.json declares the protobufjs override',
  !!(pkg.overrides && pkg.overrides.protobufjs),
  'without it the tree resolves libsignal\'s protobufjs@6.8.8 again — silently');

const MIN_FIXED = [7, 6, 3];   // advisory: vulnerable <= 7.6.2
function parse(v) { return String(v).replace(/^[^\d]*/, '').split('.').map(Number); }
function gte(a, b) {
  for (let i = 0; i < 3; i++) { if ((a[i] || 0) > (b[i] || 0)) return true; if ((a[i] || 0) < (b[i] || 0)) return false; }
  return true;
}
check('the override is at or above the first fixed release (7.6.3)',
  gte(parse(pkg.overrides?.protobufjs || '0'), MIN_FIXED),
  'declared: ' + (pkg.overrides?.protobufjs || 'none'));

// The lockfile is what actually ships. Assert EVERY resolved protobufjs is fixed.
const bad = Object.entries(lock.packages || {})
  .filter(([p]) => p.endsWith('node_modules/protobufjs'))
  .map(([p, m]) => [p, m.version])
  .filter(([, v]) => v && !gte(parse(v), MIN_FIXED));
check('no vulnerable protobufjs remains anywhere in the lockfile',
  bad.length === 0,
  'vulnerable copies still resolved: ' + JSON.stringify(bad));

const resolved = Object.entries(lock.packages || {})
  .filter(([p]) => p.endsWith('node_modules/protobufjs'))
  .map(([p, m]) => `${p}@${m.version}`);
check('at least one protobufjs is actually resolved (guard is not vacuous)',
  resolved.length > 0, 'found: ' + JSON.stringify(resolved));
console.log('     resolved: ' + resolved.join(', '));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
