# =============================================================================
# ARIA v3.1 — International Law in Defence and Security
# aria_service/intel/international_law.py
#
# The most comprehensive open-source knowledge module for international
# law as applied to global defence business development, procurement
# advisory, arms transfer compliance, and security operations.
#
# SCOPE: Global. Every major legal framework governing armed conflict,
#        arms transfers, accountability, and security operations.
#
# ARIA APPLICATION: This knowledge activates automatically when:
#   - Export licence risk is being assessed
#   - Counterparty due diligence is being conducted
#   - Hardware comparison involves prohibited or controlled items
#   - Country assessment touches on conflict, sanctions, or rule of law
#   - Contract review involves military goods or services
#   - BD advice requires understanding of legal architecture
#
# ALL SOURCES: Open-source, verifiable, citable.
# Primary: ICRC, ICJ, ICC, UN Treaty Collection, FATF, UNODC
# Secondary: RAND, RUSI, Chatham House, Opinio Juris, EJIL Talk
# =============================================================================

import json
import logging
import hashlib
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("ARIA.InternationalLaw")

# Placeholder to be replaced after initial sections are defined
_SECTIONS_PENDING = True

# =============================================================================
# SECTION 1 — LAW OF ARMED CONFLICT (LOAC) / INTERNATIONAL HUMANITARIAN LAW
# =============================================================================

LOAC_IHL = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — LAW OF ARMED CONFLICT (LOAC) / IHL                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
International Humanitarian Law (IHL) — also called the Law of Armed Conflict
(LOAC) or jus in bello — governs the conduct of hostilities and the protection
of persons during armed conflict. It is the foundational legal framework for
every military operation conducted by Arkmurus client forces. Understanding it
is not optional for credible defence BD advisory.

IHL matters to Arkmurus specifically because:
1. ECJU export licences are refused when equipment creates a "clear risk" of
   IHL violation by the recipient force
2. UK Consolidated Criteria (Criterion 2) requires IHL risk assessment
3. Arms Trade Treaty Article 7 requires IHL risk assessment before export
4. Arkmurus due diligence reports must include IHL record of client forces
5. Precision-guided munitions (PGMs) are a legitimate BD argument precisely
   because they reduce proportionality risk — ARIA must articulate this

─────────────────────────────────────────────────────────────────────────────
1.1 TREATY FOUNDATION — THE FOUR GENEVA CONVENTIONS (1949)
─────────────────────────────────────────────────────────────────────────────

Status: 196 states parties — universal ratification. Binding on all states.
Source: https://www.icrc.org/en/war-and-law/treaties-customary-law/geneva-conventions

Geneva Convention I: Protection of the Wounded and Sick in Armed Forces
in the Field
  Core provisions: duty to collect/care for wounded; protection of medical
  personnel/units/transports; distinctive emblem (Red Cross/Crescent/Crystal).
  Common Article 3: minimum protections in non-international armed conflict
  (NIAC). Prohibits: murder, torture, cruel treatment, hostage-taking,
  humiliating treatment, summary execution.

Geneva Convention II: Protection of the Wounded, Sick and Shipwrecked
Members of Armed Forces at Sea
  Naval equivalent of GC I. Hospital ships protected.

Geneva Convention III: Treatment of Prisoners of War (POW)
  POW status: members of armed forces of a party to conflict who fall into
  enemy hands. Protections: humane treatment, adequate food/clothing/shelter,
  protection from public curiosity, not compelled to provide information beyond
  name/rank/date of birth/service number. Interrogation limits.

Geneva Convention IV: Protection of Civilian Persons in Time of War
  Most extensive convention. Covers civilians in territory of adverse party and
  occupied territory. Prohibits: collective punishment, reprisals against
  civilians/property, deportation, pillage, humiliating treatment.
  Grave breaches: wilful killing, torture, wilfully causing great suffering,
  unlawful deportation, wilful deprivation of POW rights, extensive property
  destruction not justified by military necessity.

─────────────────────────────────────────────────────────────────────────────
1.2 ADDITIONAL PROTOCOLS (1977 and 2005)
─────────────────────────────────────────────────────────────────────────────

Additional Protocol I (AP I): International Armed Conflict
  174 parties. Non-parties: USA, Israel, India, Pakistan, Iran (significant).
  Key additions to GC:
  - Definition of civilian and civilian object
  - Principle of distinction codified
  - Proportionality rule codified (Article 51)
  - Precautionary measures (Articles 57-58)
  - Special protection for dams, nuclear plants, cultural property
  - Prohibition on starvation as method of warfare
  - Journalist protections
  - Mercenary definition (Article 47) — very narrow

Additional Protocol II (AP II): Non-International Armed Conflict
  168 parties. Supplements Common Article 3. Applies to armed conflicts
  between state armed forces and organised non-state armed groups with
  territorial control. Prohibits: violence to life, collective punishment,
  terrorism, slavery, humiliating treatment. Protects: civilians, medical,
  cultural property. Most African conflicts fall under AP II — critical for
  CPLP African market assessments.

Additional Protocol III (AP III): Red Crystal Emblem
  Adds Red Crystal as additional distinctive emblem. 80 parties.

─────────────────────────────────────────────────────────────────────────────
1.3 THE HAGUE REGULATIONS AND CONVENTIONS
─────────────────────────────────────────────────────────────────────────────

Hague Convention IV (1907) — Laws and Customs of War on Land:
  Annexed Hague Regulations: conduct of hostilities, treatment of POWs,
  occupied territory. Still binding as customary international law.
  Key rules: no poisoned weapons, no killing enemies who have surrendered
  (hors de combat), no pillage, quarter must be given, siege limitations.

Convention on Certain Conventional Weapons (CCW) 1980 + Protocols:
  Framework convention for weapons causing unnecessary suffering or
  indiscriminate effects. Amended Protocol II (mines, booby-traps):
  restrictions on AP mines in states not party to Ottawa Treaty.
  Protocol III (incendiary): restrictions on use against civilians.
  Protocol IV (blinding lasers): prohibition.
  Protocol V (explosive remnants): clearance obligations.
  Source: https://www.unog.ch/80256EDD006B8954/(httpAssets)/729B5C4C2CF2BC0AC1257240003E66A9/$file/CCW+text+and+protocols+english.pdf

─────────────────────────────────────────────────────────────────────────────
1.4 CUSTOMARY INTERNATIONAL HUMANITARIAN LAW
─────────────────────────────────────────────────────────────────────────────

ICRC Customary IHL Study (2005): Identifies 161 rules of customary IHL.
Binding on all states regardless of treaty ratification.
Source: https://ihl-databases.icrc.org/en/customary-ihl/v1

Most important customary rules for ARIA defence BD context:
  Rule 1: Distinction — parties must distinguish combatants/civilians
  Rule 11: Indiscriminate attacks prohibited
  Rule 14: Proportionality — excessive civilian harm prohibited
  Rule 15: Feasible precautions required
  Rule 70: Weapons causing superfluous injury/unnecessary suffering prohibited
  Rule 71: Indiscriminate weapons prohibited
  Rule 87: Humane treatment required for all persons in enemy power
  Rule 156: Serious violations of IHL are war crimes

─────────────────────────────────────────────────────────────────────────────
1.5 FOUR CORE IHL PRINCIPLES — PRACTICAL CONTENT
─────────────────────────────────────────────────────────────────────────────

DISTINCTION:
  Legal standard: Parties must at all times distinguish between: (a) combatants
  and civilians, (b) military objectives and civilian objects. Attacks may only
  be directed at military objectives. A military objective is a person/object
  which by nature, location, purpose, or use makes an effective contribution to
  military action AND whose destruction/capture/neutralisation offers definite
  military advantage.

  ARIA BD APPLICATION: When a client force has documented deliberate targeting
  of civilians (verified by OHCHR, ACLED, or HRW), this is a distinction
  violation. This is a mandatory adverse finding in any due diligence report
  and triggers UK Criterion 2 export licence risk. PGMs (Excalibur, JDAM,
  Paveway) improve distinction compliance — legitimate procurement argument.

PROPORTIONALITY:
  Legal standard: An attack is prohibited if it may be expected to cause
  incidental civilian casualties/damage which would be excessive in relation
  to the concrete and direct military advantage anticipated. Not an absolute
  prohibition — a contextual balancing test applied to each attack.

  Key case: Nils Melzer (UN Special Rapporteur) analysis of drone strikes.
  IDF vs Gaza: repeated OHCHR finding of disproportionate attacks.
  Saudi Arabia in Yemen: UK Court of Appeal 2019 — ECJU failed to adequately
  assess proportionality risk. Resulted in export licence suspension (later
  partially resumed after further assessment).

  ARIA BD APPLICATION: Precision guidance (CEP <5m vs CEP 200m unguided)
  directly reduces proportionality risk. When comparing M982 Excalibur
  (GPS, CEP <4m, $112k) vs unguided 155mm HE (CEP 100-200m, $800-1200),
  the legal argument for precision ammunition is legitimate and quantifiable.

PRECAUTION:
  Legal standard (AP I Article 57): Parties must take all feasible precautions
  to avoid/minimise civilian casualties. This includes: choosing means and
  methods causing least civilian harm, giving effective advance warning of
  attacks affecting civilians, cancelling or suspending attacks if civilian
  harm would be disproportionate.

MILITARY NECESSITY:
  Legal standard: Only measures that are actually necessary to achieve a
  legitimate military objective are permissible. Prohibits destruction of
  enemy property unless imperatively demanded by the necessities of war.
  Basis for: targeting decisions, detention authority, property requisition.

─────────────────────────────────────────────────────────────────────────────
1.6 TYPES OF ARMED CONFLICT — LEGALLY SIGNIFICANT DISTINCTION
─────────────────────────────────────────────────────────────────────────────

International Armed Conflict (IAC):
  Definition: armed conflict between two or more states.
  Full IHL treaty framework applies (all four GCs, AP I).
  Examples: Israel-Gaza (debated), Russia-Ukraine (IAC from Feb 2022),
  India-Pakistan (potential).

Non-International Armed Conflict (NIAC):
  Definition: prolonged armed conflict between a state and organised armed
  groups OR between organised armed groups. Common Article 3 + AP II.
  Examples: Mali (government vs JNIM/GSIM), Mozambique Cabo Delgado
  (government vs al-Shabaab), DRC (government vs M23/FDLR), Somalia,
  Ethiopia (multiple NIACs), Colombia (government vs ELN), Myanmar.
  Most African conflicts are NIACs. Most client forces in CPLP Africa
  operate under NIAC law.

Transnational Conflict:
  Cross-border NIAC — increasingly common. Al-Qaeda/ISIS operations
  span multiple states. Sahel operations by French Barkhane/successor,
  ECOWAS, national forces all operate in this grey area.
  Legal classification disputed — affects detention rules, targeting rules.

ARIA PRACTICAL NOTE:
  Classification matters for Arkmurus because:
  - Different weapon systems have different legal authorisation
  - Different detention rules affect how client forces must behave
  - NIAC does not have POW status — significant operational implication
  - Export licence assessment must characterise the conflict type correctly

─────────────────────────────────────────────────────────────────────────────
1.7 WAR CRIMES — DEFINITIONS RELEVANT TO EXPORT LICENCE RISK
─────────────────────────────────────────────────────────────────────────────

Rome Statute Article 8 — War Crimes (selected most relevant):
In international armed conflict:
  (a)(i) Wilful killing
  (a)(ii) Torture or inhuman treatment
  (a)(iii) Wilfully causing great suffering/serious injury
  (a)(iv) Extensive destruction/appropriation of property
  (b)(i) Intentionally directing attacks against civilians
  (b)(ii) Intentionally directing attacks against civilian objects
  (b)(iv) Intentionally launching disproportionate attack
  (b)(v) Attacking undefended places
  (b)(viii) Transfer of civilian population into occupied territory
  (b)(xxv) Starvation of civilians as method of warfare
  (b)(xxvi) Conscripting/enlisting children under 15

In non-international armed conflict:
  (c)(i) Violence to life (murder, mutilation, cruel treatment, torture)
  (c)(ii) Committing outrages upon personal dignity
  (c)(iii) Taking of hostages
  (c)(iv) Passing sentences without fair trial
  (e)(i) Intentionally directing attacks against civilians
  (e)(vii) Recruiting children under 15

EXPORT LICENCE APPLICATION:
UK ECJU Criterion 2: licence refused if clear risk equipment will be used for:
  - Internal repression
  - Torture or CIDT (cruel, inhuman, degrading treatment)
  - Serious violations of IHL
  - Acts of aggression against another country
  - Regional instability

ARIA MANDATORY PROCEDURE:
Before recommending any export to a client in conflict context:
1. ACLED: check for civilian targeting incidents in past 24 months
2. OHCHR: check for special procedures communications or reports
3. HRW/Amnesty: check for documented violations by recipient forces
4. UN Security Council: check for any conflict-related resolutions
5. UK FCO/FCDO: check travel advice and stated position on conflict
6. If adverse findings → document in DD report, flag Criterion 2 risk

─────────────────────────────────────────────────────────────────────────────
1.8 PROHIBITED WEAPONS — TREATY STATUS GLOBALLY
─────────────────────────────────────────────────────────────────────────────

CHEMICAL WEAPONS — Chemical Weapons Convention (CWC) 1993:
  State parties: 193. Non-parties: Egypt, North Korea, South Sudan.
  Prohibits: development, production, stockpiling, transfer, use.
  Scheduled chemicals: three schedules by risk.
  OPCW: implementing body with inspection mandate.
  Source: https://www.opcw.org/chemical-weapons-convention
  ARIA HARD RULE: No Arkmurus facilitation of any CW-related transfer.

BIOLOGICAL WEAPONS — Biological Weapons Convention (BWC) 1972:
  State parties: 183. Weak: no verification mechanism.
  Prohibits: development, production, stockpiling. Use already prohibited
  by 1925 Geneva Protocol.
  Source: https://www.bwc-isc.org
  ARIA HARD RULE: No Arkmurus facilitation of any BW-related transfer.

NUCLEAR WEAPONS — Treaty on Non-Proliferation (NPT) 1968:
  191 parties. Non-parties: India, Pakistan, Israel (de facto nuclear).
  North Korea withdrew 2003.
  Disarmament obligation (Article VI) unfulfilled — political flashpoint.
  IAEA safeguards: comprehensive safeguards agreements with non-NWS parties.
  TPNW (Treaty on Prohibition of Nuclear Weapons) 2021:
    70+ parties, none of the P5 or NATO states.
    Prohibits nuclear weapons categorically. Not yet customary law.
  Source: https://www.un.org/disarmament/wmd/nuclear/npt
  ARIA HARD RULE: No facilitation of any nuclear-related transfer.

