"""R-F4248 / C-218 — a signal about an OPERATOR condition reset a Phase A gate.

MEASURED LIVE 2026-08-23 via `GET /api/aria/health/error-streak`:

```
last_error: {"type": "log:error",
             "message": "[R-F4235] recording eval '...' populated ZERO honesty
                         judgments (5 entries, 5 verification recorded) ...",
             "file": "aria_service/intel/eval_runner.py", "function": "run_eval"}
last_error_age_hours: 3.5      clean_since: <3.5h ago>
level_breakdown_7d: {"log:warning": 199, "log:error": 1}
phase_a_gate_3_pass: false     phase_a_gate_3_threshold_days: 7
```

**That one `log:error` was mine, and it reset Phase A exit gate #3 to zero.**
`error_streak.is_reset_type` counts `log:error` and advances the DURABLE anchor
R-F2622 built, so the "0 fly ERRORs / 7 days" streak restarted because an
observability signal described the composition of the golden set.

Two of the signals shipped this session had the same defect:

  * `eval_runner` — a recording eval that populated zero honesty judgments.
    **This is the EXPECTED outcome on the current set** (measured yield 1 in 6,
    R-F4242), so gate #3 could never accrue seven clean days while anyone ran a
    recording eval.
  * `fallback._poll_balance_quietly` — an exhausted prepaid vendor balance. An
    operator/vendor condition the code cannot fix, recurring on every poll
    transition.

Neither is an application fault. Both are now WARNING.

## This is the third instance of a recorded class

§1 records R-F2663 (a boot ERROR reset the streak every boot, making the gate
structurally un-closeable) and R-F2668 (a one-shot re-spawn producing a NEEDS
OPERATOR ERROR every boot), and prescribes exactly this remedy: *"WARNING (not
ERROR; `is_reset_type` excludes `log:warning`) so the streak can accrue"*.

## What is NOT being done here

The signal is not being silenced. `wire_failure` / `record_gap` still fire on both
paths — that is what §21a requires, and §21a says nothing about log LEVEL. The
level is a gate input, not the reporting channel. Demoting a genuine application
fault would be the opposite mistake, so `R-F3701`'s recorder-import failure in the
same module stays at ERROR and is asserted below.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import pathlib

import pytest

from aria_service.intel import error_streak
from aria_service.llm import fallback as fb
from aria_service.llm import vendor_balance as vb
from aria_service.llm.provider import LLMProvider, LLMResult


def _run(coro):
    return asyncio.run(coro)


LIVE_EXHAUSTED_BODY = {
    "is_available": False,
    "balance_infos": [{"currency": "USD", "total_balance": "-0.02"}],
}


class _Provider(LLMProvider):
    def __init__(self, name):
        self.name = name

    @property
    def is_configured(self):
        return True

    async def complete(self, system_prompt="", user_message="", **k):
        return LLMResult(text="ok", model=self.name)

    async def stream(self, *a, **k):
        yield "ok"


def _error_markers(module) -> set[str]:
    """Leading `[R-Fxxxx]` markers of every logger.error(...) in a module.

    AST over the CALL, not a substring scan of the file: the question is which
    marker sits on an `error` call, and only the parse can answer that without
    being fooled by the same text appearing in a comment or a docstring — this
    file's own docstring quotes the offending message verbatim.
    """
    src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
    out: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "error"):
            continue
        for arg in node.args[:1]:
            parts = []
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                parts = [arg.value]
            elif isinstance(arg, ast.JoinedStr):
                parts = [v.value for v in arg.values
                         if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            for text in parts:
                if text.startswith("[R-F"):
                    out.add(text.split("]")[0] + "]")
    return out


class TestTheStreakContractIsWhyThisMatters:

    def test_error_resets_the_gate_streak_and_warning_does_not(self):
        """Pin the mechanism, so the fix below is not mistaken for cosmetics."""
        assert error_streak.is_reset_type("log:error") is True
        assert error_streak.is_reset_type("log:warning") is False


class TestOperatorConditionsDoNotResetTheGate:

    def test_the_zero_honesty_populate_is_not_an_error(self):
        from aria_service.intel import eval_runner

        assert "[R-F4235]" not in _error_markers(eval_runner), (
            "the zero-honesty populate signal is back on logger.error — it "
            "resets Phase A gate #3's durable 7-day anchor, and a zero-honesty "
            "run is the EXPECTED outcome on the current golden set, so the gate "
            "could never accrue seven clean days")

    def test_the_exhausted_balance_is_not_an_error(self):
        assert "[R-F4229]" not in _error_markers(fb), (
            "the exhausted-vendor-balance signal is back on logger.error — an "
            "empty prepaid balance is an operator condition the code cannot "
            "fix, and it recurs on every poll transition")

    def test_a_genuine_application_fault_is_still_an_error(self):
        """NEGATIVE CONTROL — this must not become 'demote everything'.

        R-F3701's recorder-import failure IS an application fault: the eval will
        silently stop populating gate #1's stores. That deserves an ERROR and
        deserves to reset the streak.
        """
        from aria_service.intel import eval_runner

        assert "[R-F3701]" in _error_markers(eval_runner), (
            "R-F3701's recorder-import failure must stay at ERROR — demoting a "
            "real fault to hide it from the gate is the opposite defect")


class TestTheSignalIsDemotedNotSilenced:

    def _drive_exhausted(self, monkeypatch, caplog):
        chain = fb.FallbackProvider([_Provider("deepseek")])
        monkeypatch.setattr(chain, "_provider_api_key", lambda p: "sk-test")

        async def _fetch(url, api_key, timeout):
            return 200, LIVE_EXHAUSTED_BODY
        monkeypatch.setattr(vb, "_fetch", _fetch)

        got = {"failure": []}
        import aria_service.intel.engine_wiring as ew
        monkeypatch.setattr(ew, "wire_failure",
                            lambda **kw: got["failure"].append(kw), raising=True)
        monkeypatch.setattr(ew, "wire_success", lambda **kw: None, raising=True)

        with caplog.at_level(logging.DEBUG, logger="aria.llm.fallback"):
            _run(chain._poll_balance_quietly(chain.providers[0]))
        return got, caplog.records

    def test_an_exhausted_balance_logs_warning_never_error(
            self, monkeypatch, caplog):
        _, records = self._drive_exhausted(monkeypatch, caplog)
        said = [r for r in records if "[R-F4229]" in r.getMessage()]
        assert said, "the exhausted balance must still be logged"
        assert any(r.levelno == logging.WARNING for r in said)
        assert not [r for r in said if r.levelno >= logging.ERROR], (
            "an ERROR here resets Phase A gate #3's 7-day streak")

    def test_it_still_reaches_the_brain(self, monkeypatch, caplog):
        """Demoting the LEVEL must not remove the §21a reporting channel."""
        got, _ = self._drive_exhausted(monkeypatch, caplog)
        assert [f for f in got["failure"]
                if f.get("module") == "llm_vendor_balance"], (
            "the vendor-balance gap must still be recorded — the log level is a "
            "gate input, not the reporting channel")
