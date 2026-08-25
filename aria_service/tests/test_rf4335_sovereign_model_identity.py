"""R-F4335/R-F4336/R-F4337 — C-281, C-282.

C-281: the CLI served the untuned BASE model under the adapter's name and no
surface in the tree could tell. Measured live 2026-08-25: ``/v1/models``
returned TWO entries with the identical id ``aria-llm-v0.4-dpo`` (base + LoRA);
vLLM resolves base served-names first, so the adapter was never applied — while
the banner printed ``aria-llm/aria-llm-v0.4-dpo`` all session.

C-282: a turn ending "tools: 0 calls" was usually a real tool call written into
prose. R-F4329 recovers the JSON form and rightly refuses to parse prose; this
re-asks instead, so the MODEL still authors every executed call.
"""
from __future__ import annotations

import json as _json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_cli import agent as cli_agent  # noqa: E402
from aria_cli import model_identity as mi  # noqa: E402
from aria_cli.llm import LLMResponse  # noqa: E402

#: The EXACT payload measured live on 2026-08-25. If this shape ever reads as
#: healthy again, the defect is back.
LIVE_COLLISION = {"object": "list", "data": [
    {"id": "aria-llm-v0.4-dpo", "parent": None, "max_model_len": 32768,
     "root": "/root/.cache/huggingface/hub/models--mistralai--Mistral-7B-"
             "Instruct-v0.3/snapshots/c170c708c41dac9275d15a8fff4eca08d5/"},
    {"id": "aria-llm-v0.4-dpo", "parent": "aria-llm-v0.4-dpo",
     "max_model_len": None, "root": "/root/adapters/aria_llm_v0_4_dpo"},
]}
HEALTHY = {"object": "list", "data": [
    {"id": "aria-llm-base", "parent": None, "root": "/base"},
    {"id": "aria-llm-v0.4-dpo", "parent": "aria-llm-base", "root": "/adapters/x"},
]}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("ARIA_CLI_MODEL_IDENTITY_CHECK", raising=False)
    mi.reset_session_identity()
    yield
    mi.reset_session_identity()


# -- C-281 THE CAPABILITY TEST ------------------------------------------------

def test_the_live_collision_is_detected_as_a_breach():
    """THE OPERATOR'S SYMPTOM. Two models, one id -> the base answers."""
    ident = mi.evaluate_models_payload(LIVE_COLLISION, "aria-llm-v0.4-dpo")
    assert ident.state == mi.COLLISION
    assert ident.is_breach is True
    assert ident.healthy is False, (
        "a colliding inventory read as healthy — this is the defect itself"
    )
    assert ident.matches == 2


def test_the_breach_detail_names_the_remedy_not_just_the_fault():
    """A diagnosis the operator cannot act on is half a fix."""
    detail = mi.evaluate_models_payload(LIVE_COLLISION, "aria-llm-v0.4-dpo").detail
    assert "served-model-name" in detail and "lora-modules" in detail
    assert "base" in detail.lower()


def test_a_correctly_named_endpoint_is_healthy():
    """The guard must be able to PASS, or it carries no information."""
    ident = mi.evaluate_models_payload(HEALTHY, "aria-llm-v0.4-dpo")
    assert ident.state == mi.OK
    assert ident.healthy is True and ident.is_breach is False


def test_a_missing_model_is_a_breach_not_a_shrug():
    ident = mi.evaluate_models_payload(HEALTHY, "aria-llm-v9.9-nope")
    assert ident.state == mi.ABSENT
    assert ident.is_breach is True and ident.healthy is False


# -- the third state is load-bearing (§1 / R-F2639) ---------------------------

@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": "junk"}])
def test_an_unreadable_inventory_is_unknown_and_neither_healthy_nor_breach(payload):
    """``unknown`` means COULD NOT MEASURE. Collapsing it either way rebuilds
    the blindness: to ``ok`` and a dead fine-tune serves silently; to a breach
    and the warning cries wolf until nobody reads it."""
    ident = mi.evaluate_models_payload(payload, "aria-llm-v0.4-dpo")
    assert ident.state == mi.UNKNOWN
    assert ident.healthy is False
    assert ident.is_breach is False


