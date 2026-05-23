# aria-web secrets shopping list (R-F832)

What to paste into a `.env` file for `import_seenode_secrets.sh`. Save
locally — NEVER commit this file (already in `.gitignore` via `.env*`).

Format: `KEY=value` per line.

---

## Tier 1 — CRITICAL (aria-web won't fully boot without these)

### Auth/sessions

| Key | Where to find | Notes |
|---|---|---|
| `ADMIN_EMAIL` | Seenode env OR your operator email | The email used to log into the admin panel |
| `ADMIN_PASSWORD` | Seenode env OR pick a new strong one (≥12 chars) | First-boot admin password — change after login |

The two values you give me set the operator-admin account on first boot.
`ARIA_API_TOKEN`, `ARIA_INTERNAL_TOKEN`, and `JWT_SECRET` I'll handle:
copy from aria-intel + generate fresh respectively.

---

## Tier 2 — REQUIRED for paying customers (billing dies without these)

### Stripe

| Key | Where to find |
|---|---|
| `STRIPE_SECRET_KEY` | Stripe Dashboard → Developers → API keys → Secret key (live) |
| `STRIPE_WEBHOOK_SECRET` | Stripe Dashboard → Developers → Webhooks → your endpoint → Signing secret (`whsec_...`) |
| `STRIPE_CHECKOUT_RETURN_URL` | e.g. `https://intel.arkmurus.com/billing/success` |
| `STRIPE_CHECKOUT_CANCEL_URL` | e.g. `https://intel.arkmurus.com/billing/cancel` |
| `STRIPE_PORTAL_RETURN_URL` | e.g. `https://intel.arkmurus.com/account` |

---

## Tier 3 — REQUIRED for operator messaging surfaces

### Telegram (for the `/aria` Telegram bot + alerts)

| Key | Where to find |
|---|---|
| `TELEGRAM_BOT_TOKEN` | `@BotFather` on Telegram → /mybots → API token |
| `TELEGRAM_WEBHOOK_SECRET` | You pick — any 32+ random chars |
| `TELEGRAM_ADMIN_CHAT_ID` | Your numeric Telegram user/chat ID |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated user IDs that can issue commands |
| `TELEGRAM_CHANNELS` | Comma-separated channels to monitor (or blank) |
| `TELEGRAM_CHAT_ID` | Default chat for alerts (group or DM) |

### Outbound email (audit W7 — split from inbound `ARIA_EMAIL_*`)

| Key | Where to find |
|---|---|
| `EMAIL_HOST` | Your SMTP provider host (e.g. `smtp.sendgrid.net`, `smtp.gmail.com`) |
| `EMAIL_PORT` | 587 (TLS) or 465 (SSL) or 25 |
| `EMAIL_SECURE` | `true` for 465, `false` for 587 |
| `EMAIL_USER` | SMTP username |
| `EMAIL_PASS` | SMTP password / API key |
| `EMAIL_FROM` | `Name <noreply@arkmurus.com>` |

If you don't want a separate outbound mailbox, **leave these unset** and
the code falls back to `ARIA_EMAIL_*` (the inbound creds). Less clean
but works.

---

## Tier 4 — REQUIRED for intel features

### External data APIs (each enables one source/feed)

| Key | Service | Sign-up |
|---|---|---|
| `COMPANIES_HOUSE_API_KEY` | UK Companies House | https://developer.company-information.service.gov.uk |
| `ACLED_EMAIL` | ACLED conflict data | https://acleddata.com (Phase A gate #5) |
| `ACLED_PASSWORD` | ACLED | same site, Phase A gate #5 |
| `BLS_API_KEY` | US Bureau of Labor Statistics | https://www.bls.gov/developers/ |
| `COMTRADE_API_KEY` | UN Comtrade | https://comtradedeveloper.un.org |
| `EIA_API_KEY` | US Energy Info Admin | https://www.eia.gov/opendata/ |
| `ADSB_API_KEY` | ADS-B Exchange / Flightradar | provider-specific |
| `AISSTREAM_API_KEY` | AIS Stream (maritime) | https://aisstream.io |
| `CLOUDFLARE_API_TOKEN` | Cloudflare (cert transparency, DNS) | https://dash.cloudflare.com → API Tokens |
| `CODEX_ACCESS_TOKEN` | Codex (corporate registry — operator-specific) | from your provider |
| `CODEX_ACCOUNT_ID` | Codex account id | same |

If any are missing the corresponding feature degrades gracefully
(returns "feature requires `<KEY>`" instead of crashing).

---

## Tier 5 — OPTIONAL features

### Web push notifications

| Key | How to get |
|---|---|
| `VAPID_PUBLIC_KEY` | `npx web-push generate-vapid-keys` → outputs both |
| `VAPID_PRIVATE_KEY` | (from same command) |
| `VAPID_SUBJECT` | `mailto:operator@arkmurus.com` |

Without these, the web-push routes return 503; everything else works.
I can run `web-push generate-vapid-keys` for you if you want — just
say so.

### Dashboard basic auth (legacy — not used in current chat UI)

| Key | Notes |
|---|---|
| `DASHBOARD_USER` | Optional legacy dashboard auth — leave blank to disable |
| `DASHBOARD_PASS` | Optional |

### Inbound mail bridge (currently ARIA_SMTP_* on aria-intel)

`server.mjs` reads `ARIA_EMAIL_*` for the inbound bridge, but
aria-intel has them as `ARIA_SMTP_*`. Pick one path:

