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


def _src() -> str:
    return pathlib.Path(
        "C:/code/crucix/aria_service/routes/aria.py"
    ).read_text(encoding="utf-8", errors="ignore")


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
    src = _src()
    intent_idx = src.find("def _detect_tool_intent")
    assert intent_idx > 0
    # Look in the first 2000 chars after the def for the import
    block = src[intent_idx:intent_idx + 2000]
    assert "is_capability_introspection_query" in block, (
        "R-F399 regression: chat router doesn't import the capability "
        "introspection detector. Capability questions still route to "
        "spawn_research_task → 0 results."
    )


def test_rf399_router_returns_self_introspect_tool():
    """When the detector fires, the router must return the
    self_introspect tool — not deep_research, not spawn_research_task."""
    src = _src()
    intent_idx = src.find("def _detect_tool_intent")
    assert intent_idx > 0
    block = src[intent_idx:intent_idx + 2500]
    assert '"tool": "self_introspect"' in block
    assert "_reason" in block
    assert "capability_introspection_rf399" in block, (
        "R-F399: the dispatch tag '_reason' is missing — operator can't "
        "see in logs that the new router fired."
    )


def test_rf399_router_fires_before_spawn_research_task():
    """CRITICAL: the introspection branch MUST run BEFORE the
    spawn_research_task / OEM batch paths, otherwise the chat router
    keeps picking sentence fragments and the bug regresses."""
    src = _src()
    intent_idx = src.find("def _detect_tool_intent")
    block = src[intent_idx:intent_idx + 10000]
    intro_idx = block.find("is_capability_introspection_query")
    spawn_idx = block.find('"tool": "spawn_research_task"')
    oem_idx = block.find("_OEM_BATCH_KW")
    batch_idx = block.find("_BATCH_RE")
    assert intro_idx > 0, "introspection detector call not found"
    assert intro_idx < spawn_idx, (
        "R-F399 CRITICAL: introspection check runs AFTER "
        "spawn_research_task — the bug regresses."
    )
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
