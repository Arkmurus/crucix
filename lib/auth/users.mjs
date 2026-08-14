// lib/auth/users.mjs
// User store — JSON file-based, no external dependencies
// Uses node:crypto for password hashing (PBKDF2) and JWT-like tokens (HMAC-SHA256)

import { createHmac, pbkdf2Sync, randomBytes, timingSafeEqual } from 'node:crypto';
import { existsSync, copyFileSync, mkdirSync } from 'node:fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PersistStore } from '../persist/store.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
// R-F1687 (2026-06-19): persist accounts on the DURABLE /data volume, not the
// ephemeral container filesystem. PERSIST_DIR is set to /data in Dockerfile.web
// (the fly volume mount, daily snapshots). Pre-fix USERS_FILE lived in runs/
// (app dir) and was wiped on EVERY deploy/machine-rebuild — so the same human
// got a brand-new random user.id each restart, orphaning all of their
// conversation history (empty sidebar) and silently logging them out. The
// /data volume survives deploys, so user.id (and thus the conversation bucket)
// is now stable for the life of the account.
//
// USERS_FILE stays overridable via USERS_FILE_OVERRIDE so capability tests can
// point a child Node process at a tmp users.json without colliding with the
// running app's real store. Production deploys leave that env var unset.
const PERSIST_DIR = process.env.PERSIST_DIR || RUNS_DIR;
const LEGACY_USERS_FILE = join(RUNS_DIR, 'users.json');
const USERS_FILE = process.env.USERS_FILE_OVERRIDE || join(PERSIST_DIR, 'users.json');

// R-F1687: one-time cutover. On the first boot after USERS_FILE moves to /data,
// the durable file won't exist yet but the legacy ephemeral one might (from the
// pre-fix image layer / a running container). Copy it forward so NO account is
// lost in the move. Idempotent: skips once the durable file exists.
function _migrateLegacyUsersFile() {
  try {
    if (USERS_FILE === LEGACY_USERS_FILE) return;            // not moved (dev / override)
    if (existsSync(USERS_FILE)) return;                      // already on durable store
    if (!existsSync(LEGACY_USERS_FILE)) return;              // nothing to migrate
    mkdirSync(dirname(USERS_FILE), { recursive: true });
    copyFileSync(LEGACY_USERS_FILE, USERS_FILE);
    console.log(`[Auth] R-F1687: migrated legacy users.json → ${USERS_FILE} (durable volume)`);
  } catch (e) {
    console.warn(`[Auth] R-F1687: legacy users.json migration skipped: ${e.message}`);
  }
}

// JWT-like secret — MUST be provided via the JWT_SECRET env var in production.
// Pre-2026-04-20 this had a `|| randomBytes(32).toString('hex')` fallback that
// generated a fresh random secret on every process start. Consequence: every
// seenode deploy invalidated every user's token silently (everyone logged out
// on next request). Worse, the fallback masked the configuration error —
// nobody could tell from logs whether JWT_SECRET was set or not.
//
// Now:
//   - Missing in production (NODE_ENV=production): hard-fail at import time
//     so the deploy fails fast and visibly.
//   - Missing in dev: generate + log the ephemeral value so the developer
//     can copy it into their local .env if they want stable tokens across
//     restarts.
function _resolveJwtSecret() {
  const provided = (process.env.JWT_SECRET || '').trim();
  if (provided) {
    if (provided.length < 32) {
      throw new Error(
        'JWT_SECRET is set but shorter than 32 chars — rotate it to a ' +
        'cryptographically random value (e.g. `openssl rand -hex 48`).'
      );
    }
    return provided;
  }
  if ((process.env.NODE_ENV || '').toLowerCase() === 'production') {
    throw new Error(
      'JWT_SECRET env var is REQUIRED in production. ' +
      'Generate one with `openssl rand -hex 48` and set it as a seenode ' +
      'secret before the next deploy.'
    );
  }
  const ephemeral = randomBytes(32).toString('hex');
  console.warn(
    '[Auth] JWT_SECRET not set — generated ephemeral value for dev. ' +
    'Tokens will invalidate on every restart. Set JWT_SECRET=' + ephemeral +
    ' in your .env to make it stable.'
  );
  return ephemeral;
}
const JWT_SECRET = _resolveJwtSecret();

