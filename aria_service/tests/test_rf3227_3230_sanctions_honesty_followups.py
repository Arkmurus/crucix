"""R-F3227..R-F3230 — the follow-ups the Rossi fix exposed.

R-F3217 closed a false clean on three surfaces. Auditing the rest of the same
class found four more instances, each verified live before being fixed:

  R-F3230  `evidence_brief` told the RED second-opinion model "Sanctions screen:
           CLEAN" whenever the screen dict was empty — a fabricated clean fed to
           the model whose job is to challenge an escalation.
  R-F3227  "distinctive tokens" had TWO definitions with different generic sets,
           so two generic finance words satisfied the R-F3221 two-token rule and
           a fraud headline about strangers was still admissible as coverage.
  R-F3228  the search-query shape heuristic refused to screen 6 of 32 real UK
           company names (Marks & Spencer, Smith & Nephew, Tate & Lyle, …).
  R-F3229  the markdown export still fell through to "CLEAN ✅".
"""
from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import sanctions
from aria_service.intel.dd_schema import ARKDDReport

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


# ── R-F3230 — never hand the verdict model a clean it did not earn ──────────

def test_rf3230_absent_screen_is_not_described_as_clean():
    """THE DEFECT: `sanctions_screen or 'CLEAN'` on an empty dict."""
    for empty in ({}, None):
        line = ddo._sanctions_brief_line(empty)
        assert "NOT PERFORMED" in line, line
        assert "CLEAN" not in line.upper().replace("NEVER AS CLEAR", ""), line


def test_rf3230_errored_screen_is_not_described_as_clean():
    line = ddo._sanctions_brief_line({"error": "not_entity_shaped", "matches": []})
    assert "NOT PERFORMED" in line and "not_entity_shaped" in line, line


def test_rf3230_a_real_clean_screen_is_still_reported_as_clean():
    """Going silent on a genuine clean would be R-F1696 in reverse."""
    line = ddo._sanctions_brief_line({
        "screened": True, "matches": [],
        "match_classification": {"actionable": 0, "total": 0, "noise_filtered": 0},
        "verified_sources": {"OFAC SDN": {"status": "CLEAN"},
                             "UK OFSI / HMT": {"status": "CLEAN"}},
    })
    assert line.startswith("no matches"), line
    assert "2 list(s) answered" in line, line


def test_rf3230_the_raw_screen_dict_no_longer_reaches_the_prompt():
    """Comment-stripped: this guard quotes the very string it forbids, and a
    source grep that matches its OWN explanation proves nothing (the R-F3129
    lesson, hit again writing this file)."""
    import ast
    import inspect

    tree = ast.parse(module_source(ddo))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            body.pop(0)          # the docstring, where this fix EXPLAINS itself
    code = ast.unparse(tree)     # comments are gone by construction
    assert "sanctions_screen or 'CLEAN'" not in code, (
        "R-F3230 REGRESSION: the verdict prompt is again defaulting to CLEAN")


# ── R-F3227 — ONE definition of distinctive ─────────────────────────────────

def test_rf3227_both_gates_agree_on_every_name():
    """THE DEFECT: the two gates disagreed, in opposite directions."""
    for name in ("Silverbrook Capital Management",
                 "Rossi Security (Rossi Facility Services Ltd)",
                 "ROSSI FACILITY SERVICES LTD",
                 "Supacat Limited",
                 "Measure Group Europe Limited",
                 "G4S Secure Solutions Limited"):
        assert sorted(ddo._distinctive_tokens(name)) == sorted(
            ddo._entity_distinctive_tokens(name)), (
            f"the two distinctive-token definitions disagree on {name!r} — "
            "that fork is what defeated R-F3221")


