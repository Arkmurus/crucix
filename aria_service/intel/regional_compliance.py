from .engine_wiring import wire_failure

# =============================================================================
# ARIA — Regional Compliance Blocs
# aria_service/intel/regional_compliance.py
#
# ARIA is a GLOBAL defence broking advisor. This module encodes the
# regional frameworks she must reason about with parity across every
# target market: NATO, EU, ECOWAS, SADC, AU, EAC, GCC, ASEAN, MERCOSUR,
# CIS/CSTO, OSCE. No region is privileged over any other — every brief
# should surface the relevant bloc requirements the first time the
# destination touches that region.
#
# SCOPE: Regional defence-trade, arms-control, and procurement
#        frameworks. Complements global_export_control.py (universal
#        export-control layer) and international_law.py (treaty law).
#
# ARIA APPLICATION: Activates when
#   - destination country belongs to any of the covered blocs
#   - a regional organisation is the buyer (peacekeeping force,
#     collective defence arrangement)
#   - NATO interop or standardisation is a procurement driver
#   - a regional sanctions or embargo regime applies
#   - cross-border movement within a regional customs union occurs
#
# ALL SOURCES: Open-source, verifiable, citable.
# Primary: treaty texts, official bloc websites, UN Arms Trade Treaty
#          submissions, SIPRI regional arms trade reports.
# =============================================================================

import logging

logger = logging.getLogger("aria.regional_compliance")


# =============================================================================
# SECTION 1 — NATO (NORTH ATLANTIC TREATY ORGANIZATION)
# =============================================================================

NATO_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  NATO — STANDARDISATION, INTEROPERABILITY, AND PROCUREMENT                ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
NATO (32 members as of 2026 after Finland 2023 and Sweden 2024
accession) is the world's most consequential defence alliance and the
primary driver of interoperability standards worldwide. Even non-NATO
forces in Africa, the Middle East, and Asia buy to NATO standards
because the alliance's STANAGs (Standardisation Agreements) define
ammunition, fuels, communications, and procedural compatibility for
any coalition operation.

For a global defence broker, NATO is relevant in four ways:

  1. Direct sales to NATO members (28 European + US + Canada + UK +
     Turkey, with Sweden/Finland recent additions)
  2. Sales to non-NATO buyers who require NATO-interop (AMISOM,
     AUSSOM, ECOWAS regional forces, partner nations preparing for
     coalition operations, UN peacekeepers)
  3. NATO common-funded procurements via NSPA (NATO Support and
     Procurement Agency) or NCIA (NATO Communications and Information
     Agency)
  4. Industrial participation in NATO programmes (NIAG, CNAD,
     multinational capability groups)

─────────────────────────────────────────────────────────────────────────────
1.1 STANAGs — THE STANDARDISATION LAYER
─────────────────────────────────────────────────────────────────────────────

STANAGs are developed by NATO Standardization Office (NSO) and
published as Standardisation Agreements. They govern everything from
ammunition dimensions to C2 protocols to medical evacuation marking.
Ratification is voluntary per member state but in practice most are
adopted because the cost of non-interoperability is prohibitive.

Key STANAGs relevant to defence BD:

  STANAG 2310 — 5.56×45mm NATO rifle ammunition (the reason
    5.56 NATO is the universal assault rifle calibre)
  STANAG 2324 — 7.62×51mm NATO machine-gun and rifle ammunition
  STANAG 4172 — 5.56×45mm interchangeability performance
  STANAG 4090 — 9×19mm NATO pistol ammunition
  STANAG 2832 — 12.7×99mm (.50 BMG) heavy machine-gun ammunition
  STANAG 4440 — NATO 120mm smoothbore tank gun ammunition
  STANAG 4569 — protection levels for armoured vehicles (Levels 1-6)
  STANAG 4385 — fuel standards (F-34 JP-8, F-54 diesel, F-44 JP-5)
  STANAG 2897 — logistics and supply interoperability
  STANAG 4586 — standard UAV control system interface
  STANAG 4609 — NATO digital motion imagery
  STANAG 4575 — NATO advanced data storage interface
  STANAG 4671 — airworthiness standards for military UAV systems
  STANAG 2999 — use of military helicopters in disaster relief
  STANAG 4107 — mutual acceptance of government quality assurance
  STANAG 4525 — military messaging
  STANAG 5066 — HF radio data link
  STANAG 4285 — HF modem interoperability
  STANAG 4545 — NATO Secondary Imagery Format (NSIF)
  STANAG 4559 — NATO Standard Image Library Interface
  STANAG 4609 — NATO digital motion imagery format
  STANAG 1008 — electrical power characteristics, shipboard
  STANAG 1135 — interchangeability of fuels, lubricants
  STANAG 3350 — analogue video signal
  STANAG 4193 — IFF (Identification Friend or Foe) standards
  STANAG 4197 — EW data exchange
  STANAG 4294 — NILE Link 16 datalink
  STANAG 5516 — Link 16 tactical data exchange
  STANAG 5511 — Link 11 tactical data exchange
  STANAG 5518 — Link 22 HF/UHF tactical data exchange

ARIA CRITICAL: When advising on a platform choice, ARIA must
consider STANAG compatibility. A buyer preparing to join a NATO-
aligned coalition (Ukraine, Georgia, Bosnia, Moldova) will prefer
NATO-calibre ammunition, NATO-compatible radios, STANAG 4569
armour levels, and STANAG 5516 Link 16 datalink over their Soviet-
era equivalents. A Chinese or Russian OEM offering will often
be technically capable but excluded on STANAG grounds alone.

─────────────────────────────────────────────────────────────────────────────
1.2 NATO PROCUREMENT CHANNELS
─────────────────────────────────────────────────────────────────────────────

NSPA (NATO Support and Procurement Agency) — executive agency of the
NATO Support and Procurement Organisation (NSPO). Located in
Luxembourg. Runs common procurement for member states. Annual
turnover roughly €3.5bn. Programmes include multinational
procurement, life-cycle management, and in-service support for
shared fleets (e.g. C-17 SAC, AGS, Alliance Ground Surveillance).

NCIA (NATO Communications and Information Agency) — C4ISR
procurement and operations. Located in Brussels with major sites
in The Hague and Mons. Procures ICT, cyber, satcom, and C2
systems for NATO common-funded activities.

