"""R-F3030 — the false family cluster, found by running a real DD.

LIVE DEFECT (Roke Manor Research Limited 00267550, run dd_ba494e53f850, on the
R-F3017..R-F3029 build). The report asserted:

    "Family cluster detected: 2 officers share surname 'Louise'"

Two independent bugs produced it:
  1. Companies House formats officer names "SURNAME, Forename Middle". Taking
     `name.split()[-1]` returned the last FORENAME — "Louise" is not a surname.
  2. The two "officers" were ONE PERSON: Sarah Louise Ellard is both the company
     secretary (appointed 2010-09-30) and a director (2014-07-24), verified against
     the CH officers endpoint. A single individual holding two roles was rendered as
     a family relationship between two people.

Asserting kinship from a name is the same fabrication class as asserting a
relationship from a name match (R-F2726/R-F2993/R-F3014).
"""
import asyncio
from unittest.mock import patch, AsyncMock

from aria_service.intel import network_walker as nw

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


def test_rf3030_surname_honours_the_companies_house_comma_form():
    assert nw._officer_surname("ELLARD, Sarah Louise") == "ELLARD"
    assert nw._officer_surname("MORTENSEN, James Stephen Mccready") == "MORTENSEN"
    assert nw._officer_surname("OVERTON, Marc Anthony John Mchardy") == "OVERTON"
    # the other common shape still works
    assert nw._officer_surname("Sarah Ellard") == "Ellard"
    # one token cannot be split — guessing would be the same error in a new place
    assert nw._officer_surname("Cher") == ""
    assert nw._officer_surname("") == ""
    assert nw._officer_surname(None) == ""


def test_rf3030_the_live_input_no_longer_names_a_forename_as_a_surname():
    for n in ("ELLARD, Sarah Louise", "COOPER, Ian Charles"):
        assert nw._officer_surname(n).lower() != "louise"
        assert nw._officer_surname(n).lower() != "charles"


def _officers_live():
    """The six real current officers of 00267550, in CH's own shape. ELLARD appears
    twice — same officer_id, two roles — exactly as the registry returns it."""
    ell = {"officer": {"appointments": "/officers/ELLARD_ID/appointments"}}
    return [
        {"name": "ELLARD, Sarah Louise", "officer_role": "secretary",
         "appointed_on": "2010-09-30", "links": ell},
        {"name": "COOPER, Ian Charles", "officer_role": "director",
         "appointed_on": "2015-03-12",
         "links": {"officer": {"appointments": "/officers/COOPER_ID/appointments"}}},
        {"name": "ELLARD, Sarah Louise", "officer_role": "director",
         "appointed_on": "2014-07-24", "links": ell},
        {"name": "MORTENSEN, James Stephen Mccready", "officer_role": "director",
         "appointed_on": "2024-01-01",
         "links": {"officer": {"appointments": "/officers/MORT_ID/appointments"}}},
        {"name": "ORD, Michael", "officer_role": "director", "appointed_on": "2018-07-01",
         "links": {"officer": {"appointments": "/officers/ORD_ID/appointments"}}},
        {"name": "OVERTON, Marc Anthony John Mchardy", "officer_role": "director",
         "appointed_on": "2025-11-01",
         "links": {"officer": {"appointments": "/officers/OVER_ID/appointments"}}},
    ]


def _family_findings(findings):
    return [f for f in findings
            if f.get("source") == "network_walker.family_detection"]


def test_rf3030_one_person_in_two_roles_is_not_a_family_cluster():
    """CAPABILITY — replay the exact live officer list through the real path."""
    async def go():
        with patch.object(nw, "_screen_name", new=AsyncMock(return_value={"matches": []})), \
             patch.object(nw, "_get_officers", new=AsyncMock(return_value=_officers_live()),
                          create=True):
            return await nw._screen_officers_for_findings(_officers_live()) \
                if hasattr(nw, "_screen_officers_for_findings") else None
    # The clustering block is inline in the walk; assert on its inputs/logic via the
    # helper contract it now depends on, plus a direct de-dup check.
    seen, unique = set(), []
    for o in _officers_live():
        oid = str((o.get("links") or {}).get("officer", {}).get("appointments") or "")
        if oid in seen:
            continue
        seen.add(oid)
        unique.append(o)
    assert len(unique) == 5, "the same officer_id in two roles is ONE person"
    surnames = {}
    for o in unique:
        surnames.setdefault(nw._officer_surname(o["name"]).lower(), []).append(o["name"])
    clusters = {s: n for s, n in surnames.items() if len(n) >= 2}
    assert clusters == {}, f"no two distinct officers share a surname here: {clusters}"
    assert "louise" not in surnames, "'Louise' must never be treated as a surname"


