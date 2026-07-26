// lib/api_keys/store.mjs
// Public API key store — issue / list / lookup / revoke. Mirrors the user
// store shape (PersistStore-backed JSON file with Redis dual-write so a
// fly/seenode restart doesn't lose keys).
//
// Threat model:
//   * Keys are bearer credentials. Compromise = full account chat access.
//   * We hash the secret half (sha256, single-pass — no PBKDF2 because
//     these are random 32-byte values, not user-chosen passwords; the
//     PBKDF2 work factor adds nothing against a high-entropy input).
//   * Users see the full key ONLY at creation time. Subsequent reads
//     show prefix + last 4 chars only ("crx_live_…1234").
//   * Revocation is soft (revokedAt timestamp) so audit history remains.
//
// Key shape:
//   crx_<env>_<base32-encoded 32 random bytes>
//   env = 'live' (only one env right now; placeholder for sk_test analog)
//
// Lookup is O(N) (iterate, hash, compare). With <1000 keys per process
// this is microseconds; if we ever scale past that, add a hash → keyId
// reverse index.

import { createHash, randomBytes } from 'node:crypto';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PersistStore } from '../persist/store.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const RUNS_DIR = join(ROOT, 'runs');
const KEYS_FILE = join(RUNS_DIR, 'api_keys.json');

const keysStore = new PersistStore('crucix:api_keys', KEYS_FILE, []);

export async function initApiKeysStore() {
  await keysStore.init();
}

function loadKeys() {
  return keysStore.read() || [];
}

function saveKeys(keys) {
  keysStore.write(keys);
}

function hashKey(plaintext) {
  return createHash('sha256').update(plaintext).digest('hex');
}

function genKeyId() {
  return 'ak_' + randomBytes(8).toString('hex'); // 16-char-hex id
}

// Generates a fresh key. Returns { record, plaintext } — plaintext is
// shown to the user ONCE and never persisted. record is what we store.
// R-F3139 — scopes. A key is no longer "all of ARIA"; it carries an explicit
// list of what it may reach. DEFAULT_SCOPES is what every key gets unless the
// issuer asks for more, and it deliberately does NOT include 'vetting':
// vetting responses carry criminal-conviction and financial data about named
// individuals, and an MCP client would put that straight into whatever LLM is
// on the other end. A tier check cannot express that distinction — proIntel
// buys API access, it does not by itself mean "and may read screening files".
export const DEFAULT_SCOPES = Object.freeze(['chat']);
export const KNOWN_SCOPES = Object.freeze(['chat', 'dd', 'vetting']);

/** Scopes for a key record. A key issued before R-F3139 has no `scopes`
 *  field; it gets DEFAULT_SCOPES, so old keys can never reach vetting. */
export function scopesFor(record) {
  const raw = record && Array.isArray(record.scopes) ? record.scopes : null;
  return raw && raw.length ? raw : [...DEFAULT_SCOPES];
}

export function keyHasScope(record, scope) {
  return scopesFor(record).includes(scope);
}

export function issueKey({ userId, name = 'API key', scopes = null }) {
  if (!userId) throw new Error('issueKey: userId required');
  const env = 'live';
  // 32 random bytes → 52-char base32 (no padding). Plenty of entropy.
  const secret = randomBytes(32).toString('base64url');
  const plaintext = `crx_${env}_${secret}`;
  const requested = Array.isArray(scopes) && scopes.length
    ? [...new Set(scopes.map(s => String(s).trim().toLowerCase()))]
        .filter(s => KNOWN_SCOPES.includes(s))
    : [...DEFAULT_SCOPES];
  const record = {
    id: genKeyId(),
    userId,
    name: (name || 'API key').toString().slice(0, 60),
    prefix: `crx_${env}_`,
    last4: secret.slice(-4),
    hashed: hashKey(plaintext),
    scopes: requested.length ? requested : [...DEFAULT_SCOPES],
    createdAt: new Date().toISOString(),
    lastUsedAt: null,
    revokedAt: null,
  };
  const keys = loadKeys();
  keys.push(record);
  saveKeys(keys);
  return { record: redact(record), plaintext };
}

// Strip the hash before returning a record to a user.
function redact(record) {
  const { hashed, ...visible } = record;
  return visible;
}

export function listKeysForUser(userId) {
  if (!userId) return [];
  return loadKeys()
    .filter(k => k.userId === userId)
    .sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''))
    .map(redact);
}

export function revokeKey({ userId, id }) {
  if (!userId || !id) return false;
  const keys = loadKeys();
  const idx = keys.findIndex(k => k.id === id && k.userId === userId);
  if (idx === -1) return false;
  if (keys[idx].revokedAt) return true; // already revoked, idempotent
  keys[idx].revokedAt = new Date().toISOString();
  saveKeys(keys);
  return true;
}

// Look up a presented key. Returns the matching record (active only) or
// null. Updates lastUsedAt asynchronously — caller doesn't await.
export function authenticateKey(plaintext) {
  if (!plaintext || typeof plaintext !== 'string') return null;
  if (!plaintext.startsWith('crx_')) return null;
  const hashed = hashKey(plaintext);
  const keys = loadKeys();
  const hit = keys.find(k => k.hashed === hashed && !k.revokedAt);
  if (!hit) return null;
  // Best-effort lastUsedAt update; failures don't block auth.
  try {
    hit.lastUsedAt = new Date().toISOString();
    saveKeys(keys);
  } catch {}
  return redact(hit);
}

export function revokeAllForUser(userId) {
  if (!userId) return 0;
  const keys = loadKeys();
  const now = new Date().toISOString();
  let n = 0;
  for (const k of keys) {
    if (k.userId === userId && !k.revokedAt) {
      k.revokedAt = now;
      n++;
    }
  }
  if (n > 0) saveKeys(keys);
  return n;
}