NIAG (NATO Industrial Advisory Group) — industry consultation
under the Conference of National Armaments Directors (CNAD).
Studies and technical reports that often precede formal
procurement.

CNAD (Conference of National Armaments Directors) — senior
committee where NATO armaments decisions are coordinated.
Subordinate main groups: Air (NAFAG), Land (NAAG), Naval
(NNAG), Joint Capability Group on Guided Multiple Launch
Rocket Systems (JCGM).

ARIA BD tip: NSPA framework contracts can be accessed by eligible
contractors. Non-NATO companies can supply to NSPA-managed
programmes through a NATO-member prime. Arkmurus, as a UK broker,
is eligible.

─────────────────────────────────────────────────────────────────────────────
1.3 NATO PARTNERSHIP FRAMEWORKS (NON-MEMBERS)
─────────────────────────────────────────────────────────────────────────────

Partnership for Peace (PfP) — 20 participants including Austria,
Switzerland, Ireland, Malta, and Central Asian states.
Mediterranean Dialogue (MD) — 7 Arab states (Algeria, Egypt,
  Israel, Jordan, Mauritania, Morocco, Tunisia).
Istanbul Cooperation Initiative (ICI) — 4 Gulf states (Bahrain,
  Kuwait, Qatar, UAE).
Partners Across the Globe — Australia, Japan, Republic of Korea,
  New Zealand, Colombia, Mongolia, Pakistan, Iraq, Afghanistan.

These relationships create procurement signals — a partner
country actively engaging with NATO is often preparing to
acquire NATO-interop kit.
"""


# =============================================================================
# SECTION 2 — EUROPEAN UNION (EDF / PESCO / EDA / EDIRPA / ASAP / EDIP)
# =============================================================================

EU_DEFENCE_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  EU DEFENCE INDUSTRIAL POLICY — EDF / PESCO / EDA / EDIRPA / ASAP / EDIP ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
Since 2017 the EU has built a defence industrial policy from near-zero.
The post-2022 Ukraine war accelerated this dramatically. A global
broker must understand EU defence instruments because they are the
fastest-growing pot of non-sovereign defence funding in the world and
create both BD opportunities and compliance touchpoints.

─────────────────────────────────────────────────────────────────────────────
2.1 EUROPEAN DEFENCE FUND (EDF)
─────────────────────────────────────────────────────────────────────────────

Established: 2021 (combining earlier EDIDP and PADR pilots)
Budget: €8 billion for 2021-2027
Purpose: co-finance defence R&D and development projects among
  consortia of at least three member states

Eligibility: EU member states + Norway (EEA member). Third-country
entities (UK, US, Turkey, Israel, etc.) can participate only under
specific conditions:
  - Participation via EU subsidiary, OR
  - Third-country entity does not have non-EU control that "reduces
    the EU strategic interest"
  - No export of technology to non-EU state during the project

ARIA application: UK-based brokers cannot bid directly for EDF funds
but can support EU primes bidding. Many NATO UK OEMs are setting up
or expanding EU subsidiaries (BAE Germany, BAE Sweden via Bofors,
Chemring Germany) precisely to access EDF.

─────────────────────────────────────────────────────────────────────────────
2.2 PERMANENT STRUCTURED COOPERATION (PESCO)
─────────────────────────────────────────────────────────────────────────────

Established: 2017
Members: 25 EU member states (all except Denmark and Malta as of 2026)
Purpose: binding commitments to defence investment, capability
  development, and operational readiness

Active project portfolio (~60 projects) includes:
  Military mobility
  Cyber rapid response
  Medical evacuation
  Maritime mine countermeasures
  Air-to-air refuelling
  Common Eurodrone (MALE UAV)
  Tiger III attack helicopter upgrade
  Soldier modernisation systems
  Joint EU intelligence school

Third countries can participate in individual PESCO projects on a
case-by-case basis under specific rules adopted November 2020.

─────────────────────────────────────────────────────────────────────────────
2.3 EDIRPA / ASAP / EDIP (SHORT-TERM COMMON PROCUREMENT)
─────────────────────────────────────────────────────────────────────────────

EDIRPA (European Defence Industry Reinforcement through common
  Procurement Act) — €500m, 2023-2025. Short-term joint procurement
  to replenish stocks and fill gaps created by Ukraine support.

ASAP (Act in Support of Ammunition Production) — €500m, 2023-2025.
  Ramp-up of ammunition and missile production capacity.

EDIP (European Defence Industry Programme) — successor framework
  proposed 2024, expected €1.5bn+ for 2025-2027. Consolidates and
  expands EDIRPA/ASAP with full common-procurement infrastructure.

ARIA application: these instruments fund EU-origin production. A UK
broker advising an EU member state buyer on a procurement that uses
EDIRPA/ASAP/EDIP funds must consider that EU-origin content rules
may exclude UK or US product.

─────────────────────────────────────────────────────────────────────────────
2.4 EU ARMS EMBARGOES (CFSP COUNCIL DECISIONS)
─────────────────────────────────────────────────────────────────────────────

Current EU arms embargoes (as of 2026):
  Russia (Council Decision 2014/512/CFSP, strengthened 2022)
  Belarus (Council Decision 2012/642/CFSP)
  Iran (Council Decision 2010/413/CFSP)
  North Korea (Council Decision 2016/849/CFSP)
  Syria (Council Decision 2013/255/CFSP)
  Libya (Council Decision 2011/137/CFSP, partial)
  Myanmar/Burma (Council Decision 2013/184/CFSP)
  Venezuela (Council Decision 2017/2074/CFSP)
  Central African Republic (implementing UN 2127(2013))
  Democratic Republic of the Congo (implementing UN)
  Guinea-Bissau (Council Decision 2012/285/CFSP, partial)
  Haiti (Council Decision 2022/2319/CFSP, implementing UN 2653)
  Lebanon (Council Decision 2005/888/CFSP, partial)
  Somalia (Council Decision 2010/231/CFSP, implementing UN)
  South Sudan (Council Decision 2015/740/CFSP, implementing UN)
  Sudan (Council Decision 2014/450/CFSP, implementing UN)
  Yemen (Council Decision 2014/932/CFSP, implementing UN 2216)
  Zimbabwe (Council Decision 2011/101/CFSP)

EU embargoes bind EU member states directly. UK post-Brexit
maintains its own regimes under SAMLA that largely parallel EU
embargoes.
"""


