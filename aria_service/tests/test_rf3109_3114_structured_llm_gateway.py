"""R-F3109..R-F3114 — the structured-output gateway and the dead paths it found.

MEASURED BEFORE THE FIX (2026-07-26), not inferred:

  grounded_reasoner._get_llm()        -> None on every call
  grounded_reasoner._extract_premises -> []   on a message with obvious premises
  grounded_reasoner._decompose        -> ['Answer this: ...'] (stub fallback)
  llm_eval_framework._ask_model('deepseek') -> answer = "[ERROR: cannot import
                                               name 'LLMPipeline' ...]"

Four intra-package imports named things that have never existed in this tree
(`llm_pipeline.LLMPipeline`, `intel.aria_llm_provider`, `AriaLLMProvider`), each
inside a broad `except`, so every one degraded in silence. company_investigator hit
the identical import and R-F2535 fixed it THERE — one site fixed, two left, because
nothing structural made the others impossible.

The through-line these tests defend: a failed model call must never be readable as
an answer. Not as an empty list (gateway), not as a grounded claim
(grounded_reasoner), not as a score of zero (eval).
"""
from __future__ import annotations

import ast
import asyncio
import functools
import pathlib

import pytest

from aria_service.llm.structured import (
    OUTCOME_INVALID_OUTPUT,
    NON_ANSWERING,
    StructuredResult,
    call_structured,
)
from aria_service.intel.sources._common import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_UNAVAILABLE,
)

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


class _FakeResult:
    def __init__(self, text: str, model: str = "fake-model", routed_via: str = ""):
        self.text = text
        self.model = model
        self.routed_via = routed_via
        self.input_tokens = 11
        self.output_tokens = 22


class _FakeProvider:
    """Stands in at the ONE external boundary — the provider call."""

    name = "fake"

    def __init__(self, text: str = "[]", raises: Exception | None = None):
        self._text = text
        self._raises = raises
        self.calls: list[tuple] = []

    async def complete(self, system_prompt, user_message, **kw):
        self.calls.append((system_prompt, user_message, kw))
        if self._raises:
            raise self._raises
        return _FakeResult(self._text)


# ── R-F3109 — the gateway ────────────────────────────────────────────────────

def test_valid_json_matching_schema_is_returned():
    provider = _FakeProvider('["a premise", "another"]')
    r = asyncio.run(call_structured(
        "sys", "user", schema={"type": "list", "items": "str"},
        llm=provider, caller="test",
    ))
    assert r.ok and r.outcome == OUTCOME_OK
    assert r.data == ["a premise", "another"]
    assert r.model == "fake-model"
    assert r.input_tokens == 11 and r.output_tokens == 22


def test_markdown_fenced_json_is_accepted():
    """Models routinely wrap JSON in ```json fences — normalisation, not repair."""
    provider = _FakeProvider('```json\n{"claims": []}\n```')
    r = asyncio.run(call_structured(
        "sys", "user", schema={"type": "dict", "required": ["claims"]},
        llm=provider, caller="test",
    ))
    assert r.ok, r.errors
    assert r.data == {"claims": []}


def test_prose_reply_is_invalid_output_and_keeps_the_raw_text():
    provider = _FakeProvider("I think the answer is probably Acme Ltd.")
    r = asyncio.run(call_structured("sys", "user", llm=provider, caller="test"))
    assert r.outcome == OUTCOME_INVALID_OUTPUT
    assert r.data is None
    assert "Acme Ltd" in r.raw_text, "raw reply must survive for investigation"
    assert r.errors and "not valid JSON" in r.errors[0]


def test_schema_mismatch_is_rejected_and_names_the_problem():
    provider = _FakeProvider('{"not": "a list"}')
    r = asyncio.run(call_structured(
        "sys", "user", schema={"type": "list", "items": "str"},
        llm=provider, caller="test",
    ))
    assert r.outcome == OUTCOME_INVALID_OUTPUT
    assert r.data is None
    assert "expected a JSON list" in r.errors[0]


def test_missing_required_key_is_rejected():
    provider = _FakeProvider('{"other": 1}')
    r = asyncio.run(call_structured(
        "sys", "user", schema={"type": "dict", "required": ["claims"]},
        llm=provider, caller="test",
    ))
    assert r.outcome == OUTCOME_INVALID_OUTPUT
    assert any("'claims'" in e and "missing" in e for e in r.errors), r.errors


def test_empty_body_is_invalid_output_not_an_empty_answer():
    r = asyncio.run(call_structured("sys", "user", llm=_FakeProvider("   "), caller="test"))
    assert r.outcome == OUTCOME_INVALID_OUTPUT
    assert r.data is None


