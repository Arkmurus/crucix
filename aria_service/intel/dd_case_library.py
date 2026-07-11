"""ARIA DD Case Library — permanent teaching examples from real investigations.

When a real DD case surfaces a pattern ARIA should recognise
automatically next time, it gets codified here as a CONFIRMED
knowledge fact. The library is ingested into knowledge.py at startup
so future DD runs on the same entity auto-surface the prior finding,
and future DD runs on SIMILAR entities can pattern-match against the
stored case via neural_memory or semantic search.

Each case carries:
  - topic:       canonical entity name (used for knowledge.store_fact)
  - content:     structured narrative of the findings
  - source:      where the finding originated (session + reporter)
  - confidence:  CONFIRMED (only after human verification)
  - patterns:    list of keyword patterns for cross-case matching

Additions go in reverse chronological order. Each entry must carry
explicit disclaimers: the finding is locked to the date of
investigation; if the entity remediates (retracts claims, publishes
verified filings, obtains ANCEX authorisation), update or remove the
case.
"""
from __future__ import annotations
from .engine_wiring import wire_failure

import logging
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.dd_case_library")


# =============================================================================
# CASE 001 — SERBAN INDUSTRIES SRL (Bucharest, Romania) — 2026-04-11
# =============================================================================
#
# Reporter: Antonio / Ari (Arkmurus founders)
# Context:  C4 / ML8 brokering opportunity via Serban as intermediary,
#           proposed by Alon. 27 March 2026 initial RFQ.
# Status:   RED / NO-GO — investigation complete, findings below.
#
# This is the canonical "ghost entity with deliberate misrepresentation"
# pattern. Every indicator in the Arkmurus playbook triggered against
# this entity when the human investigation was done, but the automated
# DD tool returned GREEN on the first three runs because registry
# lookup failed. The lesson is that GREEN on data_gaps is a NULL
# result, not a clearance — and that indicator 11 (founding-year
# misrepresentation via sequential-CUI analysis) is now a first-class
# orchestrator detector precisely because of this case.

SERBAN_INDUSTRIES_SRL = """
SERBAN INDUSTRIES SRL — Romania — [HIGH RISK / NO-GO — 2026-04-11]

Entity identity:
  Legal name:         Serban Industries SRL (also trades as
                      "Serban Incorporated & Partners")
  CUI:                51884521 (Romanian company number)
  Registered address: Str. Dridu 1, Bl. K3, Sc. B, Et. 3, Ap. 34,
                      Cod 013201, Bucharest, Romania
  Authorised rep:     Serban Sebastian
  Website:            s3rban.com (leetspeak)
  Email domain:       defense-division@s3rban.com
  CAEN code:          4619 — Intermediation in the trade of various
                      products (NOT a defence licence; covers general
                      commercial agents only)

Incorporation analysis:
  CUI 51884521 → sequential-CUI analysis places incorporation at
  approximately 2024-11 / 2025-01. The company is AT MOST 18 months old
  as of 2026-04-11. Verify against ONRC portal (https://portal.onrc.ro).

Red flags identified by human investigation 2026-04-11:

1. FALSE FOUNDING CLAIM [CONFIRMED / HARD STOP]
   Website claims "founded in 1994" and "31 years of experience".
   CUI-derived incorporation: 2024-11 / 2025-01. Gap: 30 years.
   Romanian CUIs are issued sequentially by ONRC — this is not a
   rounding error or ambiguity. It is a verifiable misrepresentation
   on a material fact. Under UK / EU contract law this constitutes
   fraudulent misrepresentation and voids contracting integrity.

2. RESIDENTIAL APARTMENT REGISTERED ADDRESS [CONFIRMED / RED]
   "Bl. K3, Sc. B, Et. 3, Ap. 34" decodes to Block K3, Staircase B,
   Floor 3, Apartment 34 — a residential flat, not a commercial
   office. Residential registrations are legal in Romania for SMEs
   but are a ghost-indicator 2 trigger on a claimed defence
   intermediary.

3. DUAL TRADING NAMES [CONFIRMED / AMBER]
   "Serban Industries SRL" and "Serban Incorporated & Partners" are
   the same entity (same CUI, address, representative, website,
   email domain). On a C4/ML8 transaction this is an immediate ECJU
   disclosure problem: SITCL requires naming ALL parties. Two
   letterheads pointing at one entity is not illegal per se but
   becomes relevant on licence application.

4. LEETSPEAK DOMAIN [CONFIRMED / AMBER]
   Domain 's3rban.com' uses digit '3' substituting for letter 'e'
   in the root label. Legitimate defence counterparties do not
   use leetspeak domains. Either an unimaginative brand choice
   that signals amateur presentation, or a typosquat attempt.

5. NO ANCEX BROKERING AUTHORISATION [CONFIRMED / HARD STOP]
   Romania is an EU member state and NATO ally with a mandatory
   national licensing regime for arms brokering administered by
   ANCEX (Agenția Națională de Control al Exporturilor). CAEN 4619
   (Intermediation in the trade of various products) covers
   non-specialised commercial agents and does NOT authorise any
   defence / ML-listed goods brokering. Serban Industries SRL has
   no evidence of an ANCEX authorisation. Without it they cannot
   legally broker C4 or any ML-listed goods from or through
   Romania. Arkmurus participating in a transaction with an
   unauthorised Romanian broker would be a compliance failure under
   both UK SITCL (associated-person liability) and Romanian Law
   295/2004 on the regime of weapons and ammunition.

6. ZINOZ SUPPLY CHAIN ADVERTISEMENT [RED — requires forensic review]
   A linked advertisement surfaced documenting "untracked shipments"
   as an advertised capability. "Untracked shipments" is explicit
   diversion-risk language under ATT Article 11 and prohibited
   under UK SITCL conditions. If the advertisement is genuine and
   attributable, this is evidence of active intent to conduct
   unlicensed transfers.

7. END BUYER F3 UNDISCLOSED [CONFIRMED / HARD STOP]
   The proposed deal structure had F3 anonymised via escrow. This is
   not permissible under UK law for SITCL brokering licences — all
   parties to the transaction must be identified and screened. F3
   anonymity was already rejected in Arkmurus restructure terms.

Compliance exposure for Arkmurus if engagement proceeds:
  - UK Bribery Act 2010 s.7 strict liability — onboarding a
    counterparty with deliberate misrepresentation is a failure of
    adequate procedures
  - UK SITCL brokering licence application would require disclosure
    of Serban as intermediary — ECJU would conduct own checks,
    refuse licence, damage Arkmurus credibility for future
    applications
  - UK POCA 2002 s.330/s.331 — knowledge or suspicion of money-
    laundering or defence-sector fraud triggers a positive SAR
    obligation. The findings above may constitute knowledge.
  - Romanian law 295/2004 — working with an unauthorised Romanian
    broker on military goods is an offence in Romania

Recommended Arkmurus action (as of 2026-04-11):
  - NO-GO on any engagement with Serban Industries SRL
  - Preserve all evidence: RFQ PDF, website snapshot (archive.org),
    Zinoz advertisement, email trail
  - Consult MLRO on SAR filing obligation
  - Treat as compliance incident, not commercial negotiation
  - Document in DD record file for 5-year retention under MLR 2017
  - Do not submit SITCL application naming Serban as intermediary
  - Clause Precedent 7 (misrepresentation void clause) in any
    draft agreement gives walk-away right

Keywords for cross-case pattern matching:
  - romanian CUI sequential
  - CUI 51884521
  - founding year misrepresentation
  - residential apartment registered address
  - leetspeak domain
  - ANCEX brokering authorisation
  - CAEN 4619 not a defence licence
  - Zinoz Supply Chain untracked shipments
  - F3 anonymity escrow
  - C4 ML8 Romania intermediary
"""


