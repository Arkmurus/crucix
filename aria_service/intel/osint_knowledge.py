"""ARIA OSINT Methodology Knowledge

 Module.

Comprehensive open-source intelligence (OSINT) methodology for defence
procurement intelligence, counterparty investigation, and analytical
tradecraft.  Covers the intelligence cycle, source grading, collection
disciplines, counterparty investigation methodology, structured
analytical techniques, and legal/ethical boundaries.

Ingested into ARIA's knowledge store and RAG store at startup so that
OSINT-related queries automatically surface relevant domain context.
The ``get_osint_context`` helper performs lightweight keyword matching
for prompt-time injection (same pattern as
``procurement_knowledge.get_procurement_context``).

All sources: open-source, publicly verifiable intelligence community
doctrine and best practice."""
from __future__ import annotations
from .engine_wiring import wire_success

import logging
logger = logging.getLogger("aria.osint_knowledge")


# =============================================================================
# KNOWLEDGE BLOCKS
# =============================================================================

INTELLIGENCE_CYCLE = """
================================================================
  ARIA -- THE INTELLIGENCE CYCLE (6-STEP PROCESS)
================================================================

The intelligence cycle is the foundational process model for all
intelligence work.  ARIA follows this cycle for every research task,
from a simple company lookup to a full counterparty investigation.

STEP 1 -- DIRECTION (Requirements Definition)

  What the consumer needs to know.  In ARIA's context the "consumer"
  is the defence broker or BD manager.

  WHAT ARIA DOES:
    - Parses the user query to identify the intelligence requirement.
    - Decomposes compound questions into discrete collection tasks.
    - Prioritises requirements by urgency and decision impact.
    - Identifies which collection disciplines apply (WEBINT, FININT, etc.).

  COMMON PITFALLS:
    - Accepting vague requirements without clarification ("tell me
      about Company X" -- which aspect? ownership? sanctions? capability?).
    - Failing to scope the investigation -- unlimited scope = unlimited time.
    - Not asking what decision the intelligence will support.

  QUALITY MARKERS:
    - Clear statement of the intelligence question.
    - Defined scope boundaries (geography, time period, entity type).
    - Identified decision the intelligence will inform.

STEP 2 -- COLLECTION (Data Gathering)

  Systematic acquisition of raw data from open sources.

  WHAT ARIA DOES:
    - Executes multi-source collection across WEBINT, FININT, DOCINT.
    - Queries structured databases (OpenSanctions, company registries).
    - Searches unstructured sources (news, press releases, PDFs).
    - Records provenance: source URL, access date, source tier.

  COMMON PITFALLS:
    - Single-source dependency -- one source is not intelligence.
    - Confirmation bias in search terms -- only searching for what
      you expect to find.
    - Neglecting negative evidence (absence of expected information
      is itself informative).
    - Not recording provenance at collection time.

  QUALITY MARKERS:
    - Minimum 3 independent sources for any factual claim.
    - Full provenance chain for every data point.
    - Collection plan documented before execution.
    - Both confirming and disconfirming sources sought.

STEP 3 -- PROCESSING (Data Transformation)

  Converting raw data into usable information.

  WHAT ARIA DOES:
    - OCR extraction from PDF documents and scanned images.
    - Entity extraction (names, dates, amounts, locations).
    - Language translation where needed.
    - Deduplication of records from multiple sources.
    - Structuring unstructured text into queryable formats.

  COMMON PITFALLS:
    - OCR errors propagating into analysis unchecked.
    - Entity resolution failures (same entity, different names).
    - Loss of context during extraction (e.g., a number without
      its unit or reference date).

  QUALITY MARKERS:
    - OCR output verified against original where critical.
    - Entity resolution applied consistently.
    - Data normalised (dates, currencies, entity names).

STEP 4 -- ANALYSIS (Sense-Making)

  Transforming processed information into intelligence through
  structured analytical techniques.

  WHAT ARIA DOES:
    - Applies source grading (reliability + credibility matrix).
    - Cross-references claims across independent sources.
    - Identifies patterns, anomalies, and gaps.
    - Applies structured analytical techniques (ACH, chronological
      analysis, link analysis).
    - Assigns confidence tags: [CONFIRMED], [PROBABLE], [ASSESSED],
      [UNCERTAIN], [SPECULATIVE].
    - Flags information gaps and recommends further collection.

  COMMON PITFALLS:
    - Mirror imaging (assuming adversary thinks like you).
    - Anchoring on first-received information.
    - Neglecting alternative hypotheses.
    - Confusing correlation with causation.
    - Presenting raw data as analysis.

  QUALITY MARKERS:
    - Every assertion tagged with confidence level.
    - Alternative hypotheses considered and documented.
    - Gaps and limitations explicitly stated.
    - Analysis distinguishes fact from inference.

STEP 5 -- DISSEMINATION (Delivery)

  Presenting intelligence to the consumer in actionable format.

  WHAT ARIA DOES:
    - Structures output by confidence level (highest confidence first).
    - Leads with the bottom line (BLUF -- Bottom Line Up Front).
    - Separates facts from assessments visually.
    - Provides source citations for verification.
    - Flags items requiring urgent action (sanctions hits, red flags).

  COMMON PITFALLS:
    - Burying the key finding in the middle of a report.
    - Presenting everything at the same confidence level.
    - Not tailoring output to the decision being made.
    - Omitting caveats and limitations.

  QUALITY MARKERS:
    - BLUF present and accurate.
    - Confidence tags on all assessments.
    - Source citations provided.
    - Actionable recommendations included.

STEP 6 -- FEEDBACK (Evaluation and Refinement)

  Consumer feedback drives the next cycle iteration.

  WHAT ARIA DOES:
    - Tracks which intelligence products led to user follow-up questions.
    - Identifies recurring gaps in knowledge base.
    - Logs correction events (when ARIA's assessment was wrong).
    - Updates source reliability ratings based on track record.
    - Feeds lessons learned into the correction learning store.

  COMMON PITFALLS:
    - No feedback mechanism = no improvement.
    - Treating intelligence as one-shot rather than iterative.
    - Not updating assessments when new information arrives.

  QUALITY MARKERS:
    - Correction events logged and analysed.
    - Source reliability updated over time.
    - Repeat queries trigger proactive updates.
"""

