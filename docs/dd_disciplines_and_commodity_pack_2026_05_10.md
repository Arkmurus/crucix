# ARIA Expert DD Framework + Regulated-Commodity Pack

**Date**: 2026-05-10 (R-F152)
**Triggered by**: operator request to (a) cover oil/LNG/crude DD with industry-360 knowledge, (b) ensure ARIA is *expert*, no corner cutting, (c) make adverse-media depth real (deep search, not surface). Same standard applies to defence + security.

---

## What was built

Two modules — each large, each self-contained, each opt-in to the existing DD pipeline.

### 1. `aria_service/intel/dd_disciplines.py` — the DD discipline framework

The structural backbone. Defines **30+ DD disciplines** across three tiers:

- **UNIVERSAL** (any commercial counterparty): identity_verification, sanctions_screening, pep_screening, **adverse_media**, ubo_chain, source_of_funds, source_of_wealth, litigation_history, regulatory_enforcement, operational_substance, financial_soundness, reputational_intelligence, banking_verification, insurance_verification, jurisdiction_country_risk, anti_bribery_corruption, modern_slavery_human_rights, cyber_data_protection, tax_fiscal_evasion, environmental_esg
- **DEFENCE-SPECIFIC**: end_use_verification, reexport_diversion_risk, technology_classification, nato_stanag_compliance, defence_offset
- **COMMODITY-SPECIFIC**: price_cap_attestation, vessel_tracking_ais, cargo_quality_specifications, contractual_structure, storage_inventory_verification

For each discipline:
- Definition (what it is)
- Why it matters (the failure mode it prevents)
- Evidence sources (where the data actually lives, with primary URLs)
- Verification procedure (how to actually check, not just claim)
- Common failure modes
- Output format (what a covered finding looks like in a DD report)