// ── Dual-write store (file + Redis) ──────────────────────────────────────────

const usersStore = new PersistStore('crucix:users', USERS_FILE, []);

export async function initUsersStore() {
  _migrateLegacyUsersFile();   // R-F1687: durable-volume cutover (idempotent)
  await usersStore.init();
}

function loadUsers() {
  return usersStore.read() || [];
}

function saveUsers(users) {
  usersStore.write(users);
}

function cleanUser(user) {
  if (!user) return null;
  // R-F2035 — also strip verificationAttempts (internal brute-force counter).
  const { passwordHash, verificationCode, verificationExpiry, resetCode, resetExpiry,
          verificationAttempts, ...clean } = user;
  // R-F2349 — derive the shared avatarUrl (versioned for cache-busting) so every
  // surface (main profile, sidebar, Network) references the same photo. null when
  // the user has not uploaded one → callers fall back to initials.
  clean.avatarUrl = clean.avatarUpdatedAt
    ? `/api/profile/photo/${clean.id}?v=${Date.parse(clean.avatarUpdatedAt) || 0}`
    : null;
  return clean;
}

// ── Password & Token ──────────────────────────────────────────────────────────

export function hashPassword(password) {
  const salt = randomBytes(16).toString('hex');
  const hash = pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
  return `${salt}:${hash}`;
}

/**
 * R-F4003 (C-82) — constant-time comparison.
 *
 * `candidate === hash` short-circuits at the first differing byte, so the time
 * taken leaks how long a prefix matched. The practical risk here is lower than on
 * the token path (an attacker does not choose the stored hash), but the fix is
 * one line and the alternative is explaining to an auditor why the codebase
 * compares one secret in constant time and its neighbour by `===`.
 *
 * Both sides are lowercase hex of a FIXED width, so the length check leaks
 * nothing about the secret — only that the stored record is malformed.
 */
export function verifyPassword(password, stored) {
  try {
    const [salt, hash] = stored.split(':');
    const candidate = pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
    return equalsConstantTime(candidate, hash);
  } catch {
    return false;
  }
}

/**
 * Constant-time string equality for equal-length secrets.
 *
 * `timingSafeEqual` THROWS when the buffers differ in length, so a naive swap
 * turns a malformed token into a 500 rather than a clean rejection — and the
 * throw is itself a length oracle. The length is compared first and returns
 * false, which is safe here because every value this is used on has a fixed
 * width (a base64url SHA-256 signature, a hex PBKDF2 digest): the length carries
 * no secret, only "this input is the wrong shape".
 */
function equalsConstantTime(a, b) {
  const bufA = Buffer.from(String(a ?? ''), 'utf8');
  const bufB = Buffer.from(String(b ?? ''), 'utf8');
  if (bufA.length !== bufB.length) return false;
  return timingSafeEqual(bufA, bufB);
}

/**
 * Mint a signed token.
 *
 * R-F3834 — `stage` marks a token that is NOT a session: it stands for a
 * half-finished authentication and may only be redeemed by the step that
 * finishes it. Omit it for an ordinary session token.
 *
 * @param {string} userId
 * @param {string} role
 * @param {'5m'|'7d'} [ttl]
 * @param {number} [tokenVersion]
 * @param {string|null} [stage]  e.g. 'pre2fa'
 */
export function createToken(userId, role, ttl = '7d', tokenVersion = 0, stage = null) {
  const ms = ttl === '5m' ? 5 * 60 * 1000 : 7 * 24 * 60 * 60 * 1000;
  const payload = { userId, role, ver: tokenVersion, iat: Date.now(), exp: Date.now() + ms };
  // Only present on staged tokens, so an ordinary token's claim set is byte-for-byte
  // what it was before this change.
  if (stage) payload.stage = stage;
  const data = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = createHmac('sha256', JWT_SECRET).update(data).digest('base64url');
  return `${data}.${sig}`;
}

