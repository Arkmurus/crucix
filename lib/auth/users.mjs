// lib/auth/users.mjs
// User store — JSON file-based, no external dependencies
// Uses node:crypto for password hashing (PBKDF2) and JWT-like tokens (HMAC-SHA256)

import { createHmac, pbkdf2Sync, randomBytes } from 'node:crypto';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PersistStore } from '../persist/store.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
const USERS_FILE = join(RUNS_DIR, 'users.json');

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
  const { passwordHash, verificationCode, verificationExpiry, resetCode, resetExpiry, ...clean } = user;
  return clean;
}

// ── Password & Token ──────────────────────────────────────────────────────────

export function hashPassword(password) {
  const salt = randomBytes(16).toString('hex');
  const hash = pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
  return `${salt}:${hash}`;
}

export function verifyPassword(password, stored) {
  try {
    const [salt, hash] = stored.split(':');
    const candidate = pbkdf2Sync(password, salt, 100000, 64, 'sha512').toString('hex');
    return candidate === hash;
  } catch {
    return false;
  }
}

export function createToken(userId, role, ttl = '7d', tokenVersion = 0) {
  const ms = ttl === '5m' ? 5 * 60 * 1000 : 7 * 24 * 60 * 60 * 1000;
  const payload = { userId, role, ver: tokenVersion, iat: Date.now(), exp: Date.now() + ms };
  const data = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const sig = createHmac('sha256', JWT_SECRET).update(data).digest('base64url');
  return `${data}.${sig}`;
}

export function verifyToken(token) {
  if (!token) throw new Error('No token provided');
  const [data, sig] = token.split('.');
  if (!data || !sig) throw new Error('Malformed token');
  const expected = createHmac('sha256', JWT_SECRET).update(data).digest('base64url');
  if (expected !== sig) throw new Error('Invalid token signature');
  const payload = JSON.parse(Buffer.from(data, 'base64url').toString('utf8'));
  if (payload.exp < Date.now()) throw new Error('Token expired');
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
    // Billing — seeded on creation so /api/billing/me + cleanUser have a
    // stable shape from the first request. Stripe identifiers populate
    // when the user runs /checkout for the first time. Existing users
    // created before this scaffold landed lack these fields; the read
    // path defaults to 'free' so they don't break.
    tier: 'free',
    stripeCustomerId: null,
    stripeSubscriptionId: null,
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

export async function initAdminUser() {
  const users = loadUsers();
  const hasAdmin = users.some(u => u.role === 'admin');
  if (hasAdmin) return;

  // Pre-2026-04-20 this had `|| 'Arkmurus2024!'` as fallback — a
  // predictable password hardcoded in the public GitHub repo. On any
  // future fresh deploy without ADMIN_PASSWORD, an admin account would
  // be silently created with that publicly-known password. Now we
  // hard-fail init if the env vars aren't set; the operator must set
  // real values before the admin gets created.
  const email = (process.env.ADMIN_EMAIL || '').trim();
  const password = (process.env.ADMIN_PASSWORD || '').trim();
  if (!email || !password) {
    const missing = [!email && 'ADMIN_EMAIL', !password && 'ADMIN_PASSWORD']
      .filter(Boolean).join(' + ');
    console.error(
      `[Auth] initAdminUser SKIPPED: ${missing} env var(s) not set. ` +
      'Set both on the seenode app BEFORE first boot so the bootstrap ' +
      'admin gets real credentials. No admin will be created this run.'
    );
    return;
  }
  if (password.length < 12) {
    console.error(
      '[Auth] initAdminUser SKIPPED: ADMIN_PASSWORD too short (min 12 chars). ' +
      'Rotate it to something longer — e.g. `openssl rand -base64 24`.'
    );
    return;
  }

  const id = generateId();
  const passwordHash = hashPassword(password);

  const admin = {
    id,
    username: 'admin',
    email: email.toLowerCase().trim(),
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
    createdAt: new Date().toISOString(),
    lastLogin: null,
  };

  users.push(admin);
  saveUsers(users);

  // NEVER log the password — fly / seenode log retention may outlast
  // the password rotation and anyone with log access would see it.
  // Pre-2026-04-20 this was echoed verbatim to stdout.
  console.log('[Auth] ─────────────────────────────────────────────');
  console.log('[Auth] Admin user created (no existing admin found)');
  console.log(`[Auth]   Email: ${email}`);
  console.log('[Auth]   Password: (from ADMIN_PASSWORD env, not logged)');
  console.log('[Auth] ─────────────────────────────────────────────');
}
