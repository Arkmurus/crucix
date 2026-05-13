// test/admin-identity-rf427.test.mjs
//
// Capability test for R-F427 — admin identity transparency.
// User-visible promise: "the system should be aware who is the admin and the
// operator should be able to see it from the outside without shell access".
//
// Asserts the /api/auth/system-status JSON shape across the four states that
// matter for diagnosing login failures:
//   - no-admin (empty users.json + missing env vars)
//   - matching admin (one admin row, equals ADMIN_EMAIL) -> anomaly: 'ok'
//   - env-mismatch (one admin row, different from ADMIN_EMAIL)
//   - multiple-admins (>1 row with role=admin)
// Plus: SMTP dedicated vs aria-fallback resolution, recovery enabled/disabled.

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
  const dir = mkdtempSync(join(tmpdir(), 'sysstatus-rf427-'));
  const file = join(dir, 'users.json');
  writeFileSync(file, JSON.stringify(rows, null, 2));
  return { dir, file };
}

function startServer(envOverrides) {
  return new Promise((resolveP, reject) => {
    // Build a fresh env that strips all the vars we care about, then layers
    // the overrides on top. Without stripping, the parent's env can leak in
    // (e.g., the operator's local .env with EMAIL_HOST set, which would
    // contaminate the "no SMTP" test).
    const env = { ...process.env };
    for (const k of [
      'USERS_FILE_OVERRIDE', 'ADMIN_EMAIL', 'ADMIN_PASSWORD',
      'EMAIL_HOST', 'EMAIL_PORT', 'EMAIL_USER', 'EMAIL_PASS',
      'EMAIL_FROM', 'EMAIL_SECURE',
      'ARIA_EMAIL_HOST', 'ARIA_EMAIL_PORT', 'ARIA_EMAIL_USER', 'ARIA_EMAIL_PASS',
      'ARIA_SMTP_HOST', 'ARIA_SMTP_PORT',
      'ADMIN_RECOVERY_TOKEN', 'TEST_BUILD_REV',
    ]) delete env[k];
    Object.assign(env, envOverrides);

    const proc = spawn(process.execPath, [FIXTURE], {
      cwd: ROOT, env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stderr = '';
    let stdout = '';
    let resolved = false;
    const timer = setTimeout(() => {
      if (!resolved) { proc.kill(); reject(new Error(`fixture did not start. stderr=${stderr}`)); }
    }, 10000);
    proc.stderr.on('data', d => { stderr += d.toString(); });
    proc.stdout.on('data', d => {
      stdout += d.toString();
      const m = d.toString().match(/PORT=(\d+)/);
      if (m && !resolved) {
        resolved = true; clearTimeout(timer);
        // Boot logs go to a mix of stdout (console.log) and stderr
        // (console.warn). The test asserts on the combined stream because
        // semantically "what did the boot print?" doesn't care which fd.
        resolveP({ proc, port: parseInt(m[1], 10), getStderr: () => stderr + stdout });
      }
    });
    proc.on('exit', code => {
      if (!resolved) { clearTimeout(timer); reject(new Error(`fixture exited (${code}). stderr=${stderr}`)); }
    });
  });
}

async function fetchStatus(port) {
  const r = await fetch(`http://127.0.0.1:${port}/api/auth/system-status`);
  return await r.json();
}

console.log('R-F427 capability tests\n');

const ADMIN_EMAIL = 'acorrea@arkmurus.com';
const STRONG_PASS = 'BootstrapPassword!23';
const RECOVERY_TOKEN = 'r'.repeat(64);

// 1. No-admin anomaly: empty store, no env vars.
console.log('1. Empty store + no ADMIN_* env -> anomaly=no-admin');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port, getStderr } = await startServer({ USERS_FILE_OVERRIDE: file });
  const r = await fetchStatus(port);
  check('users.total = 0', r.users.total === 0);
  check('users.admins = 0', r.users.admins === 0);
  check('admin.anomaly = no-admin', r.admin.anomaly === 'no-admin');
  check('admin.envEmailSet = false', r.admin.envEmailSet === false);
  check('admin.matchesEnv = false', r.admin.matchesEnv === false);
  check('smtp.configured = false', r.smtp.configured === false);
  check('smtp.via = null', r.smtp.via === null);
  check('recoveryReset.enabled = false', r.recoveryReset.enabled === false);
  check('buildRev present', typeof r.buildRev === 'string' && r.buildRev.length > 0);
  check('boot log warns about no admin', /no admin rows exist/.test(getStderr()));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 2. Empty store + valid ADMIN_EMAIL + ADMIN_PASSWORD -> bootstrap creates row, anomaly=ok.
console.log('\n2. Empty store + valid bootstrap env -> initAdminUser creates admin, anomaly=ok');
{
  const { dir, file } = mkUsersFile([]);
  const { proc, port, getStderr } = await startServer({
    USERS_FILE_OVERRIDE: file,
    ADMIN_EMAIL,
    ADMIN_PASSWORD: STRONG_PASS,
  });
  const r = await fetchStatus(port);
  check('users.admins = 1 after bootstrap', r.users.admins === 1);
  check('admin.anomaly = ok', r.admin.anomaly === 'ok', `got ${r.admin.anomaly}`);
  check('admin.envEmailSet = true', r.admin.envEmailSet === true);
  check('admin.matchesEnv = true', r.admin.matchesEnv === true);
  check('boot log marks identity confirmed', /admin identity confirmed/.test(getStderr()));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 3. One admin matches ADMIN_EMAIL -> anomaly=ok.
console.log('\n3. Pre-existing matching admin -> anomaly=ok');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h', tokenVersion: 1 },
    { id: 'u1', email: 'viewer@example.com', role: 'viewer', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  const r = await fetchStatus(port);
  check('users.total = 2', r.users.total === 2);
  check('users.admins = 1', r.users.admins === 1);
  check('admin.anomaly = ok', r.admin.anomaly === 'ok');
  check('admin.matchesEnv = true', r.admin.matchesEnv === true);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 4. Admin email mismatches ADMIN_EMAIL -> anomaly=env-mismatch.
console.log('\n4. Admin email differs from ADMIN_EMAIL -> anomaly=env-mismatch');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: 'someone-else@example.com', role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port, getStderr } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  const r = await fetchStatus(port);
  check('admin.anomaly = env-mismatch', r.admin.anomaly === 'env-mismatch');
  check('admin.matchesEnv = false', r.admin.matchesEnv === false);
  check('boot log warns about mismatch', /does not match/.test(getStderr()));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 5. Multiple admin rows -> anomaly=multiple-admins.
console.log('\n5. Multiple admin rows -> anomaly=multiple-admins');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
    { id: 'a2', email: 'old-admin@example.com', role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port, getStderr } = await startServer({ USERS_FILE_OVERRIDE: file, ADMIN_EMAIL });
  const r = await fetchStatus(port);
  check('users.admins = 2', r.users.admins === 2);
  check('admin.anomaly = multiple-admins', r.admin.anomaly === 'multiple-admins');
  check('admin.matchesEnv = false', r.admin.matchesEnv === false);
  check('boot log warns about multiple admins', /admin rows present/.test(getStderr()));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 6. SMTP via ARIA fallback when EMAIL_* unset and ARIA_EMAIL_* set.
console.log('\n6. SMTP resolution -> aria-fallback when ARIA_EMAIL_* set');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file,
    ADMIN_EMAIL,
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'aria-secret',
  });
  const r = await fetchStatus(port);
  check('smtp.configured = true', r.smtp.configured === true);
  check('smtp.via = aria-fallback', r.smtp.via === 'aria-fallback', `got ${r.smtp.via}`);
  check('smtp.host = ox.livemail.co.uk', r.smtp.host === 'ox.livemail.co.uk');
  check('smtp.user = aria@arkmurus.com', r.smtp.user === 'aria@arkmurus.com');
  check('smtp.port = 465 (SSL default)', r.smtp.port === 465);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 7. SMTP dedicated wins over ARIA fallback when both set.
