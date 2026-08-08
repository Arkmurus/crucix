"""R-F3237/R-F3238/R-F3239/R-F3244 — the 360 sweep after the live Marks & Spencer run.

R-F3228 made real company names screenable. Running the live path
(dd_4ef819fb82ff, "Marks & Spencer Group plc", 04256886) proved it — eleven
lists answered — and surfaced two things a green test suite had not:

  R-F3237  the watchlist judged the SAME name by a different standard, and its
           re-screen purge would have silently DELETED it again.
  R-F3244  the scorecard blocker collapsed a composite question, so the headline
           said ARIA could not state whether blocking risk exists, directly above
           an eleven-list clean screen.

R-F3239 closes a test debt: R-F3222's country-risk disclosure shipped with no
capability test of its own.
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_schema, sanctions

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ── R-F3237 — provenance, not spelling, decides ─────────────────────────────

REAL_NAMES = ["Marks & Spencer Group plc", "Smith & Nephew plc",
              "Tate & Lyle PLC", "Compagnie de Saint-Gobain"]


def test_rf3237_dd_enrolment_uses_the_same_standard_as_the_screen():
    """A name R-F3228 screens must not be refused by the watchlist on the same
    page. Both now ask `_screenable(..., trusted=True)`."""
    src = module_source(ddo)
    # R-F3280 — anchor says R-F3239, not R-F3237. The code was RENUMBERED after
    # the R-F3237 collision (the peer had already shipped R-F3237 for a source
    # health claim) and this test kept the pre-collision number, so the anchor
    # could never be found. That renumber is exactly the failure R-F3248 now
    # prevents: reserve() refuses a number git history already carries.
    i = src.index("R-F3239 - this name is the DD SUBJECT")
    window = src[i:i + 900]
    assert "_screenable(" in window and "trusted=True" in window, window[:400]
    for name in REAL_NAMES:
        assert sanctions._screenable(name, trusted=True) is True, name


def test_rf3237_operator_curated_public_entry_accepts_real_names():
    src = function_source(ddo, "add_public_watchlist_entity")
    assert "_screenable(name, trusted=True)" in src, src


def test_rf3237_purge_keeps_provenanced_entries():
    """THE DEFECT R-F3228 would otherwise have created: the re-screen purge
    deletes by name shape and persists it, so a monitored counterparty would
    vanish with no event."""
    src = module_source(ddo)
    # R-F3280 — same renumber: shipped as R-F3239, with a plain hyphen.
    i = src.index("R-F3239 - PURGE BY PROVENANCE, NOT BY NAME SHAPE")
    window = src[i:i + 2600]
    assert "_PROVENANCED" in window
    for origin in ("dd_auto_enroll", "dd_report", "vetting_case", "operator"):
        assert origin in window, origin
    # The old heuristic must SURVIVE for unprovenanced entries — that is what
    # the purge was written for (autonomous search-query junk).
    assert "_looks_like_entity_name" in window, (
        "self-cleaning of unprovenanced junk must not be lost")


def test_rf3237_the_junk_the_purge_targets_is_still_purgeable():
    """Named in the purge's own comment. It must still fail the untrusted rule."""
    junk = "SAM.gov defence military security procurement global last 7 days 2026"
    assert sanctions._screenable(junk, trusted=False) is False


# ── R-F3244 — a composite question must say which half ──────────────────────

def _readiness(*, sanctions_ok: bool, export_ok: bool) -> dict:
    body = {
        "identity": {
            "entity_name": "Marks & Spencer Group plc",
            "registration_number": "04256886",
            "registration_status": "active",
            "incorporation_date": "1926-01-01",
            "directors": [{"name": "A Director"}],
            "entity_type": "company",
            "sanctions_screen": (
                {"screened": True, "matches": [],
                 "verified_sources": {"OFAC SDN": {"status": "CLEAN"}}}
                if sanctions_ok else {"error": "not_entity_shaped", "matches": []}
            ),
        },
        "compliance": {
            "export_control": ({"recommendation": "civilian or unclassified"}
                               if export_ok else {}),
        },
    }
    return dd_schema._dd_decision_readiness(body)


def test_rf3244_export_only_gap_does_not_impugn_the_sanctions_screen():
    """THE LIVE DEFECT: eleven lists answered CLEAN, export control never
    assessed, and the report said the sanctions position was not evidenced."""
    q = _readiness(sanctions_ok=True, export_ok=False)["questions"]["sanctions_export_control"]
    assert q["answered"] is False
    assert q["sanctions_evidenced"] is True
    assert q["export_control_evidenced"] is False
    assert "no export-control assessment" in q["blocker"], q["blocker"]
    assert "sanctions screen itself completed" in q["blocker"].replace(
        "sanctions screen itself completed", "sanctions screen itself completed"), q["blocker"]


