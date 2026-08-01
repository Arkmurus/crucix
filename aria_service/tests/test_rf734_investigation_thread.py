"""R-F734 (2026-05-20) — regression tests for investigation-thread state.

Agent 1 DD audit found that multi-turn investigations were stateless:
each chat turn re-detected intent + re-fetched tool data from scratch,
with no link between Q1 ("research Acme Corp") and Q2 ("follow up on
their banking exposure"). The session stored only the message history.

R-F734 adds `session["investigation_thread"]` — a sub-dict with:
  current_entity, current_canonical, entity_type, last_tool,
  last_run_id, facts_so_far, open_questions, updated_at

…with a 6-hour TTL (stale threads return empty shape) and a
follow-up heuristic that prepends the prior thread context as a
[INVESTIGATION THREAD] block when the new user message looks like
a follow-up on the same entity.

Tests cover:
  - shape / TTL semantics
  - follow-up heuristic (entity mentioned, pronoun reference, signal
    words like "what about", "drill into")
  - update() semantics (entity change resets facts; dedupe; cap)
  - render_context_block produces non-empty output when there's signal
  - wiring present in both chat paths
"""
from __future__ import annotations

import inspect
import time

# R-F3597 — resolve source BY NAME through the current AST. `inspect.getsource`
# slices the file at the IMPORTED line numbers, so a concurrent edit during a
# long run returns a DIFFERENT function's body (measured 2026-07-31).
from ._source_probe import function_source


def test_empty_session_returns_empty_thread():
    from aria_service.intel import investigation_thread as it

    assert it.get_thread(None)["current_entity"] == ""
    assert it.get_thread({})["current_entity"] == ""
    assert it.get_thread({"foo": "bar"})["current_entity"] == ""


def test_thread_shape_keys():
    """R-F734: empty thread has all the expected fields."""
    from aria_service.intel import investigation_thread as it

    t = it.get_thread(None)
    for k in (
        "current_entity", "current_canonical", "entity_type",
        "last_tool", "last_run_id", "facts_so_far",
        "open_questions", "updated_at",
    ):
        assert k in t


def test_update_creates_thread_in_session():
    """R-F734: update() writes the thread back into the session dict."""
    from aria_service.intel import investigation_thread as it

    session = {}
    it.update(session, entity="Acme Corp", canonical="Acme Corp", entity_type="company")
    assert "investigation_thread" in session
    assert session["investigation_thread"]["current_entity"] == "Acme Corp"
    assert session["investigation_thread"]["entity_type"] == "company"
    assert session["investigation_thread"]["updated_at"] > 0


def test_update_entity_change_resets_facts():
    """R-F734: switching from Acme to Smith Industries clears the
    accumulated facts list — past facts about Acme aren't carry-forward
    context for a fresh investigation."""
    from aria_service.intel import investigation_thread as it

    session = {}
    it.update(session, entity="Acme Corp", new_facts=["F1", "F2"])
    assert len(session["investigation_thread"]["facts_so_far"]) == 2

    it.update(session, entity="Smith Industries Ltd")
    assert session["investigation_thread"]["current_entity"] == "Smith Industries Ltd"
    assert session["investigation_thread"]["facts_so_far"] == []


def test_update_dedupes_facts():
    """R-F734: adding a fact already in the list does not double-up."""
    from aria_service.intel import investigation_thread as it

    session = {}
    it.update(session, entity="Acme", new_facts=["Director changed 2024-03"])
    it.update(session, new_facts=["Director changed 2024-03", "New filing"])
    facts = session["investigation_thread"]["facts_so_far"]
    assert len(facts) == 2


def test_update_caps_facts_at_max():
    """R-F734: MAX_FACTS bound protects the session blob from growth."""
    from aria_service.intel import investigation_thread as it

    session = {}
    it.update(session, entity="Acme")
    big = [f"Fact-{i}" for i in range(50)]
    it.update(session, new_facts=big)
    assert len(session["investigation_thread"]["facts_so_far"]) <= it.MAX_FACTS


