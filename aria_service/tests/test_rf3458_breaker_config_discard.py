"""R-F3458 — get_breaker silently discarded configuration, in production.

FOUND WHILE FIXING SOMETHING ELSE. R-F3449 hit it as a test artefact: two tests called
`get_breaker(name, failure_threshold=1)` and only ever passed because they inherited
another test's failure count — the threshold they asked for was thrown away. That looked
like a test-only footgun. It is not.

THE PRODUCTION INSTANCE, in web_search.py:

    _search_duckduckgo()  ->  get_breaker("search:duckduckgo",
                                          failure_threshold=5, cooldown_seconds=600)
    search()              ->  get_breaker("search:duckduckgo").is_open()

`_breakers` is a process-global dict and the constructor only runs on first miss, so
WHICHEVER CALL SITE RAN FIRST defined DuckDuckGo's breaker for the life of the process.
The second is a read-only dead-state check inside the top-level `search()`, and a caller
that only wants `is_open()` naturally passes no thresholds — so reaching it first
registered the DEFAULTS (3 failures / 300s) and discarded the intended 5 / 600s with no
warning anywhere. That is easy to reach now that Brave/SearXNG are primary and
`_search_duckduckgo` is often skipped entirely.

The same order-dependent-global-state class as R-F3449, sitting in production rather than
in the suite.

TWO FIXES, because there are two problems:
  * `peek_breaker()` — a read must not have a side effect. That removes the instance.
  * `get_breaker()` warns and wires when an EXPLICIT value conflicts with the registered
    one. That removes the silence, so the next instance is visible instead of latent.

FIRST-REGISTRATION-WINS IS DELIBERATELY KEPT. Mutating a live breaker's thresholds would
change behaviour under callers already relying on it — a worse bug than the one fixed.
"""
from __future__ import annotations

import logging

import pytest

from aria_service.intel import circuit_breaker as cb


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """A private registry per test. The module-level `_breakers` dict is exactly the
    process-global state this bug is about — leaking it between tests would reproduce
    the R-F3449 class inside the test that exists to close it."""
    monkeypatch.setattr(cb, "_breakers", {})


def test_peek_does_not_create():
    """THE FIX: a read has no side effect."""
    assert cb.peek_breaker("search:duckduckgo") is None
    assert "search:duckduckgo" not in cb._breakers, (
        "peek_breaker registered a breaker; the read is still a write")


def test_capability_a_readonly_check_no_longer_defines_the_config():
    """THE PRODUCTION SEQUENCE, in the order that loses the config.

    Dead-state check first (no thresholds), real registration second.
    """
    dead_state = cb.peek_breaker("search:duckduckgo")
    assert not (dead_state and dead_state.is_open()), (
        "an unregistered breaker has recorded no failures and cannot be open")

    real = cb.get_breaker("search:duckduckgo", failure_threshold=5, cooldown_seconds=600)
    assert real.failure_threshold == 5, (
        "the read-only check consumed the registration and the intended threshold was "
        "discarded")
    assert real.cooldown_seconds == 600


def test_the_old_sequence_is_what_lost_it():
    """Demonstrates the defect itself, so the fix is not merely asserted.

    This is the behaviour that remains for anyone who still calls get_breaker() for a
    read — which is why the call site was changed, not just the library.
    """
    cb.get_breaker("some:backend")                       # a bare read-as-registration
    later = cb.get_breaker("some:backend", failure_threshold=5)
    assert later.failure_threshold == cb._DEFAULT_FAILURE_THRESHOLD, (
        "first-registration-wins is the documented semantic and must not change silently")


def test_a_conflicting_registration_warns(caplog):
    """THE SILENCE is half the defect: the value vanished with nothing recorded."""
    cb.get_breaker("noisy:backend", failure_threshold=3)
    with caplog.at_level(logging.WARNING):
        cb.get_breaker("noisy:backend", failure_threshold=5, cooldown_seconds=900)
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "R-F3458" in msg and "noisy:backend" in msg, msg
    assert "DISCARDED" in msg
    assert "failure_threshold" in msg and "cooldown_seconds" in msg


def _r_f3458_warnings(fn):
    """Capture only this module's warnings while running `fn`."""
    recs: list[logging.LogRecord] = []

    class _H(logging.Handler):
        def emit(self, record):
            recs.append(record)

    h = _H()
    cb.logger.addHandler(h)
    try:
        result = fn()
    finally:
        cb.logger.removeHandler(h)
    return result, [r for r in recs if "R-F3458" in r.getMessage()]


def test_matching_config_is_not_noisy():
    """A warning on every agreeing call would be ignored within a day."""
    cb.get_breaker("quiet:backend", failure_threshold=3, cooldown_seconds=300)
    _, warns = _r_f3458_warnings(
        lambda: cb.get_breaker("quiet:backend", failure_threshold=3, cooldown_seconds=300))
    assert not warns, "an agreeing re-registration warned"


def test_omitted_arguments_never_conflict():
    """None means "took the default", so a bare get_breaker() on a configured breaker is
    not a conflict — otherwise every read-style call would warn."""
    cb.get_breaker("cfg:backend", failure_threshold=5, cooldown_seconds=600)
    got, warns = _r_f3458_warnings(lambda: cb.get_breaker("cfg:backend"))
    assert got.failure_threshold == 5, "the registered config was not returned"
    assert not warns, "omitting an argument was treated as a conflicting value"


def test_the_web_search_dead_state_check_uses_peek():
    """The call site is the instance; the library fix alone would leave it in place."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "intel" / "web_search.py").read_text(encoding="utf-8", errors="replace")
    assert 'peek_breaker("search:duckduckgo")' in src
    assert 'get_breaker("search:duckduckgo").is_open()' not in src, (
        "the read-as-registration call site is back")
