import { NextRequest, NextResponse } from 'next/server';
import { decodeToken, roleAllows, TOKEN_COOKIE, type Role } from '@/lib/auth';

// aria-app is the public front door (intel.arkmurus.com). The middleware must gate ONLY
// the pages aria-app actually SERVES — everything else (unmigrated pages, marketing,
// legal, signup, /api/*, /healthz, static) falls through to be proxied to aria-web by the
// next.config catch-all rewrite, and aria-web enforces its own auth there. Gating those
// here would wrongly bounce them to /signin (R-F2175).
//
// ROLLBACK MODE (R-F2187): all paths proxy to aria-web (old platform), which enforces
// its own auth — so aria-app gates NOTHING. As pages are rebuilt to parity, re-add their
// prefix here so the new page is auth-gated again.
const ADMIN_PREFIXES: string[] = [];
const SUPPORT_PREFIXES: string[] = [];
const CUSTOMER_PREFIXES: string[] = ['/dashboard'];

function matchPrefix(pathname: string, list: string[]): boolean {
  return list.some((p) => pathname === p || pathname.startsWith(p + '/'));
}

/** Roles required for an aria-app-owned page, or null if this path is NOT an owned page. */
function requiredRoles(pathname: string): Role[] | null {
  if (matchPrefix(pathname, ADMIN_PREFIXES)) return ['admin'];
  if (matchPrefix(pathname, SUPPORT_PREFIXES)) return ['support'];
  if (matchPrefix(pathname, CUSTOMER_PREFIXES)) return ['customer', 'support', 'admin'];
  return null;
}

export function middleware(req: NextRequest) {
  const need = requiredRoles(req.nextUrl.pathname);
  if (!need) return NextResponse.next(); // not an owned page -> serve/proxy untouched

  const user = decodeToken(req.cookies.get(TOKEN_COOKIE)?.value);
  if (!user) {
    const url = req.nextUrl.clone();
    url.pathname = '/signin';
    url.searchParams.set('next', req.nextUrl.pathname);
    return NextResponse.redirect(url);
  }

  if (!roleAllows(user.role, need)) {
    const url = req.nextUrl.clone();
    url.pathname = user.role === 'admin' ? '/admin' : user.role === 'support' ? '/support' : '/dashboard';
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  // Skip Next internals + static; the handler itself decides what to gate vs pass through.
  matcher: ['/((?!_next/static|_next/image|favicon.ico|assets).*)'],
};
