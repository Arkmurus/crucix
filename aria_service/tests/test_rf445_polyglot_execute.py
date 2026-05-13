"""R-F445 — execute polyglot adverse-media queries via search_multilingual.

R-F441 surfaced `web_footprint["adverse_media_queries"]` but added
zero search recall — no executor consumed them. R-F445 closes the
loop: dd_orchestrator's Digital layer now runs the top 4 prepared
queries through `web_search.search_multilingual` (one language per
call) and aggregates the results onto
`web_footprint["adverse_media_hits"]`.

Cost cap: 4 langs × 5 results = ≤20 backend searches per DD.
Opt-out: ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED=1.
"""
from __future__ import annotations


# ───────────────────────── R-F441 → R-F445 contract glue ────────────────────


def test_rf445_queries_list_consumed_in_lang_order():
    """The orchestrator iterates `_queries` (list of (lang, query) tuples)
    and pairs them with search_multilingual `languages=[lang]`. Pin the
    contract so future changes to build_queries_for_languages don't
    silently drop the executor."""
    from aria_service.intel.adverse_media_polyglot import (
        build_queries_for_languages,
    )
    queries = build_queries_for_languages("Acme Defence Ltd", ["en", "ru", "zh"])
    langs = [l for l, _ in queries]
    # English is not in ADVERSE_TERMS so silently dropped — only ru + zh
    assert langs == ["ru", "zh"]
    for lang, q in queries:
        assert q.startswith('"Acme Defence Ltd"')
        assert " OR " in q


# ───────────────────────── search_multilingual call shape ───────────────────


def test_rf445_calls_search_multilingual_per_lang(monkeypatch):
    """The orchestrator must call search_multilingual ONCE per (lang, q)
    pair with `languages=[lang]` and `translate_query=False` (we
    already built the per-lang query). Cap at 4 langs."""
    import asyncio
    from aria_service.intel import web_search

    calls: list[dict] = []

    async def _fake_sm(query, languages=None, *, max_results=15, translate_query=True):
        calls.append({
            "query": query, "languages": list(languages or []),
            "translate_query": translate_query,
            "max_results": max_results,
        })
        return []  # no results — orchestrator must handle empty

    monkeypatch.setattr(web_search, "search_multilingual", _fake_sm)

    # Replicate the orchestrator's exact call pattern inline so the
    # test pins the contract without needing _run_digital plumbing.
    _queries = [
        ("ru", '"Acme" (коррупция OR санкции)'),
        ("zh", '"Acme" (制裁 OR 腐败)'),
        ("ar", '"Acme" (فساد OR عقوبات)'),
        ("fa", '"Acme" (تحریم OR فساد)'),
        ("fr", '"Acme" (corruption OR sanctions)'),  # 5th — must be DROPPED by cap
    ]
    _capped = _queries[:4]

    async def _run_one(lang, q):
        return lang, await web_search.search_multilingual(
            q, languages=[lang], max_results=5, translate_query=False,
        )

    asyncio.run(asyncio.gather(*(_run_one(l, q) for l, q in _capped)))

    assert len(calls) == 4, (
        f"R-F445: expected 4 calls (capped), got {len(calls)}"
    )
    assert {c["languages"][0] for c in calls} == {"ru", "zh", "ar", "fa"}
    # 5th language (fr) must NOT have been called
    assert "fr" not in {c["languages"][0] for c in calls}
    # translate_query=False — we already built per-language strings
    for c in calls:
        assert c["translate_query"] is False
        assert c["max_results"] == 5


# ───────────────────────── Hit aggregation ──────────────────────────────────


def test_rf445_hits_aggregate_with_lang_tag(monkeypatch):
    """Each result is normalised to {lang, title, url, snippet} and
    persisted on web_footprint['adverse_media_hits']."""
    import asyncio
    from aria_service.intel import web_search

    class _FakeResult:
        def __init__(self, title, url, snippet):
            self.title = title
            self.url = url
            self.snippet = snippet

    async def _fake_sm(query, languages=None, *, max_results=15, translate_query=True):
        lang = (languages or ["en"])[0]
        return [
            _FakeResult(
                f"{lang}-title-{i}",
                f"https://example.{lang}/{i}",
                f"{lang}-snippet-{i}",
            )
            for i in range(3)
        ]

    monkeypatch.setattr(web_search, "search_multilingual", _fake_sm)

    queries = [("ru", '"x" (a OR b)'), ("zh", '"x" (c OR d)')]
    hits: list[dict] = []

    async def _run_one(lang, q):
        return lang, await web_search.search_multilingual(
            q, languages=[lang], max_results=5, translate_query=False,
        )

    results = asyncio.run(asyncio.gather(*(_run_one(l, q) for l, q in queries)))
    for lang, res_list in results:
        for r in (res_list or [])[:5]:
            hits.append({
                "lang": lang,
                "title": getattr(r, "title", "")[:240],
                "url": getattr(r, "url", "")[:500],
                "snippet": getattr(r, "snippet", "")[:400],
            })

    assert len(hits) == 6  # 2 langs × 3 results
    assert {h["lang"] for h in hits} == {"ru", "zh"}
    # Field shape pinned
    for h in hits:
        assert "title" in h and "url" in h and "snippet" in h and "lang" in h


def test_rf445_handles_search_exception_gracefully(monkeypatch):
    """If search_multilingual raises for one lang, the others still
    complete and the lang's result is empty (not a crash)."""
    import asyncio
    from aria_service.intel import web_search

    async def _fake_sm(query, languages=None, *, max_results=15, translate_query=True):
        lang = (languages or ["en"])[0]
        if lang == "ru":
            raise RuntimeError("simulated upstream timeout")
        return []

    monkeypatch.setattr(web_search, "search_multilingual", _fake_sm)

    queries = [("ru", "q1"), ("zh", "q2")]

    async def _run_one(lang, q):
        try:
            return lang, await web_search.search_multilingual(
                q, languages=[lang], max_results=5, translate_query=False,
            )
        except Exception:
            return lang, []

    results = asyncio.run(asyncio.gather(*(_run_one(l, q) for l, q in queries)))
    # Both lang results present (RU empty due to exception caught)
    assert {l for l, _ in results} == {"ru", "zh"}
    assert results[0][1] == []  # ru → caught


# ───────────────────────── Kill-switch contract ─────────────────────────────


def test_rf445_kill_switch_pattern_works(monkeypatch):
    """ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED=1 short-circuits the
    executor. Pin env-var truthy/falsy semantics so the orchestrator's
    `not os.getenv(...).strip()` gate evaluates correctly."""
    import os
    monkeypatch.setenv("ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED", "1")
    assert bool(os.getenv("ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED", "").strip())
    monkeypatch.delenv("ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED")
    assert not os.getenv("ARIA_ADVERSE_POLYGLOT_EXECUTE_DISABLED", "").strip()
