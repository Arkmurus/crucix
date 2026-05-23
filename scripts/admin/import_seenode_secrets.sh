#!/usr/bin/env bash
# R-F832 (2026-05-23) — import Seenode env → Fly `aria-web` + `aria-wa`.
#
# Operator workflow:
#   1. Log into seenode dashboard → app settings → Environment Variables
#   2. Export the env block to a local file (one KEY=VALUE per line)
#   3. Run: bash scripts/admin/import_seenode_secrets.sh ~/Downloads/seenode.env
#
# What this script does:
#   - Parses the .env file
#   - Filters out values that should NOT be migrated (HOSTNAME, PWD, etc.)
#   - Splits the env into:
#       * web-only keys → `flyctl secrets set ... -a aria-web`
#       * shared keys (ARIA_INTERNAL_TOKEN etc.) → BOTH aria-web AND aria-wa
#       * wa-only keys → `flyctl secrets set ... -a aria-wa`
#   - DRY-RUN by default — set FLYIO_APPLY=1 to actually push secrets.
#
# Safety:
#   - Never echoes secret values to stdout (only key names)
#   - Refuses to apply if any of EXCLUDE_NAMES is in the input
#   - Refuses to overwrite existing fly secrets unless FLYIO_OVERWRITE=1
#     is set (defensive — accidental re-import shouldn't clobber a
#     value that was rotated post-migration)

set -euo pipefail

ENV_FILE="${1:-}"
if [ -z "${ENV_FILE}" ] || [ ! -f "${ENV_FILE}" ]; then
  echo "usage: $0 <path-to-seenode.env-file>" >&2
  echo "       FLYIO_APPLY=1 $0 ...  → actually push secrets (else dry-run)" >&2
  exit 1
fi

APPLY="${FLYIO_APPLY:-0}"
OVERWRITE="${FLYIO_OVERWRITE:-0}"

# ── Key routing ────────────────────────────────────────────────────────────
# Each key in the env file is routed to one or more Fly apps based on
# which side actually reads it. The Node server reads everything; the
# WA listener reads only its own subset.

WEB_KEYS=(
  # Auth / sessions
  ADMIN_EMAIL ADMIN_PASSWORD ADMIN_RECOVERY_TOKEN JWT_SECRET APP_URL
  # Billing
  STRIPE_SECRET_KEY STRIPE_WEBHOOK_SECRET
  STRIPE_CHECKOUT_RETURN_URL STRIPE_CHECKOUT_CANCEL_URL STRIPE_PORTAL_RETURN_URL
  # Telegram
  TELEGRAM_BOT_TOKEN TELEGRAM_WEBHOOK_SECRET TELEGRAM_ADMIN_CHAT_ID
  TELEGRAM_ALLOWED_USERS TELEGRAM_CHANNELS TELEGRAM_CHAT_ID
  # Outbound mail (audit W7 — both naming conventions; whichever is set wins)
  EMAIL_HOST EMAIL_USER EMAIL_PASS EMAIL_FROM EMAIL_PORT EMAIL_SECURE
  SMTP_HOST SMTP_USER SMTP_PASS SMTP_PORT
  # Inbound mail (the bridge) — both naming conventions
  ARIA_EMAIL_HOST ARIA_EMAIL_USER ARIA_EMAIL_PASS ARIA_EMAIL_FROM
  ARIA_EMAIL_PORT ARIA_EMAIL_ENABLED ARIA_EMAIL_POLL_MS ARIA_EMAIL_BACKFILL_COUNT
  ARIA_SMTP_HOST ARIA_SMTP_USER ARIA_SMTP_PASS ARIA_SMTP_PORT
  # Web push
  VAPID_PUBLIC_KEY VAPID_PRIVATE_KEY VAPID_SUBJECT
  # External APIs the Node side calls directly
  COMPANIES_HOUSE_API_KEY CLOUDFLARE_API_TOKEN BLS_API_KEY COMTRADE_API_KEY
  EIA_API_KEY ADSB_API_KEY AISSTREAM_API_KEY FIRMS_MAP_KEY FRED_API_KEY
  OPENSANCTIONS_API_KEY OPEN_SANCTIONS_API_KEY
  ACLED_EMAIL ACLED_PASSWORD
  CODEX_ACCESS_TOKEN CODEX_ACCOUNT_ID
  # Twilio (alternative WA channel)
  TWILIO_ACCOUNT_SID TWILIO_AUTH_TOKEN TWILIO_WHATSAPP_FROM
  # Zoom integration
  ZOOM_APP_CLIENT_ID ZOOM_APP_CLIENT_SECRET
  # Dashboard basic auth
  DASHBOARD_USER DASHBOARD_PASS
  # Alert + webhook fanout
  ARIA_SLACK_WEBHOOK ARIA_WEBHOOK_URLS ARIA_TEAM_EMAILS COMPLIANCE_TEAM_EMAILS
  # Reports + audit signing (used by both Python + Node)
  REPORT_SIGNING_KEY
  # Misc Node-side toggles
  ARIA_VERSION_LABEL ARIA_TONE ARIA_CHAT_PROXY_TIMEOUT_MS ARIA_DD_PROXY_TIMEOUT_MS
  ARIA_PROXY_TIMEOUT_MS ARIA_STREAM_PROXY_TIMEOUT_MS ARIA_MAX_DOC_CHARS
  AUTO_INVESTIGATE_MAX_DAILY ALLOW_LEGACY_TWILIO CRUCIX_LANG
)

