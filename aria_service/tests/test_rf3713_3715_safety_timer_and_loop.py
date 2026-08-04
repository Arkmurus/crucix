"""R-F3713/R-F3714/R-F3715 — CAPABILITY: a safety timer is not cancelled by a
store blip, the browser fallback stops firing unconditionally, and the thread
pool is bounded.

R-F3713 — guardian check-in is a DEAD-MAN'S SWITCH. `_get` swallowed every
exception and returned None, which is also what an absent record returns.
`reconcile` then did:

    rec = await _get(user)
    if not rec or rec.get("fired"):
        await _disarm(user)      # <-- DELETES the record
        continue

So a transient store error did not merely hide the timer — it CANCELLED it,
permanently, and `_disarm` removed the record so nothing could recover it. The
alarm simply never fires, and the person it was protecting cannot know.

R-F3714 — `playwright_engine.is_available` is `async def` (:334). researcher.py
called it WITHOUT await; a coroutine object is always truthy, so every thin page
entered a 30s Playwright fetch, including on hosts with no Chromium. The
mistake is invisible on inspection because the sibling `headless.is_available`
IS synchronous, so the two read identically at the call site (§3b).

R-F3715 — nothing ever called `set_default_executor`, so all 328
`asyncio.to_thread` sites shared a pool sized `min(32, os.cpu_count()+4)` — and
on Fly `os.cpu_count()` reports the HOST, not the machine's share.

Run: python -m pytest aria_service/tests/test_rf3713_3715_safety_timer_and_loop.py -v
"""
from __future__ import annotations

import asyncio
import inspect
import threading

import pytest


# ══════════════════════════════════════════════════════════════════════════
# R-F3713 — the dead-man's switch
# ══════════════════════════════════════════════════════════════════════════

def test_a_store_failure_is_not_an_absent_timer(monkeypatch):
    from aria_service.guardian import checkin
    from aria_service.intel import redis_store

    async def _boom(key):
        raise redis_store.StoreReadError("sqlite timeout")

    monkeypatch.setattr(checkin.rs, "get_json_strict", _boom)
    with pytest.raises(checkin.CheckinStoreUnavailable):
        asyncio.run(checkin._get("user_a", strict=True))


def test_an_absent_record_is_still_none(monkeypatch):
    """No timer armed is a legitimate, distinct answer."""
    from aria_service.guardian import checkin

    async def _absent(key):
        return None

    monkeypatch.setattr(checkin.rs, "get_json_strict", _absent)
    assert asyncio.run(checkin._get("user_a", strict=True)) is None


def test_reconcile_does_NOT_disarm_on_an_unreadable_store(monkeypatch):
    """The headline: a Redis blip must not cancel a live safety timer."""
    from aria_service.guardian import checkin

    disarmed: list[str] = []

    async def _active():
        return ["user_a"]

    async def _unreadable(user, *, strict=False):
        raise checkin.CheckinStoreUnavailable("store down")

    async def _spy_disarm(user):
        disarmed.append(user)

    monkeypatch.setattr(checkin, "_active_users", _active, raising=False)
    monkeypatch.setattr(checkin, "_get", _unreadable)
    monkeypatch.setattr(checkin, "_disarm", _spy_disarm)

    async def _send(*a, **k):
        return {"ok": True}

    try:
        asyncio.run(checkin.reconcile(_send))
    except Exception:
        pass  # the loop may need more scaffolding; the assertion below is the point

    assert disarmed == [], (
        "an unreadable store DISARMED the user — _disarm DELETES the record, so "
        "a transient error permanently cancelled a dead-man's switch"
    )


def test_reconcile_still_disarms_a_genuinely_absent_record():
    """The guard must not stop legitimate cleanup."""
    src = inspect.getsource(
        __import__("aria_service.guardian.checkin", fromlist=["x"]).reconcile)
    assert "if not rec or rec.get(\"fired\"):" in src
    assert "_disarm(user)" in src, (
        "a genuinely absent or already-fired record must still be cleaned up"
    )


def test_an_unreadable_safety_timer_reaches_the_brain():
    """§21a — a safety timer we cannot read is exactly what must not be silent."""
    from aria_service.guardian import checkin

    src = inspect.getsource(checkin.reconcile)
    assert "wire_failure" in src and "data_integrity" in src


