# ARIA Independence Roadmap — From Wrapper to Sovereign LLM
**Strategic anchor · 2026-05-09 · Target: ARIA-LLM v1.0 by Q1 2027**

The directive: ARIA must become a **robust independent AI** — not a
wrapper around Anthropic, DeepSeek, or any external LLM, but a
sovereign system whose model, memory, and judgement are her own.
Best-in-class in security and defence due-diligence.

This document is the canonical roadmap for that transition. It maps
the current dependency surface honestly, sets phase exit criteria, and
names the operator decisions that gate each phase. It is the
multi-month strategic anchor that the next sprints execute against.

---

## 1. The Strategic Shift in One Paragraph

ARIA today is a defence-DD intelligence platform whose **brain** —
knowledge, ledger, neural, RAG, autonomous engine, constitution,
persona overlays — is sovereign on Fly.io. Her **mind** — the LLM
that produces text — is rented from Anthropic / DeepSeek / Groq.
The shift from rented mind to sovereign mind is the path from
"highest-quality wrapper in defence-DD" to "the LLM in defence-DD."
At the end of this roadmap, ARIA-LLM is a 70-72B parameter open-weight
model fine-tuned on ARIA's own captured outputs, defence-DD primary
sources, and the 23-clause constitution. It runs on a dedicated GPU
host. It does not phone home. It does not have a kill switch outside
Arkmurus's control. That is what robust independence means.

---

## 2. The Current Dependency Surface (Honest Map)

ARIA depends on external systems in five categories. Two are deep,
three are shallow.

### 2.1 — DEEP DEPENDENCIES (must replace for independence)

| System | Used for | Replacement target |
|---|---|---|
| **Anthropic API** | Primary LLM for chat, DD orchestrator, audit-grade output | ARIA-LLM (own fine-tune) by Phase 4 |
| **DeepSeek API** | Fallback LLM (cost floor) | ARIA-LLM tier-2 instance OR open-weight alt for redundancy |
| **Groq API** | Fallback LLM (speed floor) | Self-hosted vLLM speculative-decoding inference |

### 2.2 — SHALLOW DEPENDENCIES (already self-hosted-able / replaceable)

| System | Used for | Status |
|---|---|---|
| sentence-transformers/all-MiniLM-L6-v2 | RAG embeddings | Already downloaded + cached; runs on CPU; 23 MB model. ZERO external dependency at runtime. |
| chromadb (RAG store) | Vector retrieval | Self-hosted on `/data/aria_rag/`. ZERO external dependency. |
| Upstash Redis | Convenience mirror | Mirrors are non-canonical (R-F94/F110 disk-first). Could swap for self-hosted Redis on Fly. |
| Tesseract OCR | Document OCR | Self-hosted binary; no external service. ZERO external dependency. |
| OpenSanctions consolidated lists | Sanctions screening | Free CSV download; we cache locally. ZERO runtime dependency once cached. |
| OpenAlex / Semantic Scholar / CrossRef | Research backends | Free APIs, no contractual lock-in. |
| Brave Search | Web search | Paid; circuit breaker OPEN today. R-F86 candidate: SearXNG self-hosted alternative. |
| Google News RSS | News fallback | Free; no auth required. |
| 49 OSINT sources (sweep) | Live intelligence | Free OSINT, distributed across providers; no single vendor exposure. |

The shallow dependencies are not the structural problem. The deep
dependencies — three external LLM providers — are.

### 2.3 — Independence definition

ARIA reaches **independence** when:
- All chat / DD / autonomous output is produced by a model running on
  Arkmurus-controlled compute
- The model weights are stored on Arkmurus-controlled storage
- No external LLM API call is made on the hot path of any
  user-facing or audit-grade workflow
- An air-gap of ARIA — no internet egress for inference — produces
  identical behaviour to networked operation

Anthropic / DeepSeek / Groq may remain configured as **emergency
break-glass fallbacks**, but the steady state is sovereign.