# =============================================================================
# SECTION 3 — AFRICAN REGIONAL FRAMEWORKS
# =============================================================================

AFRICAN_FRAMEWORKS = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  AFRICAN REGIONAL FRAMEWORKS — AU / ECOWAS / SADC / EAC / CEN-SAD         ║
║  + IGAD + G5 SAHEL                                                        ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
Africa is covered by multiple overlapping regional frameworks for
defence, security, and arms control. A global broker advising on any
transaction into an African state must check whether regional bloc
obligations apply in addition to national law and to UK/US/EU export
controls. The most demanding regime among implicated blocs sets the
compliance floor.

─────────────────────────────────────────────────────────────────────────────
3.1 AFRICAN UNION (AU)
─────────────────────────────────────────────────────────────────────────────

AU Peace and Security Council (PSC): the standing decision body on
African peace and security matters. Authorises peace operations and
imposes sanctions including arms embargoes.

Key AU instruments:
  Silencing the Guns in Africa initiative (originally 2020 target,
    now Agenda 2063 flagship) — aims at reducing armed conflict and
    illicit arms flows across the continent.
  African Charter on Democracy, Elections and Governance — grounds
    for AU sanctions on unconstitutional changes of government.
  AU sanctions on member states where unconstitutional change has
    occurred (Guinea, Mali, Burkina Faso, Niger, Sudan, Gabon — at
    various points, regularly reviewed).

AU and ECOWAS arms-control instruments are mutually reinforcing.
ECOWAS is usually the faster and more operational framework.

─────────────────────────────────────────────────────────────────────────────
3.2 ECOWAS — ECONOMIC COMMUNITY OF WEST AFRICAN STATES
─────────────────────────────────────────────────────────────────────────────

Members (15): Benin, Burkina Faso, Cabo Verde, Cote d'Ivoire, The
Gambia, Ghana, Guinea, Guinea-Bissau, Liberia, Mali, Niger, Nigeria,
Senegal, Sierra Leone, Togo. (Note: Mali, Burkina Faso, and Niger
announced withdrawal in 2024, formally effective 2025; legal status
of withdrawal remains contested.)

ECOWAS Convention on Small Arms and Light Weapons, Their Ammunition
and Other Related Materials (2006) — binding on member states.

Key provisions for brokers:
  Article 3 — Prohibition on SALW transfer to non-state actors
  Article 4 — SALW transfer requires written authorisation from
    both importing and exporting member states, certified by ECOWAS
    Secretariat
  Article 5 — Exemption requests from a transfer prohibition must be
    submitted to the Executive Secretary, notified to member states
  Article 6 — End-User Certificate required for every SALW transfer,
    with specific required content (description, serial, quantity,
    itinerary, end-user, government authentication)
  Article 7 — Manufacturers' marking requirements
  Article 8 — Import marking
  Article 9 — Prohibition of transfer of manufacturing technology
    for SALW without ECOWAS authorisation
  Article 10 — National arms register
  Article 11 — Brokers must be registered with their state of
    residence, maintain transaction records, and obtain authorisation
    for every transaction

ARIA CRITICAL: any small-arms or light-weapons transfer to or
through an ECOWAS state requires the ECOWAS procedure on top of
ordinary export licensing. Arkmurus brokering a Turkish small-arms
deal to Ghana needs ECOWAS authentication of the Ghanaian EUC even
though ECJU has already issued the SITCL.

ECOWAS also maintains a regional SALW register and coordinates
ECOMICI / ECOMOG / ECOMOB regional force deployments.

─────────────────────────────────────────────────────────────────────────────
3.3 SADC — SOUTHERN AFRICAN DEVELOPMENT COMMUNITY
─────────────────────────────────────────────────────────────────────────────

Members (16): Angola, Botswana, Comoros, Democratic Republic of the
Congo, Eswatini, Lesotho, Madagascar, Malawi, Mauritius, Mozambique,
Namibia, Seychelles, South Africa, Tanzania, Zambia, Zimbabwe.

SADC Protocol on the Control of Firearms, Ammunition and Other Related
Materials (2001, amended 2006) — binding on member states.

Key provisions:
  Common standards for marking, record-keeping, and tracing
  Prohibition of transfer to non-state actors without authorisation
  Mutual legal assistance on firearms crimes
  SADC regional arms register
  Harmonised end-user verification
  Mutual recognition of national licensing decisions where transfers
    occur between SADC members

SADC Organ on Politics, Defence and Security Cooperation (OPDSC) —
deliberative and decision body. SADC Standby Force for regional
operations (e.g. SAMIM in Mozambique 2021-2024).

SADC and AU imposed sanctions on Madagascar, Eswatini, and various
members at different points. The Zimbabwe targeted sanctions regime
has been subject to repeated review.

─────────────────────────────────────────────────────────────────────────────
3.4 EAST AFRICAN COMMUNITY (EAC)
─────────────────────────────────────────────────────────────────────────────

Members (8): Burundi, Democratic Republic of the Congo (joined 2022),
Kenya, Rwanda, South Sudan, Tanzania, Uganda, Somalia (joined 2023).

The EAC has an emerging regional security architecture:
  East African Standby Force
  East African Community Regional Force (EACRF) — deployed to
    eastern DRC against M23 2022-2023
  Protocol on Peace and Security (2013)
  Common External Tariff on firearms (aligned with AU and national
    rules)

The EAC is the primary coordinating body for SALW control in East
Africa alongside the Regional Centre on Small Arms (RECSA, based in
Nairobi).

─────────────────────────────────────────────────────────────────────────────
3.5 IGAD — INTERGOVERNMENTAL AUTHORITY ON DEVELOPMENT
─────────────────────────────────────────────────────────────────────────────

Members (8): Djibouti, Eritrea, Ethiopia, Kenya, Somalia, South Sudan,
Sudan, Uganda.

Primarily a development organisation but with a peace and security
mandate including counter-terrorism (ICPAT), conflict early warning
(CEWARN), and mediation roles (e.g. IGAD-led South Sudan peace
process).

─────────────────────────────────────────────────────────────────────────────
3.6 G5 SAHEL (SUSPENDED / DEFUNCT)
─────────────────────────────────────────────────────────────────────────────

