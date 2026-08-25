"""R-F4325 / C-273 — the CLI hands a 7B model a tool set it cannot use.

THE SYMPTOM the operator saw, running `aria` against her own model:

    ARIA   You are you are you are you are you are you are you are you are
    you are you are ... ARIA — the Arkmurus Research Intelligence Agent —
    operating as an autonomous autonomous agent on the operator's autonomous
    coding agent on the operator's autonomous coding agent...

MEASURED LIVE 2026-08-25 against the served sovereign endpoint
(mistralai/Mistral-7B-Instruct-v0.3, max_model_len 32768, served as
aria-llm-v0.4-dpo). Five representative CLI tasks, scored on whether the
CORRECT tool was called — not merely whether a call happened, which is a
different and much easier question:

    tool set                                        called   CORRECT
    read_file,list_dir,grep,run,edit_file            5/6      5/6
    read_file,list_dir,glob,grep,run                 4/6      4/6
    + glob      (6 tools)                            -        3/6
    + write_file(6 tools)                            -        2/6
    ALL 31 tools (what the CLI actually sends)       1/5      0/5

At 31 tools she gets NOTHING right. The failure is graded, not a cliff, and
it tracks the size of the tools[] block:

     3 tools ( 1,448 ch) -> 5/5 calls    (but 1/5 CORRECT — she answered
                                          every task with read_file; a
                                          presence-only assertion would have
                                          scored this a perfect pass)
     5 tools ( 1,970 ch) -> 4/5
     8 tools ( 4,885 ch) -> 2/5
    20 tools ( 9,215 ch) -> 1/5
    31 tools (15,486 ch) -> 1/5, 0/5 correct

The system prompt compounds it. With a SHORT prompt she calls tools happily
at 31; with the CLI's real 20,885-char prompt she never calls one at any
length (200 ch through 20,885 ch all fail), and at 1 tool she emitted the
literal string "[TOOL_CALLS]" as TEXT — Mistral's control token arriving in
the content channel. At 4,000 ch she wrote the call as prose JSON,
``[{"name": "read_dir", "arguments": {"path": "."}}]``: she is trying to
call and cannot reach the channel.

WHY THIS IS THE RIGHT FIX AND NOT A CAPABILITY CUT. The CLI today gives her
31 tools and gets 0/5 correct — every tool is nominally available and none
of them work. Five tools at 5/6 is strictly more capability than 31 at 0/5.

THE SAME LESSON THE SERVER ALREADY LEARNED. aria_engine.py:719 (R-F1337):
"serve the compact prompt when a small sovereign model (ARIA-LLM, 7B-class)
is wired as chain primary. Default: ON whenever ARIA_LLM_URL is set." The
CLI never had that mechanism. This adds the tool-set half of it.

SCOPED TO THE SOVEREIGN. DeepSeek and Anthropic handle 31 tools fine, and
narrowing them would be a real capability loss for no reason. The narrowing
applies only to the provider whose limit was measured.
"""
from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import agent as cli_agent  # noqa: E402
from aria_cli import prompt as cli_prompt  # noqa: E402


