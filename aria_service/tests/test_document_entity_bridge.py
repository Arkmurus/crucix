"""Tests for the document-to-entity bridge.

Extracted from tonight's F3 PDF DD: when operator says "run DD on
this company in the attached document", the orchestrator was running
on the pronoun. Fix: detect pronoun, parse the [ATTACHED DOCUMENT]
block, extract real entity + jurisdiction + address + registration.
"""
from __future__ import annotations


# Canonical fixture — the Florida Sunbiz PDF ARIA processed tonight
F3_SUNBIZ_DOC = """Detail by Entity Name

Florida Limited Liability Company
F3 INTERNATIONAL RESOURCES, LLC

Filing Information
Document Number:  L20000121167
FEI/EIN Number:   99-3074982
Date Filed:       05/05/2020
State:            FL
Status:           ACTIVE
Last Event:       REINSTATEMENT
Event Date Filed: 11/01/2023

Principal Address:
3363 NE 163rd Street
#602
N. MIAMI BEACH, FL 33160

Mailing Address:
3363 NE 163rd Street
#602
N. MIAMI BEACH, FL 33160

Registered Agent Name & Address:
JACOBSON, LORIN
3363 NE 163rd Street
#602
N. MIAMI BEACH, FL 33160

Authorized Person(s) Detail:
Name & Address:
Title MGR
JACOBSON, LORIN
3363 NE 163rd Street
#602
N. MIAMI BEACH, FL 33160
"""


UK_CH_DOC = """Company overview for ACME INDUSTRIES LIMITED

Company number: 12345678
Registered office address: 1 Infinite Loop, London EC2A 4NE, England
Company type: Private limited Company
Incorporated on: 15 March 2015
Company status: Active

Directors
JOHN DOE
Appointed on 15 March 2015
Role: Director

JANE SMITH
Appointed on 20 June 2018
Role: Director
"""


# ═══════════════════════════════════════════════════════════════════════
# 1. Pronoun detection
# ═══════════════════════════════════════════════════════════════════════

def test_detects_this_company():
    from aria_service.intel.document_entity_bridge import is_pronoun_reference
    assert is_pronoun_reference("this company") is True
    assert is_pronoun_reference("This Company") is True
    assert is_pronoun_reference("this company.") is True


def test_detects_pronoun_with_qualifier():
    from aria_service.intel.document_entity_bridge import is_pronoun_reference
    assert is_pronoun_reference("this company within the attached document") is True
    assert is_pronoun_reference("the entity in the document") is True
    assert is_pronoun_reference("the attached company") is True


def test_does_not_flag_real_company_name():
    from aria_service.intel.document_entity_bridge import is_pronoun_reference
    assert is_pronoun_reference("F3 International Resources LLC") is False
    assert is_pronoun_reference("Rheinmetall AG") is False
    assert is_pronoun_reference("Acme Industries") is False


def test_pronoun_empty_string_false():
    from aria_service.intel.document_entity_bridge import is_pronoun_reference
    assert is_pronoun_reference("") is False
    assert is_pronoun_reference(None) is False  # type: ignore[arg-type]


# ═══════════════════════════════════════════════════════════════════════
# 2. Florida Sunbiz extraction (the F3 fixture)
# ═══════════════════════════════════════════════════════════════════════

def test_extracts_f3_entity_name():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    assert "F3 INTERNATIONAL RESOURCES" in result["name"].upper()
    assert "LLC" in result["name"].upper()


def test_extracts_f3_us_jurisdiction():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    assert result["jurisdiction_iso2"] == "US"


def test_extracts_f3_registration_number():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    assert result["registration_number"] == "L20000121167"


def test_extracts_f3_address():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    assert "3363 NE 163rd" in result["registered_address"]
    assert "33160" in result["registered_address"]


def test_extracts_f3_incorporation_date():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    # Date Filed 05/05/2020 → 2020-05-05
    assert result["incorporation_date"] == "2020-05-05"


def test_extracts_f3_director():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(F3_SUNBIZ_DOC)
    dirs = [d["name"] for d in result["directors"]]
    assert any("JACOBSON" in d for d in dirs)


# ═══════════════════════════════════════════════════════════════════════
# 3. UK Companies House extraction
# ═══════════════════════════════════════════════════════════════════════

