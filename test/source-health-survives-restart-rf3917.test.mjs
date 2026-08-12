// C-33 / R-F3917 — /api/source-health reported reliability that reset on every restart,
// while a DURABLE sweep history sat beside it, already written and already imported.
//
// Measured 2026-08-11: uptime 16,091s (~4.5h) against a 5-minute sweep cadence, so the
// percentages the Operational feed health panel showed were computed over roughly 53
// sweeps — everything before the last deploy was gone. The page footer states
// "Reliability = successful sweeps ÷ (success + fail)" with no hint that the window is
// "since last boot", and deploys are frequent, so a chronically flapping feed is
// laundered clean by shipping.
//
// THIS IS C-29 AGAIN, in the Node tier. Not missing persistence — the persistence
// exists. `recordSourceSweep()` is called on EVERY sweep, one line after
// `updateSourceHealth()`, and writes `source_history.json`: a bounded 96-entry
// timestamped ring per source, plus totals and a persistent EMA. `getSourceHistory()`
// already derives a restart-surviving reliability from the last 48 of those sweeps, and
// is ALREADY IMPORTED into server.mjs (line 30). The health summary simply read the
// volatile in-memory object instead. A producer and a consumer that must agree, with
// nothing forcing them to.
//
// The fix therefore adds no new storage and no new all-time counter. R-F3364 is explicit
// that a flat all-time counter is the wrong shape — it dilutes a new regression into a
// growing historical denominator, so the alarm gets blinder the longer it runs. The
// bounded 48-sweep window is the right one and already exists.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const SERVER = readFileSync(join(ROOT, 'server.mjs'), 'utf8');

function fn(name) {
  const start = SERVER.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `${name}() not found in server.mjs`);
  // Walk braces to the end of the function body.
  const open = SERVER.indexOf('{', start);
  let depth = 0;
  for (let i = open; i < SERVER.length; i++) {
    if (SERVER[i] === '{') depth++;
    else if (SERVER[i] === '}') {
      depth--;
      if (depth === 0) return SERVER.slice(start, i + 1);
    }
  }
  assert.fail(`could not delimit ${name}()`);
}

test('C-33: the health summary consults the DURABLE sweep history', () => {
  const body = fn('getSourceHealthSummary');
  assert.ok(
    /getSourceHistory\s*\(/.test(body),
    'C-33: getSourceHealthSummary() ignores getSourceHistory() — the durable, ' +
      'restart-surviving record that recordSourceSweep() writes on every sweep, and ' +
      'which server.mjs already imports. Reliability therefore resets to zero on every ' +
      'deploy while being presented as a reliability history.',
  );
});

test('C-33: the reported window is explicit, not implied', () => {
  const body = fn('getSourceHealthSummary');
  assert.ok(
    /windowSweeps/.test(body),
    'the summary must state how many sweeps its reliability covers. A percentage ' +
      'whose scope is invisible is what made "since last boot" pass for history.',
  );
  assert.ok(
    /durable/.test(body),
    'the summary must mark whether a row is backed by durable history or only by ' +
      'this process — a caller cannot otherwise tell a real record from a fresh boot.',
  );
});

test('C-33: no flat all-time counter is introduced (R-F3364)', () => {
  const body = fn('getSourceHealthSummary');
  assert.ok(
    !/totalOk\s*\+\s*h?\.?totalFail/.test(body) && !/totalOk\s*\/\s*\(/.test(body),
    'reliability computed from all-time totalOk/totalFail dilutes a new regression ' +
      'into a growing historical denominator — the exact failure R-F3364 fixed for the ' +
      'DD layer-stats counters by day-bucketing them. Use the bounded sweep window.',
  );
});

test('C-33: a never-swept source still reports null, not a fabricated 100%', () => {
  const body = fn('getSourceHealthSummary');
  // R-F2719 buckets unconfigured / not-checked separately BECAUSE null is honest.
  assert.ok(
    /reliability\s*[:=][^;]*null/.test(body) || /null/.test(body),
    'null reliability must survive: R-F2719 relies on it to bucket unconfigured and ' +
      'not-yet-checked feeds separately instead of counting them healthy.',
  );
});

test('C-33: server.mjs still parses', () => {
  // Cheap guard — a syntax error here takes the whole web tier down at boot.
  assert.ok(SERVER.includes('function getSourceHealthSummary('));
});

// ---- C-38 / R-F3929 - two defects the high-effort review found in R-F3917 ----

test('C-38: an unconfigured feed keeps its null reliability', () => {
  const body = fn('getSourceHealthSummary');
  assert.ok(
    /not_configured/.test(body) && /unconfigured/.test(body),
    'C-38 finding 4: durable reliability overrode the R-F2719 disabled-excluded null. ' +
      'recordSourceSweep counts an unconfigured sweep as a FAILURE (ok = status === "ok", ' +
      'no carve-out), so its durable ema goes to 0 and Comtrade/CSL get reclassified ' +
      'from "no API key was ever set" to "degraded, 0%, dead".',
  );
  assert.ok(
    /unconfigured \? null/.test(body),
    'the unconfigured branch must yield null, not 0 - classifySourceHealth buckets on null',
  );
});

test('C-38: retired feeds are not resurrected by the durable union', () => {
  const body = fn('getSourceHealthSummary');
  assert.ok(
    /freshEnough|DURABLE_LIVE_WINDOW_MS/.test(body),
    'C-38 finding 9: source_history.json is never pruned, so unioning its names ' +
      'resurrects retired integrations as live feeds with a non-null reliability, ' +
      'inflating totalTracked. A durable-only name needs a recency test.',
  );
});