SOURCE_GRADING = """
================================================================
  ARIA -- SOURCE GRADING AND CONFIDENCE ASSESSMENT
================================================================

ARIA uses a dual-axis grading system derived from NATO STANAG 2022
(Intelligence Reports) and the Admiralty System.  Every piece of
information is graded on two dimensions:

1. SOURCE RELIABILITY (the source itself)
2. INFORMATION CREDIBILITY (the specific claim)

--- SOURCE RELIABILITY SCALE ---

  A -- COMPLETELY RELIABLE
    No doubt about the source's authenticity, trustworthiness,
    or competency. History of complete reliability.
    ARIA examples: Official government gazette, UN Security Council
    resolution, signed court judgment, central bank publication.

  B -- USUALLY RELIABLE
    Minor doubt. Source has provided valid information in most cases.
    ARIA examples: Major international news wire (Reuters, AFP, AP),
    established defence industry publication (Jane's, SIPRI),
    well-known company registry (Companies House, SEC EDGAR).

  C -- FAIRLY RELIABLE
    Doubt of authenticity, trustworthiness, or competency but has
    provided valid information in the past.
    ARIA examples: Regional news outlets, industry association
    publications, company press releases, LinkedIn profiles.

  D -- NOT USUALLY RELIABLE
    Significant doubt. Has provided invalid information in the past.
    ARIA examples: Unverified social media accounts, anonymous forum
    posts, sources with known bias or propaganda history.

  E -- UNRELIABLE
    History of invalid or fabricated information.
    ARIA examples: Known disinformation outlets, state propaganda
    channels of adversarial nations, previously debunked sources.

  F -- RELIABILITY CANNOT BE JUDGED
    No basis for evaluating reliability. New or unknown source.
    ARIA examples: First encounter with a source, information from
    an unattributed origin, leaked documents without verification.

--- INFORMATION CREDIBILITY SCALE ---

  1 -- CONFIRMED
    Confirmed by other independent sources. Logical, consistent
    with other information on the subject.
    ARIA tag: [CONFIRMED]

  2 -- PROBABLY TRUE
    Not confirmed but logical, consistent with other information,
    and from a source that has been reliable in the past.
    ARIA tag: [PROBABLE]

  3 -- POSSIBLY TRUE
    Not confirmed, reasonably logical, agrees with some other
    information on the subject.
    ARIA tag: [ASSESSED]

  4 -- DOUBTFULLY TRUE
    Not confirmed, possible but not logical, no other information
    on the subject.
    ARIA tag: [UNCERTAIN]

  5 -- IMPROBABLE
    Not confirmed, not logical, contradicted by other information
    on the subject.
    ARIA tag: [SPECULATIVE]

  6 -- TRUTH CANNOT BE JUDGED
    No basis for evaluating the information.
    ARIA tag: [UNVERIFIED]

--- ARIA CONFIDENCE TAG MAPPING ---

  [CONFIRMED]   = A1 or B1 -- Multiple independent reliable sources agree.
  [PROBABLE]    = A2, B2, or C1 -- Reliable source or confirmed by lesser.
  [ASSESSED]    = B3, C2, C3 -- Professional judgment, some support.
  [UNCERTAIN]   = C4, D2, D3 -- Limited evidence, significant doubt.
  [SPECULATIVE] = D4, D5, E-any -- Single unreliable source or contradicted.

--- SOURCE TIER SYSTEM ---

ARIA assigns every source to a tier for rapid assessment:

  TIER 1 -- GOVERNMENT PRIMARY SOURCES
    Official gazettes, legislation, court records, central bank data,
    sanctions lists (OFAC SDN, EU consolidated, UN SC lists),
    government procurement portals (TED, SAM.gov), official statistics.
    Reliability: A. Credibility: typically 1-2.

  TIER 2 -- AUTHORITATIVE INSTITUTIONAL SOURCES
    International organisations (UN, World Bank, IMF, SIPRI, IISS),
    major news wires (Reuters, AFP, AP, Bloomberg), established
    reference works (Jane's, Military Balance), company registries
    (Companies House, SEC EDGAR, national registries).
    Reliability: B. Credibility: typically 1-3.

  TIER 3 -- PROFESSIONAL AND INDUSTRY SOURCES
    Defence trade media (Defense News, Janes.com), industry
    associations, professional databases (Orbis, Dun & Bradstreet),
    company websites and press releases, LinkedIn corporate pages.
    Reliability: B-C. Credibility: typically 2-4.

  TIER 4 -- MEDIA AND SECONDARY SOURCES
    National and regional news outlets, specialist blogs with
    track record, academic papers, NGO reports (Transparency
    International, OCCRP, Global Witness).
    Reliability: C-D. Credibility: typically 3-5.

  TIER 5 -- UNVERIFIED WEB SOURCES
    Social media posts, anonymous forums, unattributed documents,
    personal blogs without track record, AI-generated content
    without verification.
    Reliability: D-F. Credibility: typically 4-6.

  ARIA RULE: No intelligence product may rely solely on Tier 4-5
  sources.  Tier 4-5 information must be corroborated by Tier 1-3
  before inclusion in any deliverable.
"""

