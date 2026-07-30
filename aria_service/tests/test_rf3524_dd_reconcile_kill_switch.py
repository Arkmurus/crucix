"""R-F3524 — the DD reconcile loop had no kill switch, so it could not be paused.

THE INCIDENT (2026-07-30). aria-intel entered a SIGSEGV crash-loop (exit_code=139,
oom_killed=false, ~70s period). Two reconcilers exist specifically to resurrect work that
a restart killed:

    reconcile_stale_running_dds     (R-F2300) re-launches restart-killed DDs
    reconcile_pending_adverse_media (R-F2941) re-launches orphaned adverse-media sweeps

So every crash re-armed the deep-DD load that was in flight when the box went down. The
last log line before a crash was a web search for an officer of a subject whose DD had
already been killed twice.

WHETHER THAT LOAD CAUSED THE SEGFAULT WAS NOT ESTABLISHED, and this change does not claim
it did. What WAS established is the operational defect: the operator asked to pause DD
relaunches and there was no way to do it. `_dd_reconcile_loop` checked only the singleton
role. The only levers were `ARIA_ROLE` — far too broad, it disables every singleton — or
a redeploy, on a box that was crash-looping.

The autonomous engine has had a master switch since R-F276 precisely so it can be stopped
"without redeploying" (its endpoint docstring). This loop can generate just as much
production load and had nothing.

THE PROPERTY: a subsystem that can generate production load must be pausable WITHOUT a
deploy and WITHOUT the app being healthy. An env var read on EVERY iteration satisfies
both — `flyctl secrets set` then takes effect at the next boot even mid-crash-loop, which
is exactly when it is needed.

DEFAULT ENABLED, deliberately. Turning it off is not free: orphaned `status='running'`
DDs stop being cleared, which is R-F2300's 12.5h chat-hang. It is an incident lever, not
a setting, and the code says so on the way past.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aria_service import main as m


# ── the switch itself ───────────────────────────────────────────────────────

def test_enabled_by_default_so_this_changes_nothing_unless_asked(monkeypatch):
    """A self-heal loop that silently defaulted OFF would recreate the 12.5h hang."""
    monkeypatch.delenv("ARIA_DD_RECONCILE_ENABLED", raising=False)
    assert m._dd_reconcile_enabled() is True


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("", True), ("anything", True),
    ("0", False), ("false", False), ("FALSE", False), ("no", False), ("off", False),
    ("  0  ", False),
])
def test_the_switch_reads_the_usual_falsey_spellings(value, expected, monkeypatch):
    monkeypatch.setenv("ARIA_DD_RECONCILE_ENABLED", value)
    assert m._dd_reconcile_enabled() is expected


def test_it_uses_the_modules_real_os_alias():
    """`main.py` imports `os as _os` and binds no bare `os`. My first cut wrote
    `os.getenv(...)`, which py_compile accepts and which would have raised NameError on
    the first iteration — disabling the very switch this adds, during an incident."""
    import ast
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8", errors="replace")
    bound = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            for a in node.names:
                bound.add(a.asname or a.name)
    if "os" not in bound:
        fn = src[src.index("def _dd_reconcile_enabled"):]
        fn = fn[:fn.index("\n\n\n")]
        assert not re.search(r"[^_\w]os\.getenv", fn), (
            "bare `os.getenv` in a module that only binds `_os` — NameError at runtime")
        assert "_os.getenv" in fn


# ── the property that makes it usable during an incident ────────────────────

def test_the_loop_checks_the_switch_every_iteration_not_once_at_startup():
    """THE POINT. A flag captured before `while True:` cannot be flipped on a running
    box, so it would be useless in exactly the situation it exists for."""
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8", errors="replace")
    start = src.index("async def _dd_reconcile_loop")
    body = src[start: src.index("_bg_task(", start)]

    loop_at = body.index("while True:")
    check_at = body.index("_dd_reconcile_enabled()")
    assert check_at > loop_at, (
        "the switch is read before the loop — it cannot be changed without a restart")
    # ...and the pass itself must be behind it.
    assert body.index("_dd_reconcile_once()") > check_at, (
        "the reconcile pass runs before the switch is consulted")
    assert "continue" in body[check_at: check_at + 700], (
        "a disabled pass must skip the work, not fall through to it")


def test_being_paused_is_stated_and_not_silent():
    """Silence is how an operator forgets that DD self-heal is off — and off means
    orphaned DDs hang forever (R-F2300)."""
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8", errors="replace")
    start = src.index("async def _dd_reconcile_loop")
    body = src[start: src.index("_bg_task(", start)]
    assert "[R-F3524] dd_reconcile PAUSED" in body
    assert "will NOT be cleared" in body, (
        "the warning must state the CONSEQUENCE, not just the state")
    assert "[R-F3524] dd_reconcile RESUMED" in body, "resuming must be observable too"


def test_the_warning_fires_on_transition_not_every_pass():
    """A line every 10 minutes for hours is how a real signal gets ignored."""
    src = pathlib.Path(m.__file__).read_text(encoding="utf-8", errors="replace")
    start = src.index("async def _dd_reconcile_loop")
    body = src[start: src.index("_bg_task(", start)]
    assert "_skip_logged" in body, "no transition guard — the warning repeats forever"
    assert body.index("_skip_logged = False") < body.index("while True:"), (
        "the transition guard must be initialised outside the loop")


def test_the_docstring_records_that_disabling_has_a_cost():
    """This is an incident lever. A future reader must not adopt it as a default."""
    doc = m._dd_reconcile_enabled.__doc__ or ""
    assert "incident lever, not a setting" in doc
    assert "R-F2300" in doc, "the cost of disabling (the 12.5h chat-hang) must be named"
