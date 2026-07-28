// test/aria-brain-staggered-loaders-rf2424.test.mjs
//
// Capability test for R-F2424 — aria-brain command centre loads panels in
// bounded-concurrency batches instead of one 21-wide Promise.all, so a cold
// primed-aggregate doesn't slam the single aria-intel event loop with 21
// simultaneous per-path probes (compounding the state_store contention that
// trips the DATA-UNAVAILABLE banner).
//
// Extracts the REAL runStaggered() from public/aria-brain.html and drives it
// with instrumented loaders, asserting: peak concurrency <= batch size, every
// loader runs, gaps are applied, and a throwing loader never aborts its wave.
//
// Run: node test/aria-brain-staggered-loaders-rf2424.test.mjs

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
const HTML = readFileSync(join(__dirname, '..', 'public', 'aria-brain.html'), 'utf8');

let failures = 0;
const ok = (c, m) => { console.log(`${c ? '  ✓' : '  ✗'} ${m}`); if (!c) failures++; };

// static guard: refreshAll no longer uses the 21-wide Promise.all
ok(/await runStaggered\(/.test(HTML), 'refreshAll uses runStaggered');
ok(!/await Promise\.all\(\[\s*\n\s*loadHealth\(\)/.test(HTML), 'the old 21-wide Promise.all is gone');

// extract the REAL runStaggered (self-contained; closing brace at column 0)
// R-F3354: the terminator was /\n\}\n/, which requires LF. core.autocrlf=true is
// set system-wide here, so a git checkout on Windows writes CRLF and this match
// returned null — the test then died on m[0] and reported "runStaggered() extracted
// from the page" as the failure, which names the symptom, not the cause. It passed
// only while a working copy happened to carry stray LFs from prior edits; a stash
// round-trip re-normalised the file and exposed it. Accept either terminator.
const m = HTML.match(/async function runStaggered[\s\S]*?\r?\n\}\r?\n/);
ok(!!m, 'runStaggered() extracted from the page');
const sb = { Promise, setTimeout, Array, console };
vm.createContext(sb);
vm.runInContext(m[0], sb);
const runStaggered = sb.runStaggered;

async function main() {
  // 1) concurrency cap + full coverage + gaps
  let active = 0, peak = 0;
  const calls = [];
  const mk = (i) => async () => { active++; peak = Math.max(peak, active); calls.push(i); await new Promise(r => setTimeout(r, 8)); active--; };
  const loaders = Array.from({ length: 21 }, (_, i) => mk(i));
  const t0 = Date.now();
  await runStaggered(loaders, 3, 40);
  const elapsed = Date.now() - t0;
  ok(peak <= 3, `peak concurrency ${peak} <= batch size 3`);
  ok(calls.length === 21, `all 21 loaders ran (${calls.length})`);
  ok(new Set(calls).size === 21, 'each loader ran exactly once');
  // 7 waves → 6 gaps × 40ms = 240ms minimum
  ok(elapsed >= 200, `gaps applied between waves (elapsed ${elapsed}ms >= 200ms)`);

  // 2) order preserved wave-by-wave (first wave is loaders 0,1,2)
  ok(calls.slice(0, 3).sort((a, b) => a - b).join(',') === '0,1,2', 'first wave = first 3 loaders (order preserved)');

  // 3) a throwing loader does not abort its wave-mates (allSettled)
  let ran = 0;
  const good = () => async () => { ran++; };
  const bad = () => async () => { throw new Error('panel boom'); };
  await runStaggered([good(), bad(), good(), good(), bad(), good()], 3, 0);
  ok(ran === 4, `throwing loaders isolated — 4 good panels still ran (${ran})`);

  // 4) size >= length → single wave, no gap wait
  peak = 0; active = 0;
  const small = Array.from({ length: 2 }, (_, i) => mk(i));
  const t1 = Date.now();
  await runStaggered(small, 5, 500);
  ok((Date.now() - t1) < 400, 'no trailing gap when everything fits one wave');

  console.log(failures === 0 ? '\nPASS' : `\nFAIL (${failures})`);
  process.exit(failures === 0 ? 0 : 1);
}
main();
