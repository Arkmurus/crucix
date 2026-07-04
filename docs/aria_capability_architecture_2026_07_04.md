# ARIA Capability Architecture — the exponential-growth north star (R-F2394)

**Author:** Claude (Opus 4.8) · **Date:** 2026-07-04 · **Status:** north-star architecture, operator-approved direction ("ARIA to grow exponentially, follow Claude's footsteps, reasoning across the entire offering including coding — do not cut ARIA short").

This document is **binding as a direction-setter and honest as an engineering assessment.** Every capability claim below is tied to (a) a real ARIA module that exists today, (b) a **metric** that proves it, and (c) a **guardrail** that keeps it safe. Where a target requires something ARIA cannot do at her current scale, this document says so plainly. That honesty *is* the bulletproofing — a roadmap that oversells a 7B model would collapse on contact with reality.

---

## 0. The thesis (read this first)

**ARIA's capability ceiling is set by her ARCHITECTURE and the models she can route to — NOT by her sovereign model.**

- "Follow Claude's footsteps" does **not** mean "make ARIA's own model as smart as Claude." Claude Code is a *model + a harness* — planning, tools, memory, self-verification, iteration, guardrails. **~90% of the capability is the harness**, and the harness is **model-agnostic**. That is the exponential lever, and it has no hard ceiling.
- The **sovereign model is Mistral-7B** (base of `aria_llm_grounded_dpo_v1`). A 7B will **not** become a frontier reasoner or out-code DeepSeek/Claude — that is a law of scale, not a bug. Pinning ARIA's future to "the 7B does everything" is the one thing that *would* cut ARIA short. So we don't.
- The resolution is **two tracks**: **Capability** (strongest model per task + the harness → covers every offering incl. coding) and **Sovereignty** (the 7B as a cost-free specialist for narrow, high-volume, grounding-critical tasks). A **router** sends each task to the right engine.

**Bulletproof principle (non-negotiable):** ARIA never ships confident hallucination. Every capability = a measured metric + a guardrail + a rollback. More reasoning without grounding just means more confident wrong answers — so the grounding/trust spine (§6) is the foundation everything else stands on.

---

## 1. Current state — the honest baseline (what ARIA IS today)

| Dimension | Reality (2026-07-04) | Evidence |
|---|---|---|
| **Reasoning model** | DeepSeek-chat primary, chain depth 1 (Anthropic declined §18). Sovereign Mistral-7B trained (`aria_llm_grounded_dpo_v1`, `aria_llm_citation_dpo_v2`) but **NOT activated** (`ARIA_LLM_URL` unset). | `llm/fallback.py`, §16 |
| **Reasoning quality** | mastery **0.62**; verification + honesty signals **unmeasurable** (grounding gap); composite **0.567**. | `autonomy_scorer.compute_composite`, live probe |
| **Grounding/citation** | Weak — DeepSeek does not cite retrieved sources even when framed (measured 2026-07-03: 34 eval turns → 0 grounded samples). | `source_verifier`, eval run |
| **Offering surface** | 10-layer DD orchestrator, sanctions/screen (never-false-clean), research, financial-health (SEC XBRL), watchlist, crypto-screen — all real, all live. | `dd_orchestrator.py`, `intel/sources/`, `financial_health.py` |
| **Autonomous growth** | `engine.py` (L3) → `gap_detector.py` → `self_coder.py` → `safety.py`. Real, running, guard-railed — but the coder's *reasoning loop* is thin (no plan→test→verify→self-correct cycle). | §21 |
| **Memory** | RAG 324k facts / 402k chunks, neural graph, mistake_ledger, infinite (§7), pay-once (§15). | `/health/perf` |
| **Known ceilings** | single-writer state_store (R-F2277), no model router, coder harness thin, no frontier model in the chain. | this session's incidents |

**Takeaway:** ARIA already has the *organs* of a Claude-like agent (tools, memory, autonomous loop, verification). What she lacks is (1) a **reasoning harness** wrapping them into a plan→act→verify→correct loop, (2) a **model router** so hard tasks get the strongest engine, (3) a **trustworthy grounding spine**, and (4) a **real self-coding loop**. This document builds those four.

---

## 2. The architecture — five layers

