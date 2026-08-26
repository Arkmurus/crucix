"""R-F4371 (C-316) — the coder tool-use corpus must teach what the live
failures showed she does not know, and must never teach a fabrication.

Measured across `data/training/*.jsonl` on 2026-08-26: 5,310 tool-use
trajectories, 7 distinct tool names (all due-diligence), **zero** using any
coder tool, and 76.1% with a single call-turn. She had never been trained to
call `read_file`, `edit_file`, `run`, `list_dir` or `grep` even once, which is
why the coder CLI produced "I cannot execute or modify files", a `recursive`
argument `list_dir` does not accept, and the invented path
``C:\\path\\to\\file.txt``.

The builder's central property is inherited from R-F3366 and is the one worth
guarding hardest: **every tool result is produced by really executing
`aria_cli.tools.Toolbox`**, so nothing in the corpus is a model's idea of what
a tool would have said. The first version of the builder violated this by
accident — it read `getattr(result, "content", str(result))`, and since
`ToolResult` has no `.content`, every tool message silently became a Python
dataclass repr. It raised nothing and looked fine. Hence
``test_tool_output_is_the_real_toolbox_output``.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import build_coder_tooluse_corpus as B  # noqa: E402


@pytest.fixture(scope="module")
def rows():
    """Two variants per family — really executed, really verified."""
    return B.build_rows(variants=2)


# ── the capability: it produces what was missing ────────────────────────────

def test_the_corpus_uses_the_coder_tools_that_had_zero_coverage(rows):
    """THE DEFECT. Zero of 5,310 existing trajectories called any of these."""
    used = {tc["function"]["name"]
            for r in rows for m in r["messages"]
            for tc in (m.get("tool_calls") or [])}
    assert {"read_file", "edit_file", "run", "list_dir", "grep"} <= used, \
        f"coder tools still uncovered: {used}"


def test_every_trajectory_acts_after_a_tool_result(rows):
    """THE BEHAVIOUR. 76.1% of the existing corpus stops after one call, which
    is exactly what she does live."""
    for r in rows:
        msgs = r["messages"]
        acted = any(msgs[i]["role"] == "tool"
                    and msgs[i + 1]["role"] == "assistant"
                    and msgs[i + 1].get("tool_calls")
                    for i in range(len(msgs) - 1))
        assert acted, "a trajectory never calls a tool after a tool result"


def test_trajectories_are_multi_step(rows):
    turns = [len([m for m in r["messages"] if m.get("tool_calls")]) for r in rows]
    assert min(turns) >= 2
    assert sum(turns) / len(turns) >= 2.5, \
        f"mean depth {sum(turns) / len(turns):.1f} — too shallow to shift a 76% skew"


# ── the constraint: nothing here may be invented ────────────────────────────

def test_tool_output_is_the_real_toolbox_output(tmp_path):
    """THE FABRICATION GUARD, and it has already caught one live bug.

    A `getattr(result, "content", str(result))` fallback silently wrote
    `ToolResult(output='...', is_error=False, mutation='')` — a dataclass repr —
    into every tool message. It raised nothing. Assert the recorded text IS
    what the Toolbox returned, byte for byte."""
    from aria_cli.safety import WriteGuard
    from aria_cli.tools import Toolbox

    (tmp_path / "x.txt").write_text("marker-9f31\n", encoding="utf-8")
    box = Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False))
    t = B.Trajectory(box, tmp_path, "read x.txt")
    recorded = t.call("read_file", path="x.txt")
    expected = box.read_file(path="x.txt").output

    assert recorded == expected
    assert "ToolResult(" not in recorded, "a dataclass repr reached the corpus"
    assert "marker-9f31" in recorded


def test_a_trajectory_whose_outcome_did_not_happen_is_refused(monkeypatch):
    """The builder must RAISE, not skip. A builder that quietly drops what it
    could not verify reports a smaller corpus and no reason — which is how a
    broken family survives unnoticed."""
    def _liar(box, root, v=0):
        t = B.Trajectory(box, root, "do a thing")
        t.call("list_dir", path=".")
        t.call("list_dir", path=".")
        return t.answer("I changed the file."), lambda: False

    monkeypatch.setattr(B, "TASKS", [_liar])
    with pytest.raises(RuntimeError, match="did NOT end in the intended state"):
        B.build_rows(variants=1)


# ── the schema: she must not learn to invent arguments ──────────────────────

def test_no_call_uses_an_undeclared_argument(rows):
    """`list_dir(recursive=True)` cost a whole live turn. One such row in the
    corpus teaches that inventing parameters is allowed."""
    for r in rows:
        for m in r["messages"]:
            for tc in (m.get("tool_calls") or []):
                name = tc["function"]["name"]
                args = json.loads(tc["function"]["arguments"])
                undeclared = set(args) - B.TOOL_PARAMS[name]
                assert not undeclared, f"{name}: {sorted(undeclared)}"


def test_the_builder_refuses_an_undeclared_argument_at_source(tmp_path):
    """The guard must fire where the trajectory is WRITTEN, not only where it
    is validated — otherwise a new task family can introduce one silently."""
    from aria_cli.safety import WriteGuard
    from aria_cli.tools import Toolbox

    box = Toolbox(root=tmp_path, guard=WriteGuard(self_mode=False))
    t = B.Trajectory(box, tmp_path, "list things")
    with pytest.raises(ValueError, match="undeclared argument"):
        t.call("list_dir", path=".", recursive=True)


def test_every_tool_call_id_is_mistral_legal(rows):
    """vLLM's Mistral template rejects any id that is not exactly 9
    alphanumeric chars, so a corpus carrying others trains an unusable shape."""
    for r in rows:
        for m in r["messages"]:
            for tc in (m.get("tool_calls") or []):
                cid = tc["id"]
                assert len(cid) == 9 and cid.isalnum(), cid


# ── the refusal she must unlearn ────────────────────────────────────────────

def test_no_assistant_turn_refuses_capability(rows):
    """"I cannot execute or modify files. You must manually edit the calc.py
    file" is the single most damaging thing she says, and it came from the base
    model. It must not appear anywhere in her training."""
    for r in rows:
        for m in r["messages"]:
            if m.get("role") != "assistant":
                continue
            low = (m.get("content") or "").lower()
            for phrase in B.BANNED:
                assert phrase not in low, f"corpus teaches a refusal: {phrase!r}"


def test_validate_row_rejects_a_refusal():
    """The validator must be able to FAIL, or it certifies nothing."""
    bad = {"messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "abcdefghi", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "x"}'}}]},
        {"role": "tool", "tool_call_id": "abcdefghi", "content": "x"},
        {"role": "assistant", "content": "I cannot modify files."},
    ]}
    problems = B.validate_row(bad)
    assert any("refuses capability" in p for p in problems), problems


def test_validate_row_rejects_a_single_call_trajectory():
    """The 76% single-call skew is the defect; the validator must not pass one."""
    one = {"messages": [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "abcdefghi", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path": "x"}'}}]},
        {"role": "tool", "tool_call_id": "abcdefghi", "content": "x"},
        {"role": "assistant", "content": "Done."},
    ]}
    problems = B.validate_row(one)
    assert any("call-turn" in p for p in problems), problems


# ── governance ──────────────────────────────────────────────────────────────

def test_the_builder_never_reads_the_operators_repo():
    """Every trajectory runs in a throwaway sandbox. Baking the operator's
    source — or a secret in it — into weights is not a corpus builder's call."""
    src = pathlib.Path(B.__file__).read_text(encoding="utf-8")
    code = [ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#") and '"""' not in ln]
    joined = "\n".join(code)
    assert "ROOT /" not in joined, "the builder reaches into the real repo"
    assert "mkdtemp" in joined, "sandboxing is how this stays safe"


def test_variants_differ_so_she_learns_the_shape_not_one_filename(rows):
    """Six families repeated verbatim would teach six file names. The FAMILY is
    the lesson; the identifiers must vary."""
    users = [m["content"] for r in rows for m in r["messages"]
             if m["role"] == "user"]
    assert len(set(users)) == len(users), "two variants produced identical tasks"
