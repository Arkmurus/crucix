"""R-F2447 — capability tests for Guardian Layer 3 multilingual tool intent.

Drives the REAL intent.interpret.interpret_tool path with a stub LLM (the LLM's
multilingual understanding is simulated; the module's job — parse, validate,
normalize, and above all FAIL SAFE — is what's under test). Asserts:
  * PT/ES/FR/DE phrasings route to the correct tool + extracted entity/topic,
  * a plain factual question -> None (never hijacks ordinary chat),
  * low confidence / empty entity / LLM error / unconfigured LLM -> None.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intent.interpret import interpret_tool


class _StubLLM:
    is_configured = True

    def __init__(self, mapping, *, raise_exc=False):
        self.mapping = mapping
        self.raise_exc = raise_exc

    async def complete(self, system, user, **kw):
        if self.raise_exc:
            raise RuntimeError("llm cooldown")
        text = self.mapping.get(user.strip(), '{"tool":"none","confidence":0.0}')

        class _R:
            pass
        r = _R()
        r.text = text
        return r


def _run(msg, llm, **kw):
    return asyncio.run(interpret_tool(msg, llm, **kw))


def test_multilingual_entity_routing():
    cases = {
        "investiga a empresa Acme Corp": ('{"tool":"investigate","arg":"Acme Corp","confidence":0.9}',
                                          "investigate", "Acme Corp"),
        "vérifie si la société KTRV est sanctionnée": ('{"tool":"screen","arg":"KTRV","confidence":0.88}',
                                                       "screen", "KTRV"),
        "haz una diligencia debida sobre Globex SA": ('{"tool":"dd_orchestrate","arg":"Globex SA","confidence":0.8}',
                                                      "dd_orchestrate", "Globex SA"),
        "erstelle ein Profil über Siemens": ('{"tool":"profile","arg":"Siemens","confidence":0.82}',
                                             "profile", "Siemens"),
    }
    llm = _StubLLM({k: v[0] for k, v in cases.items()})
    for msg, (_, tool, entity) in cases.items():
        out = _run(msg, llm)
        assert out is not None, msg
        assert out["tool"] == tool
        assert out["entity"] == entity
        assert out["_reason"] == "llm_intent_fallback_rf2447"


def test_query_tool_routing():
    llm = _StubLLM({"recherche sur le marché de la défense en Afrique":
                    '{"tool":"deep_research","arg":"defence market in Africa","confidence":0.75}'})
    out = _run("recherche sur le marché de la défense en Afrique", llm)
    assert out["tool"] == "deep_research"
    assert out["query"] == "defence market in Africa"


def test_plain_question_falls_through_to_none():
    llm = _StubLLM({"what is the capital of France": '{"tool":"none","confidence":0.0}'})
    assert _run("what is the capital of France", llm) is None


def test_low_confidence_falls_through():
    llm = _StubLLM({"maybe look into something":
                    '{"tool":"investigate","arg":"something","confidence":0.4}'})
    assert _run("maybe look into something", llm) is None


def test_entity_tool_with_empty_arg_is_rejected():
    llm = _StubLLM({"investiga": '{"tool":"investigate","arg":"","confidence":0.95}'})
    assert _run("investiga", llm) is None


def test_llm_error_is_failsafe_none():
    assert _run("investiga a Acme", _StubLLM({}, raise_exc=True)) is None


def test_unconfigured_or_missing_llm_is_none():
    class _Off:
        is_configured = False

        async def complete(self, *a, **k):
            raise AssertionError("must not be called")

    assert _run("investiga a Acme", _Off()) is None
    assert _run("investiga a Acme", None) is None


def test_confidence_threshold_is_tunable():
    llm = _StubLLM({"olha a Acme": '{"tool":"investigate","arg":"Acme","confidence":0.5}'})
    assert _run("olha a Acme", llm) is None                       # default 0.6
    assert _run("olha a Acme", llm, min_confidence=0.4) is not None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