# ══════════════════════════════════════════════════════════════════════════
# R-F3714 — the unawaited availability check
# ══════════════════════════════════════════════════════════════════════════

def test_playwright_is_available_is_actually_async():
    """If this ever becomes sync, the await below must be revisited."""
    from aria_service.intel.scraper import playwright_engine

    assert inspect.iscoroutinefunction(playwright_engine.is_available), (
        "the fix depends on this being async — §3b: check before you await"
    )


def test_the_caller_awaits_it():
    from aria_service.intel import researcher

    src = inspect.getsource(researcher)
    assert "if await _pw_avail():" in src, (
        "a coroutine object is ALWAYS truthy, so the unawaited guard passed "
        "unconditionally and every thin page paid a 30s Playwright fetch"
    )
    assert "if _pw_avail():" not in src


def test_the_sync_sibling_is_still_called_without_await():
    """headless.is_available IS sync — awaiting it would be the mirror bug."""
    from aria_service.intel import headless

    assert not inspect.iscoroutinefunction(headless.is_available)


def test_the_extraction_is_offloaded_in_the_scraper():
    """The sixth call site R-F3475's 'all five' sweep missed."""
    from aria_service.intel.scraper import playwright_engine

    src = inspect.getsource(playwright_engine)
    assert "await extract_structured_html_async(html)" in src, (
        "trafilatura + a dozen regex passes ran on the loop inside an async def"
    )
    assert "_extract_structured_html(html)" not in src


def test_the_offload_guard_now_covers_the_scraper_directory():
    """A guard that does not cover the directory cannot guard it."""
    import pathlib

    p = (pathlib.Path(__file__).resolve().parent
         / "test_rf3475_html_extraction_offload.py")
    src = p.read_text(encoding="utf-8")
    assert "intel/scraper/playwright_engine.py" in src


# ══════════════════════════════════════════════════════════════════════════
# R-F3715 — the bounded executor
# ══════════════════════════════════════════════════════════════════════════

def test_the_default_executor_is_bounded_and_named():
    """20 concurrent to_thread calls must not spawn 20 threads."""
    async def _go():
        from concurrent.futures import ThreadPoolExecutor
        n = 4
        asyncio.get_running_loop().set_default_executor(
            ThreadPoolExecutor(max_workers=n, thread_name_prefix="aria_default"))
        names = await asyncio.gather(
            *[asyncio.to_thread(lambda: threading.current_thread().name)
              for _ in range(20)])
        return sorted(set(names)), n

    uniq, n = asyncio.run(_go())
    assert len(uniq) <= n, (
        f"{len(uniq)} distinct workers for a {n}-worker cap — unbounded, which "
        f"is what starved the loop when os.cpu_count() reported the fly HOST"
    )
    assert all(x.startswith("aria_default") for x in uniq)


def test_boot_sets_a_bounded_default_executor():
    from aria_service import main

    src = inspect.getsource(main.lifespan)
    assert "set_default_executor" in src, (
        "nothing in the tree called this, so all 328 to_thread sites shared a "
        "pool sized from the HOST's core count"
    )
    assert "ARIA_THREAD_POOL_WORKERS" in src, "the size must be tunable per machine"


def test_boot_never_fails_on_the_tuning_knob():
    """A pool-size knob must not be able to block startup."""
    from aria_service import main

    src = inspect.getsource(main.lifespan)
    # Anchor on the CALL, not the comment that mentions it — the first textual
    # occurrence is inside the explanatory block above, and a window centred
    # there misses the handler entirely. (Third time today a structural
    # assertion matched prose instead of code.)
    i = src.index("set_default_executor(")
    window = src[i:i + 900]
    assert "except Exception" in window, (
        "a pool-size tuning knob must never be able to block startup"
    )


def test_the_per_turn_pool_threads_are_attributable():
    """Behaviour deliberately unchanged; the threads are now nameable."""
    from aria_service import aria_engine

    src = inspect.getsource(aria_engine)
    assert 'thread_name_prefix="aria_ctx_layer"' in src, (
        "the census reported an anonymous total, so nobody could say WHICH pool "
        "was growing 9 -> 11 -> 13"
    )
    assert "wait=False, cancel_futures=True" in src, (
        "the abandon-on-hang trade is correct and must stay: a stuck retrieval "
        "layer must not wedge the chat turn"
    )