**Path A (cleaner)** — rename the convention:
```
ARIA_EMAIL_HOST=<copy from ARIA_SMTP_HOST>
ARIA_EMAIL_USER=<copy from ARIA_SMTP_USER>
ARIA_EMAIL_PASS=<copy from ARIA_SMTP_PASS>
ARIA_EMAIL_PORT=<copy from ARIA_SMTP_PORT>
ARIA_EMAIL_FROM=<your inbound mailbox display>
ARIA_EMAIL_ENABLED=1
ARIA_EMAIL_POLL_MS=60000
ARIA_EMAIL_BACKFILL_COUNT=20
```

**Path B (lazy)** — set `ARIA_EMAIL_HOST` etc. equal to the existing
ARIA_SMTP_* values. Same result.

### WhatsApp behavior tuning (only matters if these features used)

| Key | Notes |
|---|---|
| `ARIA_MIRROR_GROUPS` | WA group JIDs to mirror (comma-separated) — blank disables |
| `ARIA_MIRROR_MIN_LEN` | Min chars to mirror (default 40) |
| `ARIA_DECEPTION_THRESHOLD` | Float 0-1 (e.g. 0.75) for deception alerts |
| `ARIA_COUNTERPARTY_CONTACTS` | Comma-separated phone numbers |
| `ARIA_WA_MAX_PROBES_WITHOUT_PEER` | Connection-health knob |

### Alerts + webhooks

| Key | Notes |
|---|---|
| `ARIA_SLACK_WEBHOOK` | Slack incoming webhook URL (or blank) |
| `ARIA_WEBHOOK_URLS` | Comma-separated webhook URLs for alert fan-out |
| `ARIA_TEAM_EMAILS` | Internal team for outbound alerts |
| `COMPLIANCE_TEAM_EMAILS` | Compliance/legal team |

### App config

| Key | Default if unset |
|---|---|
| `APP_URL` | `https://intel.arkmurus.com` (will work post-DNS-cutover; pre-cutover use `https://aria-web.fly.dev`) |
| `ARIA_VERSION_LABEL` | cosmetic, e.g. "v2.0.3" |
| `ARIA_TONE` | LLM tone hint, e.g. "professional" |
| `CRUCIX_LANG` | "en" |
| `ARIA_PROXY_TIMEOUT_MS` | 45000 |
| `ARIA_CHAT_PROXY_TIMEOUT_MS` | 120000 |
| `ARIA_DD_PROXY_TIMEOUT_MS` | 300000 |
| `ARIA_STREAM_PROXY_TIMEOUT_MS` | 120000 |

---

## What I handle WITHOUT operator input

These I'll set automatically — no action needed:

- `ARIA_API_TOKEN` — extracted from aria-intel via `flyctl ssh`
- `ARIA_INTERNAL_TOKEN` — extracted from aria-intel
- `ARIA_AUDIT_SIGNING_KEY` — extracted from aria-intel
- `REPORT_SIGNING_KEY` — extracted from aria-intel
- `AIRTABLE_PAT` — extracted from aria-intel
- `ARIA_SMTP_HOST/USER/PASS/PORT` — extracted from aria-intel
- `JWT_SECRET` — freshly generated (64 random bytes hex)
- `ADMIN_RECOVERY_TOKEN` — freshly generated (48 random bytes hex)
- `ARIA_SERVICE_URL` — hardcoded `http://aria-intel.internal:8000`
- `ARIA_BRAIN_URL` — same
- `BRAIN_URL` / `BRAIN_SERVICE_URL` / `BRAIN_DIRECT_URL` — same
- `ARIA_INGEST_URL` — `http://aria-intel.internal:8000/api/aria/ingest`
- `ARIA_FLY_URL` — same
- `NODE_ENV=production`

---

## How to use this list

**Path 1 (fastest)** — paste your Seenode env block here in chat, I'll
parse + set everything in one go. You skip the file step. **Caveat**:
secret values land in chat transcript.

**Path 2 (safer)** — Seenode dashboard → settings → Environment Variables
→ download/copy as `.env`-style file → save to e.g. `C:\tmp\seenode.env`
→ tell me the path. I'll run:
```powershell
bash scripts/admin/import_seenode_secrets.sh C:\tmp\seenode.env
$env:FLYIO_APPLY = "1"
bash scripts/admin/import_seenode_secrets.sh C:\tmp\seenode.env
```
Secrets values never leave your machine + the Fly API.

**Path 3 (manual)** — for any subset of Tier 2-5 you want to fill in
manually (e.g. only Stripe + Telegram), you just give me those values
and the rest stay unset (features degrade gracefully).

**My recommendation: Path 2.** Path 1 is fine if you trust the chat
transcript security but it's avoidable.

---

## Bare minimum to deploy aria-web TONIGHT

If you want a partially-functional aria-web up now and add the rest
later:

- `ADMIN_EMAIL` (your operator email)
- `ADMIN_PASSWORD` (any ≥12 char string; change after first login)

Everything else: I'll bootstrap from aria-intel + generate.

This gives you a working **chat UI + login + admin panel + proxy
to aria-intel**. Billing routes return 503 until you add `STRIPE_*`.
Telegram bot dormant until `TELEGRAM_*` added. External-data
features dormant until their keys added. Operator can add the
rest incrementally without further rebuilds (just `flyctl secrets set`).
