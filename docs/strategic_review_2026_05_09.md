# ARIA — Strategic Review for the Security & Defence DD Ecosystem

**Date:** 2026-05-09
**Author:** Claude (code+memory grounded, market analysis from prior knowledge)
**Companion docs:** `product_readiness_2026_05_09.md` (composite score), `operating_env_vars_2026_05_09.md` (operator reference)
**Reading time:** 25 min

---

## 0. Why this exists

The product-readiness assessment delivered earlier today scored ARIA at composite **58/100** vs the Oct 2026 consumer-grade-AI release target. The plan it surfaced — sidebar / Stripe / mobile / API / docs — was the **right plan for a generic AI chatbot.** It is not necessarily the right plan for ARIA.

Operator pivot 2026-05-09:
> "Lets ensure the product delivers what is needed... ARIA must provide a unique ecosystem of a valuable LLM in the security and defence DD world."

This document answers: **what does "unique ecosystem" mean specifically, and what's the gap between today and that?**

---

## 1. The category — "AI for security & defence DD"

This is not "AI for general business." It is a small, high-stakes, regulated, deeply-domain market with specific buyer types:

| Buyer | Job-to-be-done | Today's tools | Pain point |
|---|---|---|---|
| **Defence broker** (e.g. Arkmurus) | Find counterparties, prove they're sanction-clean, draft offset/EUC clauses, win bids | Manual research + Sayari + LinkedIn + Janes paywall | Hours per counterparty; no audit-grade output; no narrative reasoning |
| **OEM export-control officer** | Classify products under ECCN/Wassenaar, vet end-users, sign EUCs | LSEG / D&B / ad-hoc | Classification consistency; product-to-control mapping is brittle |
| **Compliance officer** at a defence buyer | Ensure incoming bid is clean, audit on-record | LexisNexis Diligence / Refinitiv / Sayari | Repetitive screening; no narrative; can't explain *why* a counterparty is risky |
| **Government acquisition** | Vendor risk vetting, programme intel | Janes + paywalled DBs + intel cells | Cost; no cross-source synthesis; manual workflow |
| **Defence journalist / NGO researcher** | Investigate arms flows, sanctioned actors | Bellingcat methods + OSINT + leaks | No structured knowledge graph; everything is one-off |
| **Insurance / banking compliance** (war-risk, KYC for defence accounts) | Vet exposures, screen counterparties | World-Check / Sayari | Same as compliance officer |

**Critical observation:** these buyers have **distinct workflows but overlapping data needs**. A single product can serve all six if it captures workflow context at registration and adapts.

---

## 2. The competitive landscape (where ARIA actually competes)

| Competitor | Strength | Weakness vs ARIA |
|---|---|---|
| **Sayari** | Best-in-class corporate ownership graph; 280M entities; sanctions overlay | No narrative reasoning; no DD report writing; no domain expertise in defence-specific items (ECCN, EUC); $$ enterprise sales; no chat surface |
| **Kharon** | Sanctions intel with deep IRGC / Russia / Wagner research | Same — graph-only, no LLM, no workflow tools, $$$ |
| **LSEG World-Check** | Ubiquitous KYC list; trusted by banks | Boring lookup; no intel synthesis; no chat |
| **Refinitiv** | Market data + KYC | Same parent as LSEG; same pattern |
| **Dun & Bradstreet** | Largest corporate database globally | Generic — not defence-specialised; no real-time intel |
| **Janes** | Defence equipment + programme intelligence — the gold standard | Pure paywall; no chat; no DD; expensive ($50K+/year) |
| **SIPRI** | Open arms transfer database | Open data, no product wrapper |
| **OpenSanctions** | Open consolidated sanctions | Open data; ARIA already ingests |
| **OpenCorporates** | Open corporate registry | Open data; ARIA already ingests |
| **Generic LLMs (Claude, OpenAI, Gemini)** | Best-in-class reasoning + general writing | **Hallucinates registries** (the GESPI incident); no live sanctions integration; no audit log; no domain priors; no provenance |
| **Niche AI tools** (e.g. Castellum.ai) | Some have started in this niche | Younger than ARIA in some respects; less constitutional discipline; no autonomous engine |

