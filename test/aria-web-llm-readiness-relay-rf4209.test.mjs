// R-F4209 — aria-web must preserve every definitive aria-intel readiness state.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, '..', 'server.mjs'), 'utf8');
const start = src.indexOf('async function ariaProxy(');
const end = src.indexOf('// Send sweep data', start);
const body = src.slice(start, end);

let failures = 0;
function check(label, condition) {
  if (condition) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F4209 readiness relay contract\n');
check('ariaProxy found', start >= 0 && end > start);
check('warmup remains relayed', body.includes("lastErr.includes('warming_up')"));
check('exhausted LLM state is relayed', body.includes("lastErr.includes('llm_unavailable')"));
check('unknown LLM health state is relayed', body.includes("lastErr.includes('llm_health_unavailable')"));
check('definitive readiness branch returns upstream JSON',
  /return res\.status\(503\)\.json\(JSON\.parse\(lastErr\)\)/.test(body));
check('fallback is honest when readiness JSON is malformed',
  body.includes("error: 'llm_unavailable'") && body.includes('model capacity is unavailable'));

console.log(`\n${failures === 0 ? 'PASS' : 'FAIL'} — ${failures} failure(s)`);
process.exit(failures === 0 ? 0 : 1);
