"""R-F3116/R-F3119 — what a LIVE end-to-end DD run exposed.

All of this was found by actually running `orchestrate_dd` against live sources
(Mitie, 2026-07-26) rather than by reading code. Two of the defects are in fixes
shipped hours earlier the same day.

R-F3116 — TWO BLUF WRITERS, AND I FIXED THE WRONG ONE.
    dd_orchestrator.py:8295  synthesis          — runs on EVERY live DD
    dd_orchestrator.py:10399 refresh-persisted  — runs only after an adverse-media
                                                  follow-up merges
R-F3091 (entity scope) and R-F3092 (say each thing once) were applied to the second.
The live run therefore came back with `entity_scope: None` and next_actions reading

    "Resolve decision-readiness blocker: registry status is 'dissolved'"
    "Resolve decision-readiness blocker: financial capacity is unknown"

— verbatim restatements, the exact thing R-F3092 removed. Two shipped R-numbers,
dead on the customer path. Copying the fix into the second writer would preserve the
fork that caused it, so both now render from ONE function.

R-F3119 — BRAVE WAS PINNED AS PRIMARY AND STARVED BY THE FREE STACK.
Operator directive: the DD tools run on Claude + Brave. R-F2318 made Brave PRIMARY
and left nine free backends running alongside it — not after it, WITH it, sharing one
`SEARCH_GATHER_BUDGET` under a quorum+grace gather. Measured on the live run: every
backend returned `gather timeout` or `silent`, INCLUDING Brave, while a direct Brave
call from the same machine returned HTTP 200 with 5 results in 1.2s. The paid, pinned
primary was starved by contention with the free stack it exists to replace, and the
DD fell back to RAG memory only — 7 press items, adverse media never completed,
evidence grade D.
"""
import inspect

from aria_service.intel import dd_orchestrator as ddo
from aria_service.intel import web_search

# R-F3783/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def _readiness(answered=2, clearance=False, **over):
    base = {
        "status": "NOT_CLEARED", "clearance_ready": clearance,
        "answered": answered, "required": 5, "completion_pct": answered * 20,
        "evidence_grade": "D", "evidence_ready": False,
        "blocking_reasons": ["registry status is 'dissolved'",
                             "financial capacity is unknown"],
        "questions": {
            "identity": {"label": "Verified legal identity", "answered": False,
                         "blocker": "registry status is 'dissolved'"},
            "sanctions_export_control": {"label": "Sanctions and export-control exposure",
                                         "answered": True},
            "adverse_media": {"label": "Adverse media, corruption and litigation",
                              "answered": False, "blocker": "screening did not complete"},
            "ownership_control": {"label": "Ownership and control", "answered": True},
            "financial_capacity": {"label": "Financial capacity", "answered": False,
                                   "blocker": "financial capacity is unknown"},
        },
    }
    base.update(over)
    return base


# ── R-F3116 — one writer ───────────────────────────────────────────────────
def test_rf3116_next_actions_are_actions_on_the_LIVE_path():
    """THE LIVE DEFECT: the customer received verbatim blocker restatements."""
    bluf = ddo.compose_decision_bluf(_readiness(), "MITIE FACILITIES MANAGEMENT LIMITED")
    for a in bluf["next_actions"]:
        assert not a.startswith("Resolve decision-readiness blocker:"), (
            "R-F3116 REGRESSION: the synthesis writer is restating blockers again")
    joined = " ".join(bluf["next_actions"])
    assert "companies registry" in joined            # identity remedy
    assert "audited financial statements" in joined  # financial remedy
    assert "adverse-media search" in joined          # adverse remedy


def test_rf3116_bottom_line_names_questions_not_paragraphs():
    bluf = ddo.compose_decision_bluf(_readiness(), "Acme Ltd")
    bl = bluf["bottom_line"]
    assert "NOT CLEARED" in bl and "Unresolved:" in bl
    assert "registry status is 'dissolved'" not in bl, (
        "the blocker paragraph must stay on the scorecard row that owns it")
    assert "decision-readiness scorecard" in bl


def test_rf3116_cleared_report_is_unaffected():
    bluf = ddo.compose_decision_bluf(
        _readiness(answered=5, clearance=True, evidence_ready=True), "Acme Ltd")
    assert "GREEN" in bluf["bottom_line"]
    assert "Proceed with standard commercial process" in bluf["next_actions"]


def test_rf3116_there_is_exactly_ONE_bluf_writer():
    """THE ROOT. A second writer is how R-F3091/R-F3092 ended up dead on the live
    path, and how the phase gates once disagreed with themselves (R-F2639)."""
    src = module_source(ddo)
    assert src.count('f"Resolve decision-readiness blocker: {') == 0, (
        "R-F3116 REGRESSION: a writer is emitting verbatim blocker restatements again")
    # both call sites must delegate
    assert src.count("compose_decision_bluf(") >= 3, (
        "both the synthesis and the follow-up writer must render from the one function")


