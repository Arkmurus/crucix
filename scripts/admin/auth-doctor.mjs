// scripts/admin/auth-doctor.mjs
// R-F424 — read-only diagnostic for "I can't log in / forgot-password isn't
// arriving" on the seenode deploy. Prints everything we need to root-cause
// without modifying any state.
//
// Usage on the deploy host, from project root:
//   node scripts/admin/auth-doctor.mjs
//
// Optional flags:
//   --email <addr>     override ADMIN_EMAIL when checking for the admin row
//   --file <path>      override runs/users.json location
//   --check-password   ALSO verify ADMIN_PASSWORD against the admin row's hash
//                      (off by default so this stays a strict read-only probe)
//
// No secrets are echoed. Passwords are reported by length only. Hashes are
// reported by their first 12 chars (the salt prefix), which is not useful
// for offline cracking on PBKDF2-sha512-100k.

import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { pbkdf2Sync } from 'node:crypto';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');
const DEFAULT_USERS_FILE = join(ROOT, 'runs', 'users.json');

function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    const v = argv[i + 1];
    if (k === '--email') { out.email = v; i++; continue; }
    if (k === '--file')  { out.file = v;  i++; continue; }
    if (k === '--check-password') { out.checkPassword = true; continue; }
  }
  return out;
}

function head(s) { console.log(`\n── ${s} ${'─'.repeat(Math.max(0, 60 - s.length))}`); }
function ok(m)   { console.log(`  ✓ ${m}`); }
function warn(m) { console.log(`  ⚠ ${m}`); }
function bad(m)  { console.log(`  ✗ ${m}`); }
function info(m) { console.log(`  · ${m}`); }

function verifyPbkdf2(password, stored) {
  try {
    const [salt, hash] = String(stored || '').split(':');
    if (!salt || !hash) return false;
    return pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex') === hash;
  } catch { return false; }
}

const args = parseArgs(process.argv.slice(2));
const ADMIN_EMAIL_ENV    = (process.env.ADMIN_EMAIL || '').toLowerCase().trim();
const ADMIN_PASSWORD_ENV = process.env.ADMIN_PASSWORD || '';
const targetEmail        = ((args.email || ADMIN_EMAIL_ENV) || '').toLowerCase().trim();
const usersFile          = args.file ? resolve(args.file) : DEFAULT_USERS_FILE;

console.log('auth-doctor — R-F424 diagnostic (read-only)');
console.log(`cwd: ${process.cwd()}`);
console.log(`project root: ${ROOT}`);
console.log(`node: ${process.version}  pid: ${process.pid}`);

head('Environment');
if (ADMIN_EMAIL_ENV) ok(`ADMIN_EMAIL set: ${ADMIN_EMAIL_ENV}`); else bad('ADMIN_EMAIL not set');
if (ADMIN_PASSWORD_ENV) {
  if (ADMIN_PASSWORD_ENV.length >= 12) ok(`ADMIN_PASSWORD set, length=${ADMIN_PASSWORD_ENV.length}`);
  else bad(`ADMIN_PASSWORD set but length=${ADMIN_PASSWORD_ENV.length} (must be >= 12 for initAdminUser to bootstrap)`);
  if (ADMIN_PASSWORD_ENV !== ADMIN_PASSWORD_ENV.trim()) warn(`ADMIN_PASSWORD has leading/trailing whitespace — login won't trim it`);
} else bad('ADMIN_PASSWORD not set');
if (process.env.JWT_SECRET) ok(`JWT_SECRET set, length=${process.env.JWT_SECRET.length}`);
else bad('JWT_SECRET not set — server will hard-fail at boot in production (users.mjs:39-44)');
if (process.env.NODE_ENV) info(`NODE_ENV=${process.env.NODE_ENV}`);

head('SMTP (controls whether forgot-password / verification emails actually send)');
const smtpHost = process.env.EMAIL_HOST;
const smtpUser = process.env.EMAIL_USER;
const smtpPass = process.env.EMAIL_PASS;
const smtpPort = process.env.EMAIL_PORT;
const smtpFrom = process.env.EMAIL_FROM;
const adminEmailVar = process.env.ADMIN_EMAIL || '(default acorrea@arkmurus.com)';
if (smtpHost && smtpUser && smtpPass) {
  ok(`SMTP configured: host=${smtpHost} port=${smtpPort || '587'} user=${smtpUser}`);
  if (smtpFrom) info(`EMAIL_FROM=${smtpFrom}`);
  info(`ADMIN_EMAIL (notification target) = ${adminEmailVar}`);
} else {
  const missing = [!smtpHost && 'EMAIL_HOST', !smtpUser && 'EMAIL_USER', !smtpPass && 'EMAIL_PASS'].filter(Boolean).join(', ');
  bad(`SMTP NOT configured — missing: ${missing}`);
  bad(`/api/auth/forgot-password will silently no-op the email (returns 200 either way)`);
  bad(`Reset codes will only be visible in seenode stdout logs: grep '[EMAIL] Password reset code for'`);
}

