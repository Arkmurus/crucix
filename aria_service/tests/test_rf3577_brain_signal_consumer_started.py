"""R-F3577 — the web tier pushed brain signals into a list nothing drained.

`apis/briefing.mjs::pushSignalsToBrain` writes sweep signals to the Redis list
`crucix:brain:incoming_signals`, and it is LIVE — called from `server.mjs:7709`
and `apis/briefing.mjs:847`. `intel/brain_signal_consumer.py` is the reader.
**Nothing started it**, so every web-tier signal was pushed and never consumed:
a producer with no reader, which is the cross-tier darkness §21b exists to stop.

Three compounding causes, and each hid the next:

  1. The consumer started itself as an IMPORT-TIME side effect
     (`asyncio.get_running_loop()` then `start_consumer()`), and **nothing
     imports the module** — proven by the R-F3573 orphan audit. An import-time
     side effect cannot fire without an import.
  2. Its fallback comment said "the consumer will be started when lifespan calls
     start_consumer()". Lifespan never called it. The comment asserted a caller
     that was never written.
  3. The §21a wiring sat INSIDE the `except RuntimeError` branch, so
     `wire_success("brain_signal_consumer module active")` fired on exactly the
     path where the consumer had NOT started — the brain was told the module was
     active by the code handling its failure to start.

And the monitor that was supposed to catch this asserted the module's SOURCE TEXT
(`"_auto_started" in consumer_content`), which stayed true throughout. Cf.
[[assert-the-property-not-the-wording]].
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


_REPO = pathlib.Path(__file__).resolve().parents[2]


def test_the_producer_is_actually_live():
    """Verify the premise before the fix: if nothing pushed, there would be
    nothing to drain and this whole change would be pointless."""
    server = (_REPO / "server.mjs").read_text(encoding="utf-8", errors="replace")
    briefing = (_REPO / "apis" / "briefing.mjs").read_text(encoding="utf-8", errors="replace")
    assert "pushSignalsToBrain(" in server, "the web tier no longer pushes signals"
    assert "crucix:brain:incoming_signals" in briefing
    assert "no-op since Upstash retirement" not in briefing, (
        "the producer has been turned into a no-op — the consumer is then moot"
    )


def test_the_consumer_is_registered_in_lifespan():
    """THE FIX. The docstring asked for this call for five R-numbers."""
    main_src = (_REPO / "aria_service" / "main.py").read_text(encoding="utf-8")
    assert "brain_signal_consumer" in main_src, (
        "lifespan does not reference the consumer — it will never start"
    )
    assert "_singleton_task(_brain_signal_loop" in main_src, (
        "the consumer must start via _singleton_task (R-F2073), not "
        "asyncio.create_task: on a multi-worker web role every worker would "
        "drain the same Redis list and race for the same signals"
    )


def test_the_import_time_autostart_is_gone():
    """It could never work (nothing imports the module) and it produced the
    false success signal. A module must not start its own background loop as an
    import side effect."""
    src = (_REPO / "aria_service" / "intel" / "brain_signal_consumer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Try):
            calls = [
                ast.unparse(n.func)
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
            ]
            assert "start_consumer" not in calls, (
                "module-level start_consumer() is back — an import-time side "
                "effect that cannot fire, because nothing imports this module"
            )


def test_the_success_signal_is_not_on_the_failure_path():
    """`wire_success("module active")` used to live inside `except RuntimeError`,
    i.e. it fired only when the consumer had NOT started."""
    src = (_REPO / "aria_service" / "intel" / "brain_signal_consumer.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
            name = ast.unparse(call.func).split(".")[-1]
            assert name != "wire_success", (
                "a wire_success sits inside an exception handler — a success "
                "signal on a failure path is a false claim to the brain"
            )


def test_the_loop_factory_matches_what_singleton_task_expects():
    """_singleton_task takes a ZERO-ARG coroutine function. Passing a coroutine
    object, or a sync function, fails at boot — the F28 class (§9)."""
    from aria_service.intel.brain_signal_consumer import _consume_loop

    assert inspect.iscoroutinefunction(_consume_loop)
    sig = inspect.signature(_consume_loop)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)
    ]
    assert not required, f"_consume_loop takes required args {required}; the factory must be zero-arg"


@pytest.mark.asyncio
async def test_a_drained_signal_reaches_the_brain_and_signals_success(monkeypatch):
    """Drive the real loop body once: a signal on the list must reach
    brain_hook.absorb AND emit the §21a success branch."""
    from aria_service.intel import brain_signal_consumer as bsc

    absorbed, signals = [], []
    popped = {"done": False}

    async def _fake_lpop(key, count=10):
        if popped["done"]:
            raise _Stop()
        popped["done"] = True
        assert key == "crucix:brain:incoming_signals"
        return ['{"content": "web tier saw a tender", "signal_type": "web_sweep_signal"}']

    async def _fake_absorb(**kw):
        absorbed.append(kw)

    class _Stop(Exception):
        pass

    from aria_service.intel import redis_store, brain_hook
    monkeypatch.setattr(redis_store, "lpop_multi", _fake_lpop)
    monkeypatch.setattr(brain_hook, "absorb", _fake_absorb)
    monkeypatch.setattr(bsc, "wire_success", lambda **kw: signals.append(kw))
    monkeypatch.setattr(bsc, "_STARTUP_DELAY_S", 0)
    monkeypatch.setattr(bsc, "_POLL_INTERVAL_S", 0)

    import asyncio
    task = asyncio.create_task(bsc._consume_loop())
    for _ in range(200):
        await asyncio.sleep(0)
        if absorbed and signals:
            break
    task.cancel()

    assert absorbed, "a queued web-tier signal never reached brain_hook.absorb"
    assert absorbed[0]["module"].startswith("cross_tier:")
    assert absorbed[0]["success"] is True
    assert signals, "signals were absorbed and the §21a success branch never fired"
    assert signals[-1]["module"] == "brain_signal_consumer"


def test_the_monitor_now_checks_registration_not_source_text():
    """The old monitor asserted `"_auto_started" in consumer_content` — true for
    five R-numbers while the loop had never run."""
    src = (_REPO / "aria_service" / "intel" / "wiring_monitor.py").read_text(encoding="utf-8")
    assert "consumer_registered_in_lifespan" in src, (
        "the monitor still only greps the consumer's own source"
    )
    assert "consumer_task_live" in src
    # The old key names claimed behaviour they never measured.
    assert '"consumer_has_auto_start"' not in src
    assert '"consumer_polls_key"' not in src


def test_the_consumer_left_the_orphan_baseline():
    """R-F3573's anti-rot rule, applied to this change: a module that is no
    longer orphaned must be REMOVED from the baseline, not left there."""
    import sys
    sys.path.insert(0, str(_REPO / "scripts"))
    from ecosystem_audit import ORPHAN_BASELINE_NEVER, ORPHAN_BASELINE_TEST_ONLY

    assert "intel/brain_signal_consumer.py" not in ORPHAN_BASELINE_NEVER
    assert "intel/brain_signal_consumer.py" not in ORPHAN_BASELINE_TEST_ONLY


def test_the_monitor_does_not_re_read_source_files_every_cycle():
    """R-F3577 — test_brain_signal_path() runs on the monitor loop and read four
    PRODUCTION SOURCE FILES per cycle, synchronously, on the event loop. Those
    files cannot change inside a running process, so the answer is constant.

    Adding a fourth read (main.py, ~250KB) made test_rf1091's 0.5s-budgeted loop
    test fail under load. The test was right to be sensitive: the defect is
    blocking file I/O in an async loop, not a slow test.
    """
    from aria_service.intel import wiring_monitor

    assert hasattr(wiring_monitor, "_cached_source")
    assert hasattr(wiring_monitor._cached_source, "cache_info"), (
        "_cached_source is not memoised — every monitor cycle re-reads the files"
    )

    src = inspect.getsource(wiring_monitor.test_brain_signal_path)
    assert "with open(" not in src, (
        "a raw open() is back in the monitor's per-cycle path; route it through "
        "_cached_source so the loop does not block on constant data"
    )
    assert src.count("_cached_source(") >= 4


def test_the_cached_read_degrades_to_empty_not_an_exception(tmp_path):
    """Every caller treats a missing file as 'token not present'. Raising here
    would turn a missing file into a monitor crash."""
    from aria_service.intel.wiring_monitor import _cached_source

    assert _cached_source(str(tmp_path / "does_not_exist.py")) == ""
    real = tmp_path / "real.py"
    real.write_text("token_here = 1\n", encoding="utf-8")
    assert "token_here" in _cached_source(str(real))
