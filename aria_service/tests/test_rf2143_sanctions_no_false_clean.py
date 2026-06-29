"""R-F2143 — the sanctions guard must NEVER assert CLEAN / "answer NO" when the
live screen did not actually run (source down / rate-limited / breaker-open).

For a compliance product a false "not sanctioned" is the worst possible output:
the customer makes a decision on an authoritative answer that was never screened.
R-F1696 already distinguishes a real empty result (`screened=True`) from a
source-unavailable one (`screened=False` / `source_unavailable=True`); before
R-F2143 the guard ignored that and emitted an authoritative CLEAN either way.

These drive the REAL guard (`live_primary_check`) and the chat entry point
(`guard_context_block`).
"""
import asyncio

import aria_service.intel.sanctions_claim_guard as SCG
import aria_service.intel.sanctions as SANC
import aria_service.intel.brain_hook as BH


async def _noop_absorb(*a, **k):
    return None


def _patch(monkeypatch, screen_fn):
    monkeypatch.setattr(SANC, "fuzzy_screen", screen_fn)
    monkeypatch.delattr(SANC, "screen_with_aliases", raising=False)
    monkeypatch.setattr(BH, "absorb", _noop_absorb)


def test_rf2143_source_unavailable_is_not_clean(monkeypatch):
    """Source down: matches=[] BUT screened=False → COULD_NOT_VERIFY, never CLEAN."""
    async def _down(name, **kw):
        return {"name": name, "matches": [], "screened": False,
                "source_unavailable": True, "error": "sanctions_source_unavailable"}
    _patch(monkeypatch, _down)

    res = asyncio.run(SCG.live_primary_check("Acme Holdings Ltd"))
    assert res["verdict"] == "COULD_NOT_VERIFY", res["verdict"]
    block = res["citation_block"]
    assert "answer NO" not in block, "must NOT instruct the LLM to answer NO"
    assert "MUST NOT answer" in block and "UNVERIFIED" in block


def test_rf2143_real_clean_still_clean(monkeypatch):
    """Source answered with zero matches → genuinely CLEAN (authoritative NO ok)."""
    async def _clean(name, **kw):
        return {"name": name, "matches": [], "screened": True,
                "source_unavailable": False}
    _patch(monkeypatch, _clean)

    res = asyncio.run(SCG.live_primary_check("Definitely Clean Co"))
    assert res["verdict"] == "CLEAN", res["verdict"]
    assert "answer NO" in res["citation_block"]


def test_rf2143_hit_still_hit(monkeypatch):
    async def _hit(name, **kw):
        return {"name": name, "screened": True, "source_unavailable": False,
                "matches": [{"name": "Bad Actor", "score": 0.95,
                             "jurisdictions": [{"code": "us", "label": "US OFAC SDN"}]}]}
    _patch(monkeypatch, _hit)

    res = asyncio.run(SCG.live_primary_check("Bad Actor"))
    assert res["verdict"] == "HIT", res["verdict"]


def test_rf2143_chat_entry_unavailable_not_no(monkeypatch):
    """The real chat path: a sanctions yes/no question with the source down must
    NOT produce a block that tells the LLM to answer NO."""
    async def _down(name, **kw):
        return {"name": name, "matches": [], "screened": False,
                "source_unavailable": True, "error": "breaker_open"}
    _patch(monkeypatch, _down)

    block = asyncio.run(SCG.guard_context_block("is Acme Holdings Ltd sanctioned?"))
    assert block, "a sanctions yes/no question must produce a guard block"
    assert "answer NO" not in block
    assert ("MUST NOT answer" in block) or ("do NOT answer" in block)
