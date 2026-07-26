"""R-F3091 — one company name at the top, five companies' worth of evidence below.

LIVE DEFECT (Mitie, operator report 2026-07-26). The registry layer described
MITIE FACILITIES MANAGEMENT LIMITED (02938041) — a subsidiary whose PSC is Mitie
Treasury Management Limited. Every other layer described Mitie Group PLC, the listed
parent: the £3.1bn OCS recommended acquisition, the mitie.com annual reports, the
USAspending awards, the Deloitte audit fine. The report never said which entity any
layer was about, so group evidence and subsidiary evidence were silently blended.

The fix states scope; it never silently re-points the report at the parent. A layer
resolved by REGISTRATION NUMBER describes one exact legal person; a layer resolved by
NAME SEARCH describes a brand, and for a subsidiary the brand is usually the group.
Those are different claims and are now labelled as different claims.
"""
from aria_service.intel import dd_schema


def _mitie_report():
    return {
        "identity": {
            "entity_name": "MITIE FACILITIES MANAGEMENT LIMITED",
            "entity_type": "company",
            "registration_number": "02938041",
            "jurisdiction": "United Kingdom",
            "shareholders": [{
                "name": "Mitie Treasury Management Limited",
                "kind": "corporate-entity-person-with-significant-control",
                "registration_number": "07351242",
            }],
        },
        "network": {
            "controlled_by": [{
                "controller_name": "Mitie Treasury Management Limited",
                "controller_registration_number": "07351242",
                "controller_country_registered": "England",
            }],
            "ubo_chain": [{"name": "MITIE FACILITIES MANAGEMENT LIMITED"},
                          {"name": "Mitie Treasury Management Limited"}],
        },
    }


def test_rf3091_subsidiary_is_detected_and_its_parent_named():
    sc = dd_schema._dd_entity_scope(_mitie_report())
    assert sc["is_subsidiary"] is True
    assert sc["immediate_parent"]["name"] == "Mitie Treasury Management Limited"
    assert sc["immediate_parent"]["registration_number"] == "07351242"
    assert sc["immediate_parent"]["anchored"] is True


def test_rf3091_registry_layers_are_scoped_to_the_exact_legal_entity():
    layers = {l["key"]: l for l in dd_schema._dd_entity_scope(_mitie_report())["layers"]}
    for key in ("identity", "network"):
        assert layers[key]["group_scope"] is False
        assert layers[key]["scope"] == "MITIE FACILITIES MANAGEMENT LIMITED"
        assert layers[key]["basis"] == "registry identifier"


def test_rf3091_name_search_layers_are_flagged_as_group_scope():
    """THE LIVE SYMPTOM: press/financials/awards were group-level and unlabelled."""
    layers = {l["key"]: l for l in dd_schema._dd_entity_scope(_mitie_report())["layers"]}
    for key in ("digital", "compliance"):
        assert layers[key]["group_scope"] is True
        assert "group" in layers[key]["scope"]


def test_rf3091_scope_warning_states_the_reading_rule():
    warn = " ".join(dd_schema._dd_entity_scope(_mitie_report())["warnings"])
    assert "MITIE FACILITIES MANAGEMENT LIMITED" in warn and "02938041" in warn
    assert "Mitie Treasury Management Limited" in warn
    assert "name search" in warn and "wider group" in warn


def test_rf3091_standalone_company_gets_no_group_warning():
    """The label must not fire on every company — that would be noise."""
    sc = dd_schema._dd_entity_scope({
        "identity": {"entity_name": "Acme Ltd", "entity_type": "company",
                     "registration_number": "12345678"},
        "network": {},
    })
    assert sc["is_subsidiary"] is False
    assert sc["warnings"] == []
    assert all(l["group_scope"] is False for l in sc["layers"])


def test_rf3091_person_is_never_a_subsidiary():
    sc = dd_schema._dd_entity_scope({
        "identity": {"entity_name": "Charles Woodburn", "entity_type": "person"},
        "network": {"controlled_by": [{"controller_name": "Some Holdco Ltd"}]},
    })
    assert sc["is_subsidiary"] is False


def test_rf3091_unanchored_controller_says_the_chain_was_not_walked():
    sc = dd_schema._dd_entity_scope({
        "identity": {"entity_name": "Acme Ltd", "entity_type": "company"},
        "network": {"controlled_by_unanchored": [{"controller_name": "Opaque Holdings SA"}]},
    })
    assert sc["is_subsidiary"] is True
    joined = " ".join(sc["warnings"])
    assert "NOT walked" in joined and "ultimate parent is UNKNOWN" in joined


def test_rf3091_scope_never_silently_repoints_the_subject():
    """Swapping the subject to the parent without saying so would be its own
    fabrication — the subject must remain the entity that was screened."""
    sc = dd_schema._dd_entity_scope(_mitie_report())
    assert sc["subject_name"] == "MITIE FACILITIES MANAGEMENT LIMITED"
    assert sc["subject_registration"] == "02938041"


# ── the user-visible surface ───────────────────────────────────────────────
def test_rf3091_structured_view_carries_scope_and_stamps_every_section():
    """CAPABILITY: drive `structured_view`, the contract the online report renders."""
    sv = dd_schema.structured_view(_mitie_report())
    assert sv["entity_scope"]["is_subsidiary"] is True

    by_key = {s["key"]: s for s in sv["sections"]}
    assert by_key["identity"]["scope_is_group"] is False
    assert by_key["digital"]["scope_is_group"] is True
    assert "group" in by_key["digital"]["scope_entity"]
    assert by_key["network"]["scope_entity"] == "MITIE FACILITIES MANAGEMENT LIMITED"