def test_no_provider_is_unavailable_not_invalid(monkeypatch):
    """'Never asked' and 'answered badly' are different failures (R-F3101's lesson).

    R-F3449 — this must ESTABLISH the "no provider" precondition, not assume it. With
    `llm=None`, `resolve_provider` falls back to `main.app.state.llm_provider`
    (structured.py:180-182), and `main.app` is a module-level SINGLETON whose `.state`
    persists for the whole session. So once any earlier test boots the lifespan, a real
    provider is present, the call proceeds, and this returned OUTCOME_ERROR instead of
    OUTCOME_UNAVAILABLE — one of the fifteen order-dependent failures in the R-F3448
    baseline ("assert 'error' == 'unavailable'"). Green alone, red in-suite, and the test
    was silently depending on nothing having booted the app first.
    """
    try:
        from aria_service.main import app as _app
        monkeypatch.setattr(_app.state, "llm_provider", None, raising=False)
    except Exception:
        pass          # main not importable here → resolve_provider already yields None

    r = asyncio.run(call_structured("sys", "user", llm=None, caller="test"))
    assert r.outcome == OUTCOME_UNAVAILABLE
    assert r.data is None
    assert not r.answered, "an unreachable provider is a COVERAGE GAP"


def test_provider_exception_is_classified_not_propagated():
    provider = _FakeProvider(raises=RuntimeError("upstream 500"))
    r = asyncio.run(call_structured("sys", "user", llm=provider, caller="test"))
    assert r.outcome == OUTCOME_ERROR
    assert r.data is None
    assert not r.answered


def test_provider_timeout_is_its_own_outcome():
    provider = _FakeProvider(raises=asyncio.TimeoutError())
    r = asyncio.run(call_structured("sys", "user", llm=provider, caller="test"))
    assert r.outcome == "timeout"
    assert r.data is None


@pytest.mark.parametrize("text,exc", [
    ("not json", None),
    ("", None),
    ('{"a":1}', None),
    (None, RuntimeError("boom")),
    (None, asyncio.TimeoutError()),
])
def test_north_star_data_is_never_an_empty_container_on_failure(text, exc):
    """THE PROPERTY. A caller must not be able to iterate a failure and conclude
    'nothing found'. On every non-ok outcome `data` is None — never [] or {}."""
    provider = _FakeProvider(text if text is not None else "", raises=exc)
    r = asyncio.run(call_structured(
        "sys", "user", schema={"type": "list", "items": "str"},
        llm=provider, caller="test",
    ))
    if not r.ok:
        assert r.data is None, f"{r.outcome} handed back an iterable"
        assert r.outcome in NON_ANSWERING


def test_result_is_frozen_and_json_safe():
    r = StructuredResult(outcome=OUTCOME_OK, data=[1])
    with pytest.raises(Exception):
        r.outcome = "tampered"
    assert r.as_dict()["ok"] is True


# ── R-F3110 — grounded_reasoner's dead LLM path ──────────────────────────────

def test_extract_premises_now_reaches_the_model(monkeypatch):
    """CAPABILITY TEST — the real broken path. Pre-fix this returns [] because
    _get_llm() raised ImportError on a class that never existed."""
    from aria_service.intel import grounded_reasoner as gr_mod

    provider = _FakeProvider('["Acme was fined by the FCA", "A director resigned"]')
    monkeypatch.setattr(gr_mod, "GroundedReasoner", gr_mod.GroundedReasoner)
    reasoner = gr_mod.GroundedReasoner()
    reasoner._llm = provider          # resolved provider, as _get_llm now returns

    premises = asyncio.run(reasoner._extract_premises(
        "Acme Ltd was fined by the FCA in 2024 and its director resigned."))

    assert any("FCA" in p for p in premises), f"model premises never landed: {premises}"
    assert provider.calls, "the provider was never called"


def test_extract_premises_does_not_invent_on_invalid_output():
    """A rejected reply must leave the deterministic premises untouched."""
    from aria_service.intel.grounded_reasoner import GroundedReasoner

    reasoner = GroundedReasoner()
    reasoner._llm = _FakeProvider("I cannot answer that.")
    premises = asyncio.run(reasoner._extract_premises("Acme Ltd was fined."))
    assert all("cannot answer" not in p.lower() for p in premises)


