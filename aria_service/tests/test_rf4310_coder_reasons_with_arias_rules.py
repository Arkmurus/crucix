"""R-F4310 (C-262) — the autonomous coder must reason with ARIA's FULL
playbook and constitutional rules at EVERY stage, and edit-mode must reach the
route at all.

WHY THIS EXISTS. R-F4309 was written by ARIA's autonomous coder and came to
review with four defects. The PLAN was right ("share one httpx client across
the backends"); every defect was in the CODE (a process-global with no reset
path, which leaked a test's fake client into unrelated tests) and in the TESTS
(two "capability" tests that were greps over `inspect.getsource` and could not
fail — one asserted `src.count("follow_redirects=True") >= 3` while FOUR matched
and one was a comment).

That maps exactly onto which stages can see ARIA's accumulated judgement.
`SovereignLLM` has six stages and all six funnel through `_call`:

    generate_fix_plan   task=plan  playbook YES  RAG rules YES
    write_code          task=code  playbook YES  RAG rules no
    write_edit          task=edit  playbook YES  RAG rules no   <- route 400s it
    write_tests         task=test  playbook NO   RAG rules no   <- wrote the greps
    write_reproduce_test task=test playbook NO   RAG rules no
    analyse_failure     task=heal  playbook NO   RAG rules no

Constitutional rule #2 is literally `capability-test-required-per-fix` — "Every
fix MUST include a capability test that invokes the actual broken path". The
stage that wrote R-F4309's tests had never seen it.

THREE DEFECTS, all pinned below:

  1. `task="edit"` is rejected by the route. `write_edit` (R-F1295) sends
     `task="edit"`; the route's allow-list is `plan|code|test|heal|general`, so
     every surgical edit 400s. `_write_file_content` catches it and falls back
     to WHOLE-FILE generation — the exact truncation R-F1295 exists to prevent,
     and which R-F904 then blocks. So the coder could not edit a large file at
     all, silently. Fixed, and pinned by a contract test that reads the task
     literals out of sovereign_llm so the two sides cannot drift again.

  2. The test and heal prompts carry no playbook. `_playbook_preamble()` binds
     the coder to CLAUDE.md + AGENTS.md ("root-cause not symptom, smallest diff,
     verify twice, wire success+failure, no false success"). It was on plan,
     code and edit and absent from test, reproduce-test and heal.

  3. Only the PLAN stage receives ARIA's constitutional rules. Injected at the
     ONE choke point — the route's system prompt — rather than stage by stage.
     Curating a per-stage list is the whack-a-mole shape R-F3946 rejected: the
     seventh stage added later re-opens the hole silently.

WHAT THESE TESTS PROVE, precisely: that ARIA's rules REACH the model. They
cannot prove the model obeys them — no test can — so nothing here claims that.
"""
import ast
import pathlib
import re
from types import SimpleNamespace

import pytest


def _read(rel: str) -> str:
    from . import _source_probe
    return _source_probe.repo_path(rel).read_text(encoding="utf-8", errors="replace")


# ── defect 1: the coder's own task names must be accepted by its own route ───

def _tasks_sovereign_llm_sends() -> set[str]:
    """Every literal passed as `task=` inside sovereign_llm, read by AST.

    By AST rather than by grep so a renamed stage or a reformatted call cannot
    quietly shrink the set this guard checks.
    """
    tree = ast.parse(_read("aria_service/autonomous/sovereign_llm.py"))
    tasks: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "task" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    tasks.add(kw.value.value)
    return tasks


def _tasks_the_route_accepts() -> set[str]:
    """The route's allow-list, read out of the source as a tuple literal."""
    src = _read("aria_service/routes/aria.py")
    m = re.search(r"task not in \(([^)]*)\)", src)
    assert m, "could not find the coder/llm task allow-list in routes/aria.py"
    return {s.strip().strip("\"'") for s in m.group(1).split(",") if s.strip()}


