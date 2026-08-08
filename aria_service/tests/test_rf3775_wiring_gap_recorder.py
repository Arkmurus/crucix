"""R-F3775 — CAPABILITY: the §21a wiring debt becomes Gaps the coder can consume.

§21e is binding: a finding the coder can implement must become a Gap, not a TODO.
66 modules do not reach a brain sink on both branches, and each fix is a local
single-file edit — the exact shape `self_coder.fix_gap` takes. This test drives the
recorder's real entry points, not helpers.

What is asserted, and why each one is a way this could be quietly useless:
  * a payload is built for every selected dark module — a filter that silently
    matches nothing would "succeed" while recording zero gaps;
  * `gap_type` is one the coder actually dispatches on;
  * `detail` NAMES the file, or the coder cannot find what to edit;
  * `source` is per-module, or de-dup (R-F903) collapses 16 gaps into 1;
  * an UNREADABLE baseline REFUSES rather than reading as "nothing is dark" — the
    absence-as-measurement defect this entire sweep was about.

Run: python -m pytest aria_service/tests/test_rf3775_wiring_gap_recorder.py -v
"""
from __future__ import annotations

import importlib.util
import json

from ._source_probe import repo_path

_SPEC = importlib.util.spec_from_file_location(
    "_rf3775_rec", repo_path("scripts/admin/record_wiring_gaps.py"))
rec = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rec)


_DARK = {
    "aria_service/metacognitive/identity.py": "missing-failure",
    "aria_service/learning/pair_builder.py": "no-wiring",
    "aria_service/intel/unrelated.py": "missing-failure",
}


def test_a_payload_is_built_for_each_selected_dark_module():
    """THE HEADLINE: selection must actually select."""
    out = rec.build_payloads(_DARK, ("metacognitive", "learning"))
    assert len(out) == 2, f"expected the 2 cognition modules, got {len(out)}"
    srcs = {p["source"] for p in out}
    assert srcs == {"wiring_audit:identity", "wiring_audit:pair_builder"}


def test_the_unrelated_subsystem_is_excluded():
    out = rec.build_payloads(_DARK, ("metacognitive",))
    assert [p["source"] for p in out] == ["wiring_audit:identity"]


def test_all_selects_everything():
    assert len(rec.build_payloads(_DARK, ("all",))) == 3


def test_gap_type_matches_the_ENUM_not_a_hand_typed_literal():
    """THE BUG THIS CAUGHT. My first draft wrote "MODULE_BUG"; the real value is
    "module_bug" (gap_detector.py:70). All 16 gaps would have been recorded with an
    unrecognised type — visible in the ledger and never dispatched. Assert against
    the vocabulary, never against my own string."""
    from aria_service.autonomous.gap_detector import GapType
    for p in rec.build_payloads(_DARK, ("all",)):
        assert p["gap_type"] == GapType.MODULE_BUG
        assert p["gap_type"] == "module_bug", "the enum value drifted"


def test_the_route_is_auto_fixable_and_needs_no_operator_approval():
    """MISSING_CAPABILITY is (False, True, False) — it would put SIXTEEN approval
    prompts in front of the operator for a one-line edit each. MODULE_BUG is
    (True, False, False) and drains through the staged pipeline."""
    from aria_service.autonomous.gap_detector import AUTONOMY_LEVEL, GapType
    auto_fixable, needs_approval, _hard = AUTONOMY_LEVEL[GapType.MODULE_BUG]
    assert auto_fixable is True
    assert needs_approval is False


def test_detail_supplies_a_REPRODUCER_so_the_gap_can_reach_gold():
    """R-F1857 rejects a MODULE_BUG with no reproducible fault: it can never become
    gold and only burns LLM budget. A wiring gap has no traceback, so the detail must
    hand over the reproducer it DOES have — the wiring audit, which already goes
    fail-on-unfixed -> pass-on-fixed."""
    d = rec.build_payloads(_DARK, ("all",))[0]["detail"]
    assert "scripts/ci/wiring_audit.py" in d
    assert "fail-on-unfixed" in d
    assert "no traceback" in d, "the coder must be told not to hunt an exception"


