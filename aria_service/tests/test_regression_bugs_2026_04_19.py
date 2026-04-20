"""Regression tests for three silent bugs surfaced in the 2026-04-19 audit.

Each of these bugs shipped to production and went undetected for weeks
because the failure mode was quiet — empty string, missing elif branch,
meaningless 0% baseline. A 3-line pytest would have caught each one on
first push. These are those 3-line pytests.

Bugs covered
────────────
1. adversarial_challenge._default_llm_fn returning "" when the global
   provider was reachable. Root cause: `create_llm_provider()` was called
   with no arguments (the factory requires a positional provider name),
   raised TypeError, was swallowed by the outer except, returned "".
   Every adversarial attack scored as fail for weeks. Fix commit: d576c27.

2. autonomous/tasks.py dispatch tuple missing the 15 tool kinds added in
   the 2026-04-17/18 marathon. `_execute_direct_tool` had handlers; the
   outer dispatch tuple at `elif tool_kind in (...)` did not. Every
   affected task logged "unsupported tool kind" and the 24/7 learning
   loop reported zeros for a week. Fix commit: b51d6ff.

3. adversarial_challenge.run_weekly persisting + staging amendments on
   runs where every attack response was empty (LLM degraded / billing-
   cooled). 2026-04-19 06:00 UTC wrote a fake 0% baseline and 11
   false-positive clause amendments. Fix commit: 14cfeb3.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path


# ═══════════════════════════════════════════════════════════════════════
# 1. _default_llm_fn must route through the configured provider, not
#    try to rebuild one with a zero-arg factory call.
# ═══════════════════════════════════════════════════════════════════════

def test_default_llm_fn_uses_global_provider() -> None:
    from aria_service.intel import adversarial_challenge
    from aria_service.main import app

    class _FakeResult:
        text = "SAFE_RESPONSE"

    class _FakeProvider:
        is_configured = True
        name = "fake"

        async def complete(self, **kwargs):
            # Matches the provider protocol used by _default_llm_fn:
            # system_prompt, user_message, max_tokens, timeout
            return _FakeResult()

    original = getattr(app.state, "llm_provider", None)
    app.state.llm_provider = _FakeProvider()
    try:
        result = asyncio.run(
            adversarial_challenge._default_llm_fn("hello", conversation=None)
        )
    finally:
        app.state.llm_provider = original
    assert result == "SAFE_RESPONSE", (
        "Regression: _default_llm_fn returned empty or wrong value. "
        "The d576c27 fix replaced a broken create_llm_provider() call "
        "with app.state.llm_provider — ensure that path still works."
    )


def test_default_llm_fn_returns_empty_when_no_provider() -> None:
    """Documented contract: if provider isn't configured, return '' (the
    runner marks the attack ERROR, not PASS). Prevents future refactors
    from raising instead."""
    from aria_service.intel import adversarial_challenge
    from aria_service.main import app

    original = getattr(app.state, "llm_provider", None)
    app.state.llm_provider = None
    try:
        result = asyncio.run(
            adversarial_challenge._default_llm_fn("hello", conversation=None)
        )
    finally:
        app.state.llm_provider = original
    assert result == ""


# ═══════════════════════════════════════════════════════════════════════
# 2. Every tool_kind handled by _execute_direct_tool must also appear in
#    the dispatch tuple in run_task. Prevents the class of bug where a
#    new handler is added but the dispatcher never routes to it.
# ═══════════════════════════════════════════════════════════════════════

# tool_kinds that are NOT direct tools (they go through the chat pipeline
# elsewhere in run_task) and therefore should NOT be in _execute_direct_tool.
_CHAT_TOOL_KINDS = {"deep_research", "web_search", "investigate"}


def _extract_direct_tool_kinds(source: str) -> set[str]:
    """Pull every `tool_kind == "X"` from the _execute_direct_tool body."""
    start = source.index("async def _execute_direct_tool")
    # End at the next top-level `async def` or `def `
    end = source.index("\nasync def ", start + 1)
    body = source[start:end]
    return set(re.findall(r'tool_kind\s*==\s*"([^"]+)"', body))


def _extract_dispatch_tuple(source: str) -> set[str]:
    """Pull the `elif tool_kind in (...)` tuple in run_task.

    Strips line comments first — the tuple body contains a `# ... "unsupported
    tool kind"` explanatory comment that would otherwise leak into the result.
    """
    m = re.search(
        r"elif\s+tool_kind\s+in\s*\(([^)]+)\):",
        source,
        flags=re.DOTALL,
    )
    assert m, "Could not locate dispatch tuple in autonomous/tasks.py"
    body = "\n".join(
        line.split("#", 1)[0] for line in m.group(1).splitlines()
    )
    return set(re.findall(r'"([^"]+)"', body))


def test_autonomous_dispatch_parity() -> None:
    """Every direct-tool handler must be routable from run_task.

    If this fails: either a new tool_kind was added to _execute_direct_tool
    and the dispatch tuple wasn't updated (silent 'unsupported tool kind'
    errors in production), or a dispatch-tuple entry has no matching
    handler (KeyError at runtime).
    """
    path = Path(__file__).resolve().parent.parent / "autonomous" / "tasks.py"
    source = path.read_text(encoding="utf-8")

    direct_kinds = _extract_direct_tool_kinds(source)
    dispatch_kinds = _extract_dispatch_tuple(source)

    missing_from_dispatch = direct_kinds - dispatch_kinds - _CHAT_TOOL_KINDS
    missing_from_handler = dispatch_kinds - direct_kinds - _CHAT_TOOL_KINDS

    assert not missing_from_dispatch, (
        f"Handlers exist in _execute_direct_tool but missing from "
        f"dispatch tuple: {sorted(missing_from_dispatch)}. This is the "
        f"exact bug class b51d6ff fixed."
    )
    assert not missing_from_handler, (
        f"Dispatch tuple references tool kinds with no handler: "
        f"{sorted(missing_from_handler)}"
    )


# ═══════════════════════════════════════════════════════════════════════
# 2b. knowledge_spider seed sources must exist on their upstream modules.
#     Before 2026-04-20, rag_store.recent_chunks and verified_intel.
#     recent_facts didn't exist. The spider's hasattr() gate silently
#     skipped those sources and knowledge_spider.fetches_24h stayed 0
#     for weeks. These tests assert the contract the spider depends on.
# ═══════════════════════════════════════════════════════════════════════

def test_rag_store_exposes_recent_chunks() -> None:
    from aria_service.intel import rag_store
    import inspect
    assert hasattr(rag_store, "recent_chunks"), (
        "rag_store.recent_chunks() must exist — knowledge_spider's seed "
        "collector gates on hasattr() and silently skips this source if "
        "it's missing. That was the 2026-04-20 bug."
    )
    assert inspect.iscoroutinefunction(rag_store.recent_chunks)


def test_verified_intel_exposes_recent_facts() -> None:
    from aria_service.intel import verified_intel
    import inspect
    assert hasattr(verified_intel, "recent_facts"), (
        "verified_intel.recent_facts() must exist — knowledge_spider's "
        "seed collector gates on hasattr() and silently skips this "
        "source if it's missing. That was the 2026-04-20 bug."
    )
    assert inspect.iscoroutinefunction(verified_intel.recent_facts)


def test_spider_seed_source_contract() -> None:
    """End-to-end contract: every seed source the spider checks with
    hasattr() must actually exist on its module."""
    from aria_service.intel import chat_audit_log, rag_store, verified_intel
    # These method names must match knowledge_spider._collect_seeds() gates.
    assert hasattr(chat_audit_log, "get_recent")
    assert hasattr(rag_store, "recent_chunks")
    assert hasattr(verified_intel, "recent_facts")


def test_spider_ingest_contract() -> None:
    """The spider writes fetched URLs back into RAG via
    `rag_store.add_chunk`. That function must exist — before
    2026-04-20 it didn't and every spider ingest silently no-op'd."""
    from aria_service.intel import rag_store
    import inspect
    assert hasattr(rag_store, "add_chunk")
    assert inspect.iscoroutinefunction(rag_store.add_chunk)


def test_spider_user_agent_is_ascii() -> None:
    """HTTP headers must be ASCII. Pre-2026-04-20 the spider's UA string
    had an em-dash (—, U+2014) which made every single fetch raise
    UnicodeEncodeError inside httpx, swallowed by _fetch's except clause,
    returning None — so the spider logged fetched=0 even though the
    seed-collection and queue processing were working. Two bugs in one:
    the Unicode char AND the overly-broad exception handler.

    The fix added an import-time tripwire that aborts module load if the
    UA is non-ASCII; this test asserts the tripwire still fires so nobody
    accidentally removes the guard."""
    from aria_service.learning import knowledge_spider
    # UA must encode cleanly as ASCII
    knowledge_spider._USER_AGENT.encode("ascii")  # raises if unicode leaks back in


def test_ecosystem_reassess_gate_contracts() -> None:
    """ecosystem_reassess reads self_metrics.recent_drift and
    capability_gaps.recent — both were missing pre-2026-04-20 and
    silently degraded the hourly reassess into a no-op for two of
    its four signal sources."""
    from aria_service.intel import self_metrics, capability_gaps
    import inspect
    assert hasattr(self_metrics, "recent_drift")
    assert inspect.iscoroutinefunction(self_metrics.recent_drift)
    assert hasattr(capability_gaps, "recent")
    assert inspect.iscoroutinefunction(capability_gaps.recent)


def test_learning_module_gate_contracts() -> None:
    """Every hasattr() gate in learning/* modules must resolve True.
    Enumerates the gates discovered in the 2026-04-20 audit to prevent
    another silent-skip bug class."""
    from aria_service.intel import (
        chat_audit_log,
        rag_store,
        verified_intel,
        mistake_ledger,
        capability_gaps,
        adversarial_challenge,
    )
    # learning/metacognitive_journal.py
    assert hasattr(chat_audit_log, "get_recent"), "metacog_journal seed"
    assert hasattr(adversarial_challenge, "stats"), "metacog_journal adversarial"
    assert hasattr(mistake_ledger, "recent"), "metacog_journal mistakes"
    assert hasattr(capability_gaps, "recent_gaps"), "metacog_journal gaps"
    # learning/training_export.py
    assert hasattr(adversarial_challenge, "recent_runs"), "training_export adversarial"
    # learning/knowledge_spider.py
    assert hasattr(rag_store, "recent_chunks"), "spider seed"
    assert hasattr(verified_intel, "recent_facts"), "spider seed"
    assert hasattr(rag_store, "add_chunk"), "spider ingest"


# ═══════════════════════════════════════════════════════════════════════
# 3. adversarial_challenge.run_weekly must short-circuit with
#    summary["invalid"]=True when every attack response is empty, and
#    must NOT persist a baseline or stage amendments.
# ═══════════════════════════════════════════════════════════════════════

def test_adversarial_invalid_run_guard() -> None:
    """All-empty responses → invalid=True, no persistence, no staging."""
    from aria_service.intel import adversarial_challenge as adv

    persisted: list = []
    staged: list = []

    async def _fake_run_single(attack_id: str, llm_fn=None) -> dict:
        return {
            "attack_id": attack_id,
            "category": "A_FALSE_INFO",
            "severity": "HIGH",
            "passed": False,
            "responses": ["", "   ", ""],   # all empty/whitespace
            "broke_at_turn": None,
            "violation_hits_per_turn": [],
            "anchor_clauses": [],
            "source_cases": [],
            "run_at": "2026-04-20T00:00:00+00:00",
        }

    # Patch run_single + the persist write path so the test never touches Redis.
    original_run_single = adv.run_single
    adv.run_single = _fake_run_single
    try:
        from aria_service.intel import redis_store as rs

        original_set_json = rs.set_json

        async def _capture_set_json(key, val, **kw):
            persisted.append((key, val))

        rs.set_json = _capture_set_json

        try:
            summary = asyncio.run(adv.run_weekly(attack_ids=None))
        finally:
            rs.set_json = original_set_json
    finally:
        adv.run_single = original_run_single

    assert summary.get("invalid") is True, (
        "Expected summary['invalid']==True when every response is empty. "
        "The 14cfeb3 fix added this guard after a degraded-LLM run on "
        "2026-04-19 06:00 UTC persisted a false 0% baseline and staged "
        "11 meaningless clause amendments."
    )
    assert summary.get("invalid_reason") == "llm_degraded_empty_responses"
    assert persisted == [], (
        f"Run marked invalid but still persisted: {persisted}. "
        f"The guard must short-circuit before any Redis write."
    )
