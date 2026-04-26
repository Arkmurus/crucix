"""Tests for the 2026-04-26 fix: student reading session must pick up
lang:* mastery tags as weak-topic candidates.

Background: the 6014587 commit added TASS+Kommersant (RU) and
Huanqiu+SCMP (ZH) RSS feeds and wired Cyrillic/CJK script detection
into detect_topics. But student._read_session sorted weak topics from
TOPICS only, which excluded CORE_MASTERY_TAGS (where lang:* lives).
Result: TASS articles were fetched, then deprioritised at selection,
then never read — so update_mastery never fired with lang:ru.
Live verification 2026-04-26: lang:ru moved 0.50 → 0.507 over a day,
lang:zh stayed 0.50 flat.

This test guards the fix so a future TOPICS / CORE_MASTERY_TAGS rename
doesn't regress back to silent exclusion.
"""
from __future__ import annotations


def test_weak_pool_includes_core_mastery_tags():
    """A regression-prone import boundary — both lists must be visible
    to _read_session via the same module."""
    from aria_service.intel import student

    assert "lang:ru" in student.CORE_MASTERY_TAGS
    assert "lang:zh" in student.CORE_MASTERY_TAGS
    # CORE tags must NOT already live in TOPICS (otherwise the dedupe
    # logic in _read_session is unnecessary). If a future refactor moves
    # them, this test will flag the redundancy so we can clean up.
    overlap = set(student.CORE_MASTERY_TAGS) & set(student.TOPICS)
    assert overlap == set(), f"CORE_MASTERY_TAGS leaked into TOPICS: {overlap}"


def test_research_feeds_have_language_categories():
    """The forced-feed boost relies on category prefixes 'russia_' /
    'china_' / 'arabic_'. If the feed-list curator renames a category
    the language boost silently breaks — guard against that."""
    from aria_service.intel.researcher import RESEARCH_FEEDS

    russia_feeds = [f for f in RESEARCH_FEEDS if (f.get("category") or "").startswith("russia_")]
    china_feeds = [f for f in RESEARCH_FEEDS if (f.get("category") or "").startswith("china_")]

    assert len(russia_feeds) >= 1, (
        "no russia_* feed in RESEARCH_FEEDS — lang:ru can't be lifted via reading"
    )
    assert len(china_feeds) >= 1, (
        "no china_* feed in RESEARCH_FEEDS — lang:zh can't be lifted via reading"
    )


def test_detect_topics_emits_lang_ru_on_cyrillic():
    """Smoke test the Cyrillic-detection threshold so a future refactor
    of detect_topics doesn't break the language-tag emission path."""
    from aria_service.intel.student import detect_topics

    # Real Russian sentence, well over 20 Cyrillic chars
    russian = "Министерство обороны России объявило о новых учениях в Балтийском море"
    tags = detect_topics(russian)
    assert "lang:ru" in tags

    # Below threshold — quoted name in English, must NOT trigger lang:ru
    english_with_short_ru = "President Putin (Президент) addressed the assembly"
    tags = detect_topics(english_with_short_ru)
    assert "lang:ru" not in tags


def test_detect_topics_emits_lang_zh_on_cjk():
    from aria_service.intel.student import detect_topics

    # Real Chinese — well over 20 CJK chars
    chinese = "中国人民解放军海军在南海地区进行了大规模军事演习展示新型驱逐舰"
    tags = detect_topics(chinese)
    assert "lang:zh" in tags