def test_every_task_the_coder_sends_is_accepted_by_the_route():
    """CAPABILITY: no coder stage can be rejected by the coder's own endpoint.

    `write_edit` sent task="edit" against an allow-list that never listed it, so
    R-F1295 edit-mode 400'd on every large file and silently degraded to
    whole-file generation. Reading BOTH sides means the next stage added on
    either side cannot drift apart unnoticed.
    """
    sent = _tasks_sovereign_llm_sends()
    accepted = _tasks_the_route_accepts()
    assert sent, "found no task= literals in sovereign_llm — did the AST walk break?"
    rejected = sorted(sent - accepted)
    assert not rejected, (
        f"the coder sends task(s) {rejected} that /api/aria/coder/llm rejects with "
        f"400 (accepts {sorted(accepted)}). The caller swallows the error and falls "
        f"back to whole-file generation, so the stage fails SILENTLY."
    )


def test_edit_is_one_of_the_tasks_the_coder_sends():
    """The guard above is only meaningful while write_edit still sends 'edit'.

    Without this, deleting the `task="edit"` call site would make the contract
    test pass by emptying its universe — the "a guard whose universe is empty
    always certifies" shape.
    """
    assert "edit" in _tasks_sovereign_llm_sends(), (
        "write_edit no longer sends task='edit' — if edit-mode was removed, "
        "remove this test; if it was renamed, the contract test above is now "
        "checking a different set than the one that broke"
    )


# ── defect 2: every stage prompt carries ARIA's binding playbook ─────────────

@pytest.mark.parametrize(
    "builder",
    ["_build_plan_prompt", "_build_code_prompt", "_build_edit_prompt",
     "_build_test_prompt", "_build_healing_prompt"],
)
def test_every_stage_prompt_binds_the_coder_to_the_playbook(builder):
    """CAPABILITY: the playbook reaches every prompt builder.

    `_playbook_preamble()` is what binds the coder to CLAUDE.md + AGENTS.md. It
    was on plan/code/edit and absent from test/heal — and the test stage is the
    one that wrote R-F4309's two greps-that-could-not-fail.
    """
    from . import _source_probe
    src = _source_probe.function_source(
        "aria_service/autonomous/sovereign_llm.py", builder,
    )
    assert "_playbook_preamble()" in src, (
        f"{builder} does not include _playbook_preamble() — that stage writes "
        f"code or tests for this repo with none of ARIA's binding rules in "
        f"front of it"
    )


def test_the_reproduce_test_prompt_binds_the_playbook_too():
    """`write_reproduce_test` builds its prompt inline, not via a _build_* helper."""
    from . import _source_probe
    src = _source_probe.function_source(
        "aria_service/autonomous/sovereign_llm.py", "write_reproduce_test",
    )
    assert "_playbook_preamble()" in src, (
        "write_reproduce_test writes a pytest into this repo with none of "
        "ARIA's binding rules in front of it"
    )


# ── defect 3: ARIA's constitutional rules reach EVERY task, at one choke point ──

class _CapturingProvider:
    """Records the system_prompt the route builds, and names itself in the reply."""

    def __init__(self, name: str = "deepseek"):
        self.name = name
        self.is_configured = True
        self.calls = 0
        self.system_prompts: list[str] = []

    async def complete(self, system_prompt, user_message, *,
                       max_tokens=4096, timeout=60.0, **kwargs):
        self.calls += 1
        self.system_prompts.append(system_prompt)
        return SimpleNamespace(
            text='{"title": "ok"}', model=f"{self.name}-model",
            provider=self.name, usage={}, finish_reason="stop",
            input_tokens=0, output_tokens=0,
        )


def _client_with(provider):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from aria_service.routes import aria as aria_routes

    app = FastAPI()
    app.dependency_overrides[aria_routes._router_auth_dep] = lambda: None
    app.include_router(aria_routes.router)
    app.state.llm_provider = provider
    return TestClient(app)


