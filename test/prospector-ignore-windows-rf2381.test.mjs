import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const config = readFileSync(new URL('../.prospector.yml', import.meta.url), 'utf8');

function ignorePatterns() {
  const lines = config.split(/\r?\n/);
  const start = lines.findIndex((line) => line.trim() === 'ignore-patterns:');
  assert.notEqual(start, -1, 'ignore-patterns section must exist');
  const patterns = [];
  for (const line of lines.slice(start + 1)) {
    if (/^\S/.test(line)) break;
    const match = line.match(/^\s*-\s+(.+?)\s*$/);
    if (match) patterns.push(match[1]);
  }
  return patterns.map((pattern) => new RegExp(pattern));
}

function ignored(path) {
  return ignorePatterns().some((pattern) => pattern.test(path));
}

test('R-F2381 Prospector ignores tests on Windows and POSIX paths', () => {
  assert.equal(ignored('aria_service\\tests\\test_imports.py'), true);
  assert.equal(ignored('aria_cli\\tests\\test_cap_rf1265_content_overflow.py'), true);
  assert.equal(ignored('aria_service/tests/test_imports.py'), true);
  assert.equal(ignored('conftest.py'), true);
  assert.equal(ignored('aria_service\\conftest.py'), true);
});

test('R-F2381 Prospector ignores generated helper and package files cross-platform', () => {
  assert.equal(ignored('scripts\\admin\\reserve_r_number.py'), true);
  assert.equal(ignored('scripts/admin/reserve_r_number.py'), true);
  assert.equal(ignored('aria_service\\migrations\\001_bootstrap.py'), true);
  assert.equal(ignored('aria_service/__init__.py'), true);
  assert.equal(ignored('setup.py'), true);
});

test('R-F2381 Prospector keeps production modules visible', () => {
  assert.equal(ignored('aria_service\\routes\\aria.py'), false);
  assert.equal(ignored('aria_service/intel/dd_orchestrator.py'), false);
  assert.equal(ignored('aria_cli\\cli.py'), false);
});
