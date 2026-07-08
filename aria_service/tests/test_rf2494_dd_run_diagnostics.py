"""R-F2494 — the DD report must SHOW what actually ran (codex review #1/#2/#6):
search/Brave state, the registry-adapter attempt/result, and executed mode +
auto-deep escalation + layer counts. Before this, none of it was surfaced — a
reviewer could not tell whether Brave ran, whether the BR/CNPJ registry was
attempted, or whether the run escalated to deep.

Drives the REAL _build_run_diagnostics() + confirms structured_view() surfaces it.
"""
import asyncio

import aria_service.intel.web_search as _ws
from aria_service.intel import dd_orchestrator as dor
from aria_service.intel.dd_schema import ARKDDReport, Finding, structured_view


async def _mock_health():
    return {"brave_search": {"configured": True, "scope_enabled": True, "globally_disabled": False}}


def _report(*, mode="standard", reg_status=None, adapter_src=None, gaps=None, juris="BR"):
    r = ARKDDReport()
    r.orchestrator_mode = mode
    r.layers_run = ["identity", "digital", "synthesis"]
    r.layers_skipped = ["network"]
    r.total_duration_ms = 12345
    r.identity.jurisdiction = juris
    r.identity.registration_status = reg_status
    r.identity.data_gaps = gaps or []
    if adapter_src:
        r.identity.findings = [Finding(severity="info", title="Registry", detail="x", source=adapter_src)]
    return r


def _build(report, target, mode):
    orig = _ws.get_search_health
    _ws.get_search_health = _mock_health
    try:
        return asyncio.run(dor._build_run_diagnostics(report, target, mode))
    finally:
        _ws.get_search_health = orig


def test_registry_miss_surfaced_with_reason():
    r = _report(reg_status=None, gaps=["Registry lookup unavailable for BR — no adapter yet"])
    d = _build(r, {"jurisdiction_iso2": "BR"}, "standard")
    assert d["registry"]["attempted"] is True
    assert d["registry"]["result"] == "miss"
    assert "registry" in (d["registry"]["reason"] or "").lower()
    assert d["registry"]["jurisdiction"] == "BR"


def test_registry_hit_with_adapter():
    r = _report(reg_status="Active", adapter_src="registry_adapters.receita_ws")
    d = _build(r, {}, "standard")
    assert d["registry"]["result"] == "hit"
    assert d["registry"]["adapter"] == "receita_ws"


def test_mode_auto_escalation_captured():
    r = _report(mode="deep")  # executed=deep
    d = _build(r, {"_rf409_initial_mode": "standard"}, "deep")
    assert d["mode"]["requested"] == "standard"
    assert d["mode"]["executed"] == "deep"
    assert d["mode"]["auto_escalated"] is True


def test_mode_no_escalation_when_direct():
    r = _report(mode="standard")
    d = _build(r, {}, "standard")
    assert d["mode"]["auto_escalated"] is False
    assert d["mode"]["executed"] == "standard"


def test_layers_and_search_present():
    r = _report()
    d = _build(r, {}, "standard")
    assert d["layers"]["count_run"] == 3 and d["layers"]["count_skipped"] == 1
    assert d["layers"]["total_duration_ms"] == 12345
    assert d["search"]["brave"]["configured"] is True


def test_structured_view_surfaces_run_diagnostics():
    r = _report(reg_status="Active")
    r.run_diagnostics = _build(r, {}, "standard")
    sv = structured_view(r.as_dict())
    assert "run_diagnostics" in sv
    assert sv["run_diagnostics"]["registry"]["result"] == "hit"


if __name__ == "__main__":
    for fn in (test_registry_miss_surfaced_with_reason, test_registry_hit_with_adapter,
               test_mode_auto_escalation_captured, test_mode_no_escalation_when_direct,
               test_layers_and_search_present, test_structured_view_surfaces_run_diagnostics):
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS")