def test_synthesised_claims_are_not_born_grounded():
    """R-F3110 — `grounded` is earned from evidence, never asserted at construction.

    Phase 5 downgrades evidence-less claims anyway, so this is observably neutral
    today; it is fail-safe if the honesty judge is ever unavailable, in which case
    _verify_claims_inline returns claims UNTOUCHED.
    """
    import inspect

    from aria_service.intel import grounded_reasoner as gr_mod

    src = function_source(gr_mod.GroundedReasoner, "_reason_over_evidence")
    tree = ast.parse(inspect.cleandoc(src))
    checked = 0
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Claim"):
            continue
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        grounded = kwargs.get("grounded")
        born_true = isinstance(grounded, ast.Constant) and grounded.value is True
        if born_true:
            checked += 1
            # The ONE legitimate case: a claim built from retrieved evidence.
            # `grounded` is earned from that evidence, never asserted about model
            # prose — which carries no EvidenceItem and would sail through if
            # phase 5's honesty judge were ever unavailable (:596 returns claims
            # untouched).
            assert "evidence" in kwargs, (
                "a Claim is born grounded=True without evidence= — grounded must be "
                "earned from evidence, not asserted at construction"
            )
    assert checked, "no grounded=True Claim found — has the constructor moved?"


# ── R-F3111 / R-F3114 — the eval framework's dead arms ───────────────────────

def test_unwired_aria_llm_is_unmeasured_not_a_wrong_answer(monkeypatch):
    """ARIA_LLM_URL unset is the DECLARED state (CLAUDE.md §16). It is a coverage
    gap, and must never be scored as the model answering badly."""
    from aria_service.intel.llm_eval_framework import EvalQuestion, LLMEvalFramework
    from aria_service.llm import aria_llm_provider as aria_llm

    monkeypatch.setattr(aria_llm, "is_configured", lambda: False)
    fw = LLMEvalFramework()
    question = EvalQuestion(id="q1", question="q?", expected_answer="a")
    answer, meta = asyncio.run(fw._ask_model("aria-llm", question))

    assert answer == "", "an unwired endpoint must not produce an answer string"
    assert meta.get("unmeasured"), "the coverage gap was not recorded"
    assert "[ERROR" not in answer


def test_aggregate_excludes_unmeasured_questions():
    """R-F3114 — averaging an unasked question in as 0.0 reports 'measured and
    failed' when the truth is 'could not measure' (the R-F2639 tri-state)."""
    from aria_service.intel.llm_eval_framework import LLMEvalFramework, PerQuestionScore

    fw = LLMEvalFramework()
    good = PerQuestionScore(question_id="1", model="m", answer="a", latency_ms=1.0,
                            token_count=5, correctness=1.0, overall=1.0)
    dead = PerQuestionScore(question_id="2", model="m", answer="", latency_ms=1.0,
                            token_count=0, error="no provider chain resolvable")

    result = fw._aggregate("m", [good, dead])

    assert result.questions_unmeasured == 1
    assert result.questions_attempted == 1, "unmeasured question inflated the denominator"
    assert result.overall_score == pytest.approx(1.0), \
        "a dead arm dragged the score toward zero and called it a measurement"


def test_all_unmeasured_reports_zero_attempted_not_a_zero_score():
    from aria_service.intel.llm_eval_framework import LLMEvalFramework, PerQuestionScore

    fw = LLMEvalFramework()
    dead = [PerQuestionScore(question_id=str(i), model="m", answer="", latency_ms=1.0,
                             token_count=0, error="dead arm") for i in range(5)]
    result = fw._aggregate("m", dead)

    assert result.questions_attempted == 0, "an eval that measured NOTHING claimed 5 attempts"
    assert result.questions_unmeasured == 5


# ── R-F3112 — the enforcement guard ──────────────────────────────────────────
#
# The four dead imports were not a coincidence; nothing forced an intra-package
# import to name something real. This guard is what stops the next one, and is the
# reason this is a defect-class fix rather than four edits.
#
# NAMED, SHRINKING ALLOWLIST. Each entry is a REAL dead import found by this scan
# on 2026-07-26 and left for a scoped fix — not an exemption. `test_allowlist_
# entries_are_still_genuinely_dead` fails if one is repaired without being removed
# here, so the exemption cannot outlive the defect (R-F3103's pattern).
_KNOWN_DEAD_IMPORTS = {
    "aria_service/intel/portal_coverage_audit.py::aria_service.intel.web_search.search_web",
    "aria_service/intel/report_builder.py::aria_service.intel.researcher.investigate",
    "aria_service/intel/truth_verifier.py::aria_service.intel.git_utils",
    "aria_service/intel/truth_verifier.py::aria_service.autonomous.test_runner.run_tests",
}

_REPO = pathlib.Path(__file__).resolve().parents[2]


@functools.lru_cache(maxsize=None)
def _module_path(dotted: str) -> pathlib.Path | None:
    """Resolve a dotted aria_service module to a file, WITHOUT importing it.

    importlib.util.find_spec() imports parent packages, which made this scan both
    slow enough to trip the suite timeout and side-effecting. The filesystem
    answers the same question for an in-repo package.
    """
    base = _REPO / dotted.replace(".", "/")
    module = base.with_suffix(".py")
    if module.is_file():
        return module
    package = base / "__init__.py"
    return package if package.is_file() else None


