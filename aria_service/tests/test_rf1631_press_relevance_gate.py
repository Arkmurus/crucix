"""R-F1631 — the DD must not ingest search NOISE as 'press coverage'. Search
returns results (SearXNG works) but the digital layer added every hit, so a
gozensecurity DD reported '12 press items, 2-3 relevant' (unrelated academic
papers, NATO Instagram). The entity-relevance gate keeps only hits that
reference the entity's distinctive tokens — accent-normalized, and GRACEFUL
(keeps all when the name is all-generic, so it never drops everything).
"""
from __future__ import annotations

from aria_service.intel.dd_orchestrator import (
    _entity_distinctive_tokens,
    _press_hit_is_relevant,
)


def test_distinctive_tokens_drops_generic_words():
    toks = _entity_distinctive_tokens("Gözen Security Services Inc.")
    assert "gozen" in toks, "the distinctive token must survive (accent-normalized)"
    for generic in ("security", "services", "inc"):
        assert generic not in toks, f"generic '{generic}' must not be a distinctive token"


def test_relevant_hit_kept_noise_dropped():
    toks = _entity_distinctive_tokens("Gözen Security Services Inc.")

    # Real reference to the entity → KEEP (even with accented form in source)
    assert _press_hit_is_relevant(
        "Gözen Güvenlik wins border contract", "Gozen Security ...", "https://x/gozen", toks
    )
    # Accent-only variant in the URL/title still matches the normalized token
    assert _press_hit_is_relevant("GÖZEN profile", "", "https://dev.example/gozen", toks)

    # NOISE that merely contains generic words → DROP
    assert not _press_hit_is_relevant(
        "Airport security study", "A paper on aviation security services", "https://doi.org/10.4324/x", toks
    )
    assert not _press_hit_is_relevant(
        "NATO summit photos", "Instagram post about the alliance", "https://instagram.com/p/abc", toks
    )


def test_all_generic_name_keeps_everything():
    # A name with no distinctive token must NOT cause everything to be dropped.
    toks = _entity_distinctive_tokens("Security Services Ltd")
    assert toks == [], "all-generic name → no distinctive tokens"
    assert _press_hit_is_relevant("anything", "anything", "https://x", toks) is True


def test_multi_name_union_of_tokens():
    # Brandified + raw name both contribute distinctive tokens.
    toks = _entity_distinctive_tokens("modirumgespi.com", "Modirum Gespi Defence")
    assert "modirumgespi" in toks or "modirum" in toks
    assert "gespi" in toks
    assert "defence" not in toks
