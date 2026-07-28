"""R-F3387 — the citation/grounding verifiers report CLEAN when they crash.

Tranche 2 of the §21 wiring backlog. The wiring audit flagged four
verification-layer modules as having no brain wiring at all; reading them for the
wiring gap found a worse defect underneath it.

  citation_verifier.verify_and_clean   except -> {"clean": True,  "fabricated_removed": 0}
  claim_grounding.ground_claims        except -> {"clean": True,  "ungrounded_sentences": 0}

Both are verifiers. On ANY internal error they answer "yes, this is clean" —
"every citation verifies against the evidence" and "no ungrounded figures" — when
in truth nothing was checked. That is the false-clean class this repo has fought
in sanctions (never-false-clean), in DD reports, and in the C-3 independence gate.

MEASURED BLAST RADIUS (why this is a latent hazard rather than a live incident):
`is_clean()` has no production callers, and every live consumer reads only
`["answer"]` or a count. So today the user-visible effect of a crash is that
fabricated citations pass through UNSTRIPPED — on the main chat path
(aria_engine.py:2109 and :5449), the live route (routes/aria.py:3215), DD reports
(report_builder.py:503) and model_router.py:509 — with no log, no signal and no
ledger entry. A permanently broken verifier is indistinguishable from a clean
answer. That is exactly the hole §21a's failure branch exists to close, and the
`clean: True` is the same defect one caller away from being live.

corroboration.corroborate is the counter-example and is left as it is: it already
fails CLOSED ("unparseable => fail closed, never guess", "never invent
corroboration"). Its only gap is that the failure goes to logger.warning, which
§21a classifies as DARK — so it gets the signal, not a semantic change.

FAILS BEFORE R-F3387: no module imports wire_success/wire_failure, and both
verifiers return clean=True from their exception path.
"""
from __future__ import annotations

from unittest.mock import patch

from aria_service.intel import citation_verifier, claim_grounding, corroboration


# ── citation_verifier ───────────────────────────────────────────────────────
def test_rf3387_citation_verifier_does_not_report_clean_when_it_crashes():
    """THE DEFECT. A verifier that fell over has not verified anything."""
    with patch.object(citation_verifier.gr, "extract_context_sources", side_effect=RuntimeError("boom")), \
         patch.object(citation_verifier, "wire_failure") as wf:
        res = citation_verifier.verify_and_clean("answer [1]", "context")
    assert res["clean"] is False, (
        "a crashed citation verifier reported the answer CLEAN — it verified nothing"
    )
    assert wf.called, "the failure branch must reach the brain (§21a)"


def test_rf3387_citation_verifier_still_never_raises_and_returns_the_answer():
    """The degrade contract is load-bearing: a broken verifier must not break the
    answer path. Only the CLAIM changes, not the resilience."""
    with patch.object(citation_verifier.gr, "extract_context_sources", side_effect=RuntimeError("boom")), \
         patch.object(citation_verifier, "wire_failure"):
        res = citation_verifier.verify_and_clean("answer [1]", "context")
    assert res["answer"] == "answer [1]", "the caller must still get its text back"
    assert res["fabricated_removed"] == 0


def test_rf3387_citation_verifier_wires_success_on_a_real_run():
    with patch.object(citation_verifier, "wire_success") as ws, \
         patch.object(citation_verifier, "wire_failure") as wf:
        res = citation_verifier.verify_and_clean("plain answer", "context")
    assert isinstance(res.get("clean"), bool)
    assert ws.called, "a completed verification must reach the brain"
    assert not wf.called


# ── claim_grounding ─────────────────────────────────────────────────────────
def test_rf3387_claim_grounding_does_not_report_clean_when_it_crashes():
    with patch.object(claim_grounding, "_sentence_grounded", side_effect=RuntimeError("boom")), \
         patch.object(claim_grounding, "wire_failure") as wf:
        res = claim_grounding.ground_claims("The figure is 42.", "context")
    assert res["clean"] is False, (
        "a crashed grounding check reported NO ungrounded figures — it checked nothing"
    )
    assert wf.called


