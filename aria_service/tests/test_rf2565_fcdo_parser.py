"""R-F2565 — FCDO/OFSI parser reads the live flat "2022format".

Regression guard: the previous parser looked for a nested <Name> element that the
live feed does not contain, so it returned 0 records from a 19.7k-target feed
(silent never-false-clean hole). This fixture mirrors the real schema (name1..name5
+ Name6, GroupID grouping, AliasType) and asserts entities parse with the primary
name + aliases folded together.
"""
from __future__ import annotations

from aria_service.intel.sources import fcdo_sanctions as fc

# Two rows sharing GroupID 15672 (one primary + one AKA) and one standalone entity.
_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<ArrayOfFinancialSanctionsTarget>
  <FinancialSanctionsTarget>
    <Name6>Haq</Name6>
    <name1>Mian</name1>
    <name2>Abdul</name2>
    <Address1>Bharchundi Sharif</Address1>
    <Country>Pakistan</Country>
    <GroupTypeDescription>Individual</GroupTypeDescription>
    <AliasType>Primary name</AliasType>
    <RegimeName>Global Human Rights</RegimeName>
    <DateDesignated>2022-12-09T00:00:00</DateDesignated>
    <UKSanctionsListRef>GHR0086</UKSanctionsListRef>
    <GroupID>15672</GroupID>
  </FinancialSanctionsTarget>
  <FinancialSanctionsTarget>
    <Name6>Mithoo</Name6>
    <name1>Mian</name1>
    <GroupTypeDescription>Individual</GroupTypeDescription>
    <AliasType>AKA</AliasType>
    <RegimeName>Global Human Rights</RegimeName>
    <GroupID>15672</GroupID>
  </FinancialSanctionsTarget>
  <FinancialSanctionsTarget>
    <Name6>ZAPCHASTTRADE LLP</Name6>
    <Address1>Almaty</Address1>
    <Country>Kazakhstan</Country>
    <GroupTypeDescription>Entity</GroupTypeDescription>
    <AliasType>Primary name</AliasType>
    <RegimeName>Russia</RegimeName>
    <DateDesignated>2024-11-07T00:00:00</DateDesignated>
    <GroupID>16636</GroupID>
  </FinancialSanctionsTarget>
</ArrayOfFinancialSanctionsTarget>"""


def test_parses_flat_2022format_into_entities():
    recs = fc._parse_xml(_SAMPLE)
    # 2 GroupIDs -> 2 entity records (the 2 rows of 15672 fold into one).
    assert len(recs) == 2, f"expected 2 grouped entities, got {len(recs)}"
    by_gid = {r["group_id"]: r for r in recs}

    haq = by_gid["15672"]
    assert haq["name"] == "Mian Abdul Haq"          # name1 name2 Name6, primary row
    assert "Mian Mithoo" in haq["aliases"]          # the AKA row folded in as alias
    assert haq["regime"] == "Global Human Rights"
    assert haq["entity_type"] == "Individual"
    assert haq["designation_date"].startswith("2022-12-09")
    assert "Pakistan" in haq["address"]
    assert haq["uk_ref"] == "GHR0086"

    llp = by_gid["16636"]
    assert llp["name"] == "ZAPCHASTTRADE LLP"
    assert llp["regime"] == "Russia"
    assert llp["entity_type"] == "Entity"


def test_no_name_wrapper_is_no_longer_required():
    """Explicit guard against the regression: a flat row with NO <Name> subtree must
    still yield a named record (the old parser returned 0 here)."""
    recs = fc._parse_xml(_SAMPLE)
    assert all(r["name"] for r in recs)             # every record has a real name
    assert len(recs) > 0                            # the whole feed is not silently dropped


def test_non_latin_name_captured_as_alias():
    """R-F2565 review #1: a row carrying a non-Latin script name must surface it as an
    alias so native-script screen queries match."""
    xml = """<?xml version="1.0"?>
    <ArrayOfFinancialSanctionsTarget>
      <FinancialSanctionsTarget>
        <Name6>ZADACHIN</Name6>
        <name1>Andrey</name1>
        <NameNonLatinScript>ЗАДАЧИН Андрей</NameNonLatinScript>
        <AliasType>Primary name</AliasType>
        <RegimeName>Russia</RegimeName>
        <GroupID>15890</GroupID>
      </FinancialSanctionsTarget>
    </ArrayOfFinancialSanctionsTarget>"""
    recs = fc._parse_xml(xml)
    assert len(recs) == 1
    assert recs[0]["name"] == "Andrey ZADACHIN"
    assert "ЗАДАЧИН Андрей" in recs[0]["aliases"]


def test_non_latin_only_row_is_never_dropped():
    """R-F2565 review #1 (never-false-clean edge): a designation whose ONLY name is
    non-Latin must NOT vanish (that would be a false clean)."""
    xml = """<?xml version="1.0"?>
    <ArrayOfFinancialSanctionsTarget>
      <FinancialSanctionsTarget>
        <NameNonLatinScript>الاسم الكامل</NameNonLatinScript>
        <AliasType>Primary name</AliasType>
        <RegimeName>Test</RegimeName>
        <GroupID>99999</GroupID>
      </FinancialSanctionsTarget>
    </ArrayOfFinancialSanctionsTarget>"""
    recs = fc._parse_xml(xml)
    assert len(recs) == 1                           # NOT dropped
    assert recs[0]["name"] == "الاسم الكامل"        # surfaced via the non-Latin fallback


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
