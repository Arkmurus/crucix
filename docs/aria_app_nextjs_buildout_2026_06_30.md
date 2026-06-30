# ARIA Web Rebuild — `aria-app` (Next.js + shadcn) buildout

**Date:** 2026-06-30 · **R-numbers:** R-F2169 (scaffold), R-F2170 (role backend) · **Owner:** Claude (operator-directed)

> Operator brief: "need a more engaging aria web … clone [horizon-ui/shadcn-nextjs-boilerplate] to fit the entire aria business purpose, seamless. Users have their own dashboard + admin panel; the power users (designers + coders) get the main admin panel. Thoughtful business review + implementation, like Claude for its own users."
>
> Three user types (operator, 2026-06-30): **paying businesses + individuals** (customers), and the **ARIA team** — **coders + designers** (power users) and **customer support**.

---

## 1. Decision (locked with operator)

- **Approach:** a **dedicated Next.js (App Router) + shadcn/ui app** named `aria-app`, **not** a lift-and-shift of the boilerplate.
- **Stack pin:** **React 18 / Next 14 (stable)** — the boilerplate ships React 19-RC + Next 15, too bleeding-edge for a live product.
- **Backend unchanged:** `server.mjs` (Node monolith) stays as the **API + auth + billing + WhatsApp** backend. `aria-app` is a pure frontend that consumes the existing JSON contracts: `/api/*` (Node-local) and `/api/aria/*` (brain proxy).
- **Constitution fit (§6/§18):** the boilerplate's **Supabase** (paid persistence), **OpenAI** (non-DeepSeek LLM), and **AWS S3** are **stripped**. We reuse only shadcn/ui + Tailwind + the layout/component patterns (shadcn is MIT — components are copied in, not a dependency on the repo). Auth = existing file-based JWT; billing = existing `lib/billing/` Stripe tiers; AI = ARIA brain.

## 2. Why not lift-and-shift the boilerplate

| Boilerplate pillar | ARIA reality | Action |
|---|---|---|
| Supabase (auth + Postgres) | §6 no paid persistence; auth is `users.json`/`sessions.json` JWT | strip; bridge to `/api/auth/*` |
| OpenAI (its AI layer) | §6/§18 DeepSeek + ARIA brain only | strip; call `/api/aria/*` |
| Stripe (its wiring + schema) | ARIA owns `lib/billing/` (free/pro/proIntel, webhook-granted) | use ARIA's `/api/billing/*` |
| AWS S3 uploads | vault is the brain (`/api/aria/vault`) | strip |
| **shadcn/ui + Tailwind + charts + layout** | currently vanilla CSS + jQuery | **adopt — the prize** |
| single-tier dashboard | need customer / support / admin | **build multi-panel ourselves** |

## 3. Role & panel model (3 tiers)

Backend role enum (R-F2170): `role ∈ { customer, support, admin }` (replaces the binary `user`/`admin`). Billing **tier** (`free`/`pro`/`proIntel`) stays orthogonal and only gates customer features.

| Tier | Role | Panel | Scope |
|---|---|---|---|
| Paying business + individual | `customer` | **Customer** | own dashboard, DD/reports, vault, watchlist, opportunities, ARIA chat, account + billing. Features gated by Stripe tier. |
| Customer support | `support` | **Support console** | read customer accounts, usage, tickets; **audited "view-as"**; no destructive/admin writes. |
| Coders + designers | `admin` | **Main admin** | everything: brain, gaps/self-coding, content/design, feature flags, user mgmt, deploy/build visibility, system status. |

Server enforcement: `requireRole('admin')`, `requireRole('support','admin')`, etc. replacing today's binary `requireAdmin`. Client gating in `aria-app` middleware mirrors it (server stays the source of truth).

## 4. Target structure

```
aria-app/                      # new fly app: aria-app
  app/
    signin/page.tsx            # login → POST /api/auth/login, store JWT (httpOnly cookie)
    (customer)/                # role: customer
      layout.tsx               # customer shell (sidebar/nav)
      dashboard/ account/ billing/ reports/ vault/ watchlist/ chat/ ...
    (support)/                 # role: support|admin
      layout.tsx
      support/ accounts/ tickets/ ...
    (admin)/                   # role: admin
      layout.tsx
      admin/ brain/ gaps/ design/ flags/ users/ status/ ...
  middleware.ts                # JWT verify + role-gate per route group
  lib/  auth.ts  api.ts  utils.ts
  components/ ui/(shadcn)  app-sidebar.tsx  ...
  Dockerfile.app  fly.app.toml
server.mjs                     # UNCHANGED backend (+ role enum/requireRole, R-F2170)
```

## 5. Phasing (de-risked, page-by-page, live site never breaks)

- **P0 — Foundation (R-F2169/R-F2170):** scaffold `aria-app`; auth bridge (login → JWT cookie); `middleware.ts` role gating; one working page per panel calling a real API; backend role enum + `requireRole` + tests. *(this session)*
- **P1 — Customer panel:** migrate dashboard, reports, vault, watchlist, opportunities, account/billing, ARIA chat to shadcn. Tier-gated features.
- **P2 — Admin panel:** brain, gaps/self-coding view, system status, user mgmt, feature flags, design controls.
- **P3 — Support console:** accounts/usage/tickets + audited view-as.
- **P4 — Cutover:** route `intel.arkmurus.com` authenticated paths to `aria-app`; retire static `public/*.html` page-by-page; keep marketing/legal until last.

## 6. Deploy model

- New fly app `aria-app` (lhr), `Dockerfile.app` (multi-stage `next build` → `next start`), `fly.app.toml`. No volume (stateless; state lives in `server.mjs` + brain).
- During migration both run in parallel; reverse-proxy/cutover per path. Standalone Next output to keep the image small.
- §21 wiring: any new/changed Node API path emits success **and** failure to the brain (`/api/aria/brain/signal` or local ledger) — no dark paths.

## 7. Open follow-ups / decisions

- Business-vs-individual: same customer panel for now (tier gates org features); split later if needed.
- `view-as` impersonation for support = audited, read-only, hash-chained (reuse guardian audit pattern).
- Token transport: httpOnly cookie for `aria-app` SSR + Bearer for direct API — bridge in `lib/auth.ts`.
