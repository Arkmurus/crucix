// R-F4181 capability gate: exercise npm's advisory resolver against the exact
// aria-app lockfile. A package-version assertion alone would miss vulnerable
// transitive PostCSS or Sharp nodes.

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const appDir = fileURLToPath(new URL('../aria-app/', import.meta.url));
const executable = process.platform === 'win32' ? process.env.ComSpec : 'npm';
const args = process.platform === 'win32'
  ? ['/d', '/s', '/c', 'npm audit --json'] : ['audit', '--json'];
const run = spawnSync(executable, args, {
  cwd: appDir,
  encoding: 'utf8',
  timeout: 60_000,
});

assert.notEqual(run.error?.code, 'ETIMEDOUT', 'npm audit must complete within 60 seconds');
assert.ok(run.stdout, `npm audit produced no JSON: ${run.stderr}`);

const report = JSON.parse(run.stdout);
assert.deepEqual(report.vulnerabilities, {},
  `aria-app dependency graph contains advisories: ${JSON.stringify(report.vulnerabilities)}`);
assert.equal(report.metadata?.vulnerabilities?.total, 0,
  'aria-app must have zero known dependency vulnerabilities');
assert.equal(run.status, 0, `npm audit exited ${run.status}: ${run.stderr}`);

console.log('R-F4181 capability: locked aria-app dependency graph has zero npm advisories');
