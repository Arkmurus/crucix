import { test } from 'node:test';
import assert from 'node:assert/strict';
import { cpSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';

const ROOT = path.resolve(import.meta.dirname, '..');
const html = readFileSync(path.join(ROOT, 'public/index.html'), 'utf8');

function runGuard(cwd = ROOT) {
  try {
    execFileSync(process.execPath, [path.join(cwd, 'scripts/audit/landing_claim_truth.mjs')],
      { cwd, stdio: 'pipe' });
    return { ok: true, out: '' };
  } catch (error) {
    return { ok: false, out: String(error.stdout || '') + String(error.stderr || '') };
  }
}

function guardWithLandingMutation(transform) {
  const dir = mkdtempSync(path.join(tmpdir(), 'landing-guard-'));
  try {
    for (const rel of [
      'public/index.html',
      'public/capability-claims.json',
      'scripts/audit/landing_claim_truth.mjs',
      'scripts/build_landing_page.py',
      'aria_service/intel/dd_orchestrator.py',
      'aria_service/intel/political_risk_index.py',
      'aria_service/autonomous/tasks.yaml',
    ]) {
      const target = path.join(dir, rel);
      mkdirSync(path.dirname(target), { recursive: true });
      cpSync(path.join(ROOT, rel), target);
    }
    const landing = path.join(dir, 'public/index.html');
    writeFileSync(landing, transform(readFileSync(landing, 'utf8')), 'utf8');
    return runGuard(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test('R-F2824/R-F3297 baseline truth guard passes', () => {
  const result = runGuard();
  assert.ok(result.ok, result.out);
});

test('landing contains no fabricated entity verdict or testimonial', () => {
  assert.doesNotMatch(html, /Khalid Al-Rashid|Meridian Trading|✓\s*CLEAR|PEP:\s*NEGATIVE/i);
  assert.doesNotMatch(html, /Albert Rossi|Melissa Vanbergh|Joshua Peterson/);
});

test('landing contains no unbacked absolute or false cadence claim', () => {
  assert.doesNotMatch(html, /Nothing missed|GDPR Compliant|No external dependencies|24\s*\/\s*7/i);
});

test('negative control: unsupported absolute fails the guard', () => {
  const result = guardWithLandingMutation((source) =>
    source.replace('Decisions you can trace back to evidence.', 'Nothing missed'));
  assert.ok(!result.ok, 'the guard must reject an unsupported absolute');
  assert.match(result.out, /unsupported absolute|required honest framing/i);
});

test('negative control: fabricated CLEAR fails the guard', () => {
  const result = guardWithLandingMutation((source) =>
    source.replace('Evidence before assertion.', '✓ CLEAR'));
  assert.ok(!result.ok, 'the guard must reject a fabricated verdict');
  assert.match(result.out, /CLEAR|invented outcome/i);
});

test('negative control: false continuous-monitoring claim fails the guard', () => {
  const result = guardWithLandingMutation((source) =>
    source.replace('Continuous monitoring', '24/7 monitoring'));
  assert.ok(!result.ok, 'the weekly watchlist must not be described as 24/7');
  assert.match(result.out, /24\/7|weekly job/i);
});