Formed 2014: Burkina Faso, Chad, Mali, Mauritania, Niger. Dissolved
in practice following the withdrawals of Mali (2022), Burkina Faso
(2023), and Niger (2023). No longer operational as a security force.
The Sahel security architecture in 2026 is in flux — the AES
(Alliance of Sahel States) formed by Mali/Burkina Faso/Niger has
replaced it politically.

─────────────────────────────────────────────────────────────────────────────
3.7 RECSA — REGIONAL CENTRE ON SMALL ARMS
─────────────────────────────────────────────────────────────────────────────

Based in Nairobi. Implements the Nairobi Declaration / Nairobi
Protocol on SALW in the Great Lakes Region and the Horn of Africa.
Provides technical assistance on marking, tracing, and stockpile
management. 15 member states.
"""


# =============================================================================
# SECTION 4 — GULF COOPERATION COUNCIL (GCC)
# =============================================================================

GCC_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  GULF COOPERATION COUNCIL — GCC DEFENCE AND TRADE FRAMEWORK              ║
╚══════════════════════════════════════════════════════════════════════════════╝

Members (6): Bahrain, Kuwait, Oman, Qatar, Saudi Arabia, United Arab
Emirates.

DOMAIN OVERVIEW:
The GCC is both the world's largest per-capita defence market and
one of the most complex jurisdictions for a global broker. The six
members operate highly independent procurement systems but with
significant alignment on interoperability, common customs union, and
(during crisis periods) coordinated sanctions and embargoes. Understanding
GCC-level frameworks is necessary to reason correctly about any Gulf
transaction.

─────────────────────────────────────────────────────────────────────────────
4.1 GCC CUSTOMS UNION AND COMMON EXTERNAL TARIFF
─────────────────────────────────────────────────────────────────────────────

Established 2003, fully implemented 2015. Member states share a
common external tariff structure on most imports. Military and
dual-use items are typically handled bilaterally by each state's
defence ministry, but the customs infrastructure allows goods cleared
into one GCC state to move to others with simplified procedures.

ARIA note: a product delivered to UAE free zone (Jebel Ali, KIZAD)
then re-exported to another GCC member transits a domestic movement
from the customs perspective but is a separate transaction from the
export-control perspective. Brokers frequently confuse the two.

─────────────────────────────────────────────────────────────────────────────
4.2 GCC JOINT DEFENCE AND PEACE SHIELD
─────────────────────────────────────────────────────────────────────────────

Peninsula Shield Force — GCC joint military force, headquartered in
Saudi Arabia. First established 1984, repeatedly reorganised. Deployed
to Bahrain 2011. Practical interoperability driven through Joint
Command exercises.

GCC Joint Defence Council — senior body of defence ministers,
coordinates procurement and doctrine. Has attempted common
procurement programmes (integrated air defence, common radar,
linked C2) with mixed success — national sovereignty concerns usually
prevail over joint procurement.

─────────────────────────────────────────────────────────────────────────────
4.3 GCC UNIFIED LIST OF PROHIBITED / RESTRICTED ITEMS
─────────────────────────────────────────────────────────────────────────────

Not a formal arms embargo but a common standards document covering:
  Items prohibited from import under Islamic law
  Health, safety, and environmental prohibitions
  Some defence-adjacent items (e.g. specific ammunitions, certain
    cyber-surveillance tools)

Member states apply the list variably. Weapons import is subject to
the national defence ministry of each GCC state.

─────────────────────────────────────────────────────────────────────────────
4.4 MEMBER-STATE PROCUREMENT MODELS
─────────────────────────────────────────────────────────────────────────────

Saudi Arabia: General Authority for Military Industries (GAMI) +
  Ministry of Defense. Vision 2030 target of 50% domestic content by
  2030. Saudi Arabian Military Industries (SAMI) is the state OEM
  holding vehicle. Major offset programme — GOCA (General Authority
  for Military Industries) mandates localisation.

UAE: Tawazun Council (industrial participation and offset) + Ministry
  of Defence + Strategic Development Fund. EDGE Group is the state OEM
  holding. Offset historically handled by Tawazun Economic Council
  (now restructured). Free zones (Jebel Ali, KIZAD, Al Bateen) are
  efficient routing hubs.

Qatar: Barzan Holdings (state defence company). Ministry of Defence.
  Significant 2014-2022 procurement programme (Rafale, Eurofighter,
  F-15, F-35 discussion, PANTIR, Patriot).

Kuwait: Kuwait Defence Forces General Staff + Cabinet approval for
  major procurements. Smaller offset programme.

Bahrain: Bahrain Defence Force + Ministry of Defence. US 5th Fleet
  host. Heavy US alignment.

Oman: Royal Army of Oman + Ministry of Defence. Long-standing UK
  relationship (historically the closest GCC relationship with the UK).
  More cautious procurement approach than neighbours.

─────────────────────────────────────────────────────────────────────────────
4.5 GCC INTRA-BLOC DYNAMICS AFFECTING BROKERS
─────────────────────────────────────────────────────────────────────────────

2017-2021 Qatar blockade: Saudi Arabia, UAE, Bahrain, Egypt imposed
a land-sea-air blockade on Qatar from June 2017 until January 2021.
During this period, defence trade between the blockading states and
Qatar was functionally prohibited. Al-Ula Declaration (January 2021)
restored relations but underlying tensions remain.

Iran dynamic: GCC states pursue mixed strategies on Iran. UAE and
Oman maintain working diplomatic channels; Saudi Arabia-Iran
rapprochement (Beijing-mediated, March 2023) has shifted the picture
significantly. ARIA must track the current state of regional
realignment when advising.
"""


# =============================================================================
# SECTION 5 — ASIA-PACIFIC FRAMEWORKS (ASEAN / QUAD / AUKUS / TAC)
# =============================================================================

ASIA_PACIFIC_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  ASIA-PACIFIC FRAMEWORKS — ASEAN / QUAD / AUKUS / TAC / ARF              ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The Asia-Pacific has no unified defence bloc analogous to NATO or the
EU. Instead, a patchwork of bilateral alliances (US hub-and-spoke
system: Japan, South Korea, Australia, Philippines, Thailand) and
emerging plurilateral arrangements (Quad, AUKUS) sits alongside a
regional framework (ASEAN) that explicitly rejects defence alliance
status. A global broker must understand each.

