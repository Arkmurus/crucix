"""R-F762 — state-backend reachability surfaced in /health.

Audit on 2026-05-20 (P2) flagged that main.py's lifespan called
`await rs.connect(settings.redis_url)` but discarded the result.
Consequence: a Redis-unreachable boot silently fell back to the
in-process _mem_store (knowledge grew in RAM, lost on next restart)
and the operator's only signal was a `Redis unavailable, using
in-memory fallback` WARNING in fly logs — easily missed in the
bigger noise of provider cooldowns + autonomous-cycle traces.

R-F762:
  1. lifespan captures the rs.connect() result (or False on exception)
     and stores it on app.state.state_backend + app.state.
     state_backend_reachable.
  2. /health adds a state_backend block { backend, reachable, status }.
  3. The top-level /health.status flips to 'degraded' when
     state_backend_reachable is False, on top of the existing LLM
     + autonomy gates.

Source-level test enforces the contracts so a future edit can't
quietly drop the new block.
"""
from __future__ import annotations

from pathlib import Path


def _main_src() -> str:
    p = Path(__file__).resolve().parents[1] / "main.py"
    return p.read_text(encoding="utf-8")


def test_rf762_lifespan_captures_connect_result():
    """R-F762: a boot-time state-backend failure must not be invisible.

    R-F2801: this asserted the literal source text
    ``_state_connect_ok = await rs.connect``. A later change wrapped the call in
    ``asyncio.wait_for(rs.connect(...), timeout=...)`` to bound a slow connect —
    the RESULT IS STILL CAPTURED, so the R-F762 contract holds, but the string
    match broke. That is a source-spelling gate, not a contract gate.

    Asserted structurally instead: the lifespan must ASSIGN the awaited
    ``rs.connect(...)`` to ``_state_connect_ok`` and then ACT on it. Parsed with
    ast so any equivalent spelling (wrapped, reordered, reformatted) passes,
    while genuinely dropping the result fails.
    """
    import ast

    tree = ast.parse(_main_src())

    assigns_result = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
        if "_state_connect_ok" not in targets:
            continue
        # the value must (somewhere inside it) await rs.connect(...)
        for sub in ast.walk(node.value):
            if isinstance(sub, ast.Attribute) and sub.attr == "connect" and \
               isinstance(sub.value, ast.Name) and sub.value.id == "rs":
                assigns_result = True
    assert assigns_result, (
        "R-F762 regression: lifespan no longer assigns the awaited rs.connect() "
        "result to _state_connect_ok. Boot-time failure becomes invisible again."
    )

    # …and the captured result must actually be USED, or capturing it is theatre.
    used = any(
        isinstance(n, ast.Name) and n.id == "_state_connect_ok" and isinstance(n.ctx, ast.Load)
        for n in ast.walk(tree)
    )
    assert used, (
        "R-F762 regression: _state_connect_ok is captured but never read — a "
        "boot-time failure would still be invisible."
    )


def test_rf762_lifespan_handles_connect_exception():
    """If rs.connect() raises (DNS error, Upstash 5xx, sqlite path
    refused), we must not crash the lifespan — fall back gracefully
    with a clear ERROR log."""
    src = _main_src()
    # The try/except wrapping the connect call must use logger.error
    # (not warning/debug) — boot-time backend failure is operationally
    # critical.
    assert "state-backend connect raised" in src, (
        "R-F762 regression: state-backend connect exception path lost "
        "its [R-F762] ERROR-tag wording."
    )


def test_rf762_app_state_carries_health_signals():
    src = _main_src()
    for needle in (
        "app.state.state_backend",
        "app.state.state_backend_reachable",
    ):
        assert needle in src, (
            f"R-F762 regression: lifespan does not set {needle} on "
            "app.state. /health can't read it back."
        )


def test_rf762_health_endpoint_exposes_state_backend_block():
    src = _main_src()
    # The /health response must include the state_backend block.
    assert '"state_backend": state_backend_ind' in src, (
        "R-F762 regression: /health response no longer includes the "
        "state_backend block. Monitor watchers can't see the reachable "
        "flag without it."
    )


def test_rf762_health_status_factors_in_state_backend():
    src = _main_src()
    # Top-level status must consider state_backend_ind reachable.
    assert "and state_backend_ind[\"reachable\"]" in src, (
        "R-F762 regression: /health.status no longer factors state-"
        "backend reachable into the operational-vs-degraded decision. "
        "A backend-unreachable boot would falsely report operational."
    )


def test_rf762_state_backend_block_has_three_fields():
    """The block shape must be { backend, reachable, status } — backend
    is the env-var value (sqlite/upstash/memory), reachable is the
    boolean, status is green/red for status-page rendering."""
    src = _main_src()
    for needle in (
        '"backend": getattr(app.state, "state_backend", "unknown")',
        '"reachable": bool(getattr(app.state, "state_backend_reachable"',
        '"status"',
    ):
        assert needle in src, (
            f"R-F762 regression: state_backend_ind missing {needle!r}"
        )