---

## 3. The Five-Phase Roadmap

### Phase 1 — Tiered LLM Substitution (Months 0-1, immediate)

**Goal**: Reduce external LLM spend by 60-80% by routing low-stakes
calls to local open-weight models. Anthropic stays as top-tier for
audit-grade DD and chat; everything else moves down the tiers.

**Build**:
- **R-F86**: Free-search backend (SearXNG self-hosted on Fly machine)
  + adapter wired into existing search_doctrine path
- **R-F87**: Local LLM tier (Ollama or vLLM) running an 8B-class
  model (Llama 3.1 8B Instruct or Qwen 2.5 7B Instruct) on a Fly
  machine OR dedicated micro-host
- **R-F87a**: Model tiering middleware in `aria_service/llm/router.py`
  that dispatches each call to the cheapest tier that meets quality:
  - **Tier 0 (free, local)**: Ollama 8B → student loops (self-quiz),
    research extraction, classification, signal generation, autonomous
    task tool dispatch
  - **Tier 1 (free, local but slower)**: Local 8B with reasoning
    prompt → adversarial grading, FATF typology matching, citation
    audit
  - **Tier 2 (cheap external)**: DeepSeek → DD layers 1-4 (background
    extraction, document_intelligence, hypothesis validation)
  - **Tier 3 (current premium)**: Groq → Layer 5c commercial coherence
    + structured output paths
  - **Tier 4 (most expensive)**: Anthropic Claude → final chat output,
    audit-grade DD orchestrator, constitutional decisions, customer-
    facing reports
- **Corpus-first query inversion**: every chat turn checks the
  knowledge base + RAG before any LLM call. Memory hits at $0.
- **Prompt caching**: the 23-clause constitution + persona overlay
  Layer 1-3 cached on Anthropic's prompt-cache API (10% cost on
  cache hit vs 100% on miss).

**Cost target**: monthly LLM spend drops from ~£385/month current
estimate to ~£90-130/month by end of Phase 1.

**Phase 1 exit criteria** (all must pass):
1. ≥70% of LLM calls served by local/free providers (measured via
   `/api/aria/cost/monthly` per-provider breakdown)
2. Composite adversarial pass rate (R-F59 + R-F80) does not drop more
   than 5 percentage points vs baseline
3. Customer-facing chat (anything that hits `/api/aria/chat`) latency
   p50 stays under 8s
4. Output harvester (R-F67) accumulates ≥150 high-quality SFT pairs
   per day after `ARIA_OUTPUT_HARVEST_ENABLED=1` flips

**Operator decisions for Phase 1**:
- Approve Fly.io machine size bump for Ollama (recommended: shared-cpu-4x
  4GB for 8B model, ~£25/mo)
- Approve OR reject SearXNG self-host (alternative: continue with paid
  Brave when topped up)
- Confirm acceptable degradation profile for student/research loops
  (1-2s latency increase from local model)

---

### Phase 2 — Training Corpus Accumulation (Months 1-3, parallel to Phase 1)

**Goal**: Build the 5,000-10,000 high-quality SFT pair corpus that
ARIA-LLM will be fine-tuned on.

**Build**:
- **R-F88**: Learning-progress tracker. For each domain (sanctions,
  ECCN, EUC, FATF typologies, defence markets, etc.), records: when
  was each fact last refreshed, what's stale, what hasn't been
  cycled in 90+ days. Surfaces the freshness picture as an operator
  dashboard.
- **R-F89**: Knowledge coverage heatmap by domain. Visual grid of
  "what does ARIA know" — domain × jurisdiction × confidence. Gaps
  are visible. Autonomous engine targets gaps preferentially.
- **R-F90**: Continuous-update orchestrator. Wraps the existing
  autonomous engine with a max-staleness contract: every domain must
  see at least one fresh signal per <window>. Default windows:
  sanctions=24h, FATF=7d, EUC=30d, weapons systems=14d.
