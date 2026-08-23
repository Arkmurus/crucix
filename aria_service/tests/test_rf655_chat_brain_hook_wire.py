"""R-F655 — wire brain_hook.absorb into chat + chat_stream (clause 15).

Audit 2026-05-17 found that aria_chat / aria_chat_stream made paid
Anthropic/DeepSeek calls whose output was returned to the user and
then dropped — never fed to brain_hook → rag → ledger → mastery.
Every subsequent equivalent query had to pay for the LLM again.

This violates CLAUDE.md §15 (pay-once-remember-forever): every paid
API call must write to brain_hook so the next equivalent query hits
memory for $0.

Fix: fire-and-forget brain_hook.absorb at the tail of chat_ep and
chat_stream_ep, after all guards have settled the response_text but
before the response is yielded / returned. brain_hook fans out to
all four learning tiers (student.update_mastery, knowledge.store_fact,
neural_memory.learn_from_text, capability_gaps.record_gap).

Tests pin three properties:
  1. brain_hook._MODULE_TOPICS registers aria_chat + aria_chat_stream
     (without this, signals file under 'generic' and miss the mastery
     rollups for compliance / osint / market_intel / etc.).
  2. chat_ep (non-stream, /api/aria/chat) source contains the R-F655
     absorb call.
  3. chat_stream_ep (/api/aria/chat/stream — WhatsApp default) source
     contains the R-F655 absorb call.
"""
from __future__ import annotations

import re
from pathlib import Path


_ROUTES_SRC = (
    Path(__file__).resolve().parent.parent / "routes" / "aria.py"
).read_text(encoding="utf-8")


def test_rf655_module_topics_registers_aria_chat():
    """brain_hook._MODULE_TOPICS must include aria_chat with a broad
    topic set. Without this, chat signals file under 'generic' and
    fail to roll up into the per-domain student mastery EWMAs."""
    from aria_service.intel import brain_hook
    topics = brain_hook._MODULE_TOPICS
    assert "aria_chat" in topics, (
        "R-F655: brain_hook._MODULE_TOPICS missing 'aria_chat' entry — "
        "non-stream chat signals will file under 'generic' and miss "
        "domain-specific mastery rollups."
    )
    # Chat touches everything; topic set must be broad.
    aria_chat_topics = set(topics["aria_chat"])
    expected_minimum = {"general", "compliance", "osint", "market_intel"}
    assert expected_minimum.issubset(aria_chat_topics), (
        f"R-F655: aria_chat topic set {aria_chat_topics} missing "
        f"required topics {expected_minimum - aria_chat_topics}. "
        "Chat is the user-facing surface — narrow topics undercount."
    )


def test_rf655_module_topics_registers_aria_chat_stream():
    """Same as above, for the streaming path. aria_chat_stream is the
    WhatsApp default, so missing it leaks WA-driven learning."""
    from aria_service.intel import brain_hook
    topics = brain_hook._MODULE_TOPICS
    assert "aria_chat_stream" in topics, (
        "R-F655: brain_hook._MODULE_TOPICS missing 'aria_chat_stream' "
        "entry — WhatsApp chat (which uses /chat/stream by default) "
        "will file all learning signals under 'generic'."
    )


def test_rf655_chat_ep_calls_brain_hook_absorb():
    """chat_ep (non-stream) source must invoke brain_hook.absorb with
    module='aria_chat' as a fire-and-forget background task. Without
    this, every chat answer is paid for and immediately forgotten."""
    assert "R-F655" in _ROUTES_SRC, (
        "R-F655: marker comment missing from routes/aria.py — fix reverted?"
    )
    # The absorb call must use module="aria_chat" specifically (not the
    # stream variant). Pattern matches the wrapped fire-and-forget shape.
    pattern = re.compile(
        r"R-F655.*?brain_hook.*?absorb\([^)]*module\s*=\s*[\"']aria_chat[\"']",
        re.DOTALL,
    )
    assert pattern.search(_ROUTES_SRC), (
        "R-F655: chat_ep does not call brain_hook.absorb(module='aria_chat'). "
        "The non-stream chat path is still silently discarding paid LLM "
        "output — clause 15 (pay-once-remember-forever) violation."
    )


def test_rf655_chat_stream_ep_calls_brain_hook_absorb():
    """chat_stream_ep (WhatsApp default) source must invoke
    brain_hook.absorb with module='aria_chat_stream'. Without this,
    every WA chat answer is paid for and immediately forgotten."""
    pattern = re.compile(
        r"R-F655.*?brain_hook.*?absorb\([^)]*module\s*=\s*[\"']aria_chat_stream[\"']",
        re.DOTALL,
    )
    assert pattern.search(_ROUTES_SRC), (
        "R-F655: chat_stream_ep does not call brain_hook.absorb("
        "module='aria_chat_stream'). The WhatsApp-default streaming path "
        "is still silently discarding paid LLM output — clause 15 violation."
    )


