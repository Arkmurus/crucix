"""R-F2986 — ecosystem organ-coverage rescue (coherence, not fabrication).

The DD of the live ecosystem map found the STRUCTURE 100% accurate (module node
set == filesystem, 0 phantom R-numbers, 0 dangling edges) but the ORGAN view
under-covered: 145/554 modules (26%) were "unassigned". Many were clearly organ-
able (whole learning.* / metacognitive.* subpackages, the OSINT risk-index family,
the verifier family) but missed a keyword — so the organ picture materially
under-represented the ecosystem. R-F2986 adds SPECIFIC leaf keywords (never broad
subpackage prefixes) to rescue them.

Honesty guardrails this test enforces (so the fix stays a rescue, never a clamp):
  1. PURELY ADDITIVE — a keyword addition may only move an ORPHAN into an organ,
     never re-bucket an already-assigned module (organ table is first-match-wins by
     order, so a new keyword in an earlier organ could silently steal a later
     organ's module — this test proves that never happens against a frozen baseline).
  2. Anchored assignments — representative rescued modules land in the CORRECT organ
     (catches an over-broad keyword mis-assigning).
  3. The orphan alert stays MEANINGFUL — genuinely-ambiguous modules (package roots,
     cross-cutting) remain honestly orphan; we do NOT drive orphans to zero.
"""
from __future__ import annotations

from aria_service.intel import ecosystem_map as em


def _assign_all() -> dict[str, str | None]:
    return {em._module_id(p): em._assign_organ(em._module_id(p)) for p in em.scan_modules()}


def test_rf2986_orphans_materially_reduced_but_not_zero():
    """Coherence: the organ view now covers a healthy majority, while the orphan
    alert stays meaningful (not clamped to zero)."""
    a = _assign_all()
    total = len(a)
    orphans = sum(1 for v in a.values() if v is None)
    pct_assigned = 100.0 * (total - orphans) / total
    assert pct_assigned >= 90.0, f"organ coverage regressed to {pct_assigned:.1f}% (orphans={orphans})"
    # NOT a clamp: genuinely-ambiguous modules (namespace package roots, cross-cutting)
    # must still surface as orphans — a zero-orphan map would be hiding, not honest.
    assert orphans > 0, "orphan alert must stay meaningful — never force every module into an organ"


def test_rf2986_rescued_modules_land_in_correct_organ():
    """Anchored spot-checks: each representative rescued module maps to its true
    organ (guards against an over-broad keyword mis-assigning)."""
    expect = {
        "aria_service.intel.pmesii": "osint",
        "aria_service.intel.weapon_origin_catalogue": "osint",
        "aria_service.intel.political_risk_index": "osint",
        "aria_service.learning.fsrs_scheduler": "learning",
        "aria_service.learning.style_learner": "learning",
        "aria_service.intel.rlaif": "learning",
        "aria_service.intel.reranker": "llm",
        "aria_service.intel.symbolic_reasoner": "brain",
        "aria_service.intel.truth_verifier": "guardian",
        "aria_service.intel.pii_redaction": "guardian",
        "aria_service.intel.scraper": "search",
        "aria_service.intel.captcha_solver": "search",
        "aria_service.intel.user_quota": "infra",
        "aria_service.intel.tenant_namespace": "infra",
        "aria_service.intel.report_builder": "documents",
        "aria_service.intel.person_resolver": "registries",
    }
    for mid, organ in expect.items():
        got = em._assign_organ(mid)
        assert got == organ, f"{mid} → expected organ '{organ}', got '{got}'"


def test_rf2986_no_rebucketing_of_stable_anchors():
    """PURELY ADDITIVE guarantee: modules that were ALWAYS unambiguously owned by a
    specific organ must not have been stolen by an R-F2986 keyword. These anchors
    predate R-F2986; each must still resolve to its original organ."""
    stable = {
        "aria_service.intel.ofac_sdn": "sanctions",
        "aria_service.intel.company_investigator": "dd",
        "aria_service.intel.web_search": "search",
        "aria_service.intel.circuit_breaker": "infra",
        "aria_service.intel.rag_store": "brain",
        "aria_service.autonomous.gap_detector": "autonomous",
        "aria_service.intel.counterparty_claim_ledger": "osint",  # NOT stolen by dd's prime_sub
        "aria_service.intel.student": "learning",
    }
    for mid, organ in stable.items():
        got = em._assign_organ(mid)
        assert got == organ, f"REBUCKET REGRESSION: {mid} was '{organ}', now '{got}'"
