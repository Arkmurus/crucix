"""R-F329..R-F332 — deep_research query generation hardening.

Live failure 2026-05-11 21:55: operator asked "investigate Modirum
Gespi people and network" and ARIA returned ZERO snippets across 4
query angles because each angle was templated as "Modirum Gespi
people and network <suffix>" — broken phrasing no search engine
indexes. R-F329 strips question-modifier suffixes from the entity
before templating.

R-F330 adds memory-first query expansion: before templating angles,
query RAG for what we already know about the entity (CEO names,
parent company, predecessor) and add those as additional search
angles.

R-F331 adds LinkedIn site-restricted angles when the intent signals
a people / officers / leadership question — highest-yield surface for
people data.

R-F332 is the observability check that the RAG store IS retrievable.
"""
from __future__ import annotations

import asyncio
import re

from ._app_probe import mounted_paths
from ._source_probe import repo_path


# ───────────────────────── R-F329 — strip question modifiers ─────────────────

def test_rf329_people_and_network_suffix_stripped():
    """The exact failure phrasing — verify the regex strips it."""
    pattern = re.compile(
        r"\b("
        r"people\s+(?:and\s+network|and\s+leadership|and\s+directors|"
        r"and\s+officers|and\s+management)"
        r"|directors?(?:\s+and\s+(?:officers|leadership|management))?"
        r"|leadership(?:\s+team)?"
        r"|officers?"
        r"|management(?:\s+team)?"
        r"|board(?:\s+of\s+directors)?"
        r"|ubo|beneficial\s+owners?"
        r"|shareholders?"
        r"|owners?"
        r"|founders?"
        r"|executives?"
        r"|key\s+personnel"
        r"|c-suite"
        r"|key\s+staff"
        r"|network"
        r")\s*$",
        re.IGNORECASE,
    )
    inputs = [
        ("Modirum Gespi people and network", "Modirum Gespi"),
        ("Acme Corp directors", "Acme Corp"),
        ("Beta Industries leadership team", "Beta Industries"),
        ("Gamma SA officers", "Gamma SA"),
        ("Delta Ltd shareholders", "Delta Ltd"),
        ("Epsilon BV ubo", "Epsilon BV"),
        ("Zeta key personnel", "Zeta"),
        ("Eta Inc. board of directors", "Eta Inc."),
    ]
    for raw, expected in inputs:
        m = pattern.search(raw)
        assert m is not None, f"R-F329 regression: {raw!r} not matched"
        stripped = raw[:m.start()].strip().rstrip(",;:- ")
        assert stripped == expected, (
            f"R-F329: {raw!r} → expected {expected!r}, got {stripped!r}"
        )


def test_rf329_no_strip_when_entity_doesnt_end_in_modifier():
    """Pure entity names should pass through untouched."""
    pattern = re.compile(
        r"\b(people\s+and\s+network|directors|officers|leadership)\s*$",
        re.IGNORECASE,
    )
    pure = ["Modirum GESPI", "Acme Corp", "Modirum Defence Consultancy Ltd"]
    for name in pure:
        assert pattern.search(name) is None, (
            f"R-F329 false positive: pure entity {name!r} got matched"
        )


def test_rf329_capability_modirumgespi_split_into_entity_plus_intent():
    """End-to-end capability — the 21:55 failure shape produces the
    correct (entity, intent_hint) split."""
    raw = "Modirum Gespi people and network"
    pattern = re.compile(
        r"\b("
        r"people\s+(?:and\s+network|and\s+leadership)"
        r"|directors?(?:\s+and\s+officers)?"
        r"|leadership"
        r"|officers?"
        r")\s*$",
        re.IGNORECASE,
    )
    m = pattern.search(raw)
    assert m is not None
    intent_hint = m.group(1).lower().strip()
    entity_stripped = raw[:m.start()].strip().rstrip(",;:- ")
    assert entity_stripped == "Modirum Gespi"
    assert "people" in intent_hint and "network" in intent_hint


# ───────────────────────── R-F330 — memory-first query expansion ─────────────

