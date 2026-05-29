"""R-F1038 — Normalize uppercase ARIA.* logger names to lowercase.

~30 modules use logging.getLogger("ARIA.SomeModule") instead of the
standard "aria.some_module" convention. R-F891 made the error_log_handler
case-insensitive so these logs still reach the ledger, but the uppercase
names are inconsistent with the rest of the codebase and fragile (any new
module using the wrong case silently misses the ledger).

This script performs the mechanical rename. Run it standalone:
  python scripts/rf1038_normalize_loggers.py
"""
from __future__ import annotations

import pathlib

# Map of old uppercase logger names to new lowercase equivalents.
# The suffix matches the module's __file__ stem for discoverability.
REPLACEMENTS: dict[str, str] = {
    "ARIA.ActiveChallenge": "aria.active_challenge_engine",
    "ARIA.CommercialCoherence": "aria.commercial_coherence",
    "ARIA.ComplianceReviewSpecificity": "aria.compliance_review_specificity",
    "ARIA.ClaimLedger": "aria.counterparty_claim_ledger",
    "ARIA.DDCaseLibrary": "aria.dd_case_library",
    "ARIA.DDOrchestrator": "aria.dd_orchestrator",
    "ARIA.DeceptionDetection": "aria.deception_detection",
    "ARIA.DueDiligencePlaybooks": "aria.due_diligence_playbooks",
    "ARIA.GlobalExportControl": "aria.global_export_control",
    "ARIA.GroundTruth": "aria.ground_truth_loop",
    "ARIA.InternationalLaw": "aria.international_law",
    "ARIA.LinkInvestigator": "aria.link_investigator",
    "ARIA.MarketCompetitorKnowledge": "aria.market_competitor_knowledge",
    "ARIA.MemoryRouter": "aria.memory_router",
    "ARIA.NATOStandards": "aria.nato_standards",
    "ARIA.NetworkWalker": "aria.network_walker",
    "ARIA.OSINTKnowledge": "aria.osint_knowledge",
    "ARIA.PersonResolver": "aria.person_resolver",
    "ARIA.ProcurementKnowledge": "aria.procurement_knowledge",
    "ARIA.RegionalCompliance": "aria.regional_compliance",
    "ARIA.RegionalNavigation": "aria.regional_navigation",
    "ARIA.RiskIndices": "aria.risk_indices",
    "ARIA.SecurityProtocol": "aria.security_protocol",
    "ARIA.VerifiedIntel": "aria.verified_intel",
}

FILES = [
    "aria_service/intel/active_challenge_engine.py",
    "aria_service/intel/commercial_coherence.py",
    "aria_service/intel/compliance_review_specificity.py",
    "aria_service/intel/counterparty_claim_ledger.py",
    "aria_service/intel/dd_case_library.py",
    "aria_service/intel/dd_orchestrator.py",
    "aria_service/intel/deception_detection.py",
    "aria_service/intel/due_diligence_playbooks.py",
    "aria_service/intel/global_export_control.py",
    "aria_service/intel/ground_truth_loop.py",
    "aria_service/intel/international_law.py",
    "aria_service/intel/link_investigator.py",
    "aria_service/intel/market_competitor_knowledge.py",
    "aria_service/intel/memory_router.py",
    "aria_service/intel/nato_standards.py",
    "aria_service/intel/network_walker.py",
    "aria_service/intel/osint_knowledge.py",
    "aria_service/intel/person_resolver.py",
    "aria_service/intel/procurement_knowledge.py",
    "aria_service/intel/regional_compliance.py",
    "aria_service/intel/regional_navigation.py",
    "aria_service/intel/risk_indices.py",
    "aria_service/intel/security_protocol.py",
    "aria_service/intel/verified_intel.py",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    count = 0
    for rel in FILES:
        fp = ROOT / rel
        if not fp.exists():
            print(f"SKIP (missing): {rel}")
            continue
        original = fp.read_text(encoding="utf-8")
        content = original
        for old, new in REPLACEMENTS.items():
            content = content.replace(
                f'logging.getLogger("{old}")',
                f'logging.getLogger("{new}")',
            )
        if content != original:
            fp.write_text(content, encoding="utf-8")
            print(f"UPDATED: {rel}")
            count += 1
        else:
            print(f"NO CHANGE: {rel}")
    print(f"\nTotal files updated: {count}")


if __name__ == "__main__":
    main()
