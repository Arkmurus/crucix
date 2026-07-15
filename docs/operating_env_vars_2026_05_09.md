# ARIA — Operating Environment Variables (Operator Reference)

**Date:** 2026-05-09
**Scope:** Every `process.env` (Node) and `os.getenv` (Python) reference in the codebase, mapped to which server it belongs on, what it does, and what to set it to.

Two servers:
- **fly.io** (`aria-intel`) — the Python brain (`aria_service/`). Set with `flyctl secrets set NAME=value -a aria-intel`. Auto-deploys on push.
- **seenode** (`web-qzregt3hvgvb.up-de-fra1-k8s-1.apps.run-on-seenode.com`) — Node/Express front-end + sweep + WhatsApp + email bridge. Set via the seenode dashboard. Auto-deploys on push.

Status legend:
- `LIVE` — already set in production
- `RECOMMENDED` — should be set; default behaviour is acceptable but suboptimal
- `OPERATOR-PENDING` — flagged in memory as needing operator action
- `OFF` — feature disabled by default; only set when activating
- `DEV-ONLY` — set in local `.env`; never in production

---

## 1. Core infrastructure (CRITICAL — must be set)

| Var | Server | Status | Value / Purpose |
|---|---|---|---|
| `JWT_SECRET` | seenode | **LIVE** | 64+ char random hex. Hard-fails in `NODE_ENV=production` if unset. Generate via `openssl rand -hex 48`. **Never rotate carelessly — invalidates every user's token.** |
| `NODE_ENV` | seenode | **LIVE** | `production` |
| `PORT` | both | LIVE | seenode: usually `3117`; fly: `8000` (set in `fly.toml`) |
| `REDIS_URL` *or* `UPSTASH_REDIS_URL` | both | **LIVE** | Upstash adapter URL; both processes must point to the same Upstash instance (`adapted-ostrich-92296`). |
| `UPSTASH_REDIS_TOKEN` | both | LIVE | Bearer for Upstash REST API. |
| `ADMIN_EMAIL` | seenode | **LIVE** | Used to bootstrap the first admin user. Required at first boot; ignored after. |
| `ADMIN_PASSWORD` | seenode | **LIVE** | ≥12 chars. Required at first boot. |
| `ARIA_API_TOKEN` | both | **LIVE** | Shared bearer for `/api/aria/*` routes. Same value on both servers. |
| `ARIA_INTERNAL_TOKEN` | both | **LIVE** | Distinct internal-service bearer (seenode → Python brain bridge). Different from `ARIA_API_TOKEN` per the user-facing-vs-internal split. |
| `ARIA_AUDIT_SIGNING_KEY` | fly | **LIVE** | HMAC key for audit log entries. **NEVER ROTATE** — breaks the hash chain post-cutoff (`a39f3328d92bffe4` since 2026-04-14T11:29:05Z). |
| `ARIA_FLY_URL` *and* `ARIA_BRAIN_URL` *and* `ARIA_SERVICE_URL` *and* `BRAIN_URL` *and* `BRAIN_SERVICE_URL` *and* `BRAIN_DIRECT_URL` | seenode | LIVE | All five point to the fly app, e.g. `https://aria-intel.fly.dev`. The redundancy is historical — different modules grew their own var names. Keep all in sync. |
| `ARIA_NODE_URL` *or* `SEENODE_URL` *or* `SEENODE_BASE_URL` | fly | LIVE | Reverse direction — fly → seenode for proxying chat to the Node front-end. e.g. `https://web-qzregt3hvgvb.up-de-fra1-k8s-1.apps.run-on-seenode.com` |

---

## 2. LLM providers (cost-bearing — at least one MUST be set, multi-provider recommended)

ARIA's `FallbackProvider` chain tries Anthropic → DeepSeek → Mistral → OpenRouter → Groq → local. Configure as many as you have credits for; the chain auto-uses what's available.