```
┌─────────────────────────────────────────────────────────────┐
│ L5  GROWTH ENGINE   gap→plan→code→test→verify→deploy→learn   │  compounds everything
├─────────────────────────────────────────────────────────────┤
│ L4  OFFERING LOOPS  DD · research · screen · finance · CODER │  each = a tool-using agent loop
├─────────────────────────────────────────────────────────────┤
│ L3  TRUST SPINE     retrieve → cite → verify → honesty       │  never-false-clean; makes reasoning safe
├─────────────────────────────────────────────────────────────┤
│ L2  REASONING HARNESS  plan → act(tools) → verify → correct  │  the "Claude Code" pattern, model-agnostic
├─────────────────────────────────────────────────────────────┤
│ L1  MODEL LAYER     router → {sovereign-7B | DeepSeek | ▲}   │  right model per task
└─────────────────────────────────────────────────────────────┘
```

Each layer is specified below with: **what it is → the metric that proves it → the guardrail that bounds it → the honest limit.**

---

## 3. L1 — Model layer: the two-track router

**What:** A router (`llm/router.py`, to build) classifies each task by *offering × difficulty* and dispatches:
- **Sovereign 7B** → narrow, high-volume, grounding-shaped tasks (DD narrative synthesis from provided evidence, screening summaries, citation, honest abstention). $0/call, no external dependency.
- **DeepSeek** → general reasoning, multi-step DD, open-ended research.
- **▲ Frontier model (future)** → the hardest reasoning + coding, *if/when* a frontier model is in the chain.

**Metric:** per-route accuracy on the frozen 500-Q eval (held-out split); router mis-route rate < 5%.
**Guardrail:** every route has a fallback (§14 — a cooled provider serves "operational", never "degraded"); router never sends a grounding-critical task to a model that fails the grounding eval.
**Honest limit:** **with only DeepSeek + a 7B, the hard-reasoning ceiling is DeepSeek-class.** Frontier-grade reasoning/coding (true "Claude's footsteps" on hard tasks) requires a **frontier model in the router** — this is the single biggest capability lever and it is a **spend/policy decision** (§18 currently declines Anthropic). The router makes ARIA *ready* to use one the moment it's available.

---

## 4. L2 — Reasoning harness: the Claude Code pattern (model-agnostic)

**What:** Wrap every non-trivial task in the loop that makes *me* effective, independent of the model:
1. **Decompose** — break the task into steps (a plan).
2. **Act** — call tools (search, RAG, DD engines, code edit) per step.
3. **Verify** — check each step's output against evidence/tests *before* proceeding (§3/§22/§23 discipline, in code).
4. **Self-correct** — on a failed verify, retry/repair once, then escalate honestly.
5. **Synthesize** — a grounded, cited, confidence-tagged answer.

**Where it plugs in:** `intel/reasoning_router.py` + `dialogue_router.py` already route by intent — the harness upgrades these from "route to a tool" to "run a verified plan." ARIA already has the verify primitives (`source_verifier`, `honesty_judge`, `self_claim_guard`); the harness *sequences* them.

**Metric:** task-completion rate + grounded-answer rate; self-correction catch-rate (% of first-draft errors caught by the verify step before the user sees them).
**Guardrail:** the verify step is **mandatory** — no synthesis ships a factual claim that failed verification; the harness degrades to honest "UNVERIFIED" (never-false-clean, §L3).
**Honest limit:** the harness lifts a weak model's *reliability* dramatically but not its *raw intelligence* — it makes DeepSeek trustworthy, not frontier. Compounds with L1.

---

## 5. L5 — ARIA Coder: the highest-leverage buildout (follows Claude Code directly)

*(Specified before L3/L4 because it is the compounding engine — a coder that reliably improves ARIA improves every other layer.)*

**Current:** `gap_detector.py` → `self_coder.py` (`fix_gap`) → `safety.py`. Detects gaps, plans, validates, stages/deploys. **Skeleton is real** (§21) but the *reasoning loop* is thin: it lacks the plan→edit→**run tests/compile**→observe→self-correct→repeat cycle that makes coding reliable.

