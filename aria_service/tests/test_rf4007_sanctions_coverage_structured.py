"""R-F4007 (C-86) — sanctions coverage must reach the surface as DATA.

THE DEFECT. R-F3945 stopped DD stamping never-searched lists as CLEAN and made
the report say so, but only in PROSE: `_render_screened_lists` emits report LINES
("DID NOT ANSWER (8): ... — NOT screened, treat as unchecked") and
`structured_view` — the render contract the front-end actually consumes — carried
no per-source coverage at all. Its only sanctions field is a match COUNT metric.

So the most decision-critical fact in a DD report reached the customer as a grey
bullet inside a markdown paragraph, while lesser facts got coloured verdict pills,
because the UI had no structured field to render. A reader skimming the report saw
a verdict badge and prose.

ONE COMPUTATION, NOT TWO. The obvious fix — classify the statuses again inside
`structured_view` — would put two independent classifications of the same dict in
the tree, which is how the text and the pill end up disagreeing about the same
report. `sanctions_coverage()` is the single classifier; `_render_screened_lists`
is rewritten to consume it, so the prose and the structured data cannot drift.

ABSENCE IS NOT COVERAGE. A report written before R-F3945 has no
`verified_sources`, and rendering that as "0 of 0 lists answered" would be an
invented measurement. The classifier returns None, and the UI shows nothing.
"""
from aria_service.intel import dd_schema


def _screen(**sources):
    return {"screened_at": "2026-08-14T10:00:00Z",
            "verified_sources": {k: v for k, v in sources.items()}}


def test_coverage_splits_hit_clean_and_unavailable():
    cov = dd_schema.sanctions_coverage(_screen(
        **{
            "OFAC SDN": {"status": "CLEAN"},
            "EU Consolidated": {"status": "CLEAN"},
            "BIS Entity List": {"status": "UNAVAILABLE", "via": "source_unavailable"},
            "UK OFSI": {"status": "HIT", "match_count": 2},
        }
    ))
    assert cov is not None
    assert cov["total"] == 4
    assert cov["answered"] == 3, "answered counts HIT + CLEAN, never UNAVAILABLE"
    assert sorted(cov["hit"]) == ["UK OFSI"]
    assert sorted(cov["clean"]) == ["EU Consolidated", "OFAC SDN"]
    assert sorted(cov["unavailable"]) == ["BIS Entity List"]
    assert cov["complete"] is False, "one unanswered list means coverage is not complete"


def test_complete_only_when_every_list_answered():
    cov = dd_schema.sanctions_coverage(_screen(
        **{"OFAC SDN": {"status": "CLEAN"}, "UK OFSI": {"status": "HIT"}}
    ))
    assert cov["complete"] is True
    assert cov["answered"] == cov["total"] == 2


def test_absent_detail_returns_none_not_a_zero_measurement():
    # The load-bearing one. A legacy report predates the field; reporting
    # "0 of 0 answered" would invent a measurement nobody took, which is the
    # same class as the C-39 false-CLEAN this whole line of work exists to end.
    assert dd_schema.sanctions_coverage(None) is None
    assert dd_schema.sanctions_coverage({}) is None
    assert dd_schema.sanctions_coverage({"verified_sources": {}}) is None
    assert dd_schema.sanctions_coverage("not a dict") is None


def test_unknown_status_counts_as_unanswered_never_as_clean():
    # Fail CLOSED on a status this code does not recognise: an unrecognised
    # verdict must never be absorbed into the reassuring bucket.
    cov = dd_schema.sanctions_coverage(_screen(
        **{"OFAC SDN": {"status": "PENDING"}, "EU Consolidated": {"status": "CLEAN"}}
    ))
    assert cov["clean"] == ["EU Consolidated"]
    assert "OFAC SDN" in cov["unavailable"]
    assert cov["complete"] is False


def test_structured_view_exposes_the_coverage():
    # The whole point: the render contract the front-end consumes must carry it.
    report = {
        "identity": {
            "entity_type": "company",
            "sanctions_screen": _screen(
                **{"OFAC SDN": {"status": "CLEAN"},
                   "BIS Entity List": {"status": "UNAVAILABLE"}}
            ),
        }
    }
    sv = dd_schema.structured_view(report)
    cov = sv.get("sanctions_coverage")
    assert cov is not None, "structured_view must expose sanctions_coverage"
    assert cov["total"] == 2
    assert cov["answered"] == 1
    assert cov["complete"] is False


def test_structured_view_omits_coverage_when_the_report_has_none():
    sv = dd_schema.structured_view({"identity": {"entity_type": "company"}})
    assert sv.get("sanctions_coverage") is None


def test_prose_and_structured_data_come_from_ONE_classifier():
    # Guards the property, not the wording: if someone re-implements the split
    # inside the renderer, the two can disagree about the same report — which is
    # exactly how a pill and a paragraph end up contradicting each other.
    import inspect
    src = inspect.getsource(dd_schema._render_screened_lists)
    assert "sanctions_coverage(" in src, (
        "_render_screened_lists must consume the shared classifier rather than "
        "classifying the statuses a second time"
    )


def test_render_lines_still_name_unavailable_first_and_explicitly():
    # The R-F3019 contract must survive the refactor: an unanswered list is named
    # FIRST and separately, so a reader cannot infer coverage from a summary that
    # quietly dropped it.
    lines = dd_schema._render_screened_lists(_screen(
        **{"OFAC SDN": {"status": "CLEAN"},
           "BIS Entity List": {"status": "UNAVAILABLE"}}
    ))
    assert lines, "a screen with per-source detail must still render lines"
    joined = "\n".join(lines)
    assert "DID NOT ANSWER" in joined
    assert "NOT screened" in joined
    did_not = next(i for i, s in enumerate(lines) if "DID NOT ANSWER" in s)
    no_match = next(i for i, s in enumerate(lines) if "No match" in s)
    assert did_not < no_match, "the unanswered lists must be named before the clean ones"


def test_render_lines_return_empty_without_detail():
    assert dd_schema._render_screened_lists({}) == []
    assert dd_schema._render_screened_lists(None) == []
