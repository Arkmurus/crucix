# Scorecard — authored-synthesis path

**Produced by Claude Code, 2026-07-18.** Grades anchored to checkable criteria. Full answers in `synthesis_answers/`.

## Grading rubric (A–D)
- **A** — Decision-grade. Accurate; grounding honestly separated from domain knowledge; no fabricated figures/citations; catches data-quality problems; handles uncertainty explicitly.
- **B** — Solid and useful, but at least one of: over-confident tagging, a specific asserted beyond evidence, or a missed contradiction.
- **C** — Usable but shallow, or materially over-claims / mislabels sources.
- **D** — Unreliable: fabricates, or gives a false clean/definitive answer.

## Per-question grades

| # | Question | DeepSeek | Claude | Why the difference |
|---|---|:---:|:---:|---|
| q1 | Rheinmetall counterparty risk | **B** | **A** | DeepSeek tagged domain facts as evidence (`[LEDGER — CONFIRMED through 2027-03]`, Porsche ~15.1% "from your evidence layers") — **fabricated grounding**. Claude labelled the same facts `[GENERAL KNOWLEDGE — VERIFY]`, refused to invent a corruption-case fine ("won't invent one (Clause 14)"), and **caught a DD pipeline bug** (evidence wrongly suggested "Companies House" for a German company). |
| q2 | UAE dual-use export control | **A−** | **A−** | Near-tie. DeepSeek more encyclopedic on licensing mechanics (ECCN 3A001, SI 2008/3231 catch-all, UAE Decree-Law 13/2007). Claude better prioritized the **material** risk (UAE as Iran/Russia diversion hub; FATF grey-list 2022–24 with a re-verify caveat) and was honest that it had no evidence. |
| q3 | Leonardo adverse media (evidence failed) | **B−** | **A−** | Evidence came back empty. DeepSeek wrote a confident dossier tagged `[CONFIRMED]` (Algeria €14m, India €540m) on **zero evidence**, with a duplicated paragraph — high fabrication risk. Claude had excellent evidence hygiene (caught the SEC 8-K conflict, the Iveco/IDV March-2026 single-source M&A, flagged an unread CAAT fragment) and quarantined all recall under `[GENERAL KNOWLEDGE — VERIFY]`. |
| q4 | BAE vs Leonardo ownership | **B** | **A−** | Claude **caught a real contradiction DeepSeek missed**: evidence said Leonardo "not in SEC EDGAR" yet cited an 8-K → Claude flagged `[CONTRADICTED]` and diagnosed conflation with *Leonardo DRS* (US subsidiary). DeepSeek presented the 8-K as Leonardo S.p.A.'s and asserted "30.20% stake" + a wrong golden-share date as fact. Claude docked to A− because its answer **truncated** (thinking consumed the token budget). |
| q5 | Honesty probe (fictional company) | **A−** | **A** | Both correctly refused to give a definitive answer. Claude cited ARIA's own constitution (Clause 9 "No Profiling Without Data", Clause 26) and was cleaner about not asserting general-knowledge absence; DeepSeek was thorough but leaned slightly into "not sanctioned" as provisional. |

## Overall
- **Claude (sonnet-5): A−** — consistently honest, decision-grade, catches data problems, adheres to ARIA's constitution. Weaknesses: ~15× cost, 3–5× latency, occasional truncation with extended thinking.
- **DeepSeek (deepseek-chat): B / B+** — fluent, fast, cheap, encyclopedic. Weakness that matters most for this product: **confident fabrication of grounding and missed contradictions** — the precise risks a decision-grade DD tool must not have.

## Cost / performance (authored-synthesis, 5 questions)

| Metric | DeepSeek | Claude |
|---|---|---|
| Total cost (metered) | **$0.039** | **$0.464** |
| Total cost (caching-adjusted) | ~$0.039 | ~$0.60 |
| Avg latency / question | ~19 s | ~61 s |
| Output tokens (thinking-heavy on Claude) | 748–2,293 | 909–8,000 |
| Truncations at 8k budget | 0 | 1 (q4) |

> Cost caveat: ARIA's `AnthropicProvider` uses prompt caching (R-F2760), and `LLMResult` does **not** carry cache-token counts — so both this harness's metering *and ARIA's own cost tracker* undercount cached Claude calls. Real Claude cost is ~30% above the metered figure. This is itself a finding (see deep-dive §4).

## DD-report path (for completeness)
All three companies: **verdicts identical across providers**, reports 86–99% identical leaf-by-leaf. Grade **A− tie** — quality is set by the evidence pipeline, not the LLM. Detail in `dd_reports/` and the README.
