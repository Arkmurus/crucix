/**
 * R-F3074 — server-side revocation for the bearer tokens issued by users.mjs.
 *
 * Why this exists: `POST /api/auth/logout` cleared a cookie and returned
 * `{ok:true}`, but the app authenticates with the JWT held in
 * `localStorage.crucix_token`, which the server never invalidated. Verified
 * 2026-07-25 on a live session: logout → 200, then the SAME token still
 * answered `GET /api/auth/me` with 200. So "Sign out" was purely cosmetic —
 * a token captured from a shared machine, a browser profile, or a copied
 * console value stayed usable for the remainder of its 7-day life, and there
 * was nothing the user could do about it.
 *
 * Why not bump `tokenVersion`: that mechanism already exists (users.mjs +
 * the `payload.ver` check in requireAuth) and R-F2986 uses it for
 * suspend/role-change. But it is an ALL-SESSIONS kill switch — logging out on
 * a phone would silently sign the same person out on their laptop. Logout is
 * a per-device action, so it needs per-token revocation.
 *
 * Design: store the token's SIGNATURE (never the payload — it carries the
 * userId) against the token's own expiry. The set is naturally small (one
 * entry per logout, self-pruning at exp) and lives on the durable /data volume
 * like users.json (R-F1687), so a deploy cannot silently un-revoke a session.
 */
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PersistStore } from '../persist/store.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, '..', '..');
const PERSIST_DIR = process.env.PERSIST_DIR || join(ROOT, 'runs');
const FILE = process.env.TOKEN_DENYLIST_FILE_OVERRIDE
  || join(PERSIST_DIR, 'token_denylist.json');

// Shape: { "<signature>": <expiryMs>, ... }
const store = new PersistStore('crucix:token_denylist', FILE, {});

export async function initTokenDenylist() {
  await store.init();
  _prune();
}

/** The signature is the part after the final '.' — unique per issued token. */
function _sig(token) {
  const s = String(token || '');
  const i = s.lastIndexOf('.');
  return i > 0 ? s.slice(i + 1) : '';
}

function _prune() {
  const map = store.read() || {};
  const now = Date.now();
  let changed = false;
  for (const [sig, exp] of Object.entries(map)) {
    if (!exp || exp <= now) { delete map[sig]; changed = true; }
  }
  if (changed) store.write(map);
  return map;
}

/**
 * Revoke one token. `expiresAtMs` comes from the token's own `exp` claim, so
 * the entry disappears exactly when the token would have died anyway.
 * Best-effort: a persistence failure must not turn logout into a 500.
 */
export function revokeToken(token, expiresAtMs) {
  const sig = _sig(token);
  if (!sig) return false;
  try {
    const map = _prune();
    map[sig] = Number(expiresAtMs) || (Date.now() + 7 * 24 * 60 * 60 * 1000);
    store.write(map);
    return true;
  } catch {
    return false;
  }
}

/** True when this exact token has been logged out. Cheap: one object lookup. */
export function isTokenRevoked(token) {
  const sig = _sig(token);
  if (!sig) return false;
  try {
    const exp = (store.read() || {})[sig];
    if (!exp) return false;
    if (exp <= Date.now()) return false;   // stale entry; _prune sweeps it later
    return true;
  } catch {
    return false;   // fail OPEN on a store error — never lock everyone out
  }
}

export function _denylistSizeForTests() {
  return Object.keys(store.read() || {}).length;
}