# =============================================================================
# REGISTRY OF CASES
# =============================================================================

ALL_CASES: dict[str, dict] = {
    "serban_industries_srl": {
        "topic":       "serban industries srl romania no-go 2026-04-11",
        "content":     SERBAN_INDUSTRIES_SRL,
        "source":      "dd_case_library:serban_industries_srl:2026-04-11",
        "confidence":  "CONFIRMED",
        "patterns":    [
            "serban industries", "serban incorporated", "51884521",
            "serban sebastian", "s3rban.com", "str. dridu",
            "zinoz supply chain", "ancex",
        ],
        "risk":        "RED",
    },
}


# =============================================================================
# INGESTION — store every case as a permanent CONFIRMED knowledge fact
# =============================================================================

@fail_wire(module="dd_case_library", gap_type="engine_failure")
async def ingest_all_cases() -> dict:
    """Store every case in the library via knowledge.store_fact.

    Called from main.py _seed_knowledge_bg so the case library is
    ingested once at startup (or on manual /api/aria/knowledge/reseed).
    Each case becomes a permanent CONFIRMED fact; the facts store
    handles dedup so re-running is cheap.

    Also ingests each case into the RAG store under
    source_type='dd_case_library' so semantic search can surface the
    nearest prior case when ARIA reviews a new counterparty with
    similar features (e.g. another Romanian SRL with a residential
    apartment address → Serban pops in the retrieved context).
    """
    from . import knowledge, rag_store

    results: dict = {}
    for case_id, case in ALL_CASES.items():
        try:
            # Permanent knowledge fact
            await knowledge.store_fact(
                topic=case["topic"],
                content=case["content"],
                source=case["source"],
                confidence=case["confidence"],
            )
            # RAG ingest for semantic cross-case retrieval
            await rag_store.ingest_document(
                text=case["content"],
                source=case["source"],
                source_type="dd_case_library",
                title=f"DD Case — {case['topic']}",
                url=f"internal://aria/dd_case_library/{case_id}",
                extra_metadata={
                    "case_id": case_id,
                    "risk": case["risk"],
                    "patterns": ",".join(case["patterns"]),
                    "module": "dd_case_library",
                },
            )
            results[case_id] = {"status": "OK"}
            logger.info("DD case ingested: %s", case_id)
        except Exception as e:
            results[case_id] = {"status": "ERROR", "error": str(e)}
            logger.warning("DD case ingest failed [%s]: %s", case_id, e)

    success = sum(1 for v in results.values() if v.get("status") == "OK")
    logger.info("DD case library ingestion: %d/%d cases", success, len(ALL_CASES))
    # R-F996 — wire to brain
    from .engine_wiring import wire_success, wire_failure
    wire_success(
        module="dd_case_library",
        summary="Ingest All Cases",
        source_id="dd_case_library:R-F996",
    )

    return {
        "cases_ingested": success,
        "total_cases": len(ALL_CASES),
        "detail": results,
    }

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