### ARIA's structural moats (already built)
1. **Constitutional discipline** — 23 clauses with past-incident citations. Generic LLMs hallucinate company numbers; ARIA's clause 14 explicitly bans verifiable-fact fabrication. This is **a moat that cannot be replicated by Claude/OpenAI without retraining** because it lives at the prompt layer + verification gate, not in the model weights.
2. **Audit-grade output trail** — hash-chained + HMAC-signed audit log (`a39f3328d92bffe4` cutoff 2026-04-14). Source-tier on every signal. Provenance on every claim. **This is unique among LLM products in this space.**
3. **DD orchestrator** — multi-source pipeline (registries → sanctions → media → adverse coverage → UBO inference → verification gate → claim ledger) with `[CONFIRMED]/[PROBABLE]/[ASSESSED]/[UNCERTAIN]/[SPECULATIVE]` tagging.
4. **Pay-once-remember-forever** — 16,313 facts in knowledge base, 13,785 in intel ledger, growing daily. Each new query first checks memory; only goes to the web if memory misses. This is a moat that grows with use.
5. **Autonomous engine** — 30+ scheduled tasks running daily, accumulating signals while customers sleep. Sayari/Janes don't push intel; ARIA does.
6. **Multi-channel** — chat + WhatsApp + email + (planned) public API. Generic LLMs are chat-only.
7. **Domain priors built in** — knows what an offset is, what FAA Angola is, what NSPA eligibility means, what the ECJU SITCL classification implies.

### Where ARIA is genuinely behind
1. **No structured corporate ownership graph** — Sayari's 280M-entity graph with 50%-rule traversal is a real gap. ARIA's UBO logic relies on per-call API queries.
2. **No live equipment-to-control-list mapping** — ECCN classification is currently prompt-based, not lookup-driven.
3. **No DD report PDF export** — output lives in chat. A defence broker needs a PDF they can forward.
4. **No watchlist alert push** — auto-rescreen exists; the "hey, your watchlist just got hits" alert via email/push doesn't.
5. **No team workspace** — single-user. A four-person compliance team can't share a workspace.
6. **No SSO** — blocks enterprise sales.
7. **Tier-gating exists in code (R-F40) but not enforced on the chat path.** Until enforced, free-tier abuse is possible.
8. **No public model card** — buyers in this space want explicit capability+limitation statements before trusting output.
9. **No SOC2 / ISO27001 posture** — banks/insurers will ask.
10. **Programme intelligence is signal-level, not structured.** Janes has structured programmes; ARIA has accumulating signals.

---

## 3. The strategic gap analysis — what's missing for "uniquely valuable"

I'll group into four layers. Each layer has a recommended **must-build** sub-set for the 5-month horizon and a **defer** sub-set.

### Layer 1 — Data depth (the thing customers can't get elsewhere)

**MUST BUILD (5 months):**
- **Defence-specific lists**: NDAA 1260H, US DOD 1233, EU MIL-RAD, MCF (Military-Civil Fusion), Russian state-defence proxies, Wagner / Africa Corps networks. Ingest as named lists; tag every screen with which list(s) hit.
- **Equipment ↔ ECCN ↔ Wassenaar mapping**: even a 5,000-line lookup table covering the most common defence items is more valuable than 100M-line generic CCL — because the long tail rarely matters in active deals.
- **End-User Cert templates by jurisdiction** (10 markets to start): UK, US, EU, Israel, Turkey, India, Brazil, Saudi, UAE, South Africa. Each: standard EUC clause text, known re-export-consent rules, warning flags.
- **Programme tracker**: extract programme names from intel ledger signals + procurement docs, cluster by buyer, surface as structured "programme cards" (status, expected close, OEMs in play).
- **Multi-jurisdiction sanctions diff**: daily delta against a customer's watchlist, cross-checking OFAC + UK OFSI + EU + Canadian SEMA + Australian DFAT + Swiss SECO + UN SC + country-specific (Saudi NCNT, GCC bans, Indian MEA).

