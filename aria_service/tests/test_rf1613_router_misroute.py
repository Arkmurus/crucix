"""R-F1613 — §22a query-misroute regression test.

Pre-fix bug: Stage 4's _investigate_keywords included interrogatives
(who.?is / what.?is / tell.?me.?about). "Who is the CEO of BAE Systems?
Use web search." matched has_investigate + has_company ('systems') so
investigate_company fired and returned "No findings" — silently discarding
the explicit web-search instruction.

These tests drive the SHIPPING try_local_reasoning and assert:
  1. An interrogative + "use web search" question does NOT route to
     company_investigator (source != 'company_investigator',
     intent != 'company_investigation') — it escalates.
  2. An IMPERATIVE ("investigate Acme Corp Ltd") still reaches Stage 4
     and CAN route to company_investigator (the path is not broken).

On the PRE-FIX code path, test (1) would FAIL: investigate_company would
be invoked and (because we mock it to return findings) the result source
would be 'company_investigator'. After the fix, Stage 4 is skipped for
the interrogative/web-search query, so the result escalates.
"""
import pytest

from aria_service.intel import reasoning_router
from aria_service.intel import company_investigator


class _FakeFinding:
    category = "registry"


class _FakeResult:
    def __init__(self):
        self.findings = [_FakeFinding()]
        self.summary = "Mocked investigation summary."
        self.risk_indicators = []
        self.entity_name = "Acme Corp Ltd"
        self.canonical_name = "Acme Corp Ltd"
        self.sources_cited = ["https://example.com"]
        self.duration_ms = 1


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Neutralise earlier stages + side effects so Stage 4 logic is the only
    thing that decides the outcome, and nothing touches the network."""

    # Earlier stages must NOT match so we reach Stage 4 / escalation.
    def _no_sym(question, silent_brain_hook=False):
        return {"confident": False, "confidence": 0.0, "intent": None}
    monkeypatch.setattr(reasoning_router.symbolic_reasoner, "reason", _no_sym)

    async def _no_lib(question, threshold=0.0):
        return {"match": False, "score": 0.0, "method": "none"}
    monkeypatch.setattr(reasoning_router.reasoning_library, "find_match", _no_lib)

    async def _no_local(question):
        return {"answered": False, "intent": None}
    monkeypatch.setattr(reasoning_router.local_brain, "try_local_response", _no_local)

    # grounded_reasoner.reason is imported inside the function; patch at source.
    from aria_service.intel import grounded_reasoner

    class _Abstain:
        abstained = True
        answer = None
        claims = []
        duration_ms = 1

    async def _no_gr(message, context=None):
        return _Abstain()
    monkeypatch.setattr(grounded_reasoner, "reason", _no_gr)

    # Ollama must report not-ready so Stage 5 doesn't short-circuit.
    async def _no_ollama():
        return None
    monkeypatch.setattr(reasoning_router, "_check_ollama_reasoning", _no_ollama)

    # No-op stats persistence.
    async def _no_record(source):
        return None
    monkeypatch.setattr(reasoning_router, "_record_routing", _no_record)


@pytest.mark.asyncio
async def test_interrogative_web_search_does_not_misroute(monkeypatch):
    """The operator's actual path: an interrogative with an explicit web-search
    instruction must NOT be routed to the company investigator."""
    called = {"n": 0}

    async def _spy_ic(name):
        called["n"] += 1
        return _FakeResult()

    monkeypatch.setattr(company_investigator, "investigate_company", _spy_ic)

    result = await reasoning_router.try_local_reasoning(
        "Who is the CEO of BAE Systems? Use web search."
    )

    # The misroute is fixed: investigator must not have answered.
    assert result.get("source") != "company_investigator", result
    assert result.get("intent") != "company_investigation", result
    # And the investigator must not even have been invoked.
    assert called["n"] == 0, "investigate_company should be skipped for web-search query"
    # It escalates instead of answering locally.
    assert result.get("answered") is False
    assert result.get("escalate_to") == "cloud_llm"


@pytest.mark.asyncio
async def test_imperative_investigate_still_reaches_stage4(monkeypatch):
    """Regression: a genuine imperative investigation request still reaches
    Stage 4 and CAN route to the company investigator."""
    called = {"n": 0}

    async def _spy_ic(name):
        called["n"] += 1
        return _FakeResult()

    monkeypatch.setattr(company_investigator, "investigate_company", _spy_ic)

    result = await reasoning_router.try_local_reasoning("Investigate Acme Corp Ltd")

    # No exception, and Stage 4 was exercised (investigator invoked).
    assert called["n"] == 1, "imperative investigation should reach Stage 4"
    assert result.get("source") == "company_investigator", result
    assert result.get("intent") == "company_investigation", result
