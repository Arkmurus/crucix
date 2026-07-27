"""R-F3258 — a non-string entity name must not destroy the whole digital layer.

THE LIVE SYMPTOM (AZURE PARKING LTD DD, 2026-07-27), in the report's own words:

    Gaps: ... deep_research failed: 'list' object has no attribute 'lower'
    Press coverage 2 verified / 3 unverified / 4 own-site
    Adverse media ... Raw, unfiltered search results returned: 0

THE CHAIN, verified by reading not guessing:
  * `dd_orchestrator._run_digital` builds the search subject at line 6313:
        name = report.identity.entity_name or target.get("query", "")
    `target` is the caller-supplied DD request dict and is NEVER type-checked;
    `entity_name: str = ""` is a dataclass ANNOTATION, which Python does not
    enforce at runtime. So `name` can be a list.
  * it is passed straight into `deep_researcher.investigate(llm, name, ...)`
    as `topic`.
  * an AST sweep of `investigate()` finds exactly THREE unguarded `.lower()`
    call sites — 567, 826 and 1246. 826 is safe (every name goes through
    `_sanitize_person_name`, which returns `str | None`) and 567 is safe (a list
    dies earlier on `.strip()`, a different message). Line 1246 is the ONLY
    unguarded `.lower()` that can receive an unvalidated external value:

        ... if topic.lower().split()[0] in h.get("hypothesis", "").lower()

  * `dd_orchestrator.py:6692` then catches the AttributeError and downgrades it
    to a data-gap string, so EVERY article read and fact learned in that layer is
    thrown away — which is why the sweep reported 0 raw results.

These tests drive the real `investigate()` with a list-shaped subject and a
non-empty hypothesis store, which is what makes line 1246 evaluate at all: with
an empty store the generator never runs the comparison and the bug hides.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import deep_researcher as dr


class _FakeLLM:
    is_configured = True

    async def complete(self, *a, **k):
        class _R:
            text = '{"queries": ["acme defence"], "key_findings": ["x"], "risks": []}'
        return _R()


def _harness():
    """Patch the network/LLM edges only — the code under test stays real."""
    async def _search(q, *a, **k):
        return [{"title": "a1", "link": "https://example.com/1"}]

    return [
        patch.object(dr, "_web_search", new=_search),
        patch.object(dr, "_fetch_article_text", new=AsyncMock(return_value="body text " * 40)),
        patch.object(dr, "_analyse_article", new=AsyncMock(return_value={
            "facts": [{"topic": "ownership", "content": "a fact", "confidence": "PROBABLE"}]})),
        patch.object(dr, "_process_analysis", new=AsyncMock(return_value=(1, 0))),
        # NON-EMPTY on purpose: this is what makes line 1246 evaluate.
        patch.object(dr, "_load_hypotheses", new=AsyncMock(return_value=[
            {"hypothesis": "acme has undisclosed defence exposure in angola"}])),
        patch.object(dr, "_save_hypotheses", new=AsyncMock(return_value=None)),
        patch.object(dr, "_get_read_urls", new=AsyncMock(return_value=set())),
        patch.object(dr, "_mark_read", new=AsyncMock(return_value=None)),
        patch.object(dr, "search_knowledge", return_value=""),
    ]


def _run_investigate(topic, **kw):
    async def go():
        ctxs = _harness()
        for c in ctxs:
            c.__enter__()
        try:
            return await dr.investigate(_FakeLLM(), topic, depth="quick",
                                        investigate_people=0, **kw)
        finally:
            for c in reversed(ctxs):
                c.__exit__(None, None, None)
    return asyncio.run(go())


# ── THE CAPABILITY TEST ───────────────────────────────────────────────────────
def test_list_shaped_subject_does_not_destroy_the_research() -> None:
    """The exact live failure: a list subject must not raise out of investigate()."""
    out = _run_investigate(["AZURE PARKING LTD", "azure parking limited"])

    assert isinstance(out, dict), "investigate() must return, not raise"
    assert "error" not in out or out.get("articles_read", 0) > 0
    # the research actually happened rather than being discarded
    assert out.get("articles_read", 0) >= 1, (
        "a non-string subject still cost the layer all of its gathered research"
    )


def test_the_subject_is_normalised_to_a_usable_string() -> None:
    """Coercion must produce something searchable, and say what it did."""
    out = _run_investigate(["AZURE PARKING LTD", "azure parking limited"])

    assert isinstance(out.get("topic"), str), "topic must be normalised to str"
    assert "AZURE PARKING LTD" in out["topic"]
    # never silently: the coercion is disclosed so a malformed caller is visible
    assert out.get("topic_coerced_from"), (
        "a coerced subject must be disclosed, not silently repaired"
    )


def test_a_plain_string_subject_is_untouched() -> None:
    """Regression: the normal path must behave exactly as before."""
    out = _run_investigate("Acme Defence Ltd due diligence")

    assert out["topic"] == "Acme Defence Ltd due diligence"
    assert not out.get("topic_coerced_from"), "a clean string must not be flagged"


def test_an_unusable_subject_is_refused_honestly() -> None:
    """Empty/garbage must return a NAMED error, never a silent empty sweep."""
    for bad in ([], {}, None, 12345, ["", "  "]):
        out = _run_investigate(bad)
        assert isinstance(out, dict)
        assert out.get("error"), f"{bad!r} must produce a named error, got {out!r}"
        assert "subject" in out["error"].lower() or "topic" in out["error"].lower()


# ── the orchestrator side of the same boundary ────────────────────────────────
def test_orchestrator_coerces_the_search_subject() -> None:
    """`name` at dd_orchestrator.py:6313 feeds ~20 downstream uses in _run_digital,
    so it must be a string BEFORE any of them run — not just before investigate()."""
    from aria_service.intel import dd_orchestrator as ddo

    assert hasattr(ddo, "_coerce_entity_text"), "the boundary helper must exist"
    assert ddo._coerce_entity_text(["AZURE PARKING LTD", "x"]) == "AZURE PARKING LTD"
    assert ddo._coerce_entity_text("AZURE PARKING LTD") == "AZURE PARKING LTD"
    assert ddo._coerce_entity_text(None) == ""
    assert ddo._coerce_entity_text([]) == ""
    assert ddo._coerce_entity_text({"name": "x"}) == ""
    assert ddo._coerce_entity_text(["  ", "Second Choice Ltd"]) == "Second Choice Ltd"
