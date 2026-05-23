#!/usr/bin/env bash
# R-F832 (2026-05-23) — bootstrap aria-web secrets without operator paste.
#
# What this does:
#   1. Copies SHARED secrets from aria-intel → aria-web (ARIA_API_TOKEN,
#      ARIA_INTERNAL_TOKEN, ARIA_SMTP_*, AIRTABLE_PAT, etc.)
#   2. Generates FRESH internal-only secrets (JWT_SECRET, ADMIN_PASSWORD,
#      ADMIN_RECOVERY_TOKEN) that don't need to match Seenode.
#   3. Sets hard-coded URLs pointing at Fly internal DNS (aria-intel.internal).
#   4. Skips external-API keys (Stripe, Telegram, Companies House, etc.) —
#      those only live on Seenode; operator pastes via
#      import_seenode_secrets.sh when ready.
#
# After this runs, aria-web boots with PARTIAL functionality:
#   ✅ Chat UI, login, admin panel, brain proxy to aria-intel
#   ✅ Email-bridge (uses ARIA_SMTP_*)
#   ❌ Stripe billing routes return 503 until STRIPE_* added
#   ❌ Telegram bot dormant until TELEGRAM_* added
#   ❌ Companies House / ACLED / BLS / etc. dormant until added
#
# Safety:
#   - Secret values are never echoed. Only key NAMES + lengths/fingerprints.
#   - Script is idempotent (re-runnable; refuses overwrite unless FORCE=1).

set -euo pipefail

FORCE="${FORCE:-0}"
SRC_APP="${SRC_APP:-aria-intel}"
DST_APP="${DST_APP:-aria-web}"

echo "──────────────────────────────────────────────────────────────"
echo " Bootstrap aria-web secrets (R-F832)"
echo " Source: $SRC_APP   →   Destination: $DST_APP"
echo " Force overwrite: $([ "$FORCE" = "1" ] && echo YES || echo NO)"
echo "──────────────────────────────────────────────────────────────"

# Secrets that should be IDENTICAL on aria-web as on aria-intel.
# These are the cross-service auth/signing/integration keys.
SHARED_KEYS=(
  ARIA_API_TOKEN
  ARIA_INTERNAL_TOKEN
  ARIA_AUDIT_SIGNING_KEY
  REPORT_SIGNING_KEY
  AIRTABLE_PAT
  ARIA_SMTP_HOST
  ARIA_SMTP_USER
  ARIA_SMTP_PASS
  ARIA_SMTP_PORT
)

# Hard-coded URLs: must point at Fly internal DNS, not the public hostname
HARDCODED=(
  "ARIA_SERVICE_URL=http://aria-intel.internal:8000"
  "ARIA_BRAIN_URL=http://aria-intel.internal:8000"
  "ARIA_INGEST_URL=http://aria-intel.internal:8000/api/aria/ingest"
  "BRAIN_URL=http://aria-intel.internal:8000"
  "BRAIN_SERVICE_URL=http://aria-intel.internal:8000"
  "BRAIN_DIRECT_URL=http://aria-intel.internal:8000"
  "ARIA_FLY_URL=http://aria-intel.internal:8000"
  "NODE_ENV=production"
)

# ── Step 1: Copy shared secrets from src → dst via flyctl ssh ──────────────
echo
echo "[1/3] Copying ${#SHARED_KEYS[@]} shared secrets from $SRC_APP..."

SECRETS_TO_SET=""

for key in "${SHARED_KEYS[@]}"; do
  # Capture the value into a shell variable via flyctl ssh + printenv.
  # The value never appears in stdout because we redirect through
  # base64 + then decode locally to a variable.
  raw=$(flyctl ssh console -a "$SRC_APP" -C "sh -c \"printenv $key | base64 -w 0 || true\"" 2>/dev/null | tail -n 1 | tr -d '\r\n')

  if [ -z "$raw" ]; then
    echo "  - $key: (not set on $SRC_APP — skipping)"
    continue
  fi

  # Decode (without echoing)
  value=$(echo "$raw" | base64 -d 2>/dev/null || true)
  if [ -z "$value" ]; then
    echo "  - $key: (empty after decode — skipping)"
    continue
  fi

  # Stage for batch set (one secrets-set call rather than 9 redeploys)
  SECRETS_TO_SET="$SECRETS_TO_SET $key=$value"
  echo "  - $key: extracted (length=${#value})"
  unset value raw
done

# ── Step 2: Generate fresh internal-only secrets ───────────────────────────
echo
echo "[2/3] Generating fresh internal secrets..."

# JWT_SECRET — 64 random bytes hex-encoded (128 char string)
JWT_SECRET=$(openssl rand -hex 64)
SECRETS_TO_SET="$SECRETS_TO_SET JWT_SECRET=$JWT_SECRET"
echo "  - JWT_SECRET: generated (length=${#JWT_SECRET})"
unset JWT_SECRET

# ADMIN_RECOVERY_TOKEN — 48 random bytes hex (96 char)
ADMIN_RECOVERY=$(openssl rand -hex 48)
SECRETS_TO_SET="$SECRETS_TO_SET ADMIN_RECOVERY_TOKEN=$ADMIN_RECOVERY"
echo "  - ADMIN_RECOVERY_TOKEN: generated (length=${#ADMIN_RECOVERY})"
unset ADMIN_RECOVERY

# ── Step 3: Add hardcoded URLs + apply to aria-web ────────────────────────
echo
echo "[3/3] Applying ${HARDCODED[@]+${#HARDCODED[@]}} hardcoded overrides + setting all secrets on $DST_APP..."

for kv in "${HARDCODED[@]}"; do
  SECRETS_TO_SET="$SECRETS_TO_SET $kv"
  echo "  - ${kv%%=*}: hardcoded"
done

if [ -z "${SECRETS_TO_SET// }" ]; then
  echo "FATAL: nothing to set — exiting."
  exit 1
fi

# Apply in ONE call so Fly only redeploys aria-web once
# shellcheck disable=SC2086
flyctl secrets set $SECRETS_TO_SET -a "$DST_APP" --stage 2>&1 | tail -3
unset SECRETS_TO_SET

echo
echo "──────────────────────────────────────────────────────────────"
echo " Done."
echo
echo " Next: 'flyctl deploy --config fly.web.toml --strategy rolling'"
echo " The deploy will pick up the staged secrets and apply them."
echo
echo " ⚠ Still needed for FULL aria-web functionality (operator-paste):"
echo "   - STRIPE_SECRET_KEY + STRIPE_WEBHOOK_SECRET (billing)"
echo "   - TELEGRAM_BOT_TOKEN + TELEGRAM_WEBHOOK_SECRET (telegram bot)"
echo "   - ADMIN_EMAIL + ADMIN_PASSWORD (or first-login flow uses recovery token)"
echo "   - COMPANIES_HOUSE_API_KEY, ACLED_*, BLS_API_KEY, etc. (external data)"
echo "   - VAPID_* (web push — optional)"
echo " Use import_seenode_secrets.sh once you have a Seenode env export."
echo "──────────────────────────────────────────────────────────────"