def test_detail_names_the_FILE_and_the_verdict():
    """Without the path, the coder has a complaint but no target."""
    out = rec.build_payloads(_DARK, ("metacognitive",))
    d = out[0]["detail"]
    assert "aria_service/metacognitive/identity.py" in d
    assert "missing-failure" in d
    assert "self-heal" in d, "the detail should say WHY darkness matters"


def test_detail_forbids_the_wrong_fix():
    """A local log satisfies a careless reading of 'wire it' and is still DARK."""
    d = rec.build_payloads(_DARK, ("all",))[0]["detail"]
    assert "local log is DARK" in d


def test_sources_are_distinct_so_dedup_does_not_collapse_the_batch():
    """R-F903 de-dups gaps; a shared source key would merge all 16 into one."""
    out = rec.build_payloads(_DARK, ("all",))
    assert len({p["source"] for p in out}) == len(out)


def test_payloads_are_sorted_for_a_stable_rerun():
    a = rec.build_payloads(_DARK, ("all",))
    b = rec.build_payloads(dict(reversed(list(_DARK.items()))), ("all",))
    assert [p["source"] for p in a] == [p["source"] for p in b]


def test_dry_run_builds_without_store_or_network(capsys, monkeypatch):
    """The real main(), driven end to end — no store handle, no socket."""
    def _boom(*a, **k):                     # any network use is a test failure
        raise AssertionError("--dry-run must not touch the network")
    monkeypatch.setattr(rec.urllib.request, "urlopen", _boom)
    code = rec.main(["--dry-run", "--subsystem", "metacognitive,learning"])
    assert code == 0
    assert "DRY-RUN" in capsys.readouterr().out


def test_an_unreadable_baseline_REFUSES_rather_than_reading_as_clean(tmp_path, monkeypatch, capsys):
    """The defect class this whole sweep was about: absence read as a measurement.

    A corrupt baseline must not yield "0 dark modules, nothing to do".
    """
    bad = tmp_path / "b.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(rec, "BASELINE", bad)
    assert rec.main(["--dry-run"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_a_missing_baseline_also_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rec, "BASELINE", tmp_path / "nope.json")
    assert rec.main(["--dry-run"]) == 2
    assert "REFUSED" in capsys.readouterr().err


def test_a_capped_batch_says_what_it_dropped(capsys, monkeypatch):
    """§21e no-silent-caps: a truncated run must not read as a full sweep."""
    monkeypatch.setattr(rec, "build_payloads",
                        lambda d, s: [{"source": f"s{i}", "detail": "d",
                                       "gap_type": "MODULE_BUG",
                                       "message_context": "m"} for i in range(5)])
    rec.main(["--dry-run", "--limit", "2"])
    out = capsys.readouterr().out
    assert "capping 5 -> 2" in out and "3 NOT recorded" in out


def test_a_partial_post_run_exits_nonzero(monkeypatch, capsys):
    """Reporting success for a partial sweep is the failure mode that matters."""
    monkeypatch.setattr(rec, "build_payloads",
                        lambda d, s: [{"source": "a", "detail": "d",
                                       "gap_type": "MODULE_BUG",
                                       "message_context": "m"} for _ in range(2)])
    calls = {"n": 0}

    def _half(base, token, payload, timeout=30.0):
        calls["n"] += 1
        return (calls["n"] == 1), "HTTP 500 boom"

    monkeypatch.setattr(rec, "_post", _half)
    assert rec.main([]) == 1
    cap = capsys.readouterr()
    assert "recorded=1 failed=1" in cap.out
    assert "FAILED" in cap.err


def test_the_live_baseline_yields_the_cognition_batch():
    """Against the REAL baseline in the tree, not a fixture."""
    dark = json.loads(repo_path("docs/wiring_audit_baseline.json")
                      .read_text(encoding="utf-8"))["known_dark"]
    out = rec.build_payloads(dark, ("metacognitive", "learning"))
    assert len(out) >= 10, f"expected the cognition wiring debt, got {len(out)}"
    assert all(p["detail"].count(".py") >= 1 for p in out)
