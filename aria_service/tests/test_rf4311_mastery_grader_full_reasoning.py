"""R-F4311 (C-263) — the mastery grader escalates to ARIA's FULL reasoning
instead of discarding every sample the router told it to escalate.

THE DEFECT. `reasoning_router.try_local_reasoning` documents `answered=False` as
a ROUTING signal — "no local source was confident, escalate to the cloud" — and
calls itself "the gatekeeper that decides whether the cloud is even needed".
`_grade_researched_question` treated that instruction as unmeasurable and
returned None, discarding the sample. Measured live: `independence_ratio 0.695`,
so roughly one question in five reached that branch and taught nothing. ARIA read
the material, stored it, and was then graded by a stunted subset of her own mind.

WHY THE OBVIOUS FIX WOULD HAVE BEEN A TROPHY, which is what most of this file
tests. `kb.store_fact` runs BEFORE the grade in the reading loop. So handing a
cloud model ARIA's retrieved context reads the graded document straight back out
of her knowledge base and scores ~1.0 every time — R-F2660's participation trophy
rebuilt one layer down, on the gate CLAUDE.md §1 forbids closing by measuring
less. Two guards, and the fix is worthless without either:

  1. ANTI-CIRCULARITY — a retrieved fact that IS the graded document is dropped
     before the model sees it.
  2. ATTRIBUTION CONTROL — the same question is asked again with NO memory. A
     frontier model knows plenty about European defence procurement on its own;
     crediting that would credit ARIA for the vendor's education. Credit requires
     the with-memory answer to BEAT the no-memory control by a margin.

And the escalated path must be able to LOWER mastery. A path that returns only
True or None is an upward-only ratchet on a Phase A gate — a trophy however
carefully it is dressed.
"""
import asyncio

import pytest

from aria_service.autonomous import tasks as T

# `_answer_grounding` is the fraction of the ANSWER's tokens present in the
# document, so these fixtures are deterministic: every word of GROUNDED appears
# in DOC, and none of UNGROUNDED's content words do.
DOC = ("Brazil defence procurement expanded with Embraer KC-390 deliveries "
       "and new offset rules introduced in 2026.")
GROUNDED = "Brazil defence procurement expanded Embraer KC-390 offset rules 2026"
UNGROUNDED = "Norwegian fisheries quotas declined sharply during last winter storms"
QUESTION = "What are the most important procurement facts for latam lusophone?"


class _Recorder:
    """Stub general chain. Serves queued answers and records every call."""

    def __init__(self, answers):
        self.is_configured = True
        self.name = "deepseek"
        self._answers = list(answers)
        self.calls = []

    async def complete(self, system_prompt, user_message, **kwargs):
        self.calls.append({"system": system_prompt, "user": user_message, **kwargs})
        if not self._answers:
            raise AssertionError("the grader made more completions than expected")
        nxt = self._answers.pop(0)
        if isinstance(nxt, Exception):
            raise nxt

        class _R:
            text = nxt
            model = "deepseek-model"
        return _R()


@pytest.fixture
def wired(monkeypatch):
    """Local reasoning DECLINES (the escalate branch), with stubbable memory + chain."""
    from aria_service.intel import reasoning_router, knowledge
    from aria_service.llm import structured

    async def _declines(question, **kw):
        return {"answered": False, "reason": "no local source was confident"}

    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _declines)
    monkeypatch.setenv("ARIA_GRADE_FULL_REASONING", "1")

    state = {"facts": [], "provider": None}
    monkeypatch.setattr(knowledge, "search_fact_records",
                        lambda q, limit=10: list(state["facts"]))
    monkeypatch.setattr(structured, "resolve_provider",
                        lambda llm=None: state["provider"])
    return state


def _grade(question=QUESTION, doc=DOC):
    return asyncio.run(T._grade_researched_question(question, doc))


# ── the defect itself ────────────────────────────────────────────────────────

def test_the_escalate_signal_is_obeyed_not_discarded(wired):
    """CAPABILITY: local declining now reaches ARIA's full stack.

    Before R-F4311 this returned None without ever consulting her — one sample in
    five, thrown away, on the gate they were meant to move.
    """
    # NOTE the fixture: an earlier version used "Embraer expanded KC-390 offset
    # rules", which is a near-copy of DOC — and the anti-circularity guard
    # correctly dropped it, leaving no context and no escalation. That is the
    # guard working, and it is worth keeping in view: a "holding" here must be
    # something ARIA knew BEFORE the document, or there is nothing to recall.
    wired["facts"] = [{"topic": "brazil aerospace", "content": "Embraer maintains a Gaviao Peixoto plant."}]
    provider = _Recorder([GROUNDED, UNGROUNDED])
    wired["provider"] = provider

    result = _grade()

    assert provider.calls, (
        "the grader did not escalate — `answered=False` is the router instructing "
        "it to ask the cloud, not a verdict that the sample is unmeasurable"
    )
    assert result is True


