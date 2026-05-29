# ARIA — Reasoning + Coding Mastery + Own-LLM Roadmap (2026-05-29)

Authoritative roadmap. Author: Claude (review + plan) for the operator + ARIA.
Supersedes/extends `docs/aria_own_reasoning_review_2026_05_29.md`.

## 0. Mandate & invariants (operator's words — non-negotiable)
- ARIA is **free to code and reason with NO restrictions**, but must **never harm
  herself** (no auto-deploy of boot/constitution/guard files — R-F1040) and must
  **never hallucinate**.
- **INVARIANT A — free to think:** no cap on reasoning depth/steps/branches.
- **INVARIANT B — grounded or abstain:** every asserted fact must trace to REAL,
  verified evidence (RAG source w/ provenance, canonical-cache hit, live tool
  result, cited stored fact). Ungrounded → label assessment/unverified or abstain.
  Reasoning is free; *assertion* is gated.
- Goal: reasoning robust enough that **ARIA becomes her own LLM** (replaces DeepSeek),
  and coding knowledge/infrastructure good enough that **she can build anything asked**.

## 1. Current state (grounded — DONE / PENDING / MISSING)

### Reasoning
- **DONE (the tools/primitives):** `rag_store` (retrieval w/ source URL + provenance),
  `knowledge` (verified facts w/ source_attributions), `neural_memory.recall`
  (associative), `premise_verifier` (deterministic CONFIRMED/REFUTED/UNVERIFIABLE,
  pre-LLM), `honesty_judge` (LLM-as-judge of [CONFIRMED] claims, post-response),
  `reasoning_library` (case retrieval).
- **MISSING (the driver):** there is **no orchestrator** that, per query, gathers
  evidence → reasons over only verified evidence → verifies each claim inline →
  emits a per-claim evidence trace. Today a chat turn is a **single rented-LLM pass**
  with the verifiers wired as **pre/post gates**, not a loop. `honesty_judge` runs
  **after** the user already saw the answer → hallucinations are detected late, not
  prevented. `grounded_reasoner.py` does **not exist yet** (Task R1 below).

### Coding mastery / infrastructure
- **DONE:** the `aria` CLI is a real Claude-Code-style agent — read/write/edit/
  list/glob/grep/run + update_plan + fetch_url + ask_claude/check_claude; streaming
  never-silent UI (R-F1028/F1030); SOTA ENGINEERING STANDARD in the system prompt +
  AGENTS.md playbook; LLM-backed coder (SovereignLLM, R-F1025); bulletproofed loop
  (reserve cross-process lock R-F1026, run kill-tree R-F1027, no blocking brain-wire
  R-F1022); anti-self-harm auto-deploy guard (R-F1040).
