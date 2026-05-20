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
    src = _main_src()
    assert "_state_connect_ok = await rs.connect" in src, (
        "R-F762 regression: lifespan no longer captures rs.connect() "
        "result. Boot-time failure becomes invisible again."
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