COLLECTION_DISCIPLINES = """
================================================================
  ARIA -- OSINT COLLECTION DISCIPLINES FOR DEFENCE BROKING
================================================================

OSINT is not a single method -- it encompasses multiple sub-disciplines,
each with its own sources, tools, and tradecraft.  ARIA deploys these
disciplines systematically based on the intelligence requirement.

--- WEBINT (Web Intelligence) ---

  DEFINITION: Intelligence derived from web-accessible sources including
  surface web, deep web, and cached/archived content.

  ARIA APPLICATION:
    - Search engine queries (Google, Bing, DuckDuckGo) with advanced
      operators (site:, filetype:, intitle:, inurl:, daterange:).
    - Deep web: content behind forms, databases, paywalls that search
      engines do not index (procurement portals, company registries).
    - Cached pages: Google cache, Wayback Machine (web.archive.org)
      for deleted or modified content.
    - Domain WHOIS lookup: registration date, registrant, nameservers.
    - Website technology analysis: hosting provider, CMS, SSL cert age.

  TRADECRAFT:
    - Always check Wayback Machine for historical versions of target sites.
    - Use filetype:pdf to find published documents not linked from main site.
    - A website registered last month selling military equipment is a
      red flag -- check registration date via WHOIS.
    - Cross-reference domain registration with company incorporation date.

--- SOCMINT (Social Media Intelligence) ---

  DEFINITION: Intelligence derived from social media platforms and
  professional networks.

  ARIA APPLICATION:
    - LinkedIn: corporate network mapping, employee identification,
      career history analysis, corporate relationship inference.
    - Twitter/X: real-time signals, executive announcements, geopolitical
      events, defence industry commentary.
    - Facebook/Instagram: less relevant for defence but useful for
      individual background checks in some regions.

  TRADECRAFT:
    - LinkedIn employee count and growth rate indicates company health.
    - Director LinkedIn profiles cross-referenced with company registry.
    - Absence of any social media footprint for a "major defence company"
      is a significant red flag (ghost indicator #3).
    - Track executive job changes -- a wave of departures signals trouble.
    - Monitor defence attache and military official LinkedIn activity
      for relationship mapping.

--- GEOINT (Geospatial Intelligence) ---

  DEFINITION: Intelligence derived from imagery, mapping data, and
  geospatial analysis.

  ARIA APPLICATION:
    - Satellite imagery (Google Earth, Sentinel Hub, Maxar) to verify
      claimed manufacturing facilities, warehouses, military bases.
    - Facility verification: does the claimed factory at address X
      actually exist and show signs of industrial activity?
    - Port and airfield monitoring for arms shipment tracking.
    - Conflict zone mapping for operational context.

  TRADECRAFT:
    - Compare claimed facility photos with satellite imagery.
    - Check if "headquarters" address is actually a residential building
      or virtual office (ghost indicator #5).
    - Time-series imagery shows construction, activity changes.
    - Coordinate cross-referencing with shipping manifests.

--- FININT (Financial Intelligence) ---

  DEFINITION: Intelligence derived from financial records, corporate
  filings, ownership structures, and transaction data.

  ARIA APPLICATION:
    - UBO (Ultimate Beneficial Ownership) registries: UK PSC register,
      EU AMLD beneficial ownership registers, US FinCEN BOI.
    - Company filings: annual accounts, director appointments,
      charges/liens, share allotments.
    - Sanctions screening: OFAC SDN, EU consolidated list, UN SC list,
      OpenSanctions aggregated database.
    - PEP (Politically Exposed Person) databases.
    - Adverse media screening for financial crime, fraud, corruption.
    - Stock exchange filings (10-K, 10-Q, annual reports) for listed
      defence companies.

  TRADECRAFT:
    - Follow the money: trace ownership through shell structures.
    - Multi-jurisdictional UBO lookup -- entities may be incorporated
      in one country with UBOs in another.
    - Check for circular ownership structures (A owns B owns C owns A).
    - Dormant company filings (no activity but maintained) may indicate
      shelf companies ready for use.
    - Cross-reference directors across multiple entities for hidden networks.

--- DOCINT (Document Intelligence) ---

  DEFINITION: Intelligence derived from document analysis including
  PDFs, scanned images, metadata extraction, and content analysis.

  ARIA APPLICATION:
    - PDF text extraction and OCR for scanned documents.
    - Metadata analysis: author, creation date, modification history,
      software used, GPS coordinates in photos.
    - End-User Certificate (EUC) verification: format, signatures,
      issuing authority, consistency with known templates.
    - Contract document analysis: terms, parties, obligations, pricing.
    - Export licence document verification.

  TRADECRAFT:
    - PDF metadata may reveal the true author or organisation even when
      the document is anonymised.
    - Creation date vs claimed date discrepancies indicate forgery.
    - EUC format inconsistencies with known national templates are red flags.
    - Document language and formatting style can indicate true origin.
    - Check for copy-paste artefacts from other documents.

--- TECHINT (Technical Intelligence) ---

  DEFINITION: Intelligence derived from technical specifications,
  patent filings, export classifications, and product documentation.

  ARIA APPLICATION:
    - Product specification sheets: performance claims, compliance
      standards (STANAG, MIL-STD, DEF-STAN).
    - Patent filings (WIPO, USPTO, EPO): technology ownership,
      innovation history, licensing arrangements.
    - Export classification: USML categories (ITAR), EU Common Military
      List, Wassenaar Arrangement Munitions List.
    - Harmonised System (HS) codes for customs classification.
    - Technical standard compliance: AQAP, ISO, STANAG references.

  TRADECRAFT:
    - Cross-reference claimed certifications with issuing body records.
    - Patent holder vs product manufacturer discrepancy may indicate
      licensing or IP issues.
    - HS code classification determines export control applicability.
    - Product performance claims verified against independent test data
      (e.g., STANAG 4569 protection level claims vs AEP-55 test reports).
    - Technical specifications reveal true capability beyond marketing.
"""

