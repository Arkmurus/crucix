"""R-F768 — deep_research 0-result cross-tool escalation.

Other-agent audit 2026-05-20 (Pri 4): when deep_research returned 0
results, the chat handler proposed Options A/B/C and waited for the
user to pick. Combined with the Clause-37 permission-ask anti-pattern
(R-F764), this wasted 1-2 turns before any cross-tool work was tried.

R-F768 walks the escalation tree IN-TURN when the variant-augmented
angle search yields 0 snippets:

  Phase (a): native-language web_search via search_multilingual,
             languages derived from R-F767 suggest_jurisdictions
             (Turkish → tr, Cyrillic → bg, Arabic → ar, ...).
  Phase (b): registry-adapter lookup_entity for the first 2
             suggested jurisdictions (MERSIS for TR, etc.), each
             capped at 8s.

Result dict gains:
  escalation_chain: list[str]   tags of phases that fired
  escalation_registry: dict     ISO2 → registry-adapter result

Source-level test verifies the wiring; full behavioural test would
need the full HTTP stack mocked.
"""
from __future__ import annotations

from pathlib import Path


def _researcher_src() -> str:
    p = Path(__file__).resolve().parents[1] / "intel" / "researcher.py"
    return p.read_text(encoding="utf-8")


# ── Source-level contract: escalation logic exists in deep_research ──


def test_rf768_zero_result_branch_exists():
    src = _researcher_src()
    assert "if not all_snippets:" in src, (
        "R-F768 regression: deep_research no longer has a 0-result "
        "escalation branch. Empty-result turns will fall through to "
        "the caller again, restoring the live bug."
    )


def test_rf768_calls_suggest_jurisdictions():
    src = _researcher_src()
    assert "suggest_jurisdictions" in src, (
        "R-F768 regression: deep_research no longer calls "
        "name_variants.suggest_jurisdictions to derive escalation "
        "targets. Without this, native-language search has no "
        "language list."
    )


def test_rf768_iso_to_lang_map_present():
    src = _researcher_src()
    assert '_ISO_TO_LANG' in src, (
        "R-F768 regression: _ISO_TO_LANG map missing. Native-language "
        "escalation can't translate ISO2 codes to search_multilingual "
        "language strings."
    )
    # Minimum coverage: TR and BG must map (the two most-likely
    # transcript-bug origins).
    assert '"TR": "tr"' in src
    assert '"BG": "bg"' in src
    assert '"SA": "ar"' in src
    assert '"AE": "ar"' in src


def test_rf768_phase_a_native_search_wired():
    src = _researcher_src()
    assert "search_multilingual" in src, (
        "R-F768 regression: native-language escalation does not call "
        "web_search.search_multilingual."
    )
    assert 'native_search:' in src, (
        "R-F768 regression: escalation_chain tag 'native_search:<langs>' "
        "not present. Observers can't see which phase fired."
    )


def test_rf768_phase_b_registry_adapter_wired():
    src = _researcher_src()
    assert "registry_adapters" in src, (
        "R-F768 regression: registry-adapter escalation removed. "
        "Turkish-name lookups will not hit MERSIS automatically when "
        "the open-web returns nothing."
    )
    assert "lookup_entity" in src, (
        "R-F768 regression: registry escalation no longer calls "
        "lookup_entity(name, iso2)."
    )
    assert 'registry:' in src, (
        "R-F768 regression: escalation_chain tag 'registry:<iso2>' "
        "not emitted."
    )


def test_rf768_registry_call_count_capped_at_two():
    """Cap the registry escalation to the first 2 jurisdictions to
    keep the 45s overall_budget bounded. Without this cap, a 5-
    jurisdiction suggestion list could burn the entire budget on
    slow adapters."""
    src = _researcher_src()
    assert "_jurisdictions[:2]" in src, (
        "R-F768 regression: registry escalation must be capped at "
        "the first 2 ISO2 jurisdictions to stay within the overall "
        "deep_research budget."
    )


def test_rf768_escalation_chain_in_return_dict():
    src = _researcher_src()
    assert '"escalation_chain": escalation_chain' in src, (
        "R-F768 regression: deep_research return dict no longer "
        "includes escalation_chain. Observers can't see what was "
        "tried after a 0-result first pass."
    )
    assert '"escalation_registry": escalation_registry' in src, (
        "R-F768 regression: deep_research return dict no longer "
        "includes escalation_registry — chat handler can't render "
        "MERSIS directorships without a second tool call."
    )


def test_rf768_budget_guard_present():
    """The escalation must respect overall_budget. Without a budget
    guard, a 0-result query that times out on every escalation phase
    could push deep_research past the 45s wall-clock cap."""
    src = _researcher_src()
    assert "_esc_budget" in src, (
        "R-F768 regression: escalation budget variable removed. "
        "Without it the escalation can blow past the overall_budget."
    )
    assert "if _esc_budget < 3.0:" in src, (
        "R-F768 regression: tiny-budget early-exit removed. "
        "Without it, a near-exhausted budget would still attempt "
        "escalation and likely time out."
    )


def test_rf768_imports_at_function_scope_not_module():
    """The escalation imports must be INSIDE the function (so a
    missing optional dependency doesn't crash module-load) but not
    at module top-level (where they'd be a circular-import risk).
    Verify the imports appear after 'if not all_snippets:'."""
    src = _researcher_src()
    zero_branch_idx = src.find("if not all_snippets:")
    rebuild_idx = src.find("# ── Step 4: rank URLs")
    assert zero_branch_idx > 0 and rebuild_idx > zero_branch_idx
    branch_body = src[zero_branch_idx:rebuild_idx]
    # name_variants import inside the branch
    assert "from .name_variants import suggest_jurisdictions" in branch_body, (
        "R-F768 regression: name_variants import not scoped to the "
        "escalation branch."
    )
    # search_multilingual import inside the branch
    assert "from .web_search import search_multilingual" in branch_body
    # registry_adapters import inside the branch
    assert "from . import registry_adapters" in branch_body


# ── name_variants module precondition (R-F767 dependency) ────────────


def test_rf768_suggest_jurisdictions_imported_under_correct_alias():
    """The escalation imports suggest_jurisdictions via the
    `_r768_juris` alias. If a future edit drops the alias, the
    function name in the call site below must update in lockstep."""
    src = _researcher_src()
    assert "from .name_variants import suggest_jurisdictions as _r768_juris" in src
    assert "_jurisdictions = _r768_juris(entity)" in src