@functools.lru_cache(maxsize=None)
def _module_defines(dotted: str) -> frozenset[str] | None:
    src = _module_path(dotted)
    if src is None or src.name == "__init__.py":
        # A package __init__ may re-export dynamically; do not judge its symbols.
        return None
    try:
        tree = ast.parse(src.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None
    names: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, ast.Assign):
            names.update(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            names.add(n.target.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in n.names)
    return frozenset(names)


@functools.lru_cache(maxsize=1)
def _scan_dead_intra_package_imports() -> frozenset[str]:
    dead: set[str] = set()
    root = _REPO / "aria_service"
    for path in root.rglob("*.py"):
        posix = path.as_posix()
        if "/tests/" in posix or "__pycache__" in posix:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        rel = path.relative_to(_REPO).as_posix()
        parts = path.relative_to(_REPO).with_suffix("").as_posix().split("/")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            if node.level:
                base = parts[:-1]
                if node.level > 1:
                    base = base[: len(base) - (node.level - 1)]
                dotted = ".".join(base + node.module.split("."))
            else:
                dotted = node.module
            if not dotted.startswith("aria_service"):
                continue
            if _module_path(dotted) is None:
                dead.add(f"{rel}::{dotted}")
                continue
            defined = _module_defines(dotted)
            if defined is None:
                continue
            for alias in node.names:
                if alias.name != "*" and alias.name not in defined:
                    dead.add(f"{rel}::{dotted}.{alias.name}")
    return frozenset(dead)


def test_no_new_dead_intra_package_imports():
    """An import naming something that does not exist is a silently dead path.

    Every one of these sits inside a broad `except` somewhere and degrades a real
    capability without a word — which is exactly how grounded_reasoner and the eval
    framework lost their LLM for so long.
    """
    dead = _scan_dead_intra_package_imports()
    new = dead - _KNOWN_DEAD_IMPORTS
    assert not new, (
        "new dead intra-package import(s) — the symbol does not exist:\n  "
        + "\n  ".join(sorted(new))
    )


def test_allowlist_entries_are_still_genuinely_dead():
    """A repaired import must be REMOVED from the allowlist, so the exemption
    cannot outlive the defect it documents."""
    dead = _scan_dead_intra_package_imports()
    repaired = _KNOWN_DEAD_IMPORTS - dead
    assert not repaired, (
        "these are fixed — delete them from _KNOWN_DEAD_IMPORTS:\n  "
        + "\n  ".join(sorted(repaired))
    )


def test_golden_entries_survive_conversion_to_eval_questions():
    """R-F3115 — eval_runner built EvalQuestion without its required `id`, so every
    golden entry raised TypeError into a bare `except: continue`. `_fw_questions`
    was always empty, the framework eval ran over ZERO questions, and a score of
    0.000 was logged as a measurement. This drives the real conversion.
    """
    from aria_service.intel.llm_eval_framework import EvalQuestion

    golden = [
        {"id": "gold_1", "question": "Who owns Acme?", "expected_answer": "X",
         "category": "ownership"},
        {"question": "No id here", "expected_answer": "Y"},   # id must be derived
    ]
    converted = []
    for index, item in enumerate(golden):
        converted.append(EvalQuestion(
            id=str(item.get("id") or f"golden_{index}"),
            question=item.get("question", ""),
            expected_answer=item.get("expected_answer", ""),
            category=item.get("category", "general"),
            requires_refusal=False,
        ))

    assert len(converted) == len(golden), "golden entries were dropped in conversion"
    assert converted[0].id == "gold_1"
    assert converted[1].id == "golden_1", "an entry without an id must still convert"


def test_eval_runner_passes_an_id_when_building_eval_questions():
    """Pins the fix at its real call site, not just in a local re-implementation."""
    import inspect

    from aria_service.intel import eval_runner

    src = module_source(eval_runner)
    tree = ast.parse(src)
    builds = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "EvalQuestion"
    ]
    assert builds, "EvalQuestion is no longer constructed in eval_runner"
    for node in builds:
        assert any(kw.arg == "id" for kw in node.keywords), (
            "eval_runner builds EvalQuestion without `id` — every entry will raise "
            "TypeError and be swallowed by the surrounding except"
        )


def test_the_llm_imports_this_workstream_fixed_are_gone():
    """Regression pin for the four that started this (R-F3110/R-F3111)."""
    dead = _scan_dead_intra_package_imports()
    offenders = [d for d in dead if "LLMPipeline" in d or "AriaLLMProvider" in d
                 or "intel.aria_llm_provider" in d]
    assert not offenders, offenders
