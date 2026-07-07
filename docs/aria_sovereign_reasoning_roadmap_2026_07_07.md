# ARIA Sovereign Reasoning Roadmap

**Date:** 2026-07-07  
**Status:** execution roadmap  
**Purpose:** make ARIA's sovereign reasoning ambition real without pretending a
small model is already a frontier model.

## North Star

ARIA becomes a sovereign defence-intelligence reasoning platform by compounding
three assets:

1. **Specialist memory:** RAG, DD vault, sanctions, procurement, source health,
   mistake ledger, and verified training corpora.
2. **Sovereign model:** an ARIA-owned model trained on grounded compliance,
   defence DD, citation discipline, refusal discipline, and Arkmurus workflows.
3. **Promotion gates:** ARIA serves from her own model only where measured
   evidence proves it is safe and useful; DeepSeek remains fallback until ARIA
   earns broader scope.

The objective is not "flip away from DeepSeek". The objective is to make
DeepSeek a teacher, fallback, and comparator while ARIA wins more routed tasks
over time.

## Current Truth

Verified local code state as of R-F2400:

- `aria_service/llm/model_router.py` has a two-track sovereign router.
- `ARIA_LLM_URL` configures a sovereign endpoint.
- `ARIA_LLM_PROMOTION_STAGE` gates serving:
  - `shadow`: generate sovereign output, ship DeepSeek.
  - `canary`: serve a controlled percentage of grounded turns.
  - `serve`: serve all grounded synthesis turns, with DeepSeek fallback.
  - `off`: keep endpoint configured but force DeepSeek.
- Closed-book/general reasoning stays DeepSeek until evals prove otherwise.
- Coder remains pinned to DeepSeek until a separate coder eval is passed.

## Training Flywheel

Every sovereign model iteration must follow this loop:

1. **Collect**
   - DD reports with source-backed findings.
   - Grounded chat turns with RAG/source context.
   - Sanctions/procurement/legal QA pairs.
   - Refusal/honesty failures from mistake ledger and chat audit.
   - Coder gold only when tests genuinely ran and passed.

2. **Filter**
   - Keep only examples with provenance, source context, and expected answer.
   - Reject stale officeholder/current-news examples unless date-scoped.
   - Reject any answer with fabricated citations or unsupported claims.
   - Separate "answerable" from "must abstain" examples.

3. **Train**
   - SFT on grounded specialist answers.
   - DPO/GRPO on preference pairs using `grounding_reward.score()`.
   - Refusal tuning for fake tool actions, fabricated IDs, premise injection,
     outdated officeholders, and no-data risk profiles.
   - Domain packs for sanctions, export controls, defence procurement, DD
     layers, counter-intel, and multilingual compliance.

4. **Evaluate**
   - Frozen 500-Q eval.
   - Grounded open-book eval.
   - DD quality eval.
   - Sanctions divergence eval.
   - Refusal/honesty eval.
   - Multilingual compliance eval.
   - Live shadow comparison against DeepSeek on the same grounded turns.

5. **Promote or Retrain**
   - Promote only if absolute and relative gates pass.
   - If not, produce category gap analysis, generate targeted training data,
     retrain, and rerun.

## Promotion Ladder

### Stage 0: DeepSeek Teacher

Default today. DeepSeek serves users. ARIA captures grounded turns, source
contexts, mistakes, and eval deltas.

Exit criteria:

- Training export contains enough grounded examples per category.
- Frozen eval and grounded reward harness are green.
- Source-verifier rejects fabricated citations deterministically.

### Stage 1: Sovereign Shadow

Set:

```text
ARIA_LLM_URL=<sovereign endpoint>
ARIA_LLM_PROMOTION_STAGE=shadow
```

Behavior:

- DeepSeek serves users.
- Sovereign generates alongside on grounded synthesis turns.
- Shadow metrics compare grounded rate, citation precision, abstention
  correctness, latency, and failure rate.

Exit criteria to canary:

- Sovereign grounded score >= DeepSeek on same live shadow turns.
- Fabricated citation rate <= DeepSeek.
- Refusal accuracy >= 0.90 on refusal subset.
- p95 sovereign latency <= 1.5x DeepSeek for grounded turns.
- Minimum sample size: 100 grounded shadow turns or a frozen eval run with at
  least 100 comparable grounded samples.

### Stage 2: Sovereign Canary

Set:

```text
ARIA_LLM_PROMOTION_STAGE=canary
ARIA_LLM_CANARY_PCT=10
```

Ramp:

- 10 percent -> 25 percent -> 50 percent -> 100 percent of grounded synthesis.
- Each ramp requires at least 24h of logs or 100 routed grounded turns.

Blockers:

- Any fabricated citation regression.
- Any sanctions/DD hallucination regression.
- p95 latency above threshold for two consecutive windows.
- Increased user-visible fallback/error rate.

### Stage 3: Grounded Serve

Set:

```text
ARIA_LLM_PROMOTION_STAGE=serve
```

Scope:

- Sovereign serves grounded synthesis only.
- DeepSeek remains fallback.
- General closed-book, broad reasoning, and coder remain DeepSeek.

Exit criteria to broader chat:

- 7 consecutive daily evals pass.
- Fresh DeepSeek baseline exists for same instrument.
- Sovereign >= 90 percent of DeepSeek overall and no category below 75 percent
  of DeepSeek.
- Overall score >= 0.70.

### Stage 4: Chat Primary

Scope:

- Sovereign handles chat categories it has proven.
- DeepSeek fallback remains enabled.
- High-risk DD still requires source verification and fallback if citations fail.

Blockers:

- Any category below promotion floor.
- Any material finding without source/confidence.
- Any recurring stale-current event from memory/cache.

### Stage 5: Coder Sovereignty

Coder is last, not first.

Requirements:

- Separate coding eval.
- Real fix loop: reproduce red -> patch -> tests green -> stage/deploy gate.
- Hallucinated API gate hard-blocks generated code.
- Capability-test gate blocks auto-deploy without genuine FAIL->PASS.
- Operator approval required for first coder promotion.

## Data Products Required

| Dataset | Purpose | Minimum viable size |
| --- | --- | --- |
| Grounded DD SFT | Teaches report structure and evidence-backed findings | 1,000 examples |
| Citation DPO | Teaches cite-or-abstain discipline | 2,000 preference pairs |
| Sanctions divergence | Teaches cross-list conflict handling | 500 examples |
| Procurement intelligence | Teaches market/opportunity reasoning | 750 examples |
| Refusal and honesty | Prevents fabricated actions, IDs, sources, and stale claims | 1,000 examples |
| Multilingual compliance | PT/FR/AR/ES/TR/PL/DE/RU/ZH/SW | 1,000 examples |
| Coder gold | Teaches verified code repair | only gold=True rows |

## Dashboard Requirements

Expose these on dashboard/brain surfaces before full promotion:

- Current promotion stage.
- Sovereign configured: yes/no.
- Shadow sample size.
- Sovereign vs DeepSeek grounded score.
- Fabricated citation rate.
- Refusal accuracy.
- p95 sovereign latency.
- Fallback count and reason.
- Last eval run and pass/fail.
- Next blocked category.

## Non-Negotiable Rules

- Never promote from a small sample.
- Never use a human-edited roadmap as proof of status; use eval output or live
  route metrics.
- Never serve sovereign output for current/high-risk claims without evidence.
- Never replace DeepSeek for coder until coding evals and real FAIL->PASS loops
  prove it.
- Never call ARIA "fully independent" until the dashboard shows the percentage
  of served turns by sovereign model and the quality gates backing it.

## Immediate Build Sequence

1. R-F2400: safe promotion gate in `model_router.py`.
2. Add a live promotion-status endpoint exposing router state and recent shadow
   metrics.
3. Build a shadow-metrics ledger from `_log_shadow()` instead of only wiring a
   summary.
4. Add dashboard panel for sovereign progress.
5. Run a fresh DeepSeek baseline and sovereign shadow eval on the same frozen
   set.
6. Generate targeted training data for the lowest categories.
7. Retrain v0.2/v0.3, rerun eval, and only then promote.

