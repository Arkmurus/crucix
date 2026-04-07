"""Seed ARIA with comprehensive military procurement compliance knowledge."""
import httpx
import json
import time

ARIA_URL = "https://aria-intel.fly.dev"

DOCUMENTS = [
    {
        "filename": "arms_trade_treaty.txt",
        "source": "compliance_seed",
        "context": "Arms Trade Treaty — primary international framework for conventional arms transfers",
        "content": """ARMS TRADE TREATY (ATT) 2014 — COMPLETE REFERENCE

The ATT is the primary international framework governing conventional arms transfers. 113 States Parties.

KEY OBLIGATIONS:
- Article 6: PROHIBITIONS — Cannot authorise transfer if: violates UN Security Council arms embargoes; seller has knowledge arms would be used for genocide, crimes against humanity, grave breaches of Geneva Conventions, attacks against civilians or civilian objects.
- Article 7: EXPORT ASSESSMENT — Before authorising export, must assess potential that arms could: undermine peace/security, be used to commit serious violations of IHL, serious violations of human rights law, terrorism, transnational organised crime. If overriding risk exists, SHALL NOT authorise the export.
- Article 8: IMPORT — Importing State shall provide end-use/end-user documentation to exporting State upon request.
- Article 9: TRANSIT/TRANSHIPMENT — Each State shall take measures to regulate transit through its jurisdiction where necessary.
- Article 10: BROKERING — States must regulate brokering activities within their jurisdiction, including registration requirements.
- Article 11: DIVERSION — States must take measures to prevent diversion. Includes risk assessment, mitigation measures, cooperation on diversion cases.
- Article 12: RECORD-KEEPING — Maintain records for minimum 10 years of: authorisations granted, actual transfers, details of items, quantity, value, end users.
- Article 13: REPORTING — Annual report to ATT Secretariat on authorised exports and imports.

ARMS CATEGORIES (Article 2):
1. Battle tanks  2. Armoured combat vehicles  3. Large-calibre artillery systems
4. Combat aircraft  5. Attack helicopters  6. Warships  7. Missiles and missile launchers
8. Small arms and light weapons (added by consensus)

For Arkmurus: ATT compliance is mandatory. Every brokered deal must be assessed against Article 7 criteria. Document the assessment. Keep records 10+ years.""",
    },
    {
        "filename": "uk_export_controls.txt",
        "source": "compliance_seed",
        "context": "UK Strategic Export Controls — licences, SECL, Consolidated Criteria, SPIRE system",
        "content": """UK STRATEGIC EXPORT CONTROL FRAMEWORK — COMPLETE REFERENCE

PRIMARY LEGISLATION:
- Export Control Act 2002 — primary enabling legislation
- Export Control Order 2008 (ECO 2008) — implements the Act, defines controlled goods
- Trade in Goods (Control) Order 2003 — controls brokering and trading of military goods

LICENCE TYPES:
1. SIEL (Standard Individual Export Licence) — specific goods, specific consignee, specific destination. Valid 2 years. Most common for defence equipment.
2. OIEL (Open Individual Export Licence) — multiple shipments of specified goods to specified destinations. Valid up to 5 years. For repeat exporters.
3. OGEL (Open General Export Licence) — pre-published, no application needed. Limited to low-risk goods/destinations.
4. SITEL (Standard Individual Trade Control Licence) — for brokering/trading controlled goods between two overseas countries. This is what Arkmurus typically needs.
5. OITCL (Open Individual Trade Control Licence) — for repeat brokering to pre-approved destinations.

UK STRATEGIC EXPORT CONTROL LIST (SECL):
- ML1: Smooth-bore weapons < 20mm, firearms, accessories
- ML2: Smooth-bore weapons >= 20mm, other weapons
- ML3: Ammunition and fuse setting devices
- ML4: Bombs, torpedoes, rockets, missiles
- ML5: Fire control and surveillance equipment
- ML6: Ground vehicles and components
- ML7: Chemical/biological agents
- ML8: Military explosives
- ML9: Naval vessels and components
- ML10: Military aircraft and components
- ML11: Electronic equipment for military use
- ML12: High velocity kinetic energy weapons
- ML13: Armoured equipment and constructions
- ML14: Specialised military training equipment
- ML15: Imaging and countermeasure equipment
- ML16: Forgings and castings for military use
- ML17: Miscellaneous military equipment
- ML18: Production equipment for military items
- ML19: Directed energy weapon systems
- ML20: Cryogenic and superconductive equipment
- ML21: Software for military items
- ML22: Technology for military items

8 CONSOLIDATED CRITERIA (must assess ALL before granting licence):
1. UK's international obligations and commitments (UN embargoes, OSCE, EU commitments)
2. Respect for human rights in destination country
3. Internal situation in destination (tensions, armed conflicts)
4. Regional peace, security, and stability
5. National security of UK, allies, and friendly countries
6. Behaviour of buyer country (terrorism, organised crime, international law compliance)
7. Risk of diversion or re-export to undesirable end user
8. Compatibility with technical and economic capacity of recipient

SPIRE SYSTEM: Online portal for all licence applications (spire.trade.gov.uk)
Processing time: 20-60 working days (complex cases longer)
ECJU provides technical assessment and end-use monitoring

For Arkmurus: Most deals require SITEL (brokering licence). Apply via SPIRE. Prepare EUC from buyer. Assessment against all 8 criteria is mandatory. Keep all records.""",
    },
    {
        "filename": "end_user_certificates.txt",
        "source": "compliance_seed",
        "context": "End User Certificate (EUC) procedures, requirements, red flags, templates",
        "content": """END USER CERTIFICATES (EUC) — COMPLETE GUIDE

PURPOSE: Legal document certifying identity of final recipient and intended end-use of controlled items. Required by virtually all export licensing authorities worldwide.

TYPES OF END-USE DOCUMENTATION:
1. End User Certificate (EUC) — Most comprehensive. Certifies final user, end use, non-re-export. Usually government-issued.
2. End User Statement (EUS) — Less formal. Acceptable for lower-risk transfers or when full EUC not obtainable.
3. International Import Certificate (IIC) — Importing government certifies goods will be imported into its territory.
4. Delivery Verification Certificate (DVC) — Post-delivery. Confirms goods arrived at stated destination. Sometimes required for high-risk transfers.
5. Non-Re-Export Certificate — Specific undertaking that goods will not be re-exported without permission.

ESSENTIAL ELEMENTS OF A VALID EUC:
1. Full legal name and physical address of end user (not PO Box)
2. Full description of goods including ML/ECCN classification
3. Quantity and value of goods
4. Stated end use — SPECIFIC, not vague ("for equipping 3rd Infantry Battalion" not "for defence purposes")
5. Non-re-export clause: "The items will not be re-exported, transferred, or diverted to any third party or country without prior written authorisation from [licensing authority]"
6. Non-modification clause (where applicable)
7. Signature of authorised government official with full name, title, and official stamp
8. Date of issue and unique reference number
9. Government letterhead, official stamp, or apostille
10. Contact details of certifying authority for verification

RED FLAGS IN EUC ASSESSMENT:
- Vague end-use ("for national defence" without specifics)
- End user different from consignee without clear explanation
- Reluctance to provide end-use assurances or answer questions
- Country with known diversion risk (check Wassenaar, ATT reports)
- Technical capability mismatch (small country buying advanced systems beyond force structure)
- Multiple intermediaries in the supply chain
- PO Box addresses or non-governmental premises as end-user address
- Previously denied licence applications for same destination
- End user is a trading company, not a government/military entity
- Inconsistencies between EUC and other documentation
- Unusual routing (goods transit through third countries unnecessarily)
- Cash payment or unusual payment methods
- Customer not willing to state end use or provide written assurances

EUC VERIFICATION BEST PRACTICES:
1. Cross-reference end user against sanctions lists (OFAC, EU, UK, UN)
2. Verify the signing official exists and holds stated position
3. Check if end-user country has EUC authentication procedures
4. Request pre-delivery inspection rights where possible
5. Maintain chain of custody documentation
6. Report suspected diversion to licensing authority immediately
7. Consider post-shipment verification for high-risk transfers

COUNTRY-SPECIFIC EUC REQUIREMENTS:
- UK: EUC must accompany SIEL/SITEL applications to ECJU
- France: Certificat de Destination Finale (CDF) — must be authenticated by French Embassy
- Germany: Endverbleibserklärung (EVE) — BAFA format required
- US: DSP-83 (Nontransfer and Use Certificate) for ITAR items
- Brazil: Certificado de Usuário Final (CUF) — requires Brazilian Army (DFPC) authentication
- Angola: EUC from Ministério da Defesa Nacional e Veteranos
- Mozambique: EUC from FADM via Ministério da Defesa Nacional

For Arkmurus: ALWAYS obtain EUC before applying for export/brokering licence. Verify authenticity independently. Keep originals on file for 10+ years. If anything looks wrong — STOP and seek legal advice.""",
    },
    {
        "filename": "itar_ear_reference.txt",
        "source": "compliance_seed",
        "context": "US ITAR and EAR export controls — critical for any deal involving US-origin components",
        "content": """US EXPORT CONTROLS: ITAR AND EAR — COMPLETE REFERENCE

ITAR (International Traffic in Arms Regulations):
- Administered by: DDTC (Directorate of Defense Trade Controls), US State Department
- Legal basis: Arms Export Control Act (AECA) 22 USC 2778
- Scope: Defence articles, defence services, and related technical data on USML

US MUNITIONS LIST (USML) CATEGORIES:
I. Firearms  II. Guns and Armament  III. Ammunition/Ordnance
IV. Launch Vehicles/Missiles  V. Explosives  VI. Surface Vessels
VII. Ground Vehicles  VIII. Aircraft  IX. Military Training Equipment
X. Protective Personnel Equipment  XI. Military Electronics
XII. Fire Control/Targeting  XIII. Materials and Miscellaneous
XIV. Toxicological Agents  XV. Spacecraft  XVI. Nuclear Weapons
XVII. Classified Articles  XVIII. Directed Energy Weapons
XIX. Gas Turbine Engines  XX. Submersible Vessels  XXI. Miscellaneous

CRITICAL ITAR RULES:
1. EXTRATERRITORIAL APPLICATION — ITAR applies to US persons globally AND foreign persons handling ITAR items
2. CONTAMINATION PRINCIPLE — ANY item containing US-origin ITAR-controlled components remains ITAR-controlled regardless of degree of integration or where assembled
3. REGISTRATION — All manufacturers, exporters, and brokers of USML items must register with DDTC (even if only brokering)
4. LICENCE TYPES: DSP-5 (permanent export), DSP-73 (temporary export), DSP-61 (brokering), TAA (Technical Assistance Agreement), MLA (Manufacturing Licence Agreement)
5. BROKERING: Part 129 — requires separate registration AND per-transaction approval via DSP-5

EAR (Export Administration Regulations):
- Administered by: BIS (Bureau of Industry and Security), US Commerce Department
- Scope: Dual-use items on CCL (Commerce Control List)
- ECCNs format: Category (0-9) + Product Group (A-E) + 3 digits (e.g., 6A001)
- EAR99: Items not on CCL — generally freely exportable to most destinations
- Licence exceptions: TMP (temporary), RPL (replacement parts), GOV (government), etc.

KEY DIFFERENCE: ITAR = military items (State Dept, very strict). EAR = dual-use items (Commerce Dept, more flexible).

ARKMURUS IMPLICATIONS:
1. Before ANY deal, check if equipment contains US-origin ITAR components
2. If yes, US export licence required EVEN IF equipment is manufactured outside US
3. Arkmurus may need DDTC broker registration if regularly handling ITAR items
4. ITAR violations carry penalties up to $1M per violation and 20 years imprisonment
5. Even technical discussions about ITAR items with non-US persons can constitute a violation
6. Always check: Is the end user on the ITAR debarred list?

PRACTICAL CHECKLIST FOR ARKMURUS:
□ Identify if any items in the deal are on USML or CCL
□ If USML: DDTC broker registration required + DSP-5/DSP-61
□ If CCL: Check if licence required for destination (use BIS SNAP-R system)
□ Check end user against ITAR Debarred Parties List
□ Check end user against BIS Entity List, Denied Persons List, Unverified List
□ Document all checks and keep records
□ If in doubt: request Commodity Jurisdiction (CJ) determination from DDTC""",
    },
    {
        "filename": "sanctions_and_brokering.txt",
        "source": "compliance_seed",
        "context": "Sanctions regimes, brokering controls, anti-corruption in defence procurement",
        "content": """SANCTIONS REGIMES AND BROKERING CONTROLS — COMPLETE REFERENCE

UN SECURITY COUNCIL ARMS EMBARGOES (current):
- Central African Republic (2127/2588) — partial, exemptions for government
- DRC (1807/2641) — on non-governmental entities
- Iraq — legacy restrictions on certain items
- Lebanon — Hezbollah arms embargo
- Libya (1970/2571) — comprehensive arms embargo
- Mali (2374/2640) — targeted measures
- North Korea (1718/2627) — comprehensive, strictest regime
- Somalia (733/2662) — partial, exemptions for government security forces
- South Sudan (2206/2625) — arms embargo
- Sudan — Darfur (1556) and new regime
- Yemen — on designated individuals/entities (Houthis)

EU AUTONOMOUS ARMS EMBARGOES (beyond UN):
Belarus, China (since 1989), Myanmar/Burma, Russia, Syria, Zimbabwe
Plus: targeted measures on individuals (asset freezes, travel bans)

UK SANCTIONS (post-Brexit, under SAMLA 2018):
- OFSI administers financial sanctions
- ECJU administers trade sanctions (arms embargoes)
- UK Global Human Rights Sanctions Regulations 2020
- Russia sanctions: most comprehensive UK regime, over 1800 designations

BROKERING CONTROLS — UK:
Trade in Goods (Control) Order 2003:
- Category A: Goods used for torture, CBRN, cluster munitions — ALWAYS controlled
- Category B: ML items to embargoed destinations — ALWAYS controlled
- Category C: ML items to non-embargoed destinations — controlled if broker is in UK or UK person
- No formal registration required (unlike US)
- Must apply for SITEL (per transaction) or OITCL (open)
- Extra-territorial: applies to UK persons anywhere in the world

BROKERING CONTROLS — US:
- ITAR Part 129: Must register with DDTC as broker
- Must obtain DSP-5 approval for each brokering activity
- Applies to US persons AND foreign persons if US-origin items involved
- Annual registration fee
- Penalties: up to $1M fine and 20 years imprisonment per violation

ANTI-CORRUPTION IN DEFENCE:
UK Bribery Act 2010:
- Section 1: Active bribery (offering/giving)
- Section 2: Passive bribery (requesting/receiving)
- Section 6: Bribery of foreign public officials
- Section 7: FAILURE TO PREVENT BRIBERY — corporate offence, strict liability
- Defence: company had "adequate procedures" to prevent bribery
- NO facilitation payment exception (unlike US FCPA)

ADEQUATE PROCEDURES (6 principles):
1. Proportionate procedures
2. Top-level commitment
3. Risk assessment
4. Due diligence on agents and intermediaries
5. Communication and training
6. Monitoring and review

AGENTS AND INTERMEDIARIES — HIGHEST RISK AREA:
- Must conduct enhanced due diligence on all agents
- Written agreement specifying services, fees, compliance obligations
- Fee must be proportionate to services (not success fee for "introductions")
- Know your agent's background, connections, reputation
- Monitor agent activities throughout relationship
- Report any red flags immediately

For Arkmurus: Maintain a compliance manual covering all the above. Train all staff. Document every decision. Use agents carefully with full due diligence. When in doubt, don't proceed — seek legal advice.""",
    },
]