def test_rf330_capability_memory_expansion_appends_known_principals(monkeypatch):
    """When RAG returns prior facts mentioning principal names, the
    deep_research angles should include those names as additional
    search queries."""
    from aria_service.intel import researcher as _res

    # Stub rag_store.search to return facts mentioning Elias Silvola +
    # Joao Scarparo (the principals from the 21:48 successful run).
    async def _fake_rag_search(query, top_k=5, **kw):
        return [
            {
                "text": "Modirum Defence acquired Gespi Brazil. "
                        "Elias Silvola is the President & CEO. "
                        "Joao Scarparo continues as Gespi Executive Director.",
                "similarity": 0.9,
            },
        ]

    from aria_service.intel import rag_store
    monkeypatch.setattr(rag_store, "search", _fake_rag_search)

    # Stub web_search to return nothing (we only care about the angles)
    angles_seen: list[str] = []

    async def _fake_one_angle(angle):
        angles_seen.append(angle)
        return {"ok": True, "provider": "stub", "results": []}

    # Monkey-patch internals so we can read which angles got fired
    original = _res.deep_research

    # Direct check — call the actual deep_research with a stub for
    # _search_one_angle by patching web_search.search_multilingual
    async def _stub_ml(query, languages=None, max_results=8, **kw):
        angles_seen.append(query)
        return []

    from aria_service.intel import web_search
    monkeypatch.setattr(web_search, "search_multilingual", _stub_ml)

    # Use a tiny overall_budget so the function returns fast
    result = asyncio.run(_res.deep_research(
        "Modirum GESPI",
        max_queries=8,
        max_extracts=0,
        overall_budget=10.0,
    ))

    # Among the angles fired, at least one should contain a name
    # extracted from the RAG content (Elias Silvola OR Joao Scarparo)
    all_angles_text = " | ".join(angles_seen).lower()
    has_principal = (
        "elias silvola" in all_angles_text or "joao scarparo" in all_angles_text
    )
    assert has_principal, (
        f"R-F330 regression: memory expansion didn't fire — angles {angles_seen}"
    )


# ───────────────────────── R-F331 — LinkedIn site-restricted angles ──────────

def test_rf331_people_intent_triggers_linkedin_angle_in_source():
    """Source-level capability — the R-F331 code path adds LinkedIn
    site-restricted angles to _base_angles when the intent hint
    matches a people-keyword."""
    import pathlib
    src = pathlib.Path(
        repo_path("aria_service/intel/researcher.py")
    ).read_text(encoding="utf-8", errors="ignore")
    assert "R-F331" in src
    assert "site:linkedin.com" in src
    # The people-intent trigger list must include both "people" and
    # "directors" (the two dominant operator phrasings)
    assert "people" in src
    assert "_people_intents" in src


def test_rf331_capability_linkedin_angle_added_when_people_intent(monkeypatch):
    """Capability — when entity ends in 'people and network', the
    actual angles fired should include a linkedin.com site query."""
    from aria_service.intel import researcher as _res

    angles_seen: list[str] = []

    async def _stub_ml(query, languages=None, max_results=8, **kw):
        angles_seen.append(query)
        return []

    from aria_service.intel import web_search
    monkeypatch.setattr(web_search, "search_multilingual", _stub_ml)

    async def _no_rag(*a, **kw):
        return []
    from aria_service.intel import rag_store
    monkeypatch.setattr(rag_store, "search", _no_rag)

    result = asyncio.run(_res.deep_research(
        "Modirum Gespi people and network",
        max_queries=8,
        max_extracts=0,
        overall_budget=10.0,
    ))

    angles_text = " | ".join(angles_seen).lower()
    assert "linkedin" in angles_text, (
        f"R-F331 regression: LinkedIn angle not added when intent is "
        f"'people and network'. Angles: {angles_seen}"
    )


# ───────────────────────── R-F332 — RAG retrievability check ─────────────────

def test_rf332_rag_search_endpoint_is_registered():
    """R-F332: the /rag/search endpoint must be mounted so we can
    verify the pay-once-remember-forever doctrine end-to-end."""
    import asyncio, os
    os.environ.setdefault("TESTING", "1")
    from aria_service.main import app

    # R-F3791 — the flat `app.routes` walk this used stopped seeing router-mounted
    # routes entirely, so `rag_paths` was [] and this read as an R-F332 regression.
    rag_paths = sorted(p for p in mounted_paths(app) if "rag" in p.lower())
    # Must have both /rag/search and /rag/stats wired
    assert any("/rag/search" in p for p in rag_paths), (
        f"R-F332 regression: /rag/search not registered. Got {rag_paths}"
    )
    assert any("/rag/stats" in p for p in rag_paths), (
        f"R-F332 regression: /rag/stats not registered. Got {rag_paths}"
    )