# Shared between aria-web AND aria-wa (both need to talk to aria-intel)
SHARED_KEYS=(
  ARIA_API_TOKEN ARIA_INTERNAL_TOKEN
)

WA_ONLY_KEYS=(
  ARIA_MIRROR_GROUPS ARIA_MIRROR_MIN_LEN ARIA_DECEPTION_THRESHOLD
  ARIA_COUNTERPARTY_CONTACTS ARIA_WA_MAX_PROBES_WITHOUT_PEER
  # WA listener-only config
  WA_LISTENER_ENABLED WA_LISTENER_PORT WA_LISTENER_AUTH_DIR
  WA_LISTENER_AUTO_RESPOND WA_LISTENER_GROUP_IDS WA_ALERT_GROUP_ID
)

# Skip these — they belong on aria-intel (Python brain), not aria-web/aria-wa.
# Setting on the wrong app at best wastes a secret slot, at worst breaks
# the Node app (e.g. ARIA_STATE_BACKEND only makes sense for Python).
SKIP_FOR_NODE=(
  ARIA_AUTONOMOUS_ENABLED ARIA_MONTHLY_CAP_USD ARIA_CHAT_TRAIN_CAPTURE_TEXT
  ARIA_MIRROR_DEFERRED ARIA_OUTPUT_HARVEST_ENABLED ARIA_LEARNING_CONTROLLER_ENABLED
  ARIA_STATE_BACKEND ARIA_LLM_URL
  LLM_PROVIDER LLM_API_KEY LLM_MODEL LLM_BASE_URL
  OLLAMA_URL OLLAMA_MODEL
  SELF_PING CLAUDE_CODE_USE_POWERSHELL_TOOL NODE_ENV
  # Hardcoded by this script — keep on the skip list so the import
  # doesn't fight the hardcoded value:
  ARIA_SERVICE_URL BRAIN_URL BRAIN_SERVICE_URL BRAIN_DIRECT_URL
  ARIA_FLY_URL ARIA_BRAIN_URL ARIA_INGEST_URL
)

# Never migrate these — leftovers from the seenode runtime that don't
# apply on Fly OR sensitive system internals
EXCLUDE_NAMES=(
  HOSTNAME PWD HOME PATH NODE_VERSION NPM_CONFIG_CACHE
  SHLVL OLDPWD TERM USER LANG
)

is_in_array() {
  local needle="$1"; shift
  for v; do [ "$v" = "$needle" ] && return 0; done
  return 1
}

# Hard-set values for migration — these must point at Fly internal DNS
# (not the public Seenode URL), and the SEENODE_BASE_URL itself is
# replaced by aria-wa's internal name.
HARDCODED_OVERRIDES=(
  "ARIA_SERVICE_URL=http://aria-intel.internal:8000"
  "ARIA_FLY_URL=http://aria-intel.internal:8000"
  "ARIA_BRAIN_URL=http://aria-intel.internal:8000"
  "ARIA_INGEST_URL=http://aria-intel.internal:8000/api/aria/ingest"
  "BRAIN_URL=http://aria-intel.internal:8000"
  "BRAIN_SERVICE_URL=http://aria-intel.internal:8000"
  "BRAIN_DIRECT_URL=http://aria-intel.internal:8000"
)

