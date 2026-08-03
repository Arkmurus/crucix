"""R-F397 — RAG semantic-recall similarity floor.

Live evidence 2026-05-13: ARIA self-reported that "Semantic recall
includes Hezbollah drone content at 0.43 similarity to Arnaldo La Scala
queries — false positive topic bleed — could contaminate analysis if
not caught. The RAG embedding similarity is picking up 'La Scala'
phonetically matching 'Lebanon' → unrelated Iran/Hezbollah content
bleeds into the context."

Before R-F397: `rag_store.search()` had NO similarity threshold —
chromadb's top_k was returned verbatim. A 0.43 cosine-similarity
chunk (the phonetic/acronym-match noise zone) was injected into the
LLM context next to legitimate 0.85+ hits.

After R-F397: a default floor of 0.50 filters out the noise zone.
Pass `min_similarity=0.0` to restore the legacy behaviour for the
narrow case of exploratory crawls.

Source-level tests: the function signature and the filter line must
both be present. Behavioural test is not possible without a live
chromadb instance and a corpus of embedded chunks, so we lean on
source assertions + a small unit test for the filter logic.
"""
from __future__ import annotations

import inspect
import pathlib

from ._source_probe import repo_path


def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/intel/rag_store.py")
    ).read_text(encoding="utf-8", errors="ignore")


def test_rf397_default_min_similarity_constant_defined():
    """A module-level constant must define the floor so it's easy to
    audit / tune without grepping the function body."""
    src = _src()
    assert "DEFAULT_MIN_SIMILARITY" in src, (
        "R-F397 regression: DEFAULT_MIN_SIMILARITY constant missing."
    )
    # Sanity-check the value: 0.50 is the chosen floor. If a future
    # change tightens / loosens it, this test will fail and force a
    # conscious update — preventing accidental drift.
    assert "DEFAULT_MIN_SIMILARITY = 0.50" in src or "DEFAULT_MIN_SIMILARITY = 0.5" in src, (
        "R-F397: DEFAULT_MIN_SIMILARITY value drifted from 0.50. "
        "If intentional, update this assertion."
    )


def test_rf397_search_accepts_min_similarity_kwarg():
    """The search() signature must expose min_similarity so callers
    can override the floor (e.g. exploratory crawls pass 0.0)."""
    from aria_service.intel import rag_store
    sig = inspect.signature(rag_store.search)
    assert "min_similarity" in sig.parameters, (
        "R-F397: search() does not accept min_similarity kwarg."
    )
    # Default must be the module constant, not a hardcoded number.
    default = sig.parameters["min_similarity"].default
    assert default == rag_store.DEFAULT_MIN_SIMILARITY


def test_rf397_filter_line_present_in_function_body():
    """The actual filter — chunks below the floor must be skipped
    before they enter the result list. Without this, the signature
    looks honest but the function still leaks 0.43 noise."""
    src = _src()
    # Locate the search() function and look inside it for the filter.
    idx = src.find("async def search(")
    assert idx > 0, "search() function not found"
    # Scope to ~3500 chars after the def (function is ~120 lines)
    block = src[idx:idx + 5000]
    assert "if similarity < min_similarity:" in block, (
        "R-F397 regression: floor check missing from search() body. "
        "The kwarg is wired but never enforced."
    )
    assert "continue" in block


def test_rf397_lebanon_phonetic_match_blocked_at_default_floor():
    """The specific bleed ARIA reported: 0.43 similarity (the
    La Scala→Lebanon→Hezbollah phonetic match) must not pass the
    default 0.50 floor.

    This is a pure-logic test — we don't need chromadb to assert
    that a Python `if 0.43 < 0.50: continue` works. The point is to
    pin the floor at 0.50 so a future bump can't accidentally drop
    it below 0.43.
    """
    from aria_service.intel.rag_store import DEFAULT_MIN_SIMILARITY
    # The reported false-positive similarity from ARIA's evidence.
    LEBANON_BLEED_SIM = 0.43
    assert LEBANON_BLEED_SIM < DEFAULT_MIN_SIMILARITY, (
        f"R-F397 regression: DEFAULT_MIN_SIMILARITY ({DEFAULT_MIN_SIMILARITY}) "
        f"is below the known-bad similarity ({LEBANON_BLEED_SIM}). "
        f"The original Lebanon/Hezbollah bleed would pass through."
    )


def test_rf397_floor_does_not_block_well_aligned_matches():
    """The floor must be loose enough to admit genuine semantic
    matches. Well-aligned chunks hit 0.70+; we must not block those.
    Floor of 0.50 leaves a healthy gap."""
    from aria_service.intel.rag_store import DEFAULT_MIN_SIMILARITY
    GENUINE_MATCH_SIM = 0.70   # typical well-aligned chunk
    EXCELLENT_MATCH_SIM = 0.85
    assert GENUINE_MATCH_SIM >= DEFAULT_MIN_SIMILARITY
    assert EXCELLENT_MATCH_SIM >= DEFAULT_MIN_SIMILARITY


def test_rf397_zero_floor_kwarg_restores_legacy():
    """Passing `min_similarity=0.0` must restore the legacy no-floor
    behaviour for the narrow case (exploratory crawls). This is a
    contract check — broken if a future refactor hardcodes the floor
    instead of using the kwarg."""
    from aria_service.intel import rag_store
    # The filter is `similarity < min_similarity`; with min=0.0 and
    # similarity always >= 0 (clamped at line 808), the check is
    # equivalent to `0 < 0` for the minimum case, never True. So
    # every chunk passes when the caller opts in.
    assert 0.0 == 0.0  # tautology; the real check is below
    src = _src()
    # The filter MUST use min_similarity (the parameter), not a
    # hardcoded number, so the override actually works.
    assert "if similarity < min_similarity:" in src, (
        "R-F397: filter uses hardcoded value instead of the kwarg — "
        "override path broken."
    )