ANTI-PERSONNEL LANDMINES — Ottawa Treaty (Mine Ban Treaty) 1997:
  State parties: 164. Notable non-parties: USA, Russia, China, India,
  Pakistan, South Korea, Israel, Egypt, Saudi Arabia, Iran, Pakistan.
  Prohibits: use, stockpiling, production, transfer of AP mines.
  AP mines defined as: mines designed to be exploded by presence,
  proximity, or contact of a person.
  Anti-vehicle mines (with AHD): not prohibited.
  Source: https://www.apminebanconvention.org
  ARIA ADVISORY: Parties cannot transfer to non-parties. UK is a party.
  Anti-tank mines: not covered — can facilitate if other criteria met.
  Victim-activated systems (PMN-2, etc.) = AP mines even if old Soviet stock.

CLUSTER MUNITIONS — Convention on Cluster Munitions (Oslo Convention) 2008:
  State parties: 111. Non-parties: USA, Russia, China, Israel, India,
  Brazil, Pakistan, South Korea, Saudi Arabia.
  Prohibits: use, production, stockpiling, transfer.
  Definition: munitions designed to disperse/detonate submunitions.
  DPICM (Dual-Purpose Improved Conventional Munitions) = cluster munition.
  M26 MLRS rocket = cluster munition.
  Source: https://www.clusterconvention.org
  ARIA CRITICAL NOTE: UK is a party. SITCL holder (Arkmurus) CANNOT
  broker cluster munitions. Even if client state is non-party and wants them.
  This is an absolute prohibition on brokering, not just exporting.
  US supplied DPICM to Ukraine (2023) — controversy due to US non-party status.

INCENDIARY WEAPONS — CCW Protocol III:
  Restricts use against civilian concentrations. Does not prohibit.
  White phosphorus: not specifically prohibited under CCW III if used for
  smoke/illumination not incendiary effect. Legally contested.
  Napalm/thermite: similarly contested.
  ARIA: flag incendiary weapon procurements as requiring careful legal review.

BLINDING LASERS — CCW Protocol IV (1995):
  Prohibits: lasers specifically designed to cause permanent blindness.
  Does not prohibit: laser range-finders, target designators, other laser
  systems that may cause blindness as side effect.

─────────────────────────────────────────────────────────────────────────────
1.9 AUTONOMOUS WEAPONS SYSTEMS — EMERGING LEGAL LANDSCAPE
─────────────────────────────────────────────────────────────────────────────

Lethal Autonomous Weapons Systems (LAWS) — no binding treaty yet (2026):
  International debate ongoing at UN Group of Governmental Experts (GGE)
  on LAWS under CCW since 2014.
  ICRC position (2023): new rules required; human control over force required.
  State positions:
    Favour prohibition: Austria, Belgium, Chile, New Zealand, 75+ states
    Oppose prohibition: USA, Russia, India, Israel, UK, Australia
    UK position: human oversight required but not full prohibition.

  Current legal status: existing IHL applies. LAWS must be designed to:
  - Distinguish combatants from civilians (distinction)
  - Comply with proportionality assessment
  - Apply precaution
  The legal debate is about whether machines can make these judgements.

  ARIA BD IMPLICATIONS:
  - Drone systems with autonomous target acquisition are entering market
  - TB2 (semi-autonomous), Harop (loitering/autonomous), Kargu (Turkey)
  - Clients asking about autonomous capabilities need honest legal framing
  - Export control: Wassenaar Cat 4 covers military autonomous systems
  - UK ECJU would apply Criterion 2 and human rights criteria to LAWS
"""

# =============================================================================
# SECTION 2 — ARMS CONTROL AND NON-PROLIFERATION LAW
# =============================================================================

ARMS_CONTROL_NON_PROLIFERATION = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — ARMS CONTROL AND NON-PROLIFERATION                   ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
2.1 ARMS TRADE TREATY (ATT) — Most directly relevant to Arkmurus
─────────────────────────────────────────────────────────────────────────────

Full title: Arms Trade Treaty (2013)
Entry into force: December 2014
State parties: 113 as of April 2026. Notable non-parties: USA (signed, not
  ratified), Russia, China, India, Pakistan, Saudi Arabia.
Source: https://www.thearmstradetreaty.org
Secretariat: Geneva, Switzerland

SCOPE (Article 2): Covers eight categories of conventional arms:
  1. Battle tanks
  2. Armoured combat vehicles
  3. Large-calibre artillery systems
  4. Combat aircraft (including helicopters)
  5. Warships
  6. Missiles and missile launchers
  7. Small arms
  8. Light weapons
  Also covers: ammunition/munitions (Article 3), parts and components (Article 4)

ARTICLE 6 — ABSOLUTE PROHIBITIONS (three grounds — no exception possible):
  Prohibited transfers if:
  1. The transfer would violate the state's obligations under UN Security
     Council measures adopted under Chapter VII (arms embargoes)
  2. The transfer would violate state's relevant international obligations
     (other treaties, bilateral agreements)
  3. The state has knowledge at the time of authorisation that arms would be
     used in commission of: genocide, crimes against humanity, grave breaches
     of Geneva Conventions, attacks on civilians, other war crimes

  ARIA NOTE: Article 6(3) is strict — "knowledge at time of authorisation."
  This means if Arkmurus knows of ongoing atrocities by recipient force at the
  time of facilitating a sale, facilitation could constitute complicity. This
  is why documentation of IHL risk assessment in DD reports is essential.

ARTICLE 7 — RISK ASSESSMENT (most practically relevant for ECJU):
  Before authorising export, state must assess:
  (a) Risk that conventional arms will contribute to undermining peace/security
  (b) Risk of serious violations of IHL
  (c) Risk of serious violations of international human rights law
  (d) Risk the arms/items will be used to commit acts of terrorism or
      transnational organised crime
  If "overriding risk" exists → export shall not be authorised.

  MITIGATION MEASURES: If risk is assessed but not overriding, state shall
  consider whether mitigation measures (end-use conditions, monitoring,
  joint monitoring) could reduce risk to acceptable level.

  UK IMPLEMENTATION: UK ECJU applies ATT Article 7 criteria through UK
  Consolidated Criteria assessment process. Criterion 2 maps directly to
  ATT Article 7(b) and (c). ARIA must understand this mapping.

ARTICLE 9 — TRANSIT AND TRANS-SHIPMENT:
  States to take measures to regulate transit and trans-shipment through
  their territory. UK has powers to intercept suspicious cargo.
  Relevant for Arkmurus supply chain routes through third states.

ARTICLE 10 — BROKERING:
  Each state party shall take measures to regulate brokering.
  Measures shall include: register of brokers, written authorisation
  before brokering, or declaration of country of brokering.
  This is the ATT legal basis for the UK SITCL regime.

ARTICLE 11 — DIVERSION:
  Exporters, transit states, importers shall cooperate to prevent diversion.
  Risk factors: unusual payment methods, routing, reluctance to provide
  end-use information, unusually high value.
  ARIA: these are also AML red flags — both legal frameworks align.

ATT REPORTING AND TRANSPARENCY:
  States must report annual exports of the eight categories.
  Voluntary report of imports and stockpile management.
  ARIA: ATT annual reports are Tier A sources for arms transfer data.
  Source: https://www.thearmstradetreaty.org/treaty-reporting

─────────────────────────────────────────────────────────────────────────────
2.2 WASSENAAR ARRANGEMENT — Export Control Coordination
─────────────────────────────────────────────────────────────────────────────

Full name: Wassenaar Arrangement on Export Controls for Conventional Arms
  and Dual-Use Goods and Technologies
Established: 1996 (successor to COCOM Cold War regime)
Members: 42 states including all major Western arms exporters.
  Non-members: China, India, Israel (significant).
Nature: Politically binding, not a treaty. Each state implements independently.
Source: https://www.wassenaar.org
Secretariat: Vienna, Austria

TWO CONTROL LISTS:

LIST 1 — MUNITIONS LIST (ML): Conventional military arms and equipment
  ML1: Small arms and light weapons (SALW)
    Includes: rifles, machine guns, pistols, shoulder-fired rocket launchers
    (RPG, MANPADS), all with calibre up to 20mm, and ammunition therefor.
    SITCL trigger: any brokering of ML1 goods by UK-registered entity.

  ML2: Large calibre weapons and ammunition
    Includes: artillery (>20mm), mortars, howitzers, recoilless rifles,
    military flamethrowers, tanks, MLRS above 100mm, torpedo launchers.
    All require specific export and brokering authorisation.

  ML3: Ammunition and fuzes (for ML1 and ML2)
    Standard HE, AP, HEAT projectiles. Proximity fuzes. Smart munitions.
    NOTE: standard commercial sporting ammunition (hunting) often excluded.
    Military-specification ammunition = ML3.

  ML4: Bombs, torpedoes, rockets, missiles and related equipment
    Bombs (guided and unguided), aerial torpedoes, air-launched rockets,
    cruise missiles, ballistic missiles, anti-tank missiles, MANPADS, SAMs,
    loitering munitions, underwater ordnance.
    This is the most heavily controlled Wassenaar category for Arkmurus.

  ML5: Fire control and related alerting equipment
    Fire control computers, target acquisition/designation systems,
    FCS for tanks/artillery/ships, IFF systems, military periscopes.
    Software and components for above.

  ML6: Ground vehicles and components
    Tanks, IFVs, APCs, armoured trucks (built to military specification),
    military logistics vehicles. Combat engineer vehicles.
    Components: armour plate, gun mounts, military engines.

  ML7: Chemical or biological toxic agents and riot control agents
    Tear gas (CS, CN, OC) in bulk military quantities. CW precursors.
    Riot control agents. Defoliants. CBRN protection equipment.

  ML8: Energetic materials and related substances
    Explosives, propellants, pyrotechnics, fuels for rockets.

  ML9: Vessels of war, special naval equipment and accessories
    Warships (combatant and auxiliary), submarines, combat craft.
    Components: naval gun mounts, torpedo systems, mine warfare equipment.
    Military diving/underwater equipment.

  ML10: Military aircraft, aero-engines and aircraft equipment
    Combat aircraft, trainers capable of weapons carriage, armed helicopters,
    UAVs capable of attack, military parachutes, ejection seats.
    Airborne fire control, bomb/rocket release, reconnaissance systems.

  ML11: Electronic equipment not elsewhere controlled
    Electronic warfare (EW/ECM/ECCM) systems, military IFF, radar jammers,
    military frequency-hopping radios, military encryption, psyops equipment.

  ML14: Specialised equipment for military training or simulations
    Combat simulators, flight simulators, gunnery trainers.

  ML15: Imaging or countermeasure equipment
    Military thermal imagers, image intensifiers (NVDs), laser range-finders
    designed for military use, military reconnaissance cameras.

  ML21: Software
    Software specifically designed or modified for development/production/
    operation of any ML item. Includes sensor fusion, weapon integration.

  ML22: Technology
    Technology for development/production of any ML item.

LIST 2 — DUAL-USE LIST: Commercial items with military application
  Category 1: Advanced Materials (composites, armour materials)
  Category 2: Materials Processing (machine tools for weapons parts)
  Category 3: Electronics (signal processing, certain semiconductors)
  Category 4: Computers (high-performance, military specifications)
  Category 5: Telecommunications and Information Security
  Category 6: Sensors and Lasers (thermal imaging, certain radars)
  Category 7: Navigation and Avionics (inertial navigation, GPS)
  Category 8: Marine (pressure hulls, AUV systems)
  Category 9: Aerospace and Propulsion (certain turbines, rocket propellants)

GOOD PRACTICE ELEMENTS (voluntary):
  Denial notification: members should notify others of denied applications.
  Catch-all provision: control items not listed if military end-use known.
  End-user assurance: required before transfer of sensitive items.

ARIA EXPORT CONTROL MAPPING:
  "I want to broker RPGs from a European supplier to an African MoD."
  → RPG launcher = ML1
  → RPG warheads = ML4
  → Both require SITCL (UK brokering licence)
  → European supplier requires their national export licence
  → End-user certificate required from African MoD
  → ATT Article 7 risk assessment required by UK ECJU
  → If destination is under UN arms embargo → Article 6 absolute prohibition

─────────────────────────────────────────────────────────────────────────────
2.3 CONTROL REGIMES — MULTILATERAL EXPORT CONTROL GROUPS
─────────────────────────────────────────────────────────────────────────────

AUSTRALIA GROUP (AG):
  Members: 42 states (EU + Australia, Canada, Japan, South Korea, USA, etc.)
  Purpose: harmonise export controls on chemical and biological precursors,
  equipment, and agents that could be used for CW or BW programmes.
  Not a treaty — informal arrangement with binding national implementation.
  ARIA: any client asking about dual-use chemistry equipment = trigger.
  Source: https://www.australiagroup.net

NUCLEAR SUPPLIERS GROUP (NSG):
  Members: 48 states. Purpose: nuclear-related export controls.
  Controls: nuclear materials, equipment, technology.
  Trigger list: dual-use items with nuclear application.
  ARIA: nuclear sector BD is outside Arkmurus scope. Flag to counsel.
  Source: https://www.nuclearsuppliersgroup.org

MISSILE TECHNOLOGY CONTROL REGIME (MTCR):
  Members: 35 states. Purpose: control of missiles, drones, and space
  launch vehicles capable of delivering WMDs.
  Controls: systems capable of delivering 500kg payload to 300km range.
  Note: MTCR is the reason the US restricts export of high-performance MALE
  drones (MQ-9 Reaper stays home; Bayraktar TB2 = Turkish, no MTCR restriction
  until Turkey aligned voluntarily).
  Source: https://mtcr.info

HAGUE CODE OF CONDUCT (HCOC):
  Transparency regime for ballistic missile programmes. 143 subscribed states.
  Pre-launch notifications required. Not arms control — confidence building.
  Source: https://www.hcoc.at

─────────────────────────────────────────────────────────────────────────────
2.4 ANTI-PERSONNEL MINE BAN IN OPERATIONAL CONTEXT
─────────────────────────────────────────────────────────────────────────────

Ottawa Treaty deep dive — most misunderstood prohibition for BD:

What is prohibited: development, production, stockpiling, TRANSFER, and use
  of anti-personnel mines (APMs).

What is NOT prohibited:
  - Anti-vehicle mines (without victim-activated fuzing)
  - Training in mine detection/clearance (small stockpiles allowed)
  - Anti-tank mines with essential fuzing
  - Claymore mines in command-detonated mode (AP wire-initiated = NOT APM)
  - Claymore in victim-activated mode = PROHIBITED

Client forces (if party states) cannot: use, order, direct use of APMs.
  Command responsibility extends to ordering prohibited weapon use.
  UK officers embedded with client forces must not facilitate APM use.

STOCKPILE DESTRUCTION: parties must destroy APM stockpiles in 4 years.
  Many African states have destroyed stockpiles. Angola completed 2012.
  Mozambique completed 2015. Guinea-Bissau ongoing.

CLEARANCE: parties must clear mined areas within 10 years (extensions allowed).
  Mine clearance = consistent BD opportunity in CPLP Africa.
  Angola: 1,000+ mined communities. HALO Trust, NPA active.
  Mozambique: 3,000+ mined areas. Clearance market significant.

ARIA APPLICATION: When Angola or Mozambique FAA requests anti-armour mine
  procurement, verify: (a) anti-vehicle not anti-personnel, (b) no
  victim-activated fuzing, (c) importer is treaty party with stockpile
  management obligations. If ambiguity → legal advice required.
"""

