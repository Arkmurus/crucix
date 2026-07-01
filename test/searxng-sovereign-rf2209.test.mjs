// test/searxng-sovereign-rf2209.test.mjs
//
// CAPABILITY test for R-F2209 — the Node search tier (lib/search/engine.mjs +
// lib/self/web_explorer.mjs) must target the SOVEREIGN aria-searxng instance,
// not the dead public instances it previously hardcoded (searx.be /
// search.mdosch.de / searxng.world[dead] / paulgo.io). It invokes the real
// resolver (searxngInstances) under different env and locks the source so the
// dead instances can't creep back.
//
// Run: node test/searxng-sovereign-rf2209.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
let failures = 0;
function check(name, cond) {
  console.log(`${cond ? 'ok  ' : 'FAIL'} - ${name}`);
  if (!cond) failures++;
}

// ── Behaviour of the real resolver ────────────────────────────────────────────
delete process.env.SEARXNG_URL;
const { searxngInstances } = await import('../lib/search/engine.mjs');

check('default (no env) resolves to the sovereign internal instance',
  JSON.stringify(searxngInstances()) === JSON.stringify(['http://aria-searxng.internal:8080']));

process.env.SEARXNG_URL = 'https://searxng.example.com/';
check('SEARXNG_URL override is honoured and trailing slash stripped',
  JSON.stringify(searxngInstances()) === JSON.stringify(['https://searxng.example.com']));

process.env.SEARXNG_URL = 'http://a.internal:8080 , http://b.internal:8080/';
check('comma-separated override yields multiple trimmed instances',
  JSON.stringify(searxngInstances()) === JSON.stringify(['http://a.internal:8080', 'http://b.internal:8080']));
delete process.env.SEARXNG_URL;

// ── Source-contract: no dead public instances anywhere in the Node search tier ─
const DEAD = ['searx.be', 'search.mdosch.de', 'searxng.world', 'paulgo.io'];
for (const rel of ['lib/search/engine.mjs', 'lib/self/web_explorer.mjs']) {
  const src = readFileSync(join(__dirname, '..', rel), 'utf8');
  for (const d of DEAD) {
    check(`${rel} no longer references dead public instance '${d}'`, !src.includes(d));
  }
  check(`${rel} resolves instances via searxngInstances()`, src.includes('searxngInstances()'));
}

console.log(failures === 0 ? '\nR-F2209 tests: PASS' : `\nR-F2209 tests: ${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);