/**
 * Verify a token and return its claims.
 *
 * R-F3834 — the stage check is enforced HERE, fail-closed, and not at the call
 * sites. Five places grant access from this function (server.mjs:2250 deep
 * search, :5635 requireAuth, :5785 requirePageRole, :7949 /events, :8305 the
 * socket.io handshake). Before this, the 2FA pre-auth token carried the same
 * claims as a session token, so all five accepted it and a caller holding only
 * the password of a 2FA-protected account had a five-minute full session —
 * enough to change the password or disable 2FA outright.
 *
 * Checking it per call site would have fixed those five and left the sixth to be
 * written later. Defaulting to "a staged token is not a session" means a new
 * gate is safe the day it is added, and a new STAGE is refused by every existing
 * gate until someone deliberately opts in.
 *
 * @param {string} token
 * @param {{stage?: string|null}} [opts] `stage` demands EXACTLY that stage.
 */
export function verifyToken(token, { stage = null } = {}) {
  if (!token) throw new Error('No token provided');
  const [data, sig] = token.split('.');
  if (!data || !sig) throw new Error('Malformed token');
  const expected = createHmac('sha256', JWT_SECRET).update(data).digest('base64url');
  // R-F4003 (C-82) — was `expected !== sig`. This is the comparison that matters:
  // the attacker CONTROLS `sig` and can iterate it, which is the textbook
  // precondition for a timing attack, and a short-circuiting compare leaks the
  // prefix-match length. Remote exploitation across a network is impractical, but
  // the fix is one call and leaving it is a certain audit finding.
  if (!equalsConstantTime(expected, sig)) throw new Error('Invalid token signature');
  const payload = JSON.parse(Buffer.from(data, 'base64url').toString('utf8'));
  if (payload.exp < Date.now()) throw new Error('Token expired');
  // Exact match in BOTH directions: a staged token is not a session, and a
  // session token is not a substitute for completing a stage.
  const got = payload.stage || null;
  if (got !== stage) {
    throw new Error(
      stage
        ? `Expected a '${stage}' stage token, got ${got ? `'${got}'` : 'a session token'}`
        : `Staged token ('${got}') cannot be used as a session token`,
    );
  }
  return payload;
}

// ── Generators ────────────────────────────────────────────────────────────────

export function generateId() {
  return randomBytes(6).toString('hex'); // 12-char hex
}

export function generateCode() {
  const n = parseInt(randomBytes(3).toString('hex'), 16) % 1000000;
  return String(n).padStart(6, '0');
}

// ── CRUD ──────────────────────────────────────────────────────────────────────

