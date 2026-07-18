"""R-F2730 — the REAL relationship-graph edge is now flipped in: an anchored
`controlled_by` from a corporate PSC's own registry number (R-F2726).

R-F2703 disabled the name-match source ("truthful noise degrades the grade") and
left the source-agnostic writer machinery ready for a REAL edge. This wires it: a
corporate PSC identified by its registry number resolves to the SAME canonical id a
DD of that controller would produce, so the edge lights up "Related Cases" and is
Grade-A evidence, not a fuzzy guess. Individuals / un-anchorable controllers are
DROPPED, never fabricated. Uses a REAL DDVault on a temp DB (real SQL, no mocks).
"""
import pytest

from aria_service.intel import dd_orchestrator as _do
from aria_service.intel import dd_vault as _dv
from aria_service.intel.dd_versioning import canonical_entity_id as _canon_id


@pytest.fixture()
def vault(tmp_path):
    v = _dv.DDVault(db_path=str(tmp_path / "vault.db"))
    yield v
    v.close()


_SUBJECT = "company:GB:11111111"


def _report(*, user_id="tenant_a", controlled_by=None):
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport()
    r.run_id = "run_2730"
    r.canonical_entity_id = _SUBJECT
    r.identity.entity_name = "Acme Ltd"
    r.identity.entity_type = "company"
    r.identity.jurisdiction_iso2 = "GB"
    r.user_id = user_id
    r.network.cross_linked_entities = []  # name-match source is OFF
    r.network.controlled_by = controlled_by if controlled_by is not None else [{
        "relationship": "controlled_by", "controller_name": "PARENT HOLDINGS LTD",
        "controller_registration_number": "09999999", "controller_country_registered": "England",
        "natures_of_control": ["ownership-of-shares-75-to-100-percent"],
        "anchor": "companies_house_psc_identification", "grade": "A",
    }]
    return r


def _edges(vault):
    return vault.get_cross_references(_SUBJECT)


def test_rf2730_anchored_controlled_by_edge_is_written_even_with_name_match_off_shipped_default(vault):
    """The controlled_by edge writes regardless of the (disabled) name-match flag.
    'shipped_default' in the name → the autouse fixture in the R-F2700 suite would skip
    patching; here we assert the real prod path (name-match OFF)."""
    assert _do._XREF_NAME_MATCH_ENABLED is False, "name-match must ship disabled"
    written, dropped = _do._write_relationship_edges(_report(), vault)
    assert written == 1 and dropped == 0
    rows = _edges(vault)
    assert len(rows) == 1
    e = rows[0]
    # DIRECTIONAL: subject controlled_by the controller (target = controller's own id).
    assert e["source_entity"] == _SUBJECT
    assert e["target_entity"] == _canon_id(
        entity_type="company", name="PARENT HOLDINGS LTD",
        jurisdiction_iso2="GB", registration_number="09999999")
    assert e["relationship"] == "controlled_by"
    assert "VERIFIED via registry number" in e["finding_summary"]
    assert "09999999" in e["finding_summary"]
    assert e["user_id"] == "tenant_a"


def test_rf2730_target_matches_a_real_dd_of_the_controller(vault):
    """The whole point: the anchored edge lights up 'Related Cases' when the CONTROLLER
    gets its own DD — because the target is the same canonical id that DD produces."""
    controller_id = _canon_id(entity_type="company", name="PARENT HOLDINGS LTD",
                              jurisdiction_iso2="GB", registration_number="09999999")
    vault.record_case(canonical_entity_id=controller_id, entity_name="PARENT HOLDINGS LTD",
                      risk_score=30.0, risk_level="LOW")
    _do._write_relationship_edges(_report(), vault)
    related = vault.get_related_cases(_SUBJECT)
    assert any(c.get("canonical_entity_id") == controller_id for c in related), \
        "the anchored edge must resolve to the controller's real case"


def test_rf2730_unanchorable_controllers_are_dropped_not_fabricated(vault):
    for bad in [
        {"controller_name": "No Regno Ltd", "controller_registration_number": "",
         "controller_country_registered": "England"},                       # no regnum
        {"controller_name": "Placeholder Ltd", "controller_registration_number": "N/A",
         "controller_country_registered": "England"},                       # placeholder regnum
        {"controller_name": "Foreign Ltd", "controller_registration_number": "ABC12345",
         "controller_country_registered": "Atlantis"},                      # un-mappable jurisdiction
        {"controller_name": "", "controller_registration_number": "09999999",
         "controller_country_registered": "England"},                       # no name
    ]:
        written, dropped = _do._write_relationship_edges(_report(controlled_by=[bad]), vault)
        assert written == 0 and dropped == 1, f"must DROP, not fabricate: {bad}"
    assert _edges(vault) == [], "no fabricated anchors in the graph"


def test_rf2730_fail_closed_without_attribution(vault):
    written, dropped = _do._write_relationship_edges(_report(user_id=None), vault)
    assert written == 0
    assert _edges(vault) == [], "no user_id → write nothing (R-F2697 fail-closed)"
