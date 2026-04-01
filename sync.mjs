// sync.mjs — fetches latest source files from GitHub during Seenode build
// Runs as part of the build command to bypass stale Docker layer cache.
// Uses Node.js built-in fetch (Node 18+). No extra deps required.

import { writeFileSync, mkdirSync } from 'fs';

const REPO  = 'Arkmurus/crucix';
const BRANCH = 'main';
const BASE  = `https://raw.githubusercontent.com/${REPO}/${BRANCH}`;

const FILES = [
  'server.mjs',
  'lib/alerts/telegram.mjs',
  'lib/intel/dedup.mjs',
  'lib/intel/oem_db.mjs',
  'lib/search/engine.mjs',
  'apis/sources/afdb.mjs',
  'apis/sources/cyber_threats.mjs',
  'apis/sources/export_control_intel.mjs',
  'apis/sources/gdelt.mjs',
  'apis/sources/lusophone.mjs',
  'apis/sources/opensanctions.mjs',
  'apis/sources/port_congestion.mjs',
  'apis/sources/supply_chain.mjs',
];

console.log(`[sync] Pulling ${FILES.length} files from github.com/${REPO}@${BRANCH}...`);

let ok = 0;
let fail = 0;

for (const f of FILES) {
  try {
    const res = await fetch(`${BASE}/${f}`, { signal: AbortSignal.timeout(15000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const content = await res.text();
    const dir = f.includes('/') ? f.substring(0, f.lastIndexOf('/')) : null;
    if (dir) mkdirSync(dir, { recursive: true });
    writeFileSync(f, content, 'utf8');
    console.log(`[sync] ✓ ${f}`);
    ok++;
  } catch (err) {
    console.error(`[sync] ✗ ${f}: ${err.message}`);
    fail++;
  }
}

console.log(`[sync] Done: ${ok} synced, ${fail} failed`);
if (fail > 0) process.exit(1);