export function createUser(data) {
  const users = loadUsers();

  const {
    username, email, password, fullName, role = 'viewer',
    // R-F48b: organisation context captured at registration. All optional
    // — empty defaults preserve compatibility with the legacy 1-screen
    // signup. The Python brain reads `sector` to pick a persona overlay
    // at chat time; the rest drive default heatmap regions, source mix
    // priority, and onboarding examples.
    accountType = 'individual',
    companyName = '',
    companyCountry = '',
    companySize = '',
    sector = '',
    jobTitle = '',
    useCases = [],
    regions = [],
    languages = [],
    volumeEstimate = '',
    complianceNeeds = [],
    purposeStatement = '',
  } = data;

  const id = generateId();
  const passwordHash = hashPassword(password);
  const verificationCode = generateCode();
  const verificationExpiry = new Date(Date.now() + 15 * 60 * 1000).toISOString();

  const user = {
    id,
    username,
    email: email.toLowerCase().trim(),
    passwordHash,
    fullName: fullName || username,
    role,
    status:       'pending_verification',
    tokenVersion: 0,
    verificationCode,
    verificationExpiry,
    resetCode: null,
    resetExpiry: null,
    telegramUsername: null,
    notifyDigest: true,
    notifyFlash: true,
    notifyPush: false,
    // ARIA Network (R-F2342) — community presence layer. Opt-in, privacy by
    // default (GDPR Art. 25): a user is invisible in the network until they
    // explicitly turn presence on. lastSeenAt stamps the last disconnect.
    networkVisible: false,
    lastSeenAt: null,
    // Profile photo (R-F2349) — ONE source of truth: the main-profile upload
    // feeds the Network roster + sidebar avatar. Stored as a file keyed by id;
    // the record only holds the mime + version stamp (avatarUrl is derived).
    avatarUpdatedAt: null,
    avatarMime: null,
    // Billing — seeded on creation so /api/billing/me + cleanUser have a
    // stable shape from the first request. Stripe identifiers populate
    // when the user runs /checkout for the first time. Existing users
    // created before this scaffold landed lack these fields; the read
    // path defaults to 'free' so they don't break.
    tier: 'free',
    stripeCustomerId: null,
    stripeSubscriptionId: null,
    stripeProductId: null,
    subscriptionStatus: null,
    subscriptionPeriodEnd: null,
    cancelAtPeriodEnd: false,
    // Organisation / use-case context (R-F48b). Empty strings / arrays
    // for legacy users; cleanUser passes these through to /api/auth/me
    // and the Python brain picks up `sector` to resolve a persona.
    accountType:       String(accountType || 'individual').slice(0, 32),
    companyName:       String(companyName || '').slice(0, 200),
    companyCountry:    String(companyCountry || '').slice(0, 80),
    companySize:       String(companySize || '').slice(0, 32),
    sector:            String(sector || '').slice(0, 64),
    jobTitle:          String(jobTitle || '').slice(0, 120),
    useCases:          Array.isArray(useCases) ? useCases.slice(0, 20).map(s => String(s).slice(0, 64)) : [],
    regions:           Array.isArray(regions)  ? regions.slice(0, 30).map(s => String(s).slice(0, 64))  : [],
    languages:         Array.isArray(languages) ? languages.slice(0, 20).map(s => String(s).slice(0, 16)) : [],
    volumeEstimate:    String(volumeEstimate || '').slice(0, 32),
    complianceNeeds:   Array.isArray(complianceNeeds) ? complianceNeeds.slice(0, 20).map(s => String(s).slice(0, 64)) : [],
    purposeStatement:  String(purposeStatement || '').slice(0, 600),
    createdAt: new Date().toISOString(),
    lastLogin: null,
  };

  users.push(user);
  saveUsers(users);
  return cleanUser(user);
}

export function findUserByEmail(email) {
  const users = loadUsers();
  return users.find(u => u.email === email.toLowerCase().trim()) || null;
}

export function findUserById(id) {
  const users = loadUsers();
  return users.find(u => u.id === id) || null;
}

export function findUserByUsername(username) {
  const users = loadUsers();
  return users.find(u => u.username === username) || null;
}

export function updateUser(id, updates) {
  const users = loadUsers();
  const idx = users.findIndex(u => u.id === id);
  if (idx === -1) throw new Error(`User ${id} not found`);
  users[idx] = { ...users[idx], ...updates };
  saveUsers(users);
  return cleanUser(users[idx]);
}

export function revokeTokens(id) {
  const users = loadUsers();
  const idx = users.findIndex(u => u.id === id);
  if (idx === -1) throw new Error(`User ${id} not found`);
  users[idx].tokenVersion = (users[idx].tokenVersion || 0) + 1;
  saveUsers(users);
  return cleanUser(users[idx]);
}

export function deleteUser(id) {
  const users = loadUsers();
  const filtered = users.filter(u => u.id !== id);
  if (filtered.length === users.length) throw new Error(`User ${id} not found`);
  saveUsers(filtered);
}

export function listUsers() {
  return loadUsers().map(cleanUser);
}

// ── Admin Bootstrap ───────────────────────────────────────────────────────────

