"""R-F617 — Per-user mental model.

Theory-of-Mind foundation for dialogue v2.1 Phase 2. Pins:
  - upsert + get round-trip
  - JSON list fields decode correctly
  - touch_active updates last_seen_active
  - add_domain_strength normalises to upper-case + dedups
  - record_failure appends with timestamp
  - has_heard_recently honours the recency window
  - best_user_for_domain returns the most-recently-active user with
    the named strength
  - active_in_last filters correctly
  - stats() reports counts per role + active windows
"""
from __future__ import annotations

import asyncio
import time

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Per-test fresh user_model DB."""
    db_path = tmp_path / "test_um.db"
    monkeypatch.setenv("ARIA_DIALOGUE_DB_PATH", str(db_path))
    from aria_service.intel import user_model as um
    asyncio.run(um._reset_for_tests())
    yield db_path
    asyncio.run(um._reset_for_tests())


# ══════════════════════════════════════════════════════════════════
# PART A — upsert + get
# ══════════════════════════════════════════════════════════════════

def test_rf617_upsert_creates_new_user(fresh_db):
    from aria_service.intel import user_model as um
    ok = asyncio.run(um.upsert_user(
        "antonio",
        display_name="Antonio",
        role="operator",
        preferred_channel="wa_dm",
        tone_preference="short",
        max_initiative=3,
    ))
    assert ok is True
    user = asyncio.run(um.get_user("antonio"))
    assert user is not None
    assert user["user_id"] == "antonio"
    assert user["display_name"] == "Antonio"
    assert user["role"] == "operator"
    assert user["preferred_channel"] == "wa_dm"
    assert user["max_initiative"] == 3
    # JSON list fields default to []
    assert user["domain_strengths"] == []
    assert user["active_deals"] == []


def test_rf617_upsert_updates_named_fields_only(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user(
        "antonio", display_name="Antonio", role="operator",
        preferred_channel="wa_dm",
    ))
    asyncio.run(um.upsert_user(
        "antonio", tone_preference="detailed",
    ))
    user = asyncio.run(um.get_user("antonio"))
    assert user["tone_preference"] == "detailed"
    # Other fields unchanged
    assert user["display_name"] == "Antonio"
    assert user["preferred_channel"] == "wa_dm"


def test_rf617_get_unknown_user_returns_none(fresh_db):
    from aria_service.intel import user_model as um
    assert asyncio.run(um.get_user("nobody")) is None


def test_rf617_empty_user_id_safe(fresh_db):
    from aria_service.intel import user_model as um
    assert asyncio.run(um.upsert_user("")) is False
    assert asyncio.run(um.get_user("")) is None


# ══════════════════════════════════════════════════════════════════
# PART B — touch_active
# ══════════════════════════════════════════════════════════════════

def test_rf617_touch_active_sets_last_seen(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.touch_active("antonio"))
    user = asyncio.run(um.get_user("antonio"))
    assert user is not None
    assert user["last_seen_active"] is not None
    assert user["last_seen_active"] > 0


def test_rf617_touch_active_creates_user_lazily(fresh_db):
    """First touch creates the user row — useful for chat audit
    surfacing new user_ids."""
    from aria_service.intel import user_model as um
    asyncio.run(um.touch_active("first_seen_via_chat"))
    user = asyncio.run(um.get_user("first_seen_via_chat"))
    assert user is not None


# ══════════════════════════════════════════════════════════════════
# PART C — add_domain_strength
# ══════════════════════════════════════════════════════════════════

def test_rf617_add_domain_strength_normalises_to_upper(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    asyncio.run(um.add_domain_strength("ant", "sa"))
    asyncio.run(um.add_domain_strength("ant", "pt"))
    user = asyncio.run(um.get_user("ant"))
    assert "SA" in user["domain_strengths"]
    assert "PT" in user["domain_strengths"]


def test_rf617_add_domain_strength_dedups(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    for _ in range(3):
        asyncio.run(um.add_domain_strength("ant", "SA"))
    user = asyncio.run(um.get_user("ant"))
    assert user["domain_strengths"].count("SA") == 1


def test_rf617_add_active_deal_persists(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    asyncio.run(um.add_active_deal("ant", "deal_la_scala_2026_05"))
    user = asyncio.run(um.get_user("ant"))
    assert "deal_la_scala_2026_05" in user["active_deals"]


# ══════════════════════════════════════════════════════════════════
# PART D — record_failure (ARIA's past mistakes with this user)
# ══════════════════════════════════════════════════════════════════

def test_rf617_record_failure_appends_with_timestamp(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    asyncio.run(um.record_failure(
        "ant", "Misread Lukoil OFSI status as listed; corrected."
    ))
    user = asyncio.run(um.get_user("ant"))
    fl = user["failure_log"]
    assert len(fl) == 1
    assert "Lukoil" in fl[0]["summary"]
    assert fl[0]["ts"] > 0


def test_rf617_failure_log_caps_at_50(fresh_db):
    """Defensive cap so failure_log doesn't grow unbounded."""
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    for i in range(60):
        asyncio.run(um.record_failure("ant", f"mistake {i}"))
    user = asyncio.run(um.get_user("ant"))
    assert len(user["failure_log"]) <= 50
    # Most recent retained
    summaries = [e["summary"] for e in user["failure_log"]]
    assert "mistake 59" in summaries