console.log('\n7. SMTP resolution -> dedicated wins when both sets present');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file,
    ADMIN_EMAIL,
    EMAIL_HOST: 'smtp.dedicated.example',
    EMAIL_USER: 'auth@example.com',
    EMAIL_PASS: 'dedicated-secret',
    EMAIL_PORT: '587',
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'aria-secret',
  });
  const r = await fetchStatus(port);
  check('smtp.via = dedicated', r.smtp.via === 'dedicated');
  check('smtp.host = smtp.dedicated.example', r.smtp.host === 'smtp.dedicated.example');
  check('smtp.user = auth@example.com', r.smtp.user === 'auth@example.com');
  check('smtp.port = 587', r.smtp.port === 587);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 8. Recovery token >= 32 chars -> enabled=true.
console.log('\n8. ADMIN_RECOVERY_TOKEN >= 32 chars -> recoveryReset.enabled=true');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL,
    ADMIN_RECOVERY_TOKEN: RECOVERY_TOKEN,
  });
  const r = await fetchStatus(port);
  check('recoveryReset.tokenSet = true', r.recoveryReset.tokenSet === true);
  check('recoveryReset.tokenLengthOk = true', r.recoveryReset.tokenLengthOk === true);
  check('recoveryReset.enabled = true', r.recoveryReset.enabled === true);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 9. Recovery token < 32 chars -> enabled=false (defense in depth).
console.log('\n9. ADMIN_RECOVERY_TOKEN too short -> enabled=false');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 's:h' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL,
    ADMIN_RECOVERY_TOKEN: 'short',
  });
  const r = await fetchStatus(port);
  check('recoveryReset.tokenSet = true', r.recoveryReset.tokenSet === true);
  check('recoveryReset.tokenLengthOk = false', r.recoveryReset.tokenLengthOk === false);
  check('recoveryReset.enabled = false', r.recoveryReset.enabled === false);
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

// 10. Response NEVER includes admin email or password values.
console.log('\n10. Response leaks no emails / hashes / secrets');
{
  const { dir, file } = mkUsersFile([
    { id: 'a1', email: ADMIN_EMAIL, role: 'admin', status: 'active', passwordHash: 'leaky-salt:leaky-hash' },
  ]);
  const { proc, port } = await startServer({
    USERS_FILE_OVERRIDE: file, ADMIN_EMAIL,
    ARIA_EMAIL_HOST: 'ox.livemail.co.uk',
    ARIA_EMAIL_USER: 'aria@arkmurus.com',
    ARIA_EMAIL_PASS: 'super-secret-password-value',
    ADMIN_RECOVERY_TOKEN: RECOVERY_TOKEN,
  });
  const text = JSON.stringify(await fetchStatus(port));
  check('response does not contain admin email', !text.includes(ADMIN_EMAIL));
  check('response does not contain password hash', !text.includes('leaky-hash'));
  check('response does not contain ARIA_EMAIL_PASS', !text.includes('super-secret-password-value'));
  check('response does not contain recovery token', !text.includes(RECOVERY_TOKEN));
  proc.kill();
  rmSync(dir, { recursive: true, force: true });
}

console.log(`\n${failures === 0 ? '✓ all checks passed' : '✗ ' + failures + ' check(s) failed'}`);
process.exit(failures === 0 ? 0 : 1);