def test_rf3387_claim_grounding_still_returns_the_answer_unchanged_on_failure():
    with patch.object(claim_grounding, "_sentence_grounded", side_effect=RuntimeError("boom")), \
         patch.object(claim_grounding, "wire_failure"):
        res = claim_grounding.ground_claims("The figure is 42.", "context")
    assert res["answer"] == "The figure is 42."
    assert res["ungrounded_sentences"] == 0


def test_rf3387_claim_grounding_wires_success_on_a_real_run():
    with patch.object(claim_grounding, "wire_success") as ws, \
         patch.object(claim_grounding, "wire_failure") as wf:
        claim_grounding.ground_claims("A plain sentence.", "context")
    assert ws.called
    assert not wf.called


# ── corroboration — signal only, semantics unchanged ────────────────────────
def test_rf3387_corroboration_reports_its_failure_instead_of_only_logging():
    """It already fails closed correctly. The gap is that the failure was DARK:
    a logger.warning is not a brain sink (§21a)."""
    class _Boom:
        def get_independent_count(self, records):
            raise RuntimeError("boom")

    # corroborate() builds its own SourceIndependenceChecker(), so that is what
    # must be replaced. The first draft patched a `_checker` attribute that does
    # not exist (create=True happily invented it), never reached the failure
    # path, and then hedged the assertion with `if wf.called: ... or True` — a
    # tautology that could not fail. A test that cannot fail is worth less than
    # no test, which is the lesson this whole batch keeps re-teaching.
    # _entity_key reads `entities` as a DICT of countries/oems/products, and
    # _similar needs >= _MIN_TOKENS (3) title tokens to judge. A one-token title
    # with a LIST of entities is filtered out long before the failure path — which
    # is why the first fixture never reached it and the assertion had to be
    # hedged. Drive the real clustering contract instead.
    sig = {"title": "Estonia signs radar deal with vendor",
           "url": "https://a.example/x",
           "entities": {"countries": ["Estonia"], "products": ["radar"]},
           "published": "2026-07-01T00:00:00+00:00"}
    with patch.object(corroboration, "wire_failure") as wf, \
         patch.object(corroboration, "SourceIndependenceChecker", lambda *a, **k: _Boom()):
        try:
            out = corroboration.corroborate([dict(sig), dict(sig, url="https://b.example/y")])
        except Exception as exc:  # pragma: no cover
            raise AssertionError(f"corroborate must never raise: {exc}")

    assert isinstance(out, list), "corroborate must still return a list"
    assert wf.called, (
        "the independence-count failure must reach the brain, not only logger.warning"
    )
    assert "independence count failed" in str(wf.call_args).lower()
    # Semantics unchanged: failing closed means NOTHING gets marked corroborated.
    assert not any(s.get("corroboration") == "corroborated" for s in out), (
        "a failed independence count must never invent corroboration"
    )


def test_rf3387_corroboration_wires_success_on_a_completed_batch():
    # corroborate([]) returns early BY DESIGN (nothing to corroborate), so an
    # empty batch would prove nothing — drive a real signal through instead.
    # _entity_key reads `entities` as a DICT of countries/oems/products, and
    # _similar needs >= _MIN_TOKENS (3) title tokens to judge. A one-token title
    # with a LIST of entities is filtered out long before the failure path — which
    # is why the first fixture never reached it and the assertion had to be
    # hedged. Drive the real clustering contract instead.
    sig = {"title": "Estonia signs radar deal with vendor",
           "url": "https://a.example/x",
           "entities": {"countries": ["Estonia"], "products": ["radar"]},
           "published": "2026-07-01T00:00:00+00:00"}
    with patch.object(corroboration, "wire_success") as ws:
        out = corroboration.corroborate([dict(sig)])
    assert isinstance(out, list)
    assert ws.called, "a completed corroboration pass must reach the brain"


# ── the gate must agree ─────────────────────────────────────────────────────
def test_rf3387_the_wiring_audit_no_longer_flags_these_three():
    import pathlib
    import sys
    root = pathlib.Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "scripts"))
    from pre_commit_checks import check_wiring_present

    for name in ("citation_verifier.py", "claim_grounding.py", "corroboration.py"):
        issues = check_wiring_present([root / "aria_service" / "intel" / name])
        assert issues == [], f"{name} is still flagged: {issues}"
