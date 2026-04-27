"""Regression guard for autonomous engine eager task loading.

Live observation 2026-04-27 18:00:05: start_engine() logged
"started (dry_run=False, 0 tasks loaded)" — but the system actually
has 70 tasks in tasks.yaml. Tasks were being loaded inside _engine_loop
AFTER its 90s startup_delay, so the log at start time was always 0.

Effect on production: the engine's first poll fires at +90s. If task
loading happened AFTER that first tick (because the loop had to wait
for sleep then call load_tasks), the first tick would skip everything
that was supposed to fire at boot. The 18:00:05 log showed 0 tasks
loaded; tasks didn't load until 18:01:52 — 1m47s later. Engine's
first poll therefore had nothing to fire.

Fix: load_tasks() now runs eagerly inside start_engine() before the
"started" log, so:
  1. The log shows the real count immediately.
  2. The engine has tasks ready when its 90s sleep ends.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from aria_service.autonomous import engine as engine_mod
from aria_service.autonomous import tasks as tasks_mod


def _reset_engine_state():
    """Tests share module-level state. Reset between cases."""
    engine_mod._engine_task = None
    tasks_mod._loaded_tasks = {}


def _swallow_coro(coro):
    """asyncio.create_task replacement that closes the coroutine instead of
    scheduling it — tests don't have a running loop. Closing prevents the
    'coroutine was never awaited' RuntimeWarning."""
    try:
        coro.close()
    except Exception:
        pass
    return MagicMock(done=lambda: False, cancelled=lambda: False)


def test_start_engine_loads_tasks_before_logging_count(monkeypatch, caplog):
    """start_engine must call load_tasks before the 'started' log line."""
    _reset_engine_state()
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")

    fake_llm = MagicMock()
    fake_llm.is_configured = True

    load_calls = {"n": 0}
    real_load = tasks_mod.load_tasks

    def counting_load(path=None):
        load_calls["n"] += 1
        # Populate the cache with a fake task so the count is non-zero.
        tasks_mod._loaded_tasks = {
            "T1": tasks_mod.Task(id="T1", name="dummy", cron="0 6 * * *"),
            "T2": tasks_mod.Task(id="T2", name="dummy2", cron="0 7 * * *"),
        }
        return tasks_mod._loaded_tasks

    with patch.object(tasks_mod, "load_tasks", side_effect=counting_load), \
         patch("asyncio.create_task", side_effect=_swallow_coro) as mock_create_task:
        # asyncio.create_task is patched so the engine loop never actually
        # runs — we only care about the synchronous start_engine path.

        with caplog.at_level("INFO"):
            result = engine_mod.start_engine(fake_llm)

    assert result is True
    assert load_calls["n"] == 1, "start_engine must call load_tasks exactly once"
    started_lines = [r.message for r in caplog.records if "started" in r.message and "tasks loaded" in r.message]
    assert len(started_lines) == 1
    assert "2 tasks loaded" in started_lines[0], (
        f"expected '2 tasks loaded' in start log, got: {started_lines[0]!r}"
    )

    _reset_engine_state()


def test_start_engine_tolerates_load_failure(monkeypatch, caplog):
    """If load_tasks raises, start_engine still spawns the loop and logs
    a warning rather than crashing the entire app boot."""
    _reset_engine_state()
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")

    fake_llm = MagicMock()
    fake_llm.is_configured = True

    def boom(path=None):
        raise RuntimeError("simulated yaml parse boom")

    with patch.object(tasks_mod, "load_tasks", side_effect=boom), \
         patch("asyncio.create_task", side_effect=_swallow_coro):
        with caplog.at_level("WARNING"):
            result = engine_mod.start_engine(fake_llm)

    assert result is True, "engine must still start even if eager load fails"
    warn_lines = [r.message for r in caplog.records if "eager tasks load failed" in r.message]
    assert len(warn_lines) == 1
    assert "simulated yaml parse boom" in warn_lines[0]

    _reset_engine_state()


def test_start_engine_skips_load_when_llm_unconfigured(monkeypatch):
    """When LLM is unconfigured we bail before scheduling the loop, but
    eager-load should NOT run either — no engine, no need for tasks."""
    _reset_engine_state()
    monkeypatch.setenv("ARIA_AUTONOMOUS_ENABLED", "1")

    fake_llm = MagicMock()
    fake_llm.is_configured = False

    load_calls = {"n": 0}

    def counting_load(path=None):
        load_calls["n"] += 1
        return {}

    with patch.object(tasks_mod, "load_tasks", side_effect=counting_load):
        result = engine_mod.start_engine(fake_llm)

    assert result is False
    assert load_calls["n"] == 0, "must not load tasks when engine isn't going to start"

    _reset_engine_state()
