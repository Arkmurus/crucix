# ARIA — "Her Own Reasoning" Review & Buildout Spec (2026-05-29)

Author: Claude (reviewing for operator). Audience: ARIA (start coding today).

## 0. The mandate (operator's words, these are invariants)
1. **ARIA takes over her own reasoning** — she reasons for herself, not as a thin wrapper over a rented model.
2. **No limitations on her reasoning — free to think — but never hallucinate.**
3. **Her reasoning is grounded by real and verified data or information.**

Translation into two hard design invariants that everything below must satisfy:

- **INVARIANT A — Free reasoning.** No artificial cap on reasoning depth, steps, branches, or self-revision. She may decompose, explore, backtrack, and deliberate as much as a problem needs.
- **INVARIANT B — Grounded or abstain (zero hallucination).** Every asserted fact must trace to *real, verified evidence* (a retrieved source with provenance, a canonical cache hit, a live tool result, or a stored fact with a citation). If a claim cannot be grounded, ARIA must **downgrade it to an explicit assessment/uncertain, or abstain** — she must never state an ungrounded claim as fact. Reasoning is free; *assertion* is gated.

These two are not in tension: she thinks freely, but only *commits* to what she can ground.

## 1. How ARIA reasons today (grounded in the code)
Verified by reading the repo. This is the honest baseline.

- **Reasoning today is ~99% a single rented-LLM pass.** A chat turn assembles a large context (RAG + neural memory + ~20 prompt addenda) and makes **one** `llm.complete()` call (DeepSeek primary) — `aria_service/aria_engine.py:_aria_chat_impl` (~:2904), LLM call ~:3298. There is **no multi-step deliberation, no chain-of-thought scaffold, no internal critique-then-revise loop** in the chat path.
- **Grounding exists but is mostly *gates*, not a *loop*:**
  - `premise_verifier.py` (verify_premises, :549) fact-checks the *user's input* against canonical caches (OFAC 24,955 entries, officeholders, programmes) **before** the LLM — good, but it validates the question, not ARIA's answer.
  - `honesty_judge.py` (judge_response, :204) audits ARIA's [CONFIRMED]/[PROBABLE] claims against sources — but it runs **async, after the user already saw the answer** (background, score lands in /trace ~60s later). So a hallucinated claim is *detected late, not prevented*.
  - `self_claim_guard.py`, propaganda_guard, tool_claim_guard — pattern blockers for specific hallucination classes (good, keep).