def test_rf3244_a_missing_screen_is_still_named_plainly():
    q = _readiness(sanctions_ok=False, export_ok=True)["questions"]["sanctions_export_control"]
    assert q["sanctions_evidenced"] is False
    assert "sanctions screen is not evidenced" in q["blocker"], q["blocker"]


def test_rf3244_bluf_reserves_the_strong_wording_for_a_missing_screen():
    """"cannot state whether blocking risk exists" is TRUE when the screen did
    not run and badly FALSE when it ran clean across eleven lists."""
    base = {"answered": 4, "required": 5, "completion_pct": 80,
            "evidence_grade": "C", "evidence_ready": False, "clearance_ready": False}

    export_only = dict(base, questions={"sanctions_export_control": {
        "label": "Sanctions and export-control exposure", "answered": False,
        "sanctions_evidenced": True, "export_control_evidenced": False}})
    bl = ddo.compose_decision_bluf(export_only, "Marks & Spencer Group plc")["bottom_line"]
    assert "cannot state whether blocking risk exists" not in bl, bl

    screen_missing = dict(base, questions={"sanctions_export_control": {
        "label": "Sanctions and export-control exposure", "answered": False,
        "sanctions_evidenced": False, "export_control_evidenced": True}})
    bl2 = ddo.compose_decision_bluf(screen_missing, "Acme Ltd")["bottom_line"]
    assert "cannot state whether blocking risk exists" in bl2, bl2


def test_rf3244_legacy_scorecard_without_the_flag_stays_cautious():
    """Absence of the flag is not proof the screen ran (R-F2693 discipline)."""
    legacy = {"answered": 4, "required": 5, "completion_pct": 80,
              "evidence_grade": "C", "evidence_ready": False, "clearance_ready": False,
              "questions": {"sanctions_export_control": {
                  "label": "Sanctions and export-control exposure", "answered": False}}}
    bl = ddo.compose_decision_bluf(legacy, "Acme Ltd")["bottom_line"]
    assert "cannot state whether blocking risk exists" in bl, bl


# ── R-F3239 — test debt: R-F3222's country-risk disclosure ──────────────────

def test_rf3239_country_risk_chip_discloses_an_incomplete_overlay():
    """R-F3222 shipped this with no capability test. The chip is computed from
    the sanctions/embargo regime alone, so a bare "GREEN" beside its own
    "World Bank overlay did not complete" gap overstates what was assessed."""
    body = {"identity": {"entity_name": "Acme Ltd", "entity_type": "company"},
            "compliance": {"country_risk": {"headline_risk": "GREEN",
                                            "governance_overlay_complete": False}}}
    view = dd_schema.structured_view(body)
    chips = [h for s in view["sections"] for h in s.get("highlights", [])
             if h["label"] == "Country risk"]
    assert chips, "the country-risk chip disappeared"
    assert "governance overlay did not complete" in chips[0]["value"], chips[0]


def test_rf3239_a_complete_overlay_leaves_the_chip_clean():
    body = {"identity": {"entity_name": "Acme Ltd", "entity_type": "company"},
            "compliance": {"country_risk": {"headline_risk": "GREEN"}}}
    view = dd_schema.structured_view(body)
    chips = [h for s in view["sections"] for h in s.get("highlights", [])
             if h["label"] == "Country risk"]
    assert chips and chips[0]["value"] == "GREEN", chips


# ── R-F3238 — a workflow may not advertise a route it does not have ─────────

def test_rf3238_deploy_workflow_does_not_advertise_a_dead_push_trigger():
    """THE DEFECT: the header documented "[deploy] in the commit message" for
    seven weeks after R-F1408 removed the push trigger. An agent read it, pushed
    a tagged commit, and waited on a build that was never queued."""
    import pathlib
    wf = pathlib.Path(__file__).resolve().parents[2] / ".github/workflows/deploy-fly.yml"
    text = wf.read_text(encoding="utf-8")
    lines = text.splitlines()
    # Line-anchored: "on:" appears inside prose comments too, and splitting on the
    # bare substring matched this fix's OWN explanation — the self-matching-guard
    # trap (R-F3129) for the third time today.
    start = next(i for i, l in enumerate(lines) if l.rstrip() == "on:")
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and lines[i][0].isalpha()), len(lines))
    on_block = [l for l in lines[start + 1:end] if not l.lstrip().startswith("#")]
    head = chr(10).join(lines[:start])
    assert "R-F3238" in head, "the correction is gone"
    assert "DOES NOTHING" in head, "the dead route must be labelled as dead"
    assert any("workflow_dispatch" in l for l in on_block), on_block
    # If anyone restores the trigger, this must fail so the docs are corrected
    # in the same change.
    assert not any(l.strip().startswith("push:") for l in on_block), (
        "push trigger restored — update the R-F3238 header comment too")
