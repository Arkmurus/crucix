"""R-F2772 — the inbound_leads limb must appear in the self-diagnostic catalogue.

inbound_leads is a brain topic (brain_hook._MODULE_TOPICS) wired at POST
/api/aria/leads/inbound, but was ABSENT from self_diagnostic._MODULES (Codex R-F2757),
leaving that limb invisible to diagnostic/source-health surfaces. This guards against
re-drift and confirms the entry points at a real, existing handler.
"""
from __future__ import annotations

from aria_service.intel import self_diagnostic as sd


def test_rf2772_inbound_leads_in_catalogue():
    names = {m["name"] for m in sd._MODULES}
    assert "inbound_leads" in names, "inbound_leads limb missing from self-diagnostic catalogue"


def test_rf2772_inbound_leads_entry_points_at_real_handler():
    import importlib
    entry = next(m for m in sd._MODULES if m["name"] == "inbound_leads")
    mod = importlib.import_module(entry["module"])
    assert hasattr(mod, entry["entry"]), f"{entry['module']}.{entry['entry']} must exist"
    assert entry.get("brain_registered") is True
