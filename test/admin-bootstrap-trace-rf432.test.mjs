// test/admin-bootstrap-trace-rf432.test.mjs
//
// Capability test for R-F432 — /api/auth/system-status exposes a
// bootstrap-trace so the operator can diagnose "why didn't initAdminUser
// auto-create the admin row on this deploy?" without shell access to
// seenode logs.
//
// Pinned invariants:
//   - admin.bootstrap.envEmailLen + envPasswordLen always reflect what
//     the env vars actually contained at the moment initAdminUser ran
//     (lengths only, never values);
//   - admin.bootstrap.attempted = true iff we got past the "admins
//     already exist" short-circuit;
//   - admin.bootstrap.skipReason exposes one of:
//       null | 'admin-already-exists' | 'env-missing' | 'env-password-short'
//   - admin.bootstrap.succeeded = true only if a row was minted.
//
// Run: node test/admin-bootstrap-trace-rf432.test.mjs

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { allowLoopbackNetwork } from './helpers/net_guard.mjs';
allowLoopbackNetwork();

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const FIXTURE = join(__dirname, 'fixtures', 'system-status-server.mjs');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

function mkUsersFile(rows) {
  const dir = mkdtempSync(join(tmpdir(), 'trace-rf432-'));
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

console.log('R-F432 capability tests\n');

const ADMIN_EMAIL = 'acorrea@arkmurus.com';
const STRONG_PASS = 'StrongBootstrap!23';   // 18 chars, well above 12-char floor
const SHORT_PASS = 'tooShort1';              // 9 chars, below floor

// 1. Empty store + valid env -> bootstrap fires, trace.succeeded=true
console.log('1. Empty store + valid ADMIN_EMAIL/PASSWORD -> bootstrap succeeds');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL, ADMIN_PASSWORD: STRONG_PASS,
  });
  const s = await getStatus(port);
  const bt = s.admin.bootstrap;
  check('bootstrap.attempted = true', bt.attempted === true);
  check('bootstrap.succeeded = true', bt.succeeded === true);
  check('bootstrap.skipReason = null', bt.skipReason === null);
  check('bootstrap.envEmailLen matches env', bt.envEmailLen === ADMIN_EMAIL.length);
  check('bootstrap.envPasswordLen matches env', bt.envPasswordLen === STRONG_PASS.length);
  check('bootstrap.ranAt is ISO timestamp', /^\d{4}-\d{2}-\d{2}T/.test(bt.ranAt));
  check('users.admins = 1 (row was minted)', s.users.admins === 1);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 2. Empty store + short password -> attempted=true, skipReason='env-password-short'
console.log('\n2. Short ADMIN_PASSWORD -> skipReason=env-password-short');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL, ADMIN_PASSWORD: SHORT_PASS,
  });
  const s = await getStatus(port);
  const bt = s.admin.bootstrap;
  check('bootstrap.attempted = true (got past admin-exists check)', bt.attempted === true);
  check('bootstrap.succeeded = false', bt.succeeded === false);
  check('bootstrap.skipReason = env-password-short', bt.skipReason === 'env-password-short', `got "${bt.skipReason}"`);
  check('envPasswordLen = 9 (the short value)', bt.envPasswordLen === SHORT_PASS.length);
  check('users.admins still 0', s.users.admins === 0);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 3. Empty store + no env -> attempted=true, skipReason='env-missing'
console.log('\n3. ADMIN_EMAIL+PASSWORD both unset -> skipReason=env-missing');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file });
  const s = await getStatus(port);
  const bt = s.admin.bootstrap;
  check('bootstrap.attempted = true', bt.attempted === true);
  check('bootstrap.skipReason = env-missing', bt.skipReason === 'env-missing');
  check('envEmailLen = 0', bt.envEmailLen === 0);
  check('envPasswordLen = 0', bt.envPasswordLen === 0);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 4. Existing admin -> attempted=false (short-circuit), skipReason='admin-already-exists'
console.log('\n4. Existing admin row -> attempted=false, skipReason=admin-already-exists');
{
  const { dir, file } = mkUsersFile([
    { id: 'x', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL, ADMIN_PASSWORD: STRONG_PASS,
  });
  const s = await getStatus(port);
  const bt = s.admin.bootstrap;
  check('bootstrap.attempted = false', bt.attempted === false);
  check('bootstrap.skipReason = admin-already-exists', bt.skipReason === 'admin-already-exists');
  check('bootstrap.succeeded = false (not a fresh mint)', bt.succeeded === false);
  // envPasswordLen still set even though bootstrap didn't fire — operator
  // needs that visibility to detect "we wouldn't bootstrap even if admin
  // were missing because env is misconfigured" pre-emptively.
  check('envPasswordLen still reflects env', bt.envPasswordLen === STRONG_PASS.length);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 5. Env-password with leading/trailing whitespace -> trimmed-length is reported
console.log('\n5. ADMIN_PASSWORD with whitespace -> envPasswordLen reflects trimmed value');
{
  const { dir, file } = mkUsersFile([]);
  // 10 chars of actual password, but 14 chars in env (with whitespace).
  // After trim() the length should be 10, NOT 14. This catches the
  // "operator set a value with trailing newlines and it's actually <12".
  const padded = '   short10   ';
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL, ADMIN_PASSWORD: padded,
  });
  const s = await getStatus(port);
  const bt = s.admin.bootstrap;
  check('envPasswordLen = trimmed length (not raw env length)',
        bt.envPasswordLen === padded.trim().length,
        `raw=${padded.length} trimmed=${padded.trim().length} got=${bt.envPasswordLen}`);
  check('skipReason = env-password-short (trimmed value is below 12)',
        bt.skipReason === 'env-password-short');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 6. Response NEVER contains the actual password value
console.log('\n6. Response leaks no env values');
{
  const { dir, file } = mkUsersFile([]);
  const secret = 'SuperSecretMagicPhrase123!';
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL, ADMIN_PASSWORD: secret,
  });
  const text = JSON.stringify(await getStatus(port));
  check('response does not contain ADMIN_PASSWORD value', !text.includes(secret));
  check('response does not contain ADMIN_EMAIL value', !text.includes(ADMIN_EMAIL));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
