"""R-F2418 — crypto wallet screen must NOT read an UNAVAILABLE index as clean.

`screen_wallet()` returns [] for BOTH "no match" AND "index unavailable"; the
two callers (the forensic chat tool and the DD deterministic-primitives phase)
narrated [] as "no match" — a false clean. Post-fix both use
`screen_wallet_checked` and honour `source_unavailable`.

This drives the REAL forensic dispatcher (`forensic_intent.run`). The DD-path
caller shares the identical `screen_wallet_checked` contract (asserted by code
read; driving the full orchestrator is out of scope for a unit capability test).
"""
import asyncio

import aria_service.intel.crypto_sanctions as _cs
from aria_service.intel import forensic_intent

_ADDR = "0x1234567890abcdef1234567890abcdef12345678"


async def _run_tool(checked_return):
    async def _fake_checked(address):
        return dict(checked_return)
    orig = _cs.screen_wallet_checked
    _cs.screen_wallet_checked = _fake_checked  # type: ignore[assignment]
    try:
        return await forensic_intent.run("crypto_wallet", {"address": _ADDR})
    finally:
        _cs.screen_wallet_checked = orig  # type: ignore[assignment]


def _body(res):
    return str((res or {}).get("body") or res or "")


def test_unavailable_index_is_not_clean():
    res = asyncio.run(_run_tool(
        {"screened": False, "source_unavailable": True, "matched": False, "hits": []},
    ))
    body = _body(res)
    assert "COULD NOT VERIFY" in body, body
    assert "No sanctions match" not in body, "unavailable index must NOT be narrated as no-match"


def test_true_no_match_still_reads_clean():
    res = asyncio.run(_run_tool(
        {"screened": True, "source_unavailable": False, "matched": False, "hits": []},
    ))
    body = _body(res)
    assert "No sanctions match" in body, body
    assert "COULD NOT VERIFY" not in body


def test_hit_is_surfaced():
    res = asyncio.run(_run_tool(
        {"screened": True, "source_unavailable": False, "matched": True,
         "hits": [{"entity_name": "OFAC Entity", "chain": "eth", "topics": ["sanction"]}]},
    ))
    body = _body(res)
    assert "match(es)" in body and "OFAC Entity" in body, body


if __name__ == "__main__":
    test_unavailable_index_is_not_clean()
    print("PASS test_unavailable_index_is_not_clean")
    test_true_no_match_still_reads_clean()
    print("PASS test_true_no_match_still_reads_clean")
    test_hit_is_surfaced()
    print("PASS test_hit_is_surfaced")
    print("ALL PASS")
