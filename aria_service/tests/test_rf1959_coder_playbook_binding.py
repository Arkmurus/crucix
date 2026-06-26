"""R-F1959 — bind the playbook to the autonomous coder (capability test).

AGENTS.md claims it "is injected into your system prompt automatically", but the
AUTONOMOUS self-coder (sovereign_llm.py) built its prompts with NO playbook
injected — only the interactive `aria` CLI loaded it. So the loop ran below the
bar it's supposed to hold. R-F1959 prepends CLAUDE.md + AGENTS.md to the
plan/code/edit prompts. These tests drive the REAL prompt builders and assert the
playbook now governs every autonomous fix.
"""
from aria_service.autonomous import sovereign_llm as S
from aria_service.autonomous.gap_detector import Gap, GapSeverity


def test_playbook_loads_both_documents():
    pb = S._load_playbook()
    assert pb, "playbook should load from the repo root"
    assert "AGENTS.md" in pb and "CLAUDE.md" in pb, "both binding docs must be present"


def test_preamble_marks_the_binding_frame():
    pre = S._playbook_preamble()
    assert "BINDING OPERATING PLAYBOOK" in pre
    # A few load-bearing rules the coder must be held to:
    assert "root-cause" in pre.lower()
    assert "Claude Code" in pre


def _gap():
    return Gap(
        gap_id="rf1959-test",
        gap_type="module_bug",
        severity=GapSeverity.MEDIUM,
        title="x",
        description="y",
        module="aria_service.intel.example",
    )


def test_plan_prompt_contains_playbook():
    llm = S.SovereignLLM("http://localhost:8000")
    prompt = llm._build_plan_prompt(_gap(), context="ctx")
    assert "BINDING OPERATING PLAYBOOK" in prompt
    # Playbook must come BEFORE the task framing (it is the governing frame).
    assert prompt.index("BINDING OPERATING PLAYBOOK") < prompt.index("autonomous self-coding engine")


def test_code_and_edit_prompts_contain_playbook():
    llm = S.SovereignLLM("http://localhost:8000")
    code_prompt = llm._build_code_prompt({"approach": "z"}, "print(1)", "a.py")
    edit_prompt = llm._build_edit_prompt({"approach": "z"}, "print(1)", "a.py")
    assert "BINDING OPERATING PLAYBOOK" in code_prompt
    assert "BINDING OPERATING PLAYBOOK" in edit_prompt


def test_degrades_gracefully_when_no_playbook(monkeypatch):
    # If the repo docs can't be read, the coder must fall back to inline rules,
    # never crash the prompt build.
    monkeypatch.setattr(S, "_load_playbook", lambda: "")
    assert S._playbook_preamble() == ""
    llm = S.SovereignLLM("http://localhost:8000")
    prompt = llm._build_plan_prompt(_gap(), context="ctx")
    assert "autonomous self-coding engine" in prompt  # still a valid prompt