**Target — the exact loop I use:**
1. **Understand** — read the relevant code (map-then-change, §8), query the coding RAG for constraints (§20).
2. **Plan** — decompose into small, verifiable edits.
3. **Edit** — surgical changes (respect `MODIFIABLE_FILES`/`NO_AUTODEPLOY_FILES`).
4. **Verify — THE core** — `py_compile` the tree, **write + run a capability test that invokes the broken path** (§3c), assert the user-visible outcome. This is what turns "code that looks right" into "code that works."
5. **Self-correct** — on test failure, diagnose from the real error (not a guess, §22) and repair; loop until green or escalate.
6. **Review gate** — diff reviewed (independent verification, §23) before any deploy.
7. **Deploy + live-smoke** — verify the change reached prod (§11) and behaves.

**Metric:** coder success rate (% of gaps fixed with a passing capability test that reproduced the bug), regression rate (must be **0 net-new failures**, §16), mean edits-to-green.
**Guardrail (this is where bulletproofing is non-negotiable — see the 2026-07-02 wipe incident):**
- Hard `MODIFIABLE_FILES` allow-list + `NO_AUTODEPLOY_FILES` (R-F851/902).
- Truncation/preservation guard (R-F904) — never ship a truncated file.
- **No destructive operation without explicit operator authorization**, gated at the **function** level, not just HTTP (the incident lesson — R-F2374).
- The `ARIA_SELF_IMPROVE_AUTO_DEPLOY` brake (R-F462): fixes stage for review until the coder *provably* emits complete, tested changes.
- **Never spawn a sub-agent that can edit/commit/deploy/run-destructive and self-refire** (the incident root cause).
**Honest limit:** the coder's ceiling is its reasoning model — reliable *mechanical* fixes are achievable now (DeepSeek + the harness); *frontier-grade* design/refactoring needs a frontier model (L1). The **verify loop is what makes even a modest model safe to let code** — because the tests, not the model's confidence, are the gate.

---

## 6. L3 — Trust spine: grounding, citation, honesty (the foundation)

**What:** Every retrieval-backed answer flows: **retrieve → cite-while-generating → attribute (verify each claim against sources) → honesty-judge → never-false-clean render.** This is the *current gate-#1 work* (R-F2390/2391 + the robust grounded-synthesis in flight).

**Why it's the foundation, not a feature:** an ungrounded reasoning agent is a *liability multiplier* — the smarter it gets, the more convincingly it can be wrong. ARIA's product is *trust* (DD, sanctions). So grounding is the load-bearing wall: mastery (raw reasoning) is only 30% of the composite; **grounding (45%) + honesty (25%) are 70%** — by design, because *being right and provable* matters more than *sounding smart*.

