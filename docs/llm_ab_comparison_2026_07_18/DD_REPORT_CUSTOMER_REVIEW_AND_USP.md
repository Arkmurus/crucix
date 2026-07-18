# DD reports — customer-acceptance review + the USP roadmap

**Produced by Claude Code, 2026-07-18/19.** Grounded in the three real deep-DD reports generated this session (BAE Systems plc, Leonardo S.p.A., Rheinmetall AG) — see `dd_reports/`. The question this answers: *would a paying defence/security customer accept these reports? If not, where, and how do we turn that into ARIA's USP?*

---

## The customer test: who reads this, and what do they need?

A real ARIA DD buyer is a **compliance officer at a defence prime, an M&A/investment analyst, or a broker structuring a cross-border deal.** They open a DD report to answer five questions, in order:

1. **Is this the right entity, and is its registry data verified?** (identity you can stand behind)
2. **Any sanctions / export-control exposure?** (the deal-killer check)
3. **Any adverse media, corruption, or litigation history?** (the reputational/legal check)
4. **Who ultimately owns and controls it?** (UBO / state influence)
5. **Is it financially sound enough to rely on for a multi-year commitment?** (solvency)

Judged against those five, here is how the **"clean GREEN" BAE report** actually scores.

| Customer question | What the report delivered | Accept? |
|---|---|---|
| 1. Verified identity | GLEIF record only; data-gap says *"registry unavailable — NOT registry-verified"* — **for a company that is in Companies House (`01470151`)** | ❌ |
| 2. Sanctions / export control | Clean multi-list screen — genuinely good | ✅ |
| 3. Adverse media / corruption | **EMPTY.** A GREEN "proceed" verdict with **zero** adverse media on a company with a famous ~$400M Al-Yamamah settlement | ❌❌ |
| 4. UBO / control | *"UBO walk budget exhausted at 50 nodes"* — incomplete | ❌ |
| 5. Financial health | *"UNKNOWN — not a US filer (no SEC EDGAR)"* — no financials for an LSE-listed prime | ❌ |

**Verdict: a paying customer would not accept this report.** It is well-structured and honest about *some* gaps, but hollow in the four sections that carry the decision — and worst of all, it issues a green "proceed" verdict that its own data does not support.

The same pattern holds across the trio: Leonardo came back AMBER only because Italy's registry isn't wired and its OSINT sweep failed entirely; Rheinmetall's financials and UBO were likewise UNKNOWN/incomplete.

---

## The root problem, stated plainly

ARIA's DD report today is a **well-built container that is mostly empty where it matters**, and its **verdict is computed before the decision-critical evidence is in**. Specifically:

- **Adverse media is not a verdict input.** The risk classification (`_run_synthesis` §6b) aggregates ghost-score, country-risk, sanctions, and commercial-coherence — *never adverse media*. The deep adverse-media search runs **detached, after** the verdict, and only for "triggered" targets. So a company's corruption history literally cannot change its GREEN verdict.
- **The authoritative registry isn't being used** even when the key works (Companies House found BAE instantly in a direct call, yet the DD reported "registry unavailable").
- **Financial health only works for US SEC filers** — every LSE/EU/DAX company returns UNKNOWN.
- **UBO resolution gives up at 50 nodes** and reports "budget exhausted" instead of the ownership chain.

None of this is an LLM problem. It is an **evidence-and-verdict-integrity problem** — which is exactly the layer a serious customer judges.

---

## ARIA's USP — what actually makes it worth buying

ARIA will **not** win on data breadth: Sayari, Kharon, Dun & Bradstreet and the paid aggregators have more sources and deeper graphs, and the constitution (§6/§17) rightly refuses to buy its way to parity. So the USP cannot be "more data."

**ARIA's USP is decision-grade honesty: the DD tool that never gives you a false clean, ties every material claim to a primary source or marks it unverified, and shows its work so you can audit it.** In a market where both human analysts and raw LLMs routinely produce confident, unsourced, occasionally-fabricated assessments (we measured exactly this — see `SCORECARD.md`), a report a compliance officer can **defend to a regulator** is the differentiator worth paying for.

That USP has two non-negotiable pillars, and today's reports break both:
1. **Never a false clean.** An empty/failed check must never read as "clean." (Broken: BAE's empty adverse-media → GREEN.)
2. **Decision-critical coverage, honestly graded.** The five questions above must each get a real, sourced answer or an explicit, honest gap — not a hollow section. (Broken: financials, registry, UBO.)

Everything below is in service of making those two pillars true.

---

## The roadmap (R-numbered, prioritised by USP impact × safety)

| R# | Change | USP pillar | Status |
|---|---|---|---|
| **R-F2779** | **Adverse-media never-false-clean disclosure** — when a dedicated adverse-media pass didn't run, the report explicitly says so and does not read as clean. | Never-false-clean | ✅ **implemented + capability-tested this session** |
| **R-F2780** | **Make adverse media a first-class verdict input** — run the deep adverse-media search inline (within budget), feed material findings into the risk classification, and update the GREEN-asserting DD tests to the honest contract (§23). | Never-false-clean | Reserved — needs its own focused cycle (verdict blast radius) |
| **R-F2781** | **Registry verification must fire for known-jurisdiction companies** — root-cause why Companies House isn't verifying a company it can find, and make identity registry-verified (status/directors/incorporation) whenever a registry is available. | Coverage | Reserved |
| **R-F2782** | **Financial health for non-US companies** — wire UK (Companies House accounts / filing history) and EU (Bundesanzeiger / registry filings) so LSE/DAX/EU primes get a real solvency read instead of UNKNOWN. | Coverage | Reserved |
| **R-F2783** | **Honesty tag → evidence-id binding** — any `[CONFIRMED]`/`[LEDGER]` tag in an authored/report claim must resolve to a real evidence id, else auto-demote to `[UNVERIFIED]`. Makes ARIA robust to a fabricating model (the DeepSeek failure mode from `SCORECARD.md`). | Never-false-clean | Reserved |

Also carried from the LLM deep-dive (not yet R-numbered, larger): fix cookie-wall/consent-screen extraction (the Leonardo OSINT total-failure), raise the UBO node budget with honest "chain continues" markers, and connect a final grounded-synthesis pass so the report carries a decision-useful narrative, not generic `next_actions`.

---

## One-line USP statement (for the pitch)
> **ARIA is the due-diligence tool that shows its work and never gives you a false clean** — every material claim tied to a primary source or plainly marked unverified, so the verdict is one you can put in front of a regulator. R-F2779 is the first brick; R-F2780–2783 lay the rest.
