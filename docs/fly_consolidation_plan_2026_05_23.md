# Fly.io single-control-plane consolidation plan

**Reserved**: R-F828 → R-F835 (2026-05-23)
**Operator motivation (verbatim)**: "Too many servers is the reason why the app
is having so many glitches so lets plan it all accordingly."
**Target outcome**: One control plane (Fly), Seenode decommissioned,
clean WhatsApp isolation, audit P0/P1 security findings closed in flight.

---

## Why now

Two parallel audits delivered 2026-05-23 (one fly.io-side, one seenode-side)
together identify **18 P0 + 21 P1 findings**. Many root-cause to *split
infrastructure*:

- `lib/whatsapp/waListener.mjs` (in seenode `server.mjs`) AND
  `services/wa-listener/aria_wa_listener.mjs` (standalone) — split-brain
  WA implementations; security/feature fixes in one don't propagate.
- Seenode ephemeral disk + dual auth surface (`users.json` on seenode,
  bearer tokens on fly) — every deploy risks user-data corruption (W18).
- Two log surfaces, two deploy pipelines, two auth systems = combinatorial
  failure modes.
- 9 P0 security findings on seenode that would each be fixed *naturally*
  by a fresh Fly-side rebuild (env-check leak, localhost bypass, CORS
  wildcard, JWT version-check gap, shared inbound-mail credentials).

Consolidating to a single Fly control plane eliminates the entire class
of cross-service glitches. The cost is ~12-16 hours of careful work over
2-3 weeks; the win is permanent.

---

## Current state (4 services)

| Service | Role | Status |
|---|---|---|
| Fly `aria-intel` | Python brain (chat, DD, ARIA-Coder, harvest) | **LIVE** |
| Seenode `intel.arkmurus.com` | Node web UI, auth, WA listener, Telegram, billing, proxy to fly | **LIVE** |
| RunPod (sporadic) | A100 80GB GPU for ARIA-LLM fine-tune | **LIVE** (training-only, spun down between runs) |
| Fly `aria-trainer` | Built for Fly-GPU training before Fly GPU was deprecated | **DEAD** (deleting in Phase 0) |

## Target state (2 always-on services + 1 burst)

| Service | Role |
|---|---|
| Fly `aria-intel` | Python brain — **unchanged** |
| Fly `aria-web` (NEW) | Node `server.mjs` minus WA listener — UI + auth + billing + Telegram + static + `/api/aria/*` proxy to `aria-intel.internal` |
| Fly `aria-wa` (NEW) | Baileys WA listener in isolation — separate Fly app so a WA crash never takes down web/auth |
| RunPod (sporadic) | Same — A100 for training cycles, otherwise stopped |

**Net change**: 3 always-on services, all on Fly. RunPod = burst-only
(stopped 99% of the time). Down from "Fly + Seenode + RunPod" to
"Fly + RunPod-on-demand".

---

## Phases

### Phase 0 — finish in-flight work (tonight, ~30 min after training lands)

| Step | Action | Auto/operator |
|---|---|---|
| 0.1 | Wait for RunPod training to finish (~10 min from plan write) | auto-monitor |
| 0.2 | Verify adapter at `/workspace/checkpoints/aria_llm_v0_1_sft/` | auto |
| 0.3 | Stop RunPod pod via API (cost meter ends) | auto |
| 0.4 | Delete Fly `aria-trainer` app + 100GB volume (Fly GPU deprecated → orphaned) | auto |
| 0.5 | Update `[[aria_coder_buildout_2026_05_22]]` + CLAUDE.md to reflect RunPod-only training | auto |
| 0.6 | `.gitignore` corpus tarballs at repo root (`corpus*.tar.gz`, `corpus.b64`) — audit W25 | auto |

**Success criteria**: aria-trainer Fly app removed; RunPod pod stopped;
docs reflect new architecture; no untracked corpus files in repo root.

### Phase 1 — Seenode security quick wins (when operator says go, ~30 min total)

Each is a small focused commit. Seenode auto-deploys, so each ships
immediately on push. Order chosen so a regression in any one is easy
to revert without affecting the others.