// R-F427: snapshot of who is currently treated as the admin. Set by
// initAdminUser at boot, read by /api/auth/system-status so the operator can
// see from the outside which row the running process considers authoritative.
// Reset on every initAdminUser run.
let _adminIdentitySnapshot = {
  bootedAt: null,
  adminCount: 0,
  adminEmails: [],         // never returned in clear via the public endpoint
  envEmail: null,
  matchesEnv: false,       // true iff exactly one admin AND it equals ADMIN_EMAIL
};

// R-F432: bootstrap decision trace — externally visible via
// /api/auth/system-status so the operator can diagnose "why didn't
// initAdminUser auto-create the admin row?" without shell access to seenode
// logs. Records the env-var lengths (not values), and the exact skip reason
// when bootstrap doesn't fire. Captured even when admin rows already exist
// (in which case `attempted: false` because we short-circuit before the
// bootstrap branch).
let _bootstrapTrace = {
  attempted: false,
  succeeded: false,
  skipReason: null,         // one of: null | 'admin-already-exists' | 'env-missing' | 'env-password-short' | 'unknown-failure'
  envEmailLen: 0,
  envPasswordLen: 0,
  ranAt: null,
};

export function getAdminIdentitySnapshot() {
  return { ..._adminIdentitySnapshot, adminEmails: _adminIdentitySnapshot.adminEmails.slice() };
}

export function getBootstrapTrace() {
  return { ..._bootstrapTrace };
}