COUNTERPARTY_INVESTIGATION = """
================================================================
  ARIA -- COUNTERPARTY INVESTIGATION METHODOLOGY
================================================================

A systematic methodology for investigating companies and individuals
in the defence procurement context.  ARIA follows this checklist for
every new entity encountered.

--- STEP 1: ENTITY IDENTIFICATION ---

  OBJECTIVE: Confirm the entity exists and is who they claim to be.

  ACTIONS:
    a) Name resolution: search for exact legal name, trading names,
       and known aliases.  Defence companies frequently operate under
       multiple names or through subsidiaries.
    b) Registration lookup: verify company registration in claimed
       jurisdiction (Companies House, commercial register, SEC).
    c) Registration number cross-check: registration number on
       correspondence matches the registry record.
    d) Incorporation date: when was the company formed? Recent
       incorporation (< 2 years) for a company claiming long history
       is a red flag.
    e) Registered address: is it a real commercial address or a
       virtual office / residential address?
    f) Legal form: Ltd, GmbH, SA, LLC -- does it match what is
       expected for the claimed size and jurisdiction?

--- STEP 2: NETWORK MAPPING ---

  OBJECTIVE: Map the entity's corporate network and key individuals.

  ACTIONS:
    a) Directors: current and historical directors with appointment dates.
    b) Shareholders: current shareholding structure with percentages.
    c) UBO identification: trace ultimate beneficial owners through
       corporate layers. Minimum 25% threshold in most jurisdictions.
    d) Associated entities: other companies sharing directors,
       addresses, or shareholders.
    e) Parent/subsidiary relationships: group structure mapping.
    f) Key personnel: CEO, CFO, sales directors, country managers.
    g) Cross-jurisdictional links: entities in offshore jurisdictions
       (BVI, Cayman, Seychelles) in the ownership chain.
    h) Sanctioned-jurisdiction nexus: any links to Russia, Iran,
       North Korea, Syria, Belarus, Myanmar or other sanctioned states.

--- STEP 3: SANCTIONS / PEP / ADVERSE MEDIA SCREENING ---

  OBJECTIVE: Identify any legal or reputational risk.

  ACTIONS:
    a) Sanctions lists: OFAC SDN, EU consolidated, UK sanctions list,
       UN Security Council, OpenSanctions aggregated.
    b) Screen ALL identified individuals (directors, UBOs, key personnel)
       not just the entity itself.
    c) PEP screening: are any connected individuals politically exposed?
    d) Adverse media: search for fraud, corruption, arms trafficking,
       money laundering, export control violations.
    e) Debarment lists: World Bank debarment, EU debarment, national
       debarment lists.
    f) Court records: litigation history, criminal proceedings,
       regulatory enforcement actions.
    g) Arms embargo check: is the destination country under any
       arms embargo from UN, EU, or bilateral?

--- STEP 4: DIGITAL FOOTPRINT ANALYSIS ---

  OBJECTIVE: Assess the entity's online presence and legitimacy indicators.

  ACTIONS:
    a) Website analysis: age, content quality, technology stack.
    b) Domain WHOIS: registration date, registrant (privacy-protected
       domains for defence companies are a soft red flag).
    c) Social media presence: LinkedIn company page, employee count,
       activity level.
    d) News coverage: has the company been mentioned in credible media?
    e) Industry presence: trade show participation, industry association
       membership, published case studies.
    f) Online reviews / references: other companies citing them as
       partners or suppliers.
    g) Technical footprint: do they have the IT infrastructure
       expected for their claimed size?

--- STEP 5: GHOST DETECTION (12-INDICATOR CHECKLIST) ---

  ARIA's proprietary checklist for identifying front companies,
  shell entities, and fraudulent actors in defence procurement.

  INDICATOR  1: No verifiable physical address (virtual office or residential).
  INDICATOR  2: Incorporation date < 24 months but claims long history.
  INDICATOR  3: Zero or minimal social media / web presence.
  INDICATOR  4: No identifiable employees beyond directors.
  INDICATOR  5: Registered address is shared with 10+ other companies.
  INDICATOR  6: Directors are professional nominees or serial directors
                across many unrelated companies.
  INDICATOR  7: Offshore entity in ownership chain with no commercial
                justification.
  INDICATOR  8: No filed accounts or dormant accounts for multiple years.
  INDICATOR  9: Claims government contracts but no award records found.
  INDICATOR 10: Email domain different from company website domain.
  INDICATOR 11: Contact person uses generic email (gmail, outlook) not
                corporate domain.
  INDICATOR 12: Product catalogue copied from known manufacturer with
                no evidence of authorised distribution.

  SCORING:
    0-2 indicators: LOW risk -- proceed with normal due diligence.
    3-5 indicators: MEDIUM risk -- enhanced due diligence required.
    6-8 indicators: HIGH risk -- recommend not engaging without
                    independent verification.
    9-12 indicators: CRITICAL risk -- almost certainly a front company
                     or fraudulent entity. Do not engage.

--- STEP 6: CROSS-SOURCE TRIANGULATION ---

  OBJECTIVE: Assess overall confidence in the entity profile.

  ACTIONS:
    a) Compare company registry data with website claims with LinkedIn
       profile with news coverage.
    b) Verify stated capabilities against evidence (facilities, patents,
       contract history, certifications).
    c) Check for internal contradictions (e.g., claims 500 employees
       but LinkedIn shows 5, or claims ISO certification but not in
       IAF CertSearch database).
    d) Assign overall reliability grade using source grading matrix.
    e) Document information gaps -- what could not be verified.
    f) Produce confidence assessment: what do we know, what do we
       think, what do we not know.
"""