| R | Audit ref | Scope | Effort | Risk |
|---|---|---|---|---|
| **R-F828** | W1 | Add `requireAdmin` middleware to `GET /api/admin/env-check` (currently unauthenticated, leaks token fingerprints) | 5 min | LOW — one line + the middleware already exists at server.mjs:3414 |
| **R-F829** | W4 | Socket.io `cors.origin: '*'` → allowlist `['https://intel.arkmurus.com', 'https://aria-intel.fly.dev']` | 10 min | LOW — explicit origin list |
| **R-F830** | W18 | `lib/persist/store.mjs`: if existing file is non-empty but parsed-as-array is empty, **refuse to write** + log halt + page operator. Prevents silent user-list wipe on corrupt JSON. | 15 min | LOW — defensive, fail-closed |
| **R-F831** | W3 + W2 | Tighten localhost auth bypass + enforce `TELEGRAM_WEBHOOK_SECRET` at boot (was warn-and-continue) | 20 min | LOW — env-driven |

**Operator action items** (no commit involved):
- **W6**: `git log --all --full-history -- .env7` to inventory what ever lived in that file. Rotate any secrets that show up. Cannot be done by me — I don't know what `.env7` contained or which fly secrets need rotating.
- **W7**: Set distinct `EMAIL_HOST` / `EMAIL_USER` / `EMAIL_PASS` (currently falls back to `ARIA_EMAIL_*` — same mailbox the inbound bridge reads; compromise = password-reset oracle).

**Success criteria**: 4 of 9 P0 audit findings closed; operator confirms `.env7` audit done + EMAIL_* split.

### Phase 2 — Fly migration (~12-16h sequenced over 2-3 sessions)

