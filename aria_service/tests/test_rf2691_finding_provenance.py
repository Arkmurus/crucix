"""R-F2691 — per-Finding structured provenance (DD Grade-A Phase-0, gap #4).

`Evidence` has carried url/source_tier/retrieved_at from the start; `Finding` — the
thing an analyst actually reads and acts on — carried only a bare `source` string.
Callers that HAD a url stuffed it into free text (f"{name} [from {url}]"), which is
unparseable and pollutes the source identifier that feeds the R-5005 tier gate and
the C-3 independent-origin count.
"""
from __future__ import annotations

from aria_service.intel.dd_schema import Evidence, Finding


def test_finding_carries_structured_provenance():
    f = Finding(
        severity="amber",
        title="Litigation [US] Acme v. Someone",
        source="courtlistener",
        url="https://www.courtlistener.com/docket/123/",
        source_tier="OFFICIAL",
        retrieved_at="2026-07-17T10:00:00Z",
    )
    assert f.url == "https://www.courtlistener.com/docket/123/"
    assert f.source_tier == "OFFICIAL"
    assert f.retrieved_at == "2026-07-17T10:00:00Z"
    assert f.has_provenance() is True


def test_provenance_is_optional_and_absent_is_honest():
    """The ~127 existing construction sites must keep working, and a site with no
    provenance must report none rather than inventing a tier."""
    f = Finding(severity="info", title="x", source="some_module")

    assert f.url is None
    assert f.retrieved_at is None
    assert f.source_tier == "UNKNOWN"   # never a fabricated OFFICIAL
    assert f.has_provenance() is False


def test_has_provenance_requires_a_url_not_just_a_tier():
    """A tier without a url is a claim about a source we cannot show the analyst —
    that is the gap this field exists to close, so it must not read as provenance."""
    f = Finding(severity="info", title="x", source="s", source_tier="OFFICIAL")
    assert f.has_provenance() is False


def test_finding_tier_vocabulary_matches_evidence():
    """A second tier spelling would silently fork the meaning of OFFICIAL."""
    assert Finding(severity="info", title="x").source_tier == Evidence(source="s").source_tier


def test_provenance_does_not_disturb_the_r5005_confirmation_gate():
    """Provenance is orthogonal to the verification gate — adding a url must not
    smuggle a CONFIRMED past the >=2-sources / Tier-1a rule."""
    demoted = Finding(
        severity="red", title="x", source="randomblog",
        url="https://randomblog.example/post", source_tier="UNVERIFIED",
        confidence="CONFIRMED",
    )
    assert demoted.confidence == "ASSESSED"
    assert demoted.gate_demoted is True
    # ...and a genuinely multi-sourced CONFIRMED still passes, url or no url.
    kept = Finding(
        severity="red", title="x", sources=["ofac", "companies_house"],
        confidence="CONFIRMED",
    )
    assert kept.confidence == "CONFIRMED"
    assert kept.gate_demoted is False


def test_litigation_findings_expose_machine_readable_provenance():
    """CAPABILITY: the real path — dd_orchestrator's litigation findings.

    The url was previously recoverable ONLY by parsing the display string
    f"{name} [from {url}]". It must now also be readable as a field.

    The `[from …]` suffix is deliberately RETAINED and asserted here: it measures as
    load-bearing (origin_key/_is_tier_1a_source match on DOMAINS, so stripping it
    flips bailii from pub:bailii.org/Tier-1a to external_unclassified/not-Tier-1a).
    An earlier cut of R-F2691 removed it and broke R-F600 — this assertion pins the
    coupling so nobody "cleans it up" again without fixing those two functions first.
    """
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings

    findings = _emit_court_record_findings({
        "severity": "ELEVATED",
        "us_count": 1,
        "hits": [
            {
                "title": "Acme Corp v. Regulator",
                "court": "S.D.N.Y.",
                "date": "2025-03-04",
                "jurisdiction": "US",
                "snippet": "alleged breach of sanctions controls",
                "citation_url": "https://www.courtlistener.com/docket/999/",
                "source": "courtlistener",
            }
        ],
    })

    # The first finding is the headline summary; the per-case findings follow it.
    # "Litigation [" is the PER-CASE title; the headline summary is "Litigation
    # history: N US · N UK case(s)" and is a different (still un-wired) site.
    cases = [f for f in findings if f.title.startswith("Litigation [")]
    assert cases, "no per-case litigation finding produced"
    f = cases[0]
    # NEW: machine-readable, no string parsing required.
    assert f.url == "https://www.courtlistener.com/docket/999/"
    assert f.source_tier == "OFFICIAL"
    assert f.has_provenance() is True
    # PRESERVED (R-F600 + the tier/origin coupling above): url still in source + detail.
    assert "https://www.courtlistener.com/docket/999/" in f.source
    assert "https://www.courtlistener.com/docket/999/" in f.detail

    # The coupling itself, pinned: the source string must still resolve to a real
    # publisher rather than collapsing to external_unclassified.
    from aria_service.intel.dd_independent_verifier import origin_key

    assert origin_key(f.source) == "pub:courtlistener.com"


def test_litigation_finding_without_a_citation_claims_no_tier():
    """No citation url → we cannot claim OFFICIAL authority we cannot show."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings

    findings = _emit_court_record_findings({
        "severity": "ELEVATED",
        "us_count": 1,
        "hits": [
            {"title": "Some case", "court": "S.D.N.Y.", "date": "2025-01-01",
             "jurisdiction": "US", "source": "courtlistener"}
        ],
    })
    # "Litigation [" is the PER-CASE title; the headline summary is "Litigation
    # history: N US · N UK case(s)" and is a different (still un-wired) site.
    cases = [f for f in findings if f.title.startswith("Litigation [")]
    assert cases
    f = cases[0]
    assert f.url is None
    assert f.source_tier == "UNKNOWN"
    assert f.has_provenance() is False
