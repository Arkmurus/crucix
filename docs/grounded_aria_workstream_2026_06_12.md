# Grounded / Tool-Use Training Workstream — "Bulletproof ARIA"
**Date:** 2026-06-12 · **Author:** Claude (Opus 4.8) · **Status:** plan, pre-spend
**Basis:** 3-agent codebase audit (retrieval stack, model/eval/training, tool-use) — all findings file:line-grounded.

---

## 1. The core diagnosis (grounded in the code)

**The model is trained AND evaluated CLOSED-BOOK, but deployed GROUNDED.** This is a train/serve mismatch and it is the root reason the numbers are stuck near the teacher (~0.32).

- **Eval is closed-book** — `scripts/train/eval_aria_llm.py:70` sends `messages:[{"role":"user","content": bare_question}]`. No evidence. So the model can only answer from memorized weights. A 7B model can't memorize DD facts → it guesses (wrong) or honestly refuses ("I cannot confirm without a source") → both score low. The teacher (DeepSeek) hits the *same* wall → that is *why* it caps at 0.316.
- **Training is closed-book** — the SFT distillation rows are bare `question → answer` (`sft_train.py` messages format; `data/training/aria_sft_distill_v04.jsonl`). The model never learned to *use* retrieved context.
- **Production IS grounded** — `aria_engine.py:3584` calls the LLM with `user_prompt = [history] + [7-layer retrieved context] + [question]`. The 7-layer stack (`aria_engine.py:2369`: RAG, knowledge, semantic, neural, ledger, history, compliance) is already merged in. So **in production the model is handed evidence it was never trained to exploit.**

**Implication:** our 0.288 is a *closed-book* score. The production-relevant number (open-book, with the 7-layer context) is unmeasured — and almost certainly much higher. We have been flying the plane on the wrong instrument.

---

## 2. What "bulletproof grounded ARIA" means (the target capabilities)

