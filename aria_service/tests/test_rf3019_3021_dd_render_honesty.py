"""R-F3019 / R-F3020 / R-F3021 — three render-layer honesty fixes.

R-F3019 — name the sanctions LISTS screened and the DATE. The per-list verdict has
  always existed (`_sanctions_classify.derive_verified_sources`) but nothing printed
  it, and no field carried a screening timestamp at all. A "clean" with neither a
  list nor a date is not a compliance statement.
R-F3020 — a section header reading `[OK]` was read as a quality verdict while the
  evidence grade was D. The status only ever meant "this layer ran"; say COMPLETED.
R-F3021 — a LAPSED LEI was buried inside the prose of an info finding, so nothing
  downstream could see it. Make it structured.
"""
from aria_service.intel import sanctions
from aria_service.intel.dd_schema import (
    ARKDDReport, LayerStatus, _render_screened_lists, _status_label,
)

# R-F3785/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _report(name: str) -> ARKDDReport:
    r = ARKDDReport()
    r.identity.entity_name = name
    return r


# ── R-F3019 ────────────────────────────────────────────────────────────────
_SCREEN = {
    "screened_at": "2026-07-25T09:15:00+00:00",
    "verified_sources": {
        "OFAC SDN": {"label": "US Treasury — OFAC SDN", "status": "CLEAN"},
        "UK OFSI / HMT": {"label": "HM Treasury OFSI", "status": "CLEAN"},
        "EU Consolidated": {"label": "EU Financial Sanctions Database", "status": "UNAVAILABLE"},
        "UN SC Consolidated": {"label": "UN SC Consolidated", "status": "HIT"},
    },
}


def test_rf3019_screen_stamps_a_date():
    """The field did not exist before — an undated clean is undatable."""
    import inspect
    src = module_source(sanctions)
    assert '"screened_at"' in src, "the screen must record WHEN it ran"


def test_rf3019_lists_and_date_are_named():
    lines = _render_screened_lists(_SCREEN)
    joined = "\n".join(lines)
    assert "Sanctions lists screened: 4" in joined
    assert "2026-07-25" in joined, "the screening date must be printed"
    assert "OFAC SDN" in joined and "UK OFSI / HMT" in joined


def test_rf3019_unavailable_list_is_named_separately_never_folded_into_clean():
    """never-false-clean: a list that did not answer must not be countable as clean."""
    lines = _render_screened_lists(_SCREEN)
    unavail = [l for l in lines if "DID NOT ANSWER" in l]
    assert unavail and "EU Consolidated" in unavail[0]
    assert "treat as unchecked" in unavail[0]
    clean = [l for l in lines if "No match" in l][0]
    assert "EU Consolidated" not in clean and "UN SC Consolidated" not in clean


def test_rf3019_missing_date_is_disclosed_not_faked():
    lines = _render_screened_lists({"verified_sources": {"OFAC SDN": {"status": "CLEAN"}}})
    assert "screening date not recorded" in "\n".join(lines)


def test_rf3019_no_detail_renders_nothing():
    assert _render_screened_lists({}) == []
    assert _render_screened_lists({"verified_sources": []}) == []
    assert _render_screened_lists(None) == []


def test_rf3019_compliance_section_of_a_real_report_names_the_lists():
    """CAPABILITY: drive the actual renderer a customer reads."""
    r = _report("Acme Defence Ltd")
    r.identity.sanctions_screen = _SCREEN
    md = r.render_markdown()
    body = md.split("Compliance", 1)[1]
    assert "Sanctions lists screened: 4" in body
    assert "2026-07-25" in body


# ── R-F3020 ────────────────────────────────────────────────────────────────
def test_rf3020_ok_renders_as_completed():
    assert _status_label("ok") == "COMPLETED"
    assert _status_label(LayerStatus.OK.value) == "COMPLETED"


def test_rf3020_other_statuses_are_untouched():
    assert _status_label("error") == "ERROR"
    assert _status_label("partial") == "PARTIAL"
    assert _status_label("degraded") == "DEGRADED"
    assert _status_label("something_new") == "SOMETHING_NEW", "unknown must not render blank"


def test_rf3020_stored_enum_value_is_unchanged():
    """The wire contract stays 'ok' — only the DISPLAY changes."""
    assert LayerStatus.OK.value == "ok"


def test_rf3020_rendered_report_has_no_bare_ok_header():
    r = _report("Acme Defence Ltd")
    md = r.render_markdown()
    assert "[OK]" not in md, "the header that read as a quality verdict"
    assert "[COMPLETED]" in md


# ── R-F3021 ────────────────────────────────────────────────────────────────
def test_rf3021_lei_registration_is_a_structured_field():
    r = _report("Acme Defence Ltd")
    assert r.identity.lei_registration == {}


def test_rf3021_lapsed_lei_is_rendered_and_qualified():
    r = _report("Acme Defence Ltd")
    r.identity.registration_number = "05684823"
    r.identity.lei_registration = {
        "lei": "213800XYZ", "registration_status": "LAPSED",
        "entity_status": "ACTIVE", "lapsed": True,
        "source_url": "https://search.gleif.org/#/record/213800XYZ",
    }
    md = r.render_markdown()
    assert "LEI: 213800XYZ" in md and "LAPSED" in md
    # honesty: a lapsed LEI is an administrative fact, not an accusation
    assert "not a sanctions or solvency signal" in md


def test_rf3021_issued_lei_is_stated_not_silent():
    r = _report("Acme Defence Ltd")
    r.identity.registration_number = "05684823"
    r.identity.lei_registration = {"lei": "213800XYZ", "registration_status": "ISSUED",
                                   "entity_status": "ACTIVE", "lapsed": False}
    md = r.render_markdown()
    assert "Registration: ISSUED" in md
    assert "LAPSED" not in md


def test_rf3021_orchestrator_only_flags_lapsed_on_a_real_gleif_status():
    import inspect
    from aria_service.intel import dd_orchestrator as ddo
    src = module_source(ddo)
    assert 'report.identity.lei_registration = {' in src
    assert '{"LAPSED", "RETIRED", "ANNULLED"}' in src, "only GLEIF's own lapse states"