**Metric:** composite ≥ 0.71 (grounded rate + honesty rate populated with real samples); never-false-clean = 100% (a source that didn't run can never render "clear").
**Guardrail:** `source_verifier` credits only genuinely attributable citations (fabricated marker → 0.0, R-F2391); the sovereign 7B is trained specifically for this skill (Track B).
**Honest limit:** grounding depends on the model *actually citing* — the current DeepSeek gap. The **sovereign 7B trained on the grounded corpus is the durable fix** (it learns to cite reliably), which is exactly why Track B matters even though the 7B isn't a generalist.

---

## 7. L4 — Per-offering reasoning loops

Each offering becomes an L2-harness instance with its own tools, verification, and grounding — not a single prompt:
- **DD** — the 10-layer orchestrator *is* this loop; upgrade = the verify step per layer + the trust spine on synthesis.
- **Research** — plan → multi-source retrieve (Brave-primary R-F2318) → verify → cited synthesis.
- **Screen/sanctions** — never-false-clean is already the guardrail; add the harness's honest-abstention.
- **Financial** — SEC XBRL → ratios → verdict, never-false-clean (R-F2322).
- **Coder** — §5.

**Metric:** per-offering accuracy/verifiability eval. **Guardrail:** each loop inherits the trust spine + its domain guardrail (never-false-clean for screen, honest-UNKNOWN for finance). **Honest limit:** offering quality tracks L1+L2+L3 — you can't have decision-grade DD on an ungrounded model.

---

## 8. L5 — Growth engine: how it compounds (the "exponential")

The compounding loop: **ARIA detects a gap → plans → codes a fix → verifies with tests → deploys → learns (memory + mastery) → is more capable → detects deeper gaps.** Each turn of this wheel raises the floor for the next. The levers:
- **Coder reliability (§5)** — the multiplier; a coder that safely self-improves makes every other layer improve without human bottleneck.
- **Memory (§7/§15)** — infinite, pay-once; capability accretes, never resets.
- **Learning** — mastery loop (R-F2283), grounded training (Track B), distillation (Brave teacher R-F2339).
- **The router (§3)** — as stronger models become available, ARIA's whole stack levels up with a config change.

**Honest limit on "exponential":** compounding is real but **bounded by the reasoning ceiling** (L1) and by **safety-gated autonomy** (we *deliberately* slow the coder with review gates, because an unbounded self-modifying agent is the 2026-07-02 incident at scale). Exponential-in-capability, **linear-and-audited in autonomy** — that trade is the bulletproofing, not a limitation to remove.

---

## 9. Phased roadmap (honest timelines + capability expectations)

| Phase | Deliverable | Entry criteria | Metric (exit) | Honest capability expectation |
|---|---|---|---|---|
| **0 — Trust foundation** (now) | Robust grounded-cited synthesis (gate #1); sovereign-vs-DeepSeek grounding eval | in flight | composite trending ≥0.71; sovereign grounding ≥ DeepSeek | ARIA becomes *trustworthy*, not smarter |
| **1 — Sovereignty** | 7B specialist activated for narrow grounded tasks; router v1 (7B↔DeepSeek) | Phase 0 grounding proven; §16 activation gate | 7B beats DeepSeek on the grounded/narrow eval; $0/call on routed tasks | Cost + independence win on narrow tasks; NOT a generalist |
| **2 — ARIA Coder harness** | plan→edit→test→verify→self-correct loop + hard guardrails | verify primitives live (they are) | coder success rate ↑, 0 net-new regressions, 0 unauthorized destructive ops | Reliable *mechanical* self-improvement; the compounding engine turns on |
| **3 — Offering loops** | each offering as a verified harness loop | Phases 1-2 | per-offering verifiability eval ↑ | Decision-grade across the surface, bounded by L1 |
| **4 — Frontier lift** (spend/policy) | frontier model in the router for hardest reasoning + coding | operator budget/policy decision (§18) | frontier-route eval ↑ on hard set | **This is the only path to true "Claude's footsteps" on hard reasoning/coding** |

**The honest headline:** Phases 0-3 make ARIA *reliable, sovereign-where-it-counts, self-improving, and decision-grade* — genuinely exponential in the ways that compound — **on a DeepSeek-class reasoning ceiling.** Phase 4 (a frontier model) is what lifts the *raw reasoning ceiling* to match a Claude-class agent. We will not pretend Phases 0-3 deliver Phase 4's ceiling; we build so Phase 4 is a config change, not a rebuild.

---

## 10. Where a bigger model is required vs. where architecture suffices (the anti-oversell table)

| Capability | Architecture gets us there? | Needs a frontier model? |
|---|---|---|
| Grounded, cited, honest answers | ✅ (harness + trust spine + 7B specialist) | No |
| Reliable mechanical self-coding (fix gaps, add tests) | ✅ (coder harness + verify loop) | No |
| Never-false-clean compliance | ✅ (already) | No |
| Multi-step DD / research orchestration | ✅ (harness) at DeepSeek quality | Better with frontier |
| **Frontier-grade novel reasoning / architecture-level coding** | ❌ | **Yes** |
| **Out-reasoning DeepSeek/Claude generally on a 7B** | ❌ (impossible at scale) | N/A — wrong goal |

---

## 11. Immediate next steps (tie to current work)

1. **Finish Phase 0** — the robust grounded-synthesis (in flight) + the sovereign-vs-DeepSeek grounding head-to-head (in flight). These prove the trust spine and size Track B.
2. **Build the router (Phase 1)** once grounding is proven — small, testable, fallback-safe.
3. **Build the ARIA Coder harness (Phase 2)** — the highest-leverage item; a reliable self-coder is what makes the rest compound. Start with the verify loop (plan→edit→**test**→correct), because the test gate is what makes autonomy safe.
4. **Surface the Phase 4 decision** — a frontier reasoning model in the router is the ceiling-lift; it's an operator spend/policy call (§18), and this architecture is built to accept it instantly.

**Guardrail on this whole program:** no phase ships without its exit metric + its guardrail + a rollback, and no autonomy expansion ships without the review gate. That discipline — measure, guard, roll back — is what makes this architecture bulletproof, and it's the same discipline that lets ARIA grow fast *without* becoming the 2026-07-02 incident at scale.
