"""R-F838 + R-F839 + R-F840 — post-Seenode→Fly migration networking fixes.

Capability + unit tests for the live degradation root-caused by the
other-Claude Fly assessment 2026-05-23:

R-F838: aria-intel uvicorn was binding 0.0.0.0 (IPv4-only). Fly's
        *.internal private network is IPv6-only (6PN). Cross-app
        calls from aria-web → aria-intel.internal:8000 were refused.
        Fix: change config.py default ARIA_HOST to "::".

R-F839: SEENODE_BASE_URL was overloaded — one secret used as BOTH the
        WA-send target (autonomous/delivery.py) AND the email-state
        target (routes/aria.py:5812). After Fly migration these are
        two distinct apps (aria-wa.internal:5070 vs
        aria-web.internal:3117). Fix: split into ARIA_WA_INTERNAL_URL
        + ARIA_WEB_INTERNAL_URL with SEENODE_BASE_URL as fallback
        during rollback.

R-F840: routes/aria.py:17779 hardcoded a Seenode-public fallback URL
        in the /health parity probe. After R-F835 cancels Seenode,
        the host becomes a 6s timeout on every probe. Fix: replace
        the fallback with the Fly internal URL.
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import patch


# ── R-F838 ─────────────────────────────────────────────────────────────────


def test_rf838_aria_host_default_is_dual_stack():
    """aria_service.config.Settings.host must default to '::' so a fresh
    deploy without an explicit ARIA_HOST secret binds dual-stack."""
    # Strip any inherited env vars so we see the actual code default
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ARIA_HOST", None)
        # Re-import to get a fresh Settings() with no env override
        from importlib import reload
        from aria_service import config as _cfg
        reload(_cfg)
        s = _cfg.Settings()
        assert s.host == "::", (
            f"ARIA_HOST default must be '::' for IPv6 dual-stack on Fly 6PN; "
            f"got {s.host!r}"
        )


def test_rf838_aria_host_can_still_be_overridden_to_ipv4():
    """Local dev / hosts without IPv6 must still be able to opt in to
    IPv4-only via the ARIA_HOST env var."""
    with patch.dict(os.environ, {"ARIA_HOST": "0.0.0.0"}, clear=False):
        from importlib import reload
        from aria_service import config as _cfg
        reload(_cfg)
        s = _cfg.Settings()
        assert s.host == "0.0.0.0"


# ── R-F839 ─────────────────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_rf839_wa_target_prefers_internal_url():
    """delivery._wa_target_url must return ARIA_WA_INTERNAL_URL when set,
    even if SEENODE_BASE_URL is also set (Fly internal beats Seenode)."""
    with patch.dict(os.environ, {
        "ARIA_WA_INTERNAL_URL": "http://aria-wa.internal:5070",
        "SEENODE_BASE_URL":     "https://old.seenode.example",
    }, clear=False):
        # Force fresh import so module-level reads pick up the env
        import importlib
        from aria_service.autonomous import delivery as d
        importlib.reload(d)
        assert d._wa_target_url() == "http://aria-wa.internal:5070"


def test_rf839_wa_target_falls_back_to_seenode_during_rollback():
    """If only SEENODE_BASE_URL is set (rollback scenario), use it."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ARIA_WA_INTERNAL_URL", None)
        os.environ["SEENODE_BASE_URL"] = "https://rollback.seenode.example"
        import importlib
        from aria_service.autonomous import delivery as d
        importlib.reload(d)
        assert d._wa_target_url() == "https://rollback.seenode.example"


def test_rf839_wa_target_empty_when_neither_set():
    """No targets configured → returns '' so the delivery skipped-no-target
    branch fires instead of POSTing to an empty URL."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("ARIA_WA_INTERNAL_URL", None)
        os.environ.pop("SEENODE_BASE_URL", None)
        import importlib
        from aria_service.autonomous import delivery as d
        importlib.reload(d)
        assert d._wa_target_url() == ""


def test_rf839_wa_notifier_reads_internal_url():
    """WANotifier must prefer ARIA_WA_INTERNAL_URL over SEENODE_BASE_URL.
    The constructor arg `seenode_base_url` still wins for backward-compat."""
    with patch.dict(os.environ, {
        "ARIA_WA_INTERNAL_URL": "http://aria-wa.internal:5070",
        "SEENODE_BASE_URL":     "https://old.seenode.example",
        "ARIA_INTERNAL_TOKEN":  "tok",
        "ARIA_CODER_WA_GROUP_ID": "g@g.us",
    }, clear=False):
        from aria_service.autonomous.wa_notifier import WANotifier
        n = WANotifier()
        assert n.base_url == "http://aria-wa.internal:5070"


def test_rf839_wa_notifier_constructor_arg_wins():
    """Constructor arg must override env (programmatic override path)."""
    with patch.dict(os.environ, {
        "ARIA_WA_INTERNAL_URL": "http://aria-wa.internal:5070",
        "ARIA_INTERNAL_TOKEN":  "tok",
        "ARIA_CODER_WA_GROUP_ID": "g@g.us",
    }, clear=False):
        from aria_service.autonomous.wa_notifier import WANotifier
        n = WANotifier(seenode_base_url="https://override.example")
        assert n.base_url == "https://override.example"


# ── R-F840 ─────────────────────────────────────────────────────────────────


def test_rf840_no_hardcoded_seenode_fallback_in_aria_py():
    """The literal Seenode-public host in routes/aria.py was a 6s-timeout
    landmine after R-F835. Must not reappear."""
    import pathlib
    aria = pathlib.Path(__file__).parent.parent / "routes" / "aria.py"
    text = aria.read_text(encoding="utf-8")
    assert "run-on-seenode.com" not in text, (
        "Hardcoded Seenode public host re-introduced — would timeout "
        "after Seenode subscription cancel (R-F835). Use "
        "ARIA_WEB_INTERNAL_URL or http://aria-web.internal:3117 instead."
    )


def test_rf840_internal_fallback_present_in_aria_py():
    """The new internal fallback must be the explicit code default."""
    import pathlib
    aria = pathlib.Path(__file__).parent.parent / "routes" / "aria.py"
    text = aria.read_text(encoding="utf-8")
    assert "aria-web.internal:3117" in text, (
        "Expected the Fly internal fallback 'http://aria-web.internal:3117' "
        "in routes/aria.py — the /health parity probe needs it as the "
        "last-resort default."
    )