def test_an_unreachable_endpoint_is_unknown_never_healthy():
    ident = mi.probe_model_identity(
        base_url="http://127.0.0.1:59997/v1", model="aria-llm-v0.4-dpo",
        timeout=0.5)
    assert ident.state == mi.UNKNOWN and ident.healthy is False


# -- scope: fail open, exactly like R-F4325/R-F4329 ---------------------------

def test_only_an_adapter_capable_provider_is_probed():
    assert mi.identity_check_active("aria-llm") is True
    for provider in ("deepseek", "anthropic", "ollama", "", "some-new-vendor"):
        assert mi.identity_check_active(provider) is False, (
            f"{provider!r} has no LoRA adapters; probing it could only ever "
            f"produce a false positive"
        )


def test_the_operator_can_force_it_either_way(monkeypatch):
    monkeypatch.setenv("ARIA_CLI_MODEL_IDENTITY_CHECK", "0")
    assert mi.identity_check_active("aria-llm") is False
    monkeypatch.setenv("ARIA_CLI_MODEL_IDENTITY_CHECK", "1")
    assert mi.identity_check_active("deepseek") is True


def test_a_non_probed_provider_returns_none_not_a_fabricated_ok():
    got = mi.session_model_identity(
        provider="deepseek", base_url="http://x.invalid/v1", model="m")
    assert got is None, "an unprobed provider must be 'not applicable', not 'ok'"


# -- the probe runs ONCE, and reaches the brain on BOTH branches (§21a) -------

def test_the_probe_is_memoised_across_repeated_banner_renders(monkeypatch):
    calls = []

    def _fake(*, base_url, model, api_key="", timeout=8.0):
        calls.append(model)
        return mi.ModelIdentity(state=mi.OK, model=model, matches=1)

    monkeypatch.setattr(mi, "probe_model_identity", _fake)
    for _ in range(3):
        mi.session_model_identity(provider="aria-llm",
                                  base_url="http://x/v1", model="m")
    assert len(calls) == 1, f"banner rendered 3x -> {len(calls)} HTTP probes"


@pytest.mark.parametrize("payload,expect", [
    (LIVE_COLLISION, "aria_cli_model_identity_breach"),
    (HEALTHY, "aria_cli_model_identity"),
])
def test_both_branches_reach_the_brain(monkeypatch, payload, expect):
    """§21a: success AND failure. A signal only on breach would leave silence
    ambiguous between 'verified' and 'never ran'."""
    seen = {}
    monkeypatch.setattr(
        mi, "probe_model_identity",
        lambda **kw: mi.evaluate_models_payload(payload, "aria-llm-v0.4-dpo"))
    from aria_cli import brain as brain_mod
    monkeypatch.setattr(brain_mod, "report_signal",
                        lambda **kw: seen.update(kw) or "ok")
    mi.session_model_identity(provider="aria-llm", base_url="http://x/v1",
                              model="aria-llm-v0.4-dpo", self_mode=True)
    assert seen.get("signal_type") == expect


# -- the banner must SAY it (a check nothing renders is C-281 again) ----------

def _banner_text(monkeypatch, capsys, payload):
    from aria_cli import cli as cli_mod
    monkeypatch.setattr(
        mi, "probe_model_identity",
        lambda **kw: mi.evaluate_models_payload(payload, "aria-llm-v0.4-dpo"))
    monkeypatch.setattr(cli_mod.brain_mod, "brain_enabled", lambda *a, **k: False)
    monkeypatch.setattr(cli_mod.brain_mod, "report_signal", lambda **kw: "off")
    cfg = cli_mod.LLMConfig(provider="aria-llm", model="aria-llm-v0.4-dpo",
                            base_url="http://x/v1")
    guard = cli_mod.WriteGuard(pathlib.Path.cwd())
    cli_mod._banner(cli_mod._Color(False), cfg, False, guard, pathlib.Path.cwd())
    return capsys.readouterr().out


def test_the_banner_shouts_when_the_model_is_unverified(monkeypatch, capsys):
    out = _banner_text(monkeypatch, capsys, LIVE_COLLISION)
    assert "UNVERIFIED" in out, (
        "the banner named the model without flagging that it is not the one "
        "answering — exactly what shipped on 2026-08-25"
    )
    assert "served-model-name" in out, "the remedy must reach the operator"


