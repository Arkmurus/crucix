# aria-app

ARIA's web frontend — **Next.js 15 (App Router) + shadcn/ui + Tailwind**. Frontend only:
it consumes the existing backend contracts and introduces **no new persistence** (CLAUDE.md §6).

- `/api/*` → `server.mjs` (auth, billing, OSINT/pipeline data)
- `/api/aria/*` → brain proxy (DD, vault, knowledge, chat)

## Panels (role-gated)

| Role | Panel | URLs |
|---|---|---|
| `customer` (paying business + individual) | Customer | `/dashboard`, `/reports`, `/vault`, `/watchlist`, `/chat`, `/account` |
| `support` (customer support) | Support console | `/support`, `/accounts`, `/tickets` |
| `admin` (coders + designers) | Main admin | `/admin`, `/brain`, `/gaps`, `/design`, `/flags`, `/users`, `/status` |

Role comes from the existing JWT (decoded for routing in `lib/auth.ts`; **enforced server-side**
by `requireRole`, R-F2170). `middleware.ts` is the UX gate; the backend is the source of truth.

## Dev

```bash
cd aria-app
cp .env.local.example .env.local   # point BACKEND_URL at server.mjs
npm install
npm run dev                        # http://localhost:3200
```

## Deploy (fly app `aria-app`)

```bash
flyctl deploy aria-app --config fly.app.toml \
  --build-arg ARIA_BUILD_GIT_SHA=$(git rev-parse HEAD)
flyctl secrets set BACKEND_URL=http://aria-web.internal:3117 \
  NEXT_PUBLIC_BACKEND_URL=https://intel.arkmurus.com -a aria-app
```

See `docs/aria_app_nextjs_buildout_2026_06_30.md` for the full plan and migration order.
