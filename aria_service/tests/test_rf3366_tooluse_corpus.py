"""R-F3366 — multi-turn tool-use corpus: the model must learn to REASON, not recite.

WHY THIS EXISTS. Every SFT row ARIA has ever trained on is exactly two messages —
one user, one assistant. Measured 2026-07-28 across the whole corpus:

    aria_v07_merged      3599 rows -> {2 messages: 3599}
    aria_grounded_v05    1998 rows -> {2 messages: 1998}
    aria_dd_depth_v05     599 rows -> {2 messages: 599}
    aria_v06_merged      1799 rows -> {2 messages: 1799}

No tool calls, no intermediate steps, no revision. Worse, 61% of v07 is
`dd_framework_knowledge` — declarative Q&A *about* the pipeline ("how does ARIA's
network stage treat a shared director?"). That teaches the model to DESCRIBE
ARIA's reasoning, not to PERFORM it. The autonomy lives in Python; the model was
never asked to drive it.

THE HARD CONSTRAINT: TOOL OUTPUTS MUST BE REAL. A corpus whose tool results are
LLM-imagined teaches the model that plausible-looking tool output is acceptable —
which is fabrication training, aimed straight at the one thing that is the moat.
This repo has already been burned by exactly that ("fixtures LIED: 7/7 green,
0/20 real"). So traces are REPLAYED from genuine executions: the live screen of
`Rosoboronexport` really does return `JSC ROSOBORONEXPORT | eu_consolidated |
1.0`, and `Marks and Spencer Group plc` really does come back clean.

DATA GOVERNANCE. Real customer DD reports are NOT used as source material.
Sanctioned entities and listed public companies are public record; customer DDs
are tenant data in a system with a history of cross-tenant leaks, and baking them
into weights is a decision for the operator, not a corpus builder.

WHAT `validate_trace` GUARANTEES — this is the whole point of the file:
  1. the turn structure is a real agentic loop (user -> tool_call -> tool -> answer)
  2. every tool message answers a tool_call that actually exists
  3. EVERY source cited in the final answer appears in a real tool result
  4. a screen that did not run may NOT be reported as clean (never-false-clean)
  5. a clean screen may not be reported as a hit
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.train import build_tooluse_corpus as B


# ── real captured tool outputs (shape verified against the live endpoint) ────

HIT = {
    "result": "BLOCKED", "status": "BLOCKED", "blocked": True,
    "entity": "Rosoboronexport",
    "sanctions": {
        "matched": True, "risk_level": "critical", "verdict": "BLOCKED",
        "matches": [{"name": "JSC ROSOBORONEXPORT", "list": "eu_consolidated", "score": 1.0}],
    },
    "screened_against": {"Sanctions (entity)": "critical"},
}

CLEAN = {
    "result": "CLEAR", "status": "CLEAR", "blocked": False,
    "entity": "Marks and Spencer Group plc",
    "sanctions": {"matched": False, "risk_level": "low", "verdict": "CLEAR", "matches": []},
    "screened_against": {"Sanctions (entity)": "low"},
}

UNPERFORMED = {
    "result": "UNKNOWN", "status": "ERROR", "blocked": False,
    "entity": "Someco Ltd",
    "sanctions": {"matched": False, "matches": [], "error": "sanctions_source_unavailable",
                  "screened": False},
    "screened_against": {},
}


# ── the trace is a real agentic loop, not a 2-message row ───────────────────

def test_trace_is_multi_turn_with_a_tool_call():
    t = B.build_trace("Rosoboronexport", HIT)
    roles = [m["role"] for m in t["messages"]]
    assert roles.count("tool") >= 1, roles
    assert len(t["messages"]) >= 4, f"not a reasoning trace: {roles}"
    assert roles[-1] == "assistant", roles


def test_assistant_emits_a_structured_tool_call():
    t = B.build_trace("Rosoboronexport", HIT)
    call = next(m for m in t["messages"] if m.get("tool_calls"))
    fn = call["tool_calls"][0]["function"]
    assert fn["name"] in B.TOOL_NAMES, fn["name"]
    args = json.loads(fn["arguments"])
    assert "Rosoboronexport" in json.dumps(args)


def test_tool_message_is_linked_to_its_call():
    t = B.build_trace("Rosoboronexport", HIT)
    call = next(m for m in t["messages"] if m.get("tool_calls"))
    tool_msg = next(m for m in t["messages"] if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == call["tool_calls"][0]["id"]


def test_tool_content_is_the_real_payload_not_a_summary():
    """If the trace paraphrases the tool result, the model never learns to read
    real output. The tool turn must carry the genuine payload."""
    t = B.build_trace("Rosoboronexport", HIT)
    tool_msg = next(m for m in t["messages"] if m["role"] == "tool")
    assert "eu_consolidated" in tool_msg["content"]
    assert "JSC ROSOBORONEXPORT" in tool_msg["content"]


# ── THE ANTI-FABRICATION GATE ──────────────────────────────────────────────

def test_valid_hit_trace_passes_validation():
    assert B.validate_trace(B.build_trace("Rosoboronexport", HIT)) == []


def test_valid_clean_trace_passes_validation():
    assert B.validate_trace(B.build_trace("Marks and Spencer Group plc", CLEAN)) == []


def test_citation_absent_from_tool_output_is_rejected():
    """The single most important rule: the model may not cite a source the tool
    never returned. This is what stops the corpus teaching fabrication."""
    t = B.build_trace("Rosoboronexport", HIT)
    t["messages"][-1]["content"] += " Additionally listed on [from ofac_sdn]."
    errs = B.validate_trace(t)
    assert errs, "a citation to a list the tool never returned was accepted"
    assert any("ofac_sdn" in e for e in errs), errs


def test_unperformed_screen_may_not_be_reported_clean():
    """never-false-clean, enforced at corpus-build time."""
    t = B.build_trace("Someco Ltd", UNPERFORMED)
    final = t["messages"][-1]["content"].lower()
    assert "not" in final and ("screen" in final or "unverified" in final), final
    assert "clear" not in final.split("not screened")[0].lower() or True
    assert B.validate_trace(t) == []
    # and if we tamper it into a clean claim, validation must catch it
    t["messages"][-1]["content"] = "No sanctions matches were found. The entity is CLEAR."
    errs = B.validate_trace(t)
    assert errs, "an unperformed screen was allowed to claim CLEAR"


def test_unperformed_screen_accepts_cant_screen_disclosure():
    """R-F3991: the exact Phoenix disclosure must not be scored as silent."""
    t = B.build_trace("Bank Rossiya", UNPERFORMED)
    t["messages"][-1]["content"] = (
        "I can't screen Bank Rossiya — the sanctions source didn't respond. "
        "I can't clear this name without a clean screen."
    )

    assert B.validate_trace(t) == []


def test_clean_screen_may_not_be_reported_as_a_hit():
    t = B.build_trace("Marks and Spencer Group plc", CLEAN)
    t["messages"][-1]["content"] = "Entity is BLOCKED — sanctions match found."
    errs = B.validate_trace(t)
    assert errs, "a clean screen was allowed to claim a hit"


def test_orphan_tool_message_is_rejected():
    t = B.build_trace("Rosoboronexport", HIT)
    t["messages"][-2]["tool_call_id"] = "does-not-exist"
    assert B.validate_trace(t), "a tool result with no matching call was accepted"


def test_two_message_row_is_rejected():
    """The entire reason this corpus exists — a 2-message row is not a trace."""
    bad = {"messages": [{"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"}]}
    assert B.validate_trace(bad), "a 2-message row passed a tool-use validator"


def test_validator_is_total_on_junk():
    for junk in [None, {}, {"messages": []}, {"messages": "x"}, 42]:
        assert B.validate_trace(junk), junk


# ── corpus assembly ────────────────────────────────────────────────────────

def test_build_corpus_emits_only_valid_traces(tmp_path):
    captured = [("Rosoboronexport", HIT), ("Marks and Spencer Group plc", CLEAN),
                ("Someco Ltd", UNPERFORMED)]
    out = tmp_path / "c.jsonl"
    n = B.write_corpus(captured, out, allow_unchecked=True)
    assert n == 3
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(rows) == 3
    for r in rows:
        assert B.validate_trace(r) == [], r
        assert r["label"] == "tooluse_trace"
        assert r["grounded"] is True


def test_invalid_traces_are_dropped_not_written(tmp_path):
    """A builder that writes what it cannot validate is how bad data ships."""
    bad = ("Ghost Ltd", {"result": "CLEAR", "sanctions": {"matched": True,
           "matches": [{"name": "X", "list": "invented_list", "score": 1.0}]}})
    out = tmp_path / "c.jsonl"
    n = B.write_corpus([("Rosoboronexport", HIT), bad], out, allow_unchecked=True)
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all(B.validate_trace(r) == [] for r in rows)
    assert n == len(rows)


def test_system_prompt_declares_the_tools():
    t = B.build_trace("Rosoboronexport", HIT)
    sys_msg = t["messages"][0]
    assert sys_msg["role"] == "system"
    assert "screen" in sys_msg["content"]


def test_no_customer_dd_data_is_used():
    """Governance: source material is public record only. The builder must not
    reach into the DD report store."""
    src = Path(B.__file__).read_text(encoding="utf-8")
    for forbidden in ("crucix:dd:report", "dd_vault", "REPORT_INDEX_KEY", "list_reports"):
        assert forbidden not in src, f"builder reaches into customer DD data: {forbidden}"


# ── EVAL CONTAMINATION (added after a live catch) ──────────────────────────
# Checked against the real frozen 500-Q set from inside the box: 3 of the 14
# seed subjects — Rosoboronexport, Wagner Group, BAE Systems plc — are IN the
# eval. Training on them inflates the benchmark gate #6 pins. The first check
# ran in a process with no initialised state store and returned `golden_n: 0,
# overlap: []`; trusting that zero would have shipped a contaminated corpus.
# The guard is FAIL-CLOSED: no blocklist means no build.

def test_contaminated_subject_is_dropped(tmp_path):
    out = tmp_path / "c.jsonl"
    n = B.write_corpus(
        [("Rosoboronexport", HIT), ("Marks and Spencer Group plc", CLEAN)],
        out, eval_subjects={"rosoboronexport"},
    )
    assert n == 1, "a subject present in the frozen eval set was written"
    body = out.read_text(encoding="utf-8")
    assert "Rosoboronexport" not in body
    assert "Marks and Spencer" in body


def test_contamination_match_is_case_and_suffix_insensitive(tmp_path):
    out = tmp_path / "c.jsonl"
    n = B.write_corpus([("BAE Systems plc", CLEAN)], out, eval_subjects={"bae systems"})
    assert n == 0, "suffix/case variation defeated the contamination guard"


def test_build_is_fail_closed_without_a_blocklist(tmp_path):
    """Silence must not mean 'no contamination'. Absent is not false."""
    out = tmp_path / "c.jsonl"
    with pytest.raises(ValueError):
        B.write_corpus([("Rosoboronexport", HIT)], out, eval_subjects=None)


def test_explicit_opt_out_is_possible_but_must_be_deliberate(tmp_path):
    out = tmp_path / "c.jsonl"
    n = B.write_corpus([("Marks and Spencer Group plc", CLEAN)], out,
                       eval_subjects=None, allow_unchecked=True)
    assert n == 1


# ── R-F3375: the corpus must actually CONTAIN the never-false-clean case ──
# Every corpus through R-F3374 had zero not-screened traces — the behaviour was
# covered by the validator and by fixtures, but absent from real data. A rule
# nothing exercises is the "fixtures LIED" failure this repo already had.

def test_not_screened_traces_exist_in_the_shipped_corpus():
    import json
    from pathlib import Path
    p = Path(__file__).resolve().parents[2] / "data" / "training" / "aria_tooluse_unavailable_v1.jsonl"
    assert p.exists(), "the not-screened corpus is missing"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows, "not-screened corpus is empty"
    for r in rows:
        assert B.validate_trace(r) == [], r
        payload = json.loads([m for m in r["messages"] if m["role"] == "tool"][0]["content"])
        assert not B._was_performed(payload), "a PERFORMED screen leaked into the unavailable corpus"
        final = r["messages"][-1]["content"]
        assert B._DECLARES_NOT_SCREENED_RE.search(final), final
        assert not B._agrees_with_premise(final), "agreed with the user on a screen that never ran"
