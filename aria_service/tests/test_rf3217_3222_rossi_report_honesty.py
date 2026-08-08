"""R-F3217..R-F3222 — the Rossi report (07101898) said things it had not earned.

Operator-reported, 2026-07-27. Subject supplied as
"Rossi Security (Rossi Facility Services Ltd)". The delivered report carried, on
one page:

    Identity   · Sanctions matches   none
    Identity   · Sanctions screen CLEAN … UK OFSI … CONFIRMED
    Scorecard  · Sanctions and export-control exposure — UNRESOLVED

All three describe the same object. The screen had queried NOTHING: the
entity-shape guard rejects a token beginning "(", so `screen_with_aliases`
returned `{"error": "not_entity_shaped"}` with zero calls made — and every
consumer except the scorecard failed OPEN.

Each test below drives the path that actually produced the wrong output, not a
helper beside it (CLAUDE.md §3c/§23).
"""
import asyncio
import types

import pytest

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import dd_schema, sanctions

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source

SUPPLIED = "Rossi Security (Rossi Facility Services Ltd)"
REGISTERED = "ROSSI FACILITY SERVICES LTD"


# ── R-F3218 — the name must be screenable at all ─────────────────────────────

def test_rf3218_bracketed_registered_name_is_entity_shaped():
    """The exact string the operator supplied. Pre-fix: False → screen skipped."""
    assert sanctions._looks_like_entity_name(SUPPLIED) is True
    assert sanctions._looks_like_entity_name("Acme (Holdings) Ltd") is True


def test_rf3218_split_screens_both_halves():
    outer, inner = sanctions.split_bracketed_name(SUPPLIED)
    assert outer == "Rossi Security"
    assert inner == ["Rossi Facility Services Ltd"]


def test_rf3218_fragment_guard_still_rejects_search_queries():
    """The guard exists for a reason — do not widen it into a no-op."""
    assert sanctions._looks_like_entity_name("Iran nexus before engagement") is False
    assert sanctions._looks_like_entity_name(
        "sanctions update OFAC SDN embargo 2026") is False


def test_rf3218_screen_with_aliases_reaches_the_source(monkeypatch):
    """CAPABILITY: the broken path. Pre-fix this returned not_entity_shaped
    having made zero calls; the assertion is that the source is actually asked."""
    asked: list[str] = []

    async def _fake_fuzzy(target, **kw):
        asked.append(target)
        return {"name": target, "matches": [], "screened": True, "top_score": 0,
                "blocked": False}

    monkeypatch.setattr(sanctions, "fuzzy_screen", _fake_fuzzy)
    out = asyncio.run(sanctions.screen_with_aliases(SUPPLIED))
    assert out.get("error") is None, out
    assert out.get("screened") is True
    assert any("Rossi Facility Services" in a for a in asked), asked


# ── R-F3217 — an unperformed screen must never render as CLEAN ───────────────

def _clean_branch_code() -> str:
    """The guard that decides CLEAN vs NOT-PERFORMED, comments stripped.

    Comments are stripped because a source guard that matches its own explanatory
    comment proves nothing — the R-F3129 lesson, and this file's comment names
    every key it is asserting about."""
    import inspect
    src = module_source(ddo)
    i = src.index("R-F3217 — FAIL CLOSED ON *ANY* UNPERFORMED SCREEN")
    # Walk back to the `elif` that opens the branch, forward past the CLEAN else.
    start = src.rindex("elif (", 0, i)
    window = src[start:i]
    return "\n".join(l for l in window.splitlines()
                     if not l.strip().startswith("#"))


def test_rf3217_clean_branch_is_not_an_enumeration_of_two_keys():
    """THE DEFECT: the branch tested `source_unavailable` and ONE error string, so
    every other failure mode fell through to CLEAN. It must test the positive
    condition — the screen provably ran — not a list of known failures."""
    code = _clean_branch_code()
    assert 'screen.get("screened") is not True' in code, code
    assert 'or screen.get("error")\n' in code or 'screen.get("error")' in code, code
    # The narrow equality test is what let `not_entity_shaped` through.
    assert 'screen.get("error") == "sanctions_source_unavailable"' not in code, (
        "R-F3217 REGRESSION: the CLEAN branch is again enumerating known failures")


def test_rf3217_unperformed_screen_reason_is_named():
    """Telling a reader the SOURCE was unreachable when the truth is 'the name was
    not screenable' sends them to check a system that is working (R-F3125 class)."""
    import inspect
    src = module_source(ddo)
    i = src.index("R-F3217 — name the REASON THIS screen failed")
    window = src[i:i + 1800]
    assert 'not_entity_shaped' in window
    assert "was not accepted as an" in window


def test_rf3217_chip_says_not_screened(monkeypatch):
    """The identity panel chip — the second surface that published a clean."""
    assert dd_schema._sanctions_match_metric(
        {"error": "not_entity_shaped", "matches": [],
         "match_classification": {"total": 0, "noise_filtered": 0, "actionable": 0}}
    ) == "NOT SCREENED — see data gaps"
    # A screen that RAN and found nothing still reports none.
    assert dd_schema._sanctions_match_metric(
        {"screened": True, "matches": [],
         "match_classification": {"total": 0, "noise_filtered": 0, "actionable": 0}}
    ) == "none"


def test_rf3217_quality_penalty_fires_on_any_screen_error():
    """The third surface: a screen that never ran scored identically to one that
    completed, because only the string 'sanctions_source_unavailable' counted."""
    body = {"identity": {"sanctions_screen": {"error": "not_entity_shaped"}}}
    metrics = dd_schema._quality_metrics(body)
    assert metrics["sanctions_source_unavailable"] is True
    reasons = [r for _pts, r in dd_schema._quality_penalties(metrics)]
    assert any("sanctions screen source was unavailable" in r for r in reasons), reasons