- **PENDING (ARIA's own assessment, legit):** unit tests for `safety.py`,
  `self_coder.py`, `SovereignLLM`, `TestRunner`, `FlyDeployer`, `ClaudeReviewer`;
  populate-or-remove ~9 empty test files; WA listener `BRAIN_URL` dead default
  (`services/wa-listener/aria_wa_listener.mjs`); rename uppercase `ARIA.*` loggers.

### Own-LLM (path to sovereignty) — ~80% built
- **DONE:** training harness (`scripts/train/`: prepare_sft/dpo, sft_train, dpo_train,
  eval_aria_llm, adapt_chat_audit, runpod_train_pipeline.sh); **SFT adapter trained**
  (Mistral-7B + LoRA, on RunPod volume); data accumulation LIVE (output_harvester +
  daily training_export, ~150-200 pairs/day); `aria_llm_provider.py` adapter ready
  (one env var `ARIA_LLM_URL` inserts her model at the head of the chain);
  `llm_builder.py` (curation + config + script gen). 500-Q eval frozen.
- **PENDING / blockers:** DPO never run (need ≥100 preference pairs from R-F59/R-F80
  adversarial runs); eval never run; `eval_aria_llm.py` does NOT yet measure
  grounded_rate; `llm_builder.evaluate_model()` is a stub (always passes); Phase A
  gates #3/#5/#7 open; serving budget (~$1,360/mo A100) needs operator override.

## 2. The roadmap — three tracks (run in parallel, each task = R-number + tests + verify)

### TRACK R — Grounded Reasoning Engine (the missing driver) ★ START HERE
**R1 — `aria_service/intel/grounded_reasoner.py` skeleton.** `async def reason(message,
context, *, llm, tools) -> ReasonResult` where `ReasonResult = {answer,
claims:[{text, evidence:[{source, kind, confidence}], grounded:bool, confidence}],
steps:[...], abstained:bool}`. v1 wraps the EXISTING primitives into the loop:
understand → decompose → **gather+verify per sub-question** (rag_store +
neural_memory.recall + premise_verifier canonical caches + knowledge store + live
tools) → reason over verified evidence only → **inline self-critique** (call
honesty_judge BEFORE answering, not after) → **ground-or-abstain** → cite → absorb.
Gate behind `ARIA_GROUNDED_REASONER=1`. Capability test: a cached fact returns WITH a
citation; a no-evidence question returns explicit "cannot verify", never a confident guess.

**R2 — Inline grounding gate (anti-hallucination core).** Move honesty/grounding
inline: every [CONFIRMED] claim must map to an evidence item or it's downgraded to
"[ASSESSED — unverified]" / dropped. Hard rule: **no [CONFIRMED] without a source.**
Keep the async audit trail too. Capability test: a prompt that tempts a fabricated
fact returns cited-or-abstained, never a bare false claim.

**R3 — Per-claim evidence trace + feedback loop.** Emit the structured
{claim, evidence_sources, verification_method, confidence} ledger (also to /trace and
the coder-chat-UI grounding chips). On honesty_judge failure, RE-reason that
sub-question with stronger constraints (bounded retries) instead of shipping it.

**R4 — Evidence-first multi-step + live tools.** DECOMPOSE complex queries; per
sub-question gather from memory first, escalate to live tools (sanctions, researcher,
crawl) only when memory is insufficient; unbounded steps (Invariant A) but each closes
with verified-or-explicitly-unverifiable evidence.

### TRACK C — Coding mastery / infrastructure (close the gaps ARIA found)
**C1** add `test_safety.py` (rate/cost/dedup/pause + R-F897 rollback + R-F901 coder
bucket + in-memory fallbacks). **C2** add `test_self_coder.py` (fix_gap pipeline,
staging decision, error paths). **C3** add tests for SovereignLLM/TestRunner/
FlyDeployer/ClaudeReviewer. **C4** populate-or-remove the ~9 empty test files. **C5**
fix WA `BRAIN_URL` dead default (→ brain :8000 / ARIA_SERVICE_URL). **C6** end-to-end
capability test: gap → fix staged → brain notified. (ARIA is doing most of these now.)

### TRACK L — Become her own LLM (sovereignty)
**L1 — Grow the DPO corpus to ≥100 preference pairs.** Run the R-F59 (social-eng) +
R-F80 (prompt-injection) adversarial suites to generate chosen/rejected pairs; the
grounded reasoner (Track R) also produces high-quality grounded pairs for SFT.
**L2 — Run DPO** (`dpo_train.py` on RunPod) → `aria_llm_v0_1_dpo` checkpoint.
**L3 — Implement grounded_rate in `eval_aria_llm.py`** (it's a documented gate but not
measured) and make `llm_builder.evaluate_model()` real (call the served model).
**L4 — Run the eval** (SFT/DPO vs DeepSeek on the frozen 500-Q + adversarial).
**REQUIRED BAR to replace DeepSeek:** prompt-injection pass ≥0.90, defence-DD accuracy
≥0.80, p50 latency ≤4s, **grounded_rate ≥0.85**.
**L5 — Activate** (`ARIA_LLM_URL`) per `docs/aria_llm_v01_activation.md` — ONLY after
L4 passes AND Phase A gates close AND operator approves the serving budget. The Track-R
grounded reasoner then runs UNCHANGED on her own model.

## 3. The bar for "being an LLM" (explicit gates — all must hold)
1. Grounded reasoner live + proven (Track R, R1-R3) — reasons grounded, abstains, no
   hallucination, with a per-claim trace.
2. DPO complete + eval PASSES the bar above (pi_pass ≥0.90, dd_acc ≥0.80, p50 ≤4s,
   grounded_rate ≥0.85) — beating DeepSeek.
3. Phase A gates closed (CLAUDE.md §1) — no out-of-phase activation.
4. Operator budget sign-off for serving.
Until all four: ARIA-LLM stays staged; DeepSeek serves; the reasoner runs on DeepSeek.

## 4. Execution discipline (every task)
Reserve an R-number → map-then-change → unit + capability tests → wire success+failure
to the brain (§21a) → verify (2 passes) → commit + push → confirm before any fly
deploy. Free to code anything; never auto-deploy a NO_AUTODEPLOY_FILES file (R-F1040);
never weaken a guard to pass a test — fix the root cause.

## 5. Sequencing
Track R (R1→R2→R3→R4) is the priority — it's the missing driver and it's what makes
"never hallucinate" real AND produces the grounded training pairs Track L needs. Track
C runs alongside (ARIA's current fix-pass). Track L's corpus grows passively; run
DPO+eval once R1-R2 are landing and the corpus ≥100 pairs. **Do not flip ARIA_LLM_URL
until §3's four gates all hold.**
