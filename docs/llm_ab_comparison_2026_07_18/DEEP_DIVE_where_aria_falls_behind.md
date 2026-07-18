# Deep dive — where ARIA is falling behind (and what to do)

**Produced by Claude Code, 2026-07-18.** Evidence base: the 6 DD reports + 10 authored answers in this folder. Every claim below traces to a specific run.

Goal framing (operator): *a robust, bulletproof product that compounds and delivers real value.* This document separates **real product gaps** from **local test artifacts**, then gives compounding recommendations.

---

## The 4 real product gaps

### Gap 1 — ARIA's current brain (DeepSeek) fabricates grounding and misses contradictions
**This is the most important finding.** In the authored path, DeepSeek repeatedly:
- Tagged **domain knowledge as evidence**: `[LEDGER — CONFIRMED through 2027-03]` for Rheinmetall's CEO and "Porsche ~15.1% from your evidence layers" (q1) — none of which was in the evidence.
- Wrote a **confident dossier on empty evidence** (q3, Leonardo): specific settlement figures tagged `[CONFIRMED]` when the search had returned zero usable content.
- **Missed a real contradiction** (q4): the evidence said Leonardo is "not in SEC EDGAR" yet cited an 8-K; DeepSeek presented the 8-K as Leonardo's. Claude caught it and diagnosed the *Leonardo DRS* subsidiary conflation.

For a decision-grade DD product this is the core liability: **a customer can receive a confidently-wrong report.** Claude, on the same inputs, separated `[CONFIRMED]` from `[GENERAL KNOWLEDGE — VERIFY]`, refused to invent figures, and caught the data problems. That gap in *honesty under pressure* is exactly what "bulletproof" cannot have.

> This is the single strongest argument for the operator's stated DeepSeek→Claude direction — independent of the GDPR driver.

### Gap 2 — the real quality ceiling is EVIDENCE COVERAGE, not the LLM
Leonardo scored AMBER-LIGHT from **both** providers, and both authored answers were starved — because:
- **Italy's registry isn't wired** (Registro Imprese is not among ARIA's 23 automated registries; only GB via Companies House is live).
- **The Leonardo digital sweep failed entirely** — all 23 retrieved sources were redirects / cookie walls / consent screens, "zero intelligence value."

No LLM swap fixes this. For a Milan-listed prime, returning "registry incomplete, OSINT empty" is a **coverage failure**, and it caps report quality regardless of brain. **This is where ARIA falls behind a competent human analyst the most** — not in reasoning, but in getting the evidence in the first place.

### Gap 3 — the DD-report path under-uses the LLM (double-edged)
DD verdicts are 86–99% identical across providers because the report is **deterministic templating over structured sources** — the LLM only shapes queries/extraction, it does not author analysis. 
- **Strength:** verdicts are evidence-grounded, auditable, and model-independent (a genuine moat — lean into it).
- **Gap:** the *analytical "so what"* layer customers pay for (comparative reasoning, materiality weighting, the narrative a human analyst writes) is barely present in the structured report. The authored-path answers (q1/q4) show what that layer *could* look like — and it's currently disconnected from the DD report the customer receives.

### Gap 4 — Claude integration is not switch-ready
Three concrete blockers surfaced (all fixable, all cheap):
1. **Provider is not thinking-aware.** `claude-sonnet-5` uses extended thinking; `AnthropicProvider.complete()` only reads `type=="text"` blocks and doesn't budget for thinking → with the default `max_tokens`, thinking consumes the whole budget and ARIA gets an **empty answer** (observed: q1/q3/q4 returned 0 chars until `max_tokens` was raised to 8000). **If ARIA flipped primary to a thinking Claude model today, chat would silently break.**
2. **Cost observability undercounts caching.** `LLMResult` has no cache-token fields, so ARIA's cost tracker undercounts cached Claude calls (~30% under here). The $300/mo cap (§17) could be mis-tracked or blown silently.
3. **83.5K-char system prompt** = ~36K input tokens/turn. On Claude that's ~$0.11/turn of input alone before any answer. Caching (R-F2760) softens repeat calls, but it's a structural cost multiplier and it slows generation.

---

## Not-a-gap: local test artifacts (called out for honesty)
- **Native segfaults** in search/extract on this Windows / Python-3.14 box — local-only instability; production is Linux and unaffected. The runs were completed by feeding the already-gathered DD evidence to the synthesis step.
- **Local OSINT was degraded** (no Brave key locally) until keys were pulled from the live app and validated — fixed *before* any scored run.
These say nothing about ARIA's production quality; they are recorded so no one mistakes them for product defects.

---

## Recommendations (ranked by compounding value)

1. **Fix evidence coverage first — it compounds across every customer.** Wire Italy (Registro Imprese) and Germany (Bundesanzeiger/Unternehmensregister) registries; fix extraction on cookie-wall/consent-screen sources (the Leonardo failure mode). This raises quality for *every* DD regardless of LLM, and closes the biggest real gap.

2. **Harden the honesty layer against the model, not just with it.** Add a verifier rule: any `[CONFIRMED]`/`[LEDGER]` tag must resolve to a real evidence id, else auto-demote to `[UNVERIFIED]`. This makes ARIA robust to a fabricating brain — a compounding safety property that pays off no matter which model is primary.

3. **Two-tier the brain, don't wholesale-swap it.** Keep a cheap/fast model for extraction, classification, and coverage; route the **final decision-grade synthesis** (chat, executive narratives, comparative analysis) to Claude, where the honesty + contradiction-catching is worth the cost. ARIA already has the `model_router` two-track scaffolding for exactly this. **Do not** move the deterministic DD-report path to Claude — no quality gain, ~15× cost.

4. **Before any Claude primary flip, ship the 3 integration fixes** (thinking-aware parsing + token budget, cache-token cost tracking, and a system-prompt diet). Then re-cost each tier against the $300/mo cap — free-tier DD + deep research on Claude is real spend.

5. **Connect the analytical layer to the DD report.** The authored answers (q1/q4) are markedly better "analysis" than the structured DD narrative. Route a final Claude synthesis pass over the *already-gathered, evidence-grounded* DD data to produce the customer-facing "so what" — grounded, cited, honest. This is the highest-leverage way Claude improves the actual product.

6. **Market the model-independence as a moat.** Evidence-grounded, model-independent verdicts are honest and auditable — a real differentiator. The brain choice is about the *analytical* layer, not the verdict.

---

## One-line answer to "where does ARIA stand?"
ARIA's **verdicts are solid and honest** (evidence-grounded, model-independent). ARIA falls behind in two places: **(a) evidence coverage** (missing registries + extraction failures cap real-world quality), and **(b) the analytical/narrative layer**, where its current brain (DeepSeek) is prone to confident fabrication — the one behavior a bulletproof product cannot ship. Claude fixes (b) at ~15× cost and needs 3 small integration fixes first; nothing fixes (a) except investing in coverage.