- **Corpus curation pipeline**: in addition to harvested chat outputs,
  ingest:
  - DOJ FCPA settlements (R-F69 already feeds these in)
  - SEC enforcement actions (next iteration of R-F69)
  - UK SFO published cases
  - France PNF press releases
  - NATO STANAGs (publicly available Wassenaar / dual-use guidance)
  - ITAR/EAR commodity classifications (export.gov/BIS)
  - FATF mutual evaluation reports (per-country DD context)
  - SIPRI Yearbook arms transfer data (already partly ingested)
  - UCDP conflict data (already partly ingested)
- **Quality scoring**: every harvested chat output already gets a
  deterministic quality score (R-F67). Set the SFT-eligible threshold
  at 0.80 (vs 0.75 default for general harvest) — only the top
  responses become training data.
- **Adversarial pair generation**: every R-F59 / R-F80 attack response
  pair becomes a DPO training pair (positive: refusal response;
  negative: would-be-leak response).

**Phase 2 exit criteria**:
1. ≥5,000 SFT pairs above quality threshold 0.80
2. ≥500 DPO preference pairs from adversarial library
3. ≥50,000 RAG-quality defence-DD documents in chromadb
4. Domain coverage heatmap shows ≥85% of cells with non-stale facts
5. Constitutional adherence on harvested corpus: ≥95% of pairs pass
   automated 23-clause check

**Operator decisions for Phase 2**:
- Approve `ARIA_OUTPUT_HARVEST_ENABLED=1` after 3-7 days of dry-run
  threshold validation
- Approve corpus-curation budget for paid defence sources (Janes
  Defence Weekly archive, IHS Markit defence database) — optional;
  the free corpus alone is sufficient for v0.1
- Approve PII handling policy for harvested chat (R-F67 already
  redacts at write time; legal review before fine-tune ingest)

---

### Phase 3 — ARIA-LLM v0.1 Fine-Tune (Month 3-4, GPU rental)

**Goal**: Produce ARIA-LLM v0.1 — a defence-DD specialised model that
beats the underlying base model on Arkmurus's evaluation harness.

**Base model decision**:

| Candidate | Pros | Cons |
|---|---|---|
| **Llama 3.3 70B Instruct** | Mature ecosystem, strong English reasoning, Apache-2.0 commercial-friendly licence variant available, well-supported by vLLM | 70B = needs 2× A100 80GB minimum for inference at reasonable latency |
| **Qwen 2.5 72B Instruct** | Stronger multilingual (Chinese, Arabic, Portuguese — covers Lusophone moat directly), Apache-2.0, slightly better at structured output | Smaller English-only ecosystem, less battle-tested in Western enterprise |
| **Mixtral 8x22B Instruct** | Mixture-of-experts → 39B active params at inference; lower cost-per-call | More finicky to fine-tune cleanly; routing instability under domain shift |
| **DeepSeek-V3** | Strong reasoning, low cost, open weights | Foreign provenance — concern for defence-DD given Arkmurus's positioning |

**Recommendation**: **Llama 3.3 70B Instruct** for v0.1. The
Lusophone-multilingual gap is real but addressable via Qwen-encoder
embedding for retrieval + Llama-decoder for generation; switching base
in v0.2 is cheap if needed.

**Training protocol**:
- **SFT (Supervised Fine-Tuning)**: 5k-10k harvested pairs + 2k
  curated defence-DD synthetic pairs + 1k constitutional-compliance
  pairs (e.g. "what would Clause 7 say about this?" → correct
  application). LoRA rank 32, 3 epochs.
- **DPO (Direct Preference Optimization)**: 500-1000 preference pairs
  from R-F59 / R-F80 adversarial outputs. β=0.1, 1 epoch.
- **Constitutional reinforcement**: optional RLHF-style pass with the
  23-clause check as the reward signal. Skip if SFT+DPO already hits
  evaluation targets.