# ══════════════════════════════════════════════════════════════════
# PART E — recency window + has_heard_recently
# ══════════════════════════════════════════════════════════════════

def test_rf617_has_heard_recently_within_window_returns_true(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    asyncio.run(um.add_to_recency_window("ant", "fact:lukoil_ofsi_clear"))
    assert asyncio.run(um.has_heard_recently("ant", "fact:lukoil_ofsi_clear"))


def test_rf617_has_heard_recently_unknown_fact_returns_false(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    assert not asyncio.run(um.has_heard_recently("ant", "fact:never_said"))


def test_rf617_has_heard_recently_outside_window_returns_false(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant"))
    asyncio.run(um.add_to_recency_window("ant", "fact:old"))
    # Force-age the entry by checking with a 0-second window
    assert not asyncio.run(um.has_heard_recently(
        "ant", "fact:old", max_age_seconds=0
    ))


# ══════════════════════════════════════════════════════════════════
# PART F — best_user_for_domain
# ══════════════════════════════════════════════════════════════════

def test_rf617_best_user_for_domain_returns_strongest_match(fresh_db):
    from aria_service.intel import user_model as um
    # Two users with overlapping + distinct domain strengths
    asyncio.run(um.upsert_user("ant", role="operator"))
    asyncio.run(um.add_domain_strength("ant", "GB"))
    asyncio.run(um.touch_active("ant"))
    time.sleep(0.05)
    asyncio.run(um.upsert_user("bd_analyst", role="bd"))
    asyncio.run(um.add_domain_strength("bd_analyst", "SA"))
    asyncio.run(um.add_domain_strength("bd_analyst", "AE"))
    asyncio.run(um.touch_active("bd_analyst"))

    assert asyncio.run(um.best_user_for_domain("SA")) == "bd_analyst"
    assert asyncio.run(um.best_user_for_domain("GB")) == "ant"
    assert asyncio.run(um.best_user_for_domain("NZ")) is None


def test_rf617_best_user_for_domain_prefers_most_recently_active(fresh_db):
    """When two users have the same domain strength, the more recently
    active one wins (better signal we'll get a quick response)."""
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant", role="operator"))
    asyncio.run(um.add_domain_strength("ant", "SA"))
    asyncio.run(um.touch_active("ant"))
    time.sleep(0.05)
    asyncio.run(um.upsert_user("bd", role="bd"))
    asyncio.run(um.add_domain_strength("bd", "SA"))
    asyncio.run(um.touch_active("bd"))  # more recent
    assert asyncio.run(um.best_user_for_domain("SA")) == "bd"


# ══════════════════════════════════════════════════════════════════
# PART G — active_in_last
# ══════════════════════════════════════════════════════════════════

def test_rf617_active_in_last_filters_correctly(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.touch_active("u1"))
    asyncio.run(um.touch_active("u2"))
    # u3 has no touch — should not appear
    asyncio.run(um.upsert_user("u3"))
    recent = asyncio.run(um.active_in_last(seconds=3600))
    assert set(recent) == {"u1", "u2"}


# ══════════════════════════════════════════════════════════════════
# PART H — stats
# ══════════════════════════════════════════════════════════════════

def test_rf617_stats_reports_role_counts(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("a", role="operator"))
    asyncio.run(um.upsert_user("b", role="bd"))
    asyncio.run(um.upsert_user("c", role="bd"))
    asyncio.run(um.upsert_user("d", role="analyst"))
    s = asyncio.run(um.stats())
    assert s["total_users"] == 4
    assert s["by_role"].get("operator") == 1
    assert s["by_role"].get("bd") == 2
    assert s["by_role"].get("analyst") == 1


# ══════════════════════════════════════════════════════════════════
# PART I — list_all_users
# ══════════════════════════════════════════════════════════════════

def test_rf617_list_all_users_returns_decoded_rows(fresh_db):
    from aria_service.intel import user_model as um
    asyncio.run(um.upsert_user("ant", role="operator"))
    asyncio.run(um.add_domain_strength("ant", "SA"))
    asyncio.run(um.touch_active("ant"))
    users = asyncio.run(um.list_all_users())
    assert len(users) == 1
    assert users[0]["user_id"] == "ant"
    # JSON fields decoded to list, not stored as raw string
    assert isinstance(users[0]["domain_strengths"], list)
    assert "SA" in users[0]["domain_strengths"]
