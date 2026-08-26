"""build_regional_mastery_curriculum — Claude-authored curriculum targeting the
ZERO cells on ARIA's live mastery heatmap (R-F4340 / C-285).

OPERATOR, 2026-08-26: "go deep and go wide in security and defence ... as you
can see what is already showing on aria's heat map and mastery in aria's brain
and lets go even deeper and ensure she is a master piece."

TARGETED FROM THE LIVE HEATMAP, NOT FROM GUESSWORK.
`GET /api/aria/student/mastery/heatmap` on aria-intel, read 2026-08-26:

    14 topics x 16 regions
    sampled_cells 219 | measured_cells 211 | measured_weak_cells 83
    gate_2_floor_target 0.70

The weakest cells sit at 0.000 and they CLUSTER, which is what makes them
teachable rather than random:

    sanctions      lusophone, latam_lusophone, latam_non_lusophone,
                   south_asia, balkans, southeast_asia        <- 6 regions
    relationships  central/north/southern/east/west africa,
                   southeast_asia                             <- 6 regions
    compliance     latam_lusophone, latam_non_lusophone, southeast_asia
    procurement    europe, nato, latam_non_lusophone
    technical      southern_africa
    competitor_intel north_africa

Phase A gate #2 is the heatmap floor (CLAUDE.md §1). Its own note warns the
floor "is measuring STARVATION, not capability" — a cell at 0.000 or 0.507 has
had no real observation, not a failed one. So this curriculum is aimed at cells
that were never taught, which is the honest way to move that gate: teach the
region, do not re-weight the gauge. §1 lists the ways of closing gate #2 that
are FORBIDDEN (dropping regions, truncating the breach list, leaving cells
hidden) — none of them are used here.

EVERY ANSWER STILL CARRIES A DECISION, per the golden-intel north star's named
gap (value density). Regional knowledge that stops at description is the
"assess country risk" answer the north star rejects.

CONTAMINATION. Authored from general domain knowledge; not derived from the
eval's expected_answer fields, and no eval question reproduced. The pre-flight
checks PROMPT overlap only, so answer-level discipline is human.

CONFIDENCE. Only `high` rows ship. Regional sanctions and procurement facts
change, so each answer is written to be durable — naming the MECHANISM and the
issuing body rather than a designation list that goes stale, and saying
explicitly where the reader must check the current instrument. A curriculum
that teaches a snapshot as a fact would train ARIA to state stale designations
with confidence, which is the worst outcome in a due-diligence product.

Usage:
  python scripts/train/build_regional_mastery_curriculum.py \
      --out data/training/aria_regional_mastery_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM = (
    "You are ARIA, a defence and security due-diligence intelligence agent. "
    "Name the specific mechanism — the issuing body, the instrument, the "
    "regional organisation — and then say what the operator should DO. "
    "Designation lists change: state where the current instrument must be "
    "checked rather than asserting a snapshot as permanent fact. Never invent "
    "an agency, a statute or a designation."
)

PAIRS: list[dict] = [
    # ================= SANCTIONS x LATIN AMERICA =================
    {
        "id": "sanctions_latam", "topic": "sanctions", "region": "latam_non_lusophone",
        "confidence": "high",
        "q": "How do sanctions actually work in Latin America, and what should a supplier check before a transaction there?",
        "a": (
            "The dominant regime is unilateral rather than multilateral. OFAC (Office of Foreign "
            "Assets Control, US Treasury) runs country programmes — Venezuela, Cuba and Nicaragua "
            "are the long-standing ones — plus the global list-based programmes such as SDNTK for "
            "narcotics trafficking. The EU maintains its own restrictive measures on some of the "
            "same countries, and they are NOT identical in scope. UN Security Council measures "
            "cover far less of the region than most people assume.\n\n"
            "The practical consequence: a counterparty can be lawful for an EU exporter and "
            "blocked for a US-touching transaction, and the trigger is often not nationality but "
            "US nexus — a dollar-clearing payment, a US-origin component, or a US person in the "
            "chain.\n\n"
            "What to check before transacting: whether the transaction has any US nexus at all "
            "(USD settlement is the usual one), the counterparty and its owners against both the "
            "SDN list and the EU consolidated list, and OFAC's 50 Percent Rule on the ownership "
            "chain. Check the current programme text on the issuing body's own site rather than "
            "relying on a cached list — Latin American programmes are amended frequently and by "
            "executive action."
        ),
    },
    {
        "id": "compliance_latam", "topic": "compliance", "region": "latam_lusophone",
        "confidence": "high",
        "q": "What compliance regime governs defence trade with Brazil and Lusophone Latin America, and what does an exporter have to do?",
        "a": (
            "Brazil controls defence and dual-use exports through an interministerial process led "
            "by the Ministry of Foreign Affairs, and it participates in the MTCR (Missile "
            "Technology Control Regime) and the NSG (Nuclear Suppliers Group) — participation "
            "that matters because it shapes what Brazil will license outbound. Brazil is a State "
            "Party to the Arms Trade Treaty.\n\n"
            "For an inbound supplier the binding constraint is usually not Brazilian law but the "
            "exporter's own: a European supplier needs its national licence plus EU Regulation "
            "2021/821 for dual-use, and a US-origin component pulls the transaction into ITAR "
            "re-export control regardless of where the parties sit.\n\n"
            "What the exporter must do: establish the export-control classification in the HOME "
            "jurisdiction first, obtain the national licence, and confirm end-use and end-user in "
            "writing. Then check local requirements — including anti-bribery exposure, since "
            "Brazil's Clean Company Law imposes strict liability on companies for corrupt acts, "
            "which makes agent and intermediary due diligence a compliance requirement rather "
            "than good practice. Check the current control list and licensing guidance on "
            "the issuing body's own publication before relying on a classification, "
            "since national lists are updated to track the regimes."
        ),
    },
    # ================= SANCTIONS x SOUTH ASIA / BALKANS / SE ASIA =================
    {
        "id": "sanctions_south_asia", "topic": "sanctions", "region": "south_asia",
        "confidence": "high",
        "q": "What controls a defence or dual-use transfer into South Asia, and what is the trap?",
        "a": (
            "The binding controls are the multilateral non-proliferation regimes rather than "
            "country sanctions. India and Pakistan are outside the NPT as nuclear-weapon "
            "possessors, so supplier-state policy — the NSG (Nuclear Suppliers Group) and the "
            "MTCR — governs what may be transferred, together with national lists. India "
            "participates in the MTCR, the Wassenaar Arrangement and the Australia Group; "
            "Pakistan does not.\n\n"
            "The trap is that this is a POLICY constraint, not a designation. A screening run "
            "against sanctions lists returns clean because there is no listing to find, and the "
            "exporter concludes the transfer is permissible. The refusal, when it comes, is on "
            "end-use grounds under national implementation of a regime control list.\n\n"
            "What to do: classify the item against the national control list before approaching "
            "the customer, establish end use and end user in writing, and treat catch-all "
            "controls as live — most regimes allow a licence to be required for an unlisted item "
            "where the exporter is aware of a WMD or military end use. Screening clean is not "
            "licensable. Check the current national control list and the relevant regime's "
            "published guidance rather than a cached classification, because regime "
            "membership and list content both change."
        ),
    },
    {
        "id": "sanctions_balkans", "topic": "sanctions", "region": "balkans",
        "confidence": "high",
        "q": "How should a supplier approach sanctions and export risk in the Western Balkans?",
        "a": (
            "The EU maintains restrictive measures relating to the Western Balkans, and several "
            "states in the region are EU candidates that progressively align with EU Common "
            "Foreign and Security Policy declarations — alignment that is partial and differs by "
            "country, which is precisely what makes the region hard to assess.\n\n"
            "The dominant practical risk is not designation but DIVERSION and re-export. The "
            "region has established defence manufacturing and brokering activity, so the question "
            "is usually whether goods will remain where the paperwork says.\n\n"
            "What to do: verify the end user independently rather than accepting a certificate, "
            "check whether the state has aligned with the specific EU measure at issue (alignment "
            "is declared per-measure, not blanket), and treat brokering as a separately licensable "
            "activity in its own right — most EU member states license brokering distinctly from "
            "export, and a company arranging a transfer between two third countries can require a "
            "licence even though nothing crosses its own border."
        ),
    },
    {
        "id": "sanctions_sea", "topic": "sanctions", "region": "southeast_asia",
        "confidence": "high",
        "q": "What sanctions exposure should a defence supplier expect in Southeast Asia?",
        "a": (
            "Myanmar is the principal designated exposure: the EU, the US, and the UK each "
            "maintain measures, including arms embargoes and entity designations, and their scope "
            "differs by regime. ASEAN itself imposes no sanctions — it operates by consensus and "
            "non-interference — so there is no regional list to screen against, which regularly "
            "misleads people into treating the region as low-risk.\n\n"
            "The second exposure is secondary: US measures targeting transactions with sanctioned "
            "Russian or Chinese defence entities can reach a Southeast Asian buyer that operates "
            "mixed-origin fleets, because the exposure follows the counterparty rather than the "
            "geography.\n\n"
            "What to do: screen against the US, EU and UK lists separately rather than assuming "
            "equivalence, establish whether the end user operates equipment from a sanctioned "
            "supplier that your support would service, and check the arms-embargo scope — several "
            "embargoes cover training, maintenance and technical assistance as well as hardware, "
            "so a services contract can breach an embargo that a goods sale would not. Check "
            "the current measure text on each issuing body's own site before relying "
            "on scope: the US, EU and UK amend these independently."
        ),
    },
    {
        "id": "sanctions_lusophone", "topic": "sanctions", "region": "lusophone",
        "confidence": "high",
        "q": "What sanctions and embargo picture applies across Lusophone Africa, and how should it be checked?",
        "a": (
            "The controlling instruments are UN Security Council measures where they exist, plus "
            "EU and US programmes, applied country by country. There is no Lusophone bloc regime — "
            "the shared language is not a shared legal framework, and treating it as one is the "
            "characteristic error.\n\n"
            "Angola and Mozambique are the significant defence markets. The historical UN arms "
            "embargoes in the region were lifted long ago, so the live constraint is usually the "
            "EXPORTING state's own licensing policy and end-use assessment rather than a "
            "designation on the destination.\n\n"
            "What to do: check the current UN Security Council consolidated list and the relevant "
            "sanctions committee for the specific country rather than relying on historical "
            "knowledge, since embargoes are imposed and lifted by resolution. Then apply the "
            "exporting state's criteria — for an EU exporter, the eight criteria of Common "
            "Position 2008/944/CFSP, where regional stability and internal repression are the "
            "grounds most often engaged. Screen the counterparty ownership chain regardless: "
            "designations attach to people and companies, not to countries."
        ),
    },
    # ================= RELATIONSHIPS x AFRICA =================
    {
        "id": "relationships_africa_bodies", "topic": "relationships", "region": "west_africa",
        "confidence": "high",
        "q": "Which regional bodies matter for defence engagement in Africa, and why does it change who you talk to?",
        "a": (
            "The African Union (AU) sets continental policy and mandates peace-support operations "
            "through its Peace and Security Council. Beneath it sit the Regional Economic "
            "Communities that carry the security mandate in practice: ECOWAS in West Africa, SADC "
            "in Southern Africa, EAC and IGAD in the East, and ECCAS in Central Africa. Several "
            "have standby-force arrangements and have deployed missions.\n\n"
            "Why it changes the counterpart: a requirement generated by a regional mission is "
            "funded and specified differently from a national procurement. Mission-driven "
            "requirements often route through the mandating body or an external funder — the UN, "
            "the EU, or a bilateral partner — rather than the host ministry, so the ministry may "
            "be the user and not the buyer.\n\n"
            "What to do: identify the FUNDER and the contracting authority before positioning, "
            "because they are frequently not the end user. Where a mission is externally funded, "
            "the applicable procurement rules and eligibility conditions are the funder's, and "
            "those conditions — not the relationship with the ministry — determine whether you "
            "can bid."
        ),
    },
    {
        "id": "relationships_africa_engagement", "topic": "relationships", "region": "east_africa",
        "confidence": "high",
        "q": "What governs how a defence supplier should build relationships in East Africa without creating compliance exposure?",
        "a": (
            "Two constraints operate at once. Commercially, defence access in the region typically "
            "runs through local agents and representatives. Legally, that same intermediary is the "
            "single largest source of corruption exposure, and the exposure is extraterritorial: "
            "the US FCPA and the UK Bribery Act both reach conduct abroad, and the UK Act creates "
            "a corporate offence of failing to prevent bribery for which the only defence is "
            "adequate procedures.\n\n"
            "So the relationship strategy and the compliance strategy are the same decision, not "
            "two.\n\n"
            "What to do: conduct documented due diligence on any agent before appointment — "
            "beneficial ownership, political exposure, and whether the commission is proportionate "
            "to services actually rendered. Contract the relationship with audit rights and "
            "anti-bribery warranties, pay against evidenced deliverables rather than success fees "
            "where possible, and keep the file. An agent introduced by the customer is a higher "
            "risk, not a lower one, and 'the ministry recommended them' is not diligence."
        ),
    },
    {
        "id": "relationships_sea", "topic": "relationships", "region": "southeast_asia",
        "confidence": "high",
        "q": "How should a supplier approach defence relationships and offset expectations in Southeast Asia?",
        "a": (
            "Most significant buyers in the region operate formal offset or industrial "
            "participation policies, administered by a defence-industry body within or alongside "
            "the ministry, and expectations centre on local content, technology transfer and "
            "training. Fleet composition is frequently mixed-origin, which makes "
            "interoperability and sustainment a live commercial argument rather than a technical "
            "footnote.\n\n"
            "What to do: establish the offset requirement and its multiplier rules at capture "
            "stage rather than at contract, because it is priced into the bid and carries "
            "liquidated damages. Confirm which local activities the administering body actually "
            "credits. Then test the technology-transfer element against your OWN export-control "
            "regime before promising it — a transfer the home state will not license cannot be "
            "offered, and discovering that after award converts an offset commitment into a "
            "penalty.\n\n"
            "Where the fleet is mixed-origin, check whether supporting it touches equipment from "
            "a sanctioned supplier, since the service can be restricted even when the sale is not."
        ),
    },
    # ================= PROCUREMENT x EUROPE / NATO =================
    {
        "id": "procurement_nato", "topic": "procurement", "region": "nato",
        "confidence": "high",
        "q": "How does NATO procurement actually work, and what determines whether a company can win work?",
        "a": (
            "NATO buys in three distinct ways and they have different rules. NSPA (NATO Support "
            "and Procurement Agency) runs common-funded and nation-delegated acquisition and "
            "sustainment. The NCIA (NATO Communications and Information Agency) handles C4ISR and "
            "communications. And the overwhelming majority of NATO-relevant spend is NATIONAL — "
            "member states buying their own equipment to meet NATO capability targets.\n\n"
            "What determines eligibility: for common-funded work, a company generally competes "
            "through its national delegation and must be registered and sponsored — being "
            "technically capable is not sufficient and not the gating factor. Framework agreements "
            "and basic ordering agreements are frequently the actual route to work, so presence on "
            "the framework beforehand decides who can bid at all.\n\n"
            "What to do: register through the national NATO delegation or national NSPA point of "
            "contact first, pursue framework inclusion as an objective in its own right, and "
            "separate NATO-badged opportunities from national ones — they are advertised, "
            "competed and awarded under different rules, and conflating them wastes capture "
            "effort."
        ),
    },
    {
        "id": "procurement_europe", "topic": "procurement", "region": "europe",
        "confidence": "high",
        "q": "What rules govern European defence procurement, and where do suppliers actually lose?",
        "a": (
            "Directive 2009/81/EC governs defence and security procurement in the EU, with notices "
            "published on TED (Tenders Electronic Daily). Article 346 TFEU allows a member state "
            "to exempt a contract where essential security interests are at stake — an exemption "
            "that is narrower than commonly assumed but is used, and when used removes the "
            "competition entirely. The European Defence Fund co-finances collaborative R&D under "
            "eligibility conditions concerning EU ownership and control of participants.\n\n"
            "Where suppliers lose: not on technical score but on eligibility and process. Security "
            "clearance requirements for personnel and facilities that cannot be obtained within "
            "the timetable; national-content or industrial-participation conditions; security-of-"
            "supply undertakings that a non-domestic bidder cannot credibly give; and missed "
            "clarification windows, which are usually the only lawful moment to challenge a "
            "restrictive specification.\n\n"
            "What to do: read the eligibility and security clauses before the technical "
            "requirement, decide bid/no-bid on those, and use the clarification window "
            "deliberately — after it closes, a restrictive spec is settled."
        ),
    },
    {
        "id": "procurement_latam", "topic": "procurement", "region": "latam_non_lusophone",
        "confidence": "high",
        "q": "What characterises defence procurement in Spanish-speaking Latin America, and what should a supplier plan for?",
        "a": (
            "Procurement is predominantly national and ministry-led, budgets are exposed to "
            "currency and fiscal cycles, and there is no regional procurement body equivalent to "
            "NSPA or OCCAR — so each country is a separate market with its own rules. Government-"
            "to-government routes, including US FMS, are common for major platforms because they "
            "carry financing and support arrangements a commercial sale does not.\n\n"
            "What to plan for: budget authorisation and actual disbursement are separate events "
            "and can be separated by a long interval or by a change of administration, so an award "
            "is not cash. Local partnering or registration requirements are frequently a "
            "precondition to bid rather than an advantage. And anti-bribery exposure through "
            "agents is the principal compliance risk, reaching the supplier extraterritorially "
            "under the FCPA or the UK Bribery Act.\n\n"
            "What to do: qualify the opportunity on whether the funding line is appropriated and "
            "disbursable, not on the announcement; confirm the registration or local-partner "
            "precondition early because it has lead time; and diligence any intermediary before "
            "appointment, with the file kept."
        ),
    },
    # ================= TECHNICAL / COMPETITOR =================
    {
        "id": "technical_southern_africa", "topic": "technical", "region": "southern_africa",
        "confidence": "high",
        "q": "What technical and sustainment factors decide defence equipment outcomes in Southern Africa?",
        "a": (
            "Environment and sustainment dominate, more than headline performance. Equipment faces "
            "heat, dust and long logistics distances, and availability is limited by the spares "
            "and maintenance chain rather than by the platform's specification. Fleets are often "
            "mixed-origin and ageing, so obsolescence management and the ability to integrate with "
            "existing systems matter more than a capability increment.\n\n"
            "South Africa is the region's significant defence-industrial base and a possible "
            "partner or competitor depending on the segment, which changes whether a local "
            "relationship is a route to market or a barrier.\n\n"
            "What to do: lead the technical proposition with availability and through-life "
            "support — demonstrated MTBF (mean time between failures) in comparable conditions, ILS (integrated logistics support) provisioning, spares "
            "provisioning, and local maintenance capability — rather than peak performance. Check "
            "whether sustaining the existing fleet touches equipment of sanctioned origin, since "
            "the support contract can be restricted where the original sale was not, and confirm "
            "any technology transfer implied by local maintenance is licensable from the home "
            "state."
        ),
    },
    {
        "id": "competitor_north_africa", "topic": "competitor_intel", "region": "north_africa",
        "confidence": "high",
        "q": "How should a supplier read the competitive landscape in North Africa?",
        "a": (
            "The defining feature is supplier diversification as deliberate policy. States in the "
            "region have historically operated mixed inventories — Russian, Chinese, European and "
            "US origin in the same force — and treat multi-sourcing as strategic autonomy rather "
            "than an accident of history. Competition is therefore rarely a straight product "
            "comparison.\n\n"
            "What actually decides outcomes: financing terms, offset and local-industrialisation "
            "commitments, technology transfer, political relationship, and the credibility of "
            "long-term support. A technically superior offer without financing frequently loses "
            "to an inferior one with it.\n\n"
            "What to do: map the incumbent by SEGMENT rather than by country — an incumbent in "
            "armour tells you little about air defence — and identify what the customer cannot "
            "obtain from its existing suppliers, because that gap is the entry point. Then test "
            "early whether the transfer and support the customer will demand is licensable from "
            "your home state, since a US or European supplier faces constraints a competitor may "
            "not, and that constraint is a competitive fact, not a footnote."
        ),
    },
]


def _rows(include_medium: bool) -> list[dict]:
    out: list[dict] = []
    for p in PAIRS:
        if p["confidence"] != "high" and not include_medium:
            continue
        out.append({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": p["q"]},
                {"role": "assistant", "content": p["a"]},
            ],
            "topic": f"regional_{p['topic']}_{p['region']}",
            "mastery_topic": p["topic"],
            "region": p["region"],
            "confidence": p["confidence"],
            "source": "claude_authored:R-F4340",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--include-medium", action="store_true")
    args = ap.parse_args()

    rows = _rows(args.include_medium)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n")
    import collections
    print(f"wrote {len(rows)} rows -> {args.out}")
    print("  mastery topics:", dict(collections.Counter(r["mastery_topic"] for r in rows)))
    print("  regions       :", len({r["region"] for r in rows}))
    print(f"  all high-confidence: {all(r['confidence'] == 'high' for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
