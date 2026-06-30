import { NextRequest, NextResponse } from 'next/server';
import { decodeToken, roleAllows, TOKEN_COOKIE, type Role } from '@/lib/auth';

// Route-group gating. Route groups (customer)/(support)/(admin) don't appear in the URL,
// so we map URL prefixes to the role(s) they require. The backend re-enforces on every
// /api call (requireRole, R-F2170) — this middleware is the UX gate only.
const ADMIN_PREFIXES = ['/admin', '/brain', '/gaps', '/design', '/flags', '/users', '/status'];
const SUPPORT_PREFIXES = ['/support', '/accounts', '/tickets'];

// Always-public (no auth needed).
const PUBLIC_PREFIXES = ['/signin', '/api/session', '/_next', '/favicon', '/assets'];

function requiredRoles(pathname: string): Role[] | null {
  if (ADMIN_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/'))) return ['admin'];
  if (SUPPORT_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/'))) return ['support'];
  // Everything else under the app requires an authenticated customer (or above).
  return ['customer', 'support', 'admin'];
}

export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;
  if (PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(p + '/'))) {
    return NextResponse.next();
  }

  const token = req.cookies.get(TOKEN_COOKIE)?.value;
  const user = decodeToken(token);

  if (!user) {
    const url = req.nextUrl.clone();
    url.pathname = '/signin';
    url.searchParams.set('next', pathname);
    return NextResponse.redirect(url);
  }

  const need = requiredRoles(pathname);
  if (need && !roleAllows(user.role, need)) {
    // Authenticated but wrong panel — bounce to their own home, never expose a 403 dead-end.
    const url = req.nextUrl.clone();
    url.pathname = user.role === 'admin' ? '/admin' : user.role === 'support' ? '/support' : '/dashboard';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Run on everything except Next internals + static files.
  matcher: ['/((?!_next/static|_next/image|favicon.ico|assets).*)'],
};
