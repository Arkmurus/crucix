"""R-F4169 / C-183 - C-37 measured the ambiguity and published it. The one
consumer that matters never read it, so ARIA still tells the operator that
healthy subsystems are 0% successful.

**Measured live 2026-08-19, `GET /api/aria/brain/stats` on aria-intel:**

    modules                   : 255
    success_rate == 0.0       :  12
    only_failures_recorded    :  12   <- ALL of them

    learning_progress 0/8    health_precompute 0/3     chat_audit_log 0/35
    llm_chain_exhausted 0/183   llm_deepseek 0/19      security_protocol 0/2
    search_engine_health 0/24   search_searxng 0/152   style_learner 0/1
    eagle_eye 0/4               local_brain 0/2        web_search._search_gdelt 0/1

Every zero on that surface is a rate that carries no information - and
`routes/aria.py::_execute_tool`'s `meta_query` branch, which is how ARIA answers
"how is your brain doing?", printed all twelve as `success=0.00` and
`success_rate: 0.0`. That text goes straight into an LLM prompt and out to the
operator. R-F3934 (C-37) computed `only_failures_recorded` precisely so this
reading could not be made; three render sites in the same function ignored it.

This is the C-27 shape the register already names - a producer with no consumer -
except the producer here exists to prevent a specific false claim, and the false
claim went on being made anyway.

**What this fix must NOT do, and it is the harder half.** The obvious correction
is to render a flagged module as "n/a - ignore this one". That would be a WORSE
error in the opposite direction: `only_failures_recorded` cannot distinguish a
failure-only WIRE from a module that genuinely failed every call. C-37 says so
explicitly ("the counters cannot say"), and `search_searxng` is the live proof -
C-37 verified it DOES call `wire_success`, so its 152/152 may be a real outage.
Suppressing it would hide one. So the rendering states BOTH readings and points
at `fail`/`total`, which do carry information.

The interpretation lives in `brain_hook`, next to the flags that produce it, so
a fourth render site cannot re-derive it differently.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import brain_hook as bh


def _run(coro):
    return asyncio.run(coro)


def _entry(**kw) -> dict:
    base = {
        "total": 0, "success": 0, "fail": 0, "skip": 0,
        "success_rate": 0, "only_failures_recorded": False,
        "no_measurable_signals": False, "last_signal_ago_h": 1.0,
        "status": "active",
    }
    base.update(kw)
    return base


# -- the ONE interpretation ---------------------------------------------------

def test_a_genuinely_measured_rate_renders_as_the_number():
    """The annotation must be rare. A note on every line is noise nobody reads,
    and it would bury the twelve that need it among 255 that do not."""
    out = bh.describe_success_rate(_entry(total=100, success=97, fail=3,
                                          success_rate=0.97))
    assert "0.97" in out
    assert "failure" not in out.lower(), (
        f"a healthy module was annotated: {out!r}"
    )


def test_a_failure_only_entry_states_BOTH_readings():
    """The live `llm_deepseek`: 19 of 19 signals are failures. That is either a
    module with no `wire_success` call (healthy, unmeasurable) or a module
    failing every call (an outage). The counters cannot tell them apart, so the
    rendering must not pick one."""
    out = bh.describe_success_rate(
        _entry(total=19, success=0, fail=19, success_rate=0.0,
               only_failures_recorded=True)
    )
    low = out.lower()
    assert "19" in out, f"the counts that DO carry information are missing: {out!r}"
    assert "failure" in low or "failed" in low
    # Both readings present - neither resolved for the reader.
    assert ("only" in low or "never records" in low or "no success" in low), (
        f"the failure-only reading is missing: {out!r}"
    )
    assert ("outage" in low or "failing" in low or "genuinely" in low
            or "or a" in low), (
        f"the real-outage reading is missing - suppressing it would hide a live "
        f"failure like search_searxng's 152/152: {out!r}"
    )


def test_a_failure_only_entry_is_NOT_declared_fine():
    """The over-correction guard. `search_searxng` DOES wire success (verified
    by C-37), so its 0.0 may be a genuine total outage. A rendering that says
    "not applicable, disregard" would hide it."""
    out = bh.describe_success_rate(
        _entry(total=152, success=0, fail=152, success_rate=0.0,
               only_failures_recorded=True)
    ).lower()
    for phrase in ("healthy", "working as built", "disregard", "ignore",
                   "no problem"):
        assert phrase not in out, (
            f"the rendering asserts health the counters cannot support "
            f"({phrase!r}): {out!r}"
        )


def test_a_skip_only_entry_is_not_reported_as_a_failure():
    """R-F3936's half: `success_rate` falls back to 0 when there is nothing to
    divide. The live `deploy` module read `0.0, fail: 0, total: 1` - neither a
    failure nor a rate."""
    out = bh.describe_success_rate(
        _entry(total=1, success=0, fail=0, skip=1, success_rate=0.0,
               no_measurable_signals=True)
    ).lower()
    # NB: banning the substring "fail" outright was the first draft, and it
    # forbade the rendering from truthfully saying "it is NOT a failure" -- a
    # test that punishes the honest sentence. What must be absent is the
    # AFFIRMATIVE claim.
    for claim in ("failures", "failing", "failed"):
        assert claim not in out, (
            f"a skip-only module was reported as a failure ({claim!r}): {out!r}"
        )
    assert "0.00" not in out and out.strip() != "0.0", (
        f"an unmeasurable module still renders as a measured zero: {out!r}"
    )
    assert "n/a" in out


def test_a_malformed_entry_does_not_crash_the_panel():
    """This renders inside ARIA's introspection answer. A raise here replaces a
    working answer with an error - the R-F3845 lesson (a decorative caption
    turned a live panel into a false outage report)."""
    for junk in (None, {}, [], "nope", {"success_rate": None}):
        assert isinstance(bh.describe_success_rate(junk), str)


# -- THE CAPABILITY TEST: ARIA's own introspection path ----------------------

def _stats_with_failure_only_module() -> dict:
    return {
        "total_signals": 500,
        "health": "healthy",
        "healthy_count": 2,
        "stale_count": 0,
        "stale_modules": [],
        "never_seen": [],
        "circuit_breaker": {},
        "modules": {
            "llm_deepseek": _entry(total=19, success=0, fail=19,
                                   success_rate=0.0,
                                   only_failures_recorded=True),
            "knowledge": _entry(total=400, success=396, fail=4,
                                success_rate=0.99),
        },
    }


def test_the_top_module_table_does_not_publish_a_bare_zero(monkeypatch):
    """THE CAPABILITY TEST. Drives the real `meta_query` branch of
    `_execute_tool` - the path a "how is your brain?" turn takes - and asserts
    the text handed to the LLM does not present an uninformative 0.00 as a
    measurement."""
    from aria_service.routes import aria as R

    async def _fake_stats():
        return _stats_with_failure_only_module()

    monkeypatch.setattr(bh, "get_stats", _fake_stats)

    text = _run(R._execute_tool({"tool": "meta_query", "wants_brain": True},
                                llm=None))

    assert "llm_deepseek" in text, f"the module table did not render: {text[:400]}"
    line = [l for l in text.splitlines() if "llm_deepseek" in l]
    assert line, text[:400]
    joined = " ".join(line)
    assert "success=0.00" not in joined, (
        f"ARIA still tells the operator a failure-only wire is 0% successful: "
        f"{joined!r}"
    )
    assert "19" in joined, (
        f"the counts that DO carry information were not surfaced: {joined!r}"
    )


def test_the_single_module_block_does_not_publish_a_bare_zero(monkeypatch):
    """The same defect at the second render site - asking about ONE module."""
    from aria_service.routes import aria as R

    async def _fake_stats():
        return _stats_with_failure_only_module()

    monkeypatch.setattr(bh, "get_stats", _fake_stats)

    text = _run(R._execute_tool(
        {"tool": "meta_query", "wants_brain": True, "module": "llm_deepseek"},
        llm=None))

    assert "MODULE: llm_deepseek" in text, text[:400]
    assert "success_rate: 0.0\n" not in text and "success_rate: 0\n" not in text, (
        f"the single-module block still publishes a bare zero: {text[:600]!r}"
    )


def test_a_healthy_module_is_still_rendered_plainly(monkeypatch):
    """REGRESSION GUARD - the fix must not annotate the 243 modules whose rate
    is a real measurement."""
    from aria_service.routes import aria as R

    async def _fake_stats():
        return _stats_with_failure_only_module()

    monkeypatch.setattr(bh, "get_stats", _fake_stats)

    text = _run(R._execute_tool({"tool": "meta_query", "wants_brain": True},
                                llm=None))
    line = " ".join(l for l in text.splitlines() if "knowledge" in l)
    assert "0.99" in line, f"a measured rate stopped rendering: {line!r}"
    assert "failure" not in line.lower(), (
        f"a healthy module was annotated with the ambiguity note: {line!r}"
    )