**DEFER (post-launch):**
- Real-time AIS arms-shipment tracking (operator currently has API key OFF).
- ADS-B arms-flight tracking.
- Crypto-tracing for Iran/Russia front entities.
- Court records integration (CourtListener, BAILII).

**Why these vs others:** every one of these is a compliance question a defence broker is asked daily. None of them is well-served by Claude/OpenAI/Sayari today. ARIA's existing infrastructure (intel_ledger, knowledge.py) makes them additive, not redesigns.

### Layer 2 — Workflow tools (what the user actually does in a day)

**MUST BUILD:**
- **Audit-grade DD report**: PDF export with HMAC signature, source citations on every claim, constitution clauses referenced. Can be forwarded to a counterparty, a bank, an export-control officer. **This is the single most valuable feature for converting paid customers.**
- **Watchlist with alert push**: scheduled re-screening already exists; add "alert when status changes" via email/WA/push. (Auto-rescreen runs; nothing notifies the operator on a hit.)
- **Counterparty risk dashboard**: per-company panel with sanctions status, registry match, beneficial ownership, recent signals, deception score, prior interactions. Sayari has graphs; ARIA can have the *narrative* + the numbers.
- **DD report library**: every DD ARIA runs gets persisted as a named report (`/api/aria/dd/reports`). Customer can list, search, re-run, share, export.
- **Tender comparator**: side-by-side analysis of multiple bids on the same RFQ. Highlight clause deviations, price anomalies, EUC implications.
- **Email-to-DD trigger**: forward an RFQ email to `dd@aria.app` → ARIA runs full DD on the counterparty mentioned + replies with the report.

**DEFER:**
- Slack / Teams bots
- Salesforce / HubSpot CRM connectors
- Notion / Coda exports
- Excel pivot-style intel exports

**Why:** these directly map to "billable hours saved per week" in a defence broker's job. The PDF export alone justifies the $199/mo Pro Intelligence price for a single user.

### Layer 3 — Compliance + trust (what unblocks enterprise sales)

**MUST BUILD:**
- **Public model card** — at `/about/model-card` or similar. States: what ARIA can do, what it can't, source-tier hierarchy, hallucination guards (with the 23 constitution clauses), audit log spec.
- **Privacy policy + ToS** — legal review. Per `chat_ui_scope_2026-10.md` this was flagged as SMALL but legal-review timing is what makes it MEDIUM.
- **Status page** at `status.<domain>` — uptime + incident history. Currently zero customer-facing incident visibility (2 lifespan outages in 30 days were silent to anyone outside the operator).
- **Data residency statement** — UK-hosted (fly.io LHR). Customers in defence will ask. Document.
- **DPA template** — Data Processing Agreement for paid tiers.
- **Source-tier transparency in the UI** — every claim already carries `[from <url>]`; the FE should make tiers visually obvious (e.g. green for Tier 1a official source, amber for Tier 3 secondary, red for unverified).

**DEFER:**
- SOC 2 Type 1 / Type 2 (months of work, not weeks).
- ISO 27001.
- Custom audit hooks per enterprise customer.
- Penetration test report.

### Layer 4 — Network effects (the unfair advantage at scale)

**MUST BUILD (small, foundational):**
- **Anonymised counterparty flag aggregation**: when 3+ Pro Intel users have flagged the same counterparty, surface as a "community-flagged" signal in subsequent DDs (anonymised; no leak of which user flagged). Network effect compounds with every paid user.
- **Crowdsourced source registry**: customers can submit sources they trust; auto-validated through the existing source_validator pipeline (constitution clause 18). Approved sources benefit all customers.