**Key new function: `adverse_media_query_templates()`** — addresses the operator's "really dig in deep, not just say so" point. Returns 20-50 STRUCTURED query templates per entity, targeting:
- US federal court records (CourtListener)
- UK case law (BAILII)
- International arbitration (LCIA, ICC, ICSID)
- US regulators (OFAC, DOJ, SEC, BIS)
- UK regulators (FCA, OFSI, SFO)
- ICIJ leak databases (Pandora, Panama, Paradise, OpenLux)
- Investigative journalism (OCCRP, Bellingcat, Reuters, FT, Bloomberg, WSJ)
- News archive depth (10+ years)
- Wayback Machine for removed content
- Sector-specific trade press (defence: Defense News / Janes / Shephard / Breaking Defense; energy: Argus / Platts / Tradewinds / Lloyd's List)

This is what makes adverse-media REAL — it's a programmatic search across 30+ source classes per entity, not a single "search the entity name" call.

**Key new function: `discipline_coverage_check()`** — grade a DD report against the disciplines REQUIRED for the entity type. Surfaces gaps explicitly. A buyer asking "did you check X?" gets a yes-with-evidence or a documented why-not, not silence.

**Key new function: `required_disciplines(entity_type)`** — declarative list per entity type:
- `defence_broker`: 21 disciplines
- `defence_oem`: 16 disciplines
- `commodity_broker_oil_lng`: 24 disciplines
- `commodity_broker_crude`: 23 disciplines
- `commodity_trader_major`: 14 disciplines
- `individual_pep_check`: 10 disciplines
- `financial_counterparty`: 13 disciplines

### 2. `aria_service/intel/regulated_commodity_pack.py` — the industry-360 knowledge

The **substantive knowledge** ARIA needs to be credible in oil/LNG/crude conversation. 18 sections:

1. Russia G7+ oil price cap (rules, attestation, common failures)
2. Iran SDGT shipping designations
3. Venezuela GL framework
4. Maritime dark-fleet indicators
5. **Domain-name red-flag heuristics** (the textbook `lngtradinginternationalpanamasa.com` pattern)
6. Public API entry points (is_commodity_dd_target, enrich_dd_report)
7. **OPEC + OPEC+ structure** (membership 2026, recent departures, decision bodies, voting dynamics)
8. **Major players**: 16 NOCs (Saudi Aramco, ADNOC, Rosneft, NIOC, Petrobras, Sonangol, NNPC, etc.) + 7 IOCs (Exxon, Shell, BP, Chevron, Total, Eni, ConocoPhillips) + 9 independent traders (Vitol, Glencore, Trafigura, Mercuria, Gunvor, etc.)
9. **Crude benchmarks**: Brent, WTI, Dubai/Oman, Urals, ESPO, Bonny Light, Murban, Maya, Mars
10. **Price Reporting Agencies**: Platts, Argus, OPIS, ICIS, EIG (how prices are FORMED)
11. **Shipping**: 12 vessel classes (VLCC → ULCC → Suezmax → Aframax → Panamax → MR/LR1/LR2 → Q-Max LNG → FSRU) + 6 chokepoints + freight indices
12. **Refining**: economics (crack spreads, Nelson Complexity), 7 major hubs
13. **LNG market**: 9 top exporters, top importers, pricing mechanisms (JCC/JKM/TTF/Henry Hub/Brent-linked), contract structures (long-term SPA, spot, FSRU), broker role
14. **Trade lifecycle**: 13 stages from Origination → Settlement
15. **Market events**: scheduled (OPEC, EIA, IEA, MOMR) + unscheduled (geopolitical, weather, sanctions)
16. **Energy transition**: CBAM, EEXI/CII, Methane regulations, carbon pricing
17. **Russia post-2022 dynamics**: redirected flows, dark fleet, alternate insurance/payment
18. **Industry knowledge summary** (callable for ARIA self-knowledge)

Plus 7 commodity-specific fraud patterns (advance-fee, fake POP, phantom cargo, false seller mandate, LNG paper broker, Russia price-cap evasion, vessel dark-fleet match).

---

## How this changes ARIA's behaviour

**Before today**: ARIA's DD pipeline ran 10 layers + applied 5 output guards + cited 188 sources. But the *disciplines* were implicit — a buyer asking "what do you check?" got a layer description, not a discipline catalogue. Adverse-media was "we web-search the entity" rather than a structured 30-source-class deep dive. Oil/LNG was "general DD applied to a commodity entity" rather than industry-aware analysis.

**After today's commit, with operator opt-in**:
- ARIA can answer "what do you check on a defence broker?" with 21 named disciplines, each with documented evidence sources and verification procedure
- ARIA can answer the same for an LNG broker with 24 disciplines including industry-specific ones (price-cap attestation, vessel tracking, cargo quality)
- Adverse-media generates 20-50 structured queries per entity targeting specific source classes (court / regulator / leak / journalism / news archive / trade press) — not a single noisy keyword search
- Industry conversations on oil / LNG / crude can reference OPEC structure, the trader oligopoly, benchmark grades, freight indices, refining economics, LNG contract structures — at expert level, not surface
- DD reports get a `discipline_coverage_check` at the end → gaps are surfaced explicitly rather than hidden

---

## Integration status (operator decisions pending)

Both modules are **implemented but NOT yet integrated** — opt-in by design, per the ecosystem-audit discipline.

### Integration option A — minimum (recommended first step)

Wire `dd_disciplines.discipline_coverage_check()` into `dd_orchestrator._run_synthesis` so every DD report includes a coverage section. Lift signal/noise without changing what ARIA does — just makes what it did (or didn't) explicit.

**Effort**: 30 min. **Risk**: low.

### Integration option B — adverse-media depth

Wire `dd_disciplines.adverse_media_query_templates()` into `researcher.deep_research` so adverse-media discipline runs the structured query set rather than the current ad-hoc search. Each template result feeds the same web_search backends already in place.

**Effort**: 2-4h (template execution + result aggregation + tier classification). **Risk**: medium (adds search load — circuit-breakers already there).

### Integration option C — commodity opt-in

Wire `regulated_commodity_pack.is_commodity_dd_target()` into `dd_orchestrator._detect_dd_intent` so commodity-shaped targets automatically trigger commodity enrichment. ARIA reports get the commodity_compliance section + fraud-pattern matching + commodity-specific bullets in chat.

**Effort**: 2-3h. **Risk**: medium (operator should review false-positive rate on the heuristic before auto-enabling).

### Integration option D — chat surface

Wire `dd_disciplines.discipline_summary_for_chat(entity_type)` so when a chat user asks "what does your DD cover?" ARIA returns the full discipline list with definitions. Surfaces ARIA's depth as a competitive advantage.

**Effort**: 1h (route + chat-intent matching). **Risk**: low.

**My recommendation**: ship A immediately (next session, after operator review of the modules). Then ship B (the adverse-media depth — your priority). C and D are valuable but lower urgency.

---

## What's deliberately NOT in scope

Honest about gaps:

- **Real-case adverse-media calibration** — the templates target the right source classes, but knowing which findings matter on Arkmurus's specific deal patterns is operator-domain
- **Live OPEC quota math** — module knows the structure but doesn't track current month's voluntary cuts; use OPEC MOMR + IEA monthly for that
- **Real-time crude prices** — structure not pricing; use Platts / Argus / EIA for live numbers
- **Vessel-specific sanctions designations** — the discipline is documented but the data feed (OFAC SDN by IMO + Equasis) needs to be wired in, ideally as a new Python source adapter
- **Trader-internal ownership** — Vitol / Trafigura / Mercuria are PRIVATE; only public-domain pieces included
- **Specific Arkmurus fraud cases** — the pattern catalogue is from public ICC IMB / FBI / INTERPOL bulletins; Arkmurus's actual experience is a separate corpus you'd add

These gaps are documented inside both modules (`industry_knowledge_summary.deliberate_gaps`) so when ARIA is asked, she answers honestly per Clause 7 (knowing limits).

---

## Ecosystem note

**ARIA's positioning is unchanged**: defence-broking + compliance specialist. The commodity pack is *adjacent capability*, not a vertical pivot. The home page, BD pitch, and brand stay defence. The pack lets ARIA serve commodity-broker requests using the same DD discipline without rebranding — answering operator's earlier framing.

`dd_disciplines.py` formalises ARIA's defence DD claim AT THE SAME TIME — the defence-specific disciplines (end_use, re-export, ECCN classification, NATO STANAGs, offset) are documented with the same rigour. Same standard, both verticals, no corner cutting.