def test_extracts_uk_company_name():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(UK_CH_DOC)
    assert "ACME" in result["name"].upper()
    assert "LIMITED" in result["name"].upper() or "LTD" in result["name"].upper()


def test_extracts_uk_jurisdiction():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(UK_CH_DOC)
    assert result["jurisdiction_iso2"] == "GB"


def test_extracts_uk_company_number():
    from aria_service.intel.document_entity_bridge import extract_entity_from_document
    result = extract_entity_from_document(UK_CH_DOC)
    assert result["registration_number"] == "12345678"


# ═══════════════════════════════════════════════════════════════════════
# 4. End-to-end bridge_from_message
# ═══════════════════════════════════════════════════════════════════════

def test_bridge_returns_none_without_attached_doc():
    from aria_service.intel.document_entity_bridge import bridge_from_message
    result = bridge_from_message("DD on this company", entity_hint="this company")
    assert result is None


def test_bridge_extracts_on_pronoun_with_doc():
    from aria_service.intel.document_entity_bridge import bridge_from_message
    msg = (
        "Aria, DD on this company in the attached document\n\n"
        "[ATTACHED DOCUMENT: sunbiz.pdf]\n"
        f"{F3_SUNBIZ_DOC}\n"
        "[END ATTACHED DOCUMENT]\n"
    )
    result = bridge_from_message(msg, entity_hint="this company")
    assert result is not None
    assert "F3 INTERNATIONAL RESOURCES" in result["name"].upper()
    assert result["jurisdiction_iso2"] == "US"
    assert result["registration_number"] == "L20000121167"


def test_bridge_preserves_real_entity_hint():
    """When the caller passed a REAL entity name (not a pronoun),
    keep it. Don't override with the document's extracted name."""
    from aria_service.intel.document_entity_bridge import bridge_from_message
    msg = (
        "Aria, DD on Rheinmetall AG — also see this attachment\n\n"
        "[ATTACHED DOCUMENT: sunbiz.pdf]\n"
        f"{F3_SUNBIZ_DOC}\n"
        "[END ATTACHED DOCUMENT]\n"
    )
    result = bridge_from_message(msg, entity_hint="Rheinmetall AG")
    assert result is not None
    assert result["name"] == "Rheinmetall AG"
    # But we still enrich with document fields — hint is kept
    assert result.get("_hint_kept") is True


def test_bridge_returns_none_when_doc_has_no_entity():
    from aria_service.intel.document_entity_bridge import bridge_from_message
    msg = (
        "Aria, DD on this company\n\n"
        "[ATTACHED DOCUMENT: random.txt]\n"
        "Random text content with no corporate registry markers.\n"
        "[END ATTACHED DOCUMENT]\n"
    )
    result = bridge_from_message(msg, entity_hint="this company")
    assert result is None


# ═══════════════════════════════════════════════════════════════════════
# 5. Integration — _detect_dd_intent wires the bridge
# ═══════════════════════════════════════════════════════════════════════

def test_detect_dd_intent_uses_bridge_on_pronoun():
    """End-to-end: DD intent on a pronoun + attached doc must return
    the extracted entity as the intent target."""
    from aria_service.routes.aria import _detect_dd_intent
    msg = (
        "Aria, can you do a deep DD on this company within the attached document\n\n"
        "[ATTACHED DOCUMENT: sunbiz.pdf]\n"
        f"{F3_SUNBIZ_DOC}\n"
        "[END ATTACHED DOCUMENT]\n"
    )
    intent = _detect_dd_intent(msg)
    assert intent is not None
    # Should be the real entity, not "this company"
    assert "F3 INTERNATIONAL RESOURCES" in (intent.get("name") or "").upper()
    assert intent.get("jurisdiction_iso2") == "US"
    assert intent.get("registration_number") == "L20000121167"
    assert intent.get("_bridged_from_document") is True


def test_detect_dd_intent_without_attached_doc_unchanged():
    """Existing behaviour must not regress — non-pronoun DD intent
    without a document still works normally."""
    from aria_service.routes.aria import _detect_dd_intent
    intent = _detect_dd_intent("DD on Rheinmetall AG")
    assert intent is not None
    assert intent.get("name") == "Rheinmetall AG"
    assert not intent.get("_bridged_from_document")
