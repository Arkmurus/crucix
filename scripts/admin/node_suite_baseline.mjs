#!/usr/bin/env node
// scripts/admin/node_suite_baseline.mjs
//
// R-F3862 — a failure-SET gate for the Node suite, the counterpart to
// scripts/admin/suite_baseline.py (R-F3373) on the Python side.
//
// ── WHY THE NODE SUITE HAD NO GATE ───────────────────────────────────────────
// ci.yml runs `npm test` with `continue-on-error: true` because the suite
// carries a standing set of failures. That is an honest flag — hard-failing on
// them would make CI permanently red — but it means a REAL regression cannot
// fail the build either. R-F3855 carved the two DOM-XSS guards out into their own
// blocking step; this closes the remaining ~1750 tests.
//
// ── WHY A SET AND NOT A COUNT ────────────────────────────────────────────────
// CLAUDE.md §16, learned the hard way on the Python side: "Diff the failure SET,
// never the count alone." A count moves legitimately whenever tests are added,
// and it hides a 1-for-1 swap — one test fixed, one broken, total unchanged.
//
// Usage:
//   node scripts/admin/node_suite_baseline.mjs --record   # write the baseline
//   node scripts/admin/node_suite_baseline.mjs            # gate against it
//
// Exit 0 = no NEW failures. Exit 1 = at least one failure absent from the
// baseline. Fixed tests are reported and never fail the run, so the baseline can
// be refreshed deliberately rather than drifting.

import { execFile } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

const run = promisify(execFile);
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const BASELINE = path.join(ROOT, 'docs', 'node_suite_baseline.json');

/**
 * Run the suite and return the set of failing test names plus the totals.
 *
 * Parses TAP rather than trusting the exit code: `npm test` exits non-zero for
 * the standing failures too, so the code alone cannot distinguish "same as
 * yesterday" from "new regression".
 */
let lastRawOutput = '';

async function runSuite() {
  let stdout = '';
  try {
    ({ stdout } = await run('npm', ['test'], {
      cwd: ROOT, shell: true, maxBuffer: 64 * 1024 * 1024,
    }));
  } catch (e) {
    stdout = (e.stdout || '') + (e.stderr || '');
  }
  // R-F3903 — keep the raw output so a parse failure can SAY WHY. Without this the
  // gate refuses correctly and silently, which is what left it red in CI for 8+
  // consecutive commits with nobody able to act on it.
  lastRawOutput = stdout;
  const failures = new Set();
  for (const line of stdout.split(/\r?\n/)) {
    const m = /^\s*not ok \d+ - (.+?)\s*$/.exec(line);
    // A suite-level "not ok" restates its children; keeping both double-counts.
    if (m && !/^# /.test(m[1])) failures.add(m[1]);
  }
  const num = (re) => { const m = re.exec(stdout); return m ? Number(m[1]) : null; };
  return {
    failures: [...failures].sort(),
    totals: {
      tests: num(/^# tests (\d+)$/m),
      pass: num(/^# pass (\d+)$/m),
      fail: num(/^# fail (\d+)$/m),
      skipped: num(/^# skipped (\d+)$/m),
    },
  };
}

const record = process.argv.includes('--record');

const result = await runSuite();
if (result.totals.tests === null) {
  // R-F3903 — REFUSING IS RIGHT; REFUSING IN SILENCE IS NOT.
  //
  // This gate failed on 8+ consecutive commits in CI (both agents' work) with this
  // one line and nothing else, while `node scripts/admin/node_suite_baseline.mjs`
  // passed locally — 1833 passed / 8 failed, exit 0. So the suite behaves
  // differently under CI's pinned Node 20 than under a dev Node 22, and the gate
  // printed nothing that could distinguish "the runner crashed before emitting a
  // summary" from "the TAP format changed".
  //
  // A guard that cannot say WHY it could not measure is one nobody can fix, so it
  // stays red until someone mutes it — the failure mode every allowlist and gate in
  // this repo is written against. The tail is capped so a 64MB buffer cannot flood
  // the CI log.
  console.error('[node-baseline] could not parse TAP totals — refusing to record or gate');
  const tail = (lastRawOutput || '').split(/\r?\n/).filter(Boolean).slice(-40);
  console.error(`[node-baseline] captured ${lastRawOutput.length} bytes of output; last ${tail.length} non-empty line(s):`);
  for (const line of tail) console.error(`  | ${line.slice(0, 300)}`);
  if (!lastRawOutput.trim()) {
    console.error('[node-baseline] the suite produced NO output at all — `npm test` did not start.');
  }
  process.exit(2);
}
// A run that collected almost nothing is a broken runner, not a green suite.
if (result.totals.tests < 100) {
  console.error(`[node-baseline] only ${result.totals.tests} tests collected — runner is broken, not passing`);
  process.exit(2);
}

if (record) {
  fs.mkdirSync(path.dirname(BASELINE), { recursive: true });
  fs.writeFileSync(BASELINE, `${JSON.stringify({
    recorded_at: new Date().toISOString().slice(0, 10),
    node: process.version,
    totals: result.totals,
    failures: result.failures,
  }, null, 2)}\n`);
  console.log(`[node-baseline] recorded ${result.failures.length} standing failures `
    + `(${result.totals.pass} passed / ${result.totals.fail} failed) -> ${path.relative(ROOT, BASELINE)}`);
  process.exit(0);
}

if (!fs.existsSync(BASELINE)) {
  console.log('[node-baseline] no baseline yet — run with --record first. Not gating.');
  process.exit(0);
}

const base = JSON.parse(fs.readFileSync(BASELINE, 'utf8'));
const known = new Set(base.failures || []);
const now = new Set(result.failures);
const added = [...now].filter((f) => !known.has(f));
const fixed = [...known].filter((f) => !now.has(f));

console.log(`[node-baseline] ${result.totals.pass} passed / ${result.totals.fail} failed `
  + `(baseline ${base.totals?.pass} / ${base.totals?.fail}, recorded ${base.recorded_at})`);
if (fixed.length) {
  console.log(`[node-baseline] FIXED since the baseline (${fixed.length}) — refresh it when deliberate:`);
  for (const f of fixed) console.log(`    - ${f}`);
}
if (added.length) {
  console.error(`[node-baseline] NEW FAILURES (${added.length}):`);
  for (const f of added) console.error(`  ! ${f}`);
  process.exit(1);
}
console.log('[node-baseline] no new failures.');
process.exit(0);
