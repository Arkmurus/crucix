"""R-F953 — daily contract-review self-check canary.

Runs a synthetic contract review end-to-end and flags the brain if it truncates /
empties / regresses. The governing-law clause is placed PAST the ~16K char point
that the R-F949 bug truncated at, so a regression of that class is caught before
the operator hits it.
"""
from __future__ import annotations

import asyncio


def test_rf953_governing_law_sits_past_16k():
    from aria_service.intel import contract_intelligence as ci
    doc = ci.build_selfcheck_contract()
    off = doc.find("GOVERNING LAW AND DISPUTE")
    assert off > 16000, f"governing-law clause must sit past 16K to exercise truncation, got {off}"


def test_rf953_ok_when_review_reaches_governing_law(monkeypatch):
    from aria_service.intel import contract_intelligence as ci
    import aria_service.aria_engine as ae

    async def _chat(msg, sid, llm, intel=None, **kw):
        return {"response": "Full review. GOVERNING LAW: England and Wales, LCIA London. " + "x" * 400}
    monkeypatch.setattr(ae, "aria_chat", _chat)

    class _LLM:
        is_configured = True
    r = asyncio.run(ci.run_contract_selfcheck(_LLM()))
    assert r["ok"] is True
    assert r["reaches_governing_law"] is True


def test_rf953_flags_brain_on_truncation(monkeypatch):
    from aria_service.intel import contract_intelligence as ci
    from aria_service.intel import brain_hook as bh
    import aria_service.aria_engine as ae

    async def _chat(msg, sid, llm, intel=None, **kw):
        # truncated review — never reaches governing law
        return {"response": "The excerpt ends at Clause 3. I cannot see further."}
    monkeypatch.setattr(ae, "aria_chat", _chat)

    flagged = {}
    async def _obs(event, detail="", *, success=True, gap_type=None):
        flagged["event"] = event
        flagged["success"] = success
        return {}
    monkeypatch.setattr(bh, "observe_self_event", _obs)

    class _LLM:
        is_configured = True
    r = asyncio.run(ci.run_contract_selfcheck(_LLM()))
    assert r["ok"] is False
    assert flagged.get("event") == "contract_review_selfcheck_failed"
    assert flagged.get("success") is False


def test_rf953_skips_without_llm():
    from aria_service.intel import contract_intelligence as ci
    r = asyncio.run(ci.run_contract_selfcheck(None))
    assert r["ok"] is False and r.get("skipped") == "no_llm"
