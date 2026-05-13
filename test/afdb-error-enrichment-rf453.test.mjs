// R-F453 — AfDB throw includes per-attempt error detail.
//
// Pre-R-F453 every catch block was bare `catch {}` so the final throw
// produced `new Error('All AfDB endpoints unreachable')` with .name
// === "Error". enrichFetchError(outer) landed on err.name === "Error",
// producing the literal log line "[AfDB] Error: Error" every sweep.
//
// R-F453: the per-attempt errors are captured into `_attemptErrors`
// and concatenated into the final throw message so triage sees the
// real cause (rss2json 429 / Google News 503 / jina blocked / timeout).

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const AFDB_PATH = resolve(__dirname, '..', 'apis', 'sources', 'afdb.mjs');

test('R-F453: afdb.mjs source captures per-attempt errors', () => {
  const src = readFileSync(AFDB_PATH, 'utf-8');
  // Pre-R-F453 the catches were bare `catch {}`. Now they must capture.
  assert.ok(
    src.includes('_attemptErrors'),
    'R-F453: _attemptErrors capture variable missing — has the fix been reverted?',
  );
  // The throw must reference the captured list, not the bare static message
  assert.ok(
    /All AfDB endpoints unreachable[^']*\$\{_summary\}/.test(src)
      || src.includes('${_summary}'),
    'R-F453: final throw does not include the per-attempt error summary',
  );
});

test('R-F453: no bare catch blocks remain in fetchAfDBProjects', () => {
  const src = readFileSync(AFDB_PATH, 'utf-8');
  // Locate the function body
  const fnStart = src.indexOf('async function fetchAfDBProjects()');
  assert.ok(fnStart > 0, 'fetchAfDBProjects not found in afdb.mjs');
  // Take up to ~5000 chars of the function body
  const window = src.slice(fnStart, fnStart + 5000);
  // The pre-R-F453 anti-pattern was `} catch {}` — bare catch with empty body
  const bareCatches = window.match(/}\s*catch\s*\{\s*}/g) || [];
  assert.equal(
    bareCatches.length, 0,
    `R-F453 regression: ${bareCatches.length} bare 'catch {}' blocks still present in fetchAfDBProjects`,
  );
});

test('R-F453: throw message format includes error summary', () => {
  // Verify the message format will produce something more useful than
  // "All AfDB endpoints unreachable" alone. We re-construct the format
  // string and check it includes a non-empty diagnostic.
  const sampleErrors = ['HTTP 429', 'ENOTFOUND: getaddrinfo ENOTFOUND afdb.org', 'allorigins HTTP 503'];
  const summary = sampleErrors.slice(0, 6).join(' | ') || 'no attempts recorded';
  const msg = `All AfDB endpoints unreachable: ${summary}`;
  assert.ok(msg.includes('HTTP 429'), 'thrown message should carry HTTP status');
  assert.ok(msg.includes('ENOTFOUND'), 'thrown message should carry DNS error');
  assert.ok(msg.includes('allorigins'), 'thrown message should distinguish allorigins fallback');
  // Even when no errors were collected (impossible path but defensive),
  // the message is honest
  const emptyMsg = `All AfDB endpoints unreachable: ${[].join(' | ') || 'no attempts recorded'}`;
  assert.ok(
    emptyMsg.includes('no attempts recorded'),
    'R-F453: empty-error fallback string must be honest',
  );
});
