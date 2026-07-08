"""R-F2503 — the Poland KRS OdpisPelny API is NUMBER-ONLY (a name search 404s). A
name-only lookup resolves the KRS number via GLEIF's registered_as, fetches the rich
extract, and trusts it ONLY if the fetched name verifies against the query — a wrong
GLEIF/registered_as match must never surface another company's officers.
"""
from aria_service.intel.registry_adapters import _pl_name_matches


def test_name_match_accepts_same_entity():
    # PKN Orlen ~ its full legal name (shared distinctive token 'orlen')
    assert _pl_name_matches("POLSKI KONCERN NAFTOWY ORLEN SA", "PKN Orlen") is True
    assert _pl_name_matches("ORLEN Spolka Akcyjna", "Orlen") is True
    assert _pl_name_matches("CD Projekt SA", "CD Projekt") is True


def test_name_match_rejects_different_entity():
    # the safety guard: a wrong resolved KRS must be discarded
    assert _pl_name_matches("Completely Different Sp z o o", "PKN Orlen") is False
    assert _pl_name_matches("Bank Pekao SA", "PKN Orlen") is False
    assert _pl_name_matches("", "PKN Orlen") is False
    assert _pl_name_matches("PKN Orlen", "") is False


if __name__ == "__main__":
    test_name_match_accepts_same_entity(); print("PASS accepts_same_entity")
    test_name_match_rejects_different_entity(); print("PASS rejects_different_entity")
    print("ALL PASS")
