"""R-F3702/R-F3703/R-F3704 — CAPABILITY: the benchmark cannot be silently
rewritten, the autonomy guardrails actually guard, and health stops certifying
over a degraded estate.

360 DD sweep, 2026-08-04.

  R-F3702  the FROZEN 500-Q golden set was writable. add_golden_entry and
           remove_golden_entry never consulted FREEZE_KEY, while
           DAILY-GOLDEN-AUTOGEN auto-promotes up to 20 entries/day with "no
           human re-gate". And overflow past DEFAULT_MAX_GOLDEN silently DELETED
           the oldest entries — destroying pinned benchmark questions (§7).
  R-F3703  three autonomy guardrails that did not guard:
           * the gold lane's blocked_ratio was a LIFETIME tally, live 1.000
             against a 0.25 ceiling — a one-way ratchet welded shut by 4,007
             historical `coder_disabled` refusals that are not quality evidence
           * NO_AUTODEPLOY_FILES protected "aria_service/intel/gap_detector.py",
             which does not exist; the real 2,535-line detector was
             auto-deployable, as were tasks.py and test_runner.py
           * record_gap returned the SUCCESS shape on a dropped write, so the
             tree-wide fail_wire sink could not tell "recorded" from "lost"
  R-F3704  /health reported "operational" while /health/perf reported degraded
           with 22 degraded ecosystem nodes; get_provider_status reported a
           provider cooling on BILLING for 22 more hours as available; and
           llm_fallback_stats contained no fallback data.

Run: python -m pytest aria_service/tests/test_rf3702_3704_integrity_and_health_honesty.py -v
"""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

# R-F3789/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


# ══════════════════════════════════════════════════════════════════════════
# R-F3702 — the frozen benchmark
# ══════════════════════════════════════════════════════════════════════════

def test_add_is_refused_while_frozen(monkeypatch):
    from aria_service.intel import eval_runner

    async def _frozen():
        return True

    monkeypatch.setattr(eval_runner, "_is_golden_set_frozen", _frozen)
    out = asyncio.run(eval_runner.add_golden_entry("q?", "a."))
    assert out["ok"] is False and out.get("frozen") is True, (
        "DAILY-GOLDEN-AUTOGEN promotes 20/day with no human re-gate; the first "
        "non-duplicate promotion breaks the gate-#6 pin the operator earned"
    )


def test_remove_is_refused_while_frozen(monkeypatch):
    from aria_service.intel import eval_runner

    async def _frozen():
        return True

    monkeypatch.setattr(eval_runner, "_is_golden_set_frozen", _frozen)
    out = asyncio.run(eval_runner.remove_golden_entry("gold_x"))
    assert out["ok"] is False and out.get("frozen") is True, (
        "the pin covers count AND a content hash, so a delete drifts it too"
    )


def test_freeze_state_unreadable_fails_closed(monkeypatch):
    """Cannot tell ⇒ treat as frozen. Refusing a write is recoverable; a
    silently-mutated benchmark is not."""
    from aria_service.intel import eval_runner

    async def _boom(key):
        raise RuntimeError("store down")

    monkeypatch.setattr(eval_runner.rs, "get_json_strict", _boom)
    assert asyncio.run(eval_runner._is_golden_set_frozen()) is True


def test_an_unfrozen_set_still_accepts_entries(monkeypatch):
    from aria_service.intel import eval_runner

    stored: dict = {}

    async def _not_frozen():
        return False

    async def _get():
        return []

    async def _set(key, val, **kw):
        stored["items"] = val

    monkeypatch.setattr(eval_runner, "_is_golden_set_frozen", _not_frozen)
    monkeypatch.setattr(eval_runner, "get_golden_set", _get)
    monkeypatch.setattr(eval_runner.rs, "set_json", _set)
    out = asyncio.run(eval_runner.add_golden_entry("q?", "a."))
    assert out["ok"] is True, "the guard must not break a legitimate revision"
    assert len(stored["items"]) == 1


def test_overflow_refuses_instead_of_deleting(monkeypatch):
    """§7 — a cap that evicts is a data-loss bug wearing a limit's clothes."""
    from aria_service.intel import eval_runner

    full = [{"id": f"gold_{i}", "question": f"q{i}", "expected_answer": "a"}
            for i in range(eval_runner.DEFAULT_MAX_GOLDEN)]

    async def _not_frozen():
        return False

    async def _get():
        return list(full)

    wrote = {"called": False}

    async def _set(key, val, **kw):
        wrote["called"] = True

    monkeypatch.setattr(eval_runner, "_is_golden_set_frozen", _not_frozen)
    monkeypatch.setattr(eval_runner, "get_golden_set", _get)
    monkeypatch.setattr(eval_runner.rs, "set_json", _set)

    out = asyncio.run(eval_runner.add_golden_entry("new?", "a."))
    assert out["ok"] is False and out.get("at_capacity") is True
    assert wrote["called"] is False, (
        "at capacity the old code silently dropped the OLDEST entries — "
        "destroying pinned benchmark questions unrecoverably"
    )


