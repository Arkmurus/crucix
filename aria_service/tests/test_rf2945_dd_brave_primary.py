"""R-F2945 — Brave is the DD's primary search backend on EVERY trigger path.

Operator directive 2026-07-23: "ensure brave API is the main search engine on the
DD while searxng is building up — let's not fail on this." Brave-primary was set
only by @_brave_scope on the WEB ROUTE, but orchestrate_dd is also reached from
autonomous/tasks.py and dd_trigger_pipeline.py (undecorated), and the R-F2941
adverse-media reconciler re-launches its follow-up from a loop with no scope. So
watchlist / autonomous / reconciled DDs ran SearXNG-only — the self-hosted box
that times out the digital layer (96%) and partials adverse-media.

These tests assert the DD functions themselves enable Brave, independent of how
they were launched (so a non-decorated caller can't silently drop it).
"""
from __future__ import annotations

import inspect

from aria_service.intel import dd_orchestrator as ddo


class TestBraveIsWiredIntoTheDDItself:
    def test_orchestrate_dd_enables_brave(self):
        """The one choke point every DD funnels through must set Brave-primary."""
        src = inspect.getsource(ddo.orchestrate_dd)
        assert "enable_brave_for_scope" in src, (
            "orchestrate_dd no longer enables Brave — autonomous/watchlist DDs "
            "would fall back to the SearXNG-only free stack")

    def test_adverse_media_followup_enables_brave(self):
        """The detached follow-up + the R-F2941 reconciler re-launch must not
        depend on contextvar propagation — they must set Brave themselves."""
        src = inspect.getsource(ddo._run_adverse_media_followup)
        assert "enable_brave_for_scope" in src, (
            "the adverse-media follow-up no longer enables Brave — a reconciled "
            "follow-up would run SearXNG-only and time out / partial")

    def test_it_is_env_gated_for_cost_control(self):
        """Must be flippable off (ARIA_DD_BRAVE_PRIMARY) — Brave is paid, and the
        operator's standing cost concern requires a kill switch."""
        for fn in (ddo.orchestrate_dd, ddo._run_adverse_media_followup):
            assert "ARIA_DD_BRAVE_PRIMARY" in inspect.getsource(fn)


class TestEnableBraveContract:
    """Guard the mechanism these rely on."""
    def test_enable_brave_for_scope_sets_the_flag(self):
        from aria_service.intel import web_search as ws
        # R-F3946 — the scope now carries a PURPOSE, not a bare bool: RULE ONE
        # confines Brave to DD, and a boolean cannot express "who is asking".
        # The round-trip contract this guards is unchanged in substance — set,
        # observe, clear — so it is re-expressed rather than deleted.
        ws.enable_brave_for_scope(True, purpose="dd")
        assert ws._BRAVE_CTX.get() == "dd"
        ws.enable_brave_for_scope(False)
        assert ws._BRAVE_CTX.get() == ""
