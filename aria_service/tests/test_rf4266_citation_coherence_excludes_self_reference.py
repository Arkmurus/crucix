"""R-F4266 / C-227 - the contradiction gate flagged ARIA's OWN memory record.

THE LIVE SYMPTOM, from ``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``.
The report carried an AMBER key finding::

    1 cited source(s) carry adverse markers but are absent from the
    adverse-media review set
    ... research:investigation:Vigilo Solutions Limited ...

and a matching data gap telling the reader::

    adverse-media conclusion CONTRADICTED by 1 of this report's own cited
    source(s) ... Open them before relying on the clean reading.

The source it named is ``memory://f02d73289407`` - a record ARIA wrote about her
own earlier investigation of this same subject. There is nothing to open, and
nothing contradicted anything: the same report says, four lines above, that the
sweep excluded "5 of ARIA's own memory records".

THE MECHANISM. `_adverse_media_materiality` filters in six stages:

    (a) de-duplicate            (d) drop official INDEX pages
    (b) drop SELF-REFERENCES    (e) subject attribution
    (c) drop class-contradicted (f) adverse content

`_adverse_citation_contradictions` re-ran only (e) and (f). Its docstring claimed
it "re-uses the SAME two predicates the sweep uses ... so it cannot invent an
allegation the sweep would have rejected" - which is true of the two it runs and
false of the four it skips. A self-citation is precisely an item the sweep rejects,
by a predicate (`_adverse_is_self_reference`) that already exists in the module.

WHY A FALSE POSITIVE MATTERS HERE. R-F3455 exists because the Babcock report
concluded "nothing found" while citing a live FRC investigation into its audited
accounts. That gate has to be believed on the day it is right. A gate that also
fires on ARIA quoting herself trains the reader to skim past it, and the two are
indistinguishable on the page - both render as "the report's own evidence
disagrees with its conclusion". The failure is not the noise; it is the loss of
the signal.

Self-citation cannot corroborate - "it is the same observation counted twice"
(R-F3022) - and it equally cannot CONTRADICT, for the same reason.
"""
from __future__ import annotations

from types import SimpleNamespace

from aria_service.intel import dd_orchestrator as d


def _report(press: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(
        identity=SimpleNamespace(entity_name="Vigilo Solutions Limited", aliases=None),
        digital=SimpleNamespace(press_coverage=press),
    )


# The exact citation that produced the live AMBER finding.
_SELF = {"source": "research:investigation:Vigilo Solutions Limited",
         "url": "memory://f02d73289407", "snippet": ""}


def test_the_sweeps_own_predicate_calls_this_item_a_self_reference():
    """Anchors the claim: this is not a new judgement, it is the sweep's own.

    If this ever goes red the two paths have diverged again and the fix below is
    resting on a predicate that no longer means what it meant.
    """
    assert d._adverse_is_self_reference(_SELF) is True


def test_a_memory_citation_is_not_a_contradiction():
    """THE CAPABILITY TEST - reproduces the delivered report's false AMBER."""
    out = d._adverse_citation_contradictions(_report([
        _SELF,
        {"source": "Vigilo Solutions | Home", "url": "https://vigilosolutions.co.uk/",
         "snippet": ""},
    ]))
    assert out == [], (
        "ARIA's own memory record was reported to the customer as a cited source "
        f"contradicting the report's adverse-media conclusion: {out!r}. The sweep "
        "drops self-references at stage (b); this gate must not resurrect them."
    )


def test_an_official_index_page_is_not_a_contradiction():
    """Stage (d), the R-F3089 class, pointing at this gate.

    A court INDEX is a table of contents. It reaches `_adverse_has_adverse_content`
    as True on DOMAIN MATCH ALONE - the lexicon is never consulted - so without this
    filter a paginated BAILII listing is reported as adverse content naming the
    subject, which is how it reached the Mitie report in the first place.
    """
    out = d._adverse_citation_contradictions(_report([
        {"source": "BAILII - United Kingdom Cases page 286 Vigilo",
         "url": "https://www.bailii.org/indices/uk-cases-0286.html", "snippet": ""},
    ]))
    assert out == [], f"an index page was reported as a contradiction: {out!r}"


def test_the_gate_still_fires_on_a_genuine_external_contradiction():
    """A gate that cannot fire is not a gate (CLAUDE.md S16, R-F3858).

    This is the R-F3455 case verbatim. If the fix above silenced it, the defect it
    was built to catch - a live regulatory investigation cited by the report and
    absent from its own conclusion - would sail through again.
    """
    out = d._adverse_citation_contradictions(_report([
        {"source": "FRC expands probe of Vigilo Solutions audits | Compliance Week",
         "url": "https://www.complianceweek.com/vigilo-frc-probe", "snippet": ""},
    ]))
    assert len(out) == 1 and "FRC" in out[0]["title"], (
        "the genuine contradiction R-F3455 exists to catch was suppressed: "
        f"{out!r}"
    )
