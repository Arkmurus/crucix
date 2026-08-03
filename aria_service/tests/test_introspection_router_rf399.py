"""R-F399 — introspection-intent router.

Live evidence 2026-05-13:
  07:25  Antonio asks "Aria, how many neurons are currently within your brain,
         and what is your learning and research capacity every 6-hour study
         cycle? Are you able to digest chunks of documents and online content?
         Is the information kept infinite?"
  ARIA's chat router picked "capacity every 6-hour study cycle" as the
  research topic and dispatched spawn_research_task. Result:
  "Read 0 sources, learned 0 facts."
  07:27  ARIA answered from intuition with WRONG NUMBERS — invented an
         "18-month Knowledge Base TTL" (violates aria_infinite_memory.md),
         estimated 5,000-10,000 facts (real: 35,363 — ~7x undercount).

Fix in two parts:
  (a) New `is_capability_introspection_query()` in self_infra_detector.py
      catches the question shape (how many / how much / your brain / your
      memory / is information kept / do you remember / ...).
  (b) Chat router (_detect_tool_intent) returns
      `{"tool": "self_introspect", ...}` BEFORE the spawn_research_task
      paths. Handler calls /health/perf (R-F396 + R-F400 inventory) and
      returns a structured block ARIA can quote verbatim.

Tests pin:
  1. The detector catches the four canonical question shapes ARIA fielded
     on 2026-05-13.
  2. The detector does NOT over-catch generic search queries.
  3. The chat router dispatches self_introspect early (before
     spawn_research_task / OEM batch / etc.).
  4. The handler exists in the dispatch and references /health/perf.
  5. The handler's output contains the four required honesty rules.
"""
from __future__ import annotations

import pathlib

from ._source_probe import repo_path


def _src() -> str:
    return pathlib.Path(
        repo_path("aria_service/routes/aria.py")
    ).read_text(encoding="utf-8", errors="ignore")


def _detect_intent_body() -> str:
    """Full source of `_detect_tool_intent` (signature → next module-level def).

    R-F2784 (2026-07-19): replaces the fixed `src[idx:idx + 2000/10000]` windows.
    R-F1759 later inserted a `/help` guard at the TOP of _detect_tool_intent,
    pushing the introspection import/dispatch past the old 2000-char window and
    making the old ordering check compare against the FIRST `"tool":` — now the
    benign `/help` dispatch, not a competing research path. Bounding to the real
    function body keeps the R-F399 contract honest (§23) without a fragile count.
    """
    import re
    src = _src()
    i = src.find("def _detect_tool_intent")
    assert i > 0, "R-F399: _detect_tool_intent not found"
    rest = src[i + len("def _detect_tool_intent"):]
    m = re.search(r"\n(?:async def |def )\w", rest)  # next module-level def bounds it
    end = i + len("def _detect_tool_intent") + (m.start() if m else len(rest))
    return src[i:end]


# ── 1. Detector tests — positive cases ─────────────────────────────

def test_rf399_detector_catches_neuron_count_question():
    """The literal 07:25 question from Antonio must trigger."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    q = ("Aria, how many neurons are currently within your brain, "
         "and what is your learning and research capacity every "
         "6-hour study cycle?")
    assert is_capability_introspection_query(q), (
        "R-F399 regression: the EXACT question that triggered the "
        "07:25 spawn_research_task → 0 results bug is not caught."
    )


def test_rf399_detector_catches_memory_infinite_question():
    """ARIA's '18-month TTL' hallucination was triggered by 'Is the
    information kept infinite?' — must catch this too."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    q = "Is the information kept infinite?"
    assert is_capability_introspection_query(q)


def test_rf399_detector_catches_digestion_question():
    """'Are you able to digest chunks of documents?' should route to
    /health/perf where the answer about chunking capability lives."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    q = "Are you able to digest chunks of documents and online content?"
    assert is_capability_introspection_query(q)


def test_rf399_detector_catches_how_are_you_doing():
    """Plain self-assessment ask — must catch."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert is_capability_introspection_query("how are you performing today?")
    assert is_capability_introspection_query("how am I doing as a system?")


def test_rf399_detector_catches_tell_me_about_yourself():
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert is_capability_introspection_query("tell me about yourself")
    assert is_capability_introspection_query("tell me about your memory")


