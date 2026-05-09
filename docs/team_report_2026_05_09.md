# ARIA — Team Report & Assessment

**Date:** 2026-05-09
**Audience:** Arkmurus / ARIA team
**Reading time:** 25 min

This is the consolidated picture across what's working, what's broken, what's been shipped recently, and what we need to decide as a team. Read it top-to-bottom or jump to the section relevant to your role.

---

## 0. Executive summary (read first)

ARIA today is a **structurally unique AI for security & defence due-diligence** with real moats (constitutional discipline, hash-chained audit log, 16,313 verified facts, 13,785 intel signals, autonomous engine running 30+ tasks daily). It is also unfinished as a *product* — the chat surface is solid, but the wraparound (billing, public API, mobile polish, audit-grade reports) is partial.

**Right now, three things matter most:**

1. **One critical operational bug**: every signal pushed by the seenode sweep to the Python brain via `brainAbsorb` is being dropped with HTTP 401. Cause: `ARIA_API_TOKEN` env var missing/mismatched on seenode. **Fix is one env var set; no code change needed.** Detail in §3.A.
2. **Strategic pivot just confirmed**: today's strategic review (`docs/strategic_review_2026_05_09.md`) reorders the launch sequence around customer-trust features (audit-grade PDF export, model card, status page) ahead of public API.
3. **First customer-converting feature shipped today**: audit-grade PDF export of any ARIA reply with HMAC signature, citations extraction, and constitution-clause references (R-F43, commit `d0a9437`). This is the single feature that turns ARIA from "Claude alternative for defence" into "the only AI a compliance officer will sign off on."