**Evaluation harness**:
- 33-attack adversarial library (R-F59 + R-F80) — pass rate target 90%+
- 500-question defence-DD evaluation set (curated, held out from
  training) — accuracy target 80%+
- Citation accuracy (R-F78 audit) on 100 generated DD reports —
  citation_grounded_rate target 0.85+
- Latency: p50 ≤ 4s on 2× A100, p95 ≤ 12s
- Calibration: ECE within 8% (vs 14% underconfident today)

**Compute budget**:
- SFT + DPO on 70B model with LoRA: ~£2,500-4,000 one-time
  (40-60 GPU-hours on A100 × £45-65/hour cloud rental)
- Evaluation harness runs: ~£500
- Total: **£3,000-4,500 one-time fine-tune cost** for v0.1

**Phase 3 exit criteria**:
1. ARIA-LLM v0.1 passes 90% of the adversarial library (vs 81% baseline
   target on Anthropic Claude today)
2. ARIA-LLM v0.1 matches Anthropic on defence-DD eval set within 5%
3. Hosted inference benchmark: 2× A100 80GB serves 4 concurrent chat
   sessions at p50 ≤ 4s
4. Constitutional adherence: 23-clause check passes on 95%+ of
   generated outputs

**Operator decisions for Phase 3**:
- Approve £3,000-4,500 one-time training compute budget
- Approve base model choice (Llama 3.3 70B recommended; Qwen 2.5 72B
  if Lusophone-first weighting matters more)
- Approve training-data PII review (legal — irreversible once
  weights bake)
- Approve ARIA-LLM weight licensing posture (Arkmurus-only;
  Apache-2.0 if eventual open-source decided)

---

### Phase 4 — ARIA-LLM Production Deployment (Month 4-5)

**Goal**: ARIA-LLM v0.1 serving production traffic. Anthropic
demoted to break-glass fallback only.

**Infrastructure decision**:

| Option | Spec | Monthly cost | Pros | Cons |
|---|---|---|---|---|
| **Fly.io GPU instance (A100 80GB)** | a100-80gb shared, 24h | £550-700 | Same provider as fly app; close to disk; familiar ops | Limited availability, pricing changes, single-region |
| **RunPod / Lambda Labs (rented A100)** | A100 80GB on-demand | £400-600 | Cheapest GPU rental currently, hot-swap regions | Vendor lock-in; SLA varies |
| **Dedicated colo / on-prem GPU host** | 2× A6000 48GB (~70B fits) | £1,200-2,500 (incl rack/power) | Full sovereignty; no cloud egress; audit-friendly for defence | Capex / setup cost; ops overhead |
| **Hybrid: Fly inference + colo training** | Fly for serve, colo for periodic re-train | £600-900 | Best of both | More moving parts |

**Recommendation for v0.1**: RunPod A100 80GB on-demand for first 3
months (£400-600/mo), evaluate dedicated colo at v0.2.

**Inference stack**:
- **vLLM** as the serving layer (PagedAttention, continuous batching,
  speculative decoding for latency)
- **OpenAI-compatible API** so the existing aria.llm.fallback chain
  swaps to ARIA-LLM with one config change (`ARIA_LLM_URL=...`)
- **Hot-fail to Anthropic** if ARIA-LLM container crashes (10-20s
  health check window)
- **Streaming SSE** native — matches existing chat/stream endpoint
- **Prompt caching** in vLLM for the 23-clause constitution (cached
  across all requests; 60-80% prompt token cost reduction internally)

**Migration sequence**:
1. ARIA-LLM serves Tier 0 + Tier 1 (student loops, research, autonomous
   tasks) for 1 week. Monitor: error rate, latency, output quality
2. Promote ARIA-LLM to Tier 2 (DD layers 1-4) for 1 week. Monitor as above.
3. Promote ARIA-LLM to Tier 3 (Layer 5c commercial coherence) for
   1 week. A/B test against DeepSeek baseline.