def test_the_local_path_is_untouched_when_local_answers(wired, monkeypatch):
    """The measured path must be byte-for-byte the old behaviour: no LLM call."""
    from aria_service.intel import reasoning_router

    async def _answers(question, **kw):
        return {"answered": True, "response": GROUNDED}

    monkeypatch.setattr(reasoning_router, "try_local_reasoning", _answers)
    provider = _Recorder([])          # any completion at all raises
    wired["provider"] = provider

    assert _grade() is True
    assert provider.calls == [], (
        "when the local stack answers, the escalation must not run — that would "
        "spend on every grade and change the measured path"
    )


# ── guard 1: the trophy must be impossible ───────────────────────────────────

def test_a_fact_that_is_the_graded_document_is_kept_out_of_the_context(wired):
    """CAPABILITY — the anti-circularity guard, at the prompt.

    The reading loop stores the document BEFORE grading, so it comes straight back
    out of `search_fact_records`. If it reaches the model, the model reads the
    answer off the page it is being scored against.
    """
    wired["facts"] = [
        {"topic": "the just-read article", "content": DOC},          # must be dropped
        {"topic": "older holding", "content": "Embraer signed an unrelated trainer deal."},
    ]
    provider = _Recorder([GROUNDED, UNGROUNDED])
    wired["provider"] = provider

    _grade()

    memory_prompt = provider.calls[0]["user"]
    assert "unrelated trainer deal" in memory_prompt, "the genuine holding was dropped too"
    assert DOC not in memory_prompt, (
        "the graded document reached the model as 'memory' — the grade can now be "
        "answered by reading back the page it is scored against, which is exactly "
        "the participation trophy R-F2660 removed"
    )


def test_when_the_only_memory_is_the_document_the_grade_is_unmeasured(wired):
    """The trophy case, end to end: it must return None, never True.

    If everything ARIA holds on this cell IS the thing she just read, there is no
    recall question to ask yet. Answering it anyway would certify the gate on the
    act of reading.
    """
    wired["facts"] = [{"topic": "the just-read article", "content": DOC}]
    provider = _Recorder([GROUNDED, UNGROUNDED])
    wired["provider"] = provider

    assert _grade() is None
    assert provider.calls == [], "nothing should be asked when no own-memory survives"


# ── guard 2: attribution ─────────────────────────────────────────────────────

def test_no_credit_when_the_model_knew_it_without_her(wired):
    """CAPABILITY — the attribution control.

    A frontier model answers well about European defence procurement from
    parametric knowledge alone. Crediting that would credit ARIA for the vendor's
    education, which is not mastery.
    """
    wired["facts"] = [{"topic": "holding", "content": "Some unrelated background."}]
    provider = _Recorder([GROUNDED, GROUNDED])   # control scores identically
    wired["provider"] = provider

    assert _grade() is None, (
        "the no-memory control matched the with-memory answer, so the answer is "
        "not evidence about ARIA at all — it must not be credited"
    )
    assert len(provider.calls) == 2, "the control condition did not run"


def test_credit_only_when_her_memory_beats_the_control(wired):
    """The positive case: with-memory grounded, no-memory not."""
    wired["facts"] = [{"topic": "holding", "content": "Embraer offset background."}]
    wired["provider"] = _Recorder([GROUNDED, UNGROUNDED])
    assert _grade() is True


def test_the_control_prompt_carries_no_memory(wired):
    """The control is only a baseline if it is genuinely memoryless."""
    wired["facts"] = [{"topic": "holding", "content": "Embraer offset background."}]
    provider = _Recorder([GROUNDED, UNGROUNDED])
    wired["provider"] = provider

    _grade()

    control_prompt = provider.calls[1]["user"]
    assert "Embraer offset background" not in control_prompt
    assert "ARIA'S MEMORY" not in control_prompt, (
        "the control was given ARIA's memory, so it is not a baseline and the "
        "margin comparison measures nothing"
    )


# ── the path must be able to lower mastery ───────────────────────────────────