─────────────────────────────────────────────────────────────────────────────
5.1 ASEAN — ASSOCIATION OF SOUTHEAST ASIAN NATIONS
─────────────────────────────────────────────────────────────────────────────

Members (10): Brunei, Cambodia, Indonesia, Laos, Malaysia, Myanmar,
Philippines, Singapore, Thailand, Vietnam. Timor-Leste in observer
status, accession expected mid-2020s.

ASEAN is NOT a defence alliance. It is explicitly a political-economic
community with a principle of non-interference in member states'
internal affairs (Treaty of Amity and Cooperation 1976).

ASEAN-relevant security instruments:
  Treaty of Amity and Cooperation in Southeast Asia (TAC) — 1976,
    opened to non-ASEAN states. 51 states parties as of 2026. Requires
    peaceful settlement, non-interference, renunciation of force.
    Accession is a precondition for ASEAN dialogue partnership.
  ASEAN Regional Forum (ARF) — 27 participants including ASEAN,
    dialogue partners (US, China, Russia, EU, UK, Australia, Japan,
    Korea, India, etc.). Annual ministerial dialogue on regional
    security.
  ASEAN Defence Ministers' Meeting (ADMM) and ADMM-Plus — defence
    ministers track, includes dialogue partners.
  Southeast Asia Nuclear Weapon Free Zone Treaty (Bangkok Treaty) —
    1995. Prohibits nuclear weapons throughout SEA. Has not been
    ratified by any of the five nuclear weapon states.

Arms transparency: ASEAN maintains no central arms register. ARF
encourages voluntary transparency measures, including UN Register of
Conventional Arms submissions.

Myanmar: since 2021 coup, Myanmar is a complicated ASEAN member.
ASEAN Five-Point Consensus (April 2021) — unimplemented. Myanmar's
civilian representation at ASEAN summits suspended from October 2021.
Arms sales to Myanmar subject to UK/EU/US/Australia/Canada/Japan
sanctions; ASEAN as a body has not imposed arms embargo.

─────────────────────────────────────────────────────────────────────────────
5.2 QUAD (QUADRILATERAL SECURITY DIALOGUE)
─────────────────────────────────────────────────────────────────────────────

Members: Australia, India, Japan, United States.

Not a defence alliance. Informal grouping with meetings at leader,
ministerial, and working levels. Focus on maritime security, critical
and emerging technology, supply chain resilience, infrastructure,
climate. Implicit China-balancing purpose.

Defence BD implications: Quad members increasingly prefer
interoperable systems. India's shift from Russian to Western suppliers
is partially driven by Quad alignment. Japan's 2023 export policy
revision enables lethal exports to partners — Japan-Australia and
Japan-UK industrial cooperation (GCAP fighter) are milestones.

─────────────────────────────────────────────────────────────────────────────
5.3 AUKUS — AUSTRALIA, UK, US TRILATERAL SECURITY PARTNERSHIP
─────────────────────────────────────────────────────────────────────────────

Established: September 2021.

Pillar 1 — Nuclear-powered submarines for Australia (sensitive IP,
  US-origin nuclear reactor technology). Timeline: Virginia-class
  SSN deliveries from early 2030s, SSN-AUKUS (British design) from
  late 2030s.
Pillar 2 — Advanced capabilities: AI, quantum, cyber, hypersonics,
  underwater systems, electronic warfare, information sharing. UK
  and Australian firms have preferred access.

BD implications: AUKUS Pillar 2 creates a privileged trilateral
defence-industrial zone. UK companies gain access to US ITAR-
controlled programmes that would otherwise be off-limits. Australian
primes (BAE Australia, Thales Australia, Lockheed Martin Australia,
Raytheon Australia) are now preferred partners for UK/US programmes.
Non-AUKUS partners (Canada, New Zealand) are excluded from Pillar 2.

─────────────────────────────────────────────────────────────────────────────
5.4 FIVE POWER DEFENCE ARRANGEMENTS (FPDA)
─────────────────────────────────────────────────────────────────────────────

Members: Australia, Malaysia, New Zealand, Singapore, United Kingdom.

Established 1971. Consultative arrangement committing members to
"consult in the event of any form of armed attack externally organised
or supported, or the threat of such attack" on Malaysia or Singapore.
Integrated Area Defence System (IADS) at Butterworth, Malaysia, is
the operational headquarters. Annual Exercise Bersama Shield.

BD relevance: FPDA is a minor framework compared to NATO/AUKUS but is
relevant for UK-Malaysia-Singapore trilateral industrial cooperation
and for understanding why UK forces have a continuous presence in
Southeast Asia.

─────────────────────────────────────────────────────────────────────────────
5.5 US BILATERAL ALLIANCES (HUB-AND-SPOKE SYSTEM)
─────────────────────────────────────────────────────────────────────────────

The real Asian defence architecture is the US bilateral alliance
system. A broker must understand who is a US treaty ally because that
status drives FMS access, US export licensing presumptions, and US
interoperability requirements.

Japan — Mutual Cooperation and Security Treaty (1960, current form)
Republic of Korea — Mutual Defense Treaty (1953)
Philippines — Mutual Defense Treaty (1951, reinforced by 2014 EDCA,
  2023 additional EDCA sites)
Thailand — Manila Pact (1954) + Rusk-Thanat communique (1962)
Australia — ANZUS Treaty (1951)
New Zealand — ANZUS (1951, practical effect suspended 1986 over
  nuclear ship visits)

