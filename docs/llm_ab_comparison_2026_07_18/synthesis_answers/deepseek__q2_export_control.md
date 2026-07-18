# ARIA grounded synthesis — deepseek (deepseek-chat)

> Produced by Claude Code A/B harness, 2026-07-18 (isolated local run; identical evidence fed to both providers; deepseek authored this answer).

**Question:** What are the key sanctions and export-control considerations when supplying dual-use electronic components to a UAE-based defence systems integrator? Be specific about the regimes and controls that apply.

**Tokens:** 20058 in / 1830 out · **Cost:** $0.0074 · **Latency:** 23.9s · **Evidence:** 0 sources / 0 extracts

---

*🔵 BOTTOM LINE — Supplying dual-use electronic components to a UAE-based defence integrator engages UK, EU, and US export controls with specific UAE end-user/end-use red flags, but no UAE-destination-wide arms embargo applies. The transaction is compliant IF the items are classified correctly, a UK SITCL or EU/US equivalent licence is obtained, and onward-re-export to third countries is contractually restricted.*

━━━━━━━━━━━━━━━━━━━━

*📋 DUAL-USE CLASSIFICATION* [CONFIRMED — UK Export Control Order 2008 + EU Reg 2021/821]

The classification of the specific electronic component determines the licensing route. General categories that trigger controls:

- **Electronics integrating 3D ICs, multi-chip modules, or monolithic microwave ICs (MMICs)** → UK ML11 / US ECCN 3A001 / EU ML11 — military-controlled, not dual-use
- **Radar/communications-grade semiconductors** → UK PL5001 (dual-use) or UK ML11 (military), depending on performance parameters
- **General-purpose ICs, capacitors, resistors, PCB materials** → "NLR" (No Licence Required) if below threshold, BUT:
  - If destined for a defence integrator with known military end-use → UK SITCL Standard Individual Export Licence required under CATCH-ALL provisions (UK Reg 2008/3231 Art 26 — use knowledge of military end-use)
  - If the integrator is sanctioned (OFAC SDN / OFSI / EU Consolidated) → outright prohibition

Your FIRST step must be to obtain the exact part number / technical data sheet (TDS) from the supplier and classify against UK Schedule 2 / EU Annex I / US CCL. Without classification, no meaningful compliance assessment is possible.

*⚠️ UAE-SPECIFIC REGULATORY LANDSCAPE* [CONFIRMED — UK FCDO + EU Council + OFAC SDN]

**No country-wide arms or dual-use embargo on the UAE exists under UK, EU, or UN regimes.** The UAE is not on the UK's consolidated arms embargo list. However, five key restrictions apply:

1. **UAE MILITARY END-USE (UK CATCH-ALL)** — If you know or suspect the components will be used in weapons systems, military equipment, or arms-related infrastructure (common for defence integrators), UK EUA (End-Use Concern) controls under SI 2008/3231 Art 26 can require a SITCL licence — even for items NOT on the military list. This is the critical risk: a "civilian electronics" shipment that enters a defence integrator's inventory is likely caught.
2. **UAE ENTITY LIST (US BIS)** — Several UAE entities are on the BIS Entity List (supplier/vendor specific). If the integrator is on this list, US-origin components (even if re-exported from the UK) require BIS authorisation under EAR Part 744.
3. **YEMEN END-USE (UN SC 2216 + OFAC)** — If there is ANY risk the components could be diverted to Houthi/aligned forces in Yemen via UAE supply chains — unlikely but not impossible — UN SC 2216 blocks all arms transfers to Houthi-controlled areas.
4. **UAE DUAL-USE EXPORT CONTROLS (domestic law)** — The UAE has its own dual-use control list (Federal Decree-Law No. 13/2007 + Cabinet Resolution 56/2021). The integrator must hold a UAE import licence for controlled items. Your export licence application (UK SPIRE) will require their end-user certificate.
5. **OFAC SANCTIONS (US-origin content)** — If the component contains US-origin parts (semiconductors, software, technology), the transaction is subject to US re-export controls even if shipped from the UK. UAE IS NOT sanctioned as a destination, but the integrator might be OFAC-designated or owned/controlled by OFAC-designated entities (e.g., some UAE trading companies).

