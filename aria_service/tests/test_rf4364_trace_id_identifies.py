"""R-F4364 (C-310) — the trace id on every answer identifies nothing.

MEASURED from the operator's live WhatsApp transcript, 2026-08-26. Every reply
across hours and unrelated questions — Estonia country risk, a Lagos security
assessment, "what about DD?" — carried the SAME footer token:

    *Trace:* `tr_17877`

`trace_stream.py:132` builds `tr_{int(time.time()*1000)}_{uuid4[:6]}`, which is
unique. `confidence_footer.py:448` then renders `tid[:8]`. In this era
`int(time.time()*1000)` begins `17877`, so the rendered form is
**constant for roughly three years** — two ids fifteen minutes apart render
identically.

WHY IT MATTERS MORE THAN IT LOOKS. The footer's own docstring promises the
reader "can pull the full DD lifecycle via /api/aria/trace/{trace_id}", and
`trace_stream.get_trace` does an EXACT key lookup. So the one token printed on
every customer-facing answer, whose entire purpose is to make an answer
traceable, cannot be looked up at all — by construction, not by accident. When
the operator reports a bad answer there is no way to reach its lifecycle.

The entropy in the id is in its SUFFIX (the uuid); truncating from the front
discards all of it and keeps only the part every id shares. Rendering the id in
full is the only form that satisfies the promise the footer makes.
"""
from __future__ import annotations

import time
import uuid

from aria_service.intel import confidence_footer as cf


def _mk_trace() -> str:
    """A trace id in the exact shape trace_stream.py:132 produces."""
    return f"tr_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"


def _render(trace_id):
    """Drive the real footer builder. A substantive body + a tagged claim keeps
    the footer from being suppressed for short/greeting replies."""
    body = ("The designation affects a watched counterparty and requires "
            "immediate screening before any further engagement. [CONFIRMED]")
    verification = {"verdict": "grounded", "grounded_rate": 1.0,
                    "cited": 2, "unverified": 0}
    return cf.build_footer(body, verification, tools_used=["deep_research"],
                           build_rev="R-F4364", trace_id=trace_id)


def test_two_different_traces_render_differently() -> None:
    """THE DEFECT. Ids minutes apart rendered as the same token, so every answer
    in the transcript showed `tr_17877`."""
    a = f"tr_{1787754723937}_{'c6aada'}"
    b = f"tr_{1787755623937}_{'47b304'}"
    assert a != b
    assert _render(a) != _render(b), (
        "distinct traces still render identically — the footer token cannot "
        "identify which answer it belongs to")


def test_the_rendered_trace_is_the_REAL_id() -> None:
    """LOOKUP IS THE WHOLE POINT. `trace_stream.get_trace` matches the key
    EXACTLY, so anything less than the full id is unresolvable — a token that
    looks actionable and is not is worse than printing nothing."""
    tid = _mk_trace()
    out = _render(tid)
    assert tid in out, (
        "the footer prints a token that /api/aria/trace/{trace_id} cannot "
        "resolve, so the lifecycle it advertises is unreachable")


def test_a_short_or_legacy_id_still_renders() -> None:
    """Older/short ids must not disappear from the footer."""
    assert "abc123" in _render("abc123")


def test_no_trace_means_no_trace_field() -> None:
    """Absence stays absent — this must not start printing an empty token."""
    for empty in (None, "", "   "):
        out = _render(empty)
        assert "*Trace:*" not in out, f"rendered a trace field for {empty!r}"


def test_the_constant_prefix_is_gone() -> None:
    """Regression pin on the exact live symptom: the operator saw `tr_17877` on
    every single reply. That literal must never be the whole rendered token
    again."""
    tid = _mk_trace()
    out = _render(tid)
    assert "`tr_17877`" not in out, (
        "the era-constant prefix is being rendered as the entire trace id")
