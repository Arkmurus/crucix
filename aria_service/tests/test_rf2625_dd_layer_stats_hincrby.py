"""R-F2625 — the DD per-layer stats metric was DARK: redis_store.hincrby did not exist.

THE BUG (found by a §21a wiring-coverage audit):
  * WRITER  dd_orchestrator.py:8167 (the R-F1914 finalizer block):
        await _rs.hincrby(f'crucix:dd:layer_stats:{layer_name}', status, 1)
    `redis_store.hincrby` DID NOT EXIST -> AttributeError -> swallowed by the
    block's `except Exception: pass` -> the counter was NEVER written.
  * READER  routes/aria.py:1699 (the DD health endpoint):
        raw = await rs.hgetall(f"crucix:dd:layer_stats:{layer_name}")
    `hgetall` DOES exist, so it returned {} for all 11 layers, forever.

Net effect: the DD health surface reported EMPTY stats for every layer, which
reads as "no failures" when the truth is "never recorded" — a false-clean of
the observability surface itself, and DARK per CLAUDE.md §21a.

This is a REPEAT of R-F2486, where `hget` was likewise missing and
dd_trigger_pipeline's AttributeError was swallowed (the guard failed OPEN).

These tests drive the ACTUAL writer->reader chain (§3c), not a helper.
"""
import inspect

import pytest

from aria_service.intel import redis_store as rs

_LAYER_KEY = "crucix:dd:layer_stats:__rf2625_test_layer"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    try:
        await rs.delete(_LAYER_KEY)
    except Exception:
        pass


async def test_rf2625_hincrby_exists_and_is_async():
    """The exact §3b check that would have caught this before it shipped."""
    fn = getattr(rs, "hincrby", None)
    assert callable(fn), (
        "redis_store.hincrby does not exist — dd_orchestrator.py:8167 calls it, "
        "so every DD finalizer raises AttributeError into an `except: pass`"
    )
    assert inspect.iscoroutinefunction(fn), "hincrby must be async (callers await it)"


async def test_rf2625_dd_layer_stats_writer_to_reader_chain():
    """THE CAPABILITY TEST — the operator-visible symptom.

    Replays the writer's exact call (dd_orchestrator.py:8167) and then the DD
    health endpoint's exact read (routes/aria.py:1699). Before the fix the write
    raised AttributeError and the read returned {}.
    """
    # writer — verbatim shape from the R-F1914 finalizer block
    await rs.hincrby(_LAYER_KEY, "error", 1)
    await rs.hincrby(_LAYER_KEY, "ok", 1)

    # reader — verbatim shape from the DD health endpoint
    raw = await rs.hgetall(_LAYER_KEY)

    assert raw, (
        "DD health endpoint still sees EMPTY layer stats — the per-layer metric "
        "is dark, so a failing layer is indistinguishable from a healthy one"
    )
    norm = {
        (k.decode() if isinstance(k, bytes) else k): int(v)
        for k, v in raw.items()
    }
    assert norm.get("error") == 1, f"error counter not recorded: {norm}"
    assert norm.get("ok") == 1, f"ok counter not recorded: {norm}"


async def test_rf2625_hincrby_accumulates():
    """A counter must count — repeated layer failures must add up."""
    await rs.hincrby(_LAYER_KEY, "error", 1)
    await rs.hincrby(_LAYER_KEY, "error", 1)
    await rs.hincrby(_LAYER_KEY, "error", 2)

    raw = await rs.hgetall(_LAYER_KEY)
    norm = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}
    assert norm.get("error") == 4, f"expected 4 accumulated, got {norm}"


async def test_rf2625_hincrby_does_not_clobber_sibling_fields():
    """NON-REGRESSION: incrementing one status must not wipe the others.

    The finalizer increments a DIFFERENT field per run (ok/error/timeout) on the
    SAME hash key, so a read-modify-write that replaced the hash would destroy
    the other statuses and silently under-report failures.
    """
    await rs.hincrby(_LAYER_KEY, "ok", 3)
    await rs.hincrby(_LAYER_KEY, "timeout", 1)
    await rs.hincrby(_LAYER_KEY, "error", 2)

    raw = await rs.hgetall(_LAYER_KEY)
    norm = {(k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()}
    assert norm.get("ok") == 3, f"sibling field 'ok' lost: {norm}"
    assert norm.get("timeout") == 1, f"sibling field 'timeout' lost: {norm}"
    assert norm.get("error") == 2, f"sibling field 'error' lost: {norm}"


async def test_rf2625_returns_new_value():
    """Mirrors Redis HINCRBY semantics — returns the post-increment value."""
    v1 = await rs.hincrby(_LAYER_KEY, "ok", 1)
    v2 = await rs.hincrby(_LAYER_KEY, "ok", 5)
    assert v1 == 1, f"first hincrby should return 1, got {v1}"
    assert v2 == 6, f"second hincrby should return 6, got {v2}"
