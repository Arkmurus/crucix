"""R-F1998 — LLM forensic-intent router capability tests.

Drives the REAL dispatch (forensic_intent.run) on the offline-safe sync
backends (Benford, TBML, FATF, economic substance, tier router) and the routing
logic (looks_forensic gate, interpret with a fake LLM, maybe_handle end-to-end).
The async/network primitives (sanctions divergence, crypto, citation, counter-
intel, provenance, RCA) are exercised only through the routing layer, not live,
to keep the test deterministic.
"""
import asyncio

from aria_service.intel import forensic_intent as fi


class _FakeResult:
    def __init__(self, text): self.text = text


class _FakeLLM:
    """Minimal stand-in for the chat LLM client: is_configured + async complete."""
    def __init__(self, reply: str, configured: bool = True):
        self.is_configured = configured
        self._reply = reply
        self.calls = 0

    async def complete(self, system, user, max_tokens=0, timeout=0):
        self.calls += 1
        return _FakeResult(self._reply)


# ── cheap gate ────────────────────────────────────────────────────────────────
def test_looks_forensic_gate():
    assert fi.looks_forensic("do these numbers look fabricated?")
    assert fi.looks_forensic("where is Wagner Group listed across sanctions lists?")
    assert fi.looks_forensic("screen this crypto wallet for me")
    assert fi.looks_forensic("which tier does student_quiz route to?")
    assert fi.looks_forensic("is this a shell?")   # R-F2000: bare "shell" recall
    # non-forensic chit-chat must NOT pay for an LLM classification
    assert not fi.looks_forensic("how are you today?")
    assert not fi.looks_forensic("what's the weather in London")
    assert not fi.looks_forensic("")


# ── interpret (LLM layer) ─────────────────────────────────────────────────────
def test_interpret_parses_tool_and_args():
    llm = _FakeLLM('{"tool":"benford","args":{"values":[1,2,3]},"confidence":0.9}')
    out = asyncio.run(fi.interpret("are these cooked?", llm))
    assert out["tool"] == "benford" and out["args"]["values"] == [1, 2, 3]
    assert out["confidence"] == 0.9


def test_interpret_none_when_not_forensic_or_unconfigured():
    # LLM says none
    llm = _FakeLLM('{"tool":"none","confidence":0.0}')
    assert asyncio.run(fi.interpret("hello", llm))["tool"] == "none"
    # unconfigured LLM → none, no call attempted
    off = _FakeLLM("{}", configured=False)
    assert asyncio.run(fi.interpret("benford please", off))["tool"] == "none"
    assert off.calls == 0
    # garbage output degrades to none, never raises
    bad = _FakeLLM("not json at all")
    assert asyncio.run(fi.interpret("benford please", bad))["tool"] == "none"


# ── run() dispatch — offline-safe sync backends ───────────────────────────────
def test_run_benford_real_backend():
    vals = [1, 1, 1, 2, 2, 3, 1, 4, 1, 5, 2, 1, 6, 1, 7, 2, 1, 8, 1, 9] * 4
    r = asyncio.run(fi.run("benford", {"values": vals}))
    assert r["ok"] and "Benford" in r["text"] and r["tool"] == "benford"


def test_run_tbml_classifier_real_backend():
    r = asyncio.run(fi.run("tbml", {"declared": 500000, "low": 50000, "high": 80000}))
    assert r["ok"] and "TBML" in r["text"]


def test_run_fatf_and_substance_real_backends():
    rf = asyncio.run(fi.run("fatf_typology", {"profile": {
        "jurisdictions": ["BVI"], "ubo_disclosure": "undisclosed", "payment_method": "USDT"}}))
    assert rf["ok"] and rf["tool"] == "fatf_typology" and rf["text"]
    rs = asyncio.run(fi.run("economic_substance", {"profile": {
        "employees": 2, "claimed_revenue_usd": 50000000, "paid_up_capital_usd": 1000,
        "directors_count": 1, "registered_address": "Suite 100, Regus, BVI"}}))
    assert rs["ok"] and rs["tool"] == "economic_substance" and rs["text"]


def test_run_tier_router_real_backend():
    r = asyncio.run(fi.run("tier_router", {"intent": "student_quiz"}))
    assert r["ok"] and "student_quiz" in r["text"]


# ── missing-arg → graceful "need X", never a crash ────────────────────────────
def test_run_missing_args_returns_needs_not_crash():
    r = asyncio.run(fi.run("sanctions_divergence", {}))
    assert r["ok"] is False and r.get("needs_args") and "need" in r["text"].lower()
    r2 = asyncio.run(fi.run("tbml", {"declared": 5}))   # low/high missing
    assert r2["ok"] is False and r2.get("needs_args")


# ── maybe_handle — the single chat-path hook, end to end ──────────────────────
def test_maybe_handle_routes_forensic_message():
    llm = _FakeLLM('{"tool":"benford","args":{"values":[1,2,3,4,5,6,7,8,9,1,2,3]},"confidence":0.85}')
    out = asyncio.run(fi.maybe_handle("do these figures look fabricated? 1 2 3 ...", llm))
    assert out and out["ok"] and out["tool"] == "benford"


def test_maybe_handle_skips_non_forensic_without_llm_call():
    llm = _FakeLLM('{"tool":"benford","confidence":1.0}')
    out = asyncio.run(fi.maybe_handle("how are you doing today?", llm))
    assert out is None and llm.calls == 0, "non-forensic must not pay for the LLM"


def test_maybe_handle_respects_confidence_floor():
    llm = _FakeLLM('{"tool":"benford","args":{"values":[1,2,3]},"confidence":0.2}')
    out = asyncio.run(fi.maybe_handle("benford on these maybe?", llm))
    assert out is None, "low-confidence classification must fall through to chat"


def test_registry_has_all_twelve():
    assert len(fi.PRIMITIVES) == 12
