// test/admin-system-status-live-rf429.test.mjs
//
// Capability test for R-F429 — /api/auth/system-status reads admin count
// LIVE on every request, not from the boot-frozen snapshot. R-F427 cached
// the count + matchesEnv in _adminIdentitySnapshot at boot, so when R-F428's
// createIfMissing minted an admin at runtime, the endpoint kept reporting
// "no-admin" even though users.json had the new row. This test pins the
// fix: mint a row after boot, hit system-status, assert it reflects the
// new state without restarting the process.
//
// Run: node test/admin-system-status-live-rf429.test.mjs

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const FIXTURE = join(__dirname, 'fixtures', 'system-status-server.mjs');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

function mkUsersFile(rows) {
  const dir = mkdtempSync(join(tmpdir(), 'live-rf429-'));
  const file = join(dir, 'users.json');
  writeFileSync(file, JSON.stringify(rows, null, 2));
  return { dir, file };
}

function startServer(envOverrides) {
  return new Promise((resolveP, reject) => {
    const env = { ...process.env };
    for (const k of [
      'USERS_FILE_OVERRIDE', 'ADMIN_EMAIL', 'ADMIN_PASSWORD',
      'EMAIL_HOST', 'EMAIL_USER', 'EMAIL_PASS',
      'ARIA_EMAIL_HOST', 'ARIA_EMAIL_USER', 'ARIA_EMAIL_PASS',
      'ARIA_SMTP_HOST', 'ARIA_SMTP_PORT', 'ADMIN_RECOVERY_TOKEN',
    ]) delete env[k];
    Object.assign(env, envOverrides);
    const proc = spawn(process.execPath, [FIXTURE], {
      cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) { proc.kill(); reject(new Error(`fixture did not start. stderr=${stderr}`)); }
    }, 10000);
    proc.stderr.on('data', d => { stderr += d.toString(); });
    proc.stdout.on('data', d => {
      const m = d.toString().match(/PORT=(\d+)/);
      if (m && !resolved) {
        resolved = true; clearTimeout(timer);
        resolveP({ proc, port: parseInt(m[1], 10) });
      }
    });
    proc.on('exit', code => {
      if (!resolved) { clearTimeout(timer); reject(new Error(`fixture exited (${code}). stderr=${stderr}`)); }
    });
  });
}

async function getStatus(port) {
  const r = await fetch(`http://127.0.0.1:${port}/api/auth/system-status`);
  return await r.json();
}
async function seed(port, body) {
  const r = await fetch(`http://127.0.0.1:${port}/test/seed-user`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return await r.json();
}

console.log('R-F429 capability tests\n');

const ADMIN_EMAIL = 'acorrea@arkmurus.com';

// 1. Boot empty, status reports no-admin; mint admin at runtime; status flips to ok WITHOUT restart.
console.log('1. Boot empty -> mint admin -> system-status reflects new state without restart');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });

  const before = await getStatus(port);
  check('initial users.total=0', before.users.total === 0);
  check('initial users.admins=0', before.users.admins === 0);
  check('initial admin.anomaly=no-admin', before.admin.anomaly === 'no-admin');
  check('initial admin.matchesEnv=false', before.admin.matchesEnv === false);
  const bootedAtBefore = before.bootedAt;

  // Mint at runtime via the test seam (mirrors what R-F428 createIfMissing
  // does in prod: createUser + updateUser status=active).
  const minted = await seed(port, { email: ADMIN_EMAIL, role: 'admin', status: 'active' });
  check('mint succeeded', minted.ok === true);

  const after = await getStatus(port);
  check('after users.total=1', after.users.total === 1, `got ${after.users.total}`);
  check('after users.admins=1', after.users.admins === 1, `got ${after.users.admins}`);
  check('after admin.anomaly=ok', after.admin.anomaly === 'ok', `got ${after.admin.anomaly}`);
  check('after admin.matchesEnv=true', after.admin.matchesEnv === true);
  check('after admin.envEmailSet=true', after.admin.envEmailSet === true);
  check('bootedAt is stable across requests', after.bootedAt === bootedAtBefore);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 2. Runtime mint of a viewer (non-admin) does NOT flip admin counts.
console.log('\n2. Runtime mint of non-admin user does NOT flip admin counts');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  await seed(port, { email: 'viewer@example.com', role: 'viewer', status: 'active' });
  const s = await getStatus(port);
  check('users.total=1', s.users.total === 1);
  check('users.admins=0', s.users.admins === 0);
  check('admin.anomaly=no-admin (still)', s.admin.anomaly === 'no-admin');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 3. Runtime mint of a 2nd admin (different email) flips to multiple-admins anomaly.
console.log('\n3. Runtime mint of 2nd admin -> anomaly=multiple-admins');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  const before = await getStatus(port);
  check('initial admins=1, anomaly=ok', before.users.admins === 1 && before.admin.anomaly === 'ok');

  await seed(port, { email: 'second-admin@example.com', role: 'admin', status: 'active' });
  const after = await getStatus(port);
  check('after users.admins=2', after.users.admins === 2);
  check('after admin.anomaly=multiple-admins', after.admin.anomaly === 'multiple-admins');
  check('after admin.matchesEnv=false', after.admin.matchesEnv === false);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 4. Single admin with mismatched email vs ADMIN_EMAIL -> env-mismatch (live).
console.log('\n4. Runtime mint of admin with email != ADMIN_EMAIL -> anomaly=env-mismatch');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  await seed(port, { email: 'different@example.com', role: 'admin', status: 'active' });
  const s = await getStatus(port);
  check('users.admins=1', s.users.admins === 1);
  check('admin.anomaly=env-mismatch', s.admin.anomaly === 'env-mismatch');
  check('admin.matchesEnv=false', s.admin.matchesEnv === false);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 5. No ADMIN_EMAIL env -> envEmailSet stays false, anomaly tracks admin count only.
console.log('\n5. No ADMIN_EMAIL env + runtime admin -> envEmailSet=false, anomaly=ok');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file });
  await seed(port, { email: 'someone@example.com', role: 'admin', status: 'active' });
  const s = await getStatus(port);
  check('admin.envEmailSet=false', s.admin.envEmailSet === false);
  check('admin.matchesEnv=false', s.admin.matchesEnv === false);
  check('admin.anomaly=ok (no env to compare against)', s.admin.anomaly === 'ok');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