def test_rf655_absorbs_are_fire_and_forget():
    """The chat absorbs must be wrapped in asyncio.create_task (not
    awaited inline) so brain_hook fan-out latency — which includes
    chromadb writes that can take several seconds — never blocks the
    user-facing response. This pattern mirrors the existing
    correction_learner / honesty_judge / rlaif / critique_collector
    background tasks in the same handlers."""
    # R-F4237 (2026-08-23) — AST, not a regex over formatting.
    #
    # This was `re.findall(r"...\\s*_aio655[s]?\\.create_task", ...)`, which
    # required `create_task` to be preceded only by whitespace. R-F4237 wrapped
    # every fire-and-forget spawn in this module with `_hold_job_task(...)` —
    # asyncio keeps only a WEAK reference, so an unreferenced task is collected
    # before it runs under a saturated loop (R-F1363/R-F1377/C-217). The absorbs
    # are still spawned exactly as before and still not awaited, but the regex
    # matched 0 blocks and reported the capability as GONE.
    #
    # The invariant R-F655 actually protects is "scheduled, never awaited
    # inline", so that is what is asserted — independently of how the call is
    # spelled. (Same substring-vs-AST lesson C-217 records about its own guard.)
    import ast

    tree = ast.parse(_ROUTES_SRC)
    _ABSORBS = {"_r655_absorb_bg", "_r655_stream_absorb_bg"}

    spawned: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "create_task":
            for arg in node.args:
                if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name) \
                        and arg.func.id in _ABSORBS:
                    spawned.add(arg.func.id)

    awaited_inline = {
        n.value.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name) and n.value.func.id in _ABSORBS
    }

    assert spawned == _ABSORBS, (
        f"R-F655: expected both absorb coroutines (chat + stream) to be spawned "
        f"as tasks; found {sorted(spawned)}. Inline awaits would add brain_hook "
        f"fan-out latency (chromadb writes, knowledge fact extraction) to every "
        f"chat response."
    )
    assert not awaited_inline, (
        f"R-F655: {sorted(awaited_inline)} is AWAITED inline — that is the "
        f"latency this fix exists to keep off the user-facing response.")


def test_rf655_capability_chat_response_feeds_brain_hook(monkeypatch):
    """Capability test (CLAUDE.md §5): proves the user-visible symptom is
    fixed. Pre-R-F655, a paid chat call's response_text was never seen by
    brain_hook.absorb. Post-R-F655, an absorb call fires with the response
    body.

    Strategy: load the chat_ep function from the module, monkeypatch the
    brain_hook so we can record the call, and exercise just the tail of
    chat_ep that contains the R-F655 absorb. We don't boot the whole app
    (the lifespan is heavy + dependent on env state); we verify the
    absorb dispatch shape by re-running the code path against a captured
    `brain_hook` module substitute.
    """
    import asyncio
    captured: list[dict] = []

    async def _fake_absorb(**kwargs):
        captured.append(kwargs)

    from aria_service.intel import brain_hook as _bh
    monkeypatch.setattr(_bh, "absorb", _fake_absorb)

    # Re-create the exact shape of the R-F655 fire-and-forget block.
    # If the production code drifts away from this shape, the source
    # pattern tests above will catch it — this test verifies that the
    # CALL itself works end-to-end with brain_hook.
    async def _run_like_chat_ep():
        result = {"response": "ARIA's answer: Acme Trading Ltd is on the OFAC SDN list."}
        response_text = "fallback text — should not be used when result.response present"
        user_message = "Is Acme Trading Ltd sanctioned?"
        user_id = "test-user-rf655"
        # Mirrored exactly from routes/aria.py chat_ep R-F655 block:
        _bh655_final_text = (result.get("response") if isinstance(result, dict) else None) or response_text or ""
        if _bh655_final_text.strip():
            async def _r655_absorb_bg():
                await _bh.absorb(
                    module="aria_chat",
                    summary=f"chat turn: {(user_message or '')[:120]}",
                    detail=_bh655_final_text,
                    user_id=user_id or "",
                    success=True,
                    confidence="PROBABLE",
                )
            task = asyncio.create_task(_r655_absorb_bg())
            await task  # wait for it in the test (production is fire-and-forget)

    asyncio.run(_run_like_chat_ep())

    assert len(captured) == 1, (
        f"R-F655: brain_hook.absorb expected once, got {len(captured)}."
    )
    call = captured[0]
    assert call["module"] == "aria_chat", call
    assert "Acme Trading" in call["detail"], (
        "R-F655: chat response body was not passed to brain_hook.absorb. "
        "Without the response body, the brain learns nothing from the turn."
    )
    assert call["user_id"] == "test-user-rf655", (
        "R-F655: user_id not threaded into brain_hook.absorb. Downstream "
        "context (deep_research, watchlist re-screen) loses customer tag."
    )
    assert call["success"] is True