ANALYTICAL_TECHNIQUES = """
================================================================
  ARIA -- STRUCTURED ANALYTICAL TECHNIQUES
================================================================

Structured analytical techniques reduce cognitive bias and improve
the rigour of intelligence analysis.  ARIA applies these methods
when the analysis is complex, when multiple hypotheses exist, or
when the stakes of the assessment are high.

--- ACH (ANALYSIS OF COMPETING HYPOTHESES) ---

  PURPOSE: Systematically evaluate multiple hypotheses against
  available evidence to identify the most likely explanation.

  ARIA METHOD:
    1. Identify all reasonable hypotheses (not just the two most obvious).
    2. List all significant evidence and arguments for and against each.
    3. Build a matrix: hypotheses as columns, evidence as rows.
    4. For each piece of evidence, assess whether it is consistent (C),
       inconsistent (I), or not applicable (N/A) to each hypothesis.
    5. Focus on disconfirming evidence -- look for hypotheses that have
       the most inconsistencies, not just the most confirmations.
    6. Rank hypotheses by which has the fewest inconsistencies.
    7. Assess sensitivity: does the conclusion change if any single
       piece of evidence is wrong?

  ARIA APPLICATION EXAMPLE:
    Question: Is Company X a legitimate defence manufacturer or a
    front company?
    H1: Legitimate manufacturer with real capability.
    H2: Trading company brokering others' products (legitimate).
    H3: Front company for sanctioned entity.
    H4: Fraudulent entity with no real capability.
    Evidence: website age, company registry data, factory satellite
    imagery, contract history, director backgrounds, sanctions hits.

--- CHRONOLOGICAL ANALYSIS / TIMELINE RECONSTRUCTION ---

  PURPOSE: Sequence events to identify patterns, gaps, and
  cause-effect relationships.

  ARIA METHOD:
    1. List all dated events related to the subject.
    2. Plot on a timeline with source quality indicators.
    3. Identify: gaps (periods with no information), clusters
       (many events in short period), and sequences (A then B then C).
    4. Look for events that precede or follow known decision points.
    5. Check for anachronisms (events that could not have occurred
       in the claimed sequence).

  ARIA APPLICATION EXAMPLE:
    Company incorporated January 2025, claims 10-year track record.
    Website domain registered February 2025.
    First LinkedIn activity March 2025.
    Claims major government contract completed in 2020.
    CONCLUSION: Timeline inconsistency -- likely fabricated history.

--- LINK ANALYSIS / NETWORK DIAGRAMMING ---

  PURPOSE: Map relationships between entities, individuals,
  locations, and events to identify hidden connections.

  ARIA METHOD:
    1. Identify all entities (companies, people, addresses, accounts).
    2. Map all known relationships (directorship, ownership, family,
       business, financial).
    3. Identify nodes with high connectivity (key individuals or
       entities linking multiple networks).
    4. Look for: shared directors, shared addresses, shared phone
       numbers, shared beneficial owners.
    5. Identify cut points -- entities whose removal would break
       the network into disconnected components.

  ARIA APPLICATION:
    Director A sits on boards of Company X (UK), Company Y (UAE),
    and Company Z (BVI). Company Z is 40% owned by a PEP.
    Company Y supplies Company X. Company X bids on UK MoD contracts.
    LINK ANALYSIS reveals: PEP has indirect access to UK defence
    procurement through a chain of three entities.

--- RED TEAM / DEVIL'S ADVOCATE ---

  PURPOSE: Challenge prevailing assumptions by deliberately
  arguing the opposite position.

  ARIA METHOD:
    1. State the prevailing assessment clearly.
    2. Deliberately construct the strongest possible argument against it.
    3. Identify what evidence would be needed to support the
       alternative view.
    4. Assess whether that evidence exists or could be obtained.
    5. If the alternative cannot be refuted, the prevailing assessment
       must be caveated.

  ARIA APPLICATION:
    Prevailing view: Company X is a reliable supplier.
    Red team: What if Company X is over-leveraged and will fail
    to deliver? Evidence: thin margins in last three annual reports,
    recent departure of CFO, late delivery on previous contract.
    OUTCOME: Caveat added to assessment -- financial stability
    should be monitored.

--- KEY ASSUMPTIONS CHECK ---

  PURPOSE: Identify and evaluate assumptions underpinning an
  analysis that, if wrong, would change the conclusion.

  ARIA METHOD:
    1. List all assumptions underlying the current assessment.
    2. For each assumption, ask: How confident are we? What is it
       based on? Has it been tested? What happens if it is wrong?
    3. Flag any assumption that is untested and upon which the
       conclusion critically depends.
    4. Recommend collection to test critical assumptions.

  ARIA APPLICATION:
    Assumption: "The End-User Certificate is genuine because it
    bears the correct ministry letterhead."
    Challenge: Letterheads can be forged. Has the certificate been
    verified with the issuing ministry? Is the signatory known?
    If the EUC is forged, the entire export licence application
    is based on fraudulent documentation.

--- INDICATORS AND WARNINGS ---

  PURPOSE: Define observable indicators that, if detected, signal
  a change in situation or emerging threat.

  ARIA METHOD:
    1. Define the scenario to monitor (e.g., "Company X intends to
       divert equipment to sanctioned end-user").
    2. Identify indicators that would precede or accompany this scenario.
    3. Assign each indicator a diagnostic value (strong, moderate, weak).
    4. Monitor for indicator activation.
    5. Threshold: if N strong indicators activate, escalate.

  ARIA APPLICATION FOR DIVERSION RISK:
    Strong indicators:
      - Change of delivery address after contract signature.
      - Request to change end-user after EUC issued.
      - Freight route through known diversion hub (UAE, Turkey, Malaysia).
    Moderate indicators:
      - Buyer requests removal of end-use monitoring clause.
      - Buyer representative has connections to sanctioned country.
    Weak indicators:
      - Buyer is a recently incorporated company.
      - Buyer is unwilling to provide post-delivery verification.
"""

