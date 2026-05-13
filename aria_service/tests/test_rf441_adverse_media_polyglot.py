"""R-F441 — source-language adverse-media probes.

The 11-language fanout (web_explorer._LANG_FANOUT_TRIGGERS) already
detects WHICH languages are relevant for a query, but passes a
GENERIC search string. R-F441 builds per-language adverse-media
queries (corruption/sanctions/fraud/money-laundering/etc in the
native script) so the search probes the untranslated reporting
where the real evidence lives.

Module: adverse_media_polyglot.py
Wire-in: dd_orchestrator Digital layer after R-F440 (surfaces the
per-DD polyglot plan on web_footprint; doesn't execute searches
inline — that's the autonomous researcher's loop).
"""
from __future__ import annotations


# ───────────────────────── Term coverage ────────────────────────────────────


def test_rf441_supported_languages_includes_11_target_langs():
    """Confirm coverage spans the 11 languages ARIA's fanout targets.
    Catches accidental drift if a future PR removes a language."""
    from aria_service.intel.adverse_media_polyglot import supported_languages
    langs = set(supported_languages())
    expected = {"ru", "zh", "ar", "fa", "pt", "es", "fr", "de", "it", "tr", "uk"}
    assert expected.issubset(langs), (
        f"R-F441 coverage regression: missing langs {expected - langs}"
    )


def test_rf441_each_lang_has_at_least_six_adverse_terms():
    """A list of 1-2 terms isn't useful — adverse-media coverage needs
    breadth. Each language should ship ≥6 terms covering corruption,
    sanctions, fraud, money laundering, bribery, embezzlement."""
    from aria_service.intel.adverse_media_polyglot import ADVERSE_TERMS
    for lang, terms in ADVERSE_TERMS.items():
        assert len(terms) >= 6, (
            f"R-F441: lang {lang!r} has only {len(terms)} adverse terms "
            f"({terms!r}) — bump coverage"
        )


def test_rf441_native_script_preserved():
    """Native script must NOT be ASCII-transliterated — search backends
    match better against the script the source actually publishes in.
    Spot-checks Russian, Arabic, Chinese."""
    from aria_service.intel.adverse_media_polyglot import ADVERSE_TERMS
    # Russian Cyrillic
    assert any("Ѐ" <= c <= "ӿ" for c in "".join(ADVERSE_TERMS["ru"]))
    # Arabic
    assert any("؀" <= c <= "ۿ" for c in "".join(ADVERSE_TERMS["ar"]))
    # CJK
    assert any("一" <= c <= "鿿" for c in "".join(ADVERSE_TERMS["zh"]))


# ───────────────────────── build_adverse_query ──────────────────────────────


def test_rf441_build_adverse_query_constructs_or_block():
    """`<"entity"> (t1 OR t2 OR ...)` — entity quoted, terms OR-joined,
    capped at max_terms."""
    from aria_service.intel.adverse_media_polyglot import build_adverse_query
    q = build_adverse_query("Acme Defence Ltd", "ru", max_terms=3)
    assert q.startswith('"Acme Defence Ltd"')
    assert " OR " in q
    # Three terms → two ORs
    assert q.count(" OR ") == 2


def test_rf441_build_adverse_query_unsupported_lang_returns_bare_entity():
    """Unsupported lang → caller can still run a vanilla search; we
    return the bare entity name so they don't get a malformed query."""
    from aria_service.intel.adverse_media_polyglot import build_adverse_query
    q = build_adverse_query("Acme Ltd", "xx")
    assert q == "Acme Ltd"


def test_rf441_build_adverse_query_empty_entity_returns_empty():
    from aria_service.intel.adverse_media_polyglot import build_adverse_query
    assert build_adverse_query("", "ru") == ""
    assert build_adverse_query("   ", "ru") == ""


# ───────────────────────── build_queries_for_languages ─────────────────────


def test_rf441_build_queries_dedup_and_skip_unsupported():
    """Duplicate language codes deduped; unsupported codes silently
    dropped — caller can still call build_queries with a mixed list
    without losing the supported ones."""
    from aria_service.intel.adverse_media_polyglot import (
        build_queries_for_languages,
    )
    out = build_queries_for_languages(
        "Acme Ltd",
        ["ru", "RU", "xx", "zh", "zh", "ar"],
    )
    langs = [l for l, _ in out]
    # Dedup (case-insensitive); xx dropped; preserves first-seen order
    assert langs == ["ru", "zh", "ar"]
    # Every query well-formed
    for lang, q in out:
        assert q.startswith('"Acme Ltd"')
        assert " OR " in q


def test_rf441_build_queries_empty_languages_returns_empty():
    """No languages requested → empty list, not crash."""
    from aria_service.intel.adverse_media_polyglot import (
        build_queries_for_languages,
    )
    assert build_queries_for_languages("Acme Ltd", []) == []
    assert build_queries_for_languages("Acme Ltd", None) == []
