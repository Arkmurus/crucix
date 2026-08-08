"""R-F451 (2026-05-13) — capability tests for cited_artifact_verifier
wired into dd_orchestrator Layer 5b.

DD audit on 2026-05-13 confirmed the verifier was dead code:
tests in test_nak_learnings.py exercised the module in isolation but
no production call site existed. R-F451 wires it into Layer 5b
(deception scoring) of `aria_service/intel/dd_orchestrator.py`.

These tests prove:
  1. The verifier behaves correctly on fabricated IDs (extends the
     existing unit coverage in test_nak_learnings.py with sharper
     deception-context assertions).
  2. dd_orchestrator imports + calls the verifier at the documented
     call site (Layer 5b.2 — between deception_detection and Layer 8).
  3. The return shape matches what the wiring code consumes.
"""
from __future__ import annotations

import re
import sys

import pytest

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── Test 1: verifier flags fabricated IDs with deception summary ────────


def test_fabricated_ncnda_id_flagged_as_unverified():
    """An NCNDA-* ID that has no record anywhere should come back
    unverified. This is the SERBAN-letter incident pattern."""
    from aria_service.intel import cited_artifact_verifier as cav

    text = (
        "Per our ongoing structured process under NCNDA-KOR-MCT-0126, "
        "I am awaiting your counter-signature on the related LOI-2024-0917 "
        "before circulation."
    )

    result = cav.verify_cited_artifacts(text, counterparty="FakeCounterparty")

    assert result["unverified"] >= 2, (
        f"Expected at least 2 unverified IDs (NCNDA + LOI), got "
        f"{result['unverified']}. cited={result.get('cited')}"
    )
    # Summary must clearly call out deception risk
    summary = result.get("summary") or ""
    assert "deception" in summary.lower() or "fabricat" in summary.lower(), (
        f"Summary should flag deception/fabrication risk for fully-unverified "
        f"citations. Got: {summary!r}"
    )
    # Verify the shape my dd_orchestrator wiring relies on
    cited = result["cited"]
    assert isinstance(cited, list)
    for entry in cited:
        assert "doc_id" in entry
        assert "verified" in entry
        assert "doc_type" in entry


# ── Test 2: no doc IDs → no false positives ─────────────────────────────


def test_no_doc_ids_returns_clean_summary():
    """Normal prose with no document references must NOT trigger
    deception escalation. False-positives here would tag every benign
    chat as suspicious."""
    from aria_service.intel import cited_artifact_verifier as cav

    text = "We discussed pricing yesterday and the quote looked reasonable."
    result = cav.verify_cited_artifacts(text)

    assert result["unverified"] == 0
    assert result["verified"] == 0
    # The dd_orchestrator wiring branch is `if _unv or _ver:` — neither
    # holds here, so the wiring will NOT attach cited_artifacts to
    # report.deception (correct — there are no claims to flag).


# ── Test 3: dd_orchestrator imports the verifier at the wired call site ─


def test_dd_orchestrator_wires_cited_artifact_verifier():
    """Find the R-F451 wiring block in dd_orchestrator.py and confirm
    it actually imports cited_artifact_verifier. This is a structural
    guard against the wiring being silently removed by a future merge."""
    from aria_service.intel import dd_orchestrator
    import inspect

    source = module_source(dd_orchestrator)

    # The R-F451 marker must be present
    assert "R-F451" in source, (
        "dd_orchestrator.py is missing the R-F451 wiring block — "
        "cited_artifact_verifier has been disconnected again"
    )
    # The import must be present
    assert (
        "from . import cited_artifact_verifier" in source
        or "from .cited_artifact_verifier" in source
    ), "dd_orchestrator must import cited_artifact_verifier"
    # The verifier's main entry point must be called
    assert "verify_cited_artifacts(" in source, (
        "dd_orchestrator must call cited_artifact_verifier.verify_cited_artifacts"
    )
    # The R-F451 block must live BETWEEN Layer 5b deception and Layer 8
    # counter-intel (the documented call site). Use the explicit "LAYER 8"
    # marker for the lower bound.
    rf450_pos = source.find("R-F451")
    layer_8_pos = source.find("LAYER 8: COUNTER-INTELLIGENCE")
    layer_5b_pos = source.find("LAYER 5b: DECEPTION SCORING")
    assert layer_5b_pos < rf450_pos < layer_8_pos, (
        f"R-F451 block is in the wrong place. 5b={layer_5b_pos} "
        f"R-F451={rf450_pos} 8={layer_8_pos}"
    )


# ── Test 4: return-shape contract that dd_orchestrator wiring relies on ─


def test_verifier_return_shape_matches_wiring_contract():
    """The dd_orchestrator wiring (R-F451) reads:
        result.get('unverified')
        result.get('verified')
        result.get('summary')
        result.get('cited')              # list of dicts
        a.get('doc_id') for a in cited   # for the log line
        a.get('verified') for a in cited # for the unverified filter

    If a refactor of cited_artifact_verifier changes the return shape,
    the wiring will silently degrade. This test pins the contract."""
    from aria_service.intel import cited_artifact_verifier as cav

    result = cav.verify_cited_artifacts(
        "Reference NCNDA-TEST-001 and PO-EXAMPLE-2024",
        counterparty="ContractTest",
    )

    # Top-level keys
    assert "unverified" in result and isinstance(result["unverified"], int)
    assert "verified" in result and isinstance(result["verified"], int)
    assert "summary" in result and isinstance(result["summary"], str)
    assert "cited" in result and isinstance(result["cited"], list)

    # Per-cited shape
    for entry in result["cited"]:
        assert isinstance(entry, dict)
        assert "doc_id" in entry
        assert "verified" in entry
        assert isinstance(entry["verified"], bool)


# ── Test 5: brain_hook still lists cited_artifact_verifier ──────────────


def test_brain_hook_keeps_cited_artifact_listing():
    """The DD audit flagged brain_hook.py:117 + :320 as the only places
    cited_artifact_verifier was referenced before R-F451. The wiring
    doesn't remove brain_hook's listing — both should coexist now."""
    from aria_service.intel import brain_hook
    import inspect

    source = module_source(brain_hook)
    assert "cited_artifact_verifier" in source, (
        "brain_hook.py should still list cited_artifact_verifier "
        "(it gets a topic-tag weight there)"
    )
