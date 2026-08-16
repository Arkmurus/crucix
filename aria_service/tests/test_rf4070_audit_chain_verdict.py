"""R-F4070 (C-111) — the audit trail's tamper-evidence check certified a chain
it had barely looked at, and a detected break never reached the brain.

Measured on aria-intel 2026-08-16:

    rows=1208   min seq=177   max seq=1922
      head lost before min seq : 176
      interior gaps            : 538
      total missing            : 714   (37.1%)

    GET /chat-audit/verify?sample=100  -> {"verified":true, "checked":100,"breaks":[]}
    GET /chat-audit/verify?sample=500  -> {"verified":false,"checked":500,
          "breaks":[{"index":409,"expected_prev":"a220b59a…","actual_prev":"0d26aaa1…"}]}

The brain page showed `Total Entries 1208 · Head Hash b664de09858c… · Retention
36500 days` — three rows that together read as an intact, permanent,
tamper-evident record. The module header states the intent explicitly:
*"Compliance-grade audit logs must not self-delete; HMAC chain integrity also
degrades if entries vanish from the tail."* Both halves of that sentence had
already happened, and nothing on the page checked.

Three faults in `verify_chain`, all of the same family:

1. **`verified: True` on an EMPTY log.** Zero entries returned
   `{"verified": True, "checked": 0}` — an audit trail with nothing in it
   certifying itself. The §1 "certified by an absence" shape, on the one
   surface whose entire job is to be un-fakeable.

2. **A whole-chain verdict from a partial sample.** The default depth is 100 of
   1208, and the caller cannot tell coverage from the field name. That default
   is what made the live break invisible: the damage begins below it.

3. **A detected break reported SUCCESS to the brain.** `wire_success` was called
   unconditionally before the return, `wire_failure` was imported and **never
   called**. So the one event this module exists to detect was dark by §21a.

`verified` keeps its literal meaning (no break in the span examined) and can no
longer be read as a whole-chain claim: `complete` says whether the whole log was
covered and `verdict` is one of `intact` / `broken` / `partial_ok` /
`unverifiable`. Only `intact` means the chain is sound.

Note the gaps need no separate detector: 714 missing entries break the
prev_hash → chain_hash linkage of their surviving neighbours, which is exactly
the break reported at index 409.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


def _chain(n: int, *, break_at: int | None = None) -> list[str]:
    """Build a well-formed chain and return it NEWEST FIRST, the order `lpush`
    produces and `get_recent` returns.

    `break_at` is an index into that returned newest-first list, so it matches
    the index `verify_chain` reports and the one the live API printed
    (`{"index":409}` against `?sample=500`). Passing a build-order index here
    is a mistake that silently tests the wrong link.
    """
    entries = []
    prev = "0" * 64
    for i in range(n):
        chain_hash = f"h{i:04d}"
        entries.append({"prev_hash": prev, "chain_hash": chain_hash,
                        "timestamp": f"2026-08-16T{i % 24:02d}:00:00Z"})
        prev = chain_hash
    newest_first = list(reversed(entries))
    if break_at is not None:
        newest_first[break_at]["prev_hash"] = "tampered"
    return [json.dumps(e) for e in newest_first]


class _Wires:
    def __init__(self):
        self.success = []
        self.failure = []


@pytest.fixture
def wires(monkeypatch):
    from aria_service.intel import engine_wiring as ew
    w = _Wires()
    monkeypatch.setattr(ew, "wire_success",
                        lambda **kw: w.success.append(kw))
    monkeypatch.setattr(ew, "wire_failure",
                        lambda **kw: w.failure.append(kw))
    return w


async def _verify(raw: list[str], sample: int = 100, total: int | None = None):
    from aria_service.intel import chat_audit_log as cal
    from aria_service.intel import redis_store as rs

    async def _lrange(key, start, stop):
        return raw[start: (stop + 1) if stop >= 0 else None]

    async def _llen(key):
        return len(raw) if total is None else total

    with patch.object(rs, "lrange", _lrange), patch.object(rs, "llen", _llen):
        return await cal.verify_chain(sample)


# ── 1. an empty log cannot certify itself ──────────────────────────────────

@pytest.mark.asyncio
async def test_empty_log_is_unverifiable_not_verified(wires):
    res = await _verify([])
    assert res["verdict"] == "unverifiable", res
    assert res["verified"] is not True, (
        "an audit trail with zero entries must not report verified:true — "
        f"that is a claim built on an absence. {res}")


# ── 2. a partial sample is not a whole-chain verdict ───────────────────────

@pytest.mark.asyncio
async def test_partial_sample_does_not_claim_the_whole_chain(wires):
    """The live default: 100 checked of 1208, with the break at 409."""
    res = await _verify(_chain(100), sample=100, total=1208)
    assert res["complete"] is False, res
    assert res["verdict"] == "partial_ok", (
        "no break in the first 100 of 1208 is not an intact chain; the live "
        f"break sat at index 409. {res}")
    assert res["total_entries"] == 1208
    assert res["checked"] == 100


@pytest.mark.asyncio
async def test_full_coverage_with_no_breaks_is_intact(wires):
    res = await _verify(_chain(40), sample=100, total=40)
    assert res["complete"] is True, res
    assert res["verdict"] == "intact", res
    assert res["verified"] is True


# ── 3. a break must reach the brain, and must not report success ───────────

@pytest.mark.asyncio
async def test_a_break_is_reported_as_a_failure(wires):
    res = await _verify(_chain(60, break_at=20), sample=100, total=60)
    assert res["verified"] is False, res
    assert res["verdict"] == "broken", res
    assert res["breaks"], res
    assert wires.failure, (
        "a tamper-evidence break is the one event this module exists to "
        "detect and it never reached the brain: wire_failure was imported "
        "and never called (§21a)")
    assert not wires.success, (
        "wire_success fired unconditionally before the return, so a BROKEN "
        "chain reported success")


@pytest.mark.asyncio
async def test_an_intact_chain_still_reports_success(wires):
    """The success branch must survive — a wire that only ever fails is as
    useless as one that only ever succeeds."""
    await _verify(_chain(30), sample=100, total=30)
    assert wires.success, "the success wire was lost"
    assert not wires.failure


# ── 4. the break the live system actually has ──────────────────────────────

@pytest.mark.asyncio
async def test_deep_break_is_found_when_coverage_reaches_it(wires):
    """sample=100 said verified:true; sample=500 found the break at 409."""
    shallow = await _verify(_chain(100), sample=100, total=500)
    assert shallow["verdict"] == "partial_ok"

    deep = await _verify(_chain(500, break_at=409), sample=500, total=500)
    assert deep["verdict"] == "broken", deep
    assert deep["breaks"][0]["index"] == 409, deep["breaks"][:2]


# ── 5. the panel must look deep enough to reach the break ──────────────────

def test_panel_depth_reaches_the_known_break():
    """R-F4075 — found by live-smoking the deployed R-F4070 fix, not by
    inspection.

    The verdict was honest about coverage and I had told it to cover less than
    the damage: the aggregate requested sample=200 while the live break sits at
    index 411, so the panel reported `partial_ok` on a chain a full check calls
    `broken` (breaks at 411 and at 530, the latter a restart to the genesis
    hash). A tamper-evidence check whose default depth sits above the break is
    the same defect R-F4070 fixed, one layer out.

    5000 covers the whole live log (1210 entries) so `complete` is true and the
    verdict is real; if the log outgrows it the verdict degrades to
    `complete: false` and the panel says "N of M checked" rather than going
    quietly shallow.
    """
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[2]
    routes = (repo / "aria_service" / "routes" / "aria.py").read_text(
        encoding="utf-8")
    page = (repo / "public" / "aria-brain.html").read_text(encoding="utf-8")

    m = re.search(r'"/chat-audit/verify\?sample=(\d+)"', routes)
    assert m, "the chain verdict is no longer served from the aggregate"
    depth = int(m.group(1))
    assert depth >= 1500, (
        f"panel depth {depth} — the live log holds 1210 entries with a break at "
        "411; a depth below the log lets the panel report partial_ok on a "
        "broken chain")

    assert f"/chat-audit/verify?sample={depth}" in page, (
        "the page and the aggregate registry must request the SAME path or the "
        "panel silently falls back to a direct probe at a different depth")