**DEFER:**
- Industry alerts / shared watchlists.
- Public-facing sanctions diff service (free tier feeder).
- DEFCON / DSEI integrations.

---

## 4. Registration / onboarding redesign

### Why the current flow is insufficient

Today: signup form takes name / username / email / password / confirm. After admin approval, you're in. ARIA defaults to "defence broker" persona because Antonio is the operator.

For a multi-tenant product serving the six buyer types listed in §1, this is wrong. ARIA needs to know **who you are and why you're using it** to:
- Pick the right system-prompt persona
- Default the right region heatmap
- Prioritise the right source mix
- Recommend the right tier
- Tailor onboarding examples
- Configure compliance defaults

### Proposed registration flow (3 screens)

**Screen 1 — Account fundamentals** (existing — keep as is)
- Full name
- Username
- Email
- Password
- Confirm password
- ToS / Privacy acknowledgement (new)

**Screen 2 — Organisation context** (new)
- **Account type**: `Individual` / `Company`
- **If Company**: company name, country of registration, company size (≤10 / 11–50 / 51–200 / 201–1000 / 1000+).
- **Sector**: radio buttons —
  - Defence broker / dealer / agent
  - OEM (defence manufacturer)
  - Government (acquisition, MoD, intel)
  - Compliance / export control consultancy
  - Banking / insurance (defence accounts)
  - Research / academic / NGO
  - Defence journalism
  - Other (free text)
- **Job title** (free text, optional)

**Screen 3 — Use case** (new)
- **Primary use case** (multi-select):
  - Counterparty due diligence
  - Sanctions screening
  - Tender intelligence / opportunity tracking
  - Contract / RFQ review
  - Market intelligence / strategic planning
  - Compliance audit / report generation
  - Equipment / ECCN classification
  - Programme tracking
  - Other
- **Region focus** (multi-select): `Global` / `NATO` / `EU` / `Lusophone Africa` / `Anglophone Africa` / `Francophone Africa` / `Gulf` / `Levant` / `North Africa` / `LatAm` / `MERCOSUR` / `CIS` / `South Asia` / `SE Asia` / `East Asia` / `Indo-Pacific`.
- **Languages needed** (multi-select): EN / PT / FR / ES / AR / RU / ZH / TR / Other.
- **Volume estimate**:
  - Few queries per week
  - Daily (10–50/day)
  - Heavy (50–500/day)
  - Enterprise (500+/day or shared workspace)
- **Compliance needs** (multi-select):
  - Standard
  - Audit-grade exports (HMAC-signed reports)
  - Data residency requirement
  - SSO required (SAML / OIDC)
  - On-premise option (defer / waitlist)
- **Purpose statement** (free text, 1–3 sentences): "Briefly describe how you'll use ARIA."

### Where the data goes and what it drives

| Field | Persisted on user record | Drives |
|---|---|---|
| account_type, company info | yes | Display in account.html |
| sector | yes | System-prompt persona selector (broker/compliance/journalist/etc) |
| primary_use_case | yes | Default chips on welcome screen; tailored example queries |
| region_focus | yes | Heatmap default zoom; source mix priority (Lusophone-heavy → boost Portuguese-language sources) |
| languages_needed | yes | OCR languages + search-doctrine clause 19(5) language picker |
| volume_estimate | yes | Tier recommendation + sales-touch trigger (Enterprise → operator-notify) |
| compliance_needs | yes | If "audit-grade" → enable PDF report export; if "SSO" → operator notify (not built yet) |
| purpose_statement | yes | Operator review during admin approval; corpus seeding examples |

### Persona-tuned system prompts (the high-leverage payoff)

