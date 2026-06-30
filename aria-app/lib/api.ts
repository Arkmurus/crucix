// Thin client over the existing backend contracts:
//   /api/*       -> server.mjs (Node-local: auth, billing, OSINT/pipeline data)
//   /api/aria/*  -> brain proxy (DD, vault, knowledge, chat)
// No new backend is introduced (CLAUDE.md §6/§8). aria-app is frontend-only.

import { cookies } from 'next/headers';
import { TOKEN_COOKIE } from './auth';

function backendBase(): string {
  return process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:3117';
}

/** Server-side fetch that forwards the caller's JWT from the httpOnly cookie. */
export async function apiServer<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const token = cookies().get(TOKEN_COOKIE)?.value;
  const res = await fetch(`${backendBase()}${path}`, {
    ...init,
    headers: {
      'content-type': 'application/json',
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`API ${path} -> ${res.status}`);
  return (await res.json()) as T;
}
