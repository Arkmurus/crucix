#!/usr/bin/env node
// R-F2729 — Batch 4 quality gate: a real, RUNNABLE, TERMINATING Node lint.
//
// Prospector 2026-07-18 finding #3/#4: `npm run lint` had no script, and the full static gate
// (Prospector/Pylint) timed out — so nothing enforced Node-side quality before deploy. This is
// the minimum honest gate: a SYNTAX check (`node --check`) over every tracked .mjs, which catches
// the exact failure class that caused the R-F2119/2120 outage (autonomous edits pushed 31 syntax
// errors → the whole tree un-importable). Deterministic, bounded, zero-dependency, zero side
// effects (it never executes the modules — only parses them). Exit 1 on any parse failure.
//
// Usage:  npm run lint   (or: node scripts/lint.mjs [--changed])
import { execFileSync } from 'node:child_process';

// Scope: OUR server-side ESM modules only (*.mjs). Deliberately NOT *.js — that set is
// vendored libs (public/vendor/bootstrap, aos), browser scripts (public/js/*), and
// Workflow-tool-persisted bodies (scripts/workflows/*.js legitimately use top-level `return`,
// valid only inside the Workflow harness) — all of which node --check would false-positive as
// broken standalone modules. .mjs is the real R-F2119/2120 syntax-error surface.
const _EXCLUDE = ['node_modules/', 'public/vendor/', 'scripts/workflows/'];
function tracked() {
  const changed = process.argv.includes('--changed');
  const cmd = changed
    ? ['diff', '--name-only', '--diff-filter=ACMR', 'HEAD', '--', '*.mjs']
    : ['ls-files', '*.mjs'];
  let out = '';
  try { out = execFileSync('git', cmd, { encoding: 'utf8' }); }
  catch { console.error('[lint] git not available'); process.exit(2); }
  return out.split('\n').map(s => s.trim()).filter(Boolean)
    .filter(f => !_EXCLUDE.some(x => f.includes(x)));
}

const files = tracked();
const failures = [];
for (const f of files) {
  try {
    execFileSync(process.execPath, ['--check', f], { stdio: ['ignore', 'ignore', 'pipe'] });
  } catch (e) {
    failures.push({ file: f, error: String(e.stderr || e.message).split('\n').slice(0, 3).join(' ').slice(0, 240) });
  }
}

if (failures.length === 0) {
  console.log(`[lint] OK — ${files.length} file(s) parse cleanly (node --check)`);
  process.exit(0);
}
console.error(`[lint] FAILED — ${failures.length}/${files.length} file(s) have syntax errors:`);
for (const { file, error } of failures) console.error(`  ✖ ${file}\n    ${error}`);
process.exit(1);