OSINT_LEGAL_ETHICAL = """
================================================================
  ARIA -- LEGAL AND ETHICAL BOUNDARIES FOR OSINT
================================================================

OSINT operates within strict legal and ethical boundaries.  ARIA
is designed to use only lawful open-source methods.  This section
defines what ARIA can and cannot do.

--- WHAT OSINT CAN DO (LAWFUL METHODS) ---

  - Access publicly available information on the open internet.
  - Query public government databases and registries.
  - Read published news articles, press releases, and reports.
  - Access company filings that are publicly available by law.
  - Review publicly visible social media profiles and posts.
  - Analyse publicly available satellite imagery.
  - Search patent databases and intellectual property registries.
  - Access court records that are publicly available.
  - Use search engines and their caching features.
  - Access web archives (Wayback Machine).
  - Query sanctions and watchlist databases.
  - Review published procurement notices and award records.

--- WHAT OSINT CANNOT DO (PROHIBITED) ---

  - Hack into systems, networks, or accounts. NEVER.
  - Circumvent access controls, paywalls, or authentication.
  - Use social engineering to extract information from people.
  - Impersonate individuals or organisations.
  - Access private communications (email, messaging) without
    authorisation.
  - Access classified or restricted government systems.
  - Use stolen credentials or leaked authentication data.
  - Deploy malware, keyloggers, or surveillance tools.
  - Intercept communications.
  - Access information through bribery or coercion.
  - Create fake profiles to connect with targets on social media.

--- GDPR AND DATA PROTECTION ---

  When investigating EU-based entities or EU citizens:

  - GDPR Article 6(1)(f): Legitimate interest may justify processing
    of publicly available personal data for due diligence purposes.
  - Data minimisation: collect only personal data necessary for the
    intelligence requirement. Do not build dossiers beyond scope.
  - Storage limitation: personal data should not be retained beyond
    the purpose for which it was collected.
  - Right to erasure: subjects may request deletion -- ARIA flags
    this requirement to the user.
  - Special categories (Article 9): political opinions, religious
    beliefs, health data require additional justification even when
    publicly available.
  - Cross-border transfers: extra care when transferring personal data
    outside the EEA.

  ARIA PRACTICE:
    - Collects publicly available corporate and director data for
      due diligence (legitimate interest under GDPR).
    - Does not store personal data beyond the investigation context.
    - Flags GDPR-sensitive findings for user review.
    - Does not access private social media content.

--- ATTRIBUTION AND PROVENANCE ---

  EVERY piece of intelligence ARIA produces must have:

  1. SOURCE: Where did the information come from?
     (URL, database name, document reference)
  2. ACCESS DATE: When was it accessed?
     (Important because web content changes)
  3. SOURCE TIER: What tier is the source?
     (Tier 1-5 from the source grading system)
  4. CONFIDENCE TAG: How confident is the assessment?
     ([CONFIRMED] through [SPECULATIVE])

  ARIA NEVER presents information without attribution.
  "It is widely known that..." is not acceptable.
  "According to [source] accessed on [date]..." is required.

--- DUTY TO REPORT ---

  In certain circumstances, intelligence findings create legal
  obligations to report:

  SUSPICIOUS ACTIVITY REPORTS (SAR / STR):
    - If ARIA's investigation reveals indicators of money laundering,
      terrorist financing, or sanctions evasion, the user must
      consider whether a SAR/STR obligation arises.
    - ARIA flags these indicators clearly but does not file SARs
      itself -- this is the regulated entity's responsibility.

  EXPORT CONTROL VIOLATIONS:
    - If investigation reveals likely diversion, false end-user
      declarations, or export control circumvention, ARIA flags
      this for the user to consider voluntary disclosure.

  SANCTIONS VIOLATIONS:
    - If a sanctions hit is confirmed, ARIA immediately flags this
      as requiring legal review before any further engagement.
    - ARIA applies a HARD STOP: no further analysis or recommendation
      until sanctions status is resolved by qualified legal counsel.

  ARIA DOES NOT:
    - Provide legal advice (ARIA is an intelligence tool, not a lawyer).
    - File reports on behalf of users.
    - Determine whether a legal obligation exists -- ARIA flags the
      indicators and recommends legal consultation.

--- ETHICAL PRINCIPLES ---

  1. PROPORTIONALITY: The depth of investigation must be proportionate
     to the risk and the decision at stake.
  2. NECESSITY: Collect only what is needed for the intelligence
     requirement. No fishing expeditions.
  3. ACCURACY: Present information as accurately as possible with
     appropriate caveats.
  4. FAIRNESS: Do not selectively present evidence to support a
     predetermined conclusion.
  5. TRANSPARENCY: Be clear about methods used and limitations.
  6. DO NO HARM: OSINT findings can affect reputations and livelihoods.
     Ensure accuracy before presenting negative findings.
"""