A model that, given ARIA's retrieved evidence, is **right and honest**:
1. **Grounded generation** — reads the retrieved context, answers correctly, **cites inline** (`[from <url>]` / `[from dd_orchestrate:{run_id}]`, the existing convention in `chat_sources.py`).
2. **Honest abstention** — when retrieval is empty/weak, says "insufficient evidence" instead of guessing (the existing golden behaviour — it's literally the "chosen" answer in our DPO data).
3. **No fabrication beyond context** — never invents reg numbers, addresses, figures not in the evidence (Constitution clauses 11/14/15/24, `aria_engine.py:129-200`).
4. **Calibrated confidence** — `[CONFIRMED]` only on multi-source; `[ASSESSED]`/`[UNVERIFIED]` on single/weak (clause 24).
5. **(Phase 2) Agentic tool-use** — decide *which* tool to call. Today a **regex router** (`_detect_tool_intent`, `routes/aria.py:4519`) picks tools, NOT the model. Making the model itself agentic is a later, higher-effort capability; the `aria_cli/agent.py:137` function-calling loop is the working template.

Bulletproofing = capabilities 2–4 are **trained in and gated in eval**, so the grounded model is *more* honest than the closed-book one, not less.

---

## 3. The architecture it plugs into (all real, already built)

| Need | Existing component | File |
|---|---|---|
| Retrieve evidence for a question | `rag_store.get_rag_context_with_sources()` → (formatted_text, sources[]) | `intel/rag_store.py:1169` |
| Full evidence stack | 7-layer context merge | `aria_engine.py:2369` |
| Structured facts / signals | `knowledge.py`, `intel_ledger.py` (100-yr), sanctions SQLite cache | `intel/…` |
| Serve grounded (system+multi-turn) | `serve_eval_shim.py` already supports system prompt + messages | `scripts/train/serve_eval_shim.py:150` |
| Prod LLM call (system passed separately) | `aria_llm_provider.complete(system, prompt)` | `aria_service/llm/aria_llm_provider.py:86` |
| Mineable training traces | `trace_stream` (Q→tool→context_size→response→verification→feedback); `chat_audit_log` (Q→answer→sources→grounded_rate, raw text if `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1`); DD `Finding` objects | `intel/trace_stream.py:112`, `intel/chat_audit_log.py:120`, `intel/dd_schema.py` |
| Citation convention | `chat_sources.extract()` parses `[from tool]`, URLs, `[SANCTIONS LIVE CHECK]`, `[source: …]` | `intel/chat_sources.py:76` |

**No serving changes needed** — the shim and provider already accept a system prompt + multi-turn. The work is in **data + training + eval**, not plumbing.

---

## 4. The workstream — staged, cheap-first, decision-gated

### Stage 0 — MEASURE the open-book ceiling (do this FIRST; ~$3, ~1h, NO training)
Re-run the existing v0.4-SFT model on the 500-Q eval **with retrieved context prepended** to each question (RAG context from `get_rag_context_with_sources()`), and extend the judge to check citations.
- **Why first:** it validates the entire thesis for the price of one eval. It quantifies the upside (closed-book 0.288 → open-book ???).
- **Decision gate:** 
  - Big jump (e.g. 0.288 → 0.6+) → grounding is THE lever; the workstream is justified; and we learn the model can *already partly* use context.
  - Small jump → the model can't exploit context → grounded *training* (Stage 1-2) is exactly what's missing.
  - Either outcome is decisive and cheap.
- **Build:** add a `context` field to a copy of `aria_eval_500q.jsonl` (populate per-question from RAG); `eval_aria_llm.py` prepends it; judge rubric gains a citation/anti-fabrication clause.

### Stage 1 — Build the grounded training corpus (~free–$, offline-heavy)
Three complementary sources, deduped + §24-filtered:
1. **Synthesize (distill reasoning-over-evidence, NOT facts):** for each DD question, retrieve context, then ask DeepSeek to answer **using only the provided context, with inline citations + honest abstention when context is thin.** This distills the *skill* (grounded synthesis), which is transferable, not memorized facts (which aren't). This is the bulk.
2. **Mine real traces:** export `trace_stream` records where `tool_used != ""` and feedback positive → (question + tool_context block → ARIA's grounded answer). Real evidence→answer pairs from production. Requires `ARIA_CHAT_TRAIN_CAPTURE_TEXT=1` for raw text (gap: confirm it's on).
3. **Honest-abstention pairs:** context-empty questions → "insufficient evidence" answers (these double as DPO `chosen` vs a hallucinated `rejected`).
- **Row shape** (matches `sft_train.py` template): `messages:[{user: "[CONTEXT]\n<src+passage>\n\n[QUESTION]\n<q>"},{assistant: "<grounded answer> [from <url>]"}]`.
- **Quality gate (§24):** verify citations point into the provided context; reject answers asserting facts absent from context; balance answerable vs abstain.

### Stage 2 — Train grounded SFT (+ light abstention DPO) (~$4, ~2.5h)
- SFT on the grounded corpus (reuse the proven `dpo_v04_pod_run.sh` cycle shape; same Mistral template, R-F1353 consistency).
- Optional small DPO **only on injection/abstention** pairs (now we know DPO must match the metric — R-F1528 lesson). Gentle config (beta 0.3 / lr 2e-6, proven stable).

### Stage 3 — Grounded eval + iterate
- Open-book 500-Q (Stage 0 harness) + **citation rate** + **hallucination rate** (claims not in context) + **abstention accuracy** (refuses iff context empty). Promote only if grounded-acc up AND hallucination not worse.

### Stage 4 (LATER) — Agentic tool-use
- Train the model to emit `tool_calls` (function-calling) using `aria_cli/agent.py` traces + `_detect_tool_intent` mappings as supervision, so the *model* (not a regex) selects tools. Higher effort; gate on Stages 0-3 succeeding.

### Stage 5 — Wire live + measure
- `ARIA_LLM_URL` → the grounded model; A/B vs DeepSeek on production grounded answers (`chat_audit.grounded_rate`, honesty judge). Phase-A honesty gates apply.

---

## 5. Bulletproofing — honesty baked into BOTH training and eval
The Constitution clauses (`aria_engine.py:129-200`) become **explicit training targets + eval gates**:
- Clause 14/15 → train: cite-or-don't-claim; eval gate: hallucination-rate (facts not in context) must be ~0.
- Clause 24 → train: single-source = `[ASSESSED]` not `[CONFIRMED]`; eval: calibration check.
- Abstention → train + eval: refuses **iff** context empty (no over-refusal, no over-claim).
This makes the grounded model *more* trustworthy than closed-book, which is the entire Phase-A honesty thesis.

---

## 6. Risks + mitigations
| Risk | Mitigation |
|---|---|
| Synthetic grounded data teaches "parrot the context" not reason | mix abstention + distractor-context pairs; eval hallucination-rate on adversarial/empty context |
| Train/serve context-format drift (the v0.2 collapse class) | use the EXACT production context format (`get_rag_context_with_sources` block) in training rows |
| `ARIA_CHAT_TRAIN_CAPTURE_TEXT` off → can't mine real traces | fall back to synthesized corpus (sufficient for Stage 1); flag to operator to enable for richer data |
| Retrieval→answer linkage is fragmented (Agent-1 gap #11/12) | Stage 1 source #1 (synthesize) doesn't need the linkage; #2 (mine) uses trace_stream which DOES link Q→context→answer per request |
| Over-fit (DPO lesson) | SFT-first; DPO only gentle + metric-matched |
| Cost creep | each stage is gated; Stage 0 alone may reframe everything before larger spend |

## 7. Cost / effort
- Stage 0: ~$3 / ~1h / no new training — **highest information per dollar.**
- Stage 1: mostly offline (DeepSeek API for synthesis, ~$? bounded) + my build time.
- Stage 2: ~$4 / ~2.5h GPU.
- Stage 3: folded into Stage 2 eval.
- Stages 4-5: scoped after 0-3.

## 8. THE immediate next step
**Run Stage 0** — measure v0.4-SFT open-book. One ~$3 eval reframes the entire program: it tells us how much of the "low score" is missing-evidence vs missing-skill, and quantifies the prize. Build = extend the eval set with a `context` field (RAG-populated) + prepend in `eval_aria_llm.py` + citation-aware judge. No training, no risk, decisive.
