"""R-F3553 — T1 register evidence sat inert in citations for four consecutive reports.

THE GAP, from the operator's review. The citation list carried positives directly
relevant to the decision — an SIA Approved Contractor Scheme entry for the CORRECT legal
entity, an Armed Forces Covenant listing, a gov.uk supplier record — and not one was
promoted to a finding. The evidence was fetched, stored, and never said.

A DD that reports only what is wrong is not neutral, it is skewed: the same report that
names an unresolved gap should name a verified credential, or the reader gets a
systematically negative picture built from a complete evidence set.

THE DIRECTION IS INVERTED FROM ADVERSE MEDIA, and that is the whole design.
`_adverse_names_subject` is deliberately PERMISSIVE (any shared distinctive token) and
FAILS OPEN, because for adverse items a missed hit is the expensive error. For a
CREDENTIAL the expensive error is the opposite: asserting a company holds SIA approval
when the page merely mentions it fabricates a credential — the R-F3089 name-coincidence
class pointing the other way, and arguably worse, since a false clean is at least an
absence while a false credential is an invention.

So positives require ALL the subject's distinctive tokens and FAIL CLOSED when the
subject cannot be tokenised.

CURATED, NEVER PATTERN-MATCHED. "Any gov.uk URL is a credential" is precisely the
domain-match-alone error R-F3093 removed from the adverse filter.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_orchestrator import (
    _NOT_A_CREDENTIAL,
    _POSITIVE_REGISTERS,
    _adverse_subject_tokens,
    _positive_names_subject,
    positive_register_findings,
)

SUBJ = "Bidvest Noonan (UK) Limited"
TOK = _adverse_subject_tokens(SUBJ)


def _src(url, title, snippet="x"):
    return {"url": url, "title": title, "snippet": snippet}


# ── the capability ──────────────────────────────────────────────────────────

def test_capability_the_SIA_entry_is_promoted():
    out = positive_register_findings(
        [_src("https://services.sia.homeoffice.gov.uk/acs/1",
              "Bidvest Noonan (UK) Limited t/a Bidvest Noonan", "Approved Contractor")],
        TOK, as_of="2026-07-31")
    assert len(out) == 1
    assert "SIA Approved Contractor Scheme" in out[0]["title"]
    assert out[0]["source_tier"] == "OFFICIAL"


def test_the_finding_states_what_the_register_does_NOT_attest():
    """An ACS listing is not a vetting outcome, and a Covenant signature is a pledge.
    Blurring the two manufactures assurance."""
    out = positive_register_findings(
        [_src("https://armedforcescovenant.gov.uk/s/9", SUBJ, "signed")], TOK)
    assert "voluntary PLEDGE" in out[0]["detail"]
    assert "not a vetting" in out[0]["detail"]


def test_the_finding_is_DATED():
    """An undated credential is the freshness defect in reverse: a lapsed approval
    reported without a date reads as current."""
    out = positive_register_findings(
        [_src("https://register.fca.org.uk/x", SUBJ)], TOK, as_of="2026-07-31")
    assert "as captured on 2026-07-31" in out[0]["detail"]
    assert "can lapse" in out[0]["detail"]


def test_without_a_date_it_says_so_rather_than_implying_currency():
    out = positive_register_findings([_src("https://register.fca.org.uk/x", SUBJ)], TOK)
    assert "during this run" in out[0]["detail"]


# ── attribution: the expensive error is a FABRICATED credential ─────────────

def test_a_DIFFERENT_company_on_the_same_register_is_REJECTED():
    """The exact fabrication this guards: an SIA page for 'Noonan Services Group' must
    not credit 'Bidvest Noonan (UK) Limited'."""
    out = positive_register_findings(
        [_src("https://services.sia.homeoffice.gov.uk/acs/999",
              "Noonan Services Group", "a different company")], TOK)
    assert out == [], "a credential was attributed to the wrong entity"


def test_attribution_requires_ALL_tokens_not_any():
    """The inverse of the adverse rule. 'Bidvest Group' shares one token and must not
    inherit the subject's credential."""
    assert _positive_names_subject("bidvest group plc", TOK) is False
    assert _positive_names_subject("bidvest noonan (uk) limited", TOK) is True


def test_it_FAILS_CLOSED_when_the_subject_cannot_be_tokenised():
    """Opposite of `_adverse_names_subject`, which fails OPEN by design."""
    assert _positive_names_subject("anything at all", set()) is False
    assert positive_register_findings(
        [_src("https://register.fca.org.uk/x", "anything")], set()) == []


# ── curation, not pattern matching ─────────────────────────────────────────

def test_an_arbitrary_govuk_url_is_not_a_credential():
    out = positive_register_findings(
        [_src("https://www.gov.uk/some-guidance-page", SUBJ)], TOK)
    assert out == [], "any-gov.uk-is-a-credential is the R-F3093 domain-match error"


def test_a_random_site_praising_the_subject_is_not_a_credential():
    out = positive_register_findings([_src("https://randomblog.com/x", SUBJ)], TOK)
    assert out == []