| Var | Server | Status | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | both | **LIVE (billing exhausted, OPERATOR-PENDING top-up)** | Primary provider. Recurring 400 cooldowns logged across sessions. DeepSeek picks up cleanly during exhaustion. **Top up to clear the rolling cooldown noise.** |
| `DEEPSEEK_API_KEY` | both | **LIVE** | Fallback #1. Cheap. Good for synthesis. |
| `OPENAI_API_KEY` | both | LIVE | Used for embeddings + occasional fallback. |
| `OPENAI_BASE_URL` | both | OFF | Set only when routing through a self-hosted OpenAI-compatible gateway. |
| `OPENROUTER_API_KEY` | fly | RECOMMENDED | Adds Mistral / Sonar / etc as fallbacks. |
| `GROQ_API_KEY` | fly | LIVE | Fast cheap fallback. |
| `MISTRAL_API_KEY` | both | OPERATOR-PENDING | Direct Mistral. Optional given OpenRouter coverage. |
| `GEMINI_API_KEY` | fly | OFF | Gemini fallback. Optional. |
| `OLLAMA_URL` *or* `OLLAMA_BASE_URL` | fly | DEV-ONLY | Local Ollama instance for offline reasoning. Don't set in prod (no GPU). |
| `OLLAMA_MODEL` | fly | DEV-ONLY | Model name for Ollama. |
| `LLM_PROVIDER` *and* `LLM_API_KEY` *and* `LLM_MODEL` | seenode | OFF | Legacy Node-side LLM config from before fallback chain. Leave unset. |
| `CODEX_ACCESS_TOKEN` *and* `CODEX_ACCOUNT_ID` | seenode | OFF | OpenAI Codex API; legacy. |
| `OPENAI_OAUTH_TOKEN` | seenode | OFF | Legacy. |
| `MINIMAX_API_KEY` | seenode | OFF | MiniMax. Optional fallback. |
| `ARIA_VISION_API_KEY` *and* `ARIA_VISION_PROVIDER` *and* `ARIA_VISION_MODEL` | fly | RECOMMENDED | Vision-LLM for OCR'd images / chunked PDFs. Provider e.g. `openai`, model `gpt-4o`. Without it, falls back to local EasyOCR (slower, less accurate). |

---

## 3. Cost discipline + rate limits (CRITICAL)

| Var | Server | Status | Recommended |
|---|---|---|---|
| `ARIA_MONTHLY_CAP_USD` | fly | OPERATOR-PENDING | Set to `300` (code default; the live secret may still be `100` per memory backlog `2026-04-27`. **Verify and raise.**) |
| `ARIA_MONTHLY_CAP_WARN_ONLY` | fly | OFF | Set to `1` to log warnings instead of hard-failing. Keep OFF unless intentionally over-budgeting. |
| `ARIA_DAILY_CAP_USD` | fly | OPERATOR-PENDING | Per-day spend cap for autonomous engine; recommend `15` (= $450/mo headroom). |
| `ARIA_TASK_CAP_USD` | fly | LIVE | Per-task cost cap; default in code. |
| `ARIA_USER_RPM_CAP` | fly | LIVE (default 30) | Per-user requests/min. |
| `ARIA_USER_DAILY_COST_USD_CAP` | fly | LIVE (default $5) | Per-user daily $; bumps to tier-aware once R-F40 enforces. |
| `ARIA_USER_QUOTA_UNLIMITED` | fly | RECOMMENDED | Comma-separated user IDs to bypass quotas. e.g. `antonio,ops,admin`. |
| `ARIA_LLM_RPM` | fly | LIVE | Global LLM RPM ceiling. |
| `ARIA_NEURAL_SAMPLE_RATE` | fly | OPERATOR-PENDING | Set to `0.25` for 75% LLM cost cut on neural ingest. Code shipped (commit `4e8e462`); env not flipped. |
| `BRAVE_ANSWERS_MONTHLY_USD` | fly | LIVE | Brave Answers monthly $; default in code. |
| `BRAVE_ANSWERS_MEMORY_HIT_THRESHOLD` | fly | LIVE | Memory-first hit threshold. |
| `AUTO_INVESTIGATE_MAX_DAILY` | seenode | LIVE | Caps autonomous investigation invocations/day. |

---

## 4. Intel data sources

The more of these you set, the richer ARIA's signal coverage.