def _names(schemas) -> list[str]:
    return [s["function"]["name"] for s in schemas]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for v in ("ARIA_CLI_SOVEREIGN_TOOLS", "ARIA_CODER_LLM_PROVIDER",
              "ARIA_LLM_URL", "ARIA_LLM_MODEL", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(v, raising=False)


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_sovereign_gets_a_tool_set_it_can_actually_use():
    """THE LIVE SYMPTOM. 31 tools measured 0/5 correct against her model."""
    schemas = cli_agent.tool_schemas_for_provider("aria-llm")
    assert len(schemas) <= cli_agent.SOVEREIGN_MAX_TOOLS, (
        f"the sovereign is being handed {len(schemas)} tools; measured live, "
        f"31 tools yields 0/5 correct calls and degenerate output"
    )
    assert schemas, "narrowing must not empty the tool set"


def test_the_narrowed_set_keeps_the_measured_winners():
    """The set is not arbitrary — it is the configuration that scored 5/6.
    Dropping read/list/grep/run leaves her unable to inspect anything, and
    dropping edit_file makes her a reader rather than a coding agent."""
    names = set(_names(cli_agent.tool_schemas_for_provider("aria-llm")))
    for required in ("read_file", "list_dir", "grep", "run", "edit_file"):
        assert required in names, (
            f"{required!r} missing from the sovereign tool set; the measured "
            f"5/6 configuration was read_file,list_dir,grep,run,edit_file"
        )


def test_glob_and_write_file_are_not_quietly_added_back():
    """Both were MEASURED to make her worse: adding glob scored 3/6 and
    write_file 2/6, against 5/6 without them. They look harmless, which is
    exactly why this pins them."""
    names = set(_names(cli_agent.tool_schemas_for_provider("aria-llm")))
    assert "write_file" not in names, (
        "write_file re-added: measured 2/6 vs 5/6 without it. edit_file "
        "covers the mutating case at a fraction of the schema size."
    )


# -- the other providers must not be narrowed ---------------------------

def test_deepseek_still_gets_every_tool():
    """A big-window provider handles 31 tools fine; narrowing it would be a
    capability loss bought for nothing."""
    full = cli_agent.tool_schemas_for_provider("deepseek")
    assert len(full) > cli_agent.SOVEREIGN_MAX_TOOLS, (
        f"deepseek was narrowed to {len(full)} tools — the measurement that "
        f"justified narrowing was taken against the sovereign only"
    )


def test_an_unknown_provider_is_not_narrowed():
    """Fail OPEN. Narrowing is a deliberate concession to one measured model;
    a provider we know nothing about must not silently inherit it."""
    assert len(cli_agent.tool_schemas_for_provider("some-new-vendor")) == \
        len(cli_agent.tool_schemas_for_provider("deepseek"))


def test_the_full_set_is_still_what_dispatch_can_serve():
    """Narrowing the ADVERTISED set must not narrow what the CLI can execute —
    a sub-agent or a later turn may still dispatch any tool by name."""
    narrowed = set(_names(cli_agent.tool_schemas_for_provider("aria-llm")))
    full = set(_names(cli_agent.tool_schemas_for_provider("deepseek")))
    assert narrowed < full
    assert cli_agent._all_tool_names() >= full, (
        "the executable tool set shrank along with the advertised one"
    )


# -- the operator keeps the lever ---------------------------------------

def test_an_explicit_override_wins():
    """Deriving a default is not the same as removing the control — and when
    a stronger checkpoint lands, widening it must not need a code change."""
    os.environ["ARIA_CLI_SOVEREIGN_TOOLS"] = "read_file,run"
    try:
        names = _names(cli_agent.tool_schemas_for_provider("aria-llm"))
        assert names == ["read_file", "run"], names
    finally:
        os.environ.pop("ARIA_CLI_SOVEREIGN_TOOLS", None)


def test_an_override_naming_nothing_real_falls_back_rather_than_emptying():
    """An empty tools[] would make her a chatbot silently. A typo in an env
    var must not disarm the agent."""
    os.environ["ARIA_CLI_SOVEREIGN_TOOLS"] = "nonexistent_tool,also_fake"
    try:
        names = _names(cli_agent.tool_schemas_for_provider("aria-llm"))
        assert names, "override of unknown names emptied the tool set"
        assert "read_file" in names
    finally:
        os.environ.pop("ARIA_CLI_SOVEREIGN_TOOLS", None)


def test_the_agent_actually_uses_the_narrowed_set(monkeypatch):
    """THE WIRING. A selector nothing calls is the defect this repo keeps
    finding (§1, 'certified by an absence'). Assert the Agent's own
    _all_schemas honours it."""
    monkeypatch.setenv("ARIA_CODER_LLM_PROVIDER", "aria-llm")
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")

    src = (ROOT / "aria_cli/agent.py").read_text(encoding="utf-8", errors="replace")
    i = src.index("self._all_schemas")
    line = src[i:src.index("\n", i)]
    assert "tool_schemas_for_provider" in line, (
        f"Agent._all_schemas does not go through the selector: {line.strip()!r}"
    )


# -- the compact prompt (the other half; neither works alone) -----------

def test_the_compact_prompt_is_small_enough_to_act_under():
    """MEASURED: full prompt 20,885 ch -> 0/5 correct tool calls; 344 ch -> 4/5.
    A prompt that drifts back up is the defect returning."""
    sp = cli_prompt.build_compact_system_prompt(root=pathlib.Path(r"C:\Code\Aria"))
    assert sp.strip(), "compact prompt is empty"
    assert len(sp) <= 1200, (
        f"compact prompt has grown to {len(sp)} chars; measured, tool-calling "
        f"collapses well before the full 20,885-char prompt"
    )


def test_the_compact_prompt_still_carries_the_operating_floor():
    """The rules cost one task (5/5 -> 4/5) and are kept deliberately. An agent
    that edits files and runs commands without them is the worse failure."""
    sp = cli_prompt.build_compact_system_prompt(
        root=pathlib.Path(r"C:\Code\Aria")).lower()
    for rule in ("r-number", "root cause", "never delete"):
        assert rule in sp, f"the compact prompt dropped {rule!r}"


def test_the_compact_prompt_names_the_working_directory():
    """She acts on files; a prompt that does not say where she is invites paths
    relative to nothing."""
    root = pathlib.Path(r"C:\Code\Aria")
    assert str(root) in cli_prompt.build_compact_system_prompt(root=root)


def test_the_compact_prompt_tells_her_to_ACT_not_describe():
    """The observed failure mode once degeneration stopped was NARRATION — she
    printed the command instead of calling it."""
    sp = cli_prompt.build_compact_system_prompt(
        root=pathlib.Path(r"C:\Code\Aria")).lower()
    assert "tool" in sp and ("do not describe" in sp or "not describe" in sp)


def test_compact_is_on_for_the_sovereign_and_off_for_deepseek(monkeypatch):
    """Scoped to the provider whose ceiling was measured."""
    monkeypatch.delenv("ARIA_CLI_COMPACT_PROMPT", raising=False)
    assert cli_prompt.compact_prompt_active("aria-llm") is True
    assert cli_prompt.compact_prompt_active("deepseek") is False
    assert cli_prompt.compact_prompt_active("some-new-vendor") is False


def test_the_operator_can_force_it_either_way(monkeypatch):
    """Same lever shape as the server's ARIA_LLM_COMPACT_PROMPT, so an operator
    who knows one surface finds the same control on the other."""
    monkeypatch.setenv("ARIA_CLI_COMPACT_PROMPT", "0")
    assert cli_prompt.compact_prompt_active("aria-llm") is False
    monkeypatch.setenv("ARIA_CLI_COMPACT_PROMPT", "1")
    assert cli_prompt.compact_prompt_active("deepseek") is True


def test_the_cli_actually_selects_it():
    """THE WIRING. A compact prompt nothing calls is the §1 'certified by an
    absence' shape — and this fix is worthless unwired."""
    src = (ROOT / "aria_cli/cli.py").read_text(encoding="utf-8", errors="replace")
    assert "compact_prompt_active(" in src, "cli.py never asks whether to compact"
    assert "build_compact_system_prompt(" in src, "cli.py never builds it"
