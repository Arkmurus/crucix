"""R-F4257 — the tool-use resolution axis measures a configuration that never ships.

THIRTEEN candidates and arms have been funded to move `tooluse_resolution`, and
not one has exceeded 13/16 — the SFT parent's own score. This pins WHY, at the
points verified on 2026-08-23, so the finding cannot rot into folklore and
nobody funds a fourteenth.

Production resolves company identity DETERMINISTICALLY and then removes the
model's freedom to disagree:

  1. `resolve_company_search` picks the company, returning None for ambiguous,
     dead or partial matches so no inferred registration number reaches
     identity-dependent downstream work.
  2. `format_for_prompt` hands the model the RESOLVED company —
     `Company: COMPASS LTD (11466170)` — or, when resolution failed, an explicit
     `[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]` gate. It never hands the
     model a candidate list to choose from.
  3. `enforce_resolution_response` (R-F4144) REPLACES the model's answer when
     that gate is present, fail-closed.

The 168-row eval does none of this. It feeds the raw candidate list and grades
the model on reproducing, in prose, what `resolve_company` computes exactly. The
two failure shapes it measures are therefore both structurally impossible in the
shipped path:

  * `Prudential` — "did not ask for clarification". Production emits the gate and
    OVERRIDES the answer.
  * `Compass` — "did not select the resolved company; listing registry candidates
    is not a resolution". Production never presents candidates; the resolved
    company is line one of the context block.

THE COUNTER-ARGUMENT, STATED FAIRLY: this axis can be read as defence in depth —
what the model would do if the enforcement layer ever failed. That is a
legitimate thing to measure. It is NOT a reason to block a promotion that
improves a primary axis, and this file deliberately changes no gate. Dropping an
axis to make a candidate pass is the "close the gate by measuring less" failure
CLAUDE.md section 1 forbids. The trade is the operator's call.

These assertions are BEHAVIOURAL where they can be. An earlier draft grepped the
module's prose and failed on line wrapping — the same assert-the-invariant-not-
the-spelling lesson this repo has now recorded several times.
"""
from __future__ import annotations

import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
EVAL_SET = ROOT / "data/training/split_v1/eval.jsonl"
CH = ROOT / "aria_service/intel/companies_house.py"
POD_EVALUATOR = ROOT / "scripts/train/eval_tooluse.py"
SHIM = ROOT / "scripts/train/serve_eval_shim.py"

GATE_MARKER = "[COMPANIES HOUSE — IDENTITY RESOLUTION REQUIRED]"
DATA_MARKER = "[COMPANIES HOUSE — UK REGISTRY DATA]"


class TestProductionResolvesDeterministically:
    def test_the_resolver_fails_closed_on_ambiguity(self):
        """Two equally-good live matches must yield None, so no inferred number
        reaches downstream work."""
        from aria_service.intel import companies_house as ch
        selected, _decision = ch.resolve_company_search("Acme", [
            {"title": "ACME LTD", "company_number": "0000001",
             "company_status": "active"},
            {"title": "ACME LIMITED", "company_number": "0000002",
             "company_status": "active"},
        ])
        assert selected is None

    def test_it_still_resolves_an_unambiguous_query(self):
        """The guard must not be 'always None' — that would certify nothing."""
        from aria_service.intel import companies_house as ch
        selected, _decision = ch.resolve_company_search("Acme Ltd", [
            {"title": "ACME LTD", "company_number": "0000001",
             "company_status": "active"}])
        assert selected is not None
        assert selected["company_number"] == "0000001"

    def test_the_dd_investigation_path_calls_it(self):
        """If nothing called it, this whole finding would be theoretical."""
        source = CH.read_text(encoding="utf-8")
        assert source.count("resolve_company_search(") >= 2

    def test_an_unresolved_query_becomes_an_explicit_gate(self):
        from aria_service.intel import companies_house as ch
        block = ch.format_for_prompt({
            "resolution_required": True,
            "resolution": {"query": "Prudential", "reasons": ["ambiguous"],
                           "candidates": [{"title": "PRUDENTIAL PUBLIC LIMITED COMPANY",
                                           "status": "active",
                                           "company_number": "01397169"}]},
        })
        assert GATE_MARKER in block
        assert "cannot safely identify" in block

    def test_a_resolved_query_hands_over_the_company_not_a_candidate_list(self):
        """The Compass failure shape cannot arise: there is no list to pick from."""
        from aria_service.intel import companies_house as ch
        block = ch.format_for_prompt({
            "found": True,
            "profile": {"company_name": "COMPASS LTD", "company_number": "11466170",
                        "company_status": "active", "company_type": "ltd",
                        "date_of_creation": "2018-07-16", "sic_codes": []},
        })
        assert DATA_MARKER in block
        assert "COMPASS LTD (11466170)" in block
        assert GATE_MARKER not in block

    def test_the_model_answer_is_overridden_when_the_gate_is_present(self):
        """R-F4144, measured: an answer that picks a company anyway is REPLACED."""
        from aria_service.intel import companies_house as ch
        context = "\n".join([
            ch._RESOLUTION_REQUIRED_MARKER,
            "I cannot safely identify 'Prudential'. ",
            "Candidates: PRUDENTIAL PUBLIC LIMITED COMPANY (active, 01397169)",
        ])
        freelanced = "The first result is PRUDENTIAL PUBLIC LIMITED COMPANY (01397169)."
        answer, changed = ch.enforce_resolution_response(context, freelanced)
        assert changed is True
        assert "cannot safely identify" in answer
        assert answer != freelanced

    def test_enforcement_is_a_no_op_without_the_gate(self):
        """It must not rewrite ordinary answers, or it would be a muzzle rather
        than an identity guard."""
        from aria_service.intel import companies_house as ch
        original = "ACME LTD (0000001) is the company."
        answer, changed = ch.enforce_resolution_response(
            DATA_MARKER + "\nCompany: ACME LTD (0000001)", original)
        assert changed is False
        assert answer == original


