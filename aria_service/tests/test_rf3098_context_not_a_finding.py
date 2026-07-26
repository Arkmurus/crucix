"""R-F3098 — placement is a claim: context printed as a compliance finding.

LIVE DEFECT (Mitie, operator report 2026-07-26). Under "⚖ Compliance & Sanctions" —
the section a reader scans to decide whether they may transact — sat two items that
are not compliance findings about anyone:

    INFO  Sovereign macro context: central-govt debt 130.7% of GDP
          "…Country-level context … — not a finding against this entity."
    INFO  US federal contracts: 4 award(s), $3,409,511

R-F3000 had already recognised the first as context and cut its severity to info, but
left it in the compliance findings list. Severity was never the problem: POSITION
asserted what the wording denied. A reader who scans headings rather than bodies
carries away the impression the placement created, and the disclaimer in the detail
line arrives too late to undo it. The second is a commercial fact — neither adverse
nor exculpatory, and not what that section is for.

`context_only` is purely additive: it never changes a verdict, never drops anything,
and an unflagged finding behaves exactly as before.
"""
from aria_service.intel import dd_schema
from aria_service.intel.dd_schema import Finding


def _report_with_context():
    return {
        "identity": {"entity_name": "MITIE FACILITIES MANAGEMENT LIMITED",
                     "entity_type": "company"},
        "compliance": {
            "meta": {"status": "ok"},
            "findings": [
                {"severity": "amber", "title": "Export licence required for this end-use",
                 "detail": "dual-use", "source": "export_control"},
                {"severity": "info", "context_only": True,
                 "context_kind": "Country & market context",
                 "title": "Sovereign macro context: central-govt debt 130.7% of GDP",
                 "detail": "…not a finding against this entity.",
                 "source": "worldbank_indicators"},
                {"severity": "info", "context_only": True,
                 "context_kind": "Commercial footprint",
                 "title": "US federal contracts: 4 award(s), $3,409,511",
                 "detail": "Top awarding agencies: DoD, State.", "source": "usaspending"},
            ],
        },
    }


# ── the flag ───────────────────────────────────────────────────────────────
def test_rf3098_finding_carries_the_flag_and_defaults_off():
    plain = Finding(severity="info", title="x")
    assert plain.context_only is False and plain.context_kind == ""
    ctx = Finding(severity="info", title="y", context_only=True, context_kind="Country")
    assert ctx.context_only is True and ctx.context_kind == "Country"


def test_rf3098_flag_survives_serialisation():
    d = dd_schema._sv_finding({"severity": "info", "title": "t",
                               "context_only": True, "context_kind": "Country"})
    assert d["context_only"] is True and d["context_kind"] == "Country"


# ── the split ──────────────────────────────────────────────────────────────
def test_rf3098_context_leaves_the_decision_driving_list():
    sec = _report_with_context()["compliance"]
    titles = [f["title"] for f in dd_schema._sv_findings(sec)]
    assert titles == ["Export licence required for this end-use"]
    assert not any("Sovereign macro" in t for t in titles), (
        "R-F3098 REGRESSION: a country statistic is back among compliance findings")
    assert not any("US federal contracts" in t for t in titles)


def test_rf3098_nothing_is_dropped():
    """The complaint was placement, not presence — losing the data would be worse."""
    sec = _report_with_context()["compliance"]
    ctx = [f["title"] for f in dd_schema._sv_context_findings(sec)]
    assert len(ctx) == 2
    assert any("Sovereign macro" in t for t in ctx)
    assert any("US federal contracts" in t for t in ctx)


def test_rf3098_context_is_grouped_by_kind_not_severity():
    """Severity-ranking a country statistic against a sanctions hit is exactly the
    conflation this split exists to end."""
    sec = _report_with_context()["compliance"]
    kinds = [f["context_kind"] for f in dd_schema._sv_context_findings(sec)]
    assert kinds == sorted(kinds)


def test_rf3098_an_unflagged_report_is_completely_unchanged():
    sec = {"meta": {"status": "ok"}, "findings": [
        {"severity": "red", "title": "A"}, {"severity": "info", "title": "B"}]}
    assert [f["title"] for f in dd_schema._sv_findings(sec)] == ["A", "B"]
    assert dd_schema._sv_context_findings(sec) == []


# ── the user-visible surface ───────────────────────────────────────────────
def test_rf3098_structured_view_exposes_both_lists():
    """CAPABILITY: drive `structured_view`, the contract the online report renders."""
    sv = dd_schema.structured_view(_report_with_context())
    comp = next(s for s in sv["sections"] if s["key"] == "compliance")
    assert [f["title"] for f in comp["findings"]] == ["Export licence required for this end-use"]
    assert len(comp["context_findings"]) == 2


def test_rf3098_a_context_only_section_is_not_dropped_as_empty():
    """A non-core section whose ONLY content is context still has something to show."""
    sv = dd_schema.structured_view({
        "identity": {"entity_name": "Acme", "entity_type": "company"},
        "commercial_coherence": {"meta": {"status": "ok"}, "findings": [
            {"severity": "info", "context_only": True, "context_kind": "Commercial footprint",
             "title": "Sector context: 12 comparable suppliers"}]},
    })
    comm = next((s for s in sv["sections"] if s["key"] == "commercial"), None)
    assert comm is not None, "R-F3098 REGRESSION: a context-only section was dropped"
    assert len(comm["context_findings"]) == 1


def test_rf3098_markdown_labels_context_separately():
    from aria_service.intel.dd_schema import ARKDDReport
    r = ARKDDReport(target={"name": "Mitie"})
    r.identity.entity_name = "Mitie"
    r.compliance.findings = [
        Finding(severity="amber", title="Export licence required"),
        Finding(severity="info", title="Sovereign macro context: debt 130.7% of GDP",
                context_only=True, context_kind="Country & market context"),
    ]
    md = r.render_markdown() if hasattr(r, "render_markdown") else r.as_markdown()
    assert "Export licence required" in md
    assert "about the environment, not about this entity" in md
    assert "Sovereign macro context" in md, "context must still be PRESENT"
