// test/admin-rehash-rf423.test.mjs
//
// Capability test for R-F423 — scripts/admin/rehash.mjs.
// Pins the user-visible promise: "after this script runs, the admin row's
// passwordHash verifies against the env-var password, status is 'active',
// tokenVersion is bumped, and a timestamped backup exists".
//
// Run: node test/admin-rehash-rf423.test.mjs

import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pbkdf2Sync } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..');
const SCRIPT = join(ROOT, 'scripts', 'admin', 'rehash.mjs');

function verifyPassword(password, stored) {
  const [salt, hash] = stored.split(':');
  return pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex') === hash;
}

function runScript(env, args = []) {
  return spawnSync(process.execPath, [SCRIPT, ...args], {
    env: { ...process.env, ...env },
    encoding: 'utf8',
  });
}

function mkUsersFile(rows) {
  const dir = mkdtempSync(join(tmpdir(), 'rehash-rf423-'));
  const file = join(dir, 'users.json');
  writeFileSync(file, JSON.stringify(rows, null, 2));
  return { dir, file };
}

let failures = 0;
function check(label, cond, detail) {
  if (cond) console.log(`  ✓ ${label}`);
  else {
    console.error(`  ✗ ${label}${detail ? ' — ' + detail : ''}`);
    failures += 1;
  }
}

const ADMIN_EMAIL = 'admin@example.com';
const NEW_PASSWORD = 'NewStrongPassword!23';

console.log('R-F423 capability tests\n');

// 1. Happy path — admin row exists, env vars set, script rehashes and bumps version.
console.log('1. Happy path: rehash existing admin row');
{
  const { dir, file } = mkUsersFile([
    {
      id: 'abc123',
      username: 'admin',
      email: ADMIN_EMAIL,
      passwordHash: 'oldsalt:oldhash',
      role: 'admin',
      status: 'active',
      tokenVersion: 4,
      createdAt: '2026-01-01T00:00:00Z',
    },
    { id: 'def456', email: 'user@example.com', role: 'viewer', status: 'active' },
  ]);
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file]
  );
  check('exit code 0', r.status === 0, `stderr=${r.stderr}`);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  const admin = after.find(u => u.email === ADMIN_EMAIL);
  check('admin row still present', !!admin);
  check('passwordHash changed', admin && admin.passwordHash !== 'oldsalt:oldhash');
  check('new hash verifies against new password', admin && verifyPassword(NEW_PASSWORD, admin.passwordHash));
  check('status forced to active', admin && admin.status === 'active');
  check('tokenVersion incremented (4 -> 5)', admin && admin.tokenVersion === 5);
  check('createdAt preserved', admin && admin.createdAt === '2026-01-01T00:00:00Z');
  check('non-admin row untouched', after.find(u => u.email === 'user@example.com')?.role === 'viewer');
  const backups = readdirSync(dir).filter(f => f.startsWith('users.json.bak.'));
  check('backup file created', backups.length === 1, `found ${backups.length}`);
  rmSync(dir, { recursive: true, force: true });
}

// 2. Dry run leaves file untouched
console.log('\n2. --dry-run leaves users.json untouched');
{
  const { dir, file } = mkUsersFile([
    { id: 'x', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 'orig:hash', tokenVersion: 0 },
  ]);
  const before = readFileSync(file, 'utf8');
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file, '--dry-run']
  );
  check('exit code 0', r.status === 0);
  check('file content unchanged', readFileSync(file, 'utf8') === before);
  const backups = readdirSync(dir).filter(f => f.startsWith('users.json.bak.'));
  check('no backup written in dry-run', backups.length === 0);
  rmSync(dir, { recursive: true, force: true });
}

// 3. Missing ADMIN_PASSWORD -> exit 1
console.log('\n3. Missing ADMIN_PASSWORD env exits 1');
{
  const { dir, file } = mkUsersFile([
    { id: 'x', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 'orig:hash', tokenVersion: 0 },
  ]);
  // Force-blank inherited ADMIN_PASSWORD by passing empty string.
  const r = spawnSync(process.execPath, [SCRIPT, '--file', file], {
    env: { ...process.env, ADMIN_EMAIL, ADMIN_PASSWORD: '' },
    encoding: 'utf8',
  });
  check('exit code 1', r.status === 1, `got ${r.status} stderr=${r.stderr}`);
  check('stderr mentions ADMIN_PASSWORD', /ADMIN_PASSWORD/.test(r.stderr));
  rmSync(dir, { recursive: true, force: true });
}

