# ARIA LLM A/B — Claude (sonnet-5) vs DeepSeek (deepseek-chat)

**Produced by:** Claude Code (Opus 4.8) running as the operator's engineering agent
**Date:** 2026-07-18
**For:** Antonio (operator) — to answer *"where does ARIA stand on DD quality, Claude vs DeepSeek, and where is ARIA falling behind?"*
**Provider under test:** `claude-sonnet-5` (candidate) vs `deepseek-chat` (ARIA's current primary)
**Spend:** ~$0.65 of the $12 Claude credit (DD trio ~$0.19 + synthesis ~$0.46 metered / ~$0.6 caching-adjusted; DeepSeek ~$0.05 total).

> **Honesty note on grading.** These files were produced and graded by Claude, comparing Claude against a competitor. To keep that fair, every grade is anchored to **concrete, checkable criteria** (factual errors, fabricated grounding, missed contradictions, citation discipline) and the **full answers are included** in `synthesis_answers/` so you can judge for yourself. Where the two are close, that is stated.

---

## What was tested — two very different paths

ARIA has two LLM surfaces, and they behave completely differently under a provider swap:

### 1. The DD-report path (`orchestrate_dd`) — see `dd_reports/`
Three defence primes (BAE Systems plc, Leonardo S.p.A., Rheinmetall AG) run through ARIA's real deep-DD pipeline, once with each provider, identical evidence, isolated state.

**Result: the two providers are essentially indistinguishable here** (BAE 98.9% / Rheinmetall 95.8% / Leonardo 85.9% identical, leaf-by-leaf; all three risk verdicts matched). ARIA's DD report is **deterministic and evidence-driven** — risk classification, bottom line, findings and sources are computed from structured sources (GLEIF, Companies House, OpenSanctions, SEC EDGAR, Brave OSINT), **not authored by the LLM**. Swapping the brain does **not** change DD verdicts.

### 2. The chat/synthesis path (`aria_chat` → `complete_synthesis`) — see `synthesis_answers/`
Five defence/security intelligence questions where **the LLM actually authors the answer**. Identical gathered evidence fed to both providers; only the model differs. **This is where quality diverges** — and where the grading below lives.

---

## Headline verdict

| | Claude (sonnet-5) | DeepSeek (deepseek-chat) |
|---|---|---|
| **Authored-synthesis grade** | **A−** | **B / B+** |
| **DD-report grade** | A− (tie) | A− (tie) |
| **Honesty / no-fabrication** | Strong — separates evidence from knowledge, refuses to invent figures | **Weak — fabricates grounding tags, asserts unverified specifics** |
| **Catches data problems** | Yes — caught a real SEC-filing contradiction + template artifacts DeepSeek missed | No — glossed over them confidently |
| **Constitution adherence** | Cited ARIA's own clauses (9, 14, 17, 24, 26) | Generic |
| **Cost / turn (synthesis)** | ~$0.09–0.15 (~15× DeepSeek) | ~$0.006–0.009 |
| **Latency / turn** | 35–90 s (extended thinking) | 9–29 s |
| **Completeness** | Occasionally truncates (thinking eats the budget) | Consistently complete, encyclopedic |

**Bottom line:** For the *authored* path, Claude is materially **more honest and more decision-grade** — it catches the exact failure modes (confident fabrication, missed contradictions) that make a DD product dangerous. DeepSeek is faster, cheaper, and more encyclopedic, but **fabricates grounding** and **misses data contradictions** — unacceptable in a "bulletproof" product. For the *DD-report* path, the choice is a wash on quality and a ~15× cost multiplier.

See **`SCORECARD.md`** for per-question grades and **`DEEP_DIVE_where_aria_falls_behind.md`** for the gaps and what to do about them.

---

## Contents
- `SCORECARD.md` — per-question A/B/C/D grades + rationale, cost/latency table
- `DEEP_DIVE_where_aria_falls_behind.md` — concrete gaps (product vs test-artifact) + recommendations
- `synthesis_answers/` — all 10 authored answers in full (5 questions × 2 providers)
- `dd_reports/` — the 6 DD-report summaries (3 companies × 2 providers)

## Method (reproducibility)
- Harness: `orchestrate_dd(target, llm=<provider>)` and `complete_synthesis(<provider>, ...)` driven directly with each provider — no live deploy, no production impact.
- Isolation: throwaway state DB, `share_to_company=False`, synthetic user — zero tenant/production data touched.
- Evidence parity: for the synthesis path, evidence was gathered once (Brave OSINT + the DD runs' own findings) and fed identically to both providers, so the **LLM is the only variable**.
- Fidelity fix applied first: local search was degraded (no Brave key locally); the Brave/Companies-House/OpenSanctions/GNEWS keys were pulled from the live app into a gitignored `.env` and validated live before any run.
