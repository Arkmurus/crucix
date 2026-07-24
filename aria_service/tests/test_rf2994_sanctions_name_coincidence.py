"""R-F2994 — suppress name-coincidence transparency/state-ownership "matches".

Live Silverbrook defect: the sanctions screen returned "System Capital Management
Ltd (score 1.00)" and "SCM Holdings (0.78)" against a subject named "Silverbrook
Capital Management" — a wholly unrelated Cyprus energy holding (DTEK/Ukraine).
OpenSanctions' raw score is passed through and never gated by name similarity, so a
coincidental overlap on generic words ("Capital Management") renders as a 1.00
match. The fix drops matches that share only GENERIC corporate words with the
subject from the DISPLAYED transparency set (the blocking path is untouched).
"""
from aria_service.intel.dd_orchestrator import _distinctive_tokens


def test_rf2994_distinctive_tokens_strip_generic_corporate_words():
    assert _distinctive_tokens("Silverbrook Capital Management") == {"silverbrook"}
    assert _distinctive_tokens("System Capital Management Ltd") == {"system"}
    assert _distinctive_tokens("SCM Holdings") == {"scm"}
    # a subject that is ALL generic words has no distinctive token → guard is a no-op
    assert _distinctive_tokens("Capital Management Ltd") == set()


def test_rf2994_filter_drops_system_capital_management_keeps_real_overlap():
    # Replicates the exact predicate used in _run_identity's transparency block.
    subj_disc = _distinctive_tokens("Silverbrook Capital Management")
    per_match = [
        {"name": "System Capital Management Ltd", "score": 1.00, "severity": "info"},
        {"name": "SCM Holdings", "score": 0.78, "severity": "info"},
        {"name": "Silverbrook Nominees Ltd", "score": 0.90, "severity": "info"},
    ]
    coincidences = [
        m for m in per_match
        if subj_disc and not (subj_disc & _distinctive_tokens(m["name"]))
    ]
    dropped = {m["name"] for m in coincidences}
    kept = [m for m in per_match if m not in coincidences]

    assert "System Capital Management Ltd" in dropped   # the false 1.00 is suppressed
    assert "SCM Holdings" in dropped                     # {scm} vs {silverbrook} → coincidence
    assert kept == [{"name": "Silverbrook Nominees Ltd", "score": 0.90, "severity": "info"}]


def test_rf2994_noop_when_subject_all_generic():
    # If the subject has no distinctive token, nothing is dropped (unchanged behaviour).
    subj_disc = _distinctive_tokens("Capital Management Ltd")
    per_match = [{"name": "System Capital Management Ltd", "score": 1.0, "severity": "info"}]
    coincidences = [
        m for m in per_match
        if subj_disc and not (subj_disc & _distinctive_tokens(m["name"]))
    ]
    assert coincidences == []
