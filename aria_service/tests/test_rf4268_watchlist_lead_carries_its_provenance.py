"""R-F4268 / C-229 - a watchlist lead named a third party without saying how thin it was.

THE LIVE SYMPTOM, from ``ARIA_DD_Vigilo_Solutions_Limited_dd_9fe0e61e4a0c.pdf``. Two
UK companies reached by the ownership walk were printed beside US FINRA-barred
entities, with nothing in the line to show what the match rested on::

    BRIAN REID LTD. (hop 1)
      -> BRIAN BROCK REID (score 0.91, topics: reg.action,
         lists: us_finra_barred,us_finra_actions, matched_via=primary_name)

    STEPHEN MABBOTT LTD. (hop 1)
      -> STEPHEN A. KOHN & ASSOCIATES LTD. (score 0.83, topics: reg.action,
         lists: us_finra_actions, matched_via=primary_name)

The second shares exactly ONE meaningful token with the query - the forename
"Stephen". The first matched a COMPANY query to a PERSON record. Neither fact was
on the page, and both are the first thing a reader would want.

WHY THIS IS ADDITIVE AND NOT A FILTER. Two options were available and only one is
safe. Demoting or dropping these leads would be the never-false-clean direction:
eponymous companies are a real evasion pattern, the items are already labelled
UNVERIFIED and "identity NOT confirmed", and they do not move the verdict. A
sanctions judgement about which forenames are "too common" also needs a name list
that is locale-biased and rots.

So this states the EVIDENCE and classifies nothing:
  * the shared token, verbatim, when there is only one; and
  * the record's own entity type, taken from the provider's `schema` field, when it
    disagrees with the shape of the query.

The `schema` field is the sharper half. `sanctions.py` carries it on EVERY match
(`"schema": raw.get("schema")`) and `_sanctions_classify` never read it - a signal
fetched from the provider and thrown away. Nothing else in the classifier can tell
a person from a company.

Severity is deliberately untouched: every existing demotion, cap and noise rule
still decides what a match MEANS. This only decides what the reader is told.
"""
from __future__ import annotations

from aria_service.intel._sanctions_classify import classify_matches


def _finra_person() -> list[dict]:
    """The BRIAN REID LTD. hit, as OpenSanctions returned it."""
    return [{"name": "BRIAN BROCK REID", "score": 0.91, "schema": "Person",
             "topics": ["reg.action"],
             "datasets": ["us_finra_barred", "us_finra_actions"],
             "match_field": "primary_name"}]


def _finra_company() -> list[dict]:
    """The STEPHEN MABBOTT LTD. hit - company to company, one shared token."""
    return [{"name": "STEPHEN A. KOHN & ASSOCIATES LTD.", "score": 0.83,
             "schema": "Company", "topics": ["reg.action"],
             "datasets": ["us_finra_actions"], "match_field": "primary_name"}]


def test_a_company_query_matching_a_person_record_says_so():
    """THE CAPABILITY TEST - the live BRIAN REID LTD. line."""
    out = classify_matches(_finra_person(), query_name="BRIAN REID LTD.")
    assert "person" in out["summary"].lower(), (
        "a COMPANY query matched a PERSON record and the report did not say so: "
        f"{out['summary']!r}"
    )


def test_the_type_conflict_is_machine_readable_too():
    """A renderer must not have to parse prose to know."""
    out = classify_matches(_finra_person(), query_name="BRIAN REID LTD.")
    assert out["per_match"][0].get("entity_type_conflict"), (
        f"no structured type-conflict flag: {out['per_match'][0]!r}"
    )


def test_a_single_shared_token_is_quoted_verbatim():
    """THE CAPABILITY TEST - the live STEPHEN MABBOTT LTD. line.

    "Stephen" is the whole of the evidence. Printing it is what lets a reader
    dismiss the lead in one second instead of opening OpenSanctions.
    """
    out = classify_matches(_finra_company(), query_name="STEPHEN MABBOTT LTD.")
    # NOT a bare "stephen" in the summary — the candidate's own name contains it, so
    # that assertion passes with no fix at all. It must be the explicit statement.
    assert "shares only 'stephen'" in out["summary"].lower(), (
        f"the only shared token was not stated: {out['summary']!r}"
    )
    assert out["per_match"][0].get("shared_tokens") == ["stephen"], (
        f"no structured shared-token evidence: {out['per_match'][0]!r}"
    )


def test_the_lead_is_still_reported_and_its_severity_is_unchanged():
    """Never-false-clean. This adds words; it must not remove a lead or lower it.

    Both live items classified AMBER. If this fix ever silences one, an eponymous
    shell used to hold a barred broker's business stops being surfaced.
    """
    for matches, query in ((_finra_person(), "BRIAN REID LTD."),
                           (_finra_company(), "STEPHEN MABBOTT LTD.")):
        out = classify_matches(matches, query_name=query)
        assert out["worst_severity"] == "amber", (
            f"{query} lead changed severity to {out['worst_severity']!r} — this "
            "change is presentational and must not touch classification"
        )
        assert out["total_matches"] == 1, f"{query} lead was dropped: {out!r}"


def test_a_genuine_multi_token_hit_is_not_annotated():
    """The note must be rare, or it is wallpaper.

    A real designation shares several distinctive tokens and needs no caveat; adding
    one to every line would train the reader to ignore it.
    """
    out = classify_matches(
        [{"name": "ROSOBORONEXPORT OAO", "score": 0.97, "schema": "Company",
          "string_similarity": 0.9, "topics": ["sanction"],
          "datasets": ["us_ofac_sdn"], "match_field": "primary_name"}],
        query_name="Rosoboronexport Corporation")
    assert "shares only" not in out["summary"].lower(), (
        f"a multi-token designation was annotated as thin: {out['summary']!r}"
    )


def test_an_absent_schema_asserts_nothing():
    """A provider that does not publish a type must not be read as agreeing.

    Unmeasured is not 'no conflict' — the same rule the tri-state gauges elsewhere
    in this repo follow.
    """
    m = _finra_person()
    del m[0]["schema"]
    out = classify_matches(m, query_name="BRIAN REID LTD.")
    assert not out["per_match"][0].get("entity_type_conflict"), (
        "a missing schema was reported as a type conflict"
    )
    assert "person" not in out["summary"].lower(), (
        f"a type was asserted with no evidence for it: {out['summary']!r}"
    )
