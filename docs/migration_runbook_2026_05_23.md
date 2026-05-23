# Seenode → Fly migration runbook (R-F832 – R-F835)

**Plan reference**: `docs/fly_consolidation_plan_2026_05_23.md`
**Why**: operator 2026-05-23 — "too many servers is the reason why
the app is having so many glitches".

This runbook is the step-by-step the operator follows. Each step has
a checkbox; check them as they complete. The plan doc has the strategy;
this doc has the keystrokes.

---

## Pre-flight (do FIRST, before any deploy)

- [ ] **Export Seenode env to a local file**
  - Seenode dashboard → app settings → Environment Variables → copy or
    download as `.env`-style file (`KEY=VALUE` per line).
  - Save to e.g. `C:\tmp\seenode.env`. Do not commit this file.
- [ ] **Audit `.env7`** (audit W6)
  ```powershell
  git log --all --full-history -- .env7
  ```
  If anything found, treat any secret that EVER lived there as
  compromised. Rotate via the relevant provider (Stripe, Telegram,
  etc.) before R-F834 cutover. List of secrets that need rotation
  goes into a separate `[[secrets_rotation_2026_05_23]]` memory note.
- [ ] **Confirm DNS provider** for `intel.arkmurus.com`
  - Note registrar + nameservers — you'll need to update the A/CNAME
    in R-F834. Lower the TTL to 60s **24 hours before** the cutover.

---

## R-F832 — `aria-web` Fly app (~30-45 min once secrets ready)

### Setup

- [ ] Create the app:
  ```powershell
  flyctl apps create aria-web --org personal
  ```
- [ ] Create the persistent volume (1GB for users.json + sessions + audit logs):
  ```powershell
  flyctl volumes create aria_web_data --region lhr --size 1 -a aria-web --yes
  ```

### Secrets

- [ ] **Dry-run** the secret import (no push, just reports the routing):
  ```powershell
  bash scripts/admin/import_seenode_secrets.sh C:\tmp\seenode.env
  ```
  - Review the **unrouted keys** report — any unknown key needs a manual
    decision (does it belong on aria-web, aria-wa, neither?).
  - If any unrouted key matters, edit `scripts/admin/import_seenode_secrets.sh`
    to add it to `WEB_KEYS` / `WA_ONLY_KEYS` / `SHARED_KEYS`, commit, re-dry-run.
- [ ] **Apply** the secrets:
  ```powershell
  $env:FLYIO_APPLY = "1"
  bash scripts/admin/import_seenode_secrets.sh C:\tmp\seenode.env
  ```
  This sets secrets on BOTH `aria-web` and `aria-wa` in one pass.

### Deploy

- [ ] Build + deploy (image build takes 5-10 min first time):
  ```powershell
  flyctl deploy --config fly.web.toml --strategy rolling
  ```
- [ ] Verify boot health:
  ```powershell
  curl https://aria-web.fly.dev/healthz
  # Expected: HTTP 200 + JSON body
  ```
- [ ] Smoke-test critical paths (use a browser, not curl, so cookies set):
  - [ ] `https://aria-web.fly.dev/signin.html` loads + login works
  - [ ] `https://aria-web.fly.dev/aria.html` loads + chat with ARIA works
  - [ ] `https://aria-web.fly.dev/admin.html` loads + visible to admin user
  - [ ] Stripe webhook test — Stripe dashboard → Developers → Webhooks
        → temporarily add `https://aria-web.fly.dev/api/billing/webhook`
        → fire a test event → confirm signature verification succeeds
- [ ] **DO NOT yet point `intel.arkmurus.com` here** — that's R-F834.

---

## R-F833 — `aria-wa` Fly app (~20-30 min, requires WhatsApp QR scan)

### Setup

- [ ] Create the app:
  ```powershell
  flyctl apps create aria-wa --org personal
  ```
- [ ] Create the WA-auth volume (1GB for Baileys session blob):
  ```powershell
  flyctl volumes create aria_wa_data --region lhr --size 1 -a aria-wa --yes
  ```
- [ ] Secrets already set by `import_seenode_secrets.sh` (the `WA_ONLY_KEYS`
      + `SHARED_KEYS` arrays in the script handle this in one pass with R-F832).
      Verify:
  ```powershell
  flyctl secrets list -a aria-wa
  # Expected at minimum: ARIA_SERVICE_URL, ARIA_INTERNAL_TOKEN
  ```

### Deploy + QR scan

- [ ] Deploy:
  ```powershell
  flyctl deploy --config fly.wa.toml --strategy immediate --ha=false
  ```
- [ ] **Open logs and wait for the QR code:**
  ```powershell
  flyctl logs -a aria-wa
  # Wait for an ASCII QR block in the log output (~30-60s after boot)
  ```
- [ ] **On your phone:** WhatsApp → Settings → Linked Devices → "Link a Device"
      → scan the QR from the logs.
