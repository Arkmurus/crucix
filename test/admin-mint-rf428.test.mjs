// test/admin-mint-rf428.test.mjs
//
// Capability test for R-F428 — createIfMissing flag on /api/auth/recovery-reset.
// User-visible promise: "when I'm locked out and the admin row literally
// doesn't exist on this deploy, I can use the same recovery page (with one
// checkbox) to mint myself as the canonical admin in one round-trip — no
// shell access, no env-var redeploy dance, no orphaned ADMIN_PASSWORD bootstrap
// skipped because of a short password".
//
// Run: node test/admin-mint-rf428.test.mjs

import { spawn } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pbkdf2Sync } from 'node:crypto';
import { allowLoopbackNetwork } from './helpers/net_guard.mjs';
allowLoopbackNetwork();

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
  const dir = mkdtempSync(join(tmpdir(), 'mint-rf428-'));
  const file = join(dir, 'users.json');
  writeFileSync(file, JSON.stringify(rows, null, 2));
  return { dir, file };
}

function startServer(envOverrides) {
  return new Promise((resolveP, reject) => {
    const env = { ...process.env };
    for (const k of [
      'USERS_FILE_OVERRIDE', 'ADMIN_RECOVERY_TOKEN',
      'EMAIL_HOST', 'EMAIL_USER', 'EMAIL_PASS',
      'ARIA_EMAIL_HOST', 'ARIA_EMAIL_USER', 'ARIA_EMAIL_PASS',
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

console.log('R-F428 capability tests\n');

const ADMIN_EMAIL = 'acorrea@arkmurus.com';
const NEW_PASS = 'NewStrongPassword!23';
const TOKEN = 'a'.repeat(64);

// 1. createIfMissing=true + empty store -> mints admin, status=active, hash verifies
console.log('1. Empty store + createIfMissing=true -> mints admin');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: TOKEN, createIfMissing: true });
  check('status 200', r.status === 200, `got ${r.status} body=${JSON.stringify(r.body)}`);
  check('body.created = true', r.body && r.body.created === true);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('1 row created', after.length === 1);
  check('role = admin', after[0].role === 'admin');
  check('status = active', after[0].status === 'active');
  check('email lowercased + trimmed', after[0].email === ADMIN_EMAIL);
  check('hash verifies against new password', verifyPassword(NEW_PASS, after[0].passwordHash));
  check('createdAt present', !!after[0].createdAt);
  check('id present (12-char hex)', /^[0-9a-f]{12}$/.test(after[0].id));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 2. createIfMissing=false (default) + empty store -> 404, NOT created
console.log('\n2. createIfMissing default (false) + empty store -> 404, no mint');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: TOKEN });
  check('status 404', r.status === 404);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('store still empty', after.length === 0);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 3. createIfMissing=true + existing user with same email -> resets password (does NOT mint duplicate)
console.log('\n3. createIfMissing=true + matching row exists -> falls through to reset path');
{
  const salt = '0123456789abcdef0123456789abcdef';
  const { dir, file } = mkUsersFile([
    { id: 'u1', email: ADMIN_EMAIL, role: 'admin', status: 'pending_approval',
      passwordHash: `${salt}:${pbkdf2('OldPass1234', salt)}`, tokenVersion: 5,
      createdAt: '2026-01-01T00:00:00Z' },
  ]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: TOKEN, createIfMissing: true });
  check('status 200', r.status === 200);
  check('body.created is falsy (existing row was reset)', !r.body || !r.body.created);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('still 1 row (not duplicated)', after.length === 1);
  check('new password verifies', verifyPassword(NEW_PASS, after[0].passwordHash));
  check('tokenVersion bumped 5 -> 6', after[0].tokenVersion === 6);
  check('createdAt preserved', after[0].createdAt === '2026-01-01T00:00:00Z');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 4. createIfMissing=true + DIFFERENT admin already exists -> 409, no mint (single-admin invariant)
console.log('\n4. createIfMissing=true blocked by existing admin with different email -> 409');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: 'someone-else@example.com', role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: TOKEN, createIfMissing: true });
  check('status 409', r.status === 409, `got ${r.status}`);
  check('error message mentions the other admin', r.body && /someone-else@example\.com/.test(r.body.error));
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('still 1 row (no parallel admin minted)', after.length === 1);
  check('still the original admin', after[0].email === 'someone-else@example.com');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 5. createIfMissing=true alongside a non-admin viewer for someone else -> mints fine
console.log('\n5. createIfMissing=true with only non-admin rows present -> mints admin');
{
  const { dir, file } = mkUsersFile([
    { id: 'v1', email: 'viewer@example.com', role: 'viewer', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: TOKEN, createIfMissing: true });
  check('status 200', r.status === 200);
  check('body.created = true', r.body && r.body.created === true);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('2 rows total', after.length === 2);
  const admin = after.find(u => u.email === ADMIN_EMAIL);
  check('new admin row present', !!admin);
  check('viewer untouched', after.find(u => u.email === 'viewer@example.com')?.role === 'viewer');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 6. createIfMissing=true + invalid token -> 401 (no mint, defence-in-depth)
console.log('\n6. createIfMissing=true with wrong token -> 401 (no mint)');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: NEW_PASS, recoveryToken: 'b'.repeat(64), createIfMissing: true });
  check('status 401', r.status === 401);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('store still empty', after.length === 0);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 7. createIfMissing=true + short password -> 400 (no mint)
console.log('\n7. createIfMissing=true with short password -> 400 (no mint)');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r = await post(port, { email: ADMIN_EMAIL, newPassword: 'short', recoveryToken: TOKEN, createIfMissing: true });
  check('status 400', r.status === 400);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('store still empty', after.length === 0);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 8. Email is case-insensitive (canonicalised before mint + before duplicate check)
console.log('\n8. Mixed-case email is normalised -> same row, no duplicate');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_RECOVERY_TOKEN: TOKEN });
  const r1 = await post(port, { email: 'AcoRRea@ArkMurus.com', newPassword: NEW_PASS, recoveryToken: TOKEN, createIfMissing: true });
  check('first request 200 + minted', r1.status === 200 && r1.body.created === true);
  const r2 = await post(port, { email: 'acorrea@arkmurus.com', newPassword: 'AnotherStrongPw!23', recoveryToken: TOKEN, createIfMissing: true });
  check('second request 200 (reset, not minted)', r2.status === 200);
  check('second request did NOT report created', !r2.body.created);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('only 1 row exists', after.length === 1);
  check('row stored with lower-cased email', after[0].email === 'acorrea@arkmurus.com');
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