Non-treaty but strategic: Taiwan (Taiwan Relations Act 1979), India
(Major Defense Partner 2016 + 2023 MDP-SAA status), Singapore,
Vietnam (comprehensive strategic partnership), Indonesia.
"""


# =============================================================================
# SECTION 6 — AMERICAS (MERCOSUR / OAS / RIO PACT)
# =============================================================================

AMERICAS_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  AMERICAS — OAS / RIO PACT / MERCOSUR / LATIN AMERICAN SECURITY          ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The Western Hemisphere has a thick treaty architecture from the Cold
War (OAS, Rio Pact, IATRA) but very limited modern defence industrial
cooperation. Most regional states operate independent procurement
systems with close US alignment for major platforms. The exception
is Brazil, which operates a genuine domestic defence industrial base.

─────────────────────────────────────────────────────────────────────────────
6.1 OAS — ORGANIZATION OF AMERICAN STATES
─────────────────────────────────────────────────────────────────────────────

Members (35): all independent states in the Americas except Venezuela
(membership-review status) and Cuba (suspended 1962, suspension lifted
2009 but Cuba has not rejoined).

Key defence and security instruments:

Inter-American Treaty of Reciprocal Assistance (Rio Pact, 1947) —
  collective defence treaty. Invoked after 9/11. Venezuela denounced
  2013, re-ratified 2019, re-denounced 2024. Cuba withdrew 1960.
  Nicaragua withdrew 2012. Mexico withdrew 2002.

Inter-American Convention Against the Illicit Manufacturing of and
  Trafficking in Firearms, Ammunition, Explosives, and Other Related
  Materials (CIFTA, 1997) — binding on 31 OAS members (US signed,
  never ratified). Requires:
    - Criminalisation of illicit manufacturing and trafficking
    - Marking of firearms at manufacture and import
    - Record-keeping for 10 years minimum
    - Import/export licensing
    - Controlled deliveries
    - Law-enforcement cooperation

Inter-American Convention on Transparency in Conventional Weapons
  Acquisitions (1999) — annual reporting on acquisitions.

CICTE (Inter-American Committee Against Terrorism) — counter-terrorism
  coordination.

MAPP (OAS Mission to Support the Peace Process) in Colombia — ongoing
  since 2004, evolved with peace agreement implementation.

─────────────────────────────────────────────────────────────────────────────
6.2 MERCOSUR — COMMON SOUTHERN MARKET
─────────────────────────────────────────────────────────────────────────────

Members (full): Argentina, Brazil, Paraguay, Uruguay
Venezuela: suspended 2016, definitive suspension 2017
Bolivia: acceding member (process ongoing)
Associate: Chile, Colombia, Ecuador, Guyana, Peru, Suriname

Primary mandate: economic integration. Limited defence dimension —
no MERCOSUR arms embargo or control mechanism of its own. MERCOSUR
does coordinate on:
  Common Customs Code (weapons among controlled items)
  MERCOSUR Plan of Action against Illicit Firearms Trafficking (2015)
  Transit controls on cross-border weapons movement

BD relevance: MERCOSUR free trade area affects customs routing. Brazil
is the defence industrial heart of the bloc. Argentina and Uruguay
are typical buyers; Paraguay is small but strategically positioned
(tri-border area with Brazil and Argentina).

─────────────────────────────────────────────────────────────────────────────
6.3 UNASUR / CELAC / PROSUR
─────────────────────────────────────────────────────────────────────────────

UNASUR (Union of South American Nations) — once held a South American
Defence Council. Largely defunct since 2018 after multiple
withdrawals.

CELAC (Community of Latin American and Caribbean States) — 33 members,
regional political forum without defence mandate.

PROSUR (2019) — successor to UNASUR, Bolsonaro-era Brazil initiative,
largely inactive post-2022.

─────────────────────────────────────────────────────────────────────────────
6.4 COLOMBIA NATO GLOBAL PARTNER
─────────────────────────────────────────────────────────────────────────────

Colombia became NATO's first Latin American "Global Partner" in 2018.
Has standing cooperation programme with NATO on standardisation,
education, defence reform. Not a treaty ally but preferred partner
for NATO-interop procurements in the Americas.
"""


# =============================================================================
# SECTION 7 — POST-SOVIET FRAMEWORKS (CIS / CSTO / SCO)
# =============================================================================

POST_SOVIET_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  POST-SOVIET FRAMEWORKS — CIS / CSTO / SCO / EURASIAN ECONOMIC UNION      ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The post-Soviet space has multiple overlapping organisations. Since
February 2022, the practical relevance of most of them is dominated by
the consequences of the Russian invasion of Ukraine. For a global
broker, the critical point is that Russia-linked defence trade is
effectively closed to any UK-based broker, and secondary sanctions
risk extends through many of these organisations.

─────────────────────────────────────────────────────────────────────────────
7.1 CSTO — COLLECTIVE SECURITY TREATY ORGANIZATION
─────────────────────────────────────────────────────────────────────────────

Members (6): Russia, Belarus, Armenia, Kazakhstan, Kyrgyzstan,
Tajikistan.

Note: Armenia has suspended participation in 2023-2024 citing
CSTO's failure to respond to Azerbaijani operations. Practical
future uncertain.

CSTO functions as a collective-defence pact (Article 4, analogous to
NATO Article 5). Operational body: CSTO Collective Rapid Reaction
Force (CRRF). Single real-world deployment: Kazakhstan, January 2022,
internal unrest.

Defence trade: CSTO members can source from Russian OEMs on
preferential terms. The Russian defence industrial base is the
primary supplier for all CSTO members' core equipment.

ARIA rule: a UK broker cannot source Russian equipment for CSTO
members (sanctions). A UK broker CAN sell Western equipment to
Kazakhstan, Kyrgyzstan, Tajikistan, Armenia (subject to end-use and
diversion-risk review). Armenia's 2023-2024 Western reorientation
opens real BD opportunity.

─────────────────────────────────────────────────────────────────────────────
7.2 CIS — COMMONWEALTH OF INDEPENDENT STATES
─────────────────────────────────────────────────────────────────────────────

Current members: Russia, Belarus, Kazakhstan, Kyrgyzstan, Tajikistan,
Uzbekistan, Turkmenistan (associate), Moldova (associate, signalled
withdrawal 2023), Azerbaijan (associate).

Former members: Ukraine (never ratified founding treaty, left
effective 2018), Georgia (left 2009 after Russia-Georgia war).

CIS is a loose political-economic framework. Defence dimension
minimal — meaningful military cooperation happens via CSTO.

─────────────────────────────────────────────────────────────────────────────
7.3 SHANGHAI COOPERATION ORGANIZATION (SCO)
─────────────────────────────────────────────────────────────────────────────

Members: China, Russia, India, Pakistan, Kazakhstan, Kyrgyzstan,
Tajikistan, Uzbekistan, Iran (full member from 2023), Belarus (full
member from 2024).

Observer states: Afghanistan (under Taliban, uncertain status),
Mongolia.

Dialogue partners: many (Türkiye, Qatar, UAE, Saudi Arabia, Egypt,
Armenia, Azerbaijan, Cambodia, Nepal, Sri Lanka).

SCO is a security and economic cooperation organisation. Defence
cooperation is mostly ceremonial (Peace Mission exercises) but the
Regional Anti-Terrorist Structure (RATS) in Tashkent is operational.

