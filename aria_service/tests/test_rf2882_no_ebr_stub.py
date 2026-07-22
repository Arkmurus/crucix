"""R-F2882 — the dead European Business Register signup stub must stay removed.

EBR was a legacy email-form PortalDef pointing at the DEFUNCT ebr.org with no API.
BRIS/EBR has no open bulk API (EU access is per-member-state registries), so it was
dead weight masquerading as a company-data source. Real EU coverage = the per-state
registry adapters (R-F2881's coverage panel). This ratchet keeps the stub gone.
"""
from aria_service.intel.portal_registry import PORTALS


def test_rf2882_no_defunct_ebr_url():
    bad = [p.id for p in PORTALS if "ebr.org" in (getattr(p, "url", "") or "").lower()]
    assert not bad, f"the dead ebr.org stub (no API) must stay removed, found: {bad}"


def test_rf2882_no_european_business_register_portal():
    assert "european_business_register" not in {p.id for p in PORTALS}