def test_rf3116_synthesis_writer_delegates():
    src = module_source(ddo)
    assert "_bluf = compose_decision_bluf(_ready, name)" in src, (
        "the SYNTHESIS writer — the one that runs on every live DD — must delegate")


# ── R-F3119 — Brave is the DD search tier ──────────────────────────────────
def test_rf3119_dd_scope_drops_the_free_stack():
    src = module_source(web_search)
    assert "_brave_exclusive" in src
    assert "backend_tasks = backend_tasks[:1]" in src, (
        "R-F3119 REGRESSION: DD scope is fanning out to the free stack again, which "
        "is what starved the pinned primary")


def test_rf3119_backend_names_cannot_drift_from_the_tasks():
    """R-F2318 already had an off-by-one label drift here. If the names list keeps
    naming backends that are no longer launched, every result is mislabelled."""
    src = module_source(web_search)
    assert "[] if _brave_exclusive else" in src, (
        "the ecosystem snapshot must not name backends that did not run")


def test_rf3119_is_scoped_to_DD_not_global():
    """The continuous researcher never sets the Brave flag and must keep its free
    stack — this is a DD-scope change, not a global one."""
    src = module_source(web_search)
    assert "_brave_exclusive = _brave_on and (" in src, (
        "exclusivity must be gated on the Brave SCOPE, never applied globally")


def test_rf3119_has_an_escape_hatch():
    src = module_source(web_search)
    assert "ARIA_DD_BRAVE_EXCLUSIVE" in src, (
        "a routing change this load-bearing needs an env kill-switch")


def test_rf3119_memory_backend_is_retained():
    """`memory` is our own $0 local cache (R-F185/§15 pay-once) — it cannot time out
    on a network and must not be dropped with the external free stack."""
    src = module_source(web_search)
    i = src.index("_brave_exclusive = _brave_on")
    j = src.index("_all_tasks = [", i)
    assert "_query_memory" in src[j:j + 400], "the memory task must still be launched"


# ── R-F3122 — SearXNG fallback; policy change must not cost resilience ─────
def test_rf3122_searxng_is_the_fallback_and_it_is_sovereign():
    """OPERATOR (2026-07-26): "if brave fails utilise aria searxng". The fallback is
    ARIA's OWN self-hosted SearXNG (R-F183), not the third-party free stack. R-F3119
    dropped everything, leaving DD with NO web tier when Brave fails."""
    src = module_source(web_search)
    assert "_brave_fallback_tasks = backend_tasks[1:2]" in src, (
        "R-F3122 REGRESSION: the DD fallback is not exactly one backend (SearXNG)")
    assert '_backend_names + ["searxng"]' in src, "the fallback must be SearXNG alone"


def test_rf3122_third_party_backends_are_not_in_the_dd_path_at_all():
    """Neither phase may use DuckDuckGo/GNews/Google/Bing/academic/defence/GDELT."""
    src = module_source(web_search)
    i = src.index("_brave_fallback_tasks: list = []")
    window = src[i:i + 900]
    for third_party in ("duckduckgo", "gnews", "google_news", "bing_news", "gdelt"):
        assert third_party not in window.lower(), (
            f"{third_party} must not be part of the DD fallback")


def test_rf3122_fallback_only_fires_when_the_primary_yields_nothing():
    src = module_source(web_search)
    assert "if _primary_yield == 0:" in src, (
        "SearXNG must run ONLY on the primary's failure path, never alongside it")


def test_rf3122_unused_coroutines_are_closed():
    """The list literal CONSTRUCTS all nine coroutines before we choose; the ones we
    never await must be closed or each search leaks them with a RuntimeWarning."""
    src = module_source(web_search)
    assert "for _unused in backend_tasks[2:]:" in src and "_unused.close()" in src
    assert "_t.close()" in src


def test_rf3122_fallback_use_is_DISCLOSED_never_silent():
    """§14: cooling is not breaking, but it must be visible."""
    src = module_source(web_search)
    assert '"brave_fallback_used"' in src and '"brave_primary"' in src


def test_rf3122_rationale_does_not_claim_unproven_contention():
    """R-F3119 asserted the free stack STARVED the primary. All ten backends failed
    including Brave, while a direct Brave call answered in 1.2s — event-loop
    starvation, not contention. An unsupported causal claim must not survive as
    justification in a comment."""
    src = module_source(web_search)
    i = src.index("R-F3122 — RATIONALE CORRECTED")
    window = src[i:i + 1200]
    assert "NOT budget contention" in window