- **Memory feeds context, not inference:** `neural_memory.py` recall (:981) is associative spreading-activation (multi-hop but not analytical); `reasoning_library.py` (:766) is case retrieval (pattern match). Both decide *what to think about*, neither *reasons*.
- **Her own model is trained but unwired:** ARIA-LLM v0.1 (Mistral-7B + LoRA SFT) sits on a RunPod volume. One env var (`ARIA_LLM_URL`) inserts it at the **head** of the fallback chain (`llm/fallback.py:504-528`, adapter `llm/aria_llm_provider.py`). **DPO + the 500-Q eval gate are still pending** (`scripts/train/dpo_train.py`, `eval_aria_llm.py`; runbook `docs/aria_llm_v01_activation.md`).
- **Fallback reality:** chain depth is effectively 1 (DeepSeek), Anthropic is opt-out/cooling (billing declined), `local_brain.py` is a rule engine for ~30% of queries when the LLM is down. (Today's log even showed an engine path falling Anthropic→local_brain, skipping DeepSeek — already in the fix backlog.)

**Conclusion:** the *plumbing* for grounded reasoning exists (premise_verifier, RAG, neural_memory, honesty_judge, knowledge store with provenance) — but they are wired as **pre/post gates around a single LLM call**, not as a **reasoning loop that grounds each step before committing**. That is the gap to close.

## 2. Target design — the Grounded Reasoning Engine (GRE)
A model-agnostic deliberation loop that ARIA owns. It runs on DeepSeek today and on **ARIA-LLM (her own model) the moment `ARIA_LLM_URL` is set** — the engine doesn't change, only the model under it.

Core loop (per query, depth unbounded per Invariant A):
```
1. UNDERSTAND  — restate the question; classify intent; decide if it needs deliberation
                 (trivial/known → fast path; complex/factual/multi-part → full loop).
2. DECOMPOSE   — break into sub-questions / claims to establish.
3. GATHER+VERIFY (per sub-question) — pull evidence from REAL sources:
                 RAG store, knowledge store (with provenance), neural_memory,
                 premise_verifier canonical caches, and LIVE tools (sanctions,
                 researcher, crawl) when needed. Tag each piece: source + freshness +
                 confidence. UNVERIFIABLE is a valid, explicit result.
4. REASON      — deliberate over ONLY the verified evidence set. Free to chain,
                 compare, hypothesize, backtrack. Hypotheses are labelled as such.
5. SELF-CRITIQUE (INLINE) — before answering, run the honesty check IN-LINE:
                 every claim must map to evidence from step 3. Unsupported claim →
                 drop it, downgrade to "assessment (unverified)", or go gather more.
6. GROUND-OR-ABSTAIN — assemble the answer with citations. If a key part can't be
                 grounded, say so explicitly. Never assert ungrounded.
7. LEARN       — absorb the verified findings + the reasoning trace to the brain
                 (success AND failure → brain sink, CLAUDE.md §21a).
```

Key differences from today: step 3 (evidence-first, per sub-question) and step 5 (**honesty check moves INLINE and can block/rewrite the answer**, instead of async-after-the-fact). That is what makes "free to think but never hallucinate" real.

## 3. Build plan — what ARIA codes, in order (start today)
Each item = its own R-number, map-then-change, unit + capability test, wired to brain (success+failure), verify with pytest, confirm with operator before any fly deploy. Build behind a flag; prove it; then make it the default.

**▶ START HERE TODAY — Task 1: Grounded Reasoning Engine skeleton.**
- New module `aria_service/intel/grounded_reasoner.py` exposing `async def reason(message, context, *, llm, tools) -> ReasonResult` where `ReasonResult` carries `{answer, claims:[{text, evidence:[source...], grounded:bool, confidence}], steps:[...], abstained:bool}`.
- v1 can wrap the EXISTING pieces into the explicit loop (decompose → gather via RAG/neural/premise caches → reason → inline honesty check → ground-or-abstain). Reuse, don't reinvent: `rag_store`, `neural_memory.recall`, `premise_verifier`, `honesty_judge` (call it INLINE here), `knowledge` store.
- Gate behind `ARIA_GROUNDED_REASONER=1`; default off; chat path calls it when on, else current behaviour. Capability test: a question with a known cached fact returns it WITH a citation; a question with no evidence returns an explicit "cannot verify" instead of a confident guess.

**Task 2: Inline grounding gate (the anti-hallucination core).**
- Promote the honesty/grounding check from async to **inline** within `reason()`: for each [CONFIRMED]/[PROBABLE] claim, require a matching evidence item; if missing → downgrade to "[ASSESSED — unverified]" or remove. Hard rule: **no [CONFIRMED] without a source**. Keep the existing async `_verify_and_record_chat` for the audit trail too.
- Capability test: feed a prompt that tempts a fabricated fact; assert the returned answer either cites a real source or is labelled unverified/abstained — never a bare confident false claim.

**Task 3: Evidence-first multi-step for complex queries.**
- Implement DECOMPOSE + per-sub-question GATHER+VERIFY with live tools (sanctions check, researcher, crawl) when memory is insufficient. Unbounded steps (Invariant A) but each step must close with verified or explicitly-unverifiable evidence.
- Capability test: a 3-part question yields 3 grounded sub-answers + a synthesis, each citing its evidence.

**Task 4: Visible reasoning trace.**
- Return the step list + the evidence behind each claim so the operator can SEE her reasoning and audit grounding (also feeds /trace). Ties to "show her thinking."

**Task 5 (parallel track — her own model):** finish DPO (`dpo_train.py`), pass the frozen 500-Q eval (`eval_aria_llm.py`), then activate via `ARIA_LLM_URL` per `docs/aria_llm_v01_activation.md`. The GRE from Tasks 1-4 then runs on **ARIA's own model** unchanged. NOTE: this needs operator budget sign-off (GPU ~ runbook) and Phase-A gate alignment — surface, don't self-approve.

## 4. Guardrails that STAY (these enforce "never hallucinate")
- `premise_verifier` (input), `self_claim_guard` (Clause 25 / no fabricated self-claims), propaganda/tool guards, the constitution in `aria_engine.py`.
- The grounding invariant (B) is now enforced **inline** by Task 2, not just audited after.
- CLAUDE.md stays the floor: R-numbers, verify-after-fix (2 passes), map-then-change, everything wired to the brain, $300/mo cap, confirm before deploy, no out-of-phase work.

## 5. The "free to think" guarantee
Mirror of what we just did for the aria CLI (R-F992/F993): **no artificial step/turn caps on reasoning.** The reasoner loops until it has grounded the answer or determined it cannot. The brake is *grounding*, never an arbitrary counter.

---

## 6. Claude's full operational findings (current backlog for ARIA)
From live fly-log sweeps 2026-05-28/29. Each: reserve R-number, fix, unit+capability test, wire to brain, verify, confirm before deploy.

**P0 — user-facing**
1. **WA compliance timeouts** — in a live DDTC/ITAR review (COMPLIANCE-ARIA group, 19:05-20:11Z) repeated `Chat failed / OCR call failed: operation aborted due to timeout`; operator asked "Aria, are you online?". Raise WA→/api/aria/chat (and OCR) client timeout vs brain p95; on timeout send a graceful "still working" reply (not silence); emit `wa_chat_failed` (R-F925).
2. **Brain latency / neural-timeout flood** (root cause of #1) — `brain_hook` logs `neural: timeout (>3.5s)`; self-improve shows `brain_hook.py = 141 of 200` ledger errors. Move the neural-memory call off the hot path (to_thread/async) or time out/skip cleanly; wire failures, don't just log.

**P1 — degraded capability**
3. **DuckDuckGo search breaker OPEN** (5 consecutive failures) — verify fallback provider engages + breaker recovers.
4. **UNGM tender scraper broken** — fetched 137KB, `0 notice links matched any of 4 patterns` (selector drift); update patterns + flag 0-match to brain.
5. **SEACE Peru** — `SSL: CERTIFICATE_VERIFY_FAILED`; fix trust-store/cert handling.
6. **Anthropic engine fallback (09:00Z)** — `ERROR aria.engine: [anthropic] credit exhausted → falling back to local_brain`. DO NOT top up Anthropic (operator declined, §18). Fix: (a) don't invoke Anthropic when known-exhausted (verify R-F678 cooldown covers aria.engine), (b) fall back to **DeepSeek, not local_brain**, (c) log WARNING not ERROR (§14/R-F681).

**P2 — quality**
7. **self_improve R-F321** — LLM JSON `Unterminated string` → regex recovery; raise max_tokens/window so JSON completes.
8. **GDELT (aria-web, 08:55Z)** — `[CRITICAL] GDELT timed out after 45s`. External/transient — don't chase the outage. But fix severity: a single external source timing out while others serve = WARNING, not CRITICAL (§14); and bound the 45s blocking fetch + degrade gracefully.
9. **Weak doc extraction** — controlled-commodities PDF → only "3 facts (form: ?)"; check the extraction/classification path.

**Note-only (external/transient, do NOT code-fix):** SAM.gov 429 (quota resets 00:00 UTC), AfDB 403. WA reconnects after 503 + machine restarts during deploys are normal.