def test_rf3227_two_generic_finance_words_are_not_an_identification():
    """MEASURED before the fix: both of these were admitted as coverage of
    'Silverbrook Capital Management' because 'capital' + 'management' counted
    as two distinctive tokens."""
    toks = ddo._entity_distinctive_tokens("Silverbrook Capital Management")
    assert ddo._press_hit_is_relevant(
        "System Capital Management sued for widespread fraud", "",
        "https://news.example/system-capital-management", toks) is False
    assert ddo._press_hit_is_relevant(
        "Ashcroft Capital Management fined by SEC", "",
        "https://sec.example/ashcroft-capital-management", toks) is False
    # The subject's own coverage still lands.
    assert ddo._press_hit_is_relevant(
        "Silverbrook Capital Management appoints new CFO", "",
        "https://ft.example/silverbrook", toks) is True


def test_rf3227_press_gate_keeps_its_list_contract():
    """Callers index into this; it must stay an ordered list, not a set."""
    out = ddo._entity_distinctive_tokens("ROSSI FACILITY SERVICES LTD")
    assert isinstance(out, list) and out == ["rossi", "facility"]


# ── R-F3228 — provenance decides whether to second-guess the name ───────────

REAL_NAMES_THE_HEURISTIC_REJECTED = [
    "Marks & Spencer Group plc",
    "Smith & Nephew plc",
    "Tate & Lyle PLC",
    "Compagnie de Saint-Gobain",
    "A.G. Barr p.l.c.",
    "Rossi Security, Ltd",
]


def test_rf3228_real_company_names_are_screenable_as_dd_subjects():
    """THE DEFECT: 6 of 32 live UK company names could not be screened at all."""
    for name in REAL_NAMES_THE_HEURISTIC_REJECTED:
        assert sanctions._screenable(name, trusted=True) is True, name


def test_rf3228_free_text_still_gets_the_heuristic():
    """The guard earns its keep on the path it was written for — 80+ wasted
    OpenSanctions calls per cycle from tasks.yaml prose."""
    for prose in ("sanctions update OFAC SDN EU UN Security Council embargo 2026",
                  "Iran nexus before engagement"):
        assert sanctions._screenable(prose, trusted=False) is False, prose


def test_rf3228_the_denylist_holds_in_both_modes():
    """R-F49: 'ITAR' has the same shape as 'BAE'. Trust must not unlock it."""
    for acronym in ("ITAR", "OFAC", "KYC", "NATO"):
        assert sanctions._screenable(acronym, trusted=True) is False, acronym
        assert sanctions._screenable(acronym, trusted=False) is False, acronym


def test_rf3228_trusted_still_refuses_prose():
    assert sanctions._screenable(" ".join(["word"] * 13), trusted=True) is False


def test_rf3228_default_source_is_untrusted():
    """A path must opt IN to being trusted; existing callers keep today's rules."""
    import inspect
    sig = inspect.signature(sanctions.screen_with_aliases)
    assert sig.parameters["source"].default == "free_text"
    assert "free_text" not in sanctions._TRUSTED_NAME_SOURCES


def test_rf3228_the_dd_subject_path_declares_its_provenance():
    import inspect
    src = module_source(ddo)
    assert 'screen_with_aliases(name, source="dd_subject")' in src
    assert 'source="registry"' in src


# ── R-F3229 — the exported deliverable ──────────────────────────────────────

def _report_with_screen(screen: dict) -> ARKDDReport:
    r = ARKDDReport(run_id="t", target={"name": "Acme Ltd"})
    r.identity.entity_name = "Acme Ltd"
    r.identity.sanctions_screen = screen
    return r


def test_rf3229_markdown_does_not_tick_an_unperformed_screen():
    md = _report_with_screen({"screened": False, "matches": []}).render_markdown()
    assert "CLEAN ✅" not in md, md[:600]
    assert "NOT SCREENED" in md


def test_rf3229_markdown_still_reports_a_genuine_clean():
    md = _report_with_screen({"screened": True, "matches": []}).render_markdown()
    assert "CLEAN ✅" in md