head('Users store');
if (!existsSync(usersFile)) {
  bad(`users.json NOT found at ${usersFile}`);
  bad(`  → on next app boot, initAdminUser() will try to bootstrap from ADMIN_EMAIL+ADMIN_PASSWORD`);
  bad(`  → bootstrap only fires if password is >= 12 chars (users.mjs:278)`);
  process.exit(2);
}
const stat = statSync(usersFile);
ok(`file exists: ${usersFile}`);
info(`size: ${stat.size} bytes  mtime: ${stat.mtime.toISOString()}`);

let users;
try {
  users = JSON.parse(readFileSync(usersFile, 'utf8'));
} catch (e) {
  bad(`failed to parse users.json: ${e.message}`);
  process.exit(3);
}
if (!Array.isArray(users)) { bad('users.json is not an array'); process.exit(3); }

info(`total users: ${users.length}`);
const admins = users.filter(u => u && u.role === 'admin');
if (admins.length === 0) {
  bad('NO admin rows in users.json');
  bad(`  → initAdminUser() should have created one at boot. If it didn't:`);
  bad(`    - ADMIN_PASSWORD < 12 chars (users.mjs:278)`);
  bad(`    - ADMIN_EMAIL or ADMIN_PASSWORD missing (users.mjs:268)`);
  bad(`    - seenode disk is ephemeral and this file is post-bootstrap empty`);
} else {
  ok(`${admins.length} admin row(s):`);
  for (const a of admins) {
    info(`  email=${a.email}  status=${a.status}  tokenVersion=${a.tokenVersion}  hash=${(a.passwordHash||'').slice(0,12)}...  createdAt=${a.createdAt}  lastLogin=${a.lastLogin}`);
  }
}

head(`Target admin row (email=${targetEmail || '<none>'})`);
if (!targetEmail) {
  bad('no target email (set ADMIN_EMAIL or pass --email)');
} else {
  const row = users.find(u => u && typeof u.email === 'string' && u.email.toLowerCase() === targetEmail);
  if (!row) {
    bad(`no row for email=${targetEmail}`);
    bad(`  → login will return "Invalid credentials"`);
    bad(`  → forgot-password will silently no-op (server.mjs:3480-3488)`);
    bad(`  → fix: rerun rehash.mjs with --create-if-missing, or delete users.json + restart so initAdminUser re-bootstraps`);
  } else {
    ok(`row found: id=${row.id}  role=${row.role}  status=${row.status}`);
    if (row.role !== 'admin')      warn(`role=${row.role} (not admin) — admin UI access blocked`);
    if (row.status === 'suspended')          bad(`status=suspended — login returns 403 (server.mjs:3233-3235)`);
    if (row.status === 'pending_verification') bad(`status=pending_verification — login returns 403 needing email code (server.mjs:3230-3232)`);
    if (row.status === 'pending_approval')   bad(`status=pending_approval — login returns 403 needing admin approval (server.mjs:3227-3229)`);
    if (row.twoFactorEnabled && row.twoFactorSecret) warn(`2FA enabled — login returns preToken, real JWT requires TOTP code`);
    info(`tokenVersion=${row.tokenVersion}  passwordHash=${(row.passwordHash||'').slice(0,12)}...`);

    if (args.checkPassword) {
      if (!ADMIN_PASSWORD_ENV) {
        warn('--check-password: ADMIN_PASSWORD not set, cannot verify');
      } else {
        const matches = verifyPbkdf2(ADMIN_PASSWORD_ENV, row.passwordHash);
        if (matches) ok(`ADMIN_PASSWORD env DOES match stored hash — login should succeed`);
        else        bad(`ADMIN_PASSWORD env does NOT match stored hash — login will fail`);
        if (!matches) bad(`  → rehash.mjs needed (forces stored hash to current env-var password)`);
      }
    } else {
      info(`(pass --check-password to verify ADMIN_PASSWORD against the stored hash)`);
    }
  }
}

head('Process / cache caveat');
info(`PersistStore caches users in memory on init() and does NOT re-read on every auth check (store.mjs:97-107).`);
info(`If you've JUST edited users.json on disk, the running server still sees the old data.`);
info(`Restart the seenode app so initUsersStore() re-reads the file.`);

console.log('\nDone.');