def test_stale_thread_returns_empty():
    """R-F734: a thread older than TTL (6h) returns empty shape."""
    from aria_service.intel import investigation_thread as it

    session = {
        "investigation_thread": {
            "current_entity": "Old Entity",
            "updated_at": time.time() - (it.THREAD_TTL_SECONDS + 60),
        }
    }
    t = it.get_thread(session)
    assert t["current_entity"] == ""


def test_fresh_thread_within_ttl_returns_data():
    from aria_service.intel import investigation_thread as it

    session = {
        "investigation_thread": {
            "current_entity": "Fresh Entity",
            "updated_at": time.time() - 60,
        }
    }
    t = it.get_thread(session)
    assert t["current_entity"] == "Fresh Entity"


def test_is_likely_followup_entity_in_message():
    """R-F734: if the new message mentions the thread entity by name,
    it's a follow-up."""
    from aria_service.intel import investigation_thread as it

    thread = {"current_entity": "Acme Corp", "current_canonical": "Acme Corp"}
    assert it.is_likely_followup("What's Acme Corp's banking exposure?", thread)


def test_is_likely_followup_pronoun():
    """R-F734: 'they / their / them / it' all signal follow-up."""
    from aria_service.intel import investigation_thread as it

    thread = {"current_entity": "Acme Corp", "current_canonical": "Acme Corp"}
    assert it.is_likely_followup("What are they doing in Mozambique?", thread)
    assert it.is_likely_followup("Their banking partners?", thread)


def test_is_likely_followup_signal_phrases():
    """R-F734: 'what about', 'follow up', 'drill into', etc."""
    from aria_service.intel import investigation_thread as it

    thread = {"current_entity": "Acme Corp", "current_canonical": "Acme Corp"}
    assert it.is_likely_followup("What about their directors?", thread)
    assert it.is_likely_followup("Follow up on the sanctions hit.", thread)
    assert it.is_likely_followup("Drill into their offshore subsidiaries.", thread)


def test_is_likely_followup_false_for_new_topic():
    """R-F734: a brand-new entity question is NOT a follow-up."""
    from aria_service.intel import investigation_thread as it

    thread = {"current_entity": "Acme Corp", "current_canonical": "Acme Corp"}
    assert not it.is_likely_followup(
        "Research Beijing Aero Holdings completely.", thread,
    )


def test_is_likely_followup_false_for_empty_thread():
    """R-F734: if no current entity, nothing to follow up on."""
    from aria_service.intel import investigation_thread as it

    assert not it.is_likely_followup("anything", {})
    assert not it.is_likely_followup("anything", {"current_entity": ""})


def test_render_context_block_empty_for_no_entity():
    from aria_service.intel import investigation_thread as it

    assert it.render_context_block({}) == ""
    assert it.render_context_block({"current_entity": ""}) == ""
    assert it.render_context_block(None) == ""


def test_render_context_block_with_entity_and_facts():
    from aria_service.intel import investigation_thread as it

    block = it.render_context_block({
        "current_entity": "Acme Corp",
        "current_canonical": "Acme Corp",
        "entity_type": "company",
        "last_tool": "dd_orchestrate",
        "facts_so_far": ["Director changed 2024-03", "No OFAC hits"],
        "open_questions": ["Banking exposure"],
    })
    assert "[INVESTIGATION THREAD" in block
    assert "Acme Corp" in block
    assert "dd_orchestrate" in block
    assert "Director changed" in block
    assert "Banking exposure" in block


def test_clear_wipes_thread():
    from aria_service.intel import investigation_thread as it

    session = {"investigation_thread": {"current_entity": "Acme"}}
    it.clear(session)
    assert session["investigation_thread"]["current_entity"] == ""


# ── Wiring presence checks ──

def test_rf734_wiring_present_in_aria_chat_impl():
    """R-F734: investigation_thread call must appear in `_aria_chat_impl`."""
    from aria_service import aria_engine

    src = function_source(aria_engine, "_aria_chat_impl")
    assert "R-F734" in src
    assert "investigation_thread" in src


def test_rf734_wiring_present_in_aria_chat_stream_impl():
    """R-F734: per §13 stream-bypass rule, mirror present in stream path."""
    from aria_service import aria_engine

    src = function_source(aria_engine, "_aria_chat_stream_impl")
    assert "R-F734" in src
    assert "investigation_thread" in src
