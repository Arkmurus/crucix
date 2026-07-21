"""R-F2839 — GLEIF must not report its LEI registration date as the incorporation date.

THE DEFECT, found by comparing a live ARIA report against a competitor's on the same
entity (SOCAR Trading SA, run dd_7bd81330d43d):

    ARIA identity.incorporation_date : 2013-05-28
    NorthRow (authoritative)          : 20/12/2007
    GLEIF entity.creationDate         : 2007-12-17   <- the field we SHOULD have used

`date_of_creation` is a COMPANIES HOUSE field name, where it genuinely means the
incorporation date — and dd_orchestrator.py:3575/:3731 correctly assign it to
`identity.incorporation_date`. The GLEIF adapter reused that load-bearing key for
`registration.initialRegistrationDate`, which is when the LEI was issued, not when the
company was formed. So both consumers were right and the SOURCE was wrong, which is why
this is fixed in gleif.py rather than at the two call sites.

Result: every GLEIF-sourced entity carried an incorporation date that was wrong by the
gap between formation and LEI issuance — here SIX YEARS — stated as fact on a
customer-visible report. Same family as R-F2838: not a false CLEAN, but a confident,
specific, WRONG assertion put in front of a decision-maker.

GLEIF does carry the real thing (`entity.creationDate`), so the fix is to use the
correct field, and to surface the LEI date separately under an honest name rather than
discard it.
"""
import pytest

from aria_service.intel.sources import gleif


# The exact shape the GLEIF v1 API returns (verified live against SOCAR Trading SA).
LIVE_SHAPE = {
    "entity": {
        "legalName": {"name": "SOCAR Trading SA"},
        "jurisdiction": "CH",
        "status": "ACTIVE",
        "registeredAs": "CHE-113.990.112",
        "creationDate": "2007-12-17T00:00:00Z",
        "legalForm": {"id": "MVII"},
        "legalAddress": {
            "addressLines": ["Rue du Rhône 40"], "city": "Genève",
            "region": "CH-GE", "postalCode": "1204", "country": "CH",
        },
    },
    "registration": {"initialRegistrationDate": "2013-05-28T17:08:00Z"},
}


def _profile(attrs):
    """Build the adapter's profile from a raw GLEIF attributes block."""
    return gleif.build_profile(attrs, lei="549300LYNZDH07L9NG18")


def test_incorporation_uses_entity_creation_not_lei_registration():
    """CAPABILITY: the live SOCAR case — 2007, not 2013."""
    p = _profile(LIVE_SHAPE)
    assert p["date_of_creation"] == "2007-12-17", (
        f"got {p['date_of_creation']!r}. registration.initialRegistrationDate is when "
        "the LEI was ISSUED (2013-05-28); entity.creationDate is when the company was "
        "FORMED (2007-12-17). Shipping the former as the incorporation date was wrong "
        "by six years on a customer-visible report."
    )


def test_the_lei_date_is_kept_under_an_honest_name():
    """The LEI date is useful — it just isn't the incorporation date."""
    p = _profile(LIVE_SHAPE)
    assert p.get("lei_registered_date") == "2013-05-28", (
        "the LEI registration date should be surfaced, not discarded — but under a "
        "name that says what it is"
    )


def test_missing_creation_date_is_UNKNOWN_not_the_lei_date():
    """USP: absence must stay empty, never silently fall back to the wrong field.

    Falling back to initialRegistrationDate would reintroduce the exact defect for
    every record whose creationDate happens to be absent.
    """
    attrs = {
        "entity": {"legalName": {"name": "X"}, "jurisdiction": "CH"},
        "registration": {"initialRegistrationDate": "2013-05-28T17:08:00Z"},
    }
    p = _profile(attrs)
    assert not p.get("date_of_creation"), (
        f"got {p.get('date_of_creation')!r} — with no entity.creationDate the honest "
        "answer is empty/UNKNOWN. A wrong date is worse than no date."
    )
    assert p.get("lei_registered_date") == "2013-05-28"


def test_dates_are_normalised_to_iso_day():
    """GLEIF returns full timestamps; downstream expects YYYY-MM-DD."""
    p = _profile(LIVE_SHAPE)
    assert p["date_of_creation"] == "2007-12-17"
    assert "T" not in p["date_of_creation"]


def test_other_registry_fields_survive():
    """ANTI-REGRESSION: R-F2838 showed GLEIF already supplies most of the CH gap."""
    p = _profile(LIVE_SHAPE)
    assert p["registered_as"] == "CHE-113.990.112"
    assert p["company_status"] == "active"
    assert p["jurisdiction"] == "CH"
    assert "Rue du Rh" in p["registered_office_address"]


def test_companies_house_semantics_are_untouched():
    """CH's own date_of_creation IS incorporation — the fix must not touch it."""
    import inspect
    src = inspect.getsource(
        __import__("aria_service.intel.companies_house", fromlist=["x"])
    )
    assert '"date_of_creation": data.get("date_of_creation")' in src, (
        "Companies House genuinely reports incorporation under this key; this fix is "
        "scoped to the GLEIF adapter, which borrowed the name for different data"
    )