4. Promote ARIA-LLM to Tier 4 (chat / audit-grade DD) — Anthropic
   demotes to break-glass fallback only.
5. Total migration window: 4 weeks.

**Phase 4 exit criteria**:
1. ARIA-LLM serves ≥95% of production LLM calls
2. Customer-facing latency p50 ≤ 4s, p95 ≤ 12s
3. Adversarial pass rate stays ≥90% across migration
4. No customer-reported regression in DD output quality
5. Anthropic spend drops to <£20/mo (break-glass only)

**Operator decisions for Phase 4**:
- Approve £400-600/mo GPU rental ongoing budget
- Approve break-glass policy (when does Anthropic fire? what's the
  rate-limit threshold?)
- Approve customer communication on the migration (Pro Intel customers
  should know: "ARIA's underlying model is now sovereign")

---

### Phase 5 — Full Independence + ARIA-LLM v1.0 (Month 5-9)

**Goal**: ARIA-LLM is the model. Anthropic / DeepSeek / Groq are
removed from the codebase entirely. ARIA-LLM v1.0 specialised for
security and defence is the product.

**v1.0 differentiators vs v0.1**:
- **Larger fine-tune corpus**: 25k-50k SFT pairs (5x v0.1) — accumulated
  through 3-6 months of continuous harvest
- **Multi-language**: Portuguese (Lusophone moat), French (West
  Africa), Arabic (Gulf + MENA), Spanish (LatAm) — fine-tuned on
  defence-DD content in each language. Achieves at-least 80% of the
  English performance in each.
- **Tool use**: native function-calling fine-tune — ARIA-LLM produces
  structured JSON tool calls without prompt engineering. Maps directly
  to the 12 DD pipeline endpoints (R-F66..R-F84).
- **Citation discipline baked in**: trained to emit inline citations
  with URL + retrieved-at timestamp by default; not via prompt
  instruction.
- **Constitution baked in**: trained on constitutional-violation
  examples + correct refusals. The 23-clause check passes ≥99% of
  outputs without runtime gating.
