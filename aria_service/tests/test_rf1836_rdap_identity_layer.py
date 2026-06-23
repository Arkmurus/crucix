"""R-F1836 — RDAP domain-ownership check moved to Layer 1 (identity).

Why this move is a fix, not a cosmetic relocation: the DD layers run
sequentially and the DIGITAL layer (where the RDAP check used to live, section
5e-bis) is skipped entirely in `quick` mode and whenever the identity layer
hard-stops. So the check — whose own comment said it was "worth having in quick
mode too" — never actually ran in those paths. Identity (Layer 1) always runs
and runs before the hard-stop short-circuit, so the signal now fires in every
mode and is surfaced as the identity signal it is.

These tests drive `_check_domain_ownership` (the exact coroutine the orchestrator
awaits) and assert the user-visible outcome: the finding lands in the IDENTITY
section. The AST guard proves the structural move — wired into both always-run
identity paths and removed from the skipped digital path — without needing the
live sanctions/network the full identity layer requires.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from unittest.mock import patch

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel.dd_schema import ARKDDReport


def _run(coro):
    return asyncio.run(coro)


def _mismatch_result():
    return {
        "verified": True,
        "domain": "f3ir.com",
        "flags": ["REGISTRANT_ENTITY_MISMATCH"],
        "registrar": "GoDaddy",
        "creation_date": "2024-01-01",
        "age_days": 400,
        "signals": ["registrant 'Acme Holdings' != claimed 'F3 International'"],
    }


def _clean_result():
    return {
        "verified": True,
        "domain": "example.com",
        "flags": [],
        "registrar": "MarkMonitor",
        "creation_date": "1995-08-14",
        "age_days": 11000,
        "signals": [],
    }


# ── The moved unit: writes to the IDENTITY section ────────────────────────────

def test_mismatch_lands_in_identity_as_red():
    report = ARKDDReport()
    report.identity.entity_name = "F3 International Resources"
    target = {"name": "F3 International Resources", "website": "https://f3ir.com"}
    with patch.object(ddo, "Finding", ddo.Finding), \
         patch("aria_service.intel.domain_ownership_verifier.verify_domain",
               return_value=_mismatch_result()):
        _run(ddo._check_domain_ownership(target, report))
    titles = [f.title for f in report.identity.findings]
    assert any("Domain ownership (RDAP)" in t for t in titles)
    rdap = [f for f in report.identity.findings if "Domain ownership (RDAP)" in f.title][0]
    assert rdap.severity == "red"               # mismatch is escalated to red
    assert report.digital.findings == []         # NOT in the digital section anymore


def test_clean_domain_is_info_in_identity():
    report = ARKDDReport()
    report.identity.entity_name = "Example Corp"
    target = {"name": "Example Corp", "domain": "example.com"}
    with patch("aria_service.intel.domain_ownership_verifier.verify_domain",
               return_value=_clean_result()):
        _run(ddo._check_domain_ownership(target, report))
    rdap = [f for f in report.identity.findings if "Domain ownership (RDAP)" in f.title]
    assert len(rdap) == 1
    assert rdap[0].severity == "info"


def test_no_domain_is_noop():
    report = ARKDDReport()
    report.identity.entity_name = "No Website Ltd"
    target = {"name": "No Website Ltd"}  # no url/domain/website, name not URL-like
    # verify_domain must NOT even be called.
    with patch("aria_service.intel.domain_ownership_verifier.verify_domain",
               side_effect=AssertionError("should not be called when no domain")):
        _run(ddo._check_domain_ownership(target, report))
    assert report.identity.findings == []


def test_bare_url_target_is_picked_up():
    """Operator typed 'run dd on https://f3ir.com/' → URL is the target name."""
    report = ARKDDReport()
    report.identity.entity_name = ""
    target = {"name": "https://f3ir.com/"}
    with patch("aria_service.intel.domain_ownership_verifier.verify_domain",
               return_value=_mismatch_result()) as vd:
        _run(ddo._check_domain_ownership(target, report))
    assert vd.called
    assert any("Domain ownership (RDAP)" in f.title for f in report.identity.findings)


def test_inconclusive_becomes_identity_data_gap():
    report = ARKDDReport()
    report.identity.entity_name = "Foo"
    target = {"name": "Foo", "website": "foo.com"}
    with patch("aria_service.intel.domain_ownership_verifier.verify_domain",
               return_value={"verified": False, "domain": "foo.com", "reason": "RDAP timeout"}):
        _run(ddo._check_domain_ownership(target, report))
    assert any("RDAP check inconclusive" in g for g in report.identity.data_gaps)
    assert report.identity.findings == []


# ── Structural move guard (AST) ───────────────────────────────────────────────

def _func_calls_check(func_name: str) -> bool:
    src = Path(ddo.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id == "_check_domain_ownership"):
                    return True
    return False


def test_wired_into_both_identity_paths():
    """The always-run identity layer (company + person) must call the helper —
    that is what makes the check run in quick mode / before a hard-stop."""
    assert _func_calls_check("_run_identity")
    assert _func_calls_check("_run_identity_person")


def test_removed_from_digital_layer():
    """The skipped-in-quick-mode digital layer must no longer do the RDAP check
    (else the move would be a duplicate, not a move)."""
    src = Path(ddo.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_digital":
            body_src = ast.get_source_segment(src, node) or ""
            assert "verify_domain" not in body_src
            assert "domain_ownership_verifier" not in body_src
            return
    raise AssertionError("_run_digital not found")
