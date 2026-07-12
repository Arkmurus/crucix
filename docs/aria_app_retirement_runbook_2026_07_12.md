# aria-app Retirement Runbook — collapse to a single web tier

**Date:** 2026-07-12
**Status:** PLAN — awaiting operator go (outward-facing, touches the live public domain)
**R-number:** reserve at execution time (e.g. R-Fxxxx `retire aria-app; cut public domain to aria-web`)

## Why (USP-focused)

ARIA's USP lives entirely in **aria-intel** (honesty layers, sovereign model,
verification, 360° intelligence). The web tier's only job is to deliver that
honestly and reliably. Today the delivery path has a redundant hop:

```
public → aria-app (Next.js shell, stale Jun-30 image, ROLLBACK MODE)
       → transparent proxy → aria-web (node server.mjs, the ACTUAL platform)
       → aria-intel
```

`aria-app` serves **nothing of its own in production** — verified 2026-07-12:
`aria-web.fly.dev/`, `/aria-brain`, `/api/billing/config` all return 200, i.e.
aria-web is a complete standalone platform. `aria-app` only proxies everything
(except `/preview/*` + `/_next/*`) back to it (`next.config.mjs:26-27`).

Retiring `aria-app` removes: a network hop (latency + a failure surface), a
forgotten stale app that caused this very session's "is node retired?" confusion,
and a $2/mo dedicated IP — with **zero production loss**. The Next.js code stays
in the repo for a *deliberate* future migration if that ever becomes a priority.

> NOTE: the original trigger (403→503 masking) is ALREADY FIXED in aria-web
> (R-F2579, live). So this retirement is now **simplification/robustness, not a
> bug-fix** — schedule it deliberately; it is not urgent.

## Verified current state (2026-07-12)

| Fact | Evidence |
| --- | --- |
| Public certs on **aria-app** | `flyctl certs list -a aria-app` → intel.arkmurus.com, imaria.io (Issued), www.imaria.io (Not verified) |
| aria-web has NO public certs | `flyctl certs list -a aria-web` → empty |
| aria-app dedicated IPv4 (apex target) | `149.248.216.35` ($2/mo); v6 `2a09:8280:1::13a:22fc:1` |
| aria-web IPs | v6 dedicated `2a09:8280:1::11a:be8:0`; **shared v4 only** `66.241.124.39` (NO dedicated v4) |
| aria-web serves full platform | `aria-web.fly.dev/` `/aria-brain` `/api/billing/config` → 200 |
| DNS registrar | GoDaddy (operator-managed; Claude has no access) |

## Ownership split
- **Claude / flyctl** (operator-authed locally): cert add/remove, IP alloc, app destroy.
- **Operator / GoDaddy**: the DNS record changes (apex A/AAAA + CNAME). Claude cannot do this.

## Cutover sequence (ZERO-DOWNTIME, reversible)

Do NOT destroy aria-app until the domain is proven on aria-web and a rollback
window has passed. Each step has a verify gate.

**0. Pre-flight (no traffic impact)**
- Snapshot: `flyctl certs list`, `flyctl ips list`, `flyctl status` for BOTH apps → save output.
- Confirm aria-web healthy + serving (already true) and on the current node build.
- Announce a short maintenance window (DNS TTL-dependent).

**1. IP — DONE, no new IP needed (Claude, 2026-07-12)**
- Fly routes aria-web on its existing **shared v4 `66.241.124.39`** + **dedicated
  v6 `2a09:8280:1::11a:be8:0`**. No dedicated-v4 allocation required → **no $2/mo
  charge.** (Shared v4 routes by SNI/Host; aria-web now holds the certs, so the
  hostname resolves to aria-web once DNS points at these IPs.)

**2. Certs on aria-web — DONE (Claude, 2026-07-12)**
- `flyctl certs add {intel.arkmurus.com, imaria.io, www.imaria.io} -a aria-web`
  → all three **created** (awaiting DNS validation). aria-app keeps serving until
  DNS moves, so this was non-disruptive.

**3. DNS cutover at GoDaddy (OPERATOR) — the exact changeset**
Set these records (delete the current ones pointing at aria-app's `149.248.216.35`):

| Host | Type | Value |
| --- | --- | --- |
| intel.arkmurus.com | A | 66.241.124.39 |
| intel.arkmurus.com | AAAA | 2a09:8280:1::11a:be8:0 |
| imaria.io (apex) | A | 66.241.124.39 |
| imaria.io (apex) | AAAA | 2a09:8280:1::11a:be8:0 |
| www.imaria.io | A | 66.241.124.39 |
| www.imaria.io | AAAA | 2a09:8280:1::11a:be8:0 |

- **Lower TTL to 300s a day BEFORE cutover** so rollback is fast.
- After setting, run `flyctl certs check <host> -a aria-web` until each is Issued.

**4. Verify on aria-web (Claude) — hard gate**
- `flyctl certs show <host> -a aria-web` → Issued.
- `curl -sI https://intel.arkmurus.com/api/health` → build_rev == aria-web's node build.
- Load-bearing paths return 200 and real content: landing `/`, `/aria-brain`
  (dashboard + panels honest, no false banner), auth/login, `/api/billing/config`
  (Stripe), Telegram webhook, WA proxy routes.
- Run the synthetic honesty monitor (once built) against the public domain.

**5. Rollback window (24–48h)**
- Keep aria-app RUNNING and its cert/IP intact.
- If anything breaks: revert the GoDaddy records to aria-app's IPs (fast at 300s TTL).

**6. Decommission aria-app (Claude, only after a clean window)**
- `flyctl certs remove <host> -a aria-app` (each host).
- `flyctl ips release 149.248.216.35 -a aria-app` + the v6 (stop the $2/mo).
- `flyctl apps destroy aria-app` (Next.js code remains in the repo).
- Update CLAUDE.md §16 fly inventory: web tier = aria-web only; note aria-app retired.

## Rollback (any step before 6)
DNS revert to aria-app's IPs. aria-app stays fully functional until step 6, so
rollback is a single DNS change. Nothing is destroyed until the domain is proven
on aria-web.

## Post-retirement hardening (the durable win)
- Status-preservation contract-test (generalize R-F2579): CI fails if any
  proxy/fetch site remaps a real upstream status to a generic error.
- Synthetic honesty monitor (§25 for the dashboard): scheduled dual-token probe
  asserting operator-only panels read "auth-gated", nothing reads "offline" while
  aria-intel `/health` is green.
- Update the auto-generated live topology map so docs never drift from reality again.