# =============================================================================
# SECTION REGISTRY
# =============================================================================

OSINT_SECTIONS: dict[str, dict] = {
    "intelligence_cycle": {
        "content": INTELLIGENCE_CYCLE,
        "tags": [
            "intelligence-cycle", "direction", "collection", "processing",
            "analysis", "dissemination", "feedback", "methodology",
            "tradecraft", "requirements",
        ],
        "domain": "osint",
    },
    "source_grading": {
        "content": SOURCE_GRADING,
        "tags": [
            "source-grading", "reliability", "credibility", "confidence",
            "CONFIRMED", "PROBABLE", "ASSESSED", "UNCERTAIN", "SPECULATIVE",
            "admiralty-system", "NATO", "STANAG-2022", "tier", "source-tier",
        ],
        "domain": "osint",
    },
    "collection_disciplines": {
        "content": COLLECTION_DISCIPLINES,
        "tags": [
            "WEBINT", "SOCMINT", "GEOINT", "FININT", "DOCINT", "TECHINT",
            "collection", "web-intelligence", "social-media", "geospatial",
            "financial-intelligence", "document-intelligence", "technical",
            "LinkedIn", "satellite", "UBO", "sanctions", "OCR", "patent",
        ],
        "domain": "osint",
    },
    "counterparty_investigation": {
        "content": COUNTERPARTY_INVESTIGATION,
        "tags": [
            "counterparty", "investigation", "due-diligence", "entity",
            "network-mapping", "sanctions-screening", "PEP", "ghost",
            "front-company", "shell-company", "UBO", "directors",
            "digital-footprint", "triangulation", "KYC", "AML",
        ],
        "domain": "osint",
    },
    "analytical_techniques": {
        "content": ANALYTICAL_TECHNIQUES,
        "tags": [
            "ACH", "analysis-competing-hypotheses", "chronological",
            "timeline", "link-analysis", "network", "red-team",
            "devil-advocate", "key-assumptions", "indicators-warnings",
            "structured-analysis", "bias", "methodology",
        ],
        "domain": "osint",
    },
    "osint_legal_ethical": {
        "content": OSINT_LEGAL_ETHICAL,
        "tags": [
            "legal", "ethical", "GDPR", "data-protection", "provenance",
            "attribution", "SAR", "STR", "duty-to-report", "sanctions",
            "export-control", "hacking", "prohibited", "proportionality",
            "privacy", "lawful", "boundaries",
        ],
        "domain": "osint",
    },
}


