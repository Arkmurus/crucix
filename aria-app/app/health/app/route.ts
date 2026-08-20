import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

export function GET() {
  const buildRev = process.env.ARIA_BUILD_GIT_SHA || 'UNKNOWN-BUILD';
  const verified = buildRev !== 'UNKNOWN-BUILD';

  return NextResponse.json(
    { status: verified ? 'alive' : 'degraded', build_rev: buildRev, native_routes: ['/preview', '/signin', '/api/session', '/health/app'] },
    { status: verified ? 200 : 503 },
  );
}