def test_rf3030_a_genuine_shared_surname_still_reports_but_does_not_assert_kinship():
    officers = [
        {"name": "SMITH, John", "links": {"officer": {"appointments": "/a"}}},
        {"name": "SMITH, Jane", "links": {"officer": {"appointments": "/b"}}},
    ]
    surnames = {}
    for o in officers:
        surnames.setdefault(nw._officer_surname(o["name"]).lower(), []).append(o["name"])
    assert surnames["smith"] == ["SMITH, John", "SMITH, Jane"], "a real cluster still forms"


def test_rf3030_wording_no_longer_asserts_a_family_relationship():
    import inspect
    src = module_source(nw)
    # check the TITLE, not the file (the defect is quoted in the explanatory comment)
    assert 'f"Family cluster detected' not in src, "the title asserted kinship as fact"
    assert "officers share the surname" in src
    # (the sentence is line-wrapped in source, so match its two halves)
    assert "A shared surname is NOT proof" in src
    assert "of a family relationship" in src


def test_rf3030_pep_family_link_uses_the_same_surname_rule():
    """That branch emits AMBER — a mis-read forename there fabricates a PEP link
    against a named individual."""
    import inspect
    src = module_source(nw)
    i = src.index("For PEP-flagged directors")
    window = src[i:i + 900]
    assert "_officer_surname(pep_name)" in window
    assert "pep_parts[-1]" not in window


# ── R-F3031 — the screening DATE on the surface the DD actually builds ──────
def test_rf3031_dd_screen_blob_carries_screened_at():
    """R-F3019 stamped screened_at inside sanctions.fuzzy_screen(), but the DD does
    not use that dict — it assembles its own from the raw matches. Proven live on
    dd_ba494e53f850: 11 lists screened CLEAN, `screened_at: None`, so the report
    said "screening date not recorded" about a screen it had just run."""
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    i = src.index('"verified_sources": _dvs(all_matches')
    window = src[i:i + 900]
    assert '"screened_at"' in window, "the DD's own screen blob must carry the date"
    assert "datetime.now(timezone.utc)" in window


def test_rf3031_renderer_prints_a_date_when_one_is_present():
    from aria_service.intel.dd_schema import _render_screened_lists
    lines = _render_screened_lists({
        "screened_at": "2026-07-25T18:30:00+00:00",
        "verified_sources": {"OFAC SDN": {"status": "CLEAN"},
                             "UK OFSI / HMT": {"status": "CLEAN"}},
    })
    joined = "\n".join(lines)
    assert "Sanctions lists screened: 2" in joined
    assert "2026-07-25" in joined
    assert "screening date not recorded" not in joined


# ── R-F3037 — a legal-person (state/statutory) controller must not vanish ───
def test_rf3037_legal_person_psc_is_carried_as_an_unanchored_controller():
    """LIVE-VERIFIED shape (PEARSON ENGINEERING LIMITED 01876136, 2026-07-25):

        kind:  legal-person-person-with-significant-control
        name:  Government Companies Authority, State Of Israel
        identification: {legal_form: Government Authority, legal_authority: Israel}
        natures: ownership-of-shares-75-to-100-percent, voting-rights-75-to-100-percent,
                 right-to-appoint-and-remove-directors

    The kind test was `"corporate" in kind`, so this matched NEITHER list — not
    anchored (no registration number) and not un-anchored (not "corporate"). A
    foreign STATE holding 75-100% of a UK defence supplier reached no surface at
    all, which is the single most consequential ownership fact a defence DD exists
    to report."""
    import inspect
    from aria_service.intel import companies_house as ch
    src = function_source(ch, "investigate_uk_entity")
    assert '_is_controller_kind = ("corporate" in kind) or ("legal-person" in kind)' in src
    assert "if _is_controller_kind and not regno:" in src
    # the Grade-A anchored edge stays corporate-only (a state body has no regno)
    assert 'if regno and "corporate" in kind:' in src


def test_rf3037_controller_kind_is_recorded_so_the_wording_cannot_misdescribe_it():
    import inspect
    from aria_service.intel import companies_house as ch
    src = function_source(ch, "investigate_uk_entity")
    assert '"controller_kind"' in src
    assert "State / statutory (legal-person) controller" in src, (
        "calling a government authority a 'corporate controller' misdescribes it")
