"""R-F3471 — the vetting assurance rule was PROSE. Now it is enforced.

`docs/vetting/bs7858_assurance_review_2026_07_30_rf3466.md` ends with an assurance rule,
and it is the most important sentence in the vetting module::

    No page, API, report or sales claim may convert READY_FOR_CONTROLLER_REVIEW into
    "certified", "passed" or "BS 7858 compliant". Readiness means the encoded case
    controls have no unresolved blocker/action under the pinned historical manifest;
    organizational controls and the human decision remain separate.

R-F3466 wrote that rule and shipped the surfaces clean — I verified zero matches on the
live page. But NOTHING ENFORCED IT. A rule that exists only in a document is the
producer-with-no-carrier shape this codebase keeps closing: the next edit, or a marketing
pass over the copy, can violate it silently and every test stays green.

WHY THIS PARTICULAR CLAIM IS WORTH A GUARD. BS 7858 is a code of practice, not a
software-issued certificate. Its own review lists eight organizational controls the
product does not implement — screener competence, outsourcing governance, ancillary-staff
access, acquisition transfers, and a named controller's progress review among them.
Printing "BS 7858 compliant" would assert conformity the software cannot establish, about
a named individual's employment, to a customer who is accountable for the decision. It is
the vetting module's exact analogue of a false clean on a sanctions screen.

DELIBERATELY NARROW. "certified" alone is a legitimate vetting word — a *certified copy*
of a passport is standard practice under clause 7.4, and a guard that flagged it would
cry wolf and be switched off within a week. Only CLAIM constructions are prohibited.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "vetting" / "bs7858_assurance_review_2026_07_30_rf3466.md"

#: Customer-facing vetting surfaces: the page, the API, and the engine that renders
#: report text. A claim can be introduced on any of them.
SURFACES = [
    REPO / "public" / "vetting.html",
    REPO / "aria_service" / "routes" / "vetting.py",
] + sorted((REPO / "aria_service" / "vetting").rglob("*.py"))

#: Prohibited CLAIM constructions. Each asserts conformity or a verdict the software
#: cannot issue. Bare "certified"/"passed" are NOT here on purpose — see the docstring.
_PROHIBITED = [
    re.compile(r"BS\s?7858[\s\-–]*(compliant|certified|conformant)", re.I),
    re.compile(r"(fully|software|system)[\s\-]*(certified|compliant)", re.I),
    re.compile(r"(certified|guaranteed)\s+(compliance|conformity|compliant)", re.I),
    re.compile(r"compliance\s+(certificate|certified)", re.I),
    re.compile(r"screening\s+(certificate|certified)", re.I),
]

#: Legitimate uses that must never be flagged. A certified copy is required PRACTICE.
_ALLOWED = re.compile(r"certified\s+(true\s+)?cop(y|ies)", re.I)


def _scan(text: str) -> list[str]:
    hits: list[str] = []
    for pat in _PROHIBITED:
        for m in pat.finditer(text):
            window = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
            if _ALLOWED.search(window):
                continue
            hits.append(f"{m.group(0)!r} in ...{window.strip()}...")
    return hits


def test_no_vetting_surface_claims_conformity():
    """THE GUARD. The rule is now executable on every customer-facing surface."""
    offenders: list[str] = []
    for path in SURFACES:
        if not path.exists():
            continue
        for hit in _scan(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(f"{path.relative_to(REPO).as_posix()}: {hit}")
    assert not offenders, (
        "a vetting surface asserts conformity the software cannot establish:\n  "
        + "\n  ".join(offenders)
        + "\n\nBS 7858 is a code of practice, not a software-issued certificate, and the "
          "assurance review lists organizational controls this product does not "
          "implement. Say what is true instead: the module SUPPORTS screening to "
          "BS 7858:2019 and produces a file for a named controller's decision."
    )


def test_the_scanner_can_actually_see_a_claim():
    """VERIFY THE INSTRUMENT. A guard that cannot see a violation certifies everything —
    which is precisely the failure this guard exists to prevent, one level up."""
    for probe in ("This case is BS 7858 compliant.",
                  "Fully certified screening.",
                  "ARIA issues a compliance certificate.",
                  "BS7858-certified applicant."):
        assert _scan(probe), f"the scanner cannot see {probe!r}"


def test_a_certified_copy_is_not_a_conformity_claim():
    """The other half. Clause 7.4 requires the ORIGINAL to be inspected and a copy
    retained; 'certified copy' is the vocabulary of the standard itself. Flagging it
    would make the guard noise, and a guard that cries wolf gets switched off."""
    for benign in ("A certified copy of the passport is retained on file.",
                   "Certified true copies must be sighted against the original.",
                   "The screener certified that the original was seen."):
        assert not _scan(benign), f"benign vetting language was flagged: {benign!r}"


def test_readiness_is_never_rendered_as_a_verdict():
    """READY_FOR_CONTROLLER_REVIEW is a statement about ENCODED CONTROLS, not a person.
    Any surface mapping it to a pass/certified label is the specific conversion the
    assurance rule forbids."""
    bad = re.compile(
        r"READY_FOR_CONTROLLER_REVIEW[^\n]{0,80}?[\"']\s*(PASSED|CERTIFIED|COMPLIANT|CLEAR)\s*[\"']",
        re.I)
    offenders = []
    for path in SURFACES:
        if not path.exists():
            continue
        if bad.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, (
        f"readiness is rendered as a verdict in: {offenders}. Readiness means the "
        f"encoded controls have no unresolved blocker; the human decision is separate.")


def test_the_assurance_rule_is_still_documented():
    """The guard enforces the rule; the doc explains WHY it exists. Deleting the rule
    would leave a guard nobody can justify, which is how guards get removed."""
    assert DOC.exists(), f"the assurance review is missing: {DOC}"
    text = DOC.read_text(encoding="utf-8", errors="replace")
    assert "Assurance rule" in text
    assert "READY_FOR_CONTROLLER_REVIEW" in text
    assert "BS 7858 compliant" in text, (
        "the assurance rule no longer names the prohibited claim")
