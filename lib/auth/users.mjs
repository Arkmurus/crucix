// lib/auth/users.mjs
// User store — JSON file-based, no external dependencies
// Uses node:crypto for password hashing (PBKDF2) and JWT-like tokens (HMAC-SHA256)

import { createHmac, pbkdf2Sync, randomBytes } from 'node:crypto';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
const USERS_FILE = join(RUNS_DIR, 'users.json');

// JWT-like secret — stable per process, persisted across restarts via env
const JWT_SECRET = process.env.JWT_SECRET || randomBytes(32).toString('hex');

// ── File helpers ──────────────────────────────────────────────────────────────

function ensureDir() {
  if (!existsSync(RUNS_DIR)) mkdirSync(RUNS_DIR, { recursive: true });
}

function loadUsers() {
  ensureDir();
  if (!existsSync(USERS_FILE)) return [];
  try {
    return JSON.parse(readFileSync(USERS_FILE, 'utf8'));
  } catch {
    return [];
  }
}

function saveUsers(users) {
  ensureDir();
  writeFileSync(USERS_FILE, JSON.stringify(users, null, 2));
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

export function createToken(userId, role) {
  const payload = { userId, role, iat: Date.now(), exp: Date.now() + 7 * 24 * 60 * 60 * 1000 };
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

  const { username, email, password, fullName, role = 'viewer' } = data;

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
    status: 'pending_verification',
    verificationCode,
    verificationExpiry,
    resetCode: null,
    resetExpiry: null,
    telegramUsername: null,
    notifyDigest: true,
    notifyFlash: true,
    notifyPush: false,
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

  const email    = process.env.ADMIN_EMAIL    || 'admin@arkmurus.com';
  const password = process.env.ADMIN_PASSWORD || 'Arkmurus2024!';

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

  console.log('[Auth] ─────────────────────────────────────────────');
  console.log('[Auth] Admin user created (no existing admin found)');
  console.log(`[Auth]   Email:    ${email}`);
  console.log(`[Auth]   Password: ${password}`);
  console.log('[Auth] Change these credentials after first login!');
  console.log('[Auth] ─────────────────────────────────────────────');
}
