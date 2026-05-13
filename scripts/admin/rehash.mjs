// scripts/admin/rehash.mjs
// R-F423 — one-shot tool to force the admin row in runs/users.json to use the
// current ADMIN_PASSWORD env var.
//
// Why this exists: initAdminUser in lib/auth/users.mjs only consults
// ADMIN_EMAIL / ADMIN_PASSWORD when there is no admin row yet (it returns
// early on `hasAdmin`). After bootstrap, rotating the env var is a no-op for
// login — the stored PBKDF2 hash is what counts. This script bridges that gap
// without forcing the operator to delete + recreate the row (which would lose
// the row's other fields like createdAt, billing identifiers, 2FA secrets).
//
// Usage (on the deploy host, from project root):
//   ADMIN_EMAIL=...  ADMIN_PASSWORD=...  node scripts/admin/rehash.mjs
//
// Flags (for testing — avoid passing password as a flag in production, it
// leaks into shell history):
//   --email <addr>     override ADMIN_EMAIL
//   --password <pw>    override ADMIN_PASSWORD
//   --file <path>      override runs/users.json
//   --dry-run          print the planned change, do not write
//
// On success the admin row's passwordHash is replaced with PBKDF2(new), status
// is forced to 'active', and tokenVersion is bumped so any stale JWTs from
// before the rotation are invalidated on next request (no app restart needed
// — server.mjs reads users.json on every auth check via PersistStore).

import { pbkdf2Sync, randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, renameSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');
const DEFAULT_USERS_FILE = join(ROOT, 'runs', 'users.json');

// PBKDF2 params — MUST match lib/auth/users.mjs:80-94 (hashPassword + verifyPassword).
// Duplicated rather than imported to avoid loading users.mjs (which runs
// _resolveJwtSecret() at module-load and can hard-fail in production if the
// JWT_SECRET env var is unrelated to this script's purpose).
const PBKDF2_ITER = 100000;
const PBKDF2_KEYLEN = 64;
const PBKDF2_DIGEST = 'sha512';
const SALT_BYTES = 16;

function hashPassword(password) {
  const salt = randomBytes(SALT_BYTES).toString('hex');
  const hash = pbkdf2Sync(password, salt, PBKDF2_ITER, PBKDF2_KEYLEN, PBKDF2_DIGEST).toString('hex');
  return `${salt}:${hash}`;
}

function parseArgs(argv) {
  const out = { dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === '--dry-run') { out.dryRun = true; continue; }
    const v = argv[i + 1];
    if (k === '--email') { out.email = v; i++; continue; }
    if (k === '--password') { out.password = v; i++; continue; }
    if (k === '--file') { out.file = v; i++; continue; }
    if (k === '--help' || k === '-h') { out.help = true; continue; }
  }
  return out;
}

function usage() {
  console.log(`scripts/admin/rehash.mjs — force admin password hash from env

  ADMIN_EMAIL=... ADMIN_PASSWORD=... node scripts/admin/rehash.mjs [--dry-run]
  node scripts/admin/rehash.mjs --email a@b.c --password '...' --file path/users.json [--dry-run]

Exits non-zero on missing env / short password / missing admin row.`);
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) { usage(); process.exit(0); }

  const email = (args.email || process.env.ADMIN_EMAIL || '').toLowerCase().trim();
  const password = args.password || process.env.ADMIN_PASSWORD || '';
  const file = args.file ? resolve(args.file) : DEFAULT_USERS_FILE;

  const missing = [!email && 'ADMIN_EMAIL', !password && 'ADMIN_PASSWORD'].filter(Boolean);
  if (missing.length) {
    console.error(`[rehash] missing: ${missing.join(' + ')}`);
    process.exit(1);
  }
  if (password.length < 12) {
    console.error(`[rehash] ADMIN_PASSWORD must be at least 12 chars (got ${password.length})`);
    process.exit(1);
  }
  if (!existsSync(file)) {
    console.error(`[rehash] users file not found: ${file}`);
    process.exit(3);
  }

  let users;
  try {
    users = JSON.parse(readFileSync(file, 'utf8'));
  } catch (err) {
    console.error(`[rehash] failed to parse ${file}: ${err.message}`);
    process.exit(3);
  }
  if (!Array.isArray(users)) {
    console.error(`[rehash] ${file} does not contain a user array`);
    process.exit(3);
  }

  const idx = users.findIndex(u => u && typeof u.email === 'string' && u.email.toLowerCase() === email);
  if (idx < 0) {
    console.error(`[rehash] no row for email=${email} in ${file}`);
    console.error(`[rehash] tip: delete admin row + restart, initAdminUser will recreate from env`);
    process.exit(2);
  }
  const prev = users[idx];
  if (prev.role !== 'admin') {
    console.error(`[rehash] row for ${email} has role=${prev.role}, refusing (use --force-non-admin to override — not implemented intentionally)`);
    process.exit(2);
  }

  const newHash = hashPassword(password);
  const newTokenVersion = (prev.tokenVersion || 0) + 1;

  console.log(`[rehash] target: ${email} (id=${prev.id}, role=${prev.role})`);
  console.log(`[rehash] status: ${prev.status} -> active`);
  console.log(`[rehash] tokenVersion: ${prev.tokenVersion || 0} -> ${newTokenVersion}`);
  console.log(`[rehash] passwordHash: ${(prev.passwordHash || '').slice(0, 12)}... -> ${newHash.slice(0, 12)}...`);

  if (args.dryRun) {
    console.log(`[rehash] --dry-run: no write performed`);
    return;
  }

  users[idx] = {
    ...prev,
    passwordHash: newHash,
    status: 'active',
    tokenVersion: newTokenVersion,
  };

  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const backup = `${file}.bak.${ts}`;
  writeFileSync(backup, JSON.stringify(JSON.parse(readFileSync(file, 'utf8')), null, 2));
  const tmp = `${file}.tmp.${ts}`;
  writeFileSync(tmp, JSON.stringify(users, null, 2));
  renameSync(tmp, file);

  console.log(`[rehash] backup: ${backup}`);
  console.log(`[rehash] wrote:  ${file}`);
  console.log(`[rehash] done — login now accepts the current ADMIN_PASSWORD; existing JWTs invalidated`);
}

main();