**Composite product-readiness vs Oct 2026 launch target: 60/100** (was 58/100 at start of today; today's commits added ~2 points). Detail in `docs/product_readiness_2026_05_09.md`.

---

## 1. What's working — the structural moats

These are real, measurable, and currently differentiate ARIA from anything else in the security/defence DD space.

### 1.1 Constitutional discipline (23 clauses, incident-anchored)
Every clause in `aria_engine.py:68-110` cites the past incident that motivated it. Examples:
- Clause 14 ("no fabricated verifiable facts") was added after the Modirum-GESPI incident where ARIA fabricated NACE codes + a Lisbon address from prompt context.
- Clause 12 ("no document review without text") was added after the Annex-1 GESPI incident on a truncated PDF.
- Clause 23 ("no acceptance of user-asserted compliance premises") was added after the A1_ANGOLA_ATT adversarial test exposed a false-premise vulnerability.

**Why this is a moat:** Generic LLMs (Claude/OpenAI/Gemini) hallucinate registry data because their training is "be helpful." ARIA refuses to invent. Compliance officers care about this.

### 1.2 Audit log (hash-chained + HMAC-signed)
Production fingerprint `a39f3328d92bffe4` since 2026-04-14T11:29:05Z. Every claim is timestamped and signed; the chain detects tampering after the fact. **Sayari, Kharon, LSEG do not have anything equivalent at the *output* level.**

### 1.3 Memory — 16,313 facts + 13,785 ledger signals
Verified via the production logs at last session close (2026-05-06). Disk-canonical (per fly volume), gzipped Redis snapshots (compression 5.35× verified), off-host email backup. **Pay-once-remember-forever**: every paid API call writes to the brain so the next query hits memory at $0.

### 1.4 Autonomous engine — 30+ scheduled tasks
While customers sleep, ARIA runs daily procurement scans across 15+ countries, sanctions re-screens, knowledge audits, hypothesis validation cycles. Per `chat_ui_scope_2026-10.md`: "no consumer chatbot does this."

### 1.5 Multi-channel
Web chat + WhatsApp + email + Telegram + (planned) public API. Each channel uses the same brain. A user can ask via WA "is OFAC listing CompanyX?" and get the same audit-grade answer they'd get on the web.

### 1.6 Source coverage — 48+ sources
Per the latest sweep: 48 sources producing 336 deduped updates and 230 signals in 25 seconds. Categories:
- Sanctions (OFAC, OFSI, EU, UN SC, Swiss SECO, etc.)
- Procurement portals (TED, SAM, GESPI, multi-country)
- Defence news (Defense News, Janes RSS, Shephard, regional)
- Open-source intelligence (ACLED, GDELT, ReliefWeb, OSINT feeds)
- Corporate registries (Companies House, OpenCorporates, OpenSanctions)
- Academic / research (Semantic Scholar, ArXiv, OpenAlex)

### 1.7 Lusophone Africa moat (per the strategic doctrine, *not* the only focus)
107 Lusophone procurement items, 19 Lusophone portal items, 12 Lusophone defence-news items, 123 Lusophone signals in the last sweep. **The strategic doctrine (`memory/aria_global_positioning.md`) treats Lusophone as one of many — but it's the deepest region by source coverage today.**

### 1.8 Recent shipped features (last 14 days, 36 R-numbered fixes)
Highlights:
- **R-F36** ledger Redis snapshot gzip — 4.5MB → 845KB (5.35× verified).
- **R-F35** IPv6 rate-limit bypass + UK FCDO sanctions URL migration (442 → 6044 sanctions).
- **R-F34** honest sweep tally — killed the "49/49" lie.
- **R-F33** TED v3 eForms migration done properly (after 3 whack-a-mole iterations).
- **R-F25..R-F29** five new legal modules: Turkish, Swiss, Portuguese, OHADA, Gulf.
- **R-F38 (today)** conversation history sidebar — listing/click-to-load/delete/Load-older + user_id plumbed through chat path.
- **R-F39 (today)** auth race fix + sticky title + mobile drawer + 44px touch targets.
- **R-F40 (today)** Stripe billing scaffold — env-gated, no-op until `STRIPE_SECRET_KEY` set.
- **R-F41 (today)** account/pricing UI with full tier comparison + checkout / portal flow.
- **R-F43 (today)** audit-grade PDF export with HMAC signature.

---

## 2. What's not working — be honest

### 2.1 CRITICAL: brainAbsorb 401 — every sweep signal silently dropped
External diagnostic 2026-05-09 confirmed every call from seenode → fly `/api/aria/brain/absorb` returns HTTP 401. **Cause is the missing `ARIA_API_TOKEN` env var on seenode** (or it's been rotated on one side and not the other).

Code path traced (`lib/self/learning_store.mjs:413-423`):
```
BRAIN_URL  = BRAIN_DIRECT_URL || BRAIN_SERVICE_URL || 'https://aria-intel.fly.dev'
BRAIN_TOKEN = ARIA_API_TOKEN || ARIA_INTERNAL_TOKEN || ''
```
If BRAIN_TOKEN is empty, no `Authorization: Bearer …` header → fly's router auth dependency 401s.

**Impact (huge):**
- Mastery scores not updated from sweep intelligence
- Intel ledger on the Python side missing CRUCIX-discovered signals
- Learning loop from source sweeps fully broken
- The 46 signals in the seenode-side ledger never reach ARIA's brain
- Hypothesis backlog drains slowly because new signals don't arrive
- Verification gate has fewer signals to corroborate against

**Fix:** Set `ARIA_API_TOKEN` on seenode to the same value as on fly (`flyctl secrets list -a aria-intel` shows the names; the value is what you originally set). Verify with `curl -H "Authorization: Bearer <token>" https://<seenode>/api/brain-absorb/diag` — `has_token: true`, then watch the next sweep cycle for `total_ok` to start incrementing.

**Why this happened:** memory `session_2026_04_23.md` records a previous incident where the token was rotated on fly without updating seenode, causing a 24-hour silent WA outage. This is the same failure mode.

**Note on the external reviewer's suggested fix:** they recommended setting `ARIA_API_KEY`. The code expects `ARIA_API_TOKEN`, **not** `ARIA_API_KEY`. Setting the wrong name fixes nothing.

### 2.2 Four partial sources (not two as previously logged)

| Source | Failure mode | Likely cause | Action |
|---|---|---|---|
| Breaking Defense | All attempts fail | Bot blocking / paywall | Live: try non-Googlebot UA + fallback to RSS-only |
| EU TED | 0 items (Google News fallback) | Rate limit / search-shape change | Verify v3 eForms migration is fully landed (R-F31/33) |
| NVD CVE | Timeout | NVD API instability throughout 2025 | Add longer timeout + circuit breaker; consider replacing |
| UK FCDO Sanctions | Feed unreachable | URL change ('24 format migration) | Verify URL in `lib/intel/source_registry.mjs`; FCDO consolidated list now at `assets.publishing.service.gov.uk/...` |

### 2.3 Operational state of disabled features (seenode)

These are intentionally OFF or pending — not failing, but limiting customer-perceived value:

| Feature | State | What's missing |
|---|---|---|
| WhatsApp listener | DISABLED | `WA_LISTENER_ENABLED=true` + the 3 channel-mirror env vars |
| Email reader | DISABLED | `ARIA_EMAIL_ENABLED=true` + IMAP creds |
| Telegram bot | DISABLED (chatId empty) | `TELEGRAM_CHAT_ID` |
| LLM (seenode side) | DISABLED | `LLM_PROVIDER` + `LLM_API_KEY` (seenode rarely needs its own LLM — Python brain handles it) |
| Discord | DISABLED | `DISCORD_BOT_TOKEN` etc |
| JWT_SECRET | EPHEMERAL | Set `JWT_SECRET=<openssl rand -hex 48>` so tokens persist across restarts |
| Admin user | NOT CREATED | `ADMIN_EMAIL` + `ADMIN_PASSWORD` |
| VAPID keys | EPHEMERAL | Set `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY`/`VAPID_SUBJECT` so push subs survive restart |

Full env-var inventory is in `docs/operating_env_vars_2026_05_09.md`.

### 2.4 Two lifespan outages in the last 30 days
Both on fly.io, both at cold-boot due to Python local-variable scoping issues in `aria_service/main.py:lifespan()`. The smoke-test rule (`memory/lifespan_smoke_test_required.md`) is now in CI but the outages happened before that landed. **No customer-facing status page yet** — outages were silent to anyone outside the operator.

### 2.5 Anthropic billing exhaustion (recurring)
Every cooldown burst logs an ERROR. DeepSeek fallback serves cleanly so customer-facing impact is zero, but the log noise + the fact that we're running on the second-best provider day-to-day are both addressable. Top up Anthropic; the billing block clears the rolling cooldown.

### 2.6 Hypothesis backlog drain rate trending sideways
At 2026-05-06 close: 114 OPEN, drain 6–8/cycle. Was 100 on 2026-05-03. Growing slightly faster than draining. R-F32 bumped per-cycle picks from 3 → 8; the 401 fix above will increase signal arrivals which should accelerate validation.

### 2.7 Adversarial baseline is stale
90.9% from 2026-04-23. Hasn't been re-run since. Target ≥95% before public launch. **This is a launch blocker if not refreshed.**

### 2.8 No status page, no SLA, no incident communication
Two outages in 30d, both silent to anyone outside the operator. Paying customers (when they exist) will expect a `status.aria.app`-style surface.

### 2.9 No legal layer
Privacy policy + ToS not drafted. Per `docs/strategic_review_2026_05_09.md`, this is small in lines-of-text but legal review takes 2–4 weeks calendar.

### 2.10 Single-machine architecture (fly.io)
`min_machines_running=1` on shared-cpu-2x. Volume is single-machine, so HA is traded for data coherence. First 100 paying users could hit capacity issues. Must be revisited before launch.

---

## 3. What's been shipped — chronology

### 3.A Today (2026-05-09): five commits

| Commit | What |
|---|---|
| `d598029` | docs: product-readiness assessment 2026-05-09 |
| `a7b5179` | R-F38: conversation history sidebar + user_id plumb-through |
| `bb47391` | R-F39: chat-UI polish (auth race fix, sticky title, mobile drawer, 44px tap targets) |
| `0c5c5dc` | R-F40: Stripe billing scaffold (env-gated, no-op until configured) |
| `f639053` | R-F41: account & billing page + pricing grid + dropdown link |
| `9ca9285` | docs: env-var reference + defence-DD strategic review |
| `d0a9437` | R-F43: audit-grade PDF export with HMAC signature |

R-F42 (public API) is paused mid-way; files exist on disk uncommitted.

### 3.B Last 30 days: ~36 R-Fs across fly + seenode
Major themes:
- Sanctions data integrity (R-F35, FCDO migration, IPv6 bypass)
- Tender monitor stability (R-F31/33, TED v3 migration)
- Memory durability (R-F1 + R-F36 gzip Redis snapshots)
- Knowledge depth (R-F25..R-F29 five new legal modules)
- UA rotation across the source fleet (R-F17..R-F20)

### 3.C Strategic doctrine commits (last few months)
- 23-clause constitution evolved from 15 (clauses 16–23 added incident-by-incident)
- Layer 5C commercial coherence (Slice 1 shipped)
- Two-tier mastery + heatmap
- Self-awareness stack (5/5 modules: self_metrics, capability_manifest, aria_peers, predictor, mistake_ledger)

---

## 4. What needs operator-pending action (priority order)

From `docs/operating_env_vars_2026_05_09.md` §11. **First five are zero-strategic-risk and unblock real value immediately:**

| # | Action | Server | Why |
|---|---|---|---|
| 1 | Set `ARIA_API_TOKEN` on seenode | seenode | **Fixes the brainAbsorb 401 — single highest-impact fix in the system right now.** Set to the same value as on fly. |
| 2 | Anthropic billing top-up | (Anthropic dashboard) | Ends the rolling cooldown ERROR noise; restores Anthropic as primary. |
| 3 | Verify `ARIA_MONTHLY_CAP_USD=300` on fly | fly | Code default is 300; live secret may still be 100 → projection mismatch on `/cost/monthly`. |
| 4 | Set `SAM_GOV_API_KEY` on fly | fly | Stops DAILY-PROC-SAM autonomous task firing into the void. Free tier 1000 calls/day at sam.gov/data-services. |
| 5 | Set `JWT_SECRET` on seenode | seenode | Stops tokens being invalidated on every restart. Generate with `openssl rand -hex 48`. |
| 6 | Set VAPID keys (3 vars) on seenode | seenode | Persists push-notification subscriptions across restarts. Generate with `web-push generate-vapid-keys`. |
| 7 | Set `DASHBOARD_USER` + `DASHBOARD_PASS` on seenode | seenode | Legacy dashboard basic-auth. |
| 8 | Set `ACLED_EMAIL` + `ACLED_PASSWORD` on both | both | Conflict-event coverage. |
| 9 | Set `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1` on fly | fly | Lights up the style-learner training-data capture. |
| 10 | Set `ARIA_NEURAL_SAMPLE_RATE=0.25` on fly | fly | 75% LLM cost cut on neural ingest (code shipped, env not flipped). |

**Held by operator until strategic green light:**

| # | Action | Server | Notes |
|---|---|---|---|
| 11 | Set 6 Stripe vars | seenode | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, `STRIPE_PRICE_PROINTEL`, `STRIPE_CHECKOUT_RETURN_URL`, `STRIPE_PORTAL_RETURN_URL`. Set in this order; webhook last. Until set, `/api/billing/config` returns `{configured:false}` and the FE shows "Coming soon" CTAs. |
| 12 | Set `REPORT_SIGNING_KEY` on seenode | seenode | Activates HMAC-signed PDF reports (R-F43 just shipped). Without it, PDFs generate with an explicit "UNSIGNED — not for audit use" warning. Generate with `openssl rand -hex 48`. |
| 13 | Set `ENABLE_PUBLIC_API=1` | seenode | Activates `/api/v1/*` (R-F42 paused). Hold until first Pro Intelligence customer signs up. |

---

## 5. What needs improvement — strategic gaps

Reordered from `docs/strategic_review_2026_05_09.md` §3.

### 5.1 Data depth (must-build over 5 months)
- Defence-specific lists (NDAA 1260H, DOD 1233, EU MIL-RAD, Military-Civil Fusion, Russian state-defence proxies, Wagner / Africa Corps networks)
- Equipment ↔ ECCN ↔ Wassenaar mapping (~5,000-line lookup, prompt-augmented)
- End-User Cert templates by jurisdiction (10 markets to start)
- Programme tracker (cluster intel-ledger signals into structured "programme cards")
- Multi-jurisdiction sanctions diff (daily delta against customer watchlist)

### 5.2 Workflow tools
- ✅ Audit-grade DD report PDF (just shipped R-F43)
- Watchlist push alerts (auto-rescreen runs; nothing notifies the operator on a hit)
- Counterparty risk dashboard
- DD report library (named, searchable, re-runnable)
- Tender comparator (side-by-side bid analysis)
- Email-to-DD trigger (forward an RFQ to `dd@aria.app` → ARIA runs DD)

### 5.3 Compliance / trust
- Public model card (`/about/model-card`)
- Privacy policy + ToS (legal review)
- Status page (uptime + incident history)
- Data residency statement (UK-hosted, document)
- DPA template (Data Processing Agreement)

### 5.4 Network effects
- Anonymous counterparty flag aggregation (3+ users flag same entity → "community-flagged" tag)
- Crowdsourced source registry submission (auto-validated through existing source_validator)

### 5.5 Distribution / GTM (operator-led, not engineering)
- DSEI 2026 (September 9–12) — booth or skip decision required
- Trade publication bylined article ("AI for export-control compliance")
- LinkedIn outreach to 50 friendly compliance officers (use the 234 contacts already ingested)
- Referral program (existing customer brings colleague → both get a free month of Pro Intelligence)
- Free-tier loss leader for journalists / academics → press coverage

---

## 6. Roadmap to launch (5-month view)

From `docs/strategic_review_2026_05_09.md` §6. Reordered around customer-trust, not feature-count.

**Month 1 (May → June): Audit-grade output + compliance trust**
- ✅ Audit-grade DD report PDF (R-F43 shipped today)
- Public model card
- Status page
- Privacy policy + ToS (legal review parallel)
- Persona-tuned system prompts (6 overlays + sector capture in registration)

**Month 2 (June → July): Compliance officer activation features**
- Watchlist push alerts
- Counterparty risk dashboard
- DD report library
- Stripe chat-path enforcement (when env vars land)

**Month 3 (July → August): Defence-specific data depth**
- ECCN/Wassenaar lookup
- NDAA 1260H + DOD 1233 + MCF + EU MIL-RAD ingestion
- Multi-jurisdiction sanctions diff
- EUC template library

**Month 4 (August → September): Network effects + polish**
- Anonymous flag aggregation
- Crowdsourced source submission
- DD report library shareable links (within team)
- Tender comparator
- Email-to-DD trigger

**Month 5 (September → October): Polish, GTM, launch**
- DSEI booth prep
- Marketing site copy + pricing page polish
- Public API (`/api/v1/*`) — R-F42 already scaffolded
- API docs site
- Beta-cohort feedback iteration

---

## 7. Decisions the team needs to make

These are gating items. Rank-ordered by what blocks the most downstream work.

### Within this week
1. **Sign off on the registration redesign** (3-screen flow capturing sector / use-case / region / volume / compliance needs / purpose). Drives all persona-tuning work.
2. **Pick the 6-persona overlay set**: broker / oem_export / government_acquisition / compliance / banking_insurance / journalist. Operator review of each overlay's prompt before it goes live (high blast radius).
3. **Decide whether to flip Stripe now or defer** until audit-grade DD PDF lands and feels polished. Recommendation: defer 2 weeks (the FIRST paying customer should see the polished experience).
4. **Approve domain** — `aria.app` (recommended) or alternative. Affects email templates, OAuth callbacks, sales decks.
5. **Decide tier price points** — accept $20 + $199 from `chat_ui_launch_decisions.md` or revise.

### Within this month
6. **Engage external counsel** for ToS + Privacy Policy + DPA. 2–4 weeks calendar.
7. **Decide on legal entity for revenue collection** — Arkmurus Limited or new spinout (`ARIA AI Ltd`?). Affects Stripe account.
8. **DSEI 2026 (Sept 9–12) — booth or skip?** Booth is ~£10K + travel; alternative is an industry-publication article + LinkedIn outreach.

### Within this quarter
9. **Assign GTM resource** — operator-only is unsustainable past first 50 paid users.
10. **Adversarial 95% gate decision** — accept as launch blocker, yes or no?
11. **Start SOC 2 Type 1 process** — if banking/insurance customers are in the wishlist, the certification takes ~6 months from kickoff. Start now even if launch doesn't depend on it.

---

## 8. What the team does next week

If you're the operator (Antonio):
- **Highest priority: set `ARIA_API_TOKEN` on seenode** (single env var, fixes the entire 401 cascade)
- Top up Anthropic billing
- Verify the rest of §4 priority list 1–10
- Read `docs/strategic_review_2026_05_09.md` §7 and answer items 1–5 from §7 above

If you're on the BD/compliance side:
- Test the new "Export PDF" button on a real DD reply — does the output look like something you'd forward to a counterparty's compliance officer?
- Run a real live BD inquiry through ARIA's chat (web or WA) and note where the answer was *almost* what you needed — feedback compounds the next iteration
- Review the 6-persona overlays (when drafted) for the persona closest to your daily work

If you're future engineering hire:
- Read `docs/product_readiness_2026_05_09.md` first (composite score), then `docs/strategic_review_2026_05_09.md` (gap analysis), then this doc
- Look at `lib/billing/`, `lib/reports/`, and the in-flight `lib/api_keys/` directories — they show the soft-rollout pattern this codebase uses
- The `aria_service/aria_engine.py:68-110` constitution is the highest-leverage 200 lines in the entire codebase; understand it before changing anything in chat path

---

## 9. References

| Doc | Purpose |
|---|---|
| `docs/product_readiness_2026_05_09.md` | Composite 60/100 score across 17 graded areas |
| `docs/strategic_review_2026_05_09.md` | 4-layer gap analysis + 5-month sequencing + competitive landscape |
| `docs/operating_env_vars_2026_05_09.md` | ~135 env vars across fly + seenode with current state and recommended values |
| `docs/chat_ui_scope_2026-10.md` | Original 6-week chat scope (now superseded by strategic review's reorder) |
| `docs/chat_ui_launch_decisions.md` | Pricing + waitlist + domain recommendations |
| `docs/ARIA_TEAM_PLAYBOOK.md` | Existing team playbook (read alongside this doc) |
| `aria_service/aria_engine.py:68-110` | The 23-clause constitution |
| `memory/MEMORY.md` | Index of session memories — every R-F has context here |

---

## 10. Bottom line

ARIA's intelligence engine + memory + audit log are working. The chat surface is solid. **The single biggest immediate operational issue is the brainAbsorb 401** — setting one env var (`ARIA_API_TOKEN` on seenode) restores the seenode → Python brain learning loop that has been broken for an unknown duration.

The single biggest *strategic* unlock is the audit-grade PDF export shipped today (R-F43). It is the feature compliance officers will open and forward, and it is what justifies the $199/mo Pro Intelligence price point above generic LLM alternatives.

The next two weeks: (a) flip §4 priority items 1–5 on seenode/fly; (b) fold today's registration redesign into a real onboarding flow; (c) draft the public model card so compliance buyers have something to clear.

Reading order for full context: this doc → `strategic_review_2026_05_09.md` § 3, 5, 7 → `operating_env_vars_2026_05_09.md` § 11 → `product_readiness_2026_05_09.md` headline.