def main():
    client = httpx.Client(timeout=180.0)
    total_facts = 0

    for doc in DOCUMENTS:
        print(f"Feeding: {doc['filename']}...")
        try:
            r = client.post(
                f"{ARIA_URL}/api/aria/read-document",
                json=doc,
            )
            if r.status_code == 200:
                data = r.json()
                facts = data.get("facts_learned", 0)
                hyps = data.get("hypotheses_generated", 0)
                total_facts += facts
                print(f"  -> {facts} facts learned, {hyps} hypotheses")
            else:
                print(f"  -> Error {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"  -> Failed: {e}")
        time.sleep(2)

    # Also teach explicit facts
    EXPLICIT_FACTS = [
        ("UK SITEL licence", "Arkmurus requires a SITEL (Standard Individual Trade Control Licence) for each brokering deal arranging transfer of controlled military goods between two overseas countries. Apply via SPIRE.", "CONFIRMED"),
        ("EUC requirement", "End User Certificate must be obtained from the buying government BEFORE applying for any export or brokering licence. Must include: full name/address of end user, goods description with ML category, quantity, specific end use, non-re-export clause, authorised official signature with stamp.", "CONFIRMED"),
        ("ITAR contamination", "Any item containing US-origin ITAR-controlled components remains ITAR-controlled regardless of where it is manufactured or integrated. This is called the contamination principle. Arkmurus must check every deal for US-origin content.", "CONFIRMED"),
        ("ATT Article 7", "Arms Trade Treaty Article 7 requires assessment before authorising any export. If overriding risk that arms could undermine peace/security, violate IHL, or violate human rights law, the export SHALL NOT be authorised.", "CONFIRMED"),
        ("UK Bribery Act Section 7", "Under Section 7 of the UK Bribery Act 2010, Arkmurus can be criminally liable for failing to prevent bribery by persons associated with it, unless it can prove it had adequate procedures in place. This is a strict liability offence.", "CONFIRMED"),
        ("Brokering extraterritorial", "UK brokering controls under the Trade in Goods (Control) Order 2003 are extraterritorial — they apply to UK persons anywhere in the world. Arkmurus staff must comply regardless of where they are physically located.", "CONFIRMED"),
        ("Record keeping 10 years", "Both the ATT and UK export control legislation require maintaining records of all authorised transfers for a minimum of 10 years. Arkmurus must retain all EUCs, licence applications, correspondence, and transaction records.", "CONFIRMED"),
        ("Angola EUC authority", "Angola End User Certificates are issued by the Ministério da Defesa Nacional e Veteranos. Must be on official letterhead with authorised signature and ministry stamp.", "CONFIRMED"),
        ("Mozambique EUC authority", "Mozambique End User Certificates are issued through FADM (Forças Armadas de Defesa de Moçambique) via the Ministério da Defesa Nacional.", "CONFIRMED"),
        ("Sanctions screening obligation", "Before any engagement, Arkmurus must screen all parties (end user, consignee, intermediaries, financing entities) against: UN consolidated list, EU sanctions list, UK sanctions list (OFSI), US OFAC SDN list, and any country-specific lists relevant to the transaction.", "CONFIRMED"),
    ]

    for topic, content, confidence in EXPLICIT_FACTS:
        try:
            r = client.post(
                f"{ARIA_URL}/api/aria/knowledge/fact",
                json={"topic": topic, "content": content, "confidence": confidence},
            )
            if r.status_code == 200:
                print(f"  Fact stored: {topic}")
        except:
            pass

    print(f"\nDone. Total facts from documents: {total_facts}")
    print(f"Plus {len(EXPLICIT_FACTS)} explicit facts stored.")

if __name__ == "__main__":
    main()
