"""R-F2696 — the R-5005 Tier-1a gate must read structured provenance, not a display string.

`Finding.__post_init__` demotes a CONFIRMED finding unless it has >=2 sources OR a
single Tier-1a source. The Tier-1a check (`_is_tier_1a_source`) substring-matches the
`source` STRING against an allowlist that mixes bare labels ("courtlistener") with
DOMAINS ("bailii.org", "fca.org.uk", "bis.doc.gov").

So a finding whose source is the clean label "bailii" FAILS the gate — "bailii.org" is
not a substring of "bailii" — and gets silently demoted CONFIRMED -> ASSESSED, even
though its `url` (https://www.bailii.org/…) is exactly the authority the allowlist is
describing. The only thing that made it pass was dd_orchestrator concatenating the url
INTO the source string (f"{name} [from {url}]"), i.e. the gate was depending on display
formatting. R-F2691 discovered that coupling the hard way (stripping the suffix demoted
findings); R-F2693/R-F2691 added the structured `Finding.url`. This teaches the gate to
use it.

Consequence: authority now travels in a field, not in prose. The `[from …]` suffix
stays — it independently serves R-F600/Clause-15 inline citation — but the gate no
longer DEPENDS on it.
"""
from __future__ import annotations

import pytest

from aria_service.intel.dd_schema import Finding, _is_tier_1a_source


# ── the allowlist inconsistency this fix works around ──────────────────────

def test_allowlist_mixes_bare_labels_and_domains():
    """Documents WHY the gate needs the url: the allowlist is not one vocabulary."""
    assert _is_tier_1a_source("courtlistener") is True      # bare label IS listed
    assert _is_tier_1a_source("bailii") is False            # only "bailii.org" is listed
    assert _is_tier_1a_source("https://www.bailii.org/x/1") is True  # the url matches


# ── the fix ────────────────────────────────────────────────────────────────

def test_clean_source_plus_authoritative_url_passes_the_gate():
    """CAPABILITY: the real demotion. source="bailii" + an authoritative url is a
    single Tier-1a source and must stay CONFIRMED."""
    f = Finding(
        severity="amber", title="Litigation [UK] Re XYZ Ltd",
        source="bailii", url="https://www.bailii.org/ew/cases/EWHC/2024/123.html",
        confidence="CONFIRMED",
    )
    assert f.confidence == "CONFIRMED"
    assert f.gate_demoted is False


def test_gate_no_longer_depends_on_the_url_being_concatenated_into_source():
    """The suffix and the structured field must reach the SAME verdict."""
    suffixed = Finding(
        severity="amber", title="x",
        source="bailii [from https://www.bailii.org/ew/cases/EWHC/2024/123.html]",
        confidence="CONFIRMED",
    )
    structured = Finding(
        severity="amber", title="x", source="bailii",
        url="https://www.bailii.org/ew/cases/EWHC/2024/123.html",
        confidence="CONFIRMED",
    )
    assert suffixed.confidence == structured.confidence == "CONFIRMED"
    assert suffixed.gate_demoted is structured.gate_demoted is False


def test_non_authoritative_url_does_not_smuggle_a_confirmation():
    """The url is not a bypass: a random blog url is still not Tier-1a."""
    f = Finding(
        severity="red", title="x", source="randomblog",
        url="https://randomblog.example/post", confidence="CONFIRMED",
    )
    assert f.confidence == "ASSESSED"
    assert f.gate_demoted is True
    assert "not in Tier-1a allowlist" in f.gate_reason


def test_no_url_and_non_tier1a_source_still_demotes():
    """The gate's default must not weaken."""
    f = Finding(severity="red", title="x", source="someblog", confidence="CONFIRMED")
    assert f.confidence == "ASSESSED"
    assert f.gate_demoted is True


def test_url_cannot_rescue_a_zero_source_finding():
    """A url is provenance for a SOURCE. With no source there is nothing to tier —
    the finding must stay demoted rather than being certified by a bare link."""
    f = Finding(
        severity="red", title="x",
        url="https://www.bailii.org/ew/cases/EWHC/2024/123.html",
        confidence="CONFIRMED",
    )
    assert f.confidence == "ASSESSED"
    assert f.gate_demoted is True
    assert "no source provided" in f.gate_reason


def test_url_is_trusted_as_the_provenance_OF_that_source():
    """Pins the trust model, which is UNCHANGED from the string it replaces.

    A mismatched pair — non-authoritative source, authoritative url — passes the gate.
    That is not new laxity: the old f"{name} [from {url}]" string had exactly the same
    property (the substring match saw the url regardless of the name). The gate trusts
    that a finding's `url` is the provenance of its `source`; a construction site that
    pairs an unrelated authoritative url with a junk source is mislabelling its own
    finding, and that is the bug to fix at the site, not here.

    Recorded explicitly so the equivalence is a decision, not an accident.
    """
    structured = Finding(
        severity="red", title="x", source="randomblog",
        url="https://un.org/securitycouncil/some-record", confidence="CONFIRMED",
    )
    legacy_string = Finding(
        severity="red", title="x",
        source="randomblog [from https://un.org/securitycouncil/some-record]",
        confidence="CONFIRMED",
    )
    assert structured.confidence == legacy_string.confidence == "CONFIRMED"


def test_multi_source_confirmation_is_unaffected():
    """The >=2-sources route must not change."""
    f = Finding(
        severity="red", title="x", sources=["ofac", "companies_house"],
        confidence="CONFIRMED",
    )
    assert f.confidence == "CONFIRMED"
    assert f.gate_demoted is False


@pytest.mark.parametrize("conf", ["PROBABLE", "ASSESSED", "UNCERTAIN", "SPECULATIVE"])
def test_gate_only_applies_to_confirmed(conf):
    """A url must not upgrade anything — the gate only ever demotes."""
    f = Finding(
        severity="info", title="x", source="bailii",
        url="https://www.bailii.org/ew/cases/EWHC/2024/123.html", confidence=conf,
    )
    assert f.confidence == conf
    assert f.gate_demoted is False


def test_live_litigation_finding_stays_confirmed_via_its_url(monkeypatch):
    """CAPABILITY through the real construction site: even if the display suffix were
    removed tomorrow, the gate must hold via the structured url."""
    from aria_service.intel.dd_orchestrator import _emit_court_record_findings

    findings = _emit_court_record_findings({
        "severity": "ELEVATED", "uk_count": 1,
        "hits": [{
            "title": "Re XYZ Ltd", "court": "High Court of Justice", "date": "2024-06-12",
            "jurisdiction": "UK", "citation_url": "https://www.bailii.org/abc/123",
            "source": "bailii",
        }],
    })
    case = next(f for f in findings if f.title.startswith("Litigation ["))
    assert case.url == "https://www.bailii.org/abc/123"
    # Re-tier it from the structured field alone, with the display string emptied —
    # proving the gate's verdict no longer rides on the prose.
    stripped = Finding(
        severity=case.severity, title=case.title, source="bailii",
        url=case.url, confidence="CONFIRMED",
    )
    assert stripped.confidence == "CONFIRMED"
    assert stripped.gate_demoted is False
