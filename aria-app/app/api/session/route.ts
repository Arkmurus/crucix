import { NextRequest, NextResponse } from 'next/server';
import { TOKEN_COOKIE } from '@/lib/auth';

// Sets/clears the httpOnly JWT cookie. The token itself is minted by server.mjs
// (/api/auth/login); this route only parks it in an httpOnly cookie so middleware +
// SSR can read it. No credential handling happens here.

const COOKIE_OPTS = {
  httpOnly: true,
  sameSite: 'lax' as const,
  secure: process.env.NODE_ENV === 'production',
  path: '/',
  maxAge: 60 * 60 * 24 * 7, // 7d; backend exp still governs validity (decodeToken checks exp)
};

export async function POST(req: NextRequest) {
  const contentType = req.headers.get('content-type') || '';

  // Form post (e.g. the sidebar "Sign out" button) -> logout + redirect.
  if (contentType.includes('application/x-www-form-urlencoded') || contentType.includes('multipart/form-data')) {
    const form = await req.formData();
    if (form.get('action') === 'logout') {
      // 303 converts the form POST to a GET so the browser lands on the /signin
      // PAGE (a 307 would replay the POST -> 405 Method Not Allowed).
      const res = NextResponse.redirect(new URL('/signin', req.url), { status: 303 });
      res.cookies.set(TOKEN_COOKIE, '', { ...COOKIE_OPTS, maxAge: 0 });
      return res;
    }
  }

  // JSON post from the sign-in page: { token }.
  let token: string | undefined;
  try {
    const body = (await req.json()) as { token?: string };
    token = body.token;
  } catch {
    return NextResponse.json({ ok: false, error: 'bad_request' }, { status: 400 });
  }
  if (!token) return NextResponse.json({ ok: false, error: 'missing_token' }, { status: 400 });

  const res = NextResponse.json({ ok: true });
  res.cookies.set(TOKEN_COOKIE, token, COOKIE_OPTS);
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.set(TOKEN_COOKIE, '', { ...COOKIE_OPTS, maxAge: 0 });
  return res;
}