ARIA relevance: SCO membership is a BD signal, not a compliance
constraint. A country's SCO trajectory often indicates its procurement
direction (Russian + Chinese systems preferred over Western).

─────────────────────────────────────────────────────────────────────────────
7.4 EURASIAN ECONOMIC UNION (EAEU)
─────────────────────────────────────────────────────────────────────────────

Members: Russia, Belarus, Kazakhstan, Kyrgyzstan, Armenia.

Customs union with common external tariff and free movement of goods.
Military and dual-use items are handled bilaterally by national
authorities. The customs union does create routing considerations —
goods cleared into Kazakhstan can move to Russia without further
customs formality, which creates sanctions-evasion risk on
dual-use goods.

ARIA note: since 2022, US, UK, and EU have repeatedly warned that
apparent re-exports of dual-use goods through Kazakhstan, Kyrgyzstan,
Armenia, and the UAE to Russia will be treated as sanctions evasion.
This warning is a meaningful compliance data point.
"""


# =============================================================================
# SECTION 8 — OSCE (ORGANIZATION FOR SECURITY AND CO-OPERATION IN EUROPE)
# =============================================================================

OSCE_FRAMEWORK = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  OSCE — EUROPEAN ARMS CONTROL AND CONFIDENCE-BUILDING MEASURES            ║
╚══════════════════════════════════════════════════════════════════════════════╝

DOMAIN OVERVIEW:
The OSCE (57 participating states from Vancouver to Vladivostok) is
primarily a confidence-and-security-building-measures (CSBM) forum.
Its relevance for a global broker is limited in operational terms but
important for assessing the compliance baseline of European states
and former Soviet republics.

─────────────────────────────────────────────────────────────────────────────
8.1 OSCE ARMS CONTROL INSTRUMENTS
─────────────────────────────────────────────────────────────────────────────

Treaty on Conventional Armed Forces in Europe (CFE, 1990, adapted
  1999) — limited conventional forces in the Atlantic-to-Urals zone.
  Russia suspended participation 2007; formally withdrew 2015;
  formally denounced 2023. CFE is effectively dead but remains a
  political reference.

Open Skies Treaty (1992) — mutual unarmed aerial observation.
  US withdrew 2020. Russia withdrew 2021. Remaining states parties
  maintain the framework but practical observation has collapsed.

Vienna Document 2011 — confidence-building measures including prior
  notification of significant military activities, annual exchange
  of military information, inspections and evaluation visits.
  Russia's non-compliance with Vienna Document obligations since
  2022 has been documented by the OSCE.

OSCE Document on Small Arms and Light Weapons (2000) — principles
  on SALW export controls, stockpile management, destruction of
  surplus. Updated 2022 with new commitments on tracing.

OSCE Document on Stockpiles of Conventional Ammunition (2003) —
  voluntary destruction of conventional ammunition surplus.

OSCE Principles Governing Conventional Arms Transfers (1993) — early
  precursor to the Arms Trade Treaty. Non-binding political
  commitment.

─────────────────────────────────────────────────────────────────────────────
8.2 OSCE MISSIONS AND ARMED CONFLICT MONITORING
─────────────────────────────────────────────────────────────────────────────

OSCE Special Monitoring Mission to Ukraine (2014-2022) — terminated
  after Russia's refusal to extend mandate. Had been the most
  substantial OSCE field operation.

OSCE Mission to Moldova (ongoing) — Transnistria mediation.

OSCE Minsk Group (Armenia-Azerbaijan) — Nagorno-Karabakh mediation,
  now effectively ended following Azerbaijan's 2023 operation.

─────────────────────────────────────────────────────────────────────────────
8.3 ARIA USE OF OSCE DATA
─────────────────────────────────────────────────────────────────────────────

Vienna Document annual military information exchanges are public for
participating states — ARIA can reference them for baseline
information on declared holdings of armed forces in Europe and
Central Asia. SALW destruction reports under the OSCE Document on
SALW are similarly available.

For non-participating states (e.g. most of Africa, Asia, Latin
America), OSCE is not a data source.
"""


# =============================================================================
# SECTION 9 — THE UN REGISTER OF CONVENTIONAL ARMS (UNROCA)
# =============================================================================

UN_ROCA = """
╔══════════════════════════════════════════════════════════════════════════════╗
║  UN REGISTER OF CONVENTIONAL ARMS (UNROCA)                                ║
╚══════════════════════════════════════════════════════════════════════════════╝

Established: 1991 UN General Assembly Resolution 46/36 L
Purpose: voluntary annual reporting by UN member states on imports
  and exports of seven major conventional weapons categories, plus
  small arms and light weapons (since 2003, reporting on SALW is
  encouraged but not required).

The seven categories:
  I    Battle tanks
  II   Armoured combat vehicles
  III  Large-calibre artillery systems (>75mm)
  IV   Combat aircraft (including UCAVs)
  V    Attack helicopters
  VI   Warships (including submarines, >500 metric tons or >75m)
  VII  Missiles and missile launchers (with range >=25km, plus
       SAM systems, plus MANPADS)

SALW category (since 2003, voluntary).

Annual reporting cycle: states submit data to UN Office for
Disarmament Affairs (UNODA) by 31 May covering the preceding
calendar year. Approximately 30-50 states report in recent cycles,
down from ~100 in the early 2000s.

BD / DD value: UNROCA is a cross-check. A state claiming to purchase
X and another claiming to sell X should produce matching entries.
Discrepancies can flag diversion risk, secrecy, or misdeclaration.
Many high-volume exporters (China, Russia, some Gulf states) have
stopped reporting; their absence is itself a signal.

ARIA application: when assessing counterparty credibility, ARIA can
reference UNROCA submissions as evidence of transparency
engagement. A state that has never reported, or stopped reporting, is
a lower-transparency baseline.
"""


# =============================================================================
# ALL SECTIONS — REGISTRY
# =============================================================================