export async function initAdminUser() {
  const users = loadUsers();
  const adminRows = users.filter(u => u && u.role === 'admin');
  const envEmail = (process.env.ADMIN_EMAIL || '').toLowerCase().trim();
  const envPassword = (process.env.ADMIN_PASSWORD || '').trim();

  // R-F432: reset trace at top of run so /api/auth/system-status always
  // reflects the latest decision.
  _bootstrapTrace = {
    attempted: false,
    succeeded: false,
    skipReason: null,
    envEmailLen: envEmail.length,
    envPasswordLen: envPassword.length,
    ranAt: new Date().toISOString(),
  };

  // R-F427 admin-identity assertion. Logged loudly at boot so the operator
  // doesn't have to guess from the outside who the server treats as admin.
  // Never THROWS — boot must not break on anomalies. We emit warnings, store
  // a snapshot for /api/auth/system-status, and continue.
  const matchingAdmins = envEmail ? adminRows.filter(a => a.email === envEmail) : adminRows;
  const matchesEnv = !!envEmail && matchingAdmins.length === 1 && adminRows.length === 1;

  _adminIdentitySnapshot = {
    bootedAt: new Date().toISOString(),
    adminCount: adminRows.length,
    adminEmails: adminRows.map(a => a.email).filter(Boolean),
    envEmail: envEmail || null,
    matchesEnv,
  };

  console.log('[Auth] ── admin identity ────────────────────────');
  console.log(`[Auth]   ADMIN_EMAIL env: ${envEmail || '<unset>'}`);
  console.log(`[Auth]   total users in store: ${users.length}`);
  console.log(`[Auth]   admin rows: ${adminRows.length}`);
  for (const a of adminRows) {
    const tag = envEmail && a.email === envEmail ? '  (env match)' : (envEmail ? '  (NOT in ADMIN_EMAIL)' : '');
    console.log(`[Auth]     - ${a.email}  status=${a.status}  tokenVer=${a.tokenVersion || 0}${tag}`);
  }
  if (adminRows.length === 0) {
    console.warn('[Auth]   WARNING: no admin rows exist yet. initAdminUser will attempt to bootstrap below.');
  } else if (adminRows.length > 1) {
    console.warn(`[Auth]   WARNING: ${adminRows.length} admin rows present. Only one canonical admin (ADMIN_EMAIL) should exist; the others may be stale. Investigate via /api/auth/system-status.`);
  } else if (envEmail && adminRows[0].email !== envEmail) {
    console.warn(`[Auth]   WARNING: ADMIN_EMAIL (${envEmail}) does not match the single admin row (${adminRows[0].email}). Login will check the stored row, NOT the env var.`);
  } else if (matchesEnv) {
    console.log(`[Auth]   ✓ admin identity confirmed: ${envEmail}`);
  }
  console.log('[Auth] ────────────────────────────────────────────');

  if (adminRows.length > 0) {
    _bootstrapTrace.skipReason = 'admin-already-exists';
    return;
  }

  _bootstrapTrace.attempted = true;

  // Bootstrap path: no admin yet, create one from ADMIN_EMAIL + ADMIN_PASSWORD.
  // Reuse envEmail / envPassword captured at function entry rather than
  // re-reading process.env to keep one source of truth for the run.

  // Pre-2026-04-20 this had `|| 'Arkmurus2024!'` as fallback — a
  // predictable password hardcoded in the public GitHub repo. On any
  // future fresh deploy without ADMIN_PASSWORD, an admin account would
  // be silently created with that publicly-known password. Now we
  // hard-fail init if the env vars aren't set; the operator must set
  // real values before the admin gets created.
  if (!envEmail || !envPassword) {
    const missing = [!envEmail && 'ADMIN_EMAIL', !envPassword && 'ADMIN_PASSWORD']
      .filter(Boolean).join(' + ');
    console.error(
      `[Auth] initAdminUser SKIPPED: ${missing} env var(s) not set. ` +
      'Set both on the seenode app BEFORE first boot so the bootstrap ' +
      'admin gets real credentials. No admin will be created this run.'
    );
    _bootstrapTrace.skipReason = 'env-missing';
    return;
  }
  if (envPassword.length < 12) {
    console.error(
      '[Auth] initAdminUser SKIPPED: ADMIN_PASSWORD too short (min 12 chars). ' +
      'Rotate it to something longer — e.g. `openssl rand -base64 24`.'
    );
    _bootstrapTrace.skipReason = 'env-password-short';
    return;
  }

  const id = generateId();
  const passwordHash = hashPassword(envPassword);

  const admin = {
    id,
    username: 'admin',
    email: envEmail,
    passwordHash,
    fullName: 'Arkmurus Administrator',
    role: 'admin',
    status: 'active',
    verificationCode: null,
    verificationExpiry: null,
    resetCode: null,
    resetExpiry: null,
    telegramUsername: null,
    notifyDigest: true,
    notifyFlash: true,
    notifyPush: false,
    // ARIA Network (R-F2342) — community presence layer. Opt-in, privacy by
    // default (GDPR Art. 25): a user is invisible in the network until they
    // explicitly turn presence on. lastSeenAt stamps the last disconnect.
    networkVisible: false,
    lastSeenAt: null,
    // Profile photo (R-F2349) — ONE source of truth: the main-profile upload
    // feeds the Network roster + sidebar avatar. Stored as a file keyed by id;
    // the record only holds the mime + version stamp (avatarUrl is derived).
    avatarUpdatedAt: null,
    avatarMime: null,
    createdAt: new Date().toISOString(),
    lastLogin: null,
  };

  users.push(admin);
  saveUsers(users);
  _bootstrapTrace.succeeded = true;

  // NEVER log the password — fly / seenode log retention may outlast
  // the password rotation and anyone with log access would see it.
  // Pre-2026-04-20 this was echoed verbatim to stdout.
  console.log('[Auth] ─────────────────────────────────────────────');
  console.log('[Auth] Admin user created (no existing admin found)');
  console.log(`[Auth]   Email: ${envEmail}`);
  console.log('[Auth]   Password: (from ADMIN_PASSWORD env, not logged)');
  console.log(`[Auth]   ✓ admin identity confirmed via bootstrap: ${envEmail}`);
  console.log('[Auth] ─────────────────────────────────────────────');

  // R-F427: refresh the identity snapshot after bootstrap so the post-boot
  // /api/auth/system-status response reflects the newly created admin.
  _adminIdentitySnapshot = {
    bootedAt: _adminIdentitySnapshot.bootedAt || new Date().toISOString(),
    adminCount: 1,
    adminEmails: [envEmail],
    envEmail,
    matchesEnv: true,
  };
}