# =============================================================================
# SECTION 3 — INTERNATIONAL SANCTIONS LAW
# =============================================================================

SANCTIONS_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — INTERNATIONAL SANCTIONS                               ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
3.1 UN SANCTIONS ARCHITECTURE — Chapter VII UN Charter
─────────────────────────────────────────────────────────────────────────────

Legal basis: UN Charter Articles 39-42
  Article 39: Security Council determines threat to peace, breach of peace,
    or act of aggression.
  Article 41: Non-military measures (sanctions) — binding on all 193 UN members.
  Article 42: Military measures — if Article 41 measures inadequate.

Binding effect: UN Security Council resolutions under Chapter VII are legally
  binding on all UN member states. No state can claim exemption. This makes
  UN sanctions the hardest international law constraint in the arms trade.

Sanctions Committee system: Each regime has a dedicated committee of the
  Security Council. Committee designates individuals/entities. Maintains
  consolidated list. Issues exemptions. Monitors compliance.

ACTIVE UN ARMS EMBARGOES (verify current list — changes by UNSC resolution):
Source: https://www.un.org/sc/suborg/en/sanctions/information

  Central African Republic (CAR):
    Resolution 2127 (2013) — partial embargo, exceptions for CAR government
    forces and MINUSCA (UN mission). Renewed annually.
    Wagner/Africa Corps presence: complicates enforcement significantly.

  Democratic Republic of Congo (DRC):
    Resolution 1533 (2004) — applies to non-governmental entities and
    individuals. DRC government forces are exempt.
    MONUSCO supplies: exempt with notification to committee.

  Somalia:
    Resolution 733 (1992) — comprehensive originally. Now partial.
    Federal Government forces exempt with approval process.
    Arms for Somali National Army: complicated approval mechanism.
    Al-Shabaab: full embargo applies.

  South Sudan:
    Resolution 2428 (2018) — arms embargo on state and non-state actors.
    Humanitarian exemptions available.
    Multiple violations documented by Panel of Experts.

  Sudan/Darfur:
    Resolution 1556 (2004), 1591 (2005) — applies to Darfur specifically.
    Not a general Sudan embargo. Khartoum government forces in Darfur = embargo.
    RSF (Rapid Support Forces) and SAF: embargo violations documented.

  Yemen:
    Resolution 2216 (2015) — applies to Houthi forces and forces loyal to
    former President Saleh. NOT a general Yemen embargo.
    Saudi-led coalition: NOT subject to embargo as constituted.
    Iran arms supply to Houthis: documented embargo violation.

  Libya:
    Resolution 1970 (2011) — general arms embargo with exceptions for
    non-lethal government forces. Multiple Panel of Experts findings of
    violations by UAE, Turkey, Russia, Jordan, Sudan.
    Practical enforcement: minimal. Major proliferation risk.

  North Korea (DPRK):
    Resolution 1718 (2006) + multiple successive resolutions.
    Comprehensive arms embargo: all weapons, all parties.
    No exceptions for government transactions.
    Also: nuclear-related materials, luxury goods, energy exports.

  Iran:
    Resolution 2231 (2015) — JCPOA-related. Conventional arms embargo
    lifted October 2020 (per JCPOA schedule). Ballistic missile restrictions
    remain. ARIA: Iran arms embargo landscape is complex — counsel required.

  Mali:
    Resolution 2374 (2017) — targeted measures (asset freeze, travel ban)
    on individuals. No comprehensive arms embargo. Arms to Malian state
    allowed but ECOWAS suspensions create practical obstacles.

─────────────────────────────────────────────────────────────────────────────
3.2 UK SANCTIONS FRAMEWORK (Post-Brexit)
─────────────────────────────────────────────────────────────────────────────

Primary legislation: Sanctions and Anti-Money Laundering Act (SAMLA) 2018
  Enables UK to implement sanctions independently of EU post-Brexit.
  Prior to Brexit, UK implemented EU sanctions via EU regulations.
  Post-Brexit: UK has own consolidated sanctions list.

Regulators:
  OFSI (Office of Financial Sanctions Implementation) — HM Treasury
    Financial sanctions: asset freeze, transaction prohibition
    Criminal liability: up to 7 years imprisonment
    Civil penalty: unlimited (largest: £46.6m against Standard Chartered 2023)

  OTSI (Office of Trade Sanctions Implementation) — Department for Business
    Trade sanctions: goods and services prohibitions
    Established 2023 — newer, growing enforcement capability

  ECJU (Export Control Joint Unit) — Department for Business
    Export licensing: administers export controls including arms
    Separate from sanctions but closely coordinated

UK Sanctions Regimes relevant to defence (as of April 2026):
  Russia: Most comprehensive post-February 2022. Covers:
    - Arms embargo (comprehensive — no exceptions for legitimate trade)
    - Financial sector restrictions (major Russian banks)
    - Oil, gas, coal import restrictions
    - Individual designations: 1,000+ individuals and entities
    - Shipping restrictions
    Note: Russian-origin military equipment now practically impossible
    to legitimately broker through UK framework.

  Belarus: Arms embargo + individual designations post-2020 election.
  Myanmar: Arms embargo post-2021 coup.
  Venezuela: Arms embargo.
  Iran: Arms embargo + financial restrictions.
  Syria: Arms embargo + individual designations.
  North Korea: Comprehensive sanctions implementing UN measures.
  Zimbabwe: Individual designations (remnant post-Mugabe).

Specific licence process:
  OFSI issues specific licences for designated persons (humanitarian exceptions,
  certain legal expenses, basic needs). Arkmurus: if any counterparty is
  designated, STOP and seek legal advice immediately.
  Do not proceed without specific OFSI licence. Tipping off prohibited.

─────────────────────────────────────────────────────────────────────────────
3.3 US SANCTIONS — OFAC Architecture
─────────────────────────────────────────────────────────────────────────────

Primary authority: International Emergency Economic Powers Act (IEEPA) 1977
  President declares national emergency → activates IEEPA powers.
  Most sanctions programmes operate under IEEPA emergency declarations.

OFAC (Office of Foreign Assets Control) — US Treasury:
  Administers 30+ active sanctions programmes.
  Criminal: up to 20 years. Civil: up to $1.3m per violation or 2x value.
  Source: https://ofac.treasury.gov

SDN LIST (Specially Designated Nationals and Blocked Persons):
  The most important sanctions list globally. 12,000+ names.
  US persons prohibited from any dealings. Assets blocked.
  Global reach: any USD transaction clears through US correspondent banks →
  SDN exposure. De facto global enforcement through financial system.
  Search: https://ofac.treasury.gov/sanctions-list-search