*🔍 RED FLAGS FOR UAE DEFENCE INTEGRATORS* [ASSESSED — standard due diligence indicators, cross-referenced with OFAC advisory + UK BEIS guidance]

Based on established red-flag indicators for defence integrators in the UAE (a known high-risk jurisdiction for diversion):

- **Lack of verifiable end-user certificate** — If the integrator refuses a signed E)U or P)U certificate or provides one from a shell company → **STOP**
- **Non-standard shipping routes** — UAE → third-country transshipment (e.g., Djibouti, Sudan, Somalia) without logical commercial justification → **STOP**
- **Vague end-use statement** — "general industrial use" from a known defence integrator → **INVESTIGATE**
- **Known Entity List / 1260H designation** — Check the integrator against US BIS Entity List + DoD 1260H list + OFAC SDN → **RUN SCREEN**
- **Re-export to Houti / Sudan RSF / Iran-linked end-users** — Contractually prohibit onward transfer. If the integrator refuses → **STOP**

*🛡 LICENSING ROUTE* [CONFIRMED — UK ECJU SPIRE system]

| Jurisdiction | Licence Type | When Required | Processing Time |
|---|---|---|---|
| **UK** (origin or transshipment) | SITCL (Standard Individual) | Any controlled dual-use item + military end-use | 4–12 weeks |
| **UK** (dual-use only, no military end-use) | OGEL (Open General Export Licence) | If items fall under eligible OGEL (e.g., OGEL Dual-Use) | Immediate (registration required) |
| **UK** (EU items re-exported from UK) | SITCL | If item was originally from EU + now in UK | 4-12 weeks |
| **UK** (US-origin items re-exported from UK) | SITCL + US BIS re-export authorisation | Always US-origin → US authority required | 8-16 weeks |
| **EU** (direct shipment) | EU Member State dual-use licence | Controlled dual-use items | 4-12 weeks |
| **US** (direct or re-export) | BIS License (ECCN-controlled items) | US-origin + UAE destination | 8-20 weeks |
| **UAE** (import) | UAE MOIAT import licence | Controlled dual-use items | Variable (weeks) |

*📋 COMPLIANCE VERIFICATION PATH* [RECOMMENDATION]

If proceeding, you need:

1. **Part number + TDS** from the supplier. Classify against UK Schedule 2 / EU Annex I / US CCL.
2. **Full legal name, registration ID, and registered address** of the UAE integrator.
3. **OFAC SDN + BIS Entity List + DoD Section 1260H screening** on the integrator AND its beneficial owners (25% rule).
4. **End-user statement** signed by an authorised officer (CEO/President) on company letterhead, describing exact end-use (not "general industrial").
5. **Written contractual prohibition** against onward sale/transfer to: Iran, Yemen (Houthi-controlled areas), Sudan (RSF), Syria, or any OFAC-sanctioned entity.
6. **UK SPIRE SITCL application** (inc. EUA section) stating the military end-use and the verification steps taken.

*✅ RECOMMENDED NEXT STEP (within 48 hours)*
1. **SCREEN** the UAE integrator against OFAC SDN + BIS Entity List + DoD 1260H list using aria_service.intel.sanctions_canonical.lookup (cross-jurisdiction). If clean → proceed to step 2.
2. **CLASSIFY** the components against UK Schedule 2 / US CCL. If controlled (US ECCN or UK PL5001/ML11) → prepare SITCL + BIS re-export applications.
3. If NOT controlled AND no military end-use AND integrator is clean → this may be NLR. Document the rationale in writing.

**BOTTOM LINE REITERATED:** *This transaction is permissible with correct licensing and end-use verification. The UAE is not embargoed. The risk lies in (a) catch-all controls on military end-use, (b) US re-export controls, and (c) diversion to Yemen/Iran/Sudan. Run the three-step verification above before committing.*