- [ ] Verify the link:
  ```powershell
  flyctl logs -a aria-wa | findstr /R "open Connection.*open\|peer_authenticated"
  ```
- [ ] Send a test WA message to the listener's group → confirm aria-intel
      receives it (check `flyctl logs -a aria-intel` for the inbound POST).

### Stop the seenode WA listener (avoid two devices fighting for the same
account)

- [ ] On Seenode dashboard, set env var `WHATSAPP_DISABLED=1` and restart.
      The lib/whatsapp/waListener.mjs path will skip startup. Seenode keeps
      serving HTTP traffic (still primary for now) but no longer attempts
      WA reconnect.

---

## R-F834 — DNS cutover (~5 min + 48h observation)

- [ ] **24 hours before cutover**: set TTL on the
      `intel.arkmurus.com` DNS record to **60 seconds**. Wait 24h for
      caches to expire.
- [ ] **Cutover moment** (pick a quiet window):
  ```
  # In your DNS provider (CNAME or A record):
  intel.arkmurus.com  →  aria-web.fly.dev  (or one of its IPs from
                                            `flyctl ips list -a aria-web`)
  ```
- [ ] **Stripe webhook URL update** — Stripe dashboard → Developers →
      Webhooks → edit the existing endpoint:
  - Old: `https://intel.arkmurus.com/api/billing/webhook` (resolved to seenode)
  - New: `https://intel.arkmurus.com/api/billing/webhook` (now resolves to aria-web)
  - **If the URL is identical** (DNS swap is transparent), no Stripe-side change is needed. Just verify signature still passes in the next webhook event.
- [ ] **Open the watch dashboard** for 48h:
  ```powershell
  flyctl logs -a aria-web | findstr /V "GET /healthz"
  flyctl logs -a aria-wa
  flyctl logs -a aria-intel
  ```
- [ ] **Smoke-test live**:
  - [ ] Open `https://intel.arkmurus.com` — should land on Fly aria-web
  - [ ] Log in as your admin account
  - [ ] Send a WA message — should reach aria-intel via aria-wa
  - [ ] Trigger a paid Stripe event in test mode — should hit aria-web's webhook

### Rollback (if anything goes sideways)

- [ ] Revert the DNS CNAME to point back at seenode (still running).
      60s TTL means rollback completes inside a minute.
- [ ] Investigate from `flyctl logs` — common issues:
  - Missing secret → `aria-web` boots but throws at first use
  - WA reconnect storm → check both seenode + aria-wa aren't both
    fighting for the WhatsApp link
  - Stripe webhook signature failures → confirm `STRIPE_WEBHOOK_SECRET`
    was migrated correctly

---

## R-F835 — Seenode decommission (after 48h clean obs)

- [ ] Open Seenode dashboard.
- [ ] **Take a final backup**: download `users.json`, `sessions.json`, any
      operator-data files. Stash in a local archive folder dated 2026-05-XX.
- [ ] Cancel the subscription via Seenode console.
- [ ] Update `[[seenode_is_proxy_to_fly]]` memory entry:
      "Seenode decommissioned 2026-XX-XX after R-F835."
- [ ] Update CLAUDE.md §17 architecture note.
- [ ] Optional: remove `SEENODE_BASE_URL` from aria-intel secrets:
  ```powershell
  flyctl secrets unset SEENODE_BASE_URL -a aria-intel
  ```
  (aria-wa now serves the same role at `aria-wa.internal:5070`.)

---

## What about Stripe billing during the migration window?

Between R-F832 deploy and R-F834 cutover, BOTH Seenode and aria-web are
serving the codebase but only Seenode is at `intel.arkmurus.com`. The
Stripe webhook is configured against `intel.arkmurus.com` so events land
on Seenode. No customer impact during the prep phase.

The risk window is the **DNS cutover moment** in R-F834 — for the few
seconds when DNS is mid-propagation, a webhook might race. Stripe retries
failed webhooks for up to 3 days with exponential backoff, so even a
missed event recovers automatically.

---

## What about live WhatsApp conversations during migration?

Until R-F833 step "Stop the seenode WA listener", both Seenode and
aria-wa are scanning the same WhatsApp account. Baileys will fight for
the linked-device slot — one of them will get the connection, the
other will drop. This is normal multi-device behaviour but expect 1-2
WA reconnects during the day of R-F833.

Once `WHATSAPP_DISABLED=1` is set on Seenode, only aria-wa holds the
session. No further reconnect storms.

---

## Cost during the migration window

| Period | Cost |
|---|---|
| R-F832 deploy through R-F835 (~2-7 days) | Seenode bill + aria-web (~£4-6/mo) + aria-wa (~£3-5/mo) — running both during obs |
| After R-F835 | aria-web + aria-wa only (~£7-11/mo) — Seenode cancelled |

The double-billing during the observation window is ~£10-15 for one week of overlap. Worth it for the safety net.