ARIA's current system prompt (`aria_engine.py:68+`) is a 23-clause constitution + Lusophone-Africa-anchored domain expertise + broker-mode action bias. For a sector=`compliance` user, the action bias clause should soften ("recommend research steps before action") and the audit-trail clause should harden ("ALWAYS attach claim ledger entries"). For a sector=`journalist` user, Tier-1 source preference becomes Tier-1a-public-record (court records, press archives) rather than tier-1a-government.

This is a small, well-scoped engineering task: load a `persona/<sector>.md` overlay onto the base constitution at request time. The base constitution is shared (cannot be overridden — it's the safety floor); the overlay tunes emphasis and examples.

**Recommended overlays for v1 (six personas):**
- `broker.md` — current default
- `oem_export.md` — emphasise ECCN classification, EUC compliance, dual-use rule
- `government_acquisition.md` — emphasise programme tracking, vendor risk, FMS implications
- `compliance.md` — emphasise audit log, claim ledger, [UNCERTAIN] tagging, SOC2/ISO references
- `banking_insurance.md` — emphasise sanctions screening, KYC, beneficial ownership, war-risk
- `journalist.md` — emphasise source diversity, public-record verification, no source-revealing

---

## 5. The "necessary environment" — what truly makes this product win

I'll be blunt: **shipping the four lifters from the assessment is necessary but not sufficient.** Here's the wider environment that has to exist.

### A. Pricing power requires audit credibility
A Sayari subscription costs $50K–$200K/year. A Janes subscription costs similar. ARIA at $199/mo Pro Intelligence is 3–5% of Sayari's price. **The reason customers will pay anything for ARIA is the combination of auditability + reasoning + integration that none of them offer.** If we strip the audit-grade output, we're a $20/mo Claude alternative — not a $199/mo defence-industry tool.

**What this means for sequencing:** the audit-grade DD report PDF is *more important than* the public API. Build the PDF first, the API second.

### B. The customer's compliance officer is the buyer
A defence broker can sign up at $20/mo Pro freely. But a $199/mo Pro Intelligence purchase has to clear the buyer's compliance officer, who will ask:
- Where is data stored?
- What's the audit trail spec?
- What happens if your AI hallucinates a sanction status?
- Can we export everything and walk away?

ARIA's constitution clauses + audit log + source-tier discipline answer these *technically*. But they need to be **packaged into one document** the compliance officer can read. That document is the **public model card** + a **DD output specification** + a **data residency statement**. Three small docs that unblock enterprise sales.

### C. Network effects compound the moat
Sayari's graph is its moat: more data → better matches → more users → more data. ARIA's moat is **memory + constitutional discipline + source diversity**. Network effects come from:
- Anonymous counterparty flag aggregation (3+ users flag same entity → "community-flagged" tag)
- Crowdsourced source submission with auto-validation
- Shared (anonymised) DD pattern library — "users investigating Algerian counterparties most commonly check these 5 things"

Build these *now* (small features) so they compound from the first paid customer.

### D. Distribution is harder than product
Even with the best product, defence-DD customers don't sign up via an "Upgrade to Pro" button on a chat page. Distribution happens through:
- **Defence industry conferences** (DSEI September, IDEX Feb, Eurosatory June, Farnborough July): demo booth, in-person sales, business-card exchange. **DSEI 2026 is September 9–12; that's the launch window if v1 lands by August.**
- **Trade publications** (Defense News, Janes, Shephard, Air Forces Monthly): bylined article from operator on "AI for export-control compliance" creates inbound.
- **LinkedIn outreach**: target compliance officers at defence primes + brokers + bankers (ARIA already has 234 contacts ingested per memory `session_2026_04_16`).
- **Referral program**: existing customer brings a colleague → both get a free month of Pro Intelligence.
- **Free-tier loss leader** for journalists / academics → press coverage + organic credibility.

The product team must ship by September; the GTM team (= operator until hired) must work conferences.

### E. The 5-month roadmap that follows

If we accept the strategic gap analysis above, the assessment's lifter list reorders:

| Old order | New order | Why |
|---|---|---|
| #1 sidebar — DONE | #1 (DONE) | Conversation history was right |
| #2 Stripe — DONE (scaffold) | #4 — wire chat-path enforcement when env vars land | Scaffold is fine; enforcement waits |
| #3 mobile — DONE | #2 (DONE) | Mobile polish was right |
| #4 fresh adversarial run | #5 | Important but operator action |
| #5 public API | #6 | Defer until paying customers exist |
| **NEW #3 — Audit-grade DD report PDF export** | **#3** | Single highest-value feature for converting paid customers; unblocks compliance-officer purchase |
| **NEW #4 — Public model card + privacy policy + status page** | **#5** | Compliance officer requires these to clear purchase |
| **NEW #6 — Watchlist push alerts** | **#7** | Customer activation feature; existing watchlist re-screen → just add notification |
| **NEW #7 — Persona-tuned system prompts (6 overlays)** | **#8** | Multi-tenant value; small engineering, big perception lift |
| **NEW #8 — Equipment ↔ ECCN/Wassenaar lookup** | **#9** | Differentiator; data-driven not LLM-driven |
| **NEW #9 — Defence-specific list ingestion** (NDAA 1260H, MCF, etc) | **#10** | Differentiator; same pattern as existing sanctions ingest |
| **NEW #10 — Counterparty risk dashboard** | **#11** | UI surface for the existing DD orchestrator output |
| **NEW #11 — Anonymous flag aggregation (network effect)** | **#12** | Compounds from first paid user |

Lifters 1-2 already shipped. Lifter 3 (audit-grade DD report PDF) is the next ship.

---

## 6. What this means for the 5-month-to-launch sequencing

### Months remaining before October 2026 launch: **5**

**Month 1 (May → June 2026): Audit-grade output + compliance trust**
- Audit-grade DD report PDF export (Lifter 3) — 1.5 weeks
- Public model card document at `/about/model-card.html` — 2 days
- Status page (`/status.html`, surfaces /api/health + uptime) — 2 days
- Privacy policy + ToS template (legal review parallel) — 2 days code, weeks legal
- Persona-tuned system prompts (6 overlays + sector capture in registration) — 3 days

**Month 2 (June → July): Compliance officer activation features**
- Watchlist push alerts (email + WA + dashboard badge) — 1 week
- Counterparty risk dashboard — 1 week
- DD report library (named, searchable, re-runnable) — 1 week
- Stripe chat-path enforcement (if env vars land) — 3 days

**Month 3 (July → August): Defence-specific data depth**
- Equipment ↔ ECCN/Wassenaar lookup (5,000-line table, prompt-augmented) — 1.5 weeks
- NDAA 1260H + DOD 1233 + MCF + EU MIL-RAD ingestion — 1 week
- Multi-jurisdiction sanctions diff service — 1 week
- EUC template library (10 markets) — 3 days

**Month 4 (August → September): Network effects + polish**
- Anonymous flag aggregation — 3 days
- Crowdsourced source submission — 3 days
- DD report library shareable links (within team) — 2 days
- Tender comparator — 1 week
- Email-to-DD trigger — 3 days

**Month 5 (September → October): Polish, GTM, launch**
- DSEI booth prep + demo recording — 1 week
- Marketing site copy + pricing page polish — 1 week
- Public API (`/api/v1/*`) — 1 week (R-F42 already scaffolded)
- API docs site — 3 days
- Status page incident history backfill — 1 day
- Beta-cohort feedback iteration — open

**Skipped from the original scope (move to v2):**
- Voice I/O
- Image generation
- Native mobile apps
- Artifact-style live HTML preview
- Team workspaces (single-user only at v1)
- SOC 2 / ISO 27001 (months-long process; start the certification but don't gate launch on it)

### Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Operator can't run GTM solo while building | HIGH | Hire a part-time defence-industry sales contractor by July |
| Adversarial baseline drifts below 95% pre-launch | MEDIUM | Run weekly adversarial suite; track in dashboard; gate launch on ≥95% |
| Anthropic billing exhaustion during a customer demo | LOW (mitigated) | DeepSeek + Mistral fallbacks already operational; top-up Anthropic anyway |
| First customer's compliance review surfaces a feature gap that takes 2 weeks to fix | HIGH | Start outbound to 5 friendly compliance officers in May; let them tear ARIA apart in June |
| Stripe webhook outage drops a subscription event | LOW (mitigated) | Stripe retries on 5xx; webhook handler 500s on transient errors instead of swallowing |
| Single fly.io machine outage = total platform outage | MEDIUM | Currently traded for data coherence. Document SLA expectation in the model card; revisit multi-machine post-launch. |

---

## 7. Operator decisions needed (what blocks the next two weeks)

**This week (in priority order):**
1. **Sign off on registration redesign** — yes / refine / no. Drives next ship.
2. **Pick 6-persona overlay copy review** — operator should review each overlay before it goes live (high blast radius if wrong).
3. **Decide whether to flip Stripe env vars now or defer** (per `operating_env_vars_2026_05_09.md` §6). My recommendation: defer 3 weeks until the audit-grade DD PDF lands so the FIRST paying customer's experience is the polished one, not the half-empty one.
4. **Approve `aria.app` domain** (or alternative). Affects email templates, OAuth callbacks, sales decks.
5. **Decide tier price points** — accept `chat_ui_launch_decisions.md`'s $20 + $199 or change. Affects Stripe product setup.

**This month:**
6. **Engage external counsel** for ToS + Privacy Policy + DPA — start now, expect 2–4 weeks.
7. **Decide on legal entity for revenue collection** — Arkmurus Limited or new spinout (`ARIA AI Ltd`?). Affects Stripe account, contracts.
8. **Greenlight DSEI booth** (September 9–12) or skip — booth costs ~£10K + travel; alternative is industry-publication bylined article + LinkedIn outreach.

**This quarter:**
9. **Assign GTM resource** — operator-only is unsustainable past first 50 paid users.
10. **Adversarial 95% gate decision** — accept as launch blocker?
11. **Start SOC 2 Type 1 process** — if banking/insurance customers are in the wishlist, the certification itself takes ~6 months from kickoff; start now even if launch doesn't depend on it.

---

## 8. One-sentence summary

> **ARIA's existing constitutional discipline + intel ledger + DD orchestrator is structurally unique in this market; the gap to a winnable product launch is not features, it's *packaging that gap into auditable outputs the customer's compliance officer can clear and the customer's wallet can justify*.**

The next ship that moves us most: **audit-grade DD report PDF export.** It is the single feature that turns ARIA from "Claude alternative for defence" into "the only AI a compliance officer will sign off on."

---

## 9. References

- `docs/product_readiness_2026_05_09.md` — composite score
- `docs/operating_env_vars_2026_05_09.md` — operator env-var reference
- `docs/chat_ui_launch_decisions.md` — pricing + waitlist + domain decisions
- `docs/chat_ui_scope_2026-10.md` — original 6-week chat scope
- Memory: `aria_global_positioning.md`, `aria_autonomy_doctrine.md`, `aria_core_mastery_topics.md`, `feedback_aria_rule_zero.md`, `product_vision_6mo_release.md`, `next_session_todo.md`, `bd_drive_through_plan.md`, `feedback_pay_once_remember_forever.md`, `feedback_fallback_transparency.md`
- Code: `aria_service/aria_engine.py:68-110` (constitution), `aria_service/intel/audit_log.py`, `aria_service/intel/dd_orchestrator.py`, `aria_service/intel/intel_ledger.py`, `aria_service/intel/verified_intel.py`, `aria_service/intel/source_validator.py`, `lib/billing/tiers.mjs`
