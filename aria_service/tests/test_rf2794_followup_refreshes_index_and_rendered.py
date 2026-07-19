"""R-F2794 — an adverse-media escalation must reach the LIST surfaces too.

THE DEFECT:

  The async adverse-media follow-up merges into the persisted report body under
  ``_REPORT_MERGE_LOCK`` and updates ``risk_classification`` (R-F2780 escalation)
  plus ``decision_readiness``/``bottom_line`` (R-F2786). It never touched two
  other persisted artefacts that user-facing surfaces actually read:

    1. ``body["rendered"]`` — the markdown snapshot frozen at persist time.
       ``GET /dd/report/{id}?format=markdown`` short-circuits on it, so it served
       PRE-escalation GREEN text permanently — to the aria-app report page, both
       Copy buttons, and the fine-tune capture in learning/training_export.py.

    2. The report INDEX row (``severity``/``risk``/``risk_classification``),
       which is what dashboard.html, the aria-app dashboard + reports pages,
       vls-chain.html and the dd-reports list rows render.

  Net effect: the brain escalates a report GREEN -> AMBER-LIGHT, and five list
  surfaces plus the markdown export keep showing GREEN. That is a false clean
  on the surfaces a customer is most likely to look at first.

CONTRACT: after a follow-up merge, no persisted artefact may still assert the
pre-escalation verdict.
"""

import asyncio

from aria_service.intel import dd_orchestrator as ddo


def test_escalation_invalidates_the_frozen_markdown_snapshot():
    """A stale `rendered` must not survive a verdict change."""
    body = {
        "run_id": "dd_test_2794",
        "risk_classification": "GREEN",
        "bottom_line": "🟢 GREEN — Acme passes baseline due diligence.",
        "rendered": "# DD Report\n*GREEN* — Acme passes baseline due diligence.\n",
        "identity": {"entity_name": "Acme Ltd"},
        "compliance": {}, "network": {}, "adverse_media": {},
    }

    ddo._invalidate_stale_report_render(body, reason="adverse_media_followup")

    assert not body.get("rendered"), (
        "the pre-escalation markdown snapshot must not survive a verdict refresh"
    )
    assert body.get("rendered_invalidated_reason") == "adverse_media_followup", (
        "invalidation must be traceable, not a silent delete"
    )


def test_render_invalidation_is_a_noop_when_there_is_no_snapshot():
    body = {"run_id": "dd_x", "risk_classification": "GREEN"}
    ddo._invalidate_stale_report_render(body, reason="test")
    assert "rendered" not in body or not body["rendered"]


def test_index_row_follows_the_escalated_verdict():
    """The list surfaces read the index row — it must track the escalation."""
    index = [
        {"run_id": "dd_a", "severity": "GREEN", "risk": "GREEN", "risk_classification": "GREEN"},
        {"run_id": "dd_b", "severity": "RED", "risk": "RED", "risk_classification": "RED"},
    ]

    mutated = ddo._apply_risk_to_index_rows(list(index), "dd_a", "AMBER-LIGHT")

    row_a = next(r for r in mutated if r["run_id"] == "dd_a")
    assert row_a["severity"] == "AMBER-LIGHT"
    assert row_a["risk"] == "AMBER-LIGHT"
    assert row_a["risk_classification"] == "AMBER-LIGHT", (
        "all three keys are written at persist time and all three are read by "
        "different renderers — every one must follow the escalation"
    )

    row_b = next(r for r in mutated if r["run_id"] == "dd_b")
    assert row_b["risk_classification"] == "RED", "unrelated rows must not be touched"


def test_index_update_is_safe_on_missing_or_malformed_rows():
    """Index maintenance must never break a DD run (cf. _mutate_report_index)."""
    for index in [[], [{"no_run_id": 1}], ["notadict"], [None]]:
        out = ddo._apply_risk_to_index_rows(list(index), "dd_missing", "AMBER-LIGHT")
        assert isinstance(out, list), "must always return a usable index"


def test_followup_merge_refreshes_both_artefacts_end_to_end(monkeypatch):
    """Drive the real merge helper and assert neither artefact stays stale."""
    body = {
        "run_id": "dd_e2e_2794",
        "risk_classification": "GREEN",
        "bottom_line": "🟢 GREEN — Acme passes baseline due diligence.",
        "rendered": "# frozen GREEN snapshot",
        "identity": {"entity_name": "Acme Ltd"},
        "compliance": {}, "network": {}, "adverse_media": {},
    }
    captured = {}

    async def fake_mutate(mutator, *, persist=True):
        captured["index"] = mutator([
            {"run_id": "dd_e2e_2794", "severity": "GREEN",
             "risk": "GREEN", "risk_classification": "GREEN"},
        ])
        return captured["index"]

    monkeypatch.setattr(ddo, "_mutate_report_index", fake_mutate)

    # Simulate the escalation the follow-up applies, then the sync step.
    body["risk_classification"] = "AMBER-LIGHT"
    asyncio.run(ddo._sync_report_surfaces_after_followup(body, "dd_e2e_2794"))

    assert not body.get("rendered"), "frozen GREEN markdown must be invalidated"
    row = captured["index"][0]
    assert row["risk_classification"] == "AMBER-LIGHT", "index row must follow the body"
    assert row["severity"] == "AMBER-LIGHT"
