// test/auth-recovery-reset-rf425.test.mjs
//
// Capability test for R-F425 — /api/auth/recovery-reset.
// The user-visible promise: "given the right ADMIN_RECOVERY_TOKEN on the
// server, the operator can reset any user's password without SMTP or shell
// access, and the new password verifies against the stored hash". Also pins
// that the endpoint is invisible (404) when the env var isn't set, and rate
// of failures doesn't grow into a typo/timing oracle.
//
// Run: node test/auth-recovery-reset-rf425.test.mjs

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pbkdf2Sync } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const FIXTURE = join(__dirname, 'fixtures', 'recovery-reset-server.mjs');

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else { console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`); failures += 1; }
}

function pbkdf2(password, salt) {
  return pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
}
function verifyPassword(password, stored) {
  const [salt, hash] = String(stored || '').split(':');
  if (!salt || !hash) return false;
  return pbkdf2(password, salt) === hash;
}
function mkUsersFile(rows) {
  const dir = mkdtempSync(join(tmpdir(), 'recovery-rf425-'));
  const file = join(dir, 'users.json');
  writeFileSync(file, JSON.stringify(rows, null, 2));
  return { dir, file };
}

function startServer(env) {
  return new Promise((resolve_, reject) => {
    const proc = spawn(process.execPath, [FIXTURE], {
      cwd: ROOT,
      env: { ...process.env, ...env },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) {
        proc.kill();
        reject(new Error(`server fixture did not start in 10s. stderr=${stderr}`));
      }
    }, 10000);
    proc.stderr.on('data', d => { stderr += d.toString(); });
    proc.stdout.on('data', d => {
      const m = d.toString().match(/PORT=(\d+)/);
      if (m && !resolved) {
        resolved = true;
        clearTimeout(timer);
        resolve_({ proc, port: parseInt(m[1], 10) });
      }
    });
    proc.on('exit', code => {
      if (!resolved) {
        clearTimeout(timer);
        reject(new Error(`server fixture exited (code=${code}) before reporting port. stderr=${stderr}`));
      }
    });
  });
}

async function post(port, body) {
  const res = await fetch(`http://127.0.0.1:${port}/api/auth/recovery-reset`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  let json = null;
  try { json = await res.json(); } catch {}
  return { status: res.status, body: json };
}

console.log('R-F425 capability tests\n');

const TARGET_EMAIL = 'acorrea@arkmurus.com';
const OLD_PASSWORD = 'OldPasswordABC123';
const NEW_PASSWORD = 'NewStrongPassword!23';
const VALID_TOKEN  = 'a'.repeat(64);
const WRONG_TOKEN  = 'b'.repeat(64);

function seed() {
  const salt = '0123456789abcdef0123456789abcdef';
  return [
    {
      id: 'adm-1',
      username: 'admin',
      email: TARGET_EMAIL,
      passwordHash: `${salt}:${pbkdf2(OLD_PASSWORD, salt)}`,
      fullName: 'Arkmurus Administrator',
      role: 'admin',
      status: 'active',
      tokenVersion: 7,
      verificationCode: null,
      verificationExpiry: null,
      createdAt: '2026-01-01T00:00:00Z',
      lastLogin: null,
    },
  ];
}

// 1. Endpoint returns 404 when ADMIN_RECOVERY_TOKEN env is unset
console.log('1. Endpoint hidden (404) when ADMIN_RECOVERY_TOKEN is not set');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: '' });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: NEW_PASSWORD, recoveryToken: VALID_TOKEN });
  check('status 404', r.status === 404, `got ${r.status}`);
  // file unchanged
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('users.json untouched', verifyPassword(OLD_PASSWORD, after[0].passwordHash));
  check('tokenVersion unchanged', after[0].tokenVersion === 7);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 2. Token too short (< 32 chars) also returns 404 — same defence-in-depth
console.log('\n2. Short ADMIN_RECOVERY_TOKEN also returns 404');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: 'tooshort' });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: NEW_PASSWORD, recoveryToken: 'tooshort' });
  check('status 404', r.status === 404);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 3. Wrong recovery token returns 401, password unchanged
console.log('\n3. Wrong recovery token returns 401');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: VALID_TOKEN });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: NEW_PASSWORD, recoveryToken: WRONG_TOKEN });
  check('status 401', r.status === 401);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('old password still works', verifyPassword(OLD_PASSWORD, after[0].passwordHash));
  check('tokenVersion unchanged', after[0].tokenVersion === 7);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 4. Valid token + valid email + valid password -> 200, password rotated
console.log('\n4. Valid recovery resets password, bumps tokenVersion, preserves other fields');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: VALID_TOKEN });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: NEW_PASSWORD, recoveryToken: VALID_TOKEN });
  check('status 200', r.status === 200, `got ${r.status}`);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('new password verifies', verifyPassword(NEW_PASSWORD, after[0].passwordHash));
  check('old password no longer verifies', !verifyPassword(OLD_PASSWORD, after[0].passwordHash));
  check('tokenVersion incremented 7 -> 8', after[0].tokenVersion === 8);
  check('status forced to active', after[0].status === 'active');
  check('createdAt preserved', after[0].createdAt === '2026-01-01T00:00:00Z');
  check('username preserved', after[0].username === 'admin');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 5. Valid token + unknown email -> 404
console.log('\n5. Valid token but unknown email returns 404');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: VALID_TOKEN });
  const r = await post(port, { email: 'nobody@example.com', newPassword: NEW_PASSWORD, recoveryToken: VALID_TOKEN });
  check('status 404', r.status === 404);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 6. Valid token but short password -> 400
console.log('\n6. Short new password returns 400');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: VALID_TOKEN });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: 'short', recoveryToken: VALID_TOKEN });
  check('status 400', r.status === 400);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('password unchanged', verifyPassword(OLD_PASSWORD, after[0].passwordHash));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 7. Token length must match exactly — wrong length is rejected at the length check (defends timing oracle)
console.log('\n7. Token of wrong length returns 401 before timing-safe compare');
{
  const { dir, file } = mkUsersFile(seed());
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: VALID_TOKEN });
  const r = await post(port, { email: TARGET_EMAIL, newPassword: NEW_PASSWORD, recoveryToken: 'a'.repeat(63) });
  check('status 401', r.status === 401);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
