"""R-F914 — brain→BD lead bridge: structured + cached /proactive/lead-hunt.

Operator-evidenced 2026-05-26: the BD Intelligence page showed 0 sales leads
because it only surfaced the (degraded) Node tender sweep, never the brain's
market intelligence. R-F914 adds `?structured=1` to lead-hunt: returns scored
lead cards (JSON), injects ARIA's scored-market opportunities into the prompt,
derives urgency from win_probability, and caches for 6h so the BD page (Node
side) can merge them cheaply. This tests the structured shape + cache, by
calling the handler directly with a stubbed LLM + intel (the repo's
test-the-logic-not-HTTP pattern).
"""
from __future__ import annotations

import asyncio
import types

from aria_service.routes import aria

_LLM_JSON = (
    '[{"market":"Angola","buyer":"Ministry of Defence","requirement":"wheeled APCs",'
    '"window":"Q3 budget cycle","angle":"tier-1 incumbent + Otokar","win_probability":75,'
    '"compliance_flags":"none","first_action":"call procurement director"},'
    '{"market":"Kenya","buyer":"National Police Service","requirement":"tactical UAVs",'
    '"window":"open RFI","angle":"tier-2 relationship","win_probability":40,'
    '"compliance_flags":"end-use cert","first_action":"email attache"}]'
)


class _StubLLM:
    is_configured = True

    async def complete(self, system, prompt, max_tokens=0, timeout=0):
        # The opportunity block must be injected into the prompt (intel-grounding).
        assert "Angola" in prompt, "scored opportunities should be injected into the prompt"
        return types.SimpleNamespace(text=_LLM_JSON)


def _mk_request(llm, current_data):
    app = types.SimpleNamespace(
        state=types.SimpleNamespace(llm_provider=llm, current_data=current_data))
    return types.SimpleNamespace(app=app)


def _patch_store(monkeypatch):
    import aria_service.intel.redis_store as rs
    store: dict = {}

    async def _get(k, *a, **kw):
        return store.get(k)

    async def _set(k, v, *a, **kw):
        store[k] = v
        return True

    monkeypatch.setattr(rs, "get_json", _get)
    monkeypatch.setattr(rs, "set_json", _set)
    return store


def test_rf914_structured_leads_shape_and_urgency(monkeypatch):
    _patch_store(monkeypatch)
    req = _mk_request(_StubLLM(), {
        "opportunities": [{"market": "Angola", "score": 80, "tier": 1}],
        "procurementTenders": {"items": []},
    })
    out = asyncio.run(aria.proactive_lead_hunt_ep(req, structured=True, refresh=True))
    leads = out["structured"]
    assert len(leads) == 2, out
    # urgency derived from win_probability (>=60 → HOT)
    assert leads[0]["urgency"] == "HOT", leads[0]
    assert leads[1]["urgency"] == "WARM", leads[1]
    assert leads[0]["market"] == "Angola"
    assert all(l["source"] == "brain_lead_hunt" for l in leads)
    assert out["cached"] is False


def test_rf914_second_call_is_cached(monkeypatch):
    _patch_store(monkeypatch)
    req = _mk_request(_StubLLM(), {"opportunities": [{"market": "Angola", "score": 80, "tier": 1}]})
    first = asyncio.run(aria.proactive_lead_hunt_ep(req, structured=True, refresh=True))
    assert first["cached"] is False
    # second call (no refresh) must hit the 6h cache, not the LLM
    second = asyncio.run(aria.proactive_lead_hunt_ep(req, structured=True, refresh=False))
    assert second["cached"] is True
    assert len(second["structured"]) == len(first["structured"]) == 2


def test_rf915_accepts_object_wrapper(monkeypatch):
    """R-F915 — DeepSeek often returns {"leads":[...]} despite asking for a bare
    array. Live 2026-05-26 that yielded 0 leads (no error). Must extract it."""
    _patch_store(monkeypatch)

    class _WrapLLM:
        is_configured = True
        async def complete(self, system, prompt, max_tokens=0, timeout=0):
            return types.SimpleNamespace(text=(
                '{"leads":[{"market":"Angola","buyer":"MoD","requirement":"APCs",'
                '"win_probability":80,"angle":"tier-1","window":"Q3",'
                '"compliance_flags":"none","first_action":"call"}]}'
            ))

    req = _mk_request(_WrapLLM(), {"opportunities": [{"market": "Angola", "score": 80, "tier": 1}]})
    out = asyncio.run(aria.proactive_lead_hunt_ep(req, structured=True, refresh=True))
    assert len(out["structured"]) == 1, out
    assert out["structured"][0]["market"] == "Angola"
    assert out["structured"][0]["urgency"] == "HOT"


def test_rf914_prose_path_unchanged(monkeypatch):
    """Default (non-structured) path keeps the legacy {"leads": <prose>} contract."""
    _patch_store(monkeypatch)

    class _ProseLLM:
        is_configured = True
        async def complete(self, system, prompt, max_tokens=0, timeout=0):
            return types.SimpleNamespace(text="1. Angola — APCs ...")

    req = _mk_request(_ProseLLM(), {"opportunities": []})
    out = asyncio.run(aria.proactive_lead_hunt_ep(req, structured=False))
    assert "leads" in out and isinstance(out["leads"], str)
    assert "structured" not in out
