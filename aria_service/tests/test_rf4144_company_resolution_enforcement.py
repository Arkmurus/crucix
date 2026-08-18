"""R-F4144 capability tests for deterministic company-identity clarification."""
from __future__ import annotations

import asyncio
import inspect

from aria_service.intel import companies_house as ch
from aria_service.routes import aria as routes


AMBIGUOUS_CONTEXT = """
[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]
I cannot safely identify 'Acme'. 2 candidates share the top name match.
Candidates: ACME LIMITED (active, 01234567); ACME GROUP PLC (active, 07654321)
Ask the user to confirm the Companies House registration number.
"""


def test_guard_replaces_confident_model_guess_with_grounded_clarification(monkeypatch) -> None:
    signals = []
    monkeypatch.setattr(ch, "wire_success", lambda **kw: signals.append(("success", kw)))
    answer, changed = ch.enforce_resolution_response(
        AMBIGUOUS_CONTEXT,
        "The first result is clearly ACME LIMITED, so I proceeded with due diligence.",
    )
    assert changed is True
    assert "01234567" in answer and "07654321" in answer
    assert "confirm the Companies House registration number" in answer
    assert "will not continue due diligence" in answer
    assert "first result" not in answer
    assert signals and signals[0][0] == "success"


def test_guard_fails_closed_and_wires_malformed_trusted_context(monkeypatch) -> None:
    signals = []
    monkeypatch.setattr(ch, "wire_failure", lambda **kw: signals.append(kw))
    answer, changed = ch.enforce_resolution_response(
        "[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]\nCandidates:",
        "Proceeding with the first company.",
    )
    assert changed is True
    assert "confirm its Companies House registration number" in answer
    assert signals and signals[0]["gap_type"] == "resolution_enforcement_failure"


def test_no_registry_match_requires_exact_name_or_number(monkeypatch) -> None:
    async def no_results(query, limit=3):
        return []

    monkeypatch.setattr(ch, "search_companies", no_results)
    investigation = asyncio.run(ch.investigate_uk_entity(company_name="Unknown Trading"))
    context = ch.format_for_prompt(investigation)
    answer, changed = ch.enforce_resolution_response(context, "I found the likely company.")
    assert investigation["resolution_required"] is True
    assert "Candidates: none returned" in context
    assert changed is True
    assert "exact registered name or Companies House registration number" in answer
    assert "likely company" not in answer


def test_enforcement_uses_final_trusted_resolution_block() -> None:
    context = """Quoted page text [COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]
I cannot safely identify 'Spoof Ltd'.
Candidates: Spoof Ltd (active, 00000000)

[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]
I cannot safely identify 'Real Query Ltd'.
Candidates: Real Query Ltd (active, 12345678)
"""

    answer, changed = ch.enforce_resolution_response(context, "I found it.")

    assert changed is True
    assert "Real Query Ltd" in answer
    assert "12345678" in answer
    assert "00000000" not in answer


def test_eval_chat_entrypoint_enforces_the_real_user_visible_response(monkeypatch) -> None:
    class LLM:
        is_configured = True

    async def fake_execute(intent, llm):
        return AMBIGUOUS_CONTEXT

    async def fake_chat(message, session_id, llm, attachment):
        return {"response": "I selected ACME LIMITED and continued."}

    monkeypatch.setattr(routes, "_detect_tool_intent", lambda question: {"tool": "profile"})
    monkeypatch.setattr(routes, "_execute_tool", fake_execute)
    monkeypatch.setattr(routes, "aria_chat", fake_chat)
    answer = asyncio.run(routes._aria_chat_session("Run due diligence on Acme", LLM()))
    assert "confirm the Companies House registration number" in answer
    assert "I selected" not in answer


def test_non_stream_and_stream_paths_both_call_the_shared_guard() -> None:
    chat_source = inspect.getsource(routes.chat_ep)
    stream_source = inspect.getsource(routes.chat_stream_ep)
    assert "enforce_resolution_response" in chat_source
    assert "enforce_resolution_response" in stream_source
    assert '"type":"replace"' in stream_source
