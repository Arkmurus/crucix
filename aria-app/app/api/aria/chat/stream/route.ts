import { NextRequest } from 'next/server';
import { cookies } from 'next/headers';
import { decodeToken, TOKEN_COOKIE } from '@/lib/auth';

// Streaming chat bridge: the browser holds NO token (httpOnly cookie), so this
// server-side handler reads the cookie, attaches Authorization: Bearer, forwards to
// the backend SSE endpoint (server.mjs /api/aria/chat/stream), and pipes the stream
// straight back. This route handler is a real filesystem route, so it takes precedence
// over the catch-all /api/* proxy rewrite (which can't inject the Bearer).
export const dynamic = 'force-dynamic';

const BACKEND_URL = process.env.BACKEND_URL || 'http://aria-web.internal:3117';

export async function POST(req: NextRequest) {
  const token = (await cookies()).get(TOKEN_COOKIE)?.value;
  const user = decodeToken(token);
  if (!token || !user) {
    return new Response(JSON.stringify({ error: 'unauthorized' }), { status: 401, headers: { 'content-type': 'application/json' } });
  }

  let body: Record<string, unknown> = {};
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    /* empty body tolerated */
  }

  const payload = {
    auto_tools: true,
    user_id: user.id,
    ...body,
    session_id: body.session_id || `aria-app-${user.id}`,
  };

  let backendRes: Response;
  try {
    backendRes = await fetch(`${BACKEND_URL}/api/aria/chat/stream`, {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}`, accept: 'text/event-stream' },
      body: JSON.stringify(payload),
    });
  } catch {
    return new Response(JSON.stringify({ error: 'backend_unreachable' }), { status: 502, headers: { 'content-type': 'application/json' } });
  }

  if (!backendRes.ok || !backendRes.body) {
    const text = await backendRes.text().catch(() => '');
    return new Response(text || JSON.stringify({ error: `backend ${backendRes.status}` }), {
      status: backendRes.status || 502,
      headers: { 'content-type': backendRes.headers.get('content-type') || 'application/json' },
    });
  }

  return new Response(backendRes.body, {
    status: 200,
    headers: {
      'content-type': 'text/event-stream; charset=utf-8',
      'cache-control': 'no-cache, no-transform',
      connection: 'keep-alive',
    },
  });
}