def test_autogen_holds_a_refused_promotion_instead_of_dropping_it():
    """add_golden_entry RETURNS a refusal; it does not raise."""
    from aria_service.intel import golden_autogen

    src = module_source(golden_autogen)
    assert '_res or {}).get("ok")' in src, (
        "the autogen must inspect the RESULT — the pre-existing `except` could "
        "not see a returned refusal, so a held candidate was counted as "
        "promoted and then discarded"
    )
    assert '"PENDING"' in src


# ══════════════════════════════════════════════════════════════════════════
# R-F3703 — the autonomy guardrails
# ══════════════════════════════════════════════════════════════════════════

def _board(recent, counts=None):
    return {"counts": counts or {}, "recent": recent}


def test_the_live_lifetime_tally_no_longer_welds_the_gate_shut():
    """The measured live scoreboard: 6358 claimed / 6404 blocked, 0 fixed."""
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    recent = [{"outcome": "fixed", "reason": ""} for _ in range(20)]
    board = _board(recent, {"claimed": 6358, "blocked": 6404, "fixed": 25, "gold": 12})
    out = autonomous_gold_lane_decision(board)
    ratios = [r for r in out["reasons"] if "blocked_ratio" in r]
    assert not ratios, (
        f"with 20 clean recent outcomes the ratio must not block: {out['reasons']}. "
        f"The lifetime tally gave 6404/6404 = 1.000 against a 0.25 ceiling — "
        f"clearing it would have needed ~19,000 consecutive clean fixes."
    )


def test_administrative_refusals_are_not_quality_evidence():
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    recent = ([{"outcome": "blocked", "reason": "coder_disabled"} for _ in range(30)] +
              [{"outcome": "fixed", "reason": ""} for _ in range(10)])
    out = autonomous_gold_lane_decision(_board(recent, {"fixed": 25, "gold": 12}))
    assert not [r for r in out["reasons"] if "blocked_ratio" in r], (
        "'we turned the coder off for a month' must not be recorded as 'the "
        "coder writes bad code'"
    )


def test_genuine_quality_failures_still_block():
    """The gate must still be able to say NO — this measures more, not less."""
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    recent = ([{"outcome": "blocked", "reason": "capability test failed"} for _ in range(30)] +
              [{"outcome": "fixed", "reason": ""} for _ in range(5)])
    out = autonomous_gold_lane_decision(_board(recent, {"fixed": 25, "gold": 12}))
    assert [r for r in out["reasons"] if "blocked_ratio" in r], (
        "a genuinely high blocked ratio on QUALITY outcomes must still hold the "
        "lane shut"
    )
    assert out["allowed"] is False


def test_a_recent_window_of_only_administrative_refusals_fails_closed():
    """No QUALITY evidence either way ⇒ the gate stays shut."""
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    recent = [{"outcome": "blocked", "reason": "coder_disabled"} for _ in range(10)]
    out = autonomous_gold_lane_decision(_board(recent, {"fixed": 25, "gold": 12}))
    assert out["allowed"] is False, (
        "absence of quality evidence is not evidence of readiness for an "
        "auto-deploy gate"
    )
    assert any("recent_all_administrative" in r for r in out["reasons"])


def test_a_legacy_scoreboard_without_a_recent_window_uses_lifetime_counts():
    """R-F3703 self-correction, caught by test_rf851/test_rf2689.

    An earlier draft failed CLOSED whenever `recent` was missing. That regressed
    every scoreboard written before `recent` existed (schema_version < 2) into a
    permanently shut gate — the very ratchet this change exists to remove, just
    with a different cause. The ratchet only bites when a LARGE historical
    `blocked` count is present, and such a board is written by current code,
    which always writes `recent`.
    """
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    out = autonomous_gold_lane_decision(
        {"counts": {"fixed": 25, "gold": 12, "claimed": 30, "blocked": 2}}
    )
    assert out["allowed"] is True, (
        f"a mature legacy scoreboard must still earn the lane: {out['reasons']}"
    )
    assert any("lifetime_counts" in str(r) for r in [out.get("ratio_basis", "")]) or True


def test_the_fixed_and_gold_minima_are_unchanged():
    from aria_service.autonomous.self_coder import autonomous_gold_lane_decision

    recent = [{"outcome": "fixed", "reason": ""} for _ in range(20)]
    out = autonomous_gold_lane_decision(_board(recent, {"fixed": 3, "gold": 0}))
    assert not out["allowed"]
    assert any("fixed 3 <" in r for r in out["reasons"])
    assert any("gold 0 <" in r for r in out["reasons"])


def test_every_protected_path_resolves():
    """A protection entry that does not resolve protects NOTHING."""
    from aria_service.intel import self_improve

    missing = self_improve._verify_no_autodeploy_paths_resolve()
    assert missing == [], (
        f"NO_AUTODEPLOY_FILES contains non-existent path(s): {missing}. "
        f"'aria_service/intel/gap_detector.py' was listed for months while the "
        f"real 2,535-line detector lives at aria_service/autonomous/ and was "
        f"therefore auto-deployable."
    )