// 4. Short password -> exit 1
console.log('\n4. Password shorter than 12 chars exits 1');
{
  const { dir, file } = mkUsersFile([
    { id: 'x', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 'orig:hash', tokenVersion: 0 },
  ]);
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: 'short' },
    ['--file', file]
  );
  check('exit code 1', r.status === 1);
  check('stderr mentions 12 chars', /12 chars/.test(r.stderr));
  rmSync(dir, { recursive: true, force: true });
}

// 5. No admin row for given email -> exit 2
console.log('\n5. Missing admin row for email exits 2');
{
  const { dir, file } = mkUsersFile([
    { id: 'u', email: 'someone-else@example.com', role: 'admin', status: 'active', passwordHash: 'o:h', tokenVersion: 0 },
  ]);
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file]
  );
  check('exit code 2', r.status === 2, `got ${r.status}`);
  check('stderr mentions no row', /no row/.test(r.stderr));
  rmSync(dir, { recursive: true, force: true });
}

// 6. Refuse to rehash non-admin row even if email matches
console.log('\n6. Refuses to rehash non-admin row with matching email');
{
  const { dir, file } = mkUsersFile([
    { id: 'u', email: ADMIN_EMAIL, role: 'viewer', status: 'active', passwordHash: 'o:h', tokenVersion: 0 },
  ]);
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file]
  );
  check('exit code 2', r.status === 2);
  check('stderr mentions role=viewer', /role=viewer/.test(r.stderr));
  rmSync(dir, { recursive: true, force: true });
}

// 7. Email matching is case-insensitive
console.log('\n7. Email matching is case-insensitive');
{
  const { dir, file } = mkUsersFile([
    { id: 'x', email: 'admin@example.com', role: 'admin', status: 'pending_approval', passwordHash: 'o:h', tokenVersion: 0 },
  ]);
  const r = runScript(
    { ADMIN_EMAIL: 'Admin@Example.COM', ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file]
  );
  check('exit code 0', r.status === 0, `stderr=${r.stderr}`);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('status normalised to active', after[0].status === 'active');
  rmSync(dir, { recursive: true, force: true });
}

// 8. --create-if-missing: mints admin row when email is absent
console.log('\n8. --create-if-missing mints admin row when email is absent');
{
  const { dir, file } = mkUsersFile([
    { id: 'u1', email: 'other@example.com', role: 'viewer', status: 'active', passwordHash: 'o:h' },
  ]);
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file, '--create-if-missing']
  );
  check('exit code 0', r.status === 0, `stderr=${r.stderr}`);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  const admin = after.find(u => u.email === ADMIN_EMAIL);
  check('admin row created', !!admin);
  check('role=admin', admin && admin.role === 'admin');
  check('status=active', admin && admin.status === 'active');
  check('hash verifies', admin && verifyPassword(NEW_PASSWORD, admin.passwordHash));
  check('existing viewer row preserved', after.find(u => u.email === 'other@example.com')?.role === 'viewer');
  rmSync(dir, { recursive: true, force: true });
}

// 9. --create-if-missing creates the file itself if it doesn't exist
console.log('\n9. --create-if-missing creates users.json if absent');
{
  const dir = mkdtempSync(join(tmpdir(), 'rehash-rf423-'));
  const subdir = join(dir, 'nested', 'runs');
  const file = join(subdir, 'users.json');
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file, '--create-if-missing']
  );
  check('exit code 0', r.status === 0, `stderr=${r.stderr}`);
  const after = JSON.parse(readFileSync(file, 'utf8'));
  check('users.json now exists with 1 admin row', Array.isArray(after) && after.length === 1 && after[0].role === 'admin');
  check('hash verifies', verifyPassword(NEW_PASSWORD, after[0].passwordHash));
  // Fresh-creation should NOT write a backup (nothing to back up)
  const backups = readdirSync(subdir).filter(f => f.startsWith('users.json.bak.'));
  check('no backup on fresh creation', backups.length === 0);
  rmSync(dir, { recursive: true, force: true });
}

// 10. Without --create-if-missing, missing file still exits 3
console.log('\n10. Missing file without --create-if-missing exits 3');
{
  const dir = mkdtempSync(join(tmpdir(), 'rehash-rf423-'));
  const file = join(dir, 'nonexistent.json');
  const r = runScript(
    { ADMIN_EMAIL, ADMIN_PASSWORD: NEW_PASSWORD },
    ['--file', file]
  );
  check('exit code 3', r.status === 3, `got ${r.status}`);
  check('stderr mentions create-if-missing tip', /create-if-missing/.test(r.stderr));
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