class TestTheEvalCarriesNoneOfIt:
    def test_no_eval_row_carries_the_production_identity_gate(self):
        if not EVAL_SET.is_file():
            pytest.skip("frozen eval set not present here")
        from aria_service.intel.companies_house import _RESOLUTION_REQUIRED_MARKER
        rows = [line for line in EVAL_SET.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        assert len(rows) == 168
        assert sum(1 for line in rows if _RESOLUTION_REQUIRED_MARKER in line) == 0, (
            "if the eval ever adopts the production gate this finding changes — "
            "re-verify before trusting it"
        )

    def test_the_pod_evaluator_applies_no_enforcement(self):
        for path in (POD_EVALUATOR, SHIM):
            if not path.is_file():
                pytest.skip(f"{path.name} not present here")
            source = path.read_text(encoding="utf-8")
            assert "enforce_resolution_response" not in source
            assert "companies_house" not in source

    def test_the_axis_still_exists_and_is_still_sixteen_rows(self):
        """The finding is about what the axis MEASURES, not a claim it is gone."""
        if not EVAL_SET.is_file():
            pytest.skip("frozen eval set not present here")
        rows = [json.loads(line) for line in
                EVAL_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len([r for r in rows if r.get("label") == "tooluse_resolution"]) == 16


class TestTheAnswerIsComputableWithoutTheModel:
    """Every subject no candidate has ever learned is solved exactly by a
    function, from the payload already in the model's prompt."""

    EXPECTED = {
        "Cobham": "COBHAM LIMITED",
        "Meggitt": "MEGGITT LIMITED",
        "Compass": "COMPASS LTD",
        "Prudential": None,          # no resolution -> ask
        "Prudential plc": None,
    }

    def test_the_deterministic_resolver_answers_every_failing_subject(self):
        if not EVAL_SET.is_file():
            pytest.skip("frozen eval set not present here")
        from scripts.train.build_tooluse_corpus import resolve_company
        rows = [json.loads(line) for line in
                EVAL_SET.read_text(encoding="utf-8").splitlines() if line.strip()]
        seen: dict[str, str | None] = {}
        for row in rows:
            subject = row.get("subject")
            if row.get("label") != "tooluse_resolution" or subject not in self.EXPECTED:
                continue
            payload = None
            for message in row.get("messages") or row.get("prompt") or []:
                if (isinstance(message, dict) and message.get("role") == "tool"
                        and message.get("name") == "companies_house_search"):
                    payload = json.loads(message.get("content") or "{}")
            assert payload is not None, f"{subject}: no registry payload"
            chosen, _reason, _ambiguous = resolve_company(
                subject, payload.get("results") or [])
            seen[subject] = chosen.get("title") if chosen else None
        assert seen == self.EXPECTED, (
            "the resolver's answers changed — this finding rests on them"
        )