def test_the_banner_confirms_a_verified_model(monkeypatch, capsys):
    out = _banner_text(monkeypatch, capsys, HEALTHY)
    assert "verified" in out and "UNVERIFIED" not in out


# -- C-282 the narrated tool call ---------------------------------------------

OFFERED = ["read_file", "list_dir", "grep", "run", "edit_file"]
NARRATION = (
    "To perform a comprehensive log review I will use the `grep` function.\n"
    "```python\n"
    'results = grep(pattern="(Error|Warning)", output_mode="content")\n'
    "```\n"
)


def test_the_operators_exact_narration_is_recognised():
    assert cli_agent.looks_like_narrated_tool_call(NARRATION, OFFERED) == "grep"


@pytest.mark.parametrize("body", [
    "",
    "Here is a summary of what the logs show.",
    "I could grep for errors first, shall I?",
    "The run completed and the list_dir output was long.",
])
def test_it_does_not_fire_on_prose(body):
    """It must be able to FAIL (R-F3858). A detector that always matches would
    re-ask on every plain answer and turn one turn into two."""
    assert cli_agent.looks_like_narrated_tool_call(body, OFFERED) == ""


def test_no_offered_tools_means_no_match():
    """With nothing offered, any match is pure invention (R-F4329's rule)."""
    assert cli_agent.looks_like_narrated_tool_call(NARRATION, []) == ""


class _SilentUI(cli_agent.AgentUI):
    def __init__(self):
        self.infos: list[str] = []

    def info(self, text: str) -> None:
        self.infos.append(text)

    def __getattr__(self, _name):
        return lambda *a, **k: None


class _ScriptedLLM:
    """Replays fixed LLMResponses and records how many calls happened."""

    def __init__(self, provider: str, responses):
        self.config = type("C", (), {"provider": provider})()
        self._responses = list(responses)
        self.calls = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    def chat(self, messages, tools=None):
        self.calls += 1
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="done", tool_calls=[])


def _agent(tmp_path, llm):
    from aria_cli.safety import WriteGuard
    from aria_cli.tools import Toolbox
    toolbox = Toolbox(tmp_path, WriteGuard(tmp_path))
    built = cli_agent.Agent(llm=llm, toolbox=toolbox, system_prompt="s",
                            ui=_SilentUI(), auto_approve=True)
    built.retry_backoff = 0
    return built


def _resp(content="", tool_calls=None):
    # raw_message must carry tool_calls exactly as a real provider returns it —
    # the agent echoes raw_message into history, and an assistant turn missing
    # its tool_calls is dangling, which _repair_dangling_tool_calls correctly
    # strips. Getting this wrong makes the fixture, not the code, the failure.
    raw = {"role": "assistant", "content": content}
    if tool_calls:
        raw["tool_calls"] = tool_calls
    return LLMResponse(content=content, tool_calls=tool_calls or [],
                       raw_message=raw)


def _call(name, args):
    return {"id": "abcdefghi", "type": "function",
            "function": {"name": name, "arguments": _json.dumps(args)}}


def test_a_narrated_turn_is_re_asked_and_the_tool_actually_runs(tmp_path):
    """THE CAPABILITY TEST: drive the real turn loop. Before R-F4337 this ended
    at 'tools: 0 calls' with the narration as the final answer."""
    (tmp_path / "calc.py").write_text("x = 1\n", encoding="utf-8")
    llm = _ScriptedLLM("aria-llm", [
        _resp(content=NARRATION),
        _resp(tool_calls=[_call("read_file", {"path": "calc.py"})]),
        _resp(content="x = 1"),
    ])
    built = _agent(tmp_path, llm)
    res = built.run_turn("review the logs")
    assert llm.calls == 3, "the re-ask never happened"
    assert any("R-F4337" in i for i in built.ui.infos), "the re-ask was silent"
    assert any(m.get("role") == "tool" for m in built.messages), (
        "no tool ever executed — the turn still ended narrating"
    )
    assert res.final_text == "x = 1"


def test_the_re_ask_happens_at_most_once(tmp_path):
    """A model that narrates twice must NOT loop."""
    llm = _ScriptedLLM("aria-llm", [_resp(content=NARRATION),
                                    _resp(content=NARRATION)])
    built = _agent(tmp_path, llm)
    res = built.run_turn("review the logs")
    assert llm.calls == 2, f"expected exactly 1 re-ask, got {llm.calls - 1}"
    assert res.final_text == NARRATION