def test_an_ungrounded_memory_answer_is_a_real_miss(wired):
    """CAPABILITY: the escalated path returns False, not just True/None.

    An escalation that can only credit is an upward-only ratchet on a Phase A
    gate. §1 forbids closing gate #2 by measuring less; a path that cannot fail
    measures less while looking like it measures more.
    """
    wired["facts"] = [{"topic": "holding", "content": "Unrelated background material."}]
    wired["provider"] = _Recorder([UNGROUNDED, UNGROUNDED])
    assert _grade() is False


# ── honest unmeasured cases ──────────────────────────────────────────────────

def test_insufficient_sentinel_is_unmeasured_not_a_miss(wired):
    """"My memory does not cover this" is not "I got it wrong"."""
    wired["facts"] = [{"topic": "holding", "content": "Unrelated background material."}]
    wired["provider"] = _Recorder(["INSUFFICIENT", UNGROUNDED])
    assert _grade() is None


@pytest.mark.parametrize("answers,label", [
    ([RuntimeError("chain down"), UNGROUNDED], "memory completion raised"),
    ([GROUNDED, RuntimeError("chain down")], "control completion raised"),
])
def test_an_instrument_failure_is_never_recorded_as_a_wrong_answer(wired, answers, label):
    """R-F3483's rule, extended to the new path.

    Note the second case especially: the memory answer was GOOD, but without a
    control there is no attribution — crediting it would silently reintroduce the
    vendor-knowledge trophy.
    """
    wired["facts"] = [{"topic": "holding", "content": "Unrelated background material."}]
    wired["provider"] = _Recorder(answers)
    assert _grade() is None, label


def test_no_provider_is_unmeasured(wired):
    wired["facts"] = [{"topic": "holding", "content": "Unrelated background."}]
    wired["provider"] = None
    assert _grade() is None


def test_the_switch_turns_the_escalation_off(wired, monkeypatch):
    """Operator lever: default ON, but the loop must be stoppable without a deploy."""
    monkeypatch.setenv("ARIA_GRADE_FULL_REASONING", "0")
    wired["facts"] = [{"topic": "holding", "content": "Unrelated background."}]
    provider = _Recorder([])
    wired["provider"] = provider
    assert _grade() is None
    assert provider.calls == []


# ── RULE ONE and the event loop ──────────────────────────────────────────────

def test_the_grader_never_names_a_provider_or_model(wired):
    """§17 RULE ONE: Anthropic is DD (and ARIA WA) only.

    The general chain excludes Anthropic by the `preference_only_providers`
    default, so the grader stays compliant precisely BY NOT naming anyone. A
    `prefer_provider`/`model` argument here would reach past that default and put
    continuous autonomous grading on the paid DD pin.
    """
    wired["facts"] = [{"topic": "holding", "content": "Embraer offset background."}]
    provider = _Recorder([GROUNDED, UNGROUNDED])
    wired["provider"] = provider

    _grade()

    for call in provider.calls:
        assert not call.get("prefer_provider"), "the grader pinned a provider"
        assert not call.get("model"), "the grader pinned a model"
        assert "anthropic" not in repr(call).lower()


def test_the_corpus_scan_runs_off_the_event_loop():
    """`search_fact_records` is an O(corpus) scan measured at 2.28s in C-171.

    Asserted structurally: a latency assertion here would be a flake, and C-95 /
    C-99 are two live incidents caused by blocking work on this loop.
    """
    from . import _source_probe
    src = _source_probe.function_source(
        "aria_service/autonomous/tasks.py", "_aria_own_context",
    )
    assert "to_thread" in src, (
        "search_fact_records is a blocking O(corpus) scan and must not run on the "
        "event loop — see C-171"
    )


def test_every_branch_of_the_escalated_grade_reaches_the_brain(wired):
    """§21a: the defect was samples vanishing SILENTLY, so unmeasured must be loud."""
    seen = []
    from aria_service.intel import engine_wiring

    import aria_service.autonomous.tasks as _t
    orig = _t._wire_grade
    _t._wire_grade = lambda outcome, detail: seen.append(outcome)
    try:
        wired["facts"] = [{"topic": "holding", "content": "Unrelated background."}]
        wired["provider"] = _Recorder([UNGROUNDED, UNGROUNDED])
        assert _grade() is False
    finally:
        _t._wire_grade = orig
    assert seen == ["miss"], f"expected the miss to be wired, got {seen}"
    assert callable(engine_wiring.wire_failure)