def test_rf399_detector_catches_how_many_facts():
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert is_capability_introspection_query("how many facts do you know")
    assert is_capability_introspection_query("how many signals are in your ledger")


def test_rf399_detector_catches_do_you_remember():
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert is_capability_introspection_query("do you remember our last conversation")
    assert is_capability_introspection_query("can you forget things?")


# ── 2. Detector tests — must NOT over-catch ────────────────────────

def test_rf399_detector_does_not_catch_dd_request():
    """A DD request must still route to deep_research / dd_orchestrate,
    NOT to introspection. False positive here would break ALL DD."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert not is_capability_introspection_query(
        "run a full DD on Lukoil Neftochim in Burgas"
    )
    assert not is_capability_introspection_query(
        "investigate Arnaldo La Scala please"
    )


def test_rf399_detector_does_not_catch_external_search():
    """External factual queries must NOT route to introspection."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert not is_capability_introspection_query("what is Saudi Arabia importing")
    assert not is_capability_introspection_query("who is Michele Zagaria")
    assert not is_capability_introspection_query("what are Russian sanctions")


def test_rf399_detector_does_not_catch_general_questions():
    """Generic questions about the world must not over-catch."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert not is_capability_introspection_query("how many countries are in NATO")
    assert not is_capability_introspection_query("how does export control work")
    assert not is_capability_introspection_query("are you able to share that file with me")  # asking permission, not capability


def test_rf399_detector_handles_empty():
    """Defensive: empty / None input → False, no crash."""
    from aria_service.intel.self_infra_detector import is_capability_introspection_query
    assert not is_capability_introspection_query("")
    assert not is_capability_introspection_query(None)


# ── 3. Chat router wiring ──────────────────────────────────────────

def test_rf399_router_imports_capability_detector():
    """_detect_tool_intent must import the new detector."""
    block = _detect_intent_body()
    assert "is_capability_introspection_query" in block, (
        "R-F399 regression: chat router doesn't import the capability "
        "introspection detector. Capability questions still route to "
        "spawn_research_task → 0 results."
    )


def test_rf399_router_returns_self_introspect_tool():
    """When the detector fires, the router must return the
    self_introspect tool — not deep_research, not spawn_research_task.

    R-F3620 — THIS TEST WAS RED FOR MONTHS AND TAUGHT PEOPLE TO IGNORE IT.
    It used to slice a FIXED 2500-character window after `def
    _detect_tool_intent` and grep it for the literal `"tool": "self_introspect"`.
    The branch sits ~3000 characters in, so the window simply stopped reaching
    it as the function grew — nothing was broken, the ruler was too short. It
    then sat in docs/suite_baseline.md among the known failures, which is
    exactly how a guard that cries wolf gets switched off: when ARIA really DID
    web-search herself on 2026-08-01 (Alienware Command Center support forums),
    this test was already red and told nobody.

    Now it asserts the PROPERTY by driving the real router. A window that
    cannot see the branch is not a weaker test — it is a test of nothing.
    """
    from aria_service.routes.aria import _detect_tool_intent

    for q in ("how many facts do you have?",
              "what are your capabilities?",
              "Aria, what are the issue with your current command centre?"):
        intent = _detect_tool_intent(q)
        assert intent is not None, f"no tool intent for {q!r}"
        assert intent.get("tool") == "self_introspect", (
            f"{q!r} routed to {intent.get('tool')!r} — capability questions "
            f"must not reach deep_research / spawn_research_task / web search"
        )
        assert intent.get("_reason"), (
            "the dispatch tag '_reason' is missing — the operator cannot see "
            "in logs which branch fired"
        )


def test_rf399_router_fires_before_spawn_research_task():
    """CRITICAL: the introspection branch MUST run BEFORE the web-search /
    deep_research / spawn_research_task paths, otherwise the chat router keeps
    picking sentence fragments and the bug regresses.

    R-F2784 (2026-07-19): the old check asserted introspection came before the
    FIRST `"tool":` dispatch. R-F1759 later added a `/help` guard that dispatches
    `"tool": "help"` EARLIER — a benign slash-command that never competes for a
    capability question — so the naive first-dispatch check false-failed. Assert
    the real invariant: introspection precedes the competing research/search
    dispatches (§23)."""
    body = _detect_intent_body()
    intro_idx = body.find("is_capability_introspection_query")
    assert intro_idx > 0, "introspection detector call not found"
    # The competing research/search dispatches that stole the query pre-R-F399.
    deep_idx = body.find('"tool": "deep_research"')
    spawn_idx = body.find('"tool": "spawn_research_task"')
    assert deep_idx > 0, "deep_research dispatch not found — marker drifted"
    assert intro_idx < deep_idx, (
        "R-F399 CRITICAL: introspection check runs AFTER the deep_research "
        "dispatch — capability questions regress to web search."
    )
    if spawn_idx > 0:
        assert intro_idx < spawn_idx, (
            "R-F399 CRITICAL: introspection check runs AFTER spawn_research_task."
        )
    # Also honour the OEM-batch / batch-RE paths when those markers are present.
    oem_idx = body.find("_OEM_BATCH_KW")
    batch_idx = body.find("_BATCH_RE")
    if oem_idx > 0:
        assert intro_idx < oem_idx, "introspection must run before OEM batch"
    if batch_idx > 0:
        assert intro_idx < batch_idx, "introspection must run before _BATCH_RE"


# ── 4. Handler — self_introspect tool dispatch ──────────────────────

def test_rf399_handler_dispatches_self_introspect():
    """The tool dispatch must include `if tool == 'self_introspect':`."""
    src = _src()
    assert 'if tool == "self_introspect":' in src, (
        "R-F399: self_introspect tool handler not in dispatch. "
        "The intent fires but nothing handles it."
    )


def test_rf399_handler_calls_health_perf():
    """The handler must invoke health_perf_ep() so it cites real numbers."""
    src = _src()
    dispatch_idx = src.find('if tool == "self_introspect":')
    assert dispatch_idx > 0
    block = src[dispatch_idx:dispatch_idx + 5000]
    assert "health_perf_ep()" in block, (
        "R-F399: self_introspect handler doesn't call health_perf_ep. "
        "ARIA gets no real numbers to cite."
    )


def test_rf399_handler_surfaces_retention_policy():
    """The handler must include the retention block in its output so
    ARIA can never again invent a TTL."""
    src = _src()
    dispatch_idx = src.find('if tool == "self_introspect":')
    block = src[dispatch_idx:dispatch_idx + 5000]
    assert "retention" in block.lower()
    assert "ttl_days" in block, (
        "R-F399: handler doesn't surface ttl_days. The 07:27 TTL "
        "hallucination would not be blocked."
    )
    assert "PERMANENT" in block or "permanent" in block


def test_rf399_handler_has_honesty_rules():
    """The handler's output must include explicit instructions to the
    LLM to NOT invent counts or TTLs."""
    src = _src()
    dispatch_idx = src.find('if tool == "self_introspect":')
    block = src[dispatch_idx:dispatch_idx + 5000]
    assert "HONESTY RULES" in block
    assert "DO NOT invent" in block, (
        "R-F399: honesty rules don't explicitly forbid invention."
    )
    # The 18-month TTL anti-pattern must be named
    assert "month TTL" in block or "permanent" in block.lower()


def test_rf399_handler_falls_back_on_perf_failure():
    """If /health/perf throws, the handler must return a graceful error
    that tells ARIA to flag the missing instrumentation — not invent
    numbers from intuition."""
    src = _src()
    dispatch_idx = src.find('if tool == "self_introspect":')
    block = src[dispatch_idx:dispatch_idx + 5000]
    assert "except Exception" in block
    assert "Do NOT invent" in block or "do not invent" in block.lower()


# ── 5. Regression — anti-hallucination defense ─────────────────────

def test_rf399_handler_explicitly_blocks_18_month_ttl_pattern():
    """The handler's honesty rules must explicitly call out the exact
    07:27 bug pattern ('X-month TTL') so future LLM drift can't
    re-introduce it."""
    src = _src()
    dispatch_idx = src.find('if tool == "self_introspect":')
    block = src[dispatch_idx:dispatch_idx + 5000]
    # Either explicit "X-month TTL" prohibition OR the retention block
    # surfaces ttl_days=None with permanent label
    has_explicit_block = "month TTL" in block
    has_permanent_anchor = "PERMANENT" in block and "ttl_days" in block
    assert has_explicit_block or has_permanent_anchor, (
        "R-F399: the 07:27 TTL hallucination pattern has no explicit "
        "block. Operator's aria_infinite_memory.md directive is not "
        "load-bearing on this code path."
    )
