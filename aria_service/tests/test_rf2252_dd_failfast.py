"""R-F2252 — DD fail-fast for sparse targets + honest gaps.

A no-CNPJ/no-website foreign entity (like the DD that timed the operator out) burns
the full I/O-bound budgets on empty external calls. The 2× digital budget is for
website link-tree mining an entity with no website can't use → cap at 1×. Plus WB
errors are now legible and the BR 'no directors' gap is honest about the CNPJ need.
"""
from __future__ import annotations
from pathlib import Path

_DDO = (Path(__file__).resolve().parent.parent / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
_NW = (Path(__file__).resolve().parent.parent / "intel" / "network_walker.py").read_text(encoding="utf-8")


def test_digital_budget_is_sparse_aware():
    # the digital layer caps at 1× (not a fixed 2×) when there's no website
    assert "_has_site" in _DDO
    assert "DEFAULT_LAYER_TIMEOUT_S * (2 if _has_site else 1)" in _DDO
    # and the old unconditional 2× timeout is gone from that call site
    assert "timeout=_clamp(DEFAULT_LAYER_TIMEOUT_S * 2)" not in _DDO


def test_has_site_checks_the_real_website_fields():
    # must key off the same fields _run_digital mines from (website/url/domain)
    for f in ("website", "website_url", "url", "domain"):
        assert f'target.get("{f}")' in _DDO


def test_registry_gap_is_honest_about_reg_number():
    assert "registration/company number" in _NW   # explains WHY there's no data
    assert "a CNPJ for BR" in _NW                  # the concrete example
    assert "no registry adapter returned data" not in _NW  # the misleading old text is gone
