// Auth bridge to the existing server.mjs JWT (no Supabase — CLAUDE.md §6).
// The JWT is ISSUED and VERIFIED by server.mjs; aria-app only DECODES the payload
// for client-side routing. The backend remains the source of truth: every /api call
// is re-authorised server-side via requireAuth/requireRole. Decoding here is a UX gate,
// never a security boundary.

export type Role = 'customer' | 'support' | 'admin';

export interface SessionUser {
  id: string;
  email?: string;
  role: Role;
  tier?: string;
  exp?: number;
}

export const TOKEN_COOKIE = 'aria_token';

/** base64url -> utf-8 string, Edge-runtime safe (uses atob, present in Edge + browser). */
function b64urlDecode(part: string): string {
  const b64 = part.replace(/-/g, '+').replace(/_/g, '/').padEnd(part.length + ((4 - (part.length % 4)) % 4), '=');
  // atob yields a binary string; decode as UTF-8.
  const bin = atob(b64);
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

/**
 * Decode (NOT verify) a JWT payload. Returns null on any malformed/expired input.
 *
 * The token shape is dictated by server.mjs / lib/auth/users.mjs createToken():
 *   { userId, role, ver, iat, exp }   — NOTE: `exp` and `iat` are epoch MILLISECONDS,
 *   not seconds, and the user id field is `userId`. There is no email in the token.
 * Backend roles are `viewer` (regular user) and `admin`; `support` is added in R-F2170.
 * We normalise `viewer`/`user` -> `customer` for the UI panel model.
 */
export function decodeToken(token: string | undefined | null): SessionUser | null {
  if (!token) return null;
  const parts = token.split('.');
  // Backend token is `data.sig` (2 parts); tolerate a 3-part standard JWT too.
  if (parts.length < 2) return null;
  const payloadPart = parts.length === 3 ? parts[1] : parts[0];
  try {
    const payload = JSON.parse(b64urlDecode(payloadPart)) as Record<string, unknown>;
    const rawRole = String(payload.role ?? 'customer');
    const role: Role = rawRole === 'admin' ? 'admin' : rawRole === 'support' ? 'support' : 'customer';
    // exp is epoch milliseconds (Date.now() + ttl).
    if (typeof payload.exp === 'number' && payload.exp < Date.now()) return null;
    return {
      id: String(payload.userId ?? payload.sub ?? payload.id ?? ''),
      email: payload.email ? String(payload.email) : undefined,
      role,
      tier: payload.tier ? String(payload.tier) : undefined,
      exp: typeof payload.exp === 'number' ? payload.exp : undefined,
    };
  } catch {
    return null;
  }
}

/** Landing path for a role after sign-in. */
export function homeForRole(role: Role): string {
  if (role === 'admin') return '/admin';
  if (role === 'support') return '/support';
  return '/dashboard';
}

/** Does `role` satisfy a route that needs one of `allowed`? admin is a superset of support. */
export function roleAllows(role: Role, allowed: Role[]): boolean {
  if (allowed.includes(role)) return true;
  // admin can see support areas; support cannot see admin.
  if (role === 'admin' && allowed.includes('support')) return true;
  return false;
}