def test_rf3217_bluf_does_not_claim_no_blocking_risk_when_sanctions_open():
    readiness = {
        "answered": 4, "required": 5, "completion_pct": 80,
        "evidence_grade": "C", "evidence_ready": False, "clearance_ready": False,
        "questions": {
            "identity": {"label": "Verified legal identity", "answered": True},
            "sanctions_export_control": {
                "label": "Sanctions and export-control exposure", "answered": False},
        },
    }
    bluf = ddo.compose_decision_bluf(readiness, REGISTERED)
    assert "no blocking risk" not in bluf["bottom_line"], bluf["bottom_line"]
    assert "cannot state whether blocking risk exists" in bluf["bottom_line"]


# ── R-F3220 — a node list is not a chain ─────────────────────────────────────

def test_rf3220_officer_nodes_are_not_rendered_as_an_ownership_chain():
    """CAPABILITY: `_dd_entity_scope` on the Rossi network shape. Pre-fix this
    produced 'subject → ALKSMANTAS → DIMITROV → ROSSI, Cibele → …'."""
    body = {
        "identity": {"entity_name": REGISTERED, "registration_number": "07101898",
                     "entity_type": "company"},
        "network": {
            "controlled_by": [{"controller_name": "Rossi Support Services Ltd",
                               "controller_registration_number": "14833360"}],
            "ubo_chain": [
                {"name": REGISTERED, "hop_depth": 0},
                {"name": "ALKSMANTAS, Ernestas", "hop_depth": 1},
                {"name": "DIMITROV, Dimitar Stoyanov", "hop_depth": 1},
                {"name": "ROSSI, Cibele", "hop_depth": 1},
                {"name": "ROSSI, Cibele Rocha", "hop_depth": 1},
            ],
        },
    }
    scope = dd_schema._dd_entity_scope(body)

    # The arrow chain is now the registry-anchored descent only.
    assert scope["ownership_chain_traced"] == [REGISTERED, "Rossi Support Services Ltd"]
    assert "ALKSMANTAS, Ernestas" not in scope["ownership_chain_traced"]

    hop1 = [p for p in scope["parties_traversed"] if p["hop"] == 1]
    assert hop1 and "officer" in hop1[0]["relation"].lower()
    assert "ALKSMANTAS, Ernestas" in hop1[0]["names"]


def test_rf3220_same_person_reached_twice_is_counted_once():
    body = {
        "identity": {"entity_name": REGISTERED, "entity_type": "company"},
        "network": {
            "controlled_by": [{"controller_name": "Rossi Support Services Ltd",
                               "controller_registration_number": "14833360"}],
            "ubo_chain": [
                {"name": "ROSSI, Cibele Rocha", "hop_depth": 1},
                {"name": "Rocha Cibele ROSSI", "hop_depth": 1},
            ],
        },
    }
    scope = dd_schema._dd_entity_scope(body)
    assert scope["parties_traversed_count"] == 1, scope["parties_traversed"]


# ── R-F3221 — a shared surname is not coverage ───────────────────────────────

def test_rf3221_surname_only_hits_are_not_entity_coverage():
    """CAPABILITY: the gate that let a US fraud story into a clean report."""
    toks = ddo._entity_distinctive_tokens(REGISTERED)
    assert ddo._press_hit_is_relevant(
        "Unsealed suits against Rossi, Reditus allege widespread fraud and corruption",
        "", "https://ciproud.com/rossi-reditus", toks) is False
    assert ddo._press_hit_is_relevant(
        "George Rossi", "", "https://x.com/george-rossi", toks) is False
    # Genuine coverage survives.
    assert ddo._press_hit_is_relevant(
        "ROSSI FACILITY SERVICES LTD overview - GOV.UK", "",
        "https://find-and-update.company-information.service.gov.uk/company/07101898",
        toks) is True
    # The entity's own domain survives on one token, via the host.
    assert ddo._press_hit_is_relevant(
        "About Us", "", "https://www.rossisecurity.co.uk/about-us/", toks) is True


def test_rf3221_single_distinctive_token_entities_are_unchanged():
    toks = ddo._entity_distinctive_tokens("Supacat Limited")
    assert ddo._press_hit_is_relevant(
        "Supacat wins MoD contract", "", "https://bbc.co.uk/news/x", toks) is True


# ── R-F3222 — wording and dead code ──────────────────────────────────────────

def test_rf3222_citation_penalty_names_the_real_obstacle():
    body = {"verification": {"citations_checked": 0, "citations_grounded": 0}}
    reasons = [r for _p, r in dd_schema._quality_penalties(dd_schema._quality_metrics(body))]
    assert any("no citation was checked against its source" in r for r in reasons), reasons
    assert not any("grounded by source verifier" in r for r in reasons), reasons
    # Must not collide with the R-F3132 grounding-RATE guard, which filters on the
    # phrase "citation grounding" — a sub-threshold sample still may not penalise.
    assert not any("citation grounding" in r for r in reasons), reasons


def test_rf3222_no_unreachable_bluf_block():
    """A second BLUF writer is how R-F3019/R-F3039/R-F3091 each shipped broken."""
    import inspect
    src = function_source(ddo, "_refresh_persisted_decision_readiness")
    # Everything after the FINAL return is unreachable by construction.
    tail = src[src.rindex("return readiness") + len("return readiness"):]
    assert "bottom_line" not in tail, (
        "unreachable BLUF composition is back after the final return")