echo "──────────────────────────────────────────────────────────────"
echo " Seenode → Fly secret import (R-F832/F833)"
echo " Mode: $([ "$APPLY" = "1" ] && echo "APPLY" || echo "DRY-RUN")"
echo " Overwrite: $([ "$OVERWRITE" = "1" ] && echo "YES" || echo "NO")"
echo " Source: ${ENV_FILE}"
echo "──────────────────────────────────────────────────────────────"

# Parse env file into associative array; ignore comments + blank lines
declare -A ENV_KV
parse_env_file() {
  local line key val
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"  # strip inline comments
    [ -z "${line// }" ] && continue
    key="${line%%=*}"
    val="${line#*=}"
    key="${key// /}"
    [ -z "$key" ] && continue
    if is_in_array "$key" "${EXCLUDE_NAMES[@]}"; then continue; fi
    ENV_KV["$key"]="$val"
  done < "$ENV_FILE"
}
parse_env_file

# Categorise input keys
declare -a TO_WEB=() TO_WA=() TO_BOTH=() UNROUTED=() SKIPPED=()
for k in "${!ENV_KV[@]}"; do
  if is_in_array "$k" "${SKIP_FOR_NODE[@]}"; then
    SKIPPED+=("$k")
  elif is_in_array "$k" "${SHARED_KEYS[@]}"; then
    TO_BOTH+=("$k")
  elif is_in_array "$k" "${WEB_KEYS[@]}"; then
    TO_WEB+=("$k")
  elif is_in_array "$k" "${WA_ONLY_KEYS[@]}"; then
    TO_WA+=("$k")
  else
    UNROUTED+=("$k")
  fi
done

echo
echo "Routing plan:"
echo "  → aria-web only:  ${#TO_WEB[@]} keys"
echo "  → aria-wa only:   ${#TO_WA[@]} keys"
echo "  → both apps:      ${#TO_BOTH[@]} keys"
echo "  → skipped (Python-side; stay on aria-intel):  ${#SKIPPED[@]}"
echo "  → unrouted (skipped — review):  ${#UNROUTED[@]}"
echo

if [ "${#SKIPPED[@]}" -gt 0 ]; then
  echo "ℹ Skipped (correctly belong on aria-intel, not aria-web/aria-wa):"
  for k in "${SKIPPED[@]}"; do echo "    - $k"; done
  echo
fi

if [ "${#UNROUTED[@]}" -gt 0 ]; then
  echo "⚠ Unrouted keys (not migrated — confirm intentional):"
  for k in "${UNROUTED[@]}"; do echo "    - $k"; done
  echo
fi

apply_secrets() {
  local app="$1"; shift
  local -a kv_pairs=()
  for k in "$@"; do kv_pairs+=("$k=${ENV_KV[$k]}"); done
  # Add hardcoded overrides for both web + wa apps
  for kv in "${HARDCODED_OVERRIDES[@]}"; do kv_pairs+=("$kv"); done
  if [ "${#kv_pairs[@]}" -eq 0 ]; then return 0; fi
  echo "Setting ${#kv_pairs[@]} secrets on $app..."
  if [ "$APPLY" = "1" ]; then
    flyctl secrets set "${kv_pairs[@]}" -a "$app"
  else
    echo "  (dry-run — re-run with FLYIO_APPLY=1 to push)"
    for k in "$@"; do echo "    - $k"; done
    for kv in "${HARDCODED_OVERRIDES[@]}"; do echo "    - ${kv%%=*} (hardcoded override)"; done
  fi
}

# Set web-only + shared on aria-web
WEB_FINAL=("${TO_WEB[@]}" "${TO_BOTH[@]}")
apply_secrets aria-web "${WEB_FINAL[@]}"

echo
# Set wa-only + shared on aria-wa
WA_FINAL=("${TO_WA[@]}" "${TO_BOTH[@]}")
apply_secrets aria-wa "${WA_FINAL[@]}"

echo
echo "──────────────────────────────────────────────────────────────"
echo " Done."
if [ "$APPLY" != "1" ]; then
  echo " This was a DRY-RUN. To actually push: FLYIO_APPLY=1 $0 $ENV_FILE"
fi
echo "──────────────────────────────────────────────────────────────"