@pytest.mark.parametrize("host", sorted(_NOT_A_CREDENTIAL))
def test_the_base_identity_registries_are_excluded(host):
    """Presence in Companies House is not an achievement; promoting it would drown the
    real positives, and a signal that fires on everything gets ignored."""
    out = positive_register_findings([_src(f"https://{host}/company/1", SUBJ)], TOK)
    assert out == []


def test_every_curated_register_declares_attests_and_not():
    for host, spec in _POSITIVE_REGISTERS.items():
        assert spec.get("register") and spec.get("attests") and spec.get("not"), host
        assert host not in _NOT_A_CREDENTIAL, f"{host} is both curated and excluded"


# ── hygiene ────────────────────────────────────────────────────────────────

def test_duplicates_are_collapsed():
    s = _src("https://register.fca.org.uk/x", SUBJ)
    assert len(positive_register_findings([s, s, s], TOK)) == 1


def test_malformed_input_never_raises():
    assert positive_register_findings(None, TOK) == []
    assert positive_register_findings(["not a dict", {}, {"url": None}], TOK) == []


def test_the_promoter_HAS_a_caller():
    """A capability nothing invokes is indistinguishable from one that does not exist —
    the lesson from R-F3510 and R-F3504 this session."""
    import pathlib
    from aria_service.intel import dd_orchestrator as o
    src = pathlib.Path(o.__file__).read_text(encoding="utf-8", errors="replace")
    calls = [l for l in src.splitlines()
             if "positive_register_findings(" in l and not l.lstrip().startswith(("def ", "#"))]
    assert calls, "positive_register_findings is dormant — nothing calls it"


# ── R-F3555 — two defects R-F3553 SHIPPED, caught by replaying REAL sources ──
#
# An hour after shipping, the 92 captured sources from the live Chemring report
# (dd_01531a44eb2f) were replayed through the promoter. It produced TWO fabricated
# credentials — precisely the class R-F3553 was written to prevent. Constructed inputs
# had passed; real ones did not.

def test_rf3555_the_FCA_document_archive_is_not_the_FCA_register():
    """`data.fca.org.uk` is the National Storage Mechanism — an archive of regulatory
    announcements (/artefacts/NSM/...), NOT the Financial Services Register. Presence
    means the company FILED something, not that it is authorised. The domain merely
    looks regulatory, which is how it slipped in."""
    out = positive_register_findings(
        [_src("https://data.fca.org.uk/artefacts/NSM/RNS/5498881.html",
              "Bidvest Noonan (UK) Limited annual report")], TOK)
    assert out == [], "a document archive is being promoted as an authorisation"


def test_rf3555_the_actual_FCA_register_still_counts():
    """Removing the archive must not blind the real register."""
    out = positive_register_findings(
        [_src("https://register.fca.org.uk/s/firm?id=1",
              "Bidvest Noonan (UK) Limited")], TOK)
    assert len(out) == 1


def test_rf3555_a_filing_ABOUT_another_company_is_not_this_company_s_credential():
    """THE CROSS-ATTRIBUTION. A document titled 'Babcock International Group PLC Notice
    of Annual General Meeting' was credited to Chemring because the body mentioned
    Chemring once."""
    chemring = _adverse_subject_tokens("Chemring Group PLC")
    out = positive_register_findings(
        [_src("https://register.fca.org.uk/x",
              "Babcock International Group PLC Notice of Annual General Meeting",
              "... comparison with Chemring Group PLC ...")],
        chemring)
    assert out == [], "another company's filing was promoted as this company's credential"


def test_rf3555_a_single_token_subject_does_not_collapse_the_all_rule():
    """WHY THE TITLE ANCHOR EXISTS. 'Chemring Group PLC' reduces to ONE distinctive
    token, so 'all tokens must appear' silently becomes the permissive ANY rule the
    function was written to avoid. Anchoring on the TITLE restores the distinction: a
    register LISTING names its subject in the title; a document that mentions it in the
    body does not."""
    chemring = _adverse_subject_tokens("Chemring Group PLC")
    assert len(chemring) == 1, "test premise: the subject has a single distinctive token"
    assert _positive_names_subject("Chemring Group PLC", chemring) is True
    assert _positive_names_subject(
        "Some Other PLC filing", chemring, snippet="mentions chemring") is False


def test_rf3555_an_untitled_source_cannot_carry_an_attribution():
    """No title means nothing to anchor on. Fail closed."""
    assert _positive_names_subject("", TOK) is False
    assert positive_register_findings(
        [_src("https://register.fca.org.uk/x", "", "Bidvest Noonan (UK) Limited")],
        TOK) == []


def test_rf3555_the_live_chemring_replay_promotes_NOTHING():
    """The regression, end to end: those 92 real sources contain no genuine credential
    for Chemring, so the honest output is zero. Two was a fabrication."""
    import json
    import pathlib
    p = pathlib.Path(__file__).resolve().parents[2] / "_chemring.json"
    if not p.exists():
        pytest.skip("live fixture not present in this checkout")
    r = json.loads(p.read_text(encoding="utf-8"))
    r = r.get("report", r)
    tokens = _adverse_subject_tokens(r["identity"]["entity_name"])
    src = [{"url": f.get("source_url"), "title": f.get("title"),
            "snippet": f.get("snippet")}
           for f in (r.get("adverse_media") or {}).get("findings") or []]
    assert positive_register_findings(src, tokens) == []
