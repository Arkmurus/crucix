"""R-F2578 — uk_ofsi_ingest full name must include Name6 (surname/main).

The parser read Name6 into a separate field but dropped it from the assembled full name,
truncating e.g. "Mian Abdul Haq" -> "Mian Abdul". (The audit's separate claim that this
parser 0-extracts was FALSE — verified 19,761 entities against the live feed.)
"""
from __future__ import annotations

from aria_service.intel import uk_ofsi_ingest as u

_XML = b"""<?xml version="1.0"?>
<ArrayOfFinancialSanctionsTarget xmlns="http://schemas.hmtreasury.gov.uk/ofsi/consolidatedlist">
  <FinancialSanctionsTarget>
    <name1>Mian</name1>
    <name2>Abdul</name2>
    <Name6>Haq</Name6>
    <RegimeName>Global Human Rights</RegimeName>
    <UKSanctionsListRef>GHR0086</UKSanctionsListRef>
    <GroupID>15672</GroupID>
  </FinancialSanctionsTarget>
  <FinancialSanctionsTarget>
    <Name6>ZAPCHASTTRADE LLP</Name6>
    <RegimeName>Russia</RegimeName>
    <GroupID>16636</GroupID>
  </FinancialSanctionsTarget>
</ArrayOfFinancialSanctionsTarget>"""


def test_full_name_includes_name6():
    ents = u._parse_xml(_XML)
    assert len(ents) == 2
    haq = next(e for e in ents if e.get("ref") == "GHR0086")
    assert haq["name"] == "Mian Abdul Haq"      # was "Mian Abdul" before the fix


def test_name6_only_row_not_duplicated():
    ents = u._parse_xml(_XML)
    llp = next(e for e in ents if e.get("group_id") == "16636")
    assert llp["name"] == "ZAPCHASTTRADE LLP"   # name1 empty -> Name6 used, not doubled


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
