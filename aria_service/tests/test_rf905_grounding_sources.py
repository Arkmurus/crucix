"""R-F905 — grounding keystone: chat-audit source threading + verifier
tool_context type fix.

Two latent bugs were starving the grounding metric:

1. Both chat callers pass a *dict* ``{"retrieved_sources": [...]}`` so
   ``chat_audit_log.record_chat`` can count provenance, but
   ``response_verifier.verify_and_tag_response`` expects a *string* and
   does ``re.findall(regex, tool_context)``. Passing the dict through made
   ``re.findall(regex, dict)`` raise ``TypeError``, swallowed by the broad
   ``except`` in ``_verify_and_record_chat`` → ``grounded_rate`` stayed
   ``None`` on every turn and the verifier-side grounding was silently dead.

2. The streaming path (WhatsApp default) passed ``tool_context=None``
   despite having populated the retrieved sources, so ``sources_count``
   was 0 on every stream turn (a §13 stream-bypass violation).

These tests pin the function contract that fixes (1): the verifier must
receive a STRING containing the retrieved URLs, while ``record_chat`` must
still receive the structured dict so provenance counting works.
"""
from __future__ import annotations

import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_dict_tool_context_reaches_verifier_as_string_with_urls(monkeypatch):
    """A dict tool_context must be converted to a STRING (carrying the
    source URLs) before reaching verify_and_tag_response — otherwise
    re.findall throws and grounding dies silently."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    seen_verifier_ctx: dict = {}
    seen_record_ctx: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        seen_verifier_ctx["value"] = tool_context
        # Mimic the real verifier's URL extraction so we'd surface a
        # TypeError if a non-str slipped through.
        import re
        seen_verifier_ctx["urls"] = re.findall(r"https?://[^\s]+", tool_context)
        return {"claims_checked": 3, "verified": 2, "unverified": 1, "contradicted": 0}

    async def fake_record_chat(**kwargs):
        seen_record_ctx.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    sources = [
        {"url": "https://ofac.treasury.gov/sdn", "title": "OFAC SDN list"},
        {"url": "https://sanctionsmap.eu/iran", "title": "EU sanctions map"},
    ]

    _run(ae._verify_and_record_chat(
        session_id="sess-rf905",
        user_message="Is this entity sanctioned?",
        response_text="[CONFIRMED] It appears on the OFAC SDN list. " * 4,
        tool_context={"retrieved_sources": sources},
        mastery_overall=0.8,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    # 1. Verifier received a STRING (not the dict) — the TypeError fix.
    assert isinstance(seen_verifier_ctx.get("value"), str), (
        "verify_and_tag_response must receive a str, got "
        f"{type(seen_verifier_ctx.get('value'))!r}"
    )
    # 2. The string carries both retrieved URLs so the verifier can ground.
    assert "https://ofac.treasury.gov/sdn" in seen_verifier_ctx["value"]
    assert "https://sanctionsmap.eu/iran" in seen_verifier_ctx["value"]
    assert len(seen_verifier_ctx["urls"]) == 2

    # 3. record_chat still received the STRUCTURED dict so sources_count works.
    rc = seen_record_ctx.get("tool_context")
    assert isinstance(rc, dict)
    assert rc.get("retrieved_sources") == sources

    # 4. grounded_rate was actually computed (2/3 ≈ 0.667), not swallowed to None.
    assert seen_record_ctx.get("grounded_rate") == 0.667
    assert seen_record_ctx.get("verification_status") == "grounded"


def test_string_tool_context_still_passes_through(monkeypatch):
    """Back-compat: a plain string tool_context (the documented type) must
    still reach the verifier unchanged."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    seen: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        seen["ctx"] = tool_context
        return {"claims_checked": 0, "verified": 0, "unverified": 0, "contradicted": 0}

    async def fake_record_chat(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    _run(ae._verify_and_record_chat(
        session_id="sess-rf905b",
        user_message="hi",
        response_text="A short hello with no claims to verify here at all.",
        tool_context="https://example.com/context source text",
        mastery_overall=0.8,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    assert seen.get("ctx") == "https://example.com/context source text"


def test_none_tool_context_yields_empty_string(monkeypatch):
    """None tool_context (a turn with no retrieval) must degrade to an
    empty string for the verifier, never crash."""
    from aria_service import aria_engine as ae
    from aria_service.intel import response_verifier as rv
    from aria_service.intel import chat_audit_log as cal

    seen: dict = {}

    async def fake_verify(response_text, tool_context="", session_id=""):
        seen["ctx"] = tool_context
        return {"claims_checked": 0, "verified": 0, "unverified": 0, "contradicted": 0}

    async def fake_record_chat(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(rv, "verify_and_tag_response", fake_verify)
    monkeypatch.setattr(cal, "record_chat", fake_record_chat)

    _run(ae._verify_and_record_chat(
        session_id="sess-rf905c",
        user_message="hi",
        response_text="A short hello with no retrieval and no claims here.",
        tool_context=None,
        mastery_overall=0.8,
        mastery_weak_topics=[],
        operating_mode="standard",
    ))

    assert seen.get("ctx") == ""