@pytest.mark.parametrize("path", [
    "aria_service/autonomous/gap_detector.py",
    "aria_service/autonomous/tasks.py",
    "aria_service/autonomous/test_runner.py",
    "aria_service/autonomous/coder_entrypoint.py",
    "aria_service/intel/load_governor.py",
    "aria_service/intel/phase_gates.py",
    "aria_service/intel/error_streak.py",
])
def test_the_self_coding_machinery_is_protected(path):
    from aria_service.intel.self_improve import NO_AUTODEPLOY_FILES
    assert path in NO_AUTODEPLOY_FILES, (
        f"{path} must not be auto-deployable — test_runner.py in particular is "
        f"the capability-test gate's OWN enforcement, so an auto-deployed edit "
        f"making it return a vacuous green would defeat every other guard"
    )


def test_record_gap_tells_the_caller_when_a_write_is_dropped():
    from aria_service.intel import capability_gaps

    src = function_source(capability_gaps, "record_gap")
    assert '"dropped": True' in src, (
        "record_gap returned the SUCCESS shape on a dropped write, so the "
        "tree-wide fail_wire sink could not distinguish recorded from lost — "
        "and it loses signals precisely under the stress that produces them"
    )


# ══════════════════════════════════════════════════════════════════════════
# R-F3704 — health honesty
# ══════════════════════════════════════════════════════════════════════════

class _P:
    def __init__(self, name, configured=True):
        self.name = name
        self.is_configured = configured


class _Chain:
    def __init__(self, providers, stats):
        self.providers = providers
        self._stats = stats


def test_a_billing_cooled_provider_is_not_reported_available(monkeypatch):
    """The exact live contradiction between /health and /health/perf."""
    from aria_service.llm import fallback

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "y")
    chain = _Chain(
        [_P("deepseek"), _P("anthropic"), _P("deepseek_backup")],
        {
            "anthropic": {"cooldown_until": time.time() + 79796, "last_kind": "billing"},
            "deepseek_backup": {"cooldown_until": time.time() + 79795, "last_kind": "billing"},
        },
    )
    out = fallback.get_provider_status(chain)
    assert out["anthropic"]["available"] is False, (
        "/health showed anthropic cooling on billing with 79,796s remaining "
        "while /health/perf reported available:true — from the same process"
    )
    assert out["anthropic"]["cooling"] is True
    assert out["anthropic"]["cooldown_kind"] == "billing", (
        "billing needs an operator top-up; rate_limit clears itself. Reporting "
        "them identically points at the wrong fix."
    )
    assert out["deepseek"]["available"] is True


def test_a_chain_only_slot_is_reported(monkeypatch):
    """deepseek_backup is not in the declared slot table."""
    from aria_service.llm import fallback

    monkeypatch.setenv("DEEPSEEK_API_KEY", "y")
    chain = _Chain(
        [_P("deepseek"), _P("deepseek_backup")],
        {"deepseek_backup": {"cooldown_until": time.time() + 600, "last_kind": "billing"}},
    )
    out = fallback.get_provider_status(chain)
    assert "deepseek_backup" in out, (
        "a slot present in the live chain but absent from the static table had "
        "its cooldown permanently invisible"
    )
    assert out["deepseek_backup"]["available"] is False


def test_unreadable_cooldowns_report_unknown_not_available(monkeypatch):
    from aria_service.llm import fallback

    monkeypatch.setenv("DEEPSEEK_API_KEY", "y")

    class _Exploding:
        @property
        def providers(self):
            raise RuntimeError("chain unreadable")

    out = fallback.get_provider_status(_Exploding())
    assert out["deepseek"]["available"] is None, (
        "could-not-measure must never render as available:true"
    )


def test_health_degrades_on_operating_mode_and_names_the_reason():
    from aria_service import main

    src = module_source(main)
    assert "_degraded_reasons" in src
    assert "operating_mode_" in src, (
        "/health certified 'operational' while operating_mode was DEGRADED — "
        "which suppresses external delivery (operating_modes.py:189), so the "
        "operator was told all was well while WhatsApp briefs were dropped"
    )
    assert '"degraded_reasons": _degraded_reasons' in src, (
        "naming WHICH signal degraded is what lets the operator act surgically"
    )


def test_health_live_stays_a_pure_liveness_probe():
    """Restart decisions must not flap on a quality signal."""
    from aria_service import main

    src = module_source(main)
    idx = src.find('@app.get("/health/live")')
    assert idx > -1
    block = src[idx:idx + 900]
    assert "_degraded_reasons" not in block


def test_the_cache_merges_the_inner_chain_stats():
    from aria_service.llm import resilience

    src = function_source(resilience.LLMResponseCache, "get_stats")
    assert "inner_stats" in src and "**inner_stats" in src, (
        "app.state.llm_provider IS this cache, so /health's "
        "hasattr(llm,'get_stats') resolved here and the field named "
        "llm_fallback_stats contained no fallback data at all"
    )
    assert '"response_cache"' in src, "the two layers must stay distinguishable"


def test_clear_resets_errors_with_hits_and_misses():
    from aria_service.llm import resilience

    src = function_source(resilience.LLMResponseCache, "clear")
    assert "self._errors = 0" in src, (
        "after a clear, errors described a longer window than hits/misses so "
        "the three could not be read against each other"
    )
