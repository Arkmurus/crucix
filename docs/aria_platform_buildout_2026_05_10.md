# ARIA — Platform Build-Out Plan (Robust, Sequenced, Gated)

**Date**: 2026-05-10 (revised same day with integrated 8-point review)
**HEAD**: `a5110ad`+
**Goal as stated by operator**: *"make ARIA the AI + LLM + Compliance platform to use"*
**Anchor docs**: this builds on `aria_full_architecture_2026_05_10.md` (what is), `aria_capability_expansion_roadmap_2026_05_10.md` (what's next technically), and `aria_budget_roadmap.html` (financial discipline). This document covers the **trust + distribution + scale + defensibility** layer that turns "internal tool" into "platform of record."

**Revision note (same-day integration)**: applied 8 corrections after operator + external review of v1: (1) Phase C legal padded to £15-22k, (2) design partner conversations moved to Phase A/C, (3) ARIA-LLM licensing decision pulled to Phase B Day 1, (4) 500-question eval set construction moved to Phase A, (5) DSEI re-scoped to badge-only, (6) ISO 27001 moved to Phase G, (7) Phase F revenue gate made explicit with fallback structure, (8) ARK-DD methodology white-paper publication moved to Phase C/D. Free compute options + operator time budget added.

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

### Phase A — Honesty foundation (Days 1–30, £0, ~5h operator time)

Solidify what exists. Fix every "the dashboard says X but actually Y" gap. No new features.

| Action | Why |
|---|---|
| Operator hygiene: rotate `ARIA_INTERNAL_TOKEN`, top up Brave, set `REPORT_SIGNING_KEY`, set graceful-degrade env vars | every public-facing claim must be backed by working code |
| Run `/api/aria/knowledge/seed-latam-asia` — lifts the 51% heatmap floor | regression-floor before going public |
| Investigate the 3 quarantined DDs | unresolved quarantines = ticking trust bomb |
| Reject 10 stale amendments + check adversarial regressions | clean queue = clean conscience |
| Fly logs: zero ERROR-level entries for 7 consecutive days | uptime baseline before any customer touches the system |
| **Build the 500-question evaluation set** (covers the 23 clauses, the 10 DD layers, sanctions divergence, counter-intel, multi-language, refusal scenarios) | Phase B exit gate ("ARIA-LLM v0.0 ≥80% eval pass") cannot pass if the eval set doesn't exist when training completes — Phase A has the capacity to build this in parallel |
| **Begin design partner relationship conversations** (no platform access yet — relationships only) | B2B compliance sales cycle is 3–6 months. By Phase D start, 6–8 warm leads should exist. Platform access (real DD on real counterparties) gates on Phase C exit |
| **Decide design partner pilot terms** in writing (90-day pilot, evergreen feedback contract, no payment, NDA-protected counterparty data) | locks in the structure before Phase C starts so Phase C engagement is fast |

**Exit gate (Day 30)**:
- Composite score ≥ 71% sustained
- Heatmap weakest cell ≥ 70% (was 51%)
- 0 fly ERROR logs in last 7 days
- All quarantined DDs investigated + closed
- All operator-pending env vars set
- 500-question evaluation set v1 frozen
- ≥ 4 design partner relationship conversations underway

### Phase B — Sovereign baseline (Days 30–60, £0–100, ~12h operator time + RunPod management)

Prove the sovereign-LLM pipeline works end-to-end on cheap compute. Don't go straight to 70B — prove the pipeline first.

**🔴 Phase B Day 1 — LICENSING DECISION (must precede first fine-tune run)**

Decide explicitly: open-weights or proprietary for ARIA-LLM. This is binary and irreversible after the first training run. Why it cannot be deferred:

- If **proprietary** (closed weights, never released): customer chat audit + RAG content can be in the training corpus subject to ToS. Standard B2B SaaS posture.
- If **open** (now or ever): customer PII, identifiable counterparty content, NDA-protected DD findings **must be scrubbed from the corpus before training starts.** Adding a customer-data scrubber after the model is trained does not retroactively un-train it.

**Default recommendation**: proprietary for v0.0, v0.1, v1.0 — keep weights closed, methodology open. Revisit at Phase G when the methodology has citations and the open-weights release becomes a positioning move rather than a risk.

| Action | How |
|---|---|
| **Run ARIA-LLM v0.0** — 8B fine-tune on free or near-free compute first | follows runbook in `memory/runpod_signed_up.md` |
| **Choose Phase B compute**: try free first (Kaggle 30h/wk T4/P100) → graduate to Colab Pro+ £10/mo if needed → spot RunPod/Vast.ai (£0.20-0.50/hr A100) only if compute-bound | **first cycle can hit £0** if Kaggle handles the 8B QLoRA on the small corpus |
| Use Mistral-7B-Instruct-v0.3 base (open weights, no HF gating, fits 1× A100 80GB or 1× P100 16GB at QLoRA) | unblocks all free compute paths |
| Stand up vLLM inference on a smaller pod (on-demand, not 24/7) | inference cost scales with use, not idle time |
| Set `ARIA_LLM_URL` on fly secrets — sovereign tier auto-activates in fallback chain | dormant code already wired |
| Run the 11-attack adversarial suite + the 500-question eval set (built in Phase A) against ARIA-LLM v0.0 | establishes regression baseline |
| Begin SOC 2 observation period (no auditor yet, just self-tracking control evidence) | starts the 6-month clock so Phase F lands on schedule |

**Exit gate (Day 60)**:
- ARIA-LLM v0.0 passes ≥ 80% of the 500-question eval suite (built in Phase A)
- Air-gap chat test passes (Anthropic disabled → ARIA still serves)
- Adversarial pass rate within 5pp of Anthropic baseline
- SOC 2 observation evidence-collection started
- Licensing decision recorded in writing
- Total fine-tune spend < £100

### Phase C — Trust foundation (Days 60–120, £15–22k, ~25h operator time + legal coordination)

Build the trust layer that gates every enterprise sale. **This is non-negotiable before charging real money to defence customers.**

| Action | Estimate |
|---|---|
| **Engage UK DPO + DPIA** for a defence-AI platform (specialist counsel — counterparty intel + named-individual data + cross-border defence transfers) | **£8–12k** (revised up — defence-AI niche complexity, more specialist than general commercial) |
| **Professional indemnity insurance** — Hiscox / AIG / Beazley defence-tech specialty | £2–5k/yr; non-negotiable for selling DDs |
| **ToS / EULA / Privacy Notice / Refund Policy** drafted by specialist counsel | **£3–5k** (revised up — defence-AI specialist rate, not general commercial) |
| **CHECK/CREST pen test scoping** — defer full pen test to Phase E | £0 to scope |
| **Tamper-proof audit log hardening** — extend R-F75 provenance + R-F43 HMAC sign every report at write-time | engineering: 1–2 weeks |
| **Customer-side encryption design**: envelope encryption — per-tenant Data Encryption Key (DEK) wrapped by a platform Key Encryption Key (KEK) stored in Fly.io secrets. Document key rotation, customer-loses-key recovery, KEK custodian | engineering: 1 week design, deferred implementation to Phase E multi-tenant hardening |
| **Independent disaster recovery test** — kill fly machine, restore from `/data` snapshot + email backup | engineering: 1 week |
| **Public model card** at `/model-card.html` — declare every model + every limit + every bias the platform knows about | governance |
| **Public adversarial scoreboard** — show **only the pass rate as a number** (e.g. "ARIA passes 89% of internal adversarial suite v3, last run 2026-05-08"). **Do NOT publish the test prompts** — that invites adversarial training against the specific suite and makes the score meaningless | the score is the moat; the prompts stay proprietary |
| **ICO registration** + privacy controller appointment | £35/yr ICO fee + DPO covered above |
| **Publish ARK-DD methodology white paper as "Arkmurus ARK-DD Framework v1.0"** — 10-layer pipeline + 23-clause constitution + ACH explainability + composable DD endpoint design | this is the most powerful tool for shortening the enterprise sales cycle — gives procurement teams something to evaluate against existing vendors before any sales conversation. Promote to "industry standard" in Phase G when citations accrue |
| **Develop 2–3 anonymised case studies** from existing Arkmurus DD operations (with consent) — concrete "ARIA spotted X in case Y, saved Z hours" | published before Phase D outreach begins so the first sales conversation has proof of value, not a capability claim |

**Exit gate (Day 120)**:
- DPIA signed off
- Insurance bound (cert in hand)
- Pen test scoped + booked for Phase E
- Audit log tamper-proof (HMAC verified end-to-end)
- Customer-side encryption design documented (implementation deferred to Phase E)
- DR test passes (full restore < 60 min)
- Public model card published
- Public adversarial scoreboard live (score-only, prompts proprietary)
- All terms documents reviewed by counsel
- ARK-DD Framework v1.0 white paper published
- ≥ 2 anonymised case studies published
- ≥ 6 design partner relationships warm (relationships → platform access transitions here)

### Phase D — Distribution (Days 90–180, £5–10k, parallel to C, ~3–4h/week sustained operator time)

How real customers find ARIA, try it, buy it, and stay.

**Critical sequencing**: design partner *relationships* started in Phase A, *platform access* (real DD on real counterparties) gates on Phase C exit (Day 120). Phase D begins active sales motion with 6–8 already-warm leads, not from cold.

| Action | Notes |
|---|---|
| **Stripe activation done properly** (not "flip 4 env vars") | webhook + customer DB + billing portal + UK VAT + dunning + refund flow + tax receipt — 1 week dev + £1–2k legal |
| **3 design partners onboarded with platform access** — drawn from the 6–8 warm Phase C leads | 90-day pilot, evergreen feedback contract |
| **Mobile-responsive dashboard** (R-F127 helped, but full audit) | every customer touches mobile at some point |
| **Public API beta** with 3 design partners (R-F42 scaffold + auth + rate limit + versioning) | API revenue is the asymmetric upside |
| **Direct outreach: 50 contacts** in 90 days (compliance officers + procurement leads + defence brokers) | sales is unromantic but it pays the rent. ~1 contact every 1.8 days on top of operator's other work — feasible if developer handles technical execution |
| **A simple pricing page** at `/pricing.html` — Pro £20/mo / Pro Intel £199/mo / Enterprise on application | hide nothing |
| **Public uptime page + status SLA** | enterprise procurement asks for this on day 1 |
| **DSEI September: badge-only attendance** (~£600), not a stand. Spend saved on targeted design-partner dinners + 1:1 meetings | DSEI 2026 is approximately Day 120-150 from a 2026-05-10 start — early Phase D. Badge attendance lets the operator do 30+ targeted meetings; a stand at £5-25k requires staffing the operator can't spare |

**Exit gate (Day 180)**:
- 3+ paying Pro Intel customers (£597+/mo recurring)
- 1+ enterprise pilot in active discussion
- Public API: 100+ calls / day from external IPs
- 50 outbound contacts logged in CRM
- DSEI badge attendance complete; ≥ 20 quality 1:1 meetings logged
- ≥ 95% uptime over last 90 days

### Phase E — Sovereign release (Days 180–270, £500–2k, funded by Phase D revenue, ~15h operator time + pen test coordination)

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

### Phase F — Industry trust (Days 270–365, ~12h operator time + audit coordination)

The certifications that turn ARIA from "we are trustworthy" into "we are independently certified to be trustworthy."

**🔴 Phase F revenue gate (HARD — explicit)**:

Phase F proceeds at full scope **only when accumulated Phase D + E gross margin covers Phase F costs**. The maths:

- 3 Pro Intel customers × £199/mo × 6 months (Day 180 → Day 360) = **£3,582 gross** — does NOT cover Phase F at £20-30k
- 10 customers × £199 × 6 months = **£11,940 gross** — partially covers Phase F at restructured £15-18k
- 15+ customers × £199 × 6 months = **£17,910+** — covers full-scope Phase F

**Restructured Phase F if revenue insufficient**: ship the audit floor only — DPIA renewal + insurance renewal + CHECK/CREST pen test (£10-12k total). **Defer SOC 2 Type II audit** until Phase D/E revenue justifies the auditor fees. The platform stays trustworthy (DPIA + insurance + pen test are the floor); SOC 2 ships when revenue allows.

**Full-scope Phase F (revenue ≥ £15k MRR sustained)**:

| Action | Estimate |
|---|---|
| **SOC 2 Type II audit** — 6-month observation completes Day ~240, audit Day 240–300 | £15–25k auditor fees |
| **Independent third-party adversarial benchmark** — invite a defence-OSINT firm to red-team ARIA, publish results | £5–10k engagement, priceless reputational evidence |
| **First strategic partnership** — NATO industry forum / SIPRI affiliation / EDA observer status / similar | non-monetary, opens doors |
| **Multi-language dashboard** — Portuguese (Lusophone moat) + Arabic (MENA) + Turkish | shows "we are global" |
| **Customer success function** — at least one part-time CS person handling tickets, onboarding, renewals | ~£2k/mo when revenue justifies |

**Note**: ISO 27001 moved to Phase G. Running ISO 27001 readiness + gap analysis + certification in the same 90-day window as a SOC 2 Type II audit is not achievable on a small operator team. SOC 2 alone is sufficient evidence for Year 1 enterprise sales; ISO 27001 lands in Year 2.

**Exit gate (Day 365)**:
- SOC 2 Type II report received (full-scope path) OR DPIA + insurance + pen test renewed (restructured path)
- Independent benchmark published
- Strategic affiliation announced
- 10+ paying customers
- ≥ 99% uptime over 12 months
- £15-30k+ MRR (depending on path)

### Phase G — Industry standard (Day 365+)

ARIA stops being a vendor and becomes infrastructure.

| Action | Notes |
|---|---|
| **Promote "Arkmurus ARK-DD Framework v1.0"** (published in Phase C) **to industry standard ARIADD-23** | by Day 365 the framework should have citations and customer evaluations — formalising as a standard locks in methodology IP defensively |
| **Publish the 23-clause constitution** as a defence-AI governance standard | white paper, conference talks, regulator briefings |
| **ISO 27001 readiness → certification** (moved from Phase F because parallel SOC 2 Type II + ISO 27001 is infeasible for a small team) | £10–20k Y2, less for Y3+. SOC 2 is sufficient evidence for Y1 enterprise sales; ISO 27001 is the Y2 lift |
| **Public adversarial leaderboard** — invite competitors to publish on the same suite **(only the score, not the prompts — same proprietary-prompt rule as Phase C)** | radical-transparency moat without inviting prompt-specific adversarial training |
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

## Operator time budget (the meta-constraint cash budgets miss)

Cash budgets are easy to track. Operator hours are the binding constraint when one person runs Arkmurus BD + ARIA platform + sales motion + legal coordination + financial management simultaneously.

| Phase | Operator time | Notes |
|---|---|---|
| **A** Honesty foundation | ~5h | env vars + curl + dashboard checks + eval-set construction |
| **B** Sovereign baseline | ~12h + RunPod management | pod boot, training kick-off, eval, vLLM deploy, fly secret |
| **C** Trust foundation | ~25h + legal coordination | DPO/counsel intake, DPIA Q&A, ToS review, white paper drafting, case study consents |
| **D** Distribution | ~3-4h/week sustained (~50h over 90 days) | 50 outbound contacts ≈ 1 every 1.8 days + design partner check-ins + DSEI prep |
| **E** Sovereign release | ~15h + pen test coordination | 70B fine-tune monitoring + air-gap validation + pen test scoping calls + remediation triage |
| **F** Industry trust | ~12h + audit coordination | SOC 2 evidence walk-through with auditor + benchmark engagement |
| **Total to Phase F** | **~120-150h over 9 months** | ≈ 3-4h/week average |

**Honest read**: this is feasible alongside Arkmurus BD work **if and only if the developer (Claude / future hire) handles all technical execution**. It becomes infeasible if the operator is also doing outbound sales, legal coordination, financial management, and platform operations simultaneously without delegation.

**Mitigation**: Phase F includes ≥1 part-time customer success person at £30k MRR. If revenue lands earlier, hire earlier — the operator's 3-4h/week is the binding constraint, not the cash.

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
