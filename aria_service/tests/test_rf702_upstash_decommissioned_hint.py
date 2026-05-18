"""R-F702 (2026-05-18) — strip stale Upstash hint from dashboard.

Live dashboard 2026-05-18 20:42 rendered "Upstash Redis (cluster:
adapted-ostrich-92296) Not configured. Set UPSTASH_REDIS_URL +
UPSTASH_REDIS_TOKEN env vars to enable live usage panel" — but
operator cancelled the Upstash subscription on 2026-05-12 and SQLite
is the canonical state backend. The "set these env vars" prompt is
the opposite of what the operator should do.

R-F702 reframes the absent-config branch as a `decommissioned=True`
historical-state line, and updates the frontend panel to render
"SQLite (fly volume /data) · Upstash decommissioned 2026-05-12"
instead of prompting the operator to set vars that should NOT be set.
"""
from __future__ import annotations

import asyncio
import os


def test_rf702_upstash_probe_returns_decommissioned_when_no_creds(monkeypatch):
    """Without Upstash env vars, the probe should return decommissioned=True
    plus an explanatory hint that does NOT prompt the operator to set vars."""
    for var in (
        "UPSTASH_REDIS_URL", "UPSTASH_REDIS_TOKEN",
        "UPSTASH_REST_URL", "UPSTASH_REST_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)

    from aria_service.routes.aria import _upstash_usage_probe
    result = asyncio.run(_upstash_usage_probe())
    assert isinstance(result, dict)
    assert result.get("configured") is False
    assert result.get("decommissioned") is True
    # The hint must NOT instruct the operator to set the env vars
    hint = (result.get("hint") or "").lower()
    assert "set upstash" not in hint, f"R-F702 expected no 'set upstash' prompt, got: {hint!r}"
    assert "set " not in hint or "decommissioned" in hint, (
        f"R-F702 expected decommissioned wording, got: {hint!r}"
    )
    # Spot-check explanatory content
    assert "decommissioned" in hint or "sqlite" in hint, (
        f"R-F702 expected decommissioned/sqlite in hint, got: {hint!r}"
    )


def test_rf702_frontend_panel_does_not_prompt_set_vars():
    """The frontend (public/aria-brain.html) must not contain the
    legacy `Set UPSTASH_REST_URL + UPSTASH_REST_TOKEN to enable.`
    fallback string — that prompt told the operator to do the opposite
    of canonical state."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    src = p.read_text(encoding="utf-8")
    # The legacy fallback must be gone (no untruthful "Set X to enable" line)
    assert "Set UPSTASH_REST_URL + UPSTASH_REST_TOKEN to enable" not in src, (
        "R-F702 expected the legacy 'Set ... to enable.' fallback to be removed"
    )
    # Confirm the new decommissioned line is present
    assert "decommissioned 2026-05-12" in src, (
        "R-F702 expected the decommissioned wording to be present in the panel"
    )


def test_rf702_panel_header_is_neutral_state_backend():
    """The panel header used to read 'Upstash Redis (cluster: ...)'
    unconditionally. The new header is 'State backend' so the panel
    label matches the actual content regardless of branch."""
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "public" / "aria-brain.html"
    src = p.read_text(encoding="utf-8")
    assert ">State backend<" in src, (
        "R-F702 expected neutral 'State backend' header on the panel"
    )