# =============================================================================
# SEARCH INDEX (built once at import time)
# =============================================================================

_SEARCH_INDEX: list[dict] = []


def _build_search_index() -> None:
    """Pre-compute a flat list of searchable entries from all sections."""
    for section_name, data in OSINT_SECTIONS.items():
        text_lower = data["content"].lower()
        tags_lower = " ".join(t.lower() for t in data["tags"])
        _SEARCH_INDEX.append({
            "id": section_name,
            "search_text": f"{text_lower} {tags_lower}",
            "content": data["content"],
            "tags": data["tags"],
            "domain": data["domain"],
        })


_build_search_index()


# =============================================================================
# KEYWORD CONTEXT RETRIEVAL
# =============================================================================

def get_osint_context(query: str) -> str:
    """Return relevant OSINT methodology knowledge for a given query.

    Performs keyword matching against the knowledge blocks and returns
    a formatted string suitable for inclusion in ARIA prompts.  Same
    pattern as ``procurement_knowledge.get_procurement_context``.

    Args:
        query: Free-text query (e.g., "source grading", "ghost company
               detection", "ACH analysis", "GDPR due diligence").

    Returns:
        Formatted multi-line string with matching sections.
        Empty string if no matches found.
    """
    if not query or not query.strip():
        return ""

    query_lower = query.lower().strip()
    tokens = query_lower.split()

    scored: list[tuple[int, dict]] = []
    for entry in _SEARCH_INDEX:
        score = 0
        # Exact section-name match
        if query_lower in entry["id"].lower():
            score += 100
        # Token matching
        for token in tokens:
            if len(token) < 2:
                continue
            if token in entry["search_text"]:
                score += 10
            if token in entry["id"].lower():
                score += 20
        if score > 0:
            scored.append((score, entry))

    if not scored:
        return ""

    # Sort by score descending, take top 3
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:3]

    parts: list[str] = [
        "=== ARIA OSINT Methodology Context ===",
        "",
    ]
    for _score, entry in top:
        parts.append(f"--- {entry['id'].replace('_', ' ').upper()} ---")
        # Truncate to first ~3000 chars to keep prompt size manageable
        content = entry["content"].strip()
        if len(content) > 3000:
            content = content[:3000] + "\n[... truncated ...]"
        parts.append(content)
        parts.append("")

    return "\n".join(parts)


# =============================================================================
# INGESTION (called at startup from _seed_knowledge_bg)
# =============================================================================

async def ingest_to_knowledge() -> dict:
    """Store OSINT methodology knowledge into the knowledge + RAG stores.

    Ingests all sections as permanent CONFIRMED facts via
    ``knowledge.store_fact``, and as RAG documents via
    ``rag_store.ingest_document`` for semantic retrieval.

    Follows the same pattern as ``dd_case_library.ingest_all_cases()``
    and ``nato_standards.ingest_to_knowledge()``.
    """
    from . import knowledge, rag_store

    results: dict = {}
    total_chunks = 0

    for section_name, data in OSINT_SECTIONS.items():
        try:
            # Permanent knowledge fact
            await knowledge.store_fact(
                topic=f"osint_{section_name}",
                content=data["content"],
                source=f"osint_knowledge:{section_name}",
                confidence="CONFIRMED",
            )

            # RAG ingest for semantic search
            result = await rag_store.ingest_document(
                text=data["content"],
                source=f"osint_knowledge:{section_name}",
                source_type="osint_knowledge",
                title=f"OSINT Methodology -- {section_name.replace('_', ' ').title()}",
                url=f"internal://aria/osint_knowledge/{section_name}",
                extra_metadata={
                    "domain": data["domain"],
                    "tags": ",".join(data["tags"]),
                    "module": "osint_knowledge",
                    "module_version": "1.0",
                },
            )
            chunks = result.get("chunks", 0) if isinstance(result, dict) else 0
            total_chunks += chunks
            results[section_name] = {"status": "OK", "chunks": chunks}
            logger.info(
                "OSINT section ingested: %s (%d chunks)",
                section_name, chunks,
            )
        except Exception as e:
            results[section_name] = {"status": "ERROR", "error": str(e)}
            logger.error(
                "OSINT knowledge ingestion failed [%s]: %s", section_name, e,
            )

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info(
        "OSINT knowledge ingestion: %d/%d sections, %d total chunks",
        success, len(OSINT_SECTIONS), total_chunks,
    )

    # R-F996 — wire to brain
    wire_success(
        module="osint_knowledge",
        summary="OSINT knowledge",
        source_id="osint_knowledge:R-F996",
    )
    return {
        "sections_ingested": success,
        "total_sections": len(OSINT_SECTIONS),
        "total_chunks": total_chunks,
        "detail": results,
    }