### Sanctions + corporate registries
| Var | Server | Status | Notes |
|---|---|---|---|
| `OPENSANCTIONS_API_KEY` | both | **LIVE** since 2026-04-10 | Critical — all sanctions screening goes through OpenSanctions. |
| `COMPANIES_HOUSE_API_KEY` | both | **LIVE** | UK corporate registry (Companies House). Free tier ample for steady-state DD. |
| `OPENCORPORATES_API_KEY` | seenode | RECOMMENDED | Cross-jurisdiction corporate search. Free tier limited. |

### Geopolitical / OSINT
| Var | Server | Status | Notes |
|---|---|---|---|
| `ACLED_EMAIL` + `ACLED_PASSWORD` | both | OPERATOR-PENDING (code READY — R-F2627) | ACLED conflict events (Phase A gate #5). **BOTH are required; `ACLED_API_KEY` is DEAD** — it authenticated `api.acleddata.com`, which ACLED retired. R-F2627 migrated `conflict_tracker` to the current OAuth password grant (`POST acleddata.com/oauth/token` → Bearer → `acleddata.com/api/acled/read`). Operator action: register a free myACLED account at https://acleddata.com/register/ then `flyctl secrets set ACLED_EMAIL=... ACLED_PASSWORD=... -a aria-intel`. Until set, conflict data silently serves the lower-fidelity GDELT fallback (by design, not a bug). |
| `BRAVE_API_KEY` *or* `BRAVE_SEARCH_API_KEY` | both | LIVE | General web search backbone. |
| `BRAVE_ANSWERS_API_KEY` | fly | LIVE | Brave Answers API for memory-first synthesis. |
| `RAPIDAPI_KEY` | seenode | OFF | Bridge to assorted RapidAPI sources. Optional. |
| `RELIEFWEB_APPNAME` | seenode | LIVE | ReliefWeb humanitarian feeds (free, just needs an app name string). |
| `FRED_API_KEY` | seenode | LIVE | US Federal Reserve macro data. |
| `BLS_API_KEY` | seenode | OFF | Bureau of Labor Stats. Optional. |
| `EIA_API_KEY` | seenode | OFF | US Energy Information. Optional. |
| `COMTRADE_API_KEY` | seenode | LIVE | UN Comtrade trade flows. |
| `FIRMS_MAP_KEY` | seenode | OFF | NASA FIRMS satellite fire data. Optional. |
| `ADSB_API_KEY` | seenode | OFF | ADS-B aviation tracking. Optional but powerful for arms-flight detection. |
| `AISSTREAM_API_KEY` | seenode | OFF | AIS maritime tracking. Optional but powerful for arms-shipment detection. |

### Procurement
| Var | Server | Status | Notes |
|---|---|---|---|
| `SAM_GOV_API_KEY` | both | **OPERATOR-PENDING** | US federal contracting (SAM.gov). Without it, DAILY-PROC-SAM autonomous task fires but returns nothing → LLM fabricates generic output. **Set this to silence DAILY-PROC-SAM noise.** Free tier 1000 calls/day at sam.gov/data-services. |
| `SEMANTIC_SCHOLAR_API_KEY` | fly | LIVE | Academic / research papers. |

### News + Reddit
| Var | Server | Status | Notes |
|---|---|---|---|
| `REDDIT_CLIENT_ID` + `REDDIT_CLIENT_SECRET` | seenode | OFF | Reddit OSINT signal. Optional. |
| `ACADEMIC_APIS_ENABLED` | fly | LIVE (default 1) | Master toggle for academic-paper sources. |
| `ACADEMIC_POLITE_EMAIL` | fly | LIVE | Email used in API headers (politeness convention). |

---

## 5. Communication channels

### WhatsApp
| Var | Server | Status | Notes |
|---|---|---|---|
| `WA_LISTENER_ENABLED` | seenode | LIVE (`1`) | Master toggle. |
| `WA_LISTENER_PORT` | seenode | LIVE | Internal listener port. |
| `WA_LISTENER_AUTH_DIR` | seenode | LIVE | Path where Baileys stores session creds. |
| `WA_LISTENER_GROUP_IDS` | seenode | LIVE | Group JIDs the listener will respond in. |
| `WA_LISTENER_AUTO_RESPOND` | seenode | LIVE | `1` to auto-respond. |
| `WA_REPLY_ENABLED` | seenode | LIVE | Same family. |
| `WA_ALERT_GROUP_ID` | seenode | LIVE | Group for autonomous alert pushes. |
| `WA_ALERT_POLL_MS` | seenode | LIVE | Poll interval. |
| `ARIA_WA_MAX_PROBES_WITHOUT_PEER` | seenode | LIVE | Watchdog: max consecutive probes before reconnect. |
| `ARIA_MIRROR_GROUPS` *and* `ARIA_MIRROR_DEFERRED` *and* `ARIA_MIRROR_MIN_LEN` | seenode | **3 OPERATOR-PENDING** per memory `next_session_todo.md` | Channel mirror config; flip when ready. |

### Twilio (alternative WhatsApp path)
| Var | Server | Status | Notes |
|---|---|---|---|
| `TWILIO_ACCOUNT_SID` *and* `TWILIO_AUTH_TOKEN` *and* `TWILIO_WHATSAPP_FROM` | seenode | OFF | Legacy Twilio path. Set only if Baileys path fails. |
| `ALLOW_LEGACY_TWILIO` | seenode | OFF | Master switch. Leave OFF. |

### Email (inbound + outbound)
| Var | Server | Status | Notes |
|---|---|---|---|
| `ARIA_EMAIL_ENABLED` | seenode | LIVE | Master toggle for inbound IMAP. |
| `ARIA_EMAIL_HOST` | both | LIVE | `ox.livemail.co.uk` (per memory `linkedin_email_gap.md`). |
| `ARIA_EMAIL_PORT` | both | LIVE | Usually `993` (IMAP+SSL). |
| `ARIA_EMAIL_USER` *and* `ARIA_EMAIL_PASS` | both | LIVE | Mailbox credentials. |
| `ARIA_EMAIL_FROM` | seenode | LIVE | Outbound `From` header. |
| `ARIA_EMAIL_POLL_MS` | seenode | LIVE | Poll cadence (e.g. 60000 = 1 min). |
| `ARIA_EMAIL_BACKFILL_COUNT` | seenode | LIVE | How many recent emails to backfill on cold start. **Don't exceed 20** per memory `email_bridge_2026_04_18_19.md` (caused thundering-herd flood at higher values). |
| `EMAIL_HOST` *and* `EMAIL_USER` *and* `EMAIL_PASS` *and* `EMAIL_PORT` *and* `EMAIL_FROM` *and* `EMAIL_SECURE` | seenode | LIVE (some) | Generic SMTP for password-reset / transactional emails. Distinct from the ARIA mailbox. |
| `ARIA_SMTP_HOST` *and* `ARIA_SMTP_PORT` *and* `ARIA_SMTP_USER` *and* `ARIA_SMTP_PASS` | both | LIVE | ARIA outbound SMTP for proactive emails. |
| `ARIA_TEAM_EMAILS` | both | LIVE | Comma-separated list of team recipients for daily briefings. |
| `ARIA_OPERATOR_EMAIL` | fly | LIVE | Where R-Findings + critical alerts go. |
| `ARIA_BACKUP_EMAIL_ENABLED` *and* `ARIA_BACKUP_EMAIL_TO` *and* `ARIA_BACKUP_DIR` *and* `ARIA_BACKUP_RETENTION_DAYS` | fly | LIVE | Off-host memory backup (daily snapshot via email). |
| `COMPLIANCE_TEAM_EMAILS` | seenode | OFF | Specific compliance escalation list. Optional. |
| `ARIA_COUNTERPARTY_CONTACTS` | seenode | OFF | Pre-populated contact list. Optional. |

### Telegram
| Var | Server | Status | Notes |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | seenode | LIVE | Bot token. |
| `TELEGRAM_CHAT_ID` *and* `TELEGRAM_ADMIN_CHAT_ID` | seenode | LIVE | Default chat IDs. |
| `TELEGRAM_ALLOWED_USERS` | seenode | LIVE | Whitelist for `/ask` etc. |
| `TELEGRAM_CHANNELS` | seenode | OFF | Extra channel IDs for cross-posting. |
| `TELEGRAM_POLL_INTERVAL` | seenode | LIVE (5000ms) | Poll cadence. |
| `TELEGRAM_WEBHOOK_SECRET` | seenode | OFF | Set if migrating from polling to webhook. |

### Slack / Discord / Push / Zoom
| Var | Server | Status | Notes |
|---|---|---|---|
| `ARIA_SLACK_WEBHOOK` | both | OFF | Slack alert webhook. Optional. |
| `DISCORD_BOT_TOKEN` *and* `DISCORD_CHANNEL_ID` *and* `DISCORD_GUILD_ID` *and* `DISCORD_WEBHOOK_URL` | seenode | OFF | Discord bot. Optional. |
| `VAPID_PRIVATE_KEY` *and* `VAPID_PUBLIC_KEY` *and* `VAPID_SUBJECT` | seenode | OPERATOR-PENDING | Web Push VAPID keys. Generate via `web-push generate-vapid-keys`. |
| `ZOOM_BOT_URL` | seenode | OFF | Zoom bot. Optional. |
| `ZOOM_ACCOUNT_ID` *and* `ZOOM_CLIENT_ID` *and* `ZOOM_CLIENT_SECRET` *and* `ZOOM_WEBHOOK_SECRET` | fly | OFF | Zoom integration. Optional. |
| `ARIA_ZOOM_ENABLED` | fly | OFF | Master Zoom toggle. |

---

## 6. Billing — Stripe (R-F40, ALL OPERATOR-HELD per 2026-05-09)

Set ALL six on seenode to enable paid tiers. Until then `/api/billing/config` returns `{configured: false}` and the FE shows "Coming soon" CTAs.

| Var | Server | Status | Recommended |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | seenode | OPERATOR-HELD | `sk_live_...` from Stripe Dashboard → Developers → API keys. Test with `sk_test_...` first. |
| `STRIPE_WEBHOOK_SECRET` | seenode | OPERATOR-HELD | `whsec_...` from the webhook config in Stripe Dashboard. Set the webhook URL to `https://<seenode-domain>/api/billing/webhook`. Subscribe to: `checkout.session.completed`, `customer.subscription.created/updated/deleted`, `invoice.payment_failed`. |
| `STRIPE_PRICE_PRO` | seenode | OPERATOR-HELD | `price_...` for the $20/mo Pro plan. Create in Stripe → Products → New product → recurring monthly. |
| `STRIPE_PRICE_PROINTEL` | seenode | OPERATOR-HELD | `price_...` for the $199/mo Pro Intelligence plan. |
| `STRIPE_CHECKOUT_RETURN_URL` | seenode | OPERATOR-HELD | e.g. `https://aria.app/account.html?checkout=ok` (or current host). |
| `STRIPE_PORTAL_RETURN_URL` | seenode | OPERATOR-HELD | e.g. `https://aria.app/account.html` |
| `STRIPE_CHECKOUT_CANCEL_URL` | seenode | OFF | Optional override; defaults to host root. |

---

## 7. Public API (R-F42, paused — not wired yet)

| Var | Server | Status | Recommended |
|---|---|---|---|
| `ENABLE_PUBLIC_API` | seenode | OFF | `1` enables `/api/v1/*`. **Hold until Pro Intelligence tier has actual customers.** |
| `PUBLIC_API_RATE_PER_MIN` | seenode | OFF | Per-key requests/min. Default `60`. |

---

## 8. Behaviour flags / feature toggles (Python side)

Most of these have safe defaults. Set only when changing behaviour.

| Var | Default | Set when |
|---|---|---|
| `ARIA_AUTONOMOUS_ENABLED` | `0` (OFF) | **Set `1` ONLY after 24h cost attribution is verified.** Per memory `cost_cap_and_autonomy_gate.md`. |
| `ARIA_AUTONOMOUS_RESEARCH_ENABLED` | `1` | Sub-flag — autonomous research loop only. |
| `ARIA_AUTONOMOUS_DRY_RUN` | `0` | Set `1` to dry-run the autonomous engine without executing tools. |
| `ARIA_DD_ORCHESTRATOR_ENABLED` | `1` | Master DD orchestrator toggle. Leave on. |
| `ARIA_DD_DEEP_RESEARCH` | `1` | Use deep_research path inside DD. |
| `ARIA_DEEP_EXTRACT_AUTO_INGEST` | `1` | Auto-ingest extracted documents to RAG. |
| `ARIA_DOCUMENT_READER_ENABLED` | `1` | `/api/aria/read-document` endpoint. |
| `ARIA_LAYER_5C_ENABLED` | `1` | Commercial coherence DD layer. Set `0` to bypass. |
| `ARIA_CHALLENGE_ENABLED` | `1` | Active challenge engine (Devil's Advocate). |
| `ARIA_CHALLENGE_MODEL` | (LLM default) | Override challenge LLM. |
| `ARIA_CHAT_TRAIN_CAPTURE_TEXT` | `0` | OPERATOR-PENDING per memory. Set `1` to capture raw text for the style-learner. |
| `ARIA_COMPANIES_HOUSE_ENABLED` | `1` | UK CH integration. |
| `ARIA_CONTRACT_INTELLIGENCE` | `1` | Contract review module. |
| `ARIA_CORPUS_MANAGER_ENABLED` | `1` | Self-managed corpus expansion. |
| `ARIA_CORRECTION_LEARN` | `1` | Learn from `/teach` corrections. |
| `ARIA_CORRECTION_RECALL` | `1` | Recall past corrections in answers. |
| `ARIA_CORRECTION_AUTHZ_OFF` | `0` | Set `1` to disable authz on `/teach` (DEV ONLY). |
| `ARIA_CORRECTION_TRUSTED_SENDERS` | (none) | Comma-separated user IDs trusted to teach. |
| `ARIA_DECEPTION_THRESHOLD` | `0.6` | Counterparty deception detection threshold. |
| `ARIA_DEFAULT_DEAL_TYPE` | `''` | Hint for ambiguous BD queries. |
| `ARIA_FORCE_RESEED` | `0` | Set `1` to force corpus re-seed on next boot. |
| `ARIA_GHOST_DETECTION_PRINCIPLES` | `1` | Front-company detection module. |
| `ARIA_HARVEST_DIR` *and* `ARIA_HARVEST_REDACT_NAMES` | (paths/0) | Output harvester. |
| `ARIA_KNOWLEDGE_PATH` *and* `ARIA_LEDGER_PATH` *and* `ARIA_RAG_PATH` *and* `ARIA_BACKUP_DIR` *and* `ARIA_TRAINING_EXPORT_DIR` *and* `ARIA_WRITER_AUDIT_PATH` | `/data/...` | Disk paths under fly volume. **Don't override — let defaults pin to `/data/`.** |
| `ARIA_MAX_DOC_CHARS` | (built-in) | Max chars from a single document into context. |
| `ARIA_MEM0_ENABLED` | `1` | mem0 personal notebook. |
| `ARIA_METACOGNITIVE_ENABLED` | `1` | Metacognitive engine. |
| `ARIA_OCR_AUTO_INSTALL` | `0` | Set `1` to auto-install OCR deps on boot (avoid in prod). |
| `ARIA_OCR_DPI` | `200` | OCR DPI. |
| `ARIA_OCR_LANGS` *or* `ARIA_OCR_LANGUAGES` | `en,pt,es,fr,ro` | Comma-separated ISO 639-1 codes for EasyOCR. |
| `ARIA_OCR_PREFER_CLOUD` | `0` | Set `1` to prefer cloud vision over local OCR. |
| `ARIA_OFFICEHOLDER_GUARD` | `1` | Officeholder verification guard. |
| `ARIA_OUTPUT_HARVEST_ENABLED` *and* `ARIA_OUTPUT_HARVEST_THRESHOLD` | (defaults) | Output harvester. |
| `ARIA_PLAYWRIGHT_MAX_CONCURRENT` | `2` | Browser-render concurrency. |
| `ARIA_PMESII_TEMPLATE_ENABLED` | `1` | PMESII briefing template. |
| `ARIA_RAG_BACKFILL_ENABLED` *or* `ARIA_RAG_BACKFILL_DISABLED` | (defaults) | RAG backfill cycle gate. |
| `ARIA_REDIS_WARN_BYTES` | `4194304` | Redis blob warn threshold. |
| `ARIA_REDIS_ERROR_BYTES` | `26214400` | Redis blob error threshold. |
| `ARIA_SEMANTIC_INDEX_BUILD` | `1` | Build semantic search index on boot. |
| `ARIA_STALE_KNOWLEDGE_ALERTS` | `1` | Alert on stale knowledge cells. |
| `ARIA_TONE` | `''` | Override default tone. Leave empty unless intentionally tweaking. |
| `ARIA_V3_PROMPTS_ENABLED` | `1` | New prompt template family. |
| `ARIA_VISION_LARGE_DOC_THRESHOLD` *and* `ARIA_VISION_MAX_PAGES` *and* `ARIA_VISION_MAX_TOTAL_PAGES` | (defaults) | Chunked-vision tuning. |
| `ARIA_WARNING_THRESHOLD` | `0.7` | Confidence warning threshold for the footer. |
| `ARIA_WRITER_FALLBACK_MODEL` | (built-in) | Writer fallback if primary LLM fails. |
| `ARIA_CONFIDENCE_FOOTER` | `1` | Add `[ASSESSED — confidence X%]` footer to outputs. |
| `ARIA_INT_TOKEN` | (alias) | Alias for `ARIA_INTERNAL_TOKEN`. Keep in sync. |
| `ARIA_DOC_TIMEOUT` | (default) | Document parse timeout. |
| `ARIA_NEGOTIATION_PRINCIPLES` *and* `ARIA_RESEARCHER_PRINCIPLES` *and* `ARIA_GHOST_DETECTION_PRINCIPLES` *and* `ARIA_CONTRACT_REVIEW_PRINCIPLES` *and* `ARIA_ANALYTIC_PRINCIPLES` | `1` | Principle injectors per topic. Leave on. |
| `SKIP_LIFESPAN_SMOKE` | `0` | Set `1` only in CI environments where the smoke can't run. |

### Brain memory paths (don't override unless you know what you're doing)
- `ARIA_KNOWLEDGE_PATH` → `/data/aria_knowledge.json`
- `ARIA_LEDGER_PATH` → `/data/aria_signals.json`
- `ARIA_RAG_PATH` → `/data/aria_rag/`
- `ARIA_BACKUP_DIR` → `/data/backups/`

---

## 9. Airtable + GitHub (ticketing)

| Var | Server | Status | Notes |
|---|---|---|---|
| `AIRTABLE_PAT` | fly | LIVE | Personal access token. |
| `AIRTABLE_BASE_ID` | fly | LIVE | Base ID. |
| `AIRTABLE_TICKETS_TABLE` *and* `AIRTABLE_PIPELINE_TABLE` *and* `AIRTABLE_TASK_TABLE` | fly | LIVE | Table IDs (live: pipeline `tblAv2qgrVQ7VHUBB`). |
| `AIRTABLE_FIELD_MAP` | fly | LIVE | JSON field-name override map. |
| `AIRTABLE_SYNC_ENABLED` | fly | LIVE (`1`) | Master Airtable toggle. |
| `ARIA_TICKETS_AIRTABLE_ENABLED` *and* `ARIA_TICKETS_GITHUB_ENABLED` | fly | LIVE | Per-destination toggles. |
| `GITHUB_TOKEN` | fly | LIVE | GitHub PAT for `raise_ticket` (constitution clause 22). |
| `GITHUB_REPO` | fly | LIVE | `Arkmurus/crucix`. |
| `GITHUB_ARIA_LABEL` | fly | LIVE | Issue label for ARIA-raised tickets (e.g. `aria-raised`). |

---

## 10. Cloudflare / dashboard / misc

| Var | Server | Status | Notes |
|---|---|---|---|
| `CLOUDFLARE_API_TOKEN` | seenode | OFF | If using Cloudflare DNS automation. |
| `DASHBOARD_USER` *and* `DASHBOARD_PASS` | seenode | OPERATOR-PENDING per memory | Basic-auth for legacy dashboard at `/dashboard.html`. Generate strong values. |
| `INT_TOKEN` | seenode | LIVE | Internal token (legacy alias). |
| `APP_URL` *or* `RENDER_EXTERNAL_URL` | seenode | LIVE | Public host URL used in email templates etc. |
| `CRUCIX_LANG` *and* `LANG` *and* `LANGUAGE` | both | LIVE | i18n locale (defaults to `en`). |
| `DISPLAY` | (linux) | DEV-ONLY | X display for headless browser; only relevant on workstations. |
| `ARKMURUS_LEGAL_NAME` | fly | LIVE | "Arkmurus Limited" — used in compliance outputs. |
| `ARIA_WEBHOOK_URLS` | fly | OFF | Outbound webhooks for events. Optional. |

---

## 11. Critical operator-pending items (priority order)

These are the ones that **actually matter right now** — set in this order:

| Priority | Var(s) | Why |
|---|---|---|
| 1 | **Anthropic top-up** | Eliminates the recurring 400 cooldown; restores Anthropic as primary, DeepSeek as fallback (current order is reversed). |
| 2 | `ARIA_MONTHLY_CAP_USD=300` (verify on fly) | Code default is 300; fly secret may still be 100 (memory says yes). Causes projection mismatch in `/cost/monthly`. |
| 3 | `SAM_GOV_API_KEY` | Stops DAILY-PROC-SAM from firing into the void and producing fabricated "country X is interesting" output. |
| 4 | `VAPID_PRIVATE_KEY`, `VAPID_PUBLIC_KEY`, `VAPID_SUBJECT` | Web Push for the dashboard. |
| 5 | `DASHBOARD_USER`, `DASHBOARD_PASS` | Legacy dashboard basic-auth. |
| 6 | `HF_TOKEN` (not in current grep — the var is on the wishlist; needs adding to whichever module pings the HF Hub) | Silences ~25 HEAD requests per cold-start. |
| 7 | `ACLED_EMAIL` + `ACLED_PASSWORD` | Conflict-event coverage. |
| 8 | WA mirror env vars (`ARIA_MIRROR_GROUPS`, `ARIA_MIRROR_DEFERRED`, `ARIA_MIRROR_MIN_LEN`) | Channel mirror for WA. |
| 9 | `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1` | Lights up the style-learner training data capture (per memory `session_2026_04_22_evening.md`). |
| 10 | `ARIA_NEURAL_SAMPLE_RATE=0.25` | 75% LLM cost cut on neural ingest. |

**Stripe set (items 11–16)** is held by the operator until the strategic review concludes. Once cleared:
| Priority | Var(s) | Why |
|---|---|---|
| 11 | `STRIPE_SECRET_KEY` | Activates billing. |
| 12 | `STRIPE_WEBHOOK_SECRET` | Required for safe webhook handling. |
| 13 | `STRIPE_PRICE_PRO`, `STRIPE_PRICE_PROINTEL` | Per-tier price IDs. |
| 14 | `STRIPE_CHECKOUT_RETURN_URL`, `STRIPE_PORTAL_RETURN_URL` | Post-checkout redirects. |

---

## 12. How to set on each platform

**fly.io** (per-secret):
```
flyctl secrets set NAME=value -a aria-intel
```
Multiple at once:
```
flyctl secrets set NAME1=value1 NAME2=value2 -a aria-intel
```
List current names (without values):
```
flyctl secrets list -a aria-intel
```

**seenode** — via the dashboard UI (no CLI). Set per environment.

After flipping any secret on fly, fly restarts the machine automatically. After flipping on seenode, the change takes effect on next deploy or manual restart from the dashboard.

---

## 13. Audit on next session

1. Run `flyctl secrets list -a aria-intel` and compare names against §1–§9 above.
2. Verify `ARIA_MONTHLY_CAP_USD` value matches the operator-confirmed cap (300 or higher).
3. Verify `ARIA_AUDIT_SIGNING_KEY` is set (production fingerprint should still be `a39f3328d92bffe4`).
4. Verify `ARIA_API_TOKEN` and `ARIA_INTERNAL_TOKEN` match between fly and seenode (the listener silently fails if mismatched — past gap, see `lib/auth/users.mjs:128` / memory `session_2026_04_23.md`).
5. For each operator-pending item in §11, decide whether to flip now or defer.