_STUB_RULES = [
    {"rule": "CLAUDE.md §3c capability-test-required-per-fix: every fix MUST "
             "include a capability test that invokes the actual broken path."},
    {"rule": "CLAUDE.md §1 root-cause-not-symptom: never raise a timeout or add "
             "a retry to hide a defect."},
]


@pytest.fixture
def _rules(monkeypatch):
    """Serve deterministic constitutional rules instead of the live vector store.

    The real retrieval degrades to lexical where chromadb is absent (§20), which
    is correct behaviour but makes the CONTENT unpredictable; these tests are
    about whether the rules reach the model, not about ranking quality.
    """
    from aria_service.intel import coding_rag_indexer as crag
    monkeypatch.setattr(
        crag, "query_constitutional_constraints",
        lambda query, top_k=3, **kw: list(_STUB_RULES[:top_k]),
    )
    return _STUB_RULES


@pytest.mark.parametrize("task", ["plan", "code", "edit", "test", "heal", "general"])
def test_arias_constitutional_rules_reach_every_coder_task(_rules, task):
    """CAPABILITY — the defect, at the one choke point.

    Before R-F4310 only `generate_fix_plan` retrieved these (R-F1531 via
    rag_augmented_generator), so the code writer, the test writer and the
    self-healer worked without them. Parametrised over EVERY task because the
    whole point of fixing it at the route is that a stage cannot be forgotten.
    """
    provider = _CapturingProvider()
    client = _client_with(provider)
    r = client.post(
        "/api/aria/coder/llm",
        json={"prompt": "Write a regression test for the shared client change",
              "task": task, "response_format": "json"},
    )
    assert r.status_code == 200, f"task={task!r} was rejected: {r.text[:200]}"
    assert provider.system_prompts, "the provider was never called"
    sys_prompt = provider.system_prompts[-1]
    assert "capability test that invokes the actual broken path" in sys_prompt, (
        f"ARIA's constitutional rules did not reach the {task!r} stage — the "
        f"coder is writing for this repo without the rules it is judged by. "
        f"System prompt was: {sys_prompt[:400]!r}"
    )


def test_rule_retrieval_failure_degrades_and_never_breaks_the_call(monkeypatch):
    """CAPABILITY: an unavailable rule store must not take the coder down.

    §20 records this retrieval failing silently three separate times. It is a
    grounding aid, not a precondition: if it raises, the coder must still get
    its completion — with the playbook it already carries — rather than 500.
    """
    from aria_service.intel import coding_rag_indexer as crag

    def _boom(*a, **k):
        raise RuntimeError("chromadb unavailable")

    monkeypatch.setattr(crag, "query_constitutional_constraints", _boom)
    provider = _CapturingProvider()
    client = _client_with(provider)
    r = client.post(
        "/api/aria/coder/llm",
        json={"prompt": "Write the corrected file content", "task": "code",
              "response_format": "json"},
    )
    assert r.status_code == 200, (
        f"a rule-retrieval failure broke the coder call: {r.text[:200]}"
    )
    assert provider.calls == 1
    assert "autonomous self-coding engine" in provider.system_prompts[-1], (
        "the base framing must survive a retrieval failure"
    )


def test_rule_retrieval_runs_off_the_event_loop(_rules):
    """The retrieval is a BLOCKING vector query — it must not run on the loop.

    coding_rag_indexer's own docstrings say so, and C-95/C-99 are two live
    incidents caused by blocking work on this loop. Asserted structurally
    because a latency assertion here would be a flake.
    """
    from . import _source_probe
    src = _source_probe.function_source("aria_service/routes/aria.py", "coder_llm_ep")
    assert "query_constitutional_constraints" in src, (
        "the rule injection is not in coder_llm_ep — if it moved, move this test"
    )
    assert "to_thread" in src, (
        "query_constitutional_constraints is a blocking chromadb query and must "
        "be called via asyncio.to_thread — see its own docstring, and C-95/C-99"
    )
