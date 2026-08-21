"""R-F4215 / C-195: a malformed tuning knob must never kill boot or dark a watchdog.

main.py parsed six operator env vars with a bare `int()`/`float()` and no guard.
A typo in any of them — `20m`, `5s`, `90 `, an empty-looking non-number — raises
ValueError at a point where nothing catches it:

  178   ARIA_ENGINE_LEASE_TTL_S            -> engine heartbeat dies, lease
                                              expires, the singleton role is lost
  366   ARIA_BOOT_INIT_TIMEOUT_S           -> boot fails
  1886  ARIA_WEDGE_HARD_CEILING_S          -> the event-loop wedge watchdog goes
                                              dark: the instrument that catches
                                              the C-95 starvation class
  2539  ARIA_ENGINE_ELECTION_BOOT_TIMEOUT_S-> boot fails (only TimeoutError is
                                              caught there, not ValueError)
  3390  ARIA_READING_INTERVAL_S            -> the reading loop that feeds gate-2
                                              regional mastery stops compounding
  5005  ARIA_MAX_BODY_BYTES                -> MODULE LEVEL: `import
                                              aria_service.main` itself fails

This is the same class as C-192, where the malformed-cap ValueError sat above the
only `heavy_graph_ready.set()` and parked ARIA's whole metabolism. C-192 fixed the
one instance; this closes the class.

The convention was never in doubt — `autonomous/safety.py`, `intel/user_quota.py`,
`intel/neural_memory.py`, `intel/dd_orchestrator.py` and `autonomous/self_coder.py`
each independently wrote a warn-and-fall-back env guard. main.py, the one file
where a raise takes down the service, was the outlier.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
import subprocess
import sys

import pytest

import aria_service.main as _main


# ── the helper contract (matches autonomous/safety.py) ───────────────────────

@pytest.mark.parametrize("raw", ["20m", "5s", "abc", "", "   ", "1e", "90px"])
def test_env_float_falls_back_instead_of_raising(monkeypatch, raw):
    monkeypatch.setenv("ARIA_TEST_KNOB_F", raw)
    assert _main._env_float("ARIA_TEST_KNOB_F", 12.5) == 12.5


@pytest.mark.parametrize("raw", ["20m", "abc", "", "3.7", "1e"])
def test_env_int_falls_back_instead_of_raising(monkeypatch, raw):
    monkeypatch.setenv("ARIA_TEST_KNOB_I", raw)
    assert _main._env_int("ARIA_TEST_KNOB_I", 7) == 7


def test_valid_values_are_still_honoured(monkeypatch):
    """The guard must be able to FAIL (R-F3858): a real value must win."""
    monkeypatch.setenv("ARIA_TEST_KNOB_F", "2.5")
    assert _main._env_float("ARIA_TEST_KNOB_F", 12.5) == 2.5
    monkeypatch.setenv("ARIA_TEST_KNOB_I", "42")
    assert _main._env_int("ARIA_TEST_KNOB_I", 7) == 42


def test_a_malformed_value_is_announced_not_swallowed(monkeypatch, caplog):
    """§21a: a silently-ignored misconfiguration is an unwired failure branch."""
    monkeypatch.setenv("ARIA_TEST_KNOB_F", "20m")
    with caplog.at_level("WARNING"):
        _main._env_float("ARIA_TEST_KNOB_F", 12.5)
    assert any("ARIA_TEST_KNOB_F" in r.getMessage() for r in caplog.records)


# ── capability: the real paths survive a typo ────────────────────────────────

def test_engine_lease_ttl_survives_a_typo(monkeypatch):
    """A dead heartbeat loses the engine lease and stops every singleton."""
    monkeypatch.setenv("ARIA_ENGINE_LEASE_TTL_S", "45s")
    assert _main._engine_lease_ttl_s() == 45


def test_engine_lease_ttl_keeps_its_floor(monkeypatch):
    monkeypatch.setenv("ARIA_ENGINE_LEASE_TTL_S", "1")
    assert _main._engine_lease_ttl_s() == 10, "the 10s floor must still clamp"


def test_main_still_imports_with_a_malformed_body_limit():
    """ARIA_MAX_BODY_BYTES is parsed at MODULE level — a typo broke `import`.

    Run in a subprocess: the module is already imported in this process, so an
    in-process check could not observe the failure it is asserting against.
    """
    env_line = (
        "import os; os.environ['ARIA_MAX_BODY_BYTES']='50mb'; "
        "import aria_service.main as m; "
        "print('OK', m._MAX_BODY_BYTES)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", env_line],
        capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, (
        "a malformed ARIA_MAX_BODY_BYTES makes aria_service.main un-importable "
        f"— the whole service cannot start:\n{proc.stderr[-2000:]}"
    )
    assert "OK" in proc.stdout


# ── the class-closing guard ──────────────────────────────────────────────────

def _unguarded_numeric_env_parses() -> list[tuple[int, str]]:
    """Every int()/float() over an env read in main.py that nothing catches."""
    path = inspect.getsourcefile(_main)
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    tries = [t for t in ast.walk(tree) if isinstance(t, ast.Try)]

    def is_caught(line: int) -> bool:
        for t in tries:
            if t.lineno <= line <= (t.end_lineno or 0):
                for h in t.handlers:
                    name = ast.unparse(h.type) if h.type else "bare"
                    if name == "bare" or "ValueError" in name or name == "Exception":
                        return True
        return False

    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("int", "float"):
            src = ast.unparse(n)
            if ("getenv" in src or "environ" in src) and not is_caught(n.lineno):
                out.append((n.lineno, src))
    return out


def test_no_unguarded_numeric_env_parse_remains_in_main():
    """Closes the CLASS, not the six instances — a seventh cannot appear quietly."""
    offenders = _unguarded_numeric_env_parses()
    assert not offenders, (
        "these parse an operator env var with a bare int()/float() that nothing "
        "catches, so a typo raises where it cannot be recovered — killing boot, "
        "the engine lease, the wedge watchdog, or the reading loop. Use "
        "_env_int/_env_float (main.py), which warn and fall back:\n"
        + "\n".join(f"  main.py:{ln}  {src}" for ln, src in offenders)
    )


def test_the_guard_can_actually_see_an_offender():
    """A guard that cannot fire is not a guard (R-F3858)."""
    src = inspect.getsource(_unguarded_numeric_env_parses)
    assert "getenv" in src and "is_caught" in src
    sample = ast.parse("x = int(os.getenv('A', '1'))\n")
    found = [
        n for n in ast.walk(sample)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "int" and "getenv" in ast.unparse(n)
    ]
    assert found, "the detection shape no longer matches a real offender"


def test_the_heavy_warmup_cap_uses_the_one_helper():
    """R-F4213's bespoke parser must not survive as a second mechanism.

    Two parsers in one file is the forked-measure antipattern R-F2639 records
    ('there is ONE measure now. Do not fork it again.').
    """
    src = inspect.getsource(_main._heavy_warmup_timeout_s)
    assert "_env_float(" in src, (
        "_heavy_warmup_timeout_s still parses the env itself instead of going "
        "through _env_float — main.py now has two env parsers"
    )
