"""R-F3429 — GATE A exemptions, and the duplicate-key hazard that nearly cost one.

WHAT R-F3429 DID. GATE A reported 67 public functions with no @fail_wire and no
exemption. They are not one problem: roughly a third are pure accessors — ContextVar
reads, env reads, in-place relabels, string parsing — with no failure domain. Wrapping
those floods the gap ledger with non-failures until nobody reads it, which is the §21b
"dark" condition reached by noise instead of silence. Two of them
(`cost_tracker.set_user` / `set_tier`) return a `contextvars.Token` the caller resets
with, so a wrapper would BREAK THE CONTRACT, not merely add noise.

The other 48 are genuine failure paths with real I/O — the orchestrator entry points,
the flush/poll/deploy/reconcile loops. They get WIRED, not excused. Exempting those to
turn the gate green would be the exact false clean this harness exists to prevent, so
this suite also pins that they are NOT exempt.

THE HAZARD THIS SUITE MAKES PERMANENT. `HARD_EXEMPT` is a dict literal, so a repeated
key SILENTLY KEEPS THE LAST ONE. While writing R-F3429 I added a second
`"cost_tracker.py"` key; it would have discarded the original entry, un-exempting
`cost_tracker.feature` — a SYNC GENERATOR context manager that must never be wrapped —
and someone could then have wired it and broken the context manager. The file's own
R-F1785 note warns about this hazard; I reproduced it while writing the fix for it, and
only an ad-hoc duplicate check caught it. That check is now a test.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from aria_service.intel import wiring_harness as wh

SRC = (Path(__file__).resolve().parents[1] / "intel" / "wiring_harness.py").read_text(
    encoding="utf-8")


def _dict_keys(name: str) -> list[str]:
    tree = ast.parse(SRC)
    for node in ast.walk(tree):
        target = getattr(node, "target", None)
        if isinstance(node, ast.AnnAssign) and getattr(target, "id", "") == name:
            return [k.value for k in node.value.keys              # type: ignore[union-attr]
                    if isinstance(k, ast.Constant)]
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == name for t in node.targets):
            return [k.value for k in node.value.keys              # type: ignore[union-attr]
                    if isinstance(k, ast.Constant)]
    raise AssertionError(f"{name} not found — re-anchor this test")


# ── the hazard ───────────────────────────────────────────────────────────────

def test_hard_exempt_has_no_duplicate_module_keys():
    """A repeated key silently keeps the LAST one. Caught live: a second
    "cost_tracker.py" key would have discarded the original, un-exempting a sync
    generator context manager."""
    keys = _dict_keys("HARD_EXEMPT")
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, (
        f"duplicate HARD_EXEMPT keys {dupes} — the later one silently wins and the "
        f"earlier exemptions are LOST. Merge into the existing entry instead."
    )


def test_module_gap_types_has_no_duplicate_keys():
    keys = _dict_keys("MODULE_GAP_TYPES")
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert not dupes, f"duplicate MODULE_GAP_TYPES keys {dupes}"


def test_the_generator_exemption_that_was_nearly_lost_survives():
    ok, reason = wh.is_exempt("cost_tracker.py", "feature")
    assert ok and "GENERATOR" in reason.upper()


def test_the_stream_exemptions_that_share_a_key_survive():
    """fallback.py carries stream + provider_scope + the R-F3429 accessors in ONE
    entry. If a future edit splits them into two keys, these go dark."""
    for fn in ("stream", "provider_scope", "get_preferred_provider"):
        assert wh.is_exempt("fallback.py", fn)[0], f"fallback.py::{fn} lost its exemption"


# ── every exemption carries a REASON ─────────────────────────────────────────

def test_every_exemption_states_why():
    """An exemption without a reason is indistinguishable from an oversight, and it is
    what a future reader has to trust."""
    bare = [f"{mod}::{fn}" for mod, fns in wh.HARD_EXEMPT.items()
            for fn, reason in fns.items() if not str(reason).strip()]
    assert not bare, f"exemptions with no stated reason: {bare}"


# ── the accessors are exempt; the failure paths are NOT ──────────────────────

@pytest.mark.parametrize("mod,fn", [
    ("cost_tracker.py", "set_user"),
    ("cost_tracker.py", "get_current_tier"),
    ("web_search.py", "brave_is_enabled"),
    ("web_search.py", "reset_brave_scope"),
    ("sanctions.py", "split_bracketed_name"),
    ("companies_house.py", "missing_key_gap"),
    ("brain_hook.py", "seconds_since_interactive"),
    ("openai_compat.py", "default_deepseek_model"),
    ("test_runner.py", "coder_tests_enabled"),
])
def test_pure_accessors_are_exempt(mod, fn):
    assert wh.is_exempt(mod, fn)[0], f"{mod}::{fn} should be exempt — it has no failure domain"


@pytest.mark.parametrize("mod,fn", [
    ("dd_orchestrator.py", "orchestrate_dd"),
    ("dd_orchestrator.py", "list_reports"),
    ("dd_orchestrator.py", "mark_dd_failed"),
    ("dd_orchestrator.py", "reconcile_stale_running_dds"),
    ("companies_house.py", "get_company_profile"),
    ("engine.py", "check_engine_liveness"),
    ("student.py", "flush_mastery"),
    ("news_monitor.py", "poll_feeds"),
    ("autonomous_deploy.py", "deploy"),
    ("rag_store.py", "diagnose_and_heal_corrupt_collections"),
])
def test_real_failure_paths_are_NOT_exempt(mod, fn):
    """These do real I/O and can genuinely fail. Exempting them to turn GATE A green
    would be the false clean the harness exists to prevent — they must be WIRED."""
    assert not wh.is_exempt(mod, fn)[0], (
        f"{mod}::{fn} was exempted — it is a real failure path and must carry "
        f"@fail_wire instead"
    )


# ── the gate itself ──────────────────────────────────────────────────────────

def test_gate_b_is_clean():
    """R-F3428 took GATE B from ~60 to 0 by registering two route modules. It must
    stay there — a new unregistered module silently falls to `_default`."""
    assert wh.run_all_gates()["gate_b"] == []


def test_gate_a_shrank_and_the_remainder_is_wiring_not_exempting():
    """GATE A was 67; the accessors took it to 48. The rest is a WIRING backlog, and
    this test pins the direction of travel: it must not be closed by exempting more.
    Raise the bound only when functions are genuinely wired."""
    violations = wh.run_all_gates()["gate_a"]
    assert len(violations) <= 48, (
        f"GATE A grew to {len(violations)} — a new public function landed unwired"
    )
