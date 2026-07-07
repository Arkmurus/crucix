// R-F2398 — landing claims must stay evidence-bound.
//
// The landing page is allowed to describe ARIA's ambition, but it must not
// present roadmap capabilities as already complete. This test pins the public
// wording to a machine-readable claim manifest and rejects unsupported absolutes.

import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';
import test from 'node:test';
import assert from 'node:assert/strict';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..');

test('R-F2398 landing claim truth audit passes', () => {
  const result = spawnSync(
    process.execPath,
    [join(repoRoot, 'scripts', 'audit', 'landing_claim_truth.mjs')],
    { cwd: repoRoot, encoding: 'utf8' },
  );
  assert.equal(
    result.status,
    0,
    `${result.stdout}\n${result.stderr}`,
  );
  assert.match(result.stdout, /landing claim truth passed:/);
});

test('R-F2398 capability manifest records claim evidence and completion gates', () => {
  const manifest = JSON.parse(
    readFileSync(join(repoRoot, 'public', 'capability-claims.json'), 'utf8'),
  );
  const ids = manifest.claims.map((claim) => claim.id);
  assert.deepEqual(ids, [
    'sovereign_grade_roadmap',
    'evidence_graded_dd',
    'live_source_health',
    'vault_curated_sources',
    'audit_verifiable_reports',
  ]);
  for (const claim of manifest.claims) {
    assert.ok(claim.evidence.length > 0, `${claim.id} must cite evidence`);
    assert.ok(
      claim.required_to_call_complete.length > 0,
      `${claim.id} must define completion requirements`,
    );
  }
});
