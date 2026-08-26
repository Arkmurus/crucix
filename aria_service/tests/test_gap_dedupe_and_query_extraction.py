"""Regression guards for F60 + F66 (2026-04-28 batch).

F66 — capability_gaps.record_gap was append-only. calibration_review fired
the same `[adversarial_critical_failure] Mastery scores overconfident by
23%` on every diagnostic cycle (~5 min); within 2 days it would crowd
MAX_GAPS=500 and hide real gaps from self-improve analysis.

F60 — researcher.validate_hypothesis truncated hypothesis text to 60
chars without dropping stopwords, so queries reaching the search APIs
were 'The ARIA collection pipeline is subscribed to or receiving
evidence 2026' — mostly filler, zero search signal.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch


# ── F66: capability_gaps window dedupe ──────────────────────────────────────


def test_record_gap_dedupes_within_window():
    """Same (gap_type, detail) inside the dedupe window must not be
    re-recorded. The second call returns a deduped marker instead of a
    fresh entry."""
    from aria_service.intel import capability_gaps

    written: list[tuple[str, str]] = []
    sentinels: dict[str, str] = {}

    async def fake_lpush(key, value, **kwargs):
        written.append(("lpush", value))

    async def fake_ltrim(key, start, stop):
        written.append(("ltrim", f"{start}:{stop}"))

    async def fake_get(key):
        return sentinels.get(key)

    async def fake_set(key, value, ex=None, keepttl=False):
        sentinels[key] = value

    async def run():
        with patch("aria_service.intel.capability_gaps.rs.lpush", side_effect=fake_lpush), \
             patch("aria_service.intel.capability_gaps.rs.ltrim", side_effect=fake_ltrim), \
             patch("aria_service.intel.capability_gaps.rs.get", side_effect=fake_get), \
             patch("aria_service.intel.capability_gaps.rs.get_strict", side_effect=fake_get), \
             patch("aria_service.intel.capability_gaps.rs.set", side_effect=fake_set):
            first = await capability_gaps.record_gap(
                gap_type="adversarial_critical_failure",
                detail="Mastery scores overconfident by 23%",
            )
            second = await capability_gaps.record_gap(
                gap_type="adversarial_critical_failure",
                detail="Mastery scores overconfident by 23%",
            )
            return first, second

    first, second = asyncio.run(run())

    assert "id" in first, f"first call should return a fresh entry, got {first!r}"
    assert second.get("deduped") is True, (
        f"second identical call should return a deduped marker, got {second!r}"
    )
    # Only one lpush should have happened
    lpush_count = sum(1 for k, _ in written if k == "lpush")
    assert lpush_count == 1, f"expected 1 lpush, got {lpush_count}"


def test_record_gap_different_detail_not_deduped():
    """Different detail strings must NOT collide in the dedupe key —
    same gap_type with two distinct details is two separate entries."""
    from aria_service.intel import capability_gaps

    written: list[str] = []
    sentinels: dict[str, str] = {}

    async def fake_lpush(key, value, **kwargs):
        written.append(value)

    async def fake_ltrim(key, start, stop):
        pass

    async def fake_get(key):
        return sentinels.get(key)

    async def fake_set(key, value, ex=None, keepttl=False):
        sentinels[key] = value

    async def run():
        with patch("aria_service.intel.capability_gaps.rs.lpush", side_effect=fake_lpush), \
             patch("aria_service.intel.capability_gaps.rs.ltrim", side_effect=fake_ltrim), \
             patch("aria_service.intel.capability_gaps.rs.get", side_effect=fake_get), \
             patch("aria_service.intel.capability_gaps.rs.get_strict", side_effect=fake_get), \
             patch("aria_service.intel.capability_gaps.rs.set", side_effect=fake_set):
            await capability_gaps.record_gap(
                gap_type="timeout", detail="Backend brave_search unreachable"
            )
            await capability_gaps.record_gap(
                gap_type="timeout", detail="Backend semantic_scholar unreachable"
            )

    asyncio.run(run())
    assert len(written) == 2, (
        f"two distinct details should yield 2 entries, got {len(written)}"
    )


# ── F60: hypothesis → query keyword extraction ─────────────────────────────


def test_real_pollution_hypothesis_distilled_to_keywords():
    """The real hypothesis fragments from 09:20:15–09:20:42 logs must be
    distilled down to keyword-shaped queries — no leading 'The', no
    auxiliaries, no 'evidence' filler from the source text."""
    from aria_service.intel.researcher import _extract_query_keywords

    cases = [
        # (input, must NOT contain — lowered, must contain — exact case kept)
        (
            "The ARIA collection pipeline is subscribed to or receiving evidence",
            ("the ", " is ", " subscribed "),
            ("ARIA",),
        ),
        (
            "The Trump administration is systematically reclassifying evidence",
            ("the ", " is "),
            ("Trump",),
        ),
        (
            "The sustained double-digit Asia-Pacific defence spending",
            ("the ",),
            ("Asia", "Pacific"),
        ),
    ]
    for raw, must_not, must in cases:
        q = _extract_query_keywords(raw, max_words=8)
        ql = q.lower()
        for bad in must_not:
            assert bad.strip() not in ql.split() if " " not in bad else bad not in ql, (
                f"stopword leaked into query {q!r} from {raw!r}"
            )
        for good in must:
            assert good in q, (
                f"keyword {good!r} dropped from {raw!r} — query was {q!r}"
            )


def test_extract_keywords_keeps_proper_nouns_even_if_short():
    """Capitalised tokens ('NATO', 'EU', 'F-35') must not be stripped
    even though some of them collide with stopwords lowercased."""
    from aria_service.intel.researcher import _extract_query_keywords

    q = _extract_query_keywords("It is the NATO defence procurement budget for 2026")
    assert "NATO" in q, f"NATO dropped — query was {q!r}"
    # 'It', 'is', 'the', 'for' should be stripped
    for bad in ("it", "is", "the", "for"):
        assert bad not in q.split(), f"stopword {bad!r} leaked: {q!r}"


def test_extract_keywords_caps_word_count():
    """A 30-word hypothesis must produce <=8-word query."""
    from aria_service.intel.researcher import _extract_query_keywords

    long_hypothesis = (
        "Russia continues to expand military procurement budgets while "
        "simultaneously reducing exports to traditional partner nations "
        "across Africa Latin America and Southeast Asia regional markets"
    )
    q = _extract_query_keywords(long_hypothesis, max_words=8)
    assert len(q.split()) <= 8, f"got {len(q.split())} words: {q!r}"