ALL_SECTIONS = {
    "nato": {
        "content": NATO_FRAMEWORK,
        "tags": ["NATO", "STANAG", "NSPA", "NCIA", "CNAD", "NIAG",
                 "interoperability", "4569", "5516", "Link-16", "MALE",
                 "AGS", "SAC", "PfP", "mediterranean-dialogue", "ICI",
                 "partners-across-the-globe", "standardisation"],
        "domain": "regional_compliance",
    },
    "eu_defence": {
        "content": EU_DEFENCE_FRAMEWORK,
        "tags": ["EU-defence", "EDF", "PESCO", "EDA", "EDIRPA", "ASAP",
                 "EDIP", "CFSP", "eu-arms-embargoes", "eu-sanctions",
                 "member-state-consortia", "third-country-participation"],
        "domain": "regional_compliance",
    },
    "african_frameworks": {
        "content": AFRICAN_FRAMEWORKS,
        "tags": ["AU", "ECOWAS", "SADC", "EAC", "IGAD", "RECSA",
                 "G5-Sahel", "AES", "silencing-the-guns",
                 "salw-convention", "nairobi-protocol", "firearms-protocol",
                 "ecowas-broker-registration", "sadc-register",
                 "eacrf", "ecowas-eucs", "au-arms-embargoes"],
        "domain": "regional_compliance",
    },
    "gcc": {
        "content": GCC_FRAMEWORK,
        "tags": ["GCC", "peninsula-shield", "customs-union", "saudi-arabia",
                 "GAMI", "SAMI", "UAE", "tawazun", "EDGE",
                 "qatar", "barzan", "kuwait", "bahrain", "oman",
                 "jebel-ali", "KIZAD", "al-ula", "iran-saudi",
                 "vision-2030"],
        "domain": "regional_compliance",
    },
    "asia_pacific": {
        "content": ASIA_PACIFIC_FRAMEWORK,
        "tags": ["ASEAN", "TAC", "ARF", "ADMM", "ADMM-plus",
                 "bangkok-treaty", "myanmar-sanctions",
                 "QUAD", "AUKUS", "pillar-1", "pillar-2",
                 "FPDA", "five-power", "japan-alliance",
                 "korea-alliance", "philippines-edca", "anzus",
                 "taiwan-relations-act", "major-defense-partner"],
        "domain": "regional_compliance",
    },
    "americas": {
        "content": AMERICAS_FRAMEWORK,
        "tags": ["OAS", "rio-pact", "CIFTA", "CICTE", "MERCOSUR",
                 "UNASUR", "CELAC", "PROSUR", "colombia-nato",
                 "brazil-industrial-base", "inter-american"],
        "domain": "regional_compliance",
    },
    "post_soviet": {
        "content": POST_SOVIET_FRAMEWORK,
        "tags": ["CIS", "CSTO", "SCO", "EAEU", "CRRF",
                 "armenia-suspended", "kazakhstan-january-2022",
                 "russia-sanctions-evasion", "dual-use-routing",
                 "central-asia", "iran-sco", "belarus-sco"],
        "domain": "regional_compliance",
    },
    "osce": {
        "content": OSCE_FRAMEWORK,
        "tags": ["OSCE", "CFE", "open-skies", "vienna-document",
                 "SALW-document", "stockpiles-document",
                 "principles-1993", "minsk-group", "smm-ukraine"],
        "domain": "regional_compliance",
    },
    "un_roca": {
        "content": UN_ROCA,
        "tags": ["UNROCA", "UNODA", "arms-register",
                 "seven-categories", "battle-tanks",
                 "armoured-vehicles", "combat-aircraft",
                 "attack-helicopters", "warships", "missiles",
                 "transparency", "voluntary-reporting"],
        "domain": "regional_compliance",
    },
}


# =============================================================================
# RAG STORE INJECTION
# =============================================================================

async def ingest_all_sections() -> dict:
    """Ingest the regional compliance library into ARIA's RAG store.

    Called at startup (main.py _seed_knowledge_bg) and on MONTHLY-LAW-
    REFRESH. Safe to call repeatedly — rag_store deduplicates by source URL.
    """
    from . import rag_store

    results = {}
    total_chunks = 0

    for section_name, data in ALL_SECTIONS.items():
        try:
            result = await rag_store.ingest_document(
                data["content"],
                source=f"intlaw:regional_compliance:{section_name}",
                source_type="regional_compliance",
                title=f"Regional Compliance — {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/regional_compliance/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "regional_compliance",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info("Regional compliance section ingested: %s (%d chunks)", section_name, chunks)
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error("Regional compliance section ingestion failed [%s]: %s", section_name, e)
            # R-F898 — a failed section = ARIA missing regional-compliance
            # knowledge. The logger.error reaches the ledger via R-F891's
            # ARIA.* catch, but a structured knowledge_gap is what the
            # self-improve loop actually acts on. Best-effort; never blocks.
            try:
                from . import capability_gaps as _cg
                await _cg.record_gap(
                    gap_type="knowledge_gap",
                    detail=f"regional_compliance section '{section_name}' failed to ingest: {str(e)[:160]}",
                    source="regional_compliance.ingest_all_sections",
                )
            except Exception:
                pass

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "Regional compliance ingestion: %d/%d sections, %d total chunks",
        success, len(ALL_SECTIONS), total_chunks,
    )
    # R-F994 — wire success to brain.
    # R-F4262 (dossier E2) — GATED. This fired unconditionally, so an ingest
    # that failed every single section still told the brain it had succeeded:
    # `wire_success(summary="0/N sections ingested")`. `wire_failure` was
    # imported on the next line and never called. A success wire that cannot
    # fail is the same shape as the Phase A gates §1 records as "certified by
    # an absence" — and here it certified a store that was never filled.
    from .engine_wiring import wire_success, wire_failure
    if success > 0:
        wire_success(
            module="regional_compliance",
            summary=f"Regional compliance: {success}/{len(ALL_SECTIONS)} sections ingested ({total_chunks} chunks)",
            detail=f"Sections: {success}/{len(ALL_SECTIONS)}. Chunks: {total_chunks}.",
            confidence="ASSESSED",
            source_id="regional_compliance:R-F994",
        )
    else:
        wire_failure(
            module="regional_compliance",
            detail=(f"Regional compliance ingest produced NOTHING: "
                    f"0/{len(ALL_SECTIONS)} sections, {total_chunks} chunks. "
                    f"Per-section errors: "
                    f"{[k for k, v in results.items() if v.get('status') == 'ERROR'][:6]}"),
            gap_type="knowledge_gap",
            source="regional_compliance.ingest_all_sections",
        )
    return {
        "sections_ingested": success,
        "total_sections": len(ALL_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
