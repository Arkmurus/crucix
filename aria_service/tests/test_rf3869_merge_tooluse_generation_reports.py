import copy

import pytest

from scripts.train.eval_tooluse import build_report, score_one
from scripts.train.merge_tooluse_generation_reports import merge_reports
from scripts.train.build_positive_curve_assets import deduplicate_preferences


def _trace(subject: str) -> dict:
    return {
        "subject": subject,
        "label": "tooluse_person",
        "messages": [
            {"role": "user", "content": f"Screen {subject}."},
            {"role": "assistant", "content": "No conclusion without identifiers."},
        ],
        "tool_trace": [{"tool": "sanctions_screen_person", "args": {"name": subject},
                        "result": {"status": "REVIEW_REQUIRED", "sanctions": {"matched": True}}}],
    }


def _report(trace: dict, answer: str) -> dict:
    row = score_one(trace, answer)
    report = build_report([row])
    report.update({"rows": [row], "complete": True})
    return report


def test_merge_reorders_sources_and_rescores_real_answers() -> None:
    first, second = _trace("Alpha Person"), _trace("Beta Person")
    merged = merge_reports(
        [second, first],
        [([first], _report(first, "No conclusion without identifiers.")),
         ([second], _report(second, "Beta Person is sanctioned."))],
    )
    assert [row["subject"] for row in merged["rows"]] == ["Beta Person", "Alpha Person"]
    assert merged["complete"] is True
    assert merged["total"] == 2


def test_merge_refuses_incomplete_and_missing_sources() -> None:
    trace = _trace("Alpha Person")
    incomplete = _report(trace, "No conclusion without identifiers.")
    incomplete["complete"] = False
    with pytest.raises(ValueError, match="incomplete"):
        merge_reports([trace], [([trace], incomplete)])
    with pytest.raises(ValueError, match="without generations"):
        merge_reports([copy.deepcopy(trace)], [])


def test_mixed_retention_keeps_new_subject_and_deduplicates_old_subject() -> None:
    retained = {"label": "tooluse_person", "subject": "Alpha Person", "chosen": "old"}
    repeated = {"label": "tooluse_person", "subject": "alpha person", "chosen": "new"}
    novel = {"label": "tooluse_person", "subject": "Beta Person", "chosen": "new"}
    assert deduplicate_preferences([retained, repeated, novel]) == [retained, novel]
