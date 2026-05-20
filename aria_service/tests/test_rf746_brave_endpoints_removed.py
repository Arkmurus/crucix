"""R-F746 — Brave Answers HTTP endpoints removed from the route surface.

R-F320 (2026-05-11) turned the underlying `brave_answers` module into a
permanent stub after the operator declined Brave per the
`aria_mirrors_claude` doctrine. Despite that, the public-facing routes
`GET /api/aria/research/brave-answer` and
`GET /api/aria/research/brave-answer/spend` continued to exist and
always returned the "removed" sentinel — they were dead code on the
536-endpoint surface (audit on 2026-05-20 flagged this as P1 dead
code).

R-F746 removes the two routes. The brave_answers module itself stays
(it's a mock-attach target for test_brave_removed_rf320, test_rf336,
and test_dd_ecosystem capability tests that prove the stub stays cold).

This test enforces the removal — if anyone re-adds the routes, the
surface grows on the wrong side of the "ARIA mirrors Claude" principle
and this fails loudly.
"""
from __future__ import annotations

import importlib


def _route_paths() -> set[str]:
    """Return the set of registered FastAPI route paths."""
    routes_mod = importlib.import_module("aria_service.routes.aria")
    paths: set[str] = set()
    router = getattr(routes_mod, "router", None)
    if router is None:
        return paths
    for r in router.routes:
        path = getattr(r, "path", None)
        if path:
            paths.add(path)
    return paths


def test_rf746_brave_answer_route_is_gone():
    paths = _route_paths()
    assert "/research/brave-answer" not in paths, (
        "R-F746 regression: /research/brave-answer route still registered. "
        "The brave_answers module is a permanent stub since R-F320; the "
        "route was dead surface area. Remove it or document why."
    )


def test_rf746_brave_answer_spend_route_is_gone():
    paths = _route_paths()
    assert "/research/brave-answer/spend" not in paths, (
        "R-F746 regression: /research/brave-answer/spend route still "
        "registered. Spend is structurally zero since R-F320 — the "
        "endpoint had no purpose other than confirming that."
    )


def test_rf746_brave_answers_stub_module_still_importable():
    """Conservatively keep the stub — mock-target for test_brave_removed_
    rf320 + test_rf336 + test_dd_ecosystem. Deleting the module would
    break those guards."""
    mod = importlib.import_module("aria_service.intel.brave_answers")
    assert hasattr(mod, "ask")
    assert hasattr(mod, "fetch_answer")
    assert hasattr(mod, "is_enabled")
    assert mod.is_enabled() is False