**R-F832 — `aria-web` Fly app (6-8h)**
- Create `aria-web` Fly app + volume for `users.json` / `sessions.json` / `runs/` persistence (no more ephemeral disk).
- Author `Dockerfile.web`: Node 22 + npm install + COPY `server.mjs lib/ apis/ middleware/ public/`. Skip `services/wa-listener/` (that's R-F833) and `frontend/dist/` (audit W10 — dead Angular SPA, delete).
- Author `fly.web.toml`: `[[http_service]]` on 3000, healthcheck `/healthz` (Node side), 1GB volume mounted at `/data` for `users.json` etc.
- Set secrets on `aria-web`:
  - `ARIA_SERVICE_URL=http://aria-intel.internal:8000` (Fly private network — no public hop)
  - `ARIA_API_TOKEN`, `ARIA_INTERNAL_TOKEN`, `JWT_SECRET` — copy from seenode env
  - `STRIPE_SECRET_KEY` + `STRIPE_WEBHOOK_SECRET` — copy
  - `ADMIN_EMAIL` + `ADMIN_PASSWORD` (≥12 chars per `[[seenode_disk_ephemeral]]`)
  - `EMAIL_HOST`/`EMAIL_USER`/`EMAIL_PASS` (the new split-creds from Phase 1 operator action)
  - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`
- Deploy on `aria-web.fly.dev` — parallel to seenode, no traffic yet.
- Fold in audit fixes naturally: W9 (consolidate two `verifyToken` impls), W10 (delete Angular SPA dist, -31 MB), W22-23 (delete dormant `lib/` modules + `dashboard/public/*`), W27 (delete 6 stale signin variants).

**Success criteria**: `aria-web.fly.dev/healthz` returns 200; login flow works against the new app; Stripe webhook signature verifies; Telegram bot responds when pointed at `aria-web.fly.dev/webhook`.

**R-F833 — `aria-wa` Fly app (3-4h)**
- Create `aria-wa` Fly app + 1GB volume for Baileys session auth.
- Use the **standalone** `services/wa-listener/aria_wa_listener.mjs` as the canonical implementation (per audit W15 recommendation). Delete the embedded `lib/whatsapp/waListener.mjs` from server.mjs.
- Author `Dockerfile.wa`: Node 22 + COPY `services/wa-listener/`.
- Set secrets:
  - `ARIA_SERVICE_URL=http://aria-intel.internal:8000`
  - `ARIA_INTERNAL_TOKEN` — for posting received messages back to aria-intel
  - `WHATSAPP_AUTH_DIR=/data/wa-auth` (persistent across deploys)
- **Operator action during deploy**: scan WhatsApp QR once to authenticate the new pod's session (existing seenode WA session can't migrate cleanly — Baileys auth blobs are device-bound).

**Success criteria**: `aria-wa` posts inbound WA messages to aria-intel; one round-trip test message confirms. Seenode WA listener still running in parallel (will be killed in R-F834).

**R-F834 — DNS cutover (30 min + 48h observation)**
- Update `intel.arkmurus.com` DNS A/CNAME record to point at `aria-web.fly.dev` (or `aria-web.fly.dev` IPv4 via Fly's `flyctl ips list`).
- Short TTL (60s) for fast rollback. Set 24h before cutover.
- Update Stripe webhook URL from `https://intel.arkmurus.com/api/billing/webhook` (no change if DNS swap is transparent) — confirm signature verifies post-cutover.
- Disable seenode WA listener (set `WHATSAPP_DISABLED=1`) so it stops fighting `aria-wa` for the same session.
- 48h observation: watch `flyctl logs -a aria-web` and `flyctl logs -a aria-wa` for any 5xx burst, auth failures, billing webhook misses, WA reconnect storms.

**Success criteria**: 48h with zero customer-reported issues; chat UI loads; login works; Stripe webhooks land; Telegram bot responds; WhatsApp throughput unchanged.

**R-F835 — Seenode decommission (5 min + cancel subscription)**
- Only after 48h clean observation under R-F834.
- Cancel Seenode subscription via their console.
- Update `[[seenode_is_proxy_to_fly]]` memory to "Seenode decommissioned 2026-XX-XX after R-F835".
- Remove `SEENODE_BASE_URL` from `aria-intel` secrets (now `aria-web.internal:3000`).

**Success criteria**: Seenode subscription cancelled; no requests to `intel.arkmurus.com` traversing seenode; Fly cost panel reflects new equilibrium.

### Phase 3 — debt cleanup (folded into Phase 2 commits, R-F836+)

These audit findings can/should be addressed inside Phase 2 R-numbers
rather than as separate commits — they touch the same files being
rewritten anyway.

| Finding | Where it lands |
|---|---|
| W9 — two `verifyToken` impls | Inside R-F832 — consolidate to one (server.mjs is currently authoritative; align `lib/auth/users.mjs` to match) |
| W10 — Angular SPA dist | Inside R-F832 — delete `frontend/dist/` (-31 MB) |
| W11 — `dashboard/public/*` legacy | Inside R-F832 — delete tree |
| W15 — two WA listeners | Inside R-F833 — keep `services/wa-listener/`, delete `lib/whatsapp/waListener.mjs` |
| W22-23 — dormant `lib/` modules | Inside R-F832 — delete `entity-store.mjs`, `deep-engine.mjs`, `alerts/email.mjs`, etc. (operator confirms first) |
| W25 — corpus blobs at repo root | Phase 0.6 above |
| W27 — 6 stale signin HTML variants | Inside R-F832 — delete |
| W29 — Angular sourcemaps in prod build | N/A after W10 deletes the dist |

---

## Cost model

**Today**:
- Fly aria-intel: paying
- Seenode: paying (amount unknown to me — operator to check)
- RunPod: pay-per-use only during training

**After R-F835**:
- Fly aria-intel: same
- Fly aria-web (`shared-cpu-1x` 256MB): ~£4-7/mo
- Fly aria-wa (`shared-cpu-1x` 256MB + 1GB volume): ~£4-6/mo
- RunPod: same (pay-per-use)
- Seenode bill: **gone**

Net delta depends on what Seenode currently costs. If > £13/mo, migration
**saves money**. If less, the win is operational (one control plane, fewer
glitches, audit findings closed).

---

## Roles

**What I (Claude/ARIA-Coder) execute autonomously**:
- Phase 0 — entirely
- Writing Dockerfiles + fly.toml configs in R-F832 / R-F833
- Folding audit fixes into those commits
- Tests for all new code
- Verification that the new Fly apps boot + healthcheck

**What requires operator action (explicit click / paste / decision)**:
- Phase 1 — "ship it" approval before I touch `server.mjs` (live UI)
- W6 — `.env7` git-history audit + secret rotation
- W7 — set `EMAIL_*` env vars
- R-F832 — confirm dormant `lib/` modules can be deleted
- R-F833 — scan WhatsApp QR for the new pod
- R-F834 — DNS provider login + record update
- R-F835 — cancel Seenode subscription via their console

---

## Rollback plan per phase

| Phase | Rollback |
|---|---|
| Phase 0 | Aria-trainer Fly app deletion is one-way but it was orphaned anyway. RunPod pod can be re-launched from the same image. Memory + CLAUDE.md changes are git-revertable. |
| Phase 1 | Each R-number is a separate commit. `git revert <sha>` + push reverts a single fix without affecting others. Seenode re-deploys automatically. |
| R-F832 | aria-web is parallel to seenode. If it fails health checks, no customer impact — just don't proceed to R-F834. |
| R-F833 | aria-wa is parallel to seenode's embedded WA listener. If it fails, kill aria-wa machine, seenode keeps serving. |
| R-F834 | DNS rollback: set CNAME back to seenode within 60s TTL window. Existing fly + seenode both remain functional. |
| R-F835 | Reactivate Seenode subscription if it's still in their grace period (typically 30 days). |

---

## Open operator decisions (block specific R-numbers)

1. **Phase 1 timing** — ship 4 audit fixes immediately on existing seenode, or hold until Phase 2 starts (and fix them inside the aria-web rewrite)?
   - *Recommended: ship now*. 30 min of work closes 4 P0s today; not waiting on multi-week migration.

2. **R-F832 dormant-module deletion list** — operator confirms each before deletion: `lib/search/entity-store.mjs`, `lib/search/deep-engine.mjs`, `lib/alerts/email.mjs`, `lib/alerts/alert_evaluator.mjs`, `lib/alerts/alert_rules.mjs`, `lib/intel/source_registry_bootstrap.mjs`, `lib/aria/{contacts,approach,competitors,gtm_strategy}.mjs`, `lib/llm/ideas.mjs`. Audit found these have no runtime callers. Worth a 5-minute review before deletion.

3. **DNS provider** — what manages `intel.arkmurus.com`? Need to know to plan R-F834. Cloudflare? Route53? Registrar-default?

4. **Stripe webhook URL** — confirm whether Stripe is currently configured with `https://intel.arkmurus.com/api/billing/webhook` (DNS transparent) or a direct seenode hostname (would need explicit reconfig). Stripe dashboard → Developers → Webhooks.

5. **WhatsApp QR scan** — during R-F833, the new aria-wa pod will display a QR in `flyctl logs`. Operator scans with WhatsApp → Linked Devices. Plan a 10-minute window where you're available.

6. **48h observation window for R-F834** — pick a day where you can watch logs + respond if something glitches. Avoid weekends if Stripe webhook reliability is critical.

---

## Memory + tracking

- This plan is committed at `docs/fly_consolidation_plan_2026_05_23.md` so it survives session boundaries.
- R-F828→R-F835 reserved in `data/r_number_reservations.json` 2026-05-23.
- `[[fly_consolidation_plan]]` memory will link back to this doc.
- CLAUDE.md §17 will be updated to reference this plan when R-F834 ships
  (architecture change is material to every future session).

---

## Open questions only the operator can answer

1. Seenode monthly bill £?
2. `.env7` — what was in it? (W6 audit)
3. DNS provider for `intel.arkmurus.com`?
4. Customer-facing comms during 48h R-F834 window?
5. Phase 1 — ship tonight or wait?
6. Permission to delete dormant modules listed in #2 above?
