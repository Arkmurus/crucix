import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import test from 'node:test';

function checkIgnore(path) {
  try {
    execFileSync('git', ['check-ignore', path], { encoding: 'utf8' });
    return true;
  } catch {
    return false;
  }
}

test('R-F2382 ignores generated Prospector rerun bundles only', () => {
  assert.equal(checkIgnore('data/eval_reports/prospector_rerun_20260706/aria_cli_fast.json'), true);
  assert.equal(checkIgnore('data/eval_reports/_scratch_probe.json'), true);
  assert.equal(checkIgnore('data/eval_reports/aria_eval_500q.jsonl'), false);
  assert.equal(checkIgnore('data/aria_training/coder_verifiable_gold.jsonl'), false);
});