def test_another_provider_is_never_second_guessed(tmp_path):
    """DeepSeek uses the proper channel; re-asking would burn a paid call and
    could talk it out of a legitimate plain answer."""
    llm = _ScriptedLLM("deepseek", [_resp(content=NARRATION)])
    built = _agent(tmp_path, llm)
    built.run_turn("review the logs")
    assert llm.calls == 1, "a non-sovereign provider was re-asked"


# -- R-F4336 the launcher refuses the shape that caused C-281 -----------------

LAUNCHER = ROOT / "scripts/train/serve_sovereign.sh"


def test_the_launcher_exists_and_names_the_base_distinctly():
    """Assert the LAUNCH COMMAND, not the file text.

    A first version asserted `"--served-model-name" in src` and stayed GREEN
    when the flag was stripped from the actual invocation — the string also
    appears in a comment and in an error message. A guard whose universe
    includes prose certifies prose (§16, R-F3791)."""
    lines = [ln.strip() for ln in
             LAUNCHER.read_text(encoding="utf-8").splitlines()
             if not ln.lstrip().startswith("#")]
    cmd = [ln for ln in lines if ln.startswith("--")]
    assert '--served-model-name "$BASE_NAME"' in " ".join(cmd), (
        "the vLLM invocation does not give the BASE its own served name; "
        "without it --model's own id becomes the served id and collides with "
        "the adapter — which is C-281"
    )
    assert '--lora-modules "${SERVED_NAME}=${ADAPTER_PATH}"' in " ".join(cmd)


def test_the_launcher_default_base_name_cannot_equal_the_adapter_name():
    """The default must not be the trap. BASE_NAME defaults to something that
    is not a plausible adapter id."""
    src = LAUNCHER.read_text(encoding="utf-8")
    assert 'BASE_NAME="${BASE_NAME:-aria-llm-base}"' in src


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash unavailable")
def test_the_launcher_refuses_a_colliding_pair(tmp_path):
    """The guard must fire BEFORE anything expensive happens."""
    proc = subprocess.run(
        [shutil.which("bash"), str(LAUNCHER)],
        env={"PATH": "/usr/bin:/bin", "BASE_NAME": "aria-llm-v0.4-dpo",
             "SERVED_NAME": "aria-llm-v0.4-dpo", "ADAPTER_PATH": str(tmp_path)},
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 2, f"launcher did not refuse: rc={proc.returncode}"
    assert "REFUSING TO START" in proc.stderr


def _narrating_agent(tmp_path):
    llm = _ScriptedLLM("aria-llm", [
        _resp(content=NARRATION),
        _resp(tool_calls=[_call("list_dir", {"path": "."})]),
        _resp(content="done"),
    ])
    built = _agent(tmp_path, llm)
    built.run_turn("review the logs")
    return built


def test_the_re_ask_removes_the_narration_from_history(tmp_path):
    """THE MEASURED PROPERTY. Live on the sovereign, across 5 narrated turns:
    steering with the narration still in context recovered 0/5; removing it and
    re-stating the request recovered 5/5. At temperature 0 she copies her own
    last answer, so leaving it there asks her to write it again — the same
    mechanism as the operator's byte-identical repeats."""
    built = _narrating_agent(tmp_path)
    bodies = [str(m.get("content") or "") for m in built.messages]
    assert not any(NARRATION[:40] in b for b in bodies), (
        "the narrated turn was left in history - that configuration measured "
        "0/5 recovery live"
    )


def test_the_re_ask_restates_the_original_request(tmp_path):
    """Dropping the narration must not drop the TASK with it."""
    built = _narrating_agent(tmp_path)
    users = [str(m.get("content") or "")
             for m in built.messages if m.get("role") == "user"]
    assert any("review the logs" in u and "Call the tool now" in u
               for u in users), "the re-ask lost the original request"


def test_the_retry_does_not_force_a_named_tool():
    """vLLM here rejects tool_choice='required' outright, and a NAMED tool would
    pick the tool FOR her from this module's guess — converting a wrong guess
    into a wrong execution. Measured recovery is 5/5 with her still choosing."""
    assert cli_agent._TOOL_CHOICE_ON_RETRY == "auto"