CAATSA (Countering America's Adversaries Through Sanctions Act 2017):
  Section 231: mandatory secondary sanctions on persons who knowingly engage
  in a significant transaction with Russian defence or intelligence sector.
  "Significant transaction" = deliberately vague, OFAC determines case by case.
  Applied to: Turkey (for S-400 purchase — threat and partial application),
  India (waiver granted for S-400 — but signalled risk), China (partial for
  military purchases), Indonesia (threat for Su-35 purchase — deal cancelled).
  ARIA CRITICAL: When advising client on Russian-origin major platform
  procurement (S-300/S-400, Su-30/35/57, T-90, Kilo submarine), flag CAATSA
  Section 231 risk immediately. The client faces secondary US sanctions.
  This is a material BD argument for non-Russian alternatives.

ITAR (International Traffic in Arms Regulations):
  Implements Export Administration Act. Controls USML items.
  Applies to: US persons anywhere, foreign persons in US, transfers of
  US-origin defence articles regardless of who holds them.
  Brokering: 22 CFR Part 129 — must register with DDTC, may need licence.
  Registration: $2,750/year base. Licence: case-by-case per activity.
  Source: https://www.pmddtc.state.gov/ddtc_public
  ARIA: if Arkmurus facilitates transfer of US-origin ML items, separate ITAR
  brokering registration may be required in addition to UK SITCL.

EAR (Export Administration Regulations):
  Controls dual-use goods on Commerce Control List (CCL).
  Administered by: Bureau of Industry and Security (BIS), Commerce Dept.
  Entity List: foreign entities subject to licensing requirement.
  Denied Persons List: US persons banned from exports.
  Unverified List: entities where BIS cannot verify end-use.
  Source: https://www.bis.doc.gov
  ARIA: for dual-use goods (encryption, certain electronics, SATCOM),
  EAR licence may be required even when ITAR does not apply.

─────────────────────────────────────────────────────────────────────────────
3.4 EU SANCTIONS
─────────────────────────────────────────────────────────────────────────────

Legal basis: Treaty on European Union — Common Foreign and Security Policy (CFSP)
  Council Decisions adopted unanimously → regulations directly applicable.
Implementation: EU sanctions become immediately binding in member states upon
  publication in Official Journal. No domestic implementation required.
Consolidated list: https://sanctions.ec.europa.eu (free, searchable)

Key current regimes (April 2026 — always verify):
  Russia: 14+ sanction packages post-February 2022. Comprehensive.
    Arms embargo (broadened to include non-lethal military goods).
    Financial: sanctions on major banks, SWIFT disconnection.
    Energy: oil price cap mechanism (coordinated with G7).
    Maritime: shadow fleet vessel designations.
  Belarus: Arms embargo + individual designations.
  Myanmar, Syria, Iran, Venezuela, Libya, Sudan, Zimbabwe: various.
  Iran: Also JCPOA-related aircraft licensing complications.

─────────────────────────────────────────────────────────────────────────────
3.5 EXTRATERRITORIAL SANCTIONS — KEY RISK FOR ARKMURUS
─────────────────────────────────────────────────────────────────────────────

US secondary sanctions: The US imposes sanctions on non-US persons for conduct
  entirely outside the US, involving sanctioned parties or jurisdictions.
  Mechanism: denial of access to US financial system, US market, or US
  government contracts. Not technically "jurisdiction" — economic coercion.

  Iran: CAATSA Iran provisions. Non-US firms dealing with Iranian entities
  may face US sanctions even if no US person/territory involved.
  Russia: CAATSA Russia (Section 231, 232, 233). Non-US defence contractors
  dealing with Russian intelligence/defence = exposure.
  Cuba/Venezuela: HELMS-BURTON Title III allows US persons to sue non-US
  companies dealing in expropriated US property.

EU Blocking Statute (Council Regulation 2018/1100):
  Prohibits EU operators from complying with certain US extraterritorial
  sanctions (HELMS-BURTON, CAATSA extraterritorial aspects).
  Also: requires EU operators to notify Commission, nullifies effect of US
  judgments based on these measures.
  PRACTICAL REALITY: most EU firms comply with US sanctions rather than
  risk US market access, despite EU blocking statute.

ARIA ADVISORY:
  Whenever advising on: Iranian defence market, Russian-origin equipment,
  Venezuelan procurement, or Cuban security sector → US secondary sanctions
  risk must be prominently flagged regardless of client nationality.
  ARIA does not advise on transactions that would trigger secondary sanctions.
  Refer to: Clifford Chance, Gibson Dunn, Sullivan & Cromwell sanctions teams.
"""

# =============================================================================
# SECTION 4 — INTERNATIONAL CRIMINAL LAW AND ACCOUNTABILITY
# =============================================================================

INTERNATIONAL_CRIMINAL_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — INTERNATIONAL CRIMINAL LAW AND ACCOUNTABILITY        ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
4.1 ROME STATUTE AND INTERNATIONAL CRIMINAL COURT
─────────────────────────────────────────────────────────────────────────────

Treaty: Rome Statute of the International Criminal Court (1998)
Entry into force: July 2002
State parties: 124 as of April 2026
Non-parties: USA (signed, unsigned 2002), Russia (signed, unsigned 2016),
  China, India, Israel, Saudi Arabia, Turkey, Indonesia, Egypt, Pakistan.
Source: https://www.icc-cpi.int

JURISDICTION (Article 5): Four core crimes
  1. Genocide (Article 6): acts committed with intent to destroy, in whole
     or in part, a national, ethnical, racial, or religious group.
  2. Crimes against humanity (Article 7): widespread or systematic attack
     against civilian population. Includes: murder, extermination, enslavement,
     deportation, torture, rape/sexual violence, persecution, enforced
     disappearance, apartheid, other inhumane acts.
  3. War crimes (Article 8): serious violations of the laws of armed conflict.
     See Section 1.7 for specific acts. Both IAC and NIAC covered.
  4. Crime of aggression (Article 8bis, activated 2018): use of armed force
     by one state against the sovereignty/territorial integrity/political
     independence of another state in manifest violation of the UN Charter.
     Jurisdiction: nationals of states that have ratified aggression amendment.

COMPLEMENTARITY PRINCIPLE (Article 17):
  ICC jurisdiction is complementary — not primary. ICC acts only when national
  courts are UNABLE OR UNWILLING to genuinely investigate/prosecute.
  Implication: if a state has a functioning justice system that is genuinely
  investigating, ICC defers. Most African ICC cases reflect absence of domestic
  prosecution of senior officials for mass atrocity crimes.

ACTIVE SITUATIONS/INVESTIGATIONS (April 2026 — verify current):
  Sub-Saharan Africa:
    DRC: multiple situations, ongoing investigations.
    Uganda: Lord's Resistance Army.
    CAR (Central African Republic): two situations.
    Sudan (Darfur): Al-Bashir issued warrant (died 2019); others outstanding.
    Kenya: situation closed without charges (2015).
    Côte d'Ivoire: Gbagbo acquitted (2019); others.
    Mali: situation under investigation.
    Burundi: situation under investigation.
    Nigeria: preliminary examination.
    Guinea: preliminary examination.

  Middle East/Asia:
    Palestine: investigation opened 2021.
    Bangladesh/Myanmar (Rohingya): investigation.
    Afghanistan: investigation (including US forces — suspended under pressure).
    Philippines: preliminary examination.
    Venezuela: investigation.
    Georgia: situation.
    Ukraine: investigation (Putin, Lvova-Belova warrants issued 2023).

ARIA DD APPLICATION:
  When investigating a military entity or senior official:
  1. Check ICC website for outstanding warrants: https://www.icc-cpi.int/cases
  2. Check UN Security Council resolutions for named individuals
  3. Any ICC indictee in command chain of client force = CRITICAL red flag
  4. This affects: export licence (UK Criterion 2), reputational risk,
     potential accessory liability for Arkmurus

─────────────────────────────────────────────────────────────────────────────
4.2 COMMAND RESPONSIBILITY — DIRECT RELEVANCE TO CLIENT FORCES
─────────────────────────────────────────────────────────────────────────────

Legal basis: Rome Statute Article 28 + customary international law
  Commanders are criminally responsible for crimes committed by subordinates
  if they:
  (a) Knew or consciously disregarded information that subordinates were
      committing or about to commit crimes, AND
  (b) Failed to take all necessary and reasonable measures within their power
      to prevent or repress the commission, or to submit the matter to
      competent authorities for investigation and prosecution.

Two standards:
  Military commanders: actual knowledge OR "should have known" (negligence)
  Civilian superiors: actual knowledge OR "consciously disregarded" information

ARIA PRACTICAL IMPLICATION:
  When OHCHR, HRW, Amnesty, or ACLED document that a specific unit of a client
  force committed atrocities, and that unit's commander is known, the commander
  potentially faces command responsibility exposure. If that commander is also
  the end-user or procurement decision-maker → heightened risk.
  Document all findings. Flag in DD report. Escalate to senior Arkmurus and
  legal counsel before proceeding.

─────────────────────────────────────────────────────────────────────────────
4.3 UNIVERSAL JURISDICTION
─────────────────────────────────────────────────────────────────────────────

Definition: Some states claim jurisdiction over certain international crimes
  regardless of where committed or nationality of perpetrator/victim.

Basis: Customary international law for: piracy, torture, genocide,
  crimes against humanity, war crimes, slave trade.

States with active universal jurisdiction legislation:
  Belgium: Broad UJ legislation (Pinochet-era, modified after pressure).
  Germany: Völkerstrafgesetzbuch (VStGB) 2002 — comprehensive UJ for
    international crimes. Used to prosecute Syrian war crimes.
    German case against Anwar Raslan (Syrian intelligence) — convicted 2022.
  Spain: Previously broad, now restricted to Spanish victims or residents.
  Netherlands: Cases against Afghan warlords.
  UK: International Criminal Court Act 2001 — limited UJ for Rome Statute
    crimes. War Crimes Act 1991. Torture Act 1994.
  Switzerland: Prosecuted Rwandan genocide cases.
  Sweden, France, Belgium: various prosecutions.

ARIA ADVISORY:
  When introducing European counterparts to clients who may have war crimes
  exposure, be aware that European jurisdictions could theoretically prosecute.
  This is relevant for introductions of clients to the UK/EU market.
  It is not Arkmurus's role to advise on this. Flag to senior and legal counsel.

─────────────────────────────────────────────────────────────────────────────
4.4 ANTI-CORRUPTION AND BRIBERY LAW — CRITICAL FOR DEFENCE BD
─────────────────────────────────────────────────────────────────────────────

UK BRIBERY ACT 2010 (most stringent globally for UK businesses):
Source: https://www.legislation.gov.uk/ukpga/2010/23/contents
  Section 1: Offence of bribing another person.
    Offering/giving financial or other advantage to induce improper
    performance of a function. Applies everywhere in the world.

  Section 2: Being bribed.
    Requesting/receiving advantage in return for improper performance.

  Section 6: Bribery of foreign public officials (FPOs).
    Offering/giving advantage to FPO to obtain/retain business.
    No "improper performance" element needed — simpler than S.1.
    Any official of foreign government or public international organisation.
    Procurement officials, military officers, ministers = FPOs.
    This is the most relevant offence for defence BD internationally.

  SECTION 7: FAILURE TO PREVENT BRIBERY — CORPORATE LIABILITY.
    An organisation is guilty if an "associated person" bribes to obtain/
    retain business/advantage for the organisation.
    Associated person: employee, agent, subsidiary, or other person providing
    services on the organisation's behalf (including local agents, fixers,
    representatives, consultants).
    STRICT LIABILITY: Arkmurus does not need to know about or direct the bribe.
    Defence: had "adequate procedures" to prevent bribery.
    PENALTY: Unlimited fine. No custodial sentence for organisations.
    Senior officers: prosecuted under S.1 or S.6 individually if consent/connivance.

  ADEQUATE PROCEDURES (Six Principles — Ministry of Justice guidance):
    1. Proportionate procedures
    2. Top-level commitment
    3. Risk assessment
    4. Due diligence (on associated persons)
    5. Communication and training
    6. Monitoring and review

  FACILITATION PAYMENTS: NOT permitted under UK Bribery Act.
    Unlike FCPA, even small "grease" payments for routine government services
    are prohibited. This is stricter than US law.

  HOSPITALITY: Proportionate, reasonable, bona fide hospitality is NOT an
    offence. Excessive hospitality (all-expenses travel to sporting events,
    expensive gifts) is problematic. Context and value matter.

  ARIA ADVISORY:
    Defence sector is the highest per-transaction bribery risk sector globally
    (TRACE International Bribery Risk Matrix, published annually).
    Arkmurus must:
    1. Conduct due diligence on all agents, intermediaries, consultants
    2. Commission rates must be commercially justifiable (>15% without
       documented justification = red flag)
    3. Document all hospitality decisions
    4. No cash payments for any purpose
    5. SAR filing if suspicion of bribery by counterparty

US FOREIGN CORRUPT PRACTICES ACT (FCPA) 1977:
Source: https://www.justice.gov/criminal/criminal-fraud/foreign-corrupt-practices-act
  Anti-bribery provision: prohibits US persons (including US-listed companies
    and their agents worldwide) from bribing foreign officials.
  Books and records: requires accurate books and adequate internal controls.
  Jurisdiction: US persons worldwide; companies with US market presence;
    non-US persons who use US mails or instrumentalities.
  Criminal: up to $2m per organisation, $250k per individual.
  Civil: up to $23k per violation (FCA). DOJ and SEC both enforce.
  Deferred Prosecution Agreements (DPAs): major enforcement tool.
    BAE Systems: $400m FCPA settlement (2010).
    Airbus: $4bn settlement (DOJ/DPA, 2020) — included UK SFO and France.
    Rolls-Royce: £671m settlement with SFO, DOJ, Brazil (2017).

OECD ANTI-BRIBERY CONVENTION (1997):
Source: https://www.oecd.org/corruption/anti-bribery/convention
  38 parties (OECD members + others). Each must criminalise foreign bribery.
  Monitoring: Working Group on Bribery peer reviews (Phase 1-4).
  Phase 4 reviews: assessing implementation. UK, France, Germany reviewed.

UNCAC (UN Convention Against Corruption, 2003):
Source: https://www.unodc.org/unodc/en/corruption/uncac.html
  190 parties. Most comprehensive anti-corruption framework.
  Covers: domestic bribery, foreign bribery, embezzlement of public funds,
  trading in influence, money laundering, obstruction.
  Asset recovery provisions: groundbreaking for developing states.
  Review mechanism: UNCAC Implementation Review Mechanism (IRM).
  ARIA: UNCAC CPI compliance is measured by Transparency International CPI.
  Low CPI score = higher S.6/FCPA risk environment for any BD.

SFO (Serious Fraud Office) — UK:
  Investigates and prosecutes serious/complex fraud and corruption.
  Deferred Prosecution Agreements: used for corporate settlements.
  Section 2 notices: compel disclosure of information.
  ARIA: If Arkmurus suspects involvement in corrupt scheme by counterparty,
  seek legal advice and consider voluntary disclosure to SFO before enforcement.
"""

# =============================================================================
# SECTION 5 — LAW OF THE SEA AND MARITIME SECURITY LAW
# =============================================================================

LAW_OF_THE_SEA = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — LAW OF THE SEA AND MARITIME SECURITY                 ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
5.1 UNCLOS — THE CONSTITUTION OF THE OCEAN
─────────────────────────────────────────────────────────────────────────────

Full title: United Nations Convention on the Law of the Sea (1982)
Entry into force: 1994
State parties: 168. Non-parties: USA (not ratified but accepts most as
  customary law), Turkey, Israel, Venezuela.
Source: https://www.un.org/depts/los/convention_agreements

MARITIME ZONES (critical for client EEZ patrol procurement):

Internal waters: Waters landward of baseline. Full sovereignty.
  No right of innocent passage. Ships subject to full coastal state law.
  Ports, harbours, rivers, historical bays.

Territorial sea: 0-12nm from baseline. Full sovereignty.
  Coastal state may legislate, enforce, adjudicate.
  Foreign warships: right of innocent passage (UNCLOS Art 17-32).
    Innocent passage = continuous, expeditious, not prejudicial to peace/
    good order/security. Submarines must navigate surfaced.
    Coastal state cannot suspend innocent passage but can designate sea lanes.
    Warship rules: notification/authorisation requirements debated by some
    states (Russia, China claim prior authorisation required — US rejects).

Contiguous zone: 12-24nm. Coastal state may enforce customs, fiscal,
  immigration, and sanitary laws. Cannot exercise full sovereignty.
  Important for: anti-smuggling, illegal migration enforcement.

Exclusive Economic Zone (EEZ): 12-200nm from baseline.
  Sovereign rights for: exploration/exploitation of natural resources
    (fish, oil, gas, seabed minerals).
  Jurisdiction over: artificial islands, marine scientific research,
    marine environmental protection.
  Freedom of navigation and overflight REMAINS — for all states.
  Other states: freedom of navigation, overflight, submarine cables,
    pipelines, and other lawful uses.
  ARIA BD RELEVANCE:
    EEZ patrol = primary BD opportunity for island states and states with
    offshore hydrocarbon assets. Angola (oil), Mozambique (gas), Cape Verde
    (fishing/EEZ surveillance), São Tomé (oil exploration).
    OPV procurement is driven by EEZ enforcement need.
    EEZ = 200nm radius around each island in Cape Verde archipelago.
    Combined Cape Verde EEZ: ~800,000 km² — enormous patrol requirement.

Continental Shelf: Natural prolongation of land territory.
  Sovereign rights over seabed resources even beyond 200nm EEZ.
  Extended continental shelf claims: up to 350nm with CLCS approval.
  Note: no jurisdiction over the water column above (only seabed/subsoil).

High Seas: Beyond 200nm (or beyond EEZ of any state).
  Freedom of navigation, overflight, fishing, scientific research.
  Pipelines and cables. Construction of artificial islands.
  Flag state jurisdiction over vessels on high seas.
  Exceptions: piracy (universal jurisdiction), slave trade, unauthorised
  broadcasting, stateless vessels (Art 110 right of visit).

ARCHIPELAGIC STATES: Cape Verde, São Tomé, Indonesia, Philippines.
  Special regime: baselines drawn connecting outermost points of outermost
  islands. Archipelagic waters within baselines = sovereign rights.
  Archipelagic sea lanes passage: through right of passage for foreign ships.
  ARIA: Cape Verde archipelagic state status = even larger maritime domain
  requiring surveillance capacity.

─────────────────────────────────────────────────────────────────────────────
5.2 MARITIME SECURITY LAW — ENFORCEMENT FRAMEWORKS
─────────────────────────────────────────────────────────────────────────────

PIRACY — UNCLOS Articles 101-107:
  Definition: illegal acts of violence/detention committed for private ends
    by crew/passengers of private ship against another ship or persons/
    property on high seas or outside jurisdiction of any state.
  Note: piracy requires TWO SHIPS. Single ship = not piracy.
    Armed robbery at sea (in territorial waters) = under national law.
  Universal jurisdiction: all states may seize pirate vessels and try pirates.
  International Maritime Bureau (IMB): tracks global piracy incidents.
    Source: https://www.icc-ccs.org/icc/imb
  
  KEY PIRACY ZONES (current risk areas):
    Gulf of Guinea: Nigeria, Benin, Gulf of Guinea waters.
      2023 trend: declining following multinational operation.
      Yaoundé Code of Conduct: regional response framework.
      IFC (Interregional Coordination Centre): Gulf of Guinea coordination.
    Strait of Malacca: Historical high risk, now lower.
    Red Sea/Gulf of Aden: Houthi attacks (2024-) = conflict, not piracy.
    Indian Ocean: Somali piracy largely suppressed. Risk remains.
    Mozambique Channel: Cabo Delgado insurgency affects maritime security.

SUA CONVENTION (Convention for the Suppression of Unlawful Acts 1988):
  Broader than UNCLOS piracy — applies to all unlawful acts against safety
    of maritime navigation, including in territorial waters.
  Criminalises: seizing ships by force, violence on board, placing devices
    to destroy/damage ships, destroying maritime navigation facilities.
  2005 Protocol: extends to transport of WMDs, pirates, acts of terrorism.
  Jurisdiction: flag state, state where vessel found, state of perpetrator.
  Source: https://www.imo.org/en/About/Conventions/Pages/SUA-Protocols.aspx

PROLIFERATION SECURITY INITIATIVE (PSI) — US-led:
  Not a treaty — political declaration. 107+ participating states.
  Commitment to interdict shipments of WMDs, related materials.
  Boarding authority based on bilateral ship-boarding agreements.
  Cape Verde is a PSI endorsing state — relevant to BD advisory there.

REGIONAL MARITIME FRAMEWORKS:

  Djibouti Code of Conduct (2009, Jeddah Amendment 2017):
    Coverage: Western Indian Ocean, Gulf of Aden, Red Sea.
    Parties: 20+ African and Gulf states.
    Includes: Mozambique, Tanzania, Kenya, South Africa, Somalia.
    Focus: counter-piracy, armed robbery, trafficking, IUU fishing.
    Also (2017 revision): terrorism, WMD smuggling, humanitarian responses.
    Source: https://www.imo.org/en/ourwork/security/pages/djibouti-code-of-conduct.aspx
    ARIA: any maritime security BD in this region operates within this framework.

  Yaoundé Code of Conduct (2013):
    Coverage: West and Central Africa — Gulf of Guinea.
    Parties: 25 African states including Angola, São Tomé.
    Zones A-F covering sub-regional maritime areas.
    GoGIN (Gulf of Guinea Inter-Regional Network): coordination mechanism.
    MASE: EU-funded maritime security programme for Eastern Africa.
    ARIA: Angola and São Tomé are parties. Maritime BD in these markets
    must demonstrate Yaoundé Code compatibility.

  SHADE (Shared Awareness and Deconfliction): Counter-piracy coordination.
    Active in Indian Ocean. NATO, EU Operation Atalanta, CMF, national navies.

─────────────────────────────────────────────────────────────────────────────
5.3 IUU FISHING — MAJOR MARITIME SECURITY DRIVER IN CPLP AFRICA
─────────────────────────────────────────────────────────────────────────────

Scale: Estimated $23bn/year in illegal fish value globally. Sub-Saharan Africa
  loses 37% of total fish catch to IUU. Massive economic and food security issue.
  West Africa: heavily impacted by distant-water fishing fleets (especially
  Chinese and Russian trawlers operating without proper licences).

Legal framework: UNCLOS Article 73 (coastal state enforcement in EEZ).
  Port State Measures Agreement (PSMA) 2016: first binding international
  agreement specifically targeting IUU fishing. 70+ parties.
  Regional fisheries management organisations (RFMOs): tuna, swordfish, etc.

CPLP EEZ enforcement challenge:
  Angola: 1,650km coastline. Limited patrol capacity. Oil-focused Navy.
  Mozambique: 2,470km coastline. Cabo Delgado insurgency diverts resources.
  Cape Verde: ~800,000km² EEZ for 10 inhabited islands. Major challenge.
  Guinea-Bissau: 350km coastline. Very limited naval capacity.
  São Tomé: 160km coastline. Minimal naval assets.

ARIA BD OPPORTUNITY:
  OPV and fast patrol craft procurement is directly driven by IUU enforcement need.
  Each CPLP African coastal state has a documented, persistent requirement.
  Damen StanPatrol series (Netherlands) — dominant in Africa OPV market.
  No ITAR constraint on European OPV suppliers.
  This is a consistent, fundable, legitimate procurement need = Arkmurus opportunity.
  Funding: EU Global Gateway, World Bank, African Development Bank, bilateral grants
  often available for maritime security ODA.
"""

# =============================================================================
# SECTION 6 — HUMAN RIGHTS LAW, EXPORT CONTROLS, AND CORPORATE RESPONSIBILITY
# =============================================================================

HUMAN_RIGHTS_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — HUMAN RIGHTS, EXPORT CONTROLS, CORPORATE DUTY       ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
6.1 CORE INTERNATIONAL HUMAN RIGHTS INSTRUMENTS
─────────────────────────────────────────────────────────────────────────────

UDHR (Universal Declaration of Human Rights, 1948):
  Not a treaty — UNGA resolution. Widely accepted as customary international
  law. Provides the normative framework for subsequent treaty law.

ICCPR (International Covenant on Civil and Political Rights, 1966):
  172 parties. Rights: life (Article 6), freedom from torture (Article 7),
  liberty (Article 9), fair trial (Article 14), expression (Article 19),
  assembly/association (Articles 21-22), political participation (Article 25).
  Human Rights Committee: treaty body, reviews state reports, individual
  complaints (Optional Protocol parties), inter-state complaints.
  Derogations: permitted in "time of public emergency" — but not of:
  right to life, freedom from torture, freedom from slavery, prohibition on
  imprisonment for contract, non-retroactive criminal law.

ICESCR (International Covenant on Economic, Social and Cultural Rights, 1966):
  170 parties. Rights: work, health, education, social security, food.
  Progressive realisation: states commit to maximum available resources.
  CESCR: treaty monitoring body.
  Less directly relevant to Arkmurus than ICCPR but context for PMESII.

ECHR (European Convention on Human Rights, 1950):
  46 Council of Europe member states.
  ECtHR (European Court of Human Rights) Strasbourg:
    Individual petition mechanism. Binding judgments.
    50,000+ applications per year. Backlog problem.
  UK Human Rights Act 1998: incorporates ECHR into domestic law.
  Extraterritorial application: Al-Skeini v UK [2011] — UK armed forces
    in Iraq subject to ECHR jurisdiction for deaths of Iraqi civilians.
    Implication: UK forces training/advising client forces abroad could
    engage ECHR if they exercise authority and control.

REGIONAL HUMAN RIGHTS SYSTEMS:

  African Charter on Human and Peoples' Rights (ACHPR) 1986:
    55 state parties. African Commission on Human and Peoples' Rights (Banjul).
    African Court on Human and Peoples' Rights (Arusha): binding decisions.
    Collective rights: right of peoples to self-determination, natural resources.
    Includes CPLP African states. Relevant for market assessments.

  American Convention on Human Rights (ACHR) 1969:
    25 parties. Inter-American Commission and Court (IACHR) (San José).
    Brazil, Colombia, Argentina, Chile, Peru: parties.
    USA: not a party to ACHR.

  Arab Charter on Human Rights (2004):
    21 Arab states. Arab Human Rights Committee.
    Gulf states: parties. Relevant for Gulf market context.

─────────────────────────────────────────────────────────────────────────────
6.2 HUMAN RIGHTS AND UK EXPORT CONTROL — CRITERION 2
─────────────────────────────────────────────────────────────────────────────

UK Consolidated Criteria — Criterion 2 (most important for Arkmurus):
Source: https://www.gov.uk/guidance/uk-strategic-export-controls-the-consolidated-criteria

  Export licences must be refused if there is a "clear risk" that the goods
  might be used for:
    - Internal repression
    - Serious human rights violations

  Internal repression includes: torture, arbitrary detention, extrajudicial
    execution, disappearances, use of security forces for political repression.

  Factors ECJU considers:
    (a) Nature of the goods and risk of misuse
    (b) Internal repression record of recipient government
    (c) Attitude to relevant international human rights standards
    (d) Whether goods would contribute to internal repression
    (e) Risk under present circumstances

  PRECEDENT — Court of Appeal 2019 (CAAT v Secretary of State):
    UK court ruled ECJU's licensing process for Saudi Arabia (Yemen context)
    was irrational — failed to assess IHL violations adequately.
    Government suspended licences, conducted new assessment, resumed partially.
    Lesson: ECJU now applies much more rigorous IHL/HR risk assessment
    to conflict-affected destinations. Arkmurus must reflect this in advice.

  PRECEDENT — Nigeria (2021):
    UK parliament called for suspension of licences to Nigeria following
    Lekki Tollgate killings (SARS crackdown, October 2020).
    Licences were not suspended but enhanced scrutiny applied.
    Demonstrates: documented police/security force abuses affect UK licensing.

  ARIA PRACTICAL PROTOCOL FOR CRITERION 2 ASSESSMENT:
    Step 1: Check Freedom House Civil Liberties score (1-7, higher = less free)
    Step 2: Check V-Dem (Varieties of Democracy) data
    Step 3: Check OHCHR country page for special procedures letters/reports
    Step 4: Check HRW annual country chapter for security force abuses
    Step 5: Check Amnesty country reports
    Step 6: Check ACLED for civilian targeting incidents by government forces
    Step 7: If Angola/Mozambique/Guinea-Bissau → check ISS Africa conflict tracker
    Step 8: If recent conflict → check UN Panel of Experts reports
    Step 9: Document findings and overall Criterion 2 risk rating
    Step 10: Include in DD report with "ECJU Criterion 2 assessment" section

─────────────────────────────────────────────────────────────────────────────
6.3 UN GUIDING PRINCIPLES ON BUSINESS AND HUMAN RIGHTS (UNGPs)
─────────────────────────────────────────────────────────────────────────────

Adopted by UN Human Rights Council, June 2011.
"Protect, Respect and Remedy" framework.
Not binding but increasingly referenced in legislation and procurement.

Three pillars:
  1. STATE DUTY TO PROTECT: states must protect against human rights abuses
     by third parties including businesses operating in their territory.
     Regulatory measures for business conduct required.

  2. CORPORATE RESPONSIBILITY TO RESPECT: businesses must not infringe human
     rights. Must address adverse impacts they cause or contribute to.
     Human rights due diligence (HRDD) process required.
     Includes: assessment, integration, tracking, communication.

  3. REMEDY: both judicial and non-judicial remedies for victims.

EU CSDDD (Corporate Sustainability Due Diligence Directive) 2024:
  Makes elements of UNGPs mandatory for large EU companies (phased 2025-2027).
  Requires due diligence on human rights AND environmental impacts across
  entire value chain including supply chain.
  Could apply to Arkmurus's EU-based OEM client counterparties.
  Arkmurus as SME: directly applies to large companies (500+ employees,
  €150m+ turnover in phase 1). But supply chain obligations cascade down.

UK Modern Slavery Act 2015:
  Section 54: commercial organisations with turnover >£36m must publish
  annual slavery and human trafficking statement.
  Reporting does not require action — transparency mechanism only.
  Relevant to Arkmurus if above threshold.

ARIA APPLICATION:
  UNGPs are increasingly referenced in procurement conditions by:
  - NATO (procurement conditions for members)
  - UK MoD (procurement framework)
  - Major defence primes (supply chain requirements)
  Understanding UNGPs = understanding what Arkmurus OEM clients require
  from their supply chain partners, including BD intermediaries.
"""

# =============================================================================
# SECTION 7 — PRIVATE MILITARY/SECURITY, SOFAs, AND STATUS AGREEMENTS
# =============================================================================

PMSC_AND_SOFA_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — PRIVATE MILITARY, SECURITY, AND STATUS AGREEMENTS   ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
7.1 MONTREUX DOCUMENT (2008)
─────────────────────────────────────────────────────────────────────────────

Full title: Montreux Document on Pertinent International Legal Obligations
  and Good Practices for States related to Operations of Private Military
  and Security Companies during Armed Conflict
Status: Non-binding intergovernmental document
Endorsing states: 58+ including UK, USA, France, Germany, Australia, China
Developed by: Swiss government + ICRC (2006-2008)
Source: https://www.mdforum.ch

Part I — Existing international legal obligations:
  PMSCs and their personnel must comply with applicable international law.
  States using PMSCs: responsible under international law for PMSC conduct
    if PMSCs act as "de facto organs" of the state or if state directed/
    instructed/controlled the conduct.
  Contracting states: must not contract PMSCs to perform certain functions
    (inherently governmental: use of force for security of state).
  Territorial states: have jurisdiction over PMSCs operating in their territory.

Part II — Good practices for states:
  Pre-contractual: background checks, screening, verification.
  During contract: rules of engagement, oversight mechanisms.
  Post-contract: accountability, disciplinary measures.

ARIA RELEVANCE:
  When Arkmurus advisory touches on PMSC deployment by client states:
  - Know which states have endorsed Montreux Document (credibility signal)
  - ICoCA membership of PMSC = baseline quality indicator
  - Montreux Part II standards = basis for evaluating PMSC programme quality

─────────────────────────────────────────────────────────────────────────────
7.2 INTERNATIONAL CODE OF CONDUCT (ICoC) AND ICoCA
─────────────────────────────────────────────────────────────────────────────

ICoC (International Code of Conduct for Private Security Providers) 2010:
  Developed by: Swiss government + industry + civil society multi-stakeholder
  Signatory companies: 700+. Covers: conduct standards for security providers.
  Source: https://www.icoca.ch

ICoCA (ICoC Association): Geneva-based governance body.
  Functions: certification of PMSC compliance, complaint mechanism,
    monitoring, capacity building.
  Certification: increasingly required for:
    UN procurement of security services
    US State Department security contractor registration
    UK FCDO security programme contractors
    NATO programme security requirements

Standards covered by ICoC:
  Use of force (proportionality, minimum force principle)
  Detention practices (treatment of detainees, transfer)
  Protection of vulnerable groups (children, women)
  Personnel vetting and training
  Anti-corruption
  Accountability and transparency
  Grievance and complaint mechanisms

─────────────────────────────────────────────────────────────────────────────
7.3 MERCENARY LAW — INTERNATIONAL AND DOMESTIC
─────────────────────────────────────────────────────────────────────────────

UN Mercenary Convention (International Convention against the Recruitment,
  Use, Financing and Training of Mercenaries) 1989:
  Parties: 37 states. Major states not party: USA, UK, France, Russia, China.
  Definition (Article 1): A mercenary is specifically motivated by private
    gain, not a national/resident of conflict party, not sent by another state.
    This definition is deliberately NARROW — specifically to exclude most PMSCs.

AP I Mercenary Definition (Article 47): Almost identical — very narrow.
  Requires: motivation by private gain (objective test),
  not a national or resident of conflict party,
  not sent by a state as a member of its armed forces.
  Result: most PMSC employees are NOT mercenaries under international law.

OAU Convention (1977): More broadly worded. 28 African state parties.
  Relevant in Africa where AU has institutional interest in regulation.
  Covers: "crime of mercenarism" broadly defined.
  Wagner context: Wagner Group operations were discussed in AU frameworks
  as potential mercenarism — legal characterisation disputed.

DOMESTIC LAW — UK:
  No specific mercenary law in UK.
  PMC activity regulated through: Bribery Act, export controls (SITCL for
  certain services), money laundering regulations, general criminal law.
  UK nationals serving in foreign armed forces: Royal Sign Manual (permission
  required); Foreign Enlistment Act 1870 (rarely enforced, primarily symbolic).

AFRICA CORPS / WAGNER CONTEXT (ARIA Intelligence):
  Wagner Group formally rebranded Africa Corps / African Initiative (2024)
  following Prigozhin death (August 2023).
  Operational presence: Mali, Burkina Faso, Niger, Libya, CAR, Sudan.
  Former presence: Mozambique (2019-2021 — withdrew after poor performance),
    partially effective in Syria, Libya, Ukraine.
  Legal status: Russian state-adjacent PMC with state direction.
  Sanctions: EU, UK, US sanctions on Wagner entities/leaders.
  ARIA: Track Africa Corps presence in client markets. Direct competitor to
  Western security engagement. Context for assessing political risk.

─────────────────────────────────────────────────────────────────────────────
7.4 STATUS OF FORCES AGREEMENTS (SOFAs) AND BASING AGREEMENTS
─────────────────────────────────────────────────────────────────────────────

Definition: SOFA is a bilateral/multilateral agreement defining the legal
  status of foreign military personnel in the host nation. Covers: criminal
  jurisdiction, civil liability, customs, taxation, entry/exit.

NATO SOFA (Agreement between NATO Parties Regarding Status of Forces, 1951):
  Parties: all 32 NATO member states.
  Criminal jurisdiction: sending state jurisdiction for crimes against its
  personnel/property; host state jurisdiction for all others. Both states
  may exercise concurrent jurisdiction. Waiver of jurisdiction common.
  Paris Protocol (1952): extends to civilian NATO bodies.
  London Protocol (1951): SHAPE and Supreme Headquarters status.
  ARIA: NATO SOFA determines legal environment for forces of NATO member
  states in each other's territory. Training missions, exercises, deployments.

UN Status of Mission Agreements (SOMAs):
  Negotiated between UN and each host state for peacekeeping missions.
  Based on: Model SOMA (UNGA Res A/45/594, 1990).
  Key provision: criminal jurisdiction = troop contributing country (TCC).
  Host state waives jurisdiction over UN personnel.
  Controversy: UN peacekeeping sexual abuse cases (Haiti, DRC) — TCC
  jurisdiction means accountability often inadequate.

BILATERAL SOFAs — CPLP CONTEXT:

  Portugal-CPLP bilateral defence cooperation:
    Portugal has bilateral defence cooperation agreements with all CPLP
    African states. Includes provisions for Portuguese military training teams.
    Exercício Felino: biennial CPLP combined exercise involving SOFAs.
    ARIA: Portuguese military training relationships shape equipment preferences
    in CPLP African forces toward Portuguese/European catalogue.

  USA bilateral SOFAs:
    ~100 SOFAs globally. Detailed, comprehensive agreements.
    Angola: No US SOFA. No US permanent basing.
      Limited US AFRICOM engagement. Mostly multilateral (UN/AU/ECOWAS).
      ARIA: Absence of US SOFA = more open procurement environment.
    Mozambique: US Africa Command training arrangements (not formal SOFA).
    Cape Verde: US-Cape Verde defence cooperation agreement. PSI partner.
      US maintains some maritime security engagement.

  French SOFAs in Africa (historical):
    France maintained extensive SOFA network and forward basing in Francophone
    Africa. Post-Sahel withdrawal 2022-2024: framework being renegotiated or
    terminated. Abrupt departures from: Mali (2022), Burkina Faso (2023),
    Niger (2023), Chad (2024 — rebalancing).
    ARIA: French withdrawal creates procurement vacuum. Russian (Africa Corps)
    moving in. Creates entry opportunity for UK/EU/Turkish/Brazilian suppliers
    without French-incumbent advantage. Significant BD signal.

  UK bilateral arrangements:
    Kenya: British Army Training Unit Kenya (BATUK). Long-standing.
    Sierra Leone, Nigeria: training arrangements. No permanent basing.
    No UK SOFA with CPLP African states. Arkmurus operates independently.

TRAINING MISSIONS AND EQUIPMENT IMPLICATIONS:
  Military training relationships are the strongest long-term driver of
  equipment preference. The force that trains with M16s and Bradley IFVs
  will next procure M16s and Bradleys (parts, familiarity, doctrine).
  ARIA tracks training relationships as predictors of future procurement.
  Where Russia/China provide training = future Russian/Chinese equipment sales.
  Where Western states train = future Western equipment procurement pressure.
  This is one of the most analytically important patterns for BD intelligence.
"""

# =============================================================================
# SECTION 8 — CYBER LAW, SPACE LAW, AND EMERGING DOMAINS
# =============================================================================

CYBER_AND_SPACE_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — CYBER LAW, SPACE LAW, AND EMERGING DOMAINS           ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
8.1 CYBER LAW — THE TALLINN MANUAL FRAMEWORK
─────────────────────────────────────────────────────────────────────────────

Tallinn Manual 2.0 on the International Law Applicable to Cyber Operations
  (2017): Non-binding academic expert study. Most authoritative reference.
  Commissioned by: NATO Cooperative Cyber Defence Centre of Excellence (CCDCOE).
  Source: https://ccdcoe.org/research/tallinn-manual

Key findings:
  State sovereignty applies in cyberspace. States must not conduct or
  knowingly allow cyber operations that violate sovereignty of other states.

  USE OF FORCE (UN Charter Article 2(4)):
    Cyber operations can constitute use of force. Factors: consequences
    (destructiveness comparable to kinetic attack), scale, immediate
    reversibility, military character.
    Stuxnet (2010): widely assessed as use of force against Iran's nuclear
    programme. First known state-on-state offensive cyber weapon deployment.
    NotPetya (2017): attributed to Russia, caused $10bn+ damage globally.
    Assessed by UK/US/EU as attributable to GRU (Russian military intelligence).

  ARMED ATTACK (UN Charter Article 51 — self-defence trigger):
    A cyber operation can constitute an armed attack justifying self-defence
    IF it has effects equivalent to kinetic armed attack.
    High threshold: death, destruction, significant damage required.
    Espionage alone: NOT an armed attack. Not prohibited under international law.
    Threshold debate: states disagree on where line sits.

  IHL IN CYBERSPACE:
    IHL applies to cyber operations in armed conflict context.
    Military cyber operations must comply with: distinction, proportionality,
    precaution. Attacks on civilian infrastructure (hospitals, power grids,
    water) may violate IHL if disproportionate civilian harm.
    Dual-use cyber targets (civilian internet used for military comms): complex.

  ATTRIBUTION:
    Technically challenging. Legally required before responsive action.
    States use: technical attribution + intelligence assessments.
    Public attribution: US/UK/EU practice of jointly attributing to states.
    Examples: Russia (APT28, APT29, Sandworm), China (APT10, APT41),
    North Korea (Lazarus Group), Iran (Charming Kitten/APT33).

UN GGE (Group of Governmental Experts) on Cybersecurity:
  11 consensus reports (not binding) since 2004.
  2015 report: existing international law applies to cyberspace.
  Key norms: non-interference in critical infrastructure, no false flag,
  no attacks on CERT teams, cooperation on requests.

OEWG (Open-Ended Working Group) on ICTs:
  All UN member states participate. Parallel process to GGE.
  Less consensus but broader participation. Developing state views included.

EXPORT CONTROL OF CYBER TOOLS:
  Wassenaar Arrangement (2013 amendment): controls on "intrusion software."
  EU Dual-Use Regulation: controls on surveillance and interception technology
  if known or suspected end-use is domestic repression. HRW and Amnesty
  raised concerns about Israeli spyware (Pegasus/NSO Group) exports to
  authoritarian states. EU tightened controls in 2021 Regulation.
  ARIA: cyber surveillance tools = dual-use, potentially controlled.
  Brokering surveillance technology to authoritarian governments = Criterion 2
  risk (repression) + Wassenaar dual-use controls.

─────────────────────────────────────────────────────────────────────────────
8.2 SPACE LAW AND DEFENCE
─────────────────────────────────────────────────────────────────────────────

Outer Space Treaty (OST) 1967:
  Parties: 114. Source: https://www.unoosa.org/oosa/en/ourwork/spacelaw
  Article I: Space is province of all mankind. Free for exploration and use.
  Article II: No national appropriation of outer space, moon, or other bodies.
    (Asteroid mining debate: USA (Space Act 2015), Luxembourg — claim right
    to resources, not territory. OST applicability contested.)
  Article IV: No WMDs in orbit. Moon and celestial bodies = peaceful only.
    No prohibition on conventional military satellites or weapons in orbit.
    US, Russia, China have military reconnaissance, SIGINT, GPS/GLONASS satellites.
  Article VI: States responsible for national activities (including private).
    Authorisation and continuing supervision required for non-government space.
  Article VII: Liability for damage caused by space objects.

Anti-Satellite Weapons (ASAT):
  No explicit treaty prohibition. Legal gap widely acknowledged.
  States with demonstrated ASAT capability: USA, Russia, China, India.
  Russia test (2021): created 1,500+ trackable debris fragments.
    Debris concern = reason for proposed ASAT moratorium.
  US, UK, Canada, Japan, France, Germany: voluntary ASAT test moratorium pledges.
  UN GGE on Outer Space: ongoing discussions; no binding outcome yet.
  Kinetic ASAT = customary IHL applies in armed conflict context.

Military Satellite Categories:
  Reconnaissance: optical/SAR imagery (KeyHole, Lacrosse US; Bars, Persona Russia)
  Signals Intelligence (SIGINT): interception of communications
  Navigation: GPS (USA), GLONASS (Russia), Galileo (EU), BeiDou (China)
  Communications: military SATCOM (MILSTAR, WGS US; Meridian Russia)
  Early Warning: missile launch detection (DSP/SBIRS US; Tundra Russia)
  Electronic Intelligence (ELINT): radar emission monitoring

Commercial GEOINT (Open Source Intelligence):
  Planet Labs: 200+ small satellites, sub-daily imaging, 3-5m resolution.
  Maxar Technologies: WorldView series, 30cm resolution (commercial).
  Airbus DS: Pléiades series, 50cm resolution.
  SAR (Synthetic Aperture Radar): Capella Space, ICEYE, Umbra — day/night,
    cloud-penetrating. Increasingly available commercially.
  ESA Sentinel: Free open-access 10m resolution. Source: sentinel.esa.int
  USGS EarthExplorer: US government imagery. Source: earthexplorer.usgs.gov

ARIA GEOINT CAPABILITY:
  ARIA indexes open-access satellite sources (Sentinel, USGS, NASA).
  For commercial resolution: Arkmurus would require subscription.
  Primary GEOINT use for Arkmurus: port activity monitoring, force posture
  assessment, infrastructure analysis for market entry assessment.

─────────────────────────────────────────────────────────────────────────────
8.3 ELECTRONIC WARFARE AND GPS LAW
─────────────────────────────────────────────────────────────────────────────

GPS Jamming:
  GPS signals are unlicensed frequencies. Deliberate jamming = interference
  with telecommunications + aviation safety hazard.
  ITU Radio Regulations: prohibit deliberate harmful interference with
  other states' radio communications.
  ICAO: GPS jamming affecting aviation safety = potential state responsibility.
  ARIA tracks: gpsjam.org — GPS interference mapped globally.
    High jamming: Baltic states (Russian GPS denial exercises), Middle East,
    parts of Africa near conflict zones.

Electronic Warfare Export Control:
  EW systems = ML11 Wassenaar Munitions List.
  Military ECM/ECCM: SITCL required for brokering.
  Military communications (frequency-hopping, spread spectrum): ML11.
  SATCOM: dual-use — civilian VSAT vs military SATCOM differ significantly
    in control level. Military-encrypted SATCOM = controlled.
"""

# =============================================================================
# SECTION 9 — AML/CFT LAW IN DEFENCE CONTEXT
# =============================================================================

AML_CFT_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — AML/CFT IN DEFENCE AND SECURITY                     ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
9.1 FATF — GLOBAL AML/CFT STANDARD SETTER
─────────────────────────────────────────────────────────────────────────────

FATF (Financial Action Task Force):
  Founded: 1989 by G7 Paris Summit.
  Members: 37 states + 2 regional organisations (EU Commission, GCC).
  Status: Intergovernmental body. Recommendations = international standard.
  Source: https://www.fatf-gafi.org

40 Recommendations:
  Recs 1-2: Risk assessment and national coordination
  Recs 3-8: Money laundering and asset confiscation
  Recs 9-10: Terrorist financing
  Recs 11-12: Customer due diligence (CDD)
  Recs 13-17: Correspondent banking, shell companies, wire transfers
  Recs 18-23: Designated non-financial businesses and professions (DNFBPs)
    NOTE: arms dealers = DNFBP. AML obligations apply to Arkmurus.
  Recs 24-25: Beneficial ownership (companies and trusts)
  Recs 26-32: Financial intelligence, law enforcement, international cooperation
  Recs 33-38: Statistics, NPO sector oversight
  Recs 39-40: Mutual legal assistance, extradition

MUTUAL EVALUATION PROCESS:
  Each FATF member undergoes periodic peer review (MER).
  Assessment: technical compliance with 40 Recs + effectiveness (11 outcomes).
  Results: compliant, largely compliant, partially compliant, non-compliant.
  Poor results → enhanced follow-up, possible grey/black listing.

GREY LIST (Increased Monitoring — "Jurisdictions Under Increased Monitoring"):
  States that have committed to reform within agreed timeline.
  Verify current list at each FATF plenary (February, June, October).
  Historical examples (not current — always check latest):
  Bulgaria, Cameroon, Croatia, Kenya, Nigeria, South Africa, Tanzania.
  ARIA NOTE: Grey list membership is material to AML risk assessment.
    Counterparties from grey list jurisdictions = Enhanced Due Diligence (EDD).
    Payment routing through grey list jurisdiction = red flag.

BLACK LIST (High-Risk Jurisdictions — "Call for Action"):
  North Korea, Iran, Myanmar (as of 2024 — always verify).
  Any transaction involving black-listed jurisdiction = extreme risk.
  ARIA HARD RULE: Do not facilitate any transaction involving black-listed
    FATF jurisdictions. Refer immediately to counsel.

REGIONAL FATF BODIES (FSRBs):
  GIABA: Intergovernmental Action Group Against ML in West Africa (Dakar).
    Members: ECOWAS states + Mauritania. Key for CPLP West Africa markets.
  ESAAMLG: Eastern and Southern Africa. Members include Mozambique, Tanzania.
  GABAC: Central Africa. Members include Cameroon, CAR, Congo, Gabon.
  GAFISUD/GAFILAT: Latin America. Members include Brazil, Colombia.
  MENAFATF: MENA region. Members include Gulf states, Egypt, Morocco.
  APG: Asia/Pacific. Members include India, Pakistan, Indonesia, Malaysia.
  Moneyval: Council of Europe states.

─────────────────────────────────────────────────────────────────────────────
9.2 UK AML LEGAL FRAMEWORK
─────────────────────────────────────────────────────────────────────────────

Proceeds of Crime Act 2002 (POCA):
Source: https://www.legislation.gov.uk/ukpga/2002/29
  Principal Money Laundering Offences (Section 327-329):
    327: Concealing/disguising/converting/transferring criminal property.
    328: Entering into/becoming concerned in arrangement facilitating
         acquisition/retention/use/control of criminal property.
    329: Acquiring/using/possessing criminal property.
  Criminal property: benefit from criminal conduct. Wide definition.
  Maximum penalty: 14 years imprisonment.

  TIPPING OFF (Section 333A): Criminal offence to disclose to suspect that
    SAR has been filed or investigation is underway. Up to 2 years.

  FAILURE TO DISCLOSE (Section 330): regulated sector must report known or
    suspected money laundering. Up to 5 years.

  CONSENT SAR: If Arkmurus identifies suspicious transaction and wants to
    proceed, can file a SAR and request NCA consent. NCA has 7 working days
    to refuse. After 7 days without refusal, deemed consent given.
    This is the "authorised disclosure" mechanism — important safety valve.

SAR (SUSPICIOUS ACTIVITY REPORT):
  Filed with NCA (National Crime Agency) in UK.
  Portal: ukfiu@nca.gov.uk / SARs Online portal.
  Threshold: knowledge OR suspicion. No proof required.
  Volume: ~900,000 SARs filed in UK per year (2023).
  Defence sector SARs: growing in volume post-Wagner/sanctions awareness.

Money Laundering Regulations 2017 (as amended by 2019, 2022 regs):
  Entities subject to regulations include: estate agents, accountants,
  lawyers, financial institutions, AND "high value dealers" (transactions
  >€10,000 in cash or equivalent including arms dealers).
  Arkmurus obligations:
    Customer Due Diligence (CDD): verify customer identity, beneficial owner,
    purpose of business relationship.
    Enhanced Due Diligence (EDD): for PEPs, high-risk third countries,
    complex/unusual transactions, correspondent relationships.
    Record keeping: 5 years minimum.
    Risk assessment: documented risk assessment of business.

─────────────────────────────────────────────────────────────────────────────
9.3 DEFENCE SECTOR AML TYPOLOGIES (FATF and Wolfsberg Group)
─────────────────────────────────────────────────────────────────────────────

FATF and Basel Institute of Governance have documented defence sector-specific
AML red flags. Arkmurus must apply these to every transaction:

PAYMENT RED FLAGS:
  Payment from entity not party to the contract
  Payment routing through third-country accounts without explanation
  Unusual use of cash, cryptocurrency, or value transfer
  Payment structured to avoid reporting thresholds
  Request for commission payment in different currency/jurisdiction
  Payment to nominee account without disclosed beneficiary
  Wire transfer to jurisdiction with no apparent business connection
  Unusually high prepayment or deposit for value of transaction

COUNTERPARTY RED FLAGS:
  No verifiable trading history for claimed experience
  Commission rates >15% without documented justification
  Reluctance to provide beneficial ownership information
  Multiple changes of payment instruction late in transaction
  Requests to use specific bank unfamiliar to both parties
  Use of a law firm or accountant as paymaster (risk of KYC gap)
  Counterparty registered in FATF black/grey list jurisdiction
  Counterparty shares address with sanctioned entity

CONTRACT RED FLAGS:
  Contract value significantly below/above market rate
  Unusual contract terms (no delivery dates, no inspection)
  Goods description vague or inconsistent with known use
  End-user destination changes after contract signed
  Unusual urgency without commercial explanation
  Prohibition on disclosure to third parties without business rationale

ARIA AML ASSESSMENT PROTOCOL:
  Apply typology checklist to every counterparty before engagement.
  If 3+ red flags: STOP. File SAR. Seek legal advice. Do not proceed.
  If 1-2 red flags: Enhanced due diligence. Document findings.
    Consider whether risk can be mitigated before proceeding.
  If 0 red flags: Standard CDD. Proceed with normal monitoring.
  All decisions: documented in Arkmurus file with reasoning.
  Record retention: 5 years minimum from end of business relationship.
"""

# =============================================================================
# SECTION 10 — NEUTRALITY LAW, WTO, AND INVESTMENT LAW IN DEFENCE
# =============================================================================

NEUTRALITY_WTO_INVESTMENT_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — NEUTRALITY, WTO, AND INVESTMENT IN DEFENCE           ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
10.1 NEUTRALITY LAW
─────────────────────────────────────────────────────────────────────────────

Basis: Hague Convention V (land warfare, 1907) and XIII (naval warfare, 1907).
  Customary international law supplements treaties.
  ILC work on neutrality: ongoing but no comprehensive new convention.

Core neutral state obligations:
  Abstain from providing assistance to either belligerent.
  Maintain equal treatment if providing any assistance.
  Prevent belligerents from using neutral territory for military operations.
  Prevent recruitment of their nationals by belligerents.

Modern context:
  Ukraine conflict has tested neutrality law significantly.
  Switzerland: traditional neutral. Initially struggled with allowing transit
    of German arms through Switzerland to Ukraine. Swiss law restricts this.
    2022-2024: Swiss law reform debated to allow re-export of Swiss-origin
    arms (Gepard ammunition, Leopard 2 parts) to Ukraine. Partially resolved.
  Austria: Constitutional neutrality. Similar constraints as Switzerland.
    Cannot join NATO; limited ability to support military coalitions directly.

Neutrality and arms brokering:
  A UK broker is not a neutral state entity. UK's own legal position
  (supporting Ukraine, sanctioning Russia) governs Arkmurus activities.
  Neutrality law applies to states — not private brokers directly.
  However: brokers must comply with national law which reflects state policy.

─────────────────────────────────────────────────────────────────────────────
10.2 WTO AND DEFENCE PROCUREMENT
─────────────────────────────────────────────────────────────────────────────

WTO Agreement on Government Procurement (GPA) 2012:
  47+ WTO members. Obligates covered entities to open procurement to
  foreign suppliers on non-discriminatory basis.
  Defence exception: Article III(1) — allows exclusion of procurements
    "necessary for the protection of its essential security interests
    relating to the procurement of arms, ammunition or war materials."
  ARIA: Almost all military procurement is excluded from GPA via Article III.
  WTO GPA matters for: dual-use procurement, logistics, civilian contractor
    services that sit alongside defence contracts.

WTO Article XXI (General Agreement on Tariffs and Trade):
  Security exception in GATT. States may derogate from WTO commitments
  for: essential security interests relating to fissionable materials,
  arms/ammunition/war materials traffic, or measures in time of war.
  Panel jurisdiction: Russia-Traffic in Transit (2019) — panel found it
    has jurisdiction to review Article XXI invocations. Not self-judging.
  ARIA: sanctions measures citing Article XXI may face WTO challenge.
    Ukraine export controls: US challenged. Multiple ongoing.
    Arms embargo implementation via trade measures = WTO-examined.

─────────────────────────────────────────────────────────────────────────────
10.3 INTERNATIONAL INVESTMENT LAW — DEFENCE SECTOR
─────────────────────────────────────────────────────────────────────────────

Bilateral Investment Treaties (BITs):
  3,300+ BITs in force globally. Core protections: fair and equitable
  treatment, full protection and security, MFN, national treatment,
  no expropriation without compensation, free transfer of funds.

Investor-State Dispute Settlement (ISDS):
  Mechanism: investor brings arbitration claim against host state before
  arbitral tribunals (ICSID, UNCITRAL, SCC, ICC).
  Awards: legally binding, enforceable in signatory states.
  Defence sector relevance:
    State nationalisation of defence assets (Angola nationalised Portuguese
    companies post-independence — historical BIT claims).
    Host state regulatory change affecting defence contracts.
    Contract cancellation by government = potential expropriation claim.

National Security Screening of Foreign Investment:
  UK NSI Act (National Security and Investment Act 2021):
    Mandatory notification: acquisitions in 17 sensitive sectors including
    defence, military/dual-use, AI, semiconductors.
    Government review: can block or impose conditions.
    Source: https://www.gov.uk/guidance/national-security-and-investment-act-guidance
  USA CFIUS (Committee on Foreign Investment in the United States):
    Reviews foreign acquisitions in technology/defence/critical infrastructure.
    Can block or unwind transactions.
  EU FDI Screening Framework (2020): Cooperation mechanism for screening
    foreign investment in strategic sectors across member states.
  ARIA: Any Arkmurus-advised JV or acquisition in UK/EU/US involving foreign
    defence firm must be assessed for NSI/CFIUS/FDI screening requirement.
"""

# =============================================================================
# SECTION 11 — ENVIRONMENTAL LAW IN ARMED CONFLICT AND POST-CONFLICT
# =============================================================================

ENVIRONMENTAL_LAW = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  INTERNATIONAL LAW — ENVIRONMENTAL LAW IN ARMED CONFLICT                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
11.1 IHL ENVIRONMENTAL PROTECTIONS
─────────────────────────────────────────────────────────────────────────────

AP I Articles 35(3) and 55: Prohibit methods of warfare intended or expected
  to cause widespread, long-term, and severe damage to the natural environment.
  This is a HIGH threshold: cumulative ("and") — all three elements required.
  Rarely invoked in practice. Difficult to establish causation.

ENMOD Convention (1977) — Environmental Modification Convention:
  Parties: 78. Prohibits military use of environmental modification techniques
  having widespread/long-lasting/severe effects. Cold War arms control measure.
  Relevant to: weather modification, earthquake/tsunami induction (theoretical).
  Rarely invoked. No state has been found in violation.

Rome Statute Article 8(2)(b)(iv):
  War crime to intentionally launch attack knowing it will cause incidental
  damage to environment which is "clearly excessive" to military advantage.
  This is the Rome Statute's environmental war crimes provision.
  Higher threshold than AP I: "clearly excessive" = standard met only in
  extreme cases (burning Kuwait oilfields 1991 possibly met this).

ICRC Guidelines on the Protection of the Environment in Armed Conflict (2020):
  Non-binding guidance. Restatement of applicable law.
  Key principles: precaution, proportionality apply to environmental damage.
  Protection extends during and post-conflict.

─────────────────────────────────────────────────────────────────────────────
11.2 POST-CONFLICT ENVIRONMENTAL REMEDIATION
─────────────────────────────────────────────────────────────────────────────

Landmine clearance: Ottawa Treaty Article 5 obligation (10 years + extensions).
  Angola: UN HALO Trust estimate 10m+ mines remaining. Ongoing clearance.
  Mozambique: Major clearance completed 2015 (officially mine-free).
  New contamination: ongoing from Cabo Delgado insurgency.
  ARIA BD OPPORTUNITY: Mine clearance programmes = procurement market.
    Demining equipment (manual, mechanical, MDD dogs, robotic).
    UK (HALO, MAG), US (USAID funding), EU (EDF/trust funds) = procurement.

Explosive Ordnance Disposal (EOD) / UXO:
  CCW Protocol V (2003): Explosive remnants of war clearance obligation.
  Cluster munition remnants: particularly dangerous (CCM Article 4).
  Post-conflict environments: significant UXO market for EOD equipment.
  Angola: legacy of 27-year civil war (1975-2002). Extensive UXO.

ARIA: Environmental remediation and mine clearance are legitimate,
  well-funded procurement markets in CPLP African states.
  Not combat procurement but directly adjacent and well-within scope.
"""

# =============================================================================
# SECTION 12 — ARIA LEGAL RISK FRAMEWORK AND COMPLIANCE PROTOCOLS
# =============================================================================

ARIA_LEGAL_RISK_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ARIA — INTERNATIONAL LAW RISK FRAMEWORK AND COMPLIANCE PROTOCOLS          ║
╚══════════════════════════════════════════════════════════════════════════════╝

─────────────────────────────────────────────────────────────────────────────
12.1 LAYERED LEGAL RISK ASSESSMENT — APPLIED TO EVERY TRANSACTION
─────────────────────────────────────────────────────────────────────────────

LAYER 1 — ABSOLUTE PROHIBITIONS (STOP — no exception — do not proceed):
  □ Destination under active UN arms embargo (check UN Sanctions list)
  □ End-user on OFAC SDN / HMT Consolidated / EU Consolidated / OpenSanctions
  □ Goods are: chemical weapons, biological weapons, nuclear weapons,
               cluster munitions (UK party — SITCL holder cannot broker),
               anti-personnel mines (UK party — cannot broker)
  □ Transaction involves WMD development, production, or delivery vehicle
  □ CAATSA Section 231 exposure (significant Russian defence transaction)
  □ Counterparty in FATF black-listed jurisdiction (North Korea, Iran, Myanmar)
  □ Active ICC warrant on end-user individual or direct commander

LAYER 2 — HIGH RISK — ENHANCED DUE DILIGENCE + LEGAL ADVICE REQUIRED:
  □ Destination in active armed conflict with documented IHL violations (ACLED)
  □ Recipient force has documented human rights violations (OHCHR/HRW/Amnesty)
  □ FATF grey list jurisdiction in payment chain
  □ PEP involvement without transparent business rationale
  □ Commission rate >15% without detailed documented justification
  □ Payment routing through third-country accounts
  □ Complex beneficial ownership (3+ layers) without clear rationale
  □ CAATSA exposure (Russian-origin ML goods to third states)
  □ ITAR + SITCL dual compliance required
  □ Criterion 2 adverse indicators (Criterion 2 Risk = MEDIUM or HIGH)
  □ 3+ AML typology red flags present

LAYER 3 — STANDARD RISK — DOCUMENT AND PROCEED WITH NORMAL MONITORING:
  □ SITCL required — obtain before any brokering activity commences
  □ End-user certificate required — verify authority of issuer
  □ Re-export restrictions — confirm client has no onward transfer intent
  □ UK Bribery Act adequate procedures — review before engaging local agents
  □ AML CDD completed — documentation on file
  □ ATT Article 7 risk assessment documented
  □ IHL conflict type characterisation (IAC or NIAC) for context
  □ Wassenaar ML category identified and documented

LAYER 4 — EXPORT CONTROL PATH — IDENTIFY FOR EVERY TRANSACTION:
  □ Wassenaar ML category (ML1-ML22 or Dual-Use List)
  □ UK SITCL required? (Yes for all ML brokering)
  □ UK SIEL required? (Yes for direct export)
  □ UK OGEL applicable? (Check for relevant open licence)
  □ ITAR applies? (US-origin components — DDTC registration + licence)
  □ EAR applies? (Dual-use US-origin components — BIS licence)
  □ EU origin? (Member state licence required)
  □ French origin? (CIEEMG approval required)
  □ German origin? (BAFA approval required)
  □ Israeli origin? (SIBAT approval required)
  □ Turkish origin? (SSB approval required — Measure Group Turkey angle)
  □ End-use monitoring conditions applicable?

─────────────────────────────────────────────────────────────────────────────
12.2 LEGAL RISK RATINGS
─────────────────────────────────────────────────────────────────────────────

GREEN: Layer 1 clear, Layer 2 clear, Layer 3 documented, Layer 4 identified.
  Proceed with normal commercial and compliance protocols.

AMBER: No Layer 1 triggers. One or more Layer 2 factors present.
  Do not proceed without: enhanced DD documented, legal review completed,
  senior Arkmurus sign-off, and if ECJU licence involved — submitted.

RED: Any Layer 1 trigger present.
  DO NOT PROCEED under any circumstances.
  Actions: document findings, notify senior Arkmurus, legal counsel,
  consider SAR filing obligation, preserve records.

─────────────────────────────────────────────────────────────────────────────
12.3 ARIA DD REPORT — MANDATORY INTERNATIONAL LAW SECTIONS
─────────────────────────────────────────────────────────────────────────────

Every ARK-DD report must include:

  IHL COMPLIANCE ASSESSMENT:
    Conflict type (IAC/NIAC/no conflict): [characterise]
    IHL violations by recipient force: [OHCHR/HRW/ACLED/UNSC sources]
    Criterion 2 risk rating: [GREEN/AMBER/RED]
    Impact on ECJU licence assessment: [specify]

  SANCTIONS SCREENING RESULT:
    OFAC SDN: [CLEAR / HIT — specify]
    HMT Consolidated: [CLEAR / HIT — specify]
    EU Consolidated: [CLEAR / HIT — specify]
    UN Consolidated: [CLEAR / HIT — specify]
    OpenSanctions: [CLEAR / HIT — specify]
    CAATSA exposure: [YES/NO — reasoning]

  ANTI-CORRUPTION ASSESSMENT:
    TI CPI score for market: [score, year, percentile]
    TRACE Bribery Risk Matrix: [if available]
    Local agent/intermediary DD: [conducted/pending/not applicable]
    Commission structure: [documented/justified]
    Bribery Act adequate procedures applied: [YES/NO]

  AML ASSESSMENT:
    FATF list status: [member/grey/black]
    Typology red flags: [N flags identified — list]
    SAR trigger: [YES/POSSIBLE/NO]
    CDD level: [standard/enhanced]
    PEP involvement: [YES/NO — if yes, specify]

  EXPORT CONTROL PATH:
    ML category: [ML-N]
    SITCL required: [YES/NO]
    SIEL required: [YES/NO — if export not brokering]
    ITAR applies: [YES/NO — reasoning]
    EAR applies: [YES/NO — reasoning]
    Supplier nation licence: [required from / pending / obtained]
    EUC: [required / obtained / issuing authority]

  LEGAL RISK RATING: [GREEN / AMBER / RED]
  RECOMMENDED LEGAL COUNSEL: [Clifford Chance / DLA Piper / Covington / other]

─────────────────────────────────────────────────────────────────────────────
12.4 KEY OPEN-SOURCE VERIFICATION SOURCES — ARIA REFERENCE LIBRARY
─────────────────────────────────────────────────────────────────────────────

International Courts and Law:
  ICJ:           https://www.icj-cij.org
  ICC:           https://www.icc-cpi.int
  ITLOS:         https://www.itlos.org
  ECHR:          https://www.echr.coe.int

IHL and Armed Conflict:
  ICRC Treaty DB: https://ihl-databases.icrc.org
  Customary IHL:  https://ihl-databases.icrc.org/en/customary-ihl/v1
  ICRC War Law:   https://www.icrc.org/en/war-and-law
  Case Law:       https://casebook.icrc.org

Arms Control and Non-Proliferation:
  ATT:            https://www.thearmstradetreaty.org
  Wassenaar:      https://www.wassenaar.org/control-lists
  OPCW:           https://www.opcw.org
  Mine Ban:       https://www.apminebanconvention.org
  Oslo CCM:       https://www.clusterconvention.org
  NPT:            https://www.un.org/disarmament/wmd/nuclear/npt
  CTBTO:          https://www.ctbto.org

UN Sanctions:
  UN Sanctions:   https://www.un.org/sc/suborg/en/sanctions/information
  UN Panels:      https://www.un.org/securitycouncil/sanctions/panels

UK Export Controls and Sanctions:
  ECJU:           https://www.gov.uk/government/organisations/export-control-joint-unit
  OFSI:           https://www.gov.uk/government/organisations/office-of-financial-sanctions-implementation
  HMT Sanctions:  https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets
  OTSI:           https://www.gov.uk/government/organisations/office-of-trade-sanctions-implementation

US Export Controls and Sanctions:
  OFAC:           https://ofac.treasury.gov
  DDTC/ITAR:      https://www.pmddtc.state.gov/ddtc_public
  BIS/EAR:        https://www.bis.doc.gov
  SDN search:     https://sanctionssearch.ofac.treas.gov

EU Sanctions:
  EU Sanctions:   https://sanctions.ec.europa.eu

Global Sanctions Aggregator:
  OpenSanctions:  https://www.opensanctions.org

Human Rights:
  OHCHR:          https://www.ohchr.org
  HRW:            https://www.hrw.org
  Amnesty:        https://www.amnesty.org
  ACLED:          https://acleddata.com

AML/CFT:
  FATF:           https://www.fatf-gafi.org
  NCA SARs:       https://www.nationalcrimeagency.gov.uk
  Basel AML:      https://index.baselgovernance.org

Maritime Security:
  IMO:            https://www.imo.org
  IMB Piracy:     https://www.icc-ccs.org/icc/imb
  Djibouti Code:  https://www.imo.org/en/ourwork/security/pages/djibouti-code-of-conduct

Anti-Corruption:
  TI CPI:         https://www.transparency.org/en/cpi
  TRACE:          https://www.traceinternational.org
  UNODC:          https://www.unodc.org/unodc/en/corruption
  OECD AB:        https://www.oecd.org/corruption/anti-bribery

PMSCs:
  ICoCA:          https://www.icoca.ch
  Montreux:       https://www.mdforum.ch

Cyber and Space:
  CCDCOE:         https://ccdcoe.org/research/tallinn-manual
  UN OOSA:        https://www.unoosa.org/oosa/en/ourwork/spacelaw

Legal Commentary:
  Opinio Juris:   https://opiniojuris.org
  EJIL Talk:      https://www.ejiltalk.org
  Lawfare:        https://www.lawfaremedia.org
  Just Security:  https://www.justsecurity.org

─────────────────────────────────────────────────────────────────────────────
12.5 ARIA IS NOT A LAWYER — REFERRAL PROTOCOL
─────────────────────────────────────────────────────────────────────────────

ARIA identifies legal risk and applies established frameworks.
ARIA does not provide legal advice. Legal advice requires qualified counsel.

For any AMBER or RED rated transaction, Arkmurus should consult:

Export Control and Sanctions Counsel:
  Clifford Chance (London): Export control, sanctions, FCPA
  DLA Piper (London): Export control, trade sanctions, ITAR
  Covington & Burling (London): Export control, OFAC, ITAR
  King & Spalding (London): FCPA, export control, government contracts
  Skadden (London): Sanctions, OFAC, regulatory

Anti-Corruption and Bribery Counsel:
  Serious Fraud Office (SFO) self-reporting: SFO.gov.uk
  Mishcon de Reya: Criminal defence, SFO investigations
  Peters & Peters: International criminal and regulatory

International Law:
  Matrix Chambers: Public international law, IHL
  20 Essex Street: International arbitration, trade law
  Essex Court Chambers: Investment arbitration, public international law
"""


# =============================================================================
# ALL KNOWLEDGE SECTIONS — Registered for injection
# =============================================================================

ALL_SECTIONS = {
    "loac_ihl": {
        "content": LOAC_IHL,
        "tags": ["IHL", "LOAC", "Geneva-Conventions", "war-crimes",
                 "armed-conflict", "proportionality", "distinction",
                 "prohibited-weapons", "cluster-munitions", "mines"],
        "domain": "international_law",
    },
    "arms_control_non_proliferation": {
        "content": ARMS_CONTROL_NON_PROLIFERATION,
        "tags": ["ATT", "Wassenaar", "ML-categories", "ITAR", "non-proliferation",
                 "export-control", "NPT", "CWC", "BWC", "MTCR", "Australia-Group"],
        "domain": "compliance",
    },
    "sanctions_law": {
        "content": SANCTIONS_LAW,
        "tags": ["sanctions", "OFAC", "SDN", "HMT", "UN-embargo", "CAATSA",
                 "Russia", "arms-embargo", "SAMLA", "OFSI", "secondary-sanctions"],
        "domain": "compliance",
    },
    "international_criminal_law": {
        "content": INTERNATIONAL_CRIMINAL_LAW,
        "tags": ["ICC", "Rome-Statute", "war-crimes", "crimes-against-humanity",
                 "genocide", "command-responsibility", "universal-jurisdiction",
                 "Bribery-Act", "FCPA", "anti-corruption", "UNCAC", "SFO"],
        "domain": "compliance",
    },
    "law_of_the_sea": {
        "content": LAW_OF_THE_SEA,
        "tags": ["UNCLOS", "EEZ", "maritime-security", "piracy", "OPV",
                 "Gulf-of-Guinea", "Djibouti-Code", "Yaoundé-Code", "IUU",
                 "ITLOS", "Cape-Verde", "Angola", "Mozambique"],
        "domain": "international_law",
    },
    "human_rights_law": {
        "content": HUMAN_RIGHTS_LAW,
        "tags": ["human-rights", "ECHR", "ICCPR", "Criterion-2", "OHCHR",
                 "HRW", "export-licence", "internal-repression", "UNGPs",
                 "CSDDD", "corporate-responsibility"],
        "domain": "compliance",
    },
    "pmsc_and_sofa_law": {
        "content": PMSC_AND_SOFA_LAW,
        "tags": ["PMSC", "Montreux", "ICoC", "mercenary", "SOFA", "basing",
                 "NATO-SOFA", "Africa-Corps", "Wagner", "training-missions",
                 "CPLP", "Portugal", "France"],
        "domain": "international_law",
    },
    "cyber_and_space_law": {
        "content": CYBER_AND_SPACE_LAW,
        "tags": ["cyber-law", "Tallinn-Manual", "ASAT", "space-law",
                 "Outer-Space-Treaty", "GPS-jamming", "EW", "CCDCOE",
                 "attribution", "LAWS", "autonomous-weapons"],
        "domain": "international_law",
    },
    "aml_cft_law": {
        "content": AML_CFT_LAW,
        "tags": ["AML", "CFT", "FATF", "POCA", "SAR", "grey-list",
                 "black-list", "CDD", "EDD", "PEP", "red-flags",
                 "money-laundering", "defence-sector"],
        "domain": "compliance",
    },
    "neutrality_wto_investment": {
        "content": NEUTRALITY_WTO_INVESTMENT_LAW,
        "tags": ["neutrality", "WTO", "GPA", "Article-XXI", "BITs",
                 "ISDS", "NSI-Act", "CFIUS", "FDI-screening",
                 "investment-law", "defence-procurement"],
        "domain": "international_law",
    },
    "environmental_law": {
        "content": ENVIRONMENTAL_LAW,
        "tags": ["environmental-law", "ENMOD", "mine-clearance", "EOD",
                 "UXO", "Angola-mines", "Mozambique-clearance",
                 "post-conflict", "demining"],
        "domain": "international_law",
    },
    "aria_legal_risk_framework": {
        "content": ARIA_LEGAL_RISK_FRAMEWORK,
        "tags": ["legal-risk", "compliance-framework", "SITCL", "ITAR",
                 "Criterion-2", "AML", "SAR", "due-diligence-protocol",
                 "GREEN-AMBER-RED", "export-control-path"],
        "domain": "compliance",
    },
}


# =============================================================================
# RAG STORE INJECTION — adapted for ARIA's existing Redis/chromadb stack
# =============================================================================

async def ingest_all_sections() -> dict:
    """Ingest the complete international law library into ARIA's RAG store.

    Uses rag_store.ingest_document() which handles chunking, embedding,
    and chromadb storage automatically. Safe to call repeatedly —
    rag_store deduplicates by source URL.

    Call this:
      - Once at first deploy
      - Periodically (monthly) to refresh after module updates
      - Via POST /api/aria/law/ingest endpoint
    """
    from . import rag_store

    results = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intlaw:{section_name}",
                source_type="international_law",
                title=f"International Law — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/international_law/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("Law section ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("Law section ingestion failed [%s]: %s", section_name, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info("International law ingestion: %d/%d sections, %d total chunks",
                success, len(ALL_SECTIONS), total_chunks)
    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }


# =============================================================================
# LIVE SOURCE CRAWLING — keeps ARIA's legal knowledge current
# =============================================================================

# Primary authoritative sources ARIA should crawl for law updates.
# These are the sources cited in the knowledge sections above.
# The corpus manager's weekly crawl will index fresh content from these.

LAW_SOURCE_URLS = {
    "A": [
        # Treaty bodies and courts
        "https://www.icrc.org/en/war-and-law",
        "https://www.icj-cij.org/en/decisions",
        "https://www.icc-cpi.int/situations-and-cases",
        "https://www.itlos.org/en/main/cases/",
        # Arms control
        "https://www.wassenaar.org/control-lists/",
        "https://www.un.org/disarmament/convarms/arms-trade-treaty-2/",
        "https://mtcr.info/",
        "https://www.opcw.org/",
        # Sanctions
        "https://www.un.org/securitycouncil/sanctions/information",
        "https://ofac.treasury.gov/recent-actions",
        "https://www.gov.uk/government/collections/uk-sanctions-regimes-under-the-sanctions-act",
        # Export control
        "https://www.gov.uk/government/organisations/export-control-organisation",
        "https://www.pmddtc.state.gov/ddtc_public",
        # AML/CFT
        "https://www.fatf-gafi.org/en/publications.html",
    ],
    "B": [
        # Legal analysis
        "https://www.ejiltalk.org/",
        "https://opiniojuris.org/",
        "https://www.lawfaremedia.org/",
        "https://www.justsecurity.org/",
        # Defence law research
        "https://lieber.westpoint.edu/",
        "https://www.chathamhouse.org/topics/international-law",
        "https://rusi.org/explore-our-research",
    ],
}


async def register_law_sources() -> dict:
    """Register the law primary sources with the corpus manager.

    This ensures they are included in the weekly crawl cycle, keeping
    ARIA's legal knowledge fresh as treaties are amended, sanctions
    updated, and new case law emerges.
    """
    from . import corpus_manager

    registered = 0
    for tier, urls in LAW_SOURCE_URLS.items():
        for url in urls:
            added = await corpus_manager.add_url_directly(url, tier)
            if added:
                registered += 1

    logger.info("Law sources registered with corpus manager: %d URLs", registered)
    return {"registered": registered, "total_urls": sum(len(v) for v in LAW_SOURCE_URLS.values())}


async def refresh_law_knowledge() -> dict:
    """Full refresh: re-ingest static sections + crawl live sources.

    Designed to be called from the autonomous scheduler (monthly) or
    manually via the API. Ensures ARIA's legal knowledge stays current.
    """
    # 1. Re-ingest the static law library (catches any module updates)
    ingest_result = await ingest_all_sections()

    # 2. Register sources with corpus manager (idempotent)
    register_result = await register_law_sources()

    # 3. Crawl the law-specific sources for fresh content
    from . import corpus_manager
    crawl_result = await corpus_manager.run_weekly_crawl(tiers=["A", "B"])

    return {
        "static_ingestion": ingest_result,
        "sources_registered": register_result,
        "live_crawl": crawl_result,
    }
