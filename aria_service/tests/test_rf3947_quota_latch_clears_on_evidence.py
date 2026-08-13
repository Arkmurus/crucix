"""C-41 / R-F3947 — the quota latch could only ever move toward "spent".

THE SYMPTOM, measured live 2026-08-13. `/api/aria/sanctions/source/status`
reported `quota_exhausted: true, since 2026-07-31T23:04:22+00:00` — thirteen days
and one monthly boundary later — while the SAME machine, in the same minute,
screened "Rosoboronexport" straight through the OpenSanctions aggregate and got
a real hit back (opensanctions.org entity URL, 24 dataset slugs the local floor
does not hold). The API was answering. The status surface said it was spent.

That reading cost a wrong operator recommendation: "upgrade the OpenSanctions
plan". Nothing needed upgrading.

THE RECORD PRODUCTION HELD, which is the shape that hangs forever — no
`expires_at`, and (written before the TTL was added) no expiry on the key:

    {"since": "2026-07-31T23:04:22+00:00", "detail": "...", "action": "..."}

A REJECTED FIX, recorded because the next reader will reach for it too. The
first attempt derived the missing boundary from `since` via the module's own
`_next_month_start_utc()`. It works, and it is wrong:
`test_opensanctions_quota_flag_lapses` pins the opposite as a deliberate
decision — "silently flipping them to 'fine' would be inventing a reset nobody
observed" — and that author is right. An inference about a reset is weaker
evidence than the thing itself. Reverted.

THE ROOT CAUSE, which the fix below addresses instead:

   NOTHING CLEARS THE LATCH ON EVIDENCE, ONLY ON A MANUAL OPERATOR CALL.
   A monthly boundary is the EARLIEST a spent quota can become unspent, not the
   only way: an operator can upgrade the plan mid-month. A successful 200 from
   OpenSanctions is direct proof the quota is not spent — the same evidence
   class that SETS the flag (a 429 body). Setting on evidence and clearing only
   by hand is what makes a latch, and CLAUDE.md §17 already records this exact
   shape in the LLM billing cooldown (R-F3513): "a cooling provider is never
   called, so it sustains itself".

A 200 is not an inference about a reset; it is the reset, observed. It also
covers the case no boundary can — a plan upgraded mid-month.

WHAT THIS IS NOT: a TTL bump or a retry. Both would be the §1 band-aid — a guess
about time standing in for a fact that is directly observable on every call.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aria_service.intel import sanctions as s


async def _note_and_settle():
    """Schedule the clear and WAIT for it — production never waits (see below)."""
    task = await s._note_opensanctions_success()
    if task is not None:
        await task


# ── 1. The legacy record: no expires_at, past its monthly boundary ───────────

def _legacy_record(since: datetime) -> dict:
    """EXACTLY the shape live production holds — no expires_at, no TTL."""
    return {
        "since": since.isoformat(timespec="seconds"),
        "detail": '{"detail":"This API key has exceeded its rate limit for the month."}',
        "action": "operator: upgrade the OpenSanctions plan or wait for the monthly reset",
    }


@pytest.fixture
def state_holds(monkeypatch):
    """Point the reader at an in-memory record."""
    def _install(record):
        async def _get_json(key, *a, **kw):
            return record
        from aria_service.intel import redis_store as rs
        monkeypatch.setattr(rs, "get_json", _get_json)
    return _install


def test_a_legacy_record_is_NOT_lapsed_by_inference(state_holds):
    """The rejected fix, pinned so it is not re-attempted.

    A pre-expires_at record stays `exhausted` no matter how old it is. Deriving
    the boundary from `since` would report a reset nobody observed —
    test_opensanctions_quota_flag_lapses already decided this, with reasons, and
    this test agrees with it rather than quietly reversing it.

    What retires such a record is the 200 in section 2 below: evidence, not
    arithmetic about the calendar.
    """
    last_month = datetime.now(timezone.utc).replace(day=1) - timedelta(days=2)
    state_holds(_legacy_record(last_month))

    out = asyncio.run(s.get_opensanctions_quota_state())
    assert out["exhausted"] is True, (
        "a record with no expires_at must not be flipped to 'fine' by inferring "
        "a monthly reset — that invents an observation. The API answering is "
        "what clears it."
    )


def test_modern_record_is_unaffected(state_holds):
    """The expires_at path already worked and must keep working, untouched."""
    now = datetime.now(timezone.utc)
    state_holds({"since": now.isoformat(), "expires_at": (now + timedelta(days=9)).isoformat()})
    assert asyncio.run(s.get_opensanctions_quota_state())["exhausted"] is True

    state_holds({"since": now.isoformat(), "expires_at": (now - timedelta(days=1)).isoformat()})
    assert asyncio.run(s.get_opensanctions_quota_state())["exhausted"] is False


# ── 2. Clearing on the evidence that would have set it ──────────────────────

class _Recorder:
    def __init__(self):
        self.deletes = []

    async def delete(self, key, *a, **kw):
        self.deletes.append(key)
        return True


@pytest.fixture
def store(monkeypatch):
    rec = _Recorder()
    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "delete", rec.delete)
    s._reset_quota_recovery_latch()
    return rec


def test_a_successful_call_clears_the_exhaustion_record(store):
    """A 200 is PROOF the quota is not spent — the mirror of the 429 that set it.

    Without this the state can only be cleared by hand, so an operator who
    upgrades the plan mid-month keeps reading 'exhausted' indefinitely.
    """
    asyncio.run(_note_and_settle())

    assert s._QUOTA_STATE_KEY in store.deletes, (
        "a successful OpenSanctions response must retire the exhaustion record"
    )


def test_the_clear_happens_ONCE_per_process_not_per_call(store):
    """Bounded, or the fix becomes a store write on every screen (R-F2157)."""
    for _ in range(25):
        asyncio.run(_note_and_settle())

    assert len(store.deletes) == 1, (
        f"expected exactly one store op per recovery episode, got "
        f"{len(store.deletes)} — a per-call write is the self-DOS class this "
        f"repo has already been bitten by twice"
    )


def test_a_fresh_429_re_arms_the_latch(store, monkeypatch):
    """Recovery must not be permanent: the quota can be spent again next month."""
    asyncio.run(_note_and_settle())
    assert len(store.deletes) == 1

    async def _noop_set_json(*a, **kw):
        return True
    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "set_json", _noop_set_json)
    asyncio.run(s._record_quota_exhausted("exceeded its rate limit for the month"))

    asyncio.run(_note_and_settle())
    assert len(store.deletes) == 2, (
        "after a genuine re-exhaustion, the next success must clear it again"
    )


def test_recovery_reaches_the_brain_once(store, monkeypatch):
    """§21a — the RECOVERY is an outcome too, and it was unobservable.

    Once per episode, deliberately: a per-call signal would be the flood that
    has already filled the 500-slot capability ledger.
    """
    seen = []
    monkeypatch.setattr(s, "wire_success",
                        lambda **kw: seen.append(kw), raising=False)

    for _ in range(10):
        asyncio.run(_note_and_settle())

    assert len(seen) == 1, f"expected one recovery signal, got {len(seen)}"
    assert "opensanctions" in (seen[0].get("summary") or "").lower()


def test_a_store_failure_while_clearing_does_not_claim_recovery(store, monkeypatch):
    """If the delete fails, the latch must stay armed so we retry — never
    report a recovery we did not achieve."""
    async def _boom(*a, **kw):
        raise RuntimeError("store down")
    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "delete", _boom)

    asyncio.run(_note_and_settle())      # must not raise
    monkeypatch.setattr(rs, "delete", store.delete)
    asyncio.run(_note_and_settle())

    assert store.deletes, (
        "a failed clear must leave the latch armed so the next success retries; "
        "otherwise one store blip strands the record forever — which is the "
        "very defect being fixed"
    )



# ── 3. THE REGRESSION THIS SHIPPED WITH, pinned so it cannot return ─────────


def test_the_clear_never_blocks_the_screen(monkeypatch, store):
    """R-F3947's FIRST version awaited the store delete inline on the success
    branch of every OpenSanctions call. Measured live minutes after deploy:
    POST /sanctions/fuzzy went from sub-second to HTTP=000 at 150s, while the
    OpenSanctions API answered in 0.11s from the same machine. The sqlite writer
    is contended, and because a failed clear deliberately leaves the latch armed,
    every subsequent screen retried the same blocking write.

    Retiring a stale status flag is not part of producing a screen result and
    must not sit in its latency budget.
    """
    async def _glacial_delete(*a, **kw):
        await asyncio.sleep(30)          # a contended writer
        return True

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "delete", _glacial_delete)

    async def _drive():
        started = asyncio.get_running_loop().time()
        await s._note_opensanctions_success()
        return asyncio.get_running_loop().time() - started

    elapsed = asyncio.run(_drive())
    assert elapsed < 1.0, (
        f"the success path waited {elapsed:.1f}s on a slow store — this is the "
        f"live screening outage R-F3947 caused. Schedule the clear; never await "
        f"it on the request path."
    )


def test_only_one_clear_is_in_flight_at_a_time(monkeypatch, store):
    """Concurrent screens must not pile up tasks against a contended writer."""
    calls = []

    async def _slow_delete(*a, **kw):
        calls.append(1)
        await asyncio.sleep(0.05)
        return True

    from aria_service.intel import redis_store as rs
    monkeypatch.setattr(rs, "delete", _slow_delete)

    async def _drive():
        tasks = [await s._note_opensanctions_success() for _ in range(20)]
        for t in [t for t in tasks if t is not None]:
            await t

    asyncio.run(_drive())
    assert len(calls) == 1, f"expected one in-flight clear, got {len(calls)}"

# ── 3. The wiring: the success path must actually call it ──────────────────

def test_both_opensanctions_entry_points_note_success():
    """§3c — the fix is inert unless the real 200 branches invoke it."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(s))
    for fn_name in ("_opensanctions_match", "_opensanctions_search"):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        called = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_note_opensanctions_success" in called, (
            f"{fn_name} returns ok=True without retiring the exhaustion record, "
            f"so the latch stays armed while the API answers — the live symptom"
        )
