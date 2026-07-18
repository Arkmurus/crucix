# ARIA grounded synthesis — claude (claude-sonnet-5)

> Produced by Claude Code A/B harness, 2026-07-18 (isolated local run; identical evidence fed to both providers; claude authored this answer).

**Question:** What are the key sanctions and export-control considerations when supplying dual-use electronic components to a UAE-based defence systems integrator? Be specific about the regimes and controls that apply.

**Tokens:** 202 in / 2730 out · **Cost:** $0.0416 · **Latency:** 36.6s · **Evidence:** 0 sources / 0 extracts

---

*🟡 BOTTOM LINE — Exporting dual-use electronics to a UAE integrator is legally routine but high-risk on diversion; treat every deal as INVESTIGATE until end-user, end-use and re-export chain are locked down.*

━━━━━━━━━━━━━━━━━━━━

*🧭 EVIDENCE STATUS*

No OSINT/tool evidence was returned for this query — the evidence block is empty. Everything below is [GENERAL KNOWLEDGE — VERIFY], drawn from established export-control doctrine, not from a live search or named-entity lookup. If you have a specific counterparty name, share it and I will run sanctions/registry screening properly rather than reasoning in the abstract.

━━━━━━━━━━━━━━━━━━━━

*📋 CLASSIFICATION* [GENERAL KNOWLEDGE — VERIFY]

1. Classify the item first — this drives everything else:
   - UK: Export Control Order 2008 / UK Strategic Export Control Lists (SITCL). Determine if it falls under Category 3 (Electronics) of the Wassenaar-derived dual-use list, or under a Military List (ML) entry if it's designed/modified for military use.
   - EU: Regulation (EU) 2021/821 (recast dual-use regulation), Annex I control list — again Category 3 Electronics is the most likely bucket for components.
   - US: If the component has US origin, US content, or US-controlled technology embedded, it may carry an ECCN under the EAR (Commerce Control List, Category 3 or 7 depending on function). If the component was specifically designed for military application it may instead sit under ITAR/USML — a much harder regime (no de minimis, DDTC licensing, broader re-export control).

2. The classification decision is the single most consequential step. A part that looks like "commercial electronics" but has military-grade tolerances, radiation hardening, or is destined for integration into a weapon system can flip from EAR dual-use to ITAR-controlled or ML-controlled instantly.

*⚠️ COMPLIANCE FLAGS* [GENERAL KNOWLEDGE — VERIFY]

1. UAE ITSELF — Not under UN, EU, UK or US comprehensive sanctions. It is a federal state with its own export-control regime (UAE Federal Law No. 13 of 2007 on Weapons, Ammunition, Military Equipment and Technology). That said, treat "no UAE sanctions" as necessary, not sufficient.

2. DIVERSION / TRANSSHIPMENT RISK — This is the material risk, not sanctions on the UAE itself. The UAE (particularly Dubai/Sharjah free zones) is a well-documented transshipment hub used to route sanctioned goods and dual-use electronics toward Iran and, since 2022, Russia, evading OFAC/EU/UK controls. UK OFSI, US BIS, and EU authorities have all issued specific enforcement guidance flagging UAE-based intermediaries as high-risk re-export nodes. This is doctrine, not a claim about any specific counterparty — I have no evidence naming your integrator as involved in diversion.

3. FATF STATUS — UAE was on the FATF grey list from March 2022 to February 2024 for AML/CFT deficiencies; it was removed in Feb 2024. That removal is itself now over a year old and should be re-verified against the current FATF list before you rely on it — do not assume "clean" status has persisted without checking.

4. US RE-EXPORT REACH — If any component contains >10% US-controlled content (or is subject to EAR's de minimis rules), a UAE re-export or onward transfer can trigger a US licensing requirement even with zero US company involved in the transaction. ITAR items carry NO de minimis threshold at all — any US-origin ITAR content anywhere in the supply chain keeps DDTC jurisdiction attached indefinitely.

5. END-USER / END-USE CERTIFICATION — For UK SITCL and EU dual-use licensing, an End-User Undertaking / EUC naming the UAE integrator AND the ultimate military end-user is standard practice, and increasingly mandatory where the buyer is a systems integrator rather than the terminal military user — integrators are exactly the entity type sanctions authorities flag for onward-transfer risk.

*💼 ARKMURUS POSITIONING* [ASSESSED]

UAE sits in Arkmurus's COLD-ENTRY tier — we have no established relationship capital with Emirati MoD or integrators comparable to our Lusophone Africa incumbency. Any deal here needs a documented, defensible compliance trail from day one, both because Arkmurus's reputation is on the line with UK/EU regulators, and because in a cold-entry market we cannot lean on relationship trust to smooth over a weak paper trail — competitors already established there will out-compete us on relationships, so our differentiator has to be compliance rigor and export-licensing competence, not warmth.

*✅ RECOMMENDED ACTION*

1. Get the exact part number / technical spec of the component and run it through ECCN/Wassenaar/ML classification before quoting anything
2. Identify whether any US-origin content or technology is embedded — this determines EAR vs ITAR exposure
3. Request full corporate details on the named UAE integrator (legal name, trade licence, free zone vs mainland registration) — share the name and I will run UAE MOCI/registry checks, OFAC/OFSI/EU consolidated screening, and adverse-media search
4. Obtain a signed End-User Undertaking naming the specific military/government end-user, not just the integrator
5. Check current FATF list status for UAE (last verified Feb 2024 delisting — confirm it still holds)
6. Route the licensing decision through UK ECJU/SPIRE (or EU competent authority) BEFORE shipment — do not rely on a general licence for anything ML-adjacent

*📅 NEXT STEP*

Share the specific component spec and the counterparty's legal name within 48 hours — I'll run ECCN classification, UAE registry lookup, and sanctions screening (OFAC/OFSI/EU/UN) rather than continuing on generic doctrine.
