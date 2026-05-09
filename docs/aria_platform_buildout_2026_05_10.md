# ARIA — Platform Build-Out Plan (Robust, Sequenced, Gated)

**Date**: 2026-05-10
**HEAD**: `07a8cfe`
**Goal as stated by operator**: *"make ARIA the AI + LLM + Compliance platform to use"*
**Anchor docs**: this builds on `aria_full_architecture_2026_05_10.md` (what is), `aria_capability_expansion_roadmap_2026_05_10.md` (what's next technically), and `aria_budget_roadmap.html` (financial discipline). This document covers the **trust + distribution + scale + defensibility** layer that turns "internal tool" into "platform of record."

---

## What "THE platform" actually means

A platform is the platform when (1) defence brokers and compliance officers reach for it by name; (2) regulators accept its output without re-validation; (3) competitors are measured against it, not the other way round; (4) it cannot be displaced without re-architecting the customer's own process.

Making ARIA that requires building four pillars in lockstep:

| Pillar | What it is | Why it matters |
|---|---|---|
| **Capability** | depth of what ARIA can do (DD layers, search reach, mastery, sovereign LLM) | table stakes — buy-in starts here |
| **Trust** | certifications, auditable methodology, third-party validation, insurance | gates enterprise sales completely |
| **Distribution** | how customers find / try / buy / renew | revenue precondition for everything else |
| **Defensibility** | what makes ARIA un-displaceable (specificity, constitution, methodology IP, sovereign LLM) | the moat — without this, success invites cloning |

Building one without the others fails. Capability without trust = "neat demo, can't deploy." Trust without distribution = certified-but-unsold. Distribution without defensibility = winning the first 12 months then losing the next 24.

---

## The 7-phase trajectory (12 months from today)

Each phase has: scope · cost · time · exit gate. **No phase starts until the prior gate passes.** Phases C–E run partly in parallel; A–B and F–G are sequential.

### Phase A — Honesty foundation (Days 1–30, £0)

Solidify what exists. Fix every "the dashboard says X but actually Y" gap. No new features.

| Action | Why |
|---|---|
| Operator hygiene: rotate `ARIA_INTERNAL_TOKEN`, top up Brave, set `REPORT_SIGNING_KEY`, set graceful-degrade env vars (R-F0..F8 from session pickup memory) | every public-facing claim must be backed by working code |
| Run `/api/aria/knowledge/seed-latam-asia` — lifts the 51% heatmap floor | regression-floor before going public |
| Investigate the 3 quarantined DDs | unresolved quarantines = ticking trust bomb |
| Reject 10 stale amendments + check adversarial regressions | clean queue = clean conscience |
| Fly logs: zero ERROR-level entries for 7 consecutive days | uptime baseline before any customer touches the system |

**Exit gate (Day 30)**:
- Composite score ≥ 71% sustained
- Heatmap weakest cell ≥ 70% (was 51%)
- 0 fly ERROR logs in last 7 days
- All quarantined DDs investigated + closed
- All operator-pending env vars set

### Phase B — Sovereign baseline (Days 30–60, £0–100)

Prove the sovereign-LLM pipeline works end-to-end on cheap compute. Don't go straight to 70B — prove the pipeline first.

| Action | How |
|---|---|
| Run **ARIA-LLM v0.0** — 8B fine-tune on RunPod (operator signed up 2026-05-10) | follows runbook in `memory/runpod_signed_up.md` |
| Use Mistral-7B-Instruct base (open weights, no HF gating, fits 1× A100 80GB) | first SFT + DPO + eval cycle ~$8-25 |
| Stand up vLLM inference on a smaller pod | can run on-demand, not 24/7 |
| Set `ARIA_LLM_URL` on fly secrets — sovereign tier auto-activates in fallback chain | dormant code already wired |
| Run the 11-attack adversarial suite against ARIA-LLM v0.0 | establishes regression baseline |
| Begin SOC 2 observation period (no auditor yet, just self-tracking control evidence) | starts the 6-month clock so Phase F lands on schedule |

**Exit gate (Day 60)**:
- ARIA-LLM v0.0 passes ≥ 80% of internal eval suite
- Air-gap chat test passes (Anthropic disabled → ARIA still serves)
- Adversarial pass rate within 5pp of Anthropic baseline
- SOC 2 observation evidence-collection started
- Total fine-tune spend < £100

### Phase C — Trust foundation (Days 60–120, £8–15k)

Build the trust layer that gates every enterprise sale. **This is non-negotiable before charging real money to defence customers.**

| Action | Estimate |
|---|---|
| **Engage UK DPO + DPIA** for the platform | £4–8k legal + £2–4k DPO retainer Y1 |
| **CHECK/CREST pen test scoping** — defer full pen test to Phase E | £0 to scope |
| **Professional indemnity insurance** — Hiscox / AIG defence-tech specialty | £2–5k/yr; non-negotiable for selling DDs |
| **ToS / EULA / Privacy Notice / Refund Policy** drafted by counsel | £1–3k one-off |
| **Tamper-proof audit log hardening** — extend R-F75 provenance + R-F43 HMAC sign every report at write-time | engineering: 1–2 weeks |
| **Independent disaster recovery test** — kill fly machine, restore from `/data` snapshot + email backup | engineering: 1 week |
| **Public model card** at `/model-card.html` (already shipped) — declare every model + every limit + every bias the platform knows about | governance |
| **Public adversarial scoreboard** — `/adversarial.html` — show pass rate, failed attacks, when each was last validated | radical transparency = competitive moat |
| **ICO registration** + privacy controller appointment | £35/yr ICO fee + DPO covered above |

**Exit gate (Day 120)**:
- DPIA signed off
- Insurance bound (cert in hand)
- Pen test scoped + booked for Phase E
- Audit log tamper-proof (HMAC verified end-to-end)
- DR test passes (full restore < 60 min)
- Public model card published
- Public adversarial scoreboard live
- All terms documents reviewed by counsel

### Phase D — Distribution (Days 90–180, £5–10k, parallel to C)

How real customers find ARIA, try it, buy it, and stay.

| Action | Notes |
|---|---|
| **Stripe activation done properly** (not "flip 4 env vars") | webhook + customer DB + billing portal + UK VAT + dunning + refund flow + tax receipt — 1 week dev + £1–2k legal |
| **3 design partners onboarded at zero cost** (Arkmurus's own network: brokers, compliance officers, defence procurement leads) | gated on Phase C trust artefacts; 90-day pilot, evergreen feedback contract |
| **2 anonymised case studies** from Arkmurus's own DD work (with consent) | concrete evidence — not "AI for defence DD" but "ARIA spotted X in case Y, saved Z hours" |
| **Mobile-responsive dashboard** (R-F127 helped, but full audit) | every customer touches mobile at some point |
| **Public API beta** with 3 design partners (R-F42 scaffold + auth + rate limit + versioning) | API revenue is the asymmetric upside |
| **Direct outreach: 50 contacts** in 90 days (compliance officers + procurement leads + defence brokers) | sales is unromantic but it pays the rent |
| **A simple pricing page** at `/pricing.html` — Pro £20/mo / Pro Intel £199/mo / Enterprise on application | hide nothing |
| **Public uptime page + status SLA** | enterprise procurement asks for this on day 1 |
| **One conference: DSEI September** | physical presence, 50 leads target |

**Exit gate (Day 180)**:
- 3+ paying Pro Intel customers (£597+/mo recurring)
- 1+ enterprise pilot in active discussion
- Public API: 100+ calls / day from external IPs
- 2 case studies published
- 50 outbound contacts logged in CRM
- DSEI presence booked
- ≥ 95% uptime over last 90 days

### Phase E — Sovereign release (Days 180–270, £500–2k, funded by Phase D revenue)

ARIA-LLM v1.0 ships. The Phase 4 of the budget roadmap, but only after the 3-customer gate passes.

| Action | Estimate |
|---|---|
| **70B QLoRA fine-tune** on Llama-3.3-70B-Instruct via RunPod | £40–100 compute (per assessment of budget roadmap, 70B QLoRA on 5K pairs is achievable on a 4× A100 cluster for hours) |
| **vLLM inference on reserved RunPod A100 80GB** | £400–600/mo serving (covered by 3+ Pro Intel × £199 = £597/mo) |
| **Air-gap production validation** — kill all external LLM providers; run ARIA-LLM v1.0 standalone for 24h on production traffic | the moat: ARIA serves customers without an external LLM dependency |
| **CHECK/CREST pen test** (booked in Phase C) | £8–15k |
| **Pen test remediation** | scope dependent; budget £2–5k |
| **Customer-data isolation** — verify each Pro Intel customer's chat audit + DD reports + RAG queries are scoped to their tenant | multi-tenant hardening |
| **White-label DD report option** for Enterprise tier — operator's logo + brand on the PDF | enterprise differentiator |

**Exit gate (Day 270)**:
- ARIA-LLM v1.0 passes ≥ 90% adversarial suite + ≥ 90% A/B vs Anthropic on customer-facing chat
- Air-gap production test passes
- Pen test report received + critical/high findings remediated
- 5+ paying customers
- 1 enterprise contract signed (or in legal review)
- Multi-tenant data isolation independently verified

### Phase F — Industry trust (Days 270–365, £20–30k)

The certifications that turn ARIA from "we are trustworthy" into "we are independently certified to be trustworthy."

| Action | Estimate |
|---|---|
| **SOC 2 Type II audit** — 6-month observation completes Day ~240, audit Day 240–300 | £15–25k auditor fees |
| **ISO 27001 gap analysis** (internal) → readiness audit → certification | £10–20k Y1, less for Y2+ |
| **Independent third-party adversarial benchmark** — invite a defence-OSINT firm to red-team ARIA, publish results | £5–10k engagement, priceless reputational evidence |
| **First strategic partnership** — NATO industry forum / SIPRI affiliation / EDA observer status / similar | non-monetary, opens doors |
| **Multi-language dashboard** — Portuguese (Lusophone moat) + Arabic (MENA) + Turkish | shows "we are global" |
| **Customer success function** — at least one part-time CS person handling tickets, onboarding, renewals | ~£2k/mo when revenue justifies |

**Exit gate (Day 365)**:
- SOC 2 Type II report received
- ISO 27001 path on track (not necessarily certified yet)
- Independent benchmark published
- Strategic affiliation announced
- 10+ paying customers
- ≥ 99% uptime over 12 months
- £30k+ MRR

### Phase G — Industry standard (Day 365+)

ARIA stops being a vendor and becomes infrastructure.

| Action | Notes |
|---|---|
| **Open-source the ARK-DD methodology** as a published standard (e.g. ARIADD-23) | lock in methodology IP defensively while becoming the industry reference |
| **Publish the 23-clause constitution** as a defence-AI governance standard | white paper, conference talks, regulator briefings |
| **Public adversarial leaderboard** — invite competitors to publish on the same suite | radical-transparency moat |
| **Annual industry report** — "State of Defence DD AI 2027" | content marketing flywheel |
| **Reference customer programme** — anonymised customer stories, regulator briefings, expert testimony | each reference deepens the moat |
| **Mobile native app** (iOS/Android via Capacitor) | enterprise customer expectation |
| **Public conference: ARIA Defence DD Summit** Year 2 | own the conversation |

**Exit gate (Day 730)**:
- ARK-DD methodology cited by 3+ regulators / industry bodies
- 25+ paying customers
- ≥ 1 customer-published case study showing ARIA caught what humans missed
- ARIA-LLM v2.0 released
- £150k+ MRR

---

## The robustness pillars (built into every phase)

Robust ≠ slow. Robust = nothing collapses when one piece fails.

| Pillar | Implementation | Phase |
|---|---|---|
| **Multi-vendor LLM** | 4-path chain already live: Anthropic → DeepSeek → Groq → ARIA-LLM | done |
| **Disk-first persistence** | `/data/aria_*.json` canonical; Redis is mirror | done |
| **Daily off-host backup** | email backup to operator inbox | done (verify monthly) |
| **Tamper-proof audit log** | HMAC + provenance per fact | C (hardening) |
| **Customer-side encryption** | each Pro Intel tenant has its own data-at-rest key | E |
| **Disaster recovery plan** | tested fly machine wipe + restore | C |
| **SLA + status page** | public uptime + incident history | D |
| **Versioned API** | `/api/v1/...`, `/api/v2/...`; deprecate gracefully | D |
| **Adversarial regression in CI** | the 11-attack suite runs on every PR; fail blocks merge | C |
| **Independent audit** | SOC 2 Type II + ISO 27001 + pen test | F |
| **Constitutional adherence audit** | every chat audit row stamps clauses checked | done |
| **No single-point dependency** | 3 LLM providers + 8 search backends + 188 sources | done |

If any one of these breaks, the platform doesn't fall over — it falls back. That's robust.

---

## The defensibility moat (what makes us un-displaceable)

These are the things competitors **cannot copy by spending money**:

| Moat | What it is | When it locks in |
|---|---|---|
| **Defence-DD specificity** | every layer, every clause, every source curated for defence | done |
| **Constitutional discipline (23 clauses)** | hallucination guards built into the prompt, audited per turn | done |
| **ARK-DD methodology IP** | 10-layer pipeline + ACH explainability + structured fail-open | done |
| **Lusophone + MENA + Turkey moat** | Arkmurus's domain advantage; competitors are NATO-default | done |
| **5-substrate brain + 100-year retention** | pay-once-remember-forever — competitors re-pay every query | done |
| **Sovereign LLM with constitutional weights** | regulatory + cost advantage no general LLM can replicate | E |
| **ARIADD-23 standard published** | by becoming the standard, we set the bar competitors are measured against | G |
| **Independently-audited adversarial pass rate** | published, third-party-validated, reproducible | F |
| **Reference customer testimony** | each customer story deepens defensibility | G |

Every quarter we ship one moat-deepening item. By Day 365, four are live. By Day 730, all nine.

---

## Honest risks (and pre-committed responses)

| Risk | Likelihood | Pre-committed response |
|---|---|---|
| Phase D fails to land 3 paying customers by Day 180 | medium | Compress Phase E — ship ARIA-LLM v0.0 at 8B as v1.0 instead of waiting for 70B; use the £100 internal budget. The moat ships even if revenue lags. |
| RunPod 70B fine-tune fails to converge on small corpus (<5K pairs) | medium | Augment with synthetic pairs from chat audit; or run smaller (13B Mistral or Llama-3.1-8B fine-tune) for v1.0; promote to 70B in v1.1. |
| SOC 2 audit reveals control deficiencies | low–medium | Phase F has 90-day buffer for remediation; defer Phase G start by 60 days. |
| Pen test reveals critical findings | medium | Budget £5k remediation; pause Phase E sovereign release until critical/high closed. |
| Anthropic / DeepSeek pricing changes mid-Phase | low | Tier router auto-shifts to cheapest capable provider; sovereign tier deprecates dependency by Phase E anyway. |
| Insurance refused (defence-AI niche) | low | Hiscox / AIG / Beazley all do defence-tech; if all decline, Lloyd's syndicate via specialist broker. £2–5k floor. |
| Competitor (Janes / Kharon / Sayari) drops price | low–medium | Differentiation is methodology + sovereignty + Lusophone moat — can't be price-matched without replicating those. |
| Operator capacity (BD on top of running ARIA) | high | Phase F includes ≥1 customer success hire when revenue ≥ £30k MRR. |

---

## Day 1 actions (concrete next 7 days)

If the operator agrees to this trajectory, the first 7 days are:

1. **Today** — operator runs the 4 graceful-degrade env vars + the `POST /api/aria/knowledge/seed-latam-asia` curl. Free, 30 min.
2. **Day 2** — operator boots a small RunPod pod (Community Cloud, A100 80GB, ~$2/hr) and runs `prepare_sft.py` against the existing 280-pair training corpus. Even at small scale this validates the harness. Cost ~$5.
3. **Day 3-4** — operator decides Phase B base model (Mistral-7B vs Llama-3.1-8B); runs first SFT cycle. ~$10-15.
4. **Day 5** — first eval — does ARIA-LLM v0.0 work at all? If yes, deploy vLLM. If no, debug.
5. **Day 6** — operator engages UK DPO via referral (free initial conversation). Begin DPIA scoping.
6. **Day 7** — review week's progress + commit to Phase B exit gate criteria. If Phase B looks achievable in 60 days at <£100, proceed. If not, re-plan.

These 7 days cost <£20 and lock in or kill Phase B before any meaningful spend.

---

## What this is NOT

- **Not a sales projection.** We do not assume any customer will sign. Every revenue assumption is gated.
- **Not a 12-month commitment.** Each phase has its own go/no-go.
- **Not a fundraising pitch.** This is a self-funded, revenue-funded, capital-disciplined plan.
- **Not feature-creep.** Every phase ships ≤ 5 things. The temptation to add a sixth is the temptation to fail.

---

## Bottom line

ARIA already has the four highest-leverage moats today:
1. Defence-DD specific 10-layer pipeline (done)
2. Constitutional discipline (done)
3. 5-substrate memory + pay-once-remember-forever (done)
4. Sovereign-LLM path coded + RunPod signed up (done)

What stands between today and "the platform" is **trust + distribution + audit + scale** — Phases C through G. None of them are technically hard. All of them require discipline and sequence.

If we build A through G in order, with honest gates between each, ARIA is **the platform** by Day 365 — independently audited, customer-validated, methodology-published, sovereign-LLM-deployed, insurance-bound, ICO-registered, SOC 2 Type II reported.

If we skip a phase or fudge a gate, we will eventually fail an audit, lose a customer, or hit a regulatory wall — and rebuilding from there costs more than doing it in order from the start.

**The plan is simple. The discipline is the work.**