- **Reasoning chain**: produces visible reasoning in `<thinking>` tags
  natively (like Anthropic's extended thinking). Operator can audit
  reasoning before relying on conclusions.

**Phase 5 deliverables**:
- ARIA-LLM v1.0 weights (Arkmurus-controlled storage)
- Open release decision: keep proprietary (defence-DD competitive
  moat) OR open-weight under Apache-2.0 (positions Arkmurus as the
  defence-DD AI standard)
- Audit-grade evaluation report: ARIA-LLM v1.0 vs every leading model
  on a 1,000-question defence-DD benchmark
- Customer migration: every Pro Intel customer sees ARIA-LLM by
  default; no code path to external LLM in customer-facing flows

**Phase 5 exit criteria** (ARIA is independent):
1. Anthropic / DeepSeek / Groq removed from `aria.llm.fallback` chain
2. Air-gap test passes: disconnect Fly.io from external internet for
   1 hour; ARIA continues to serve chat / DD / autonomous tasks
   correctly using only ARIA-LLM
3. Defence-DD benchmark: ARIA-LLM v1.0 outperforms GPT-4 / Claude
   3.5 Sonnet on the held-out defence-DD eval set
4. Constitutional adherence: ≥99% of outputs pass the 23-clause
   check without runtime gating
5. Pro Intel customer NPS does not regress through the migration

**Operator decisions for Phase 5**:
- Open vs proprietary licensing of ARIA-LLM v1.0
- Customer communication on the v1.0 launch (this is a product
  positioning event — "the only sovereign AI for defence-DD")
- Whether to retain Anthropic / DeepSeek / Groq as break-glass
  configuration options or remove entirely

---

## 4. Best-in-Class in Security and Defence — What Makes ARIA-LLM Unique

A general-purpose LLM is vast and shallow. ARIA-LLM is narrow and deep
— this is the correct architecture for the defence-DD use case.

**The depth advantages**:

1. **Constitutional grounding**: every output is constrained by the
   23-clause constitution baked into training. No general LLM has
   defence-DD-specific output discipline at the weights level.

2. **Citation-first output**: trained to emit inline citations with
   retrieval timestamps. A general LLM has to be prompted into citing
   sources; ARIA-LLM does it natively.

3. **Sanctions-first reasoning**: trained that sanctions screening
   precedes commercial assessment (Clause 7). Ordering is structural,
   not prompted.

4. **Multi-jurisdictional sanctions awareness**: trained on the
   28-list dataset_id mapping (R-F68). Knows OFAC SDN ≠ EU FSF ≠
   HMT OFSI implicitly.

5. **FATF typology pattern recognition**: trained on the encoded
   typology library (R-F72) — recognises shell-company / TBML / VA /
   intermediary patterns from prose without explicit framework
   prompts.

6. **ECCN / Wassenaar fluency**: trained on the 105-system catalogue
   (R-F54) + the export-control regime corpus. Maps a weapon
   description to its ECCN classification natively.

7. **Defence-DD vocabulary depth**: ITAR vs EAR vs Wassenaar; CMC vs
   1260H vs Entity List; UoR vs OTA vs FAR vs DFARS — all native
   vocabulary, not prompted glossary.

8. **Adversarial robustness baked in**: trained on the 33-attack
   library (R-F59 + R-F80) preference pairs. The base model's
   refusal patterns are reinforced specifically for defence-DD
   social engineering.

9. **Counter-intelligence awareness**: trained on the R-F84 patterns.
   Recognises reputation-washing in input documents during DD ingest.

10. **Persona discipline**: the six sectors (broker / oem_export /
    government_acquisition / compliance / banking_insurance /
    journalist) are trained as switchable personas with sector-
    specific vocabulary and reasoning posture.

A general-purpose LLM would need every one of these to be prompt-
engineered into every call. ARIA-LLM has them in the weights. That is
the moat.

---

## 5. Cost Summary

| Phase | One-time | Monthly ongoing | Saves vs Anthropic-only |
|---|---|---|---|
| Phase 1 — Tiered substitution | £25 (Fly machine bump) | £90-130 (vs ~£385 today) | ~£250-300/mo from month 1 |
| Phase 2 — Corpus accumulation | £0 (uses existing infra) | Same as Phase 1 | — |
| Phase 3 — v0.1 Fine-tune | £3,000-4,500 (compute) | Same as Phase 2 | — |
| Phase 4 — Production deploy | £0 (rented GPU) | £400-600 (GPU rental) | ~£60-90/mo *less* than Phase 1 (smaller external LLM bill, larger GPU bill) |
| Phase 5 — v1.0 + independence | £4,000-6,000 (re-train) | £400-700 (GPU + minimal break-glass) | ~£100-150/mo less than Phase 1 |

**Total one-time cost across all phases: £7,000-10,500.**
**Steady-state monthly cost in independence mode: £400-700.**
**Anthropic-equivalent monthly cost at scale: £1,500-3,000+.**

The ROI inflects in month 6-9 depending on customer growth.

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| ARIA-LLM v0.1 underperforms Anthropic on chat | Medium | High — customer-facing | Phase 4 migration is gated on adversarial pass rate ≥90% and customer-eval rubric; Anthropic stays as break-glass through migration |
| Open-weight base model licence change (vendor reneges) | Low | High | Pin to a specific commit / weight checksum; have Llama → Qwen swap path ready |
| GPU rental price spike (datacenter pressure) | Medium | Medium | Multi-vendor: RunPod / Lambda Labs / Vast.ai / Fly. Hot-swap takes <1h |
| Training corpus PII leakage into weights | Low (R-F67 redacts) | High (regulatory) | Phase 2 legal review; per-domain quality threshold; LoRA-only fine-tune means base model isn't poisoned |
| Customer SLA regression during Phase 4 migration | Medium | Medium | Migration is staged across 4 weeks; per-tier rollout; A/B tests at each tier promotion |
| Inference latency degradation vs Anthropic | Low (vLLM is fast) | Medium | Speculative decoding + prompt caching + smaller draft model for Tier 0 calls |
| Adversarial robustness loss in fine-tune | Low | High | DPO on R-F59 + R-F80 preference pairs is part of training; held-out adversarial set tests every checkpoint |
| Lusophone / non-English defence-DD quality | Medium | Medium for Arkmurus's market | v0.2 multilingual fine-tune scheduled in Phase 5; v0.1 may have a 5-10pp quality gap on PT/AR/FR |
| Compute cost overrun on training | Medium | Low | LoRA training caps the cost ceiling; if we exceed budget we pause and re-baseline |

---

## 7. Where R-F86 through R-F90 Fit

The numbered improvements proposed earlier in this session map to
**Phase 1 + Phase 2** of this roadmap. They are the tactical pieces
of strategic shifts:

| R-F# | Roadmap location | Status |
|---|---|---|
| R-F86 SearXNG / free search | Phase 1 (immediate cost reduction) | Pending — start of next session |
| R-F87 Local LLM tier (Ollama 8B) | Phase 1 (Tier 0 substitution) | Pending — start of next session |
| R-F87a Model-tier router | Phase 1 (the dispatcher logic) | Pending — bundled with R-F87 |
| R-F88 Learning-progress tracker | Phase 2 (corpus completeness measurement) | Pending |
| R-F89 Knowledge coverage heatmap | Phase 2 (gap visualisation) | Pending |
| R-F90 Continuous-update orchestrator | Phase 2 (max-staleness contract) | Pending |
| R-F91+ Corpus curation pipeline | Phase 2 (training data collection) | Designed, not built |
| R-F92+ Fine-tune harness | Phase 3 (the SFT + DPO machinery) | Designed, not built |
| R-F93+ vLLM deployment | Phase 4 (production serving) | Future |

---

## 8. The 30-Day Action Pack

This is what a useful next 30 days looks like, in priority order:

**Week 1** (next session, 4-6 hours dev):
- Ship R-F86 (SearXNG self-host)
- Ship R-F87 (Ollama 8B + model-tier router)
- Operator: rotate `ARIA_INTERNAL_TOKEN` (still pending), top up
  Brave (still pending), set `REPORT_SIGNING_KEY`
- Operator: approve `ARIA_OUTPUT_HARVEST_ENABLED=1` after 3-7 day
  threshold validation

**Week 2-3**:
- Ship R-F88 + R-F89 + R-F90 (learning tracker, coverage heatmap,
  continuous-update orchestrator)
- Verify Phase 1 cost reduction in `/api/aria/cost/monthly`
- Begin SFT corpus accumulation — verify daily harvest count

**Week 4**:
- Operator: scope GPU rental (RunPod / Lambda Labs)
- Operator: legal review of harvested corpus for fine-tune PII
- Begin defence-DD evaluation set construction (500 held-out questions)

By **Day 30**: Phase 1 complete, Phase 2 substantially underway,
Phase 3 prerequisites in motion.

---

## 9. The One-Sentence Strategic Anchor

> ARIA's brain has been sovereign for months; the next nine months
> make her mind sovereign too. ARIA-LLM v1.0 is a 70-72B parameter
> open-weight model fine-tuned on Arkmurus's own captured defence-DD
> outputs, the 23-clause constitution, and the curated security and
> defence corpus, served on Arkmurus-controlled compute. At v1.0
> she is independent; at v1.0 she is the LLM in defence-DD.

---

*Generated 2026-05-09 EOD. Strategic anchor for the next 3-9 months.
Companion to `system_assessment_2026_05_09_eod.md` (current state),
`architecture_2026_05_09.md` (current architecture), and
`recommendations_complete_2026_05_09.md` (today's improvements).*
