// test/api-get-error-envelope-rf464.test.mjs
//
// Capability test for R-F464 — API.get error-envelope detection.
//
// Web/UI audit P0 (next_session_pickup_2026_05_15): 9 of 13 pages
// (dashboard, opportunities, account, admin, bd-intelligence, sources,
// etc.) bypass fetchJson() and call API.get() directly. Pre-R-F464,
// API.get silently returned `{error: "ARIA service offline"}` from
// seenode's _brainFallback as if it were data. Callers doing
// `if (!data) return` proceeded with the error object as the happy
// path → silent fallback rendered fake placeholder values.
//
// R-F464 lifts the fetchJson `data.error` check into API.get itself.
// Every page using API.get now gets the same loud-fail treatment.
//
// Static-source guard plus a runtime simulation of the envelope detector.
//
// Run: node test/api-get-error-envelope-rf464.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(
  join(__dirname, '..', 'public', 'js', 'app.js'),
  'utf8'
);

let failures = 0;
function check(label, cond) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}`); failures += 1; }
}

console.log('R-F464 capability tests\n');

// ── 1. Static source: _isErrorEnvelope wired into API.get ───────────
console.log('1. API.get pipes through _isErrorEnvelope check');
{
  check('_isErrorEnvelope helper defined', SRC.includes('_isErrorEnvelope'));
  // The `get()` method must invoke it on the parsed body
  const getStart = SRC.indexOf('  async get(path)');
  check('async get method defined', getStart > -1);
  const getBody = SRC.slice(getStart, getStart + 1000);
  check('parses body via await r.json()', getBody.includes('await r.json()'));
  check('checks _isErrorEnvelope on data', getBody.includes('_isErrorEnvelope(data)'));
  check('returns null on error envelope',
        /isErrorEnvelope\(data\)[\s\S]+?return null/.test(getBody));
}

// ── 2. Runtime envelope-detection truth table ───────────────────────
console.log('\n2. _isErrorEnvelope behaviour (mirrored implementation)');
{
  // Mirror the implementation to test in Node (the real app.js requires
  // a browser global `fetch` to evaluate top-to-bottom).
  function _isErrorEnvelope(data) {
    if (!data || typeof data !== 'object' || Array.isArray(data)) return false;
    const keys = Object.keys(data);
    if (!keys.includes('error') || !data.error) return false;
    const errEnvelope = new Set(['error', 'fly_status', 'fly_error', 'path', 'detail']);
    return keys.every(k => errEnvelope.has(k));
  }

  // The seenode/fly error envelope must be detected
  check('detects seenode _brainFallback shape',
        _isErrorEnvelope({ error: 'ARIA service offline' }) === true);
  check('detects fly proxy error shape',
        _isErrorEnvelope({
          error: 'fly endpoint unavailable',
          path: '/api/aria/x',
          fly_status: 0,
          fly_error: 'fetch failed',
        }) === true);
  check('detects FastAPI HTTPException detail shape',
        _isErrorEnvelope({ error: 'bad', detail: 'why' }) === true);

  // Real APIs returning {results, error: null} are NOT envelopes
  check('does NOT flag {results, error: null} as envelope',
        _isErrorEnvelope({ results: [], error: null }) === false);
  check('does NOT flag {results, error: ""} as envelope',
        _isErrorEnvelope({ results: [], error: '' }) === false);
  check('does NOT flag responses without error key',
        _isErrorEnvelope({ status: 'healthy', uptime: 123 }) === false);

  // Defensive: handle null, undefined, primitives, arrays
  check('does NOT flag null',     _isErrorEnvelope(null) === false);
  check('does NOT flag undefined',_isErrorEnvelope(undefined) === false);
  check('does NOT flag arrays',   _isErrorEnvelope([{ error: 'x' }]) === false);
  check('does NOT flag strings',  _isErrorEnvelope('error string') === false);
  check('does NOT flag numbers',  _isErrorEnvelope(42) === false);

  // Mixed: real-shape with error field gets through (don't break partial-success APIs)
  check('does NOT flag {data, error} (real partial-success)',
        _isErrorEnvelope({ data: { x: 1 }, error: 'warn' }) === false);
}

console.log(`\nR-F464 tests: ${failures === 0 ? 'PASS' : `FAIL (${failures})`}`);
process.exit(failures === 0 ? 0 : 1);
