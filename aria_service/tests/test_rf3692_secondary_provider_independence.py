"""R-F3692 — CAPABILITY: a CRITICAL answer can never be "cross-model verified"
by the model that wrote it.

THE DEFECT (360 DD sweep, 2026-08-04). `pick_secondary_provider(llm)` was called
with NO `exclude_name` at routes/aria.py:11657 (the chat gate) and at
verification_gate.py:623 (`observe_critical_response`). The selector then walked
`FallbackProvider.providers` and returned the FIRST active provider — the one
that had just authored the answer. On agreement the reply was stamped:

    🛡 [CROSS-MODEL CONSISTENT — 2 models, same evidence; not independent
       corroboration]

…on sanctions HIT/CLEAN and HALT/PROCEED answers where one model had agreed
with itself. Measured against the live chain shape
`[deepseek, anthropic(billing-cooled), deepseek_backup(billing-cooled)]`:

    secondary with NO exclude    -> deepseek     (the primary — self-verified)
    secondary excluding deepseek -> None         (honest: no second vendor)

Three compounding causes, all fixed:
  1. no exclusion passed        -> the selector is now SAFE BY DEFAULT
  2. exclusion by exact NAME    -> `deepseek_backup` (same account, same key,
                                   fallback.py:1534/1545) could "verify"
                                   `deepseek`; now excluded by VENDOR
  3. `cooldown_until` compared against `time.monotonic()` while fallback.py
     writes it in wall-clock (`time.time()`, fallback.py:340/:544/:549)

Run: python -m pytest aria_service/tests/test_rf3692_secondary_provider_independence.py -v
"""
from __future__ import annotations

import time

import pytest

from aria_service.learning import verification_gate as vg

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


class _Prov:
    def __init__(self, name: str, configured: bool = True):
        self.name = name
        self.is_configured = configured

    async def complete(self, *a, **kw):  # pragma: no cover - not exercised
        raise AssertionError("not called in these tests")


class _Chain:
    """Minimal stand-in for FallbackProvider: the two attributes the selector reads."""

    def __init__(self, providers, stats=None):
        self.providers = providers
        self._stats = stats or {}


def _cooling(seconds: float = 3600.0) -> dict:
    # Wall-clock, exactly as fallback.py._record_failure writes it.
    return {"cooldown_until": time.time() + seconds}


# ── The live chain shape at the time of the audit ──────────────────────────

def _live_chain():
    return _Chain(
        [_Prov("deepseek"), _Prov("anthropic"), _Prov("deepseek_backup")],
        {"anthropic": _cooling(79796), "deepseek_backup": _cooling(79795)},
    )


def test_live_chain_yields_no_secondary_rather_than_the_primary():
    """The headline case: one reachable vendor ⇒ None, not self-verification."""
    got = vg.pick_secondary_provider(_live_chain())
    assert got is None, (
        f"returned {getattr(got, 'name', got)!r} as a 'second opinion' — with "
        f"anthropic and deepseek_backup both cooling, the only provider left is "
        f"the one that wrote the answer"
    )


def test_never_returns_the_provider_it_was_told_to_exclude():
    got = vg.pick_secondary_provider(_live_chain(), exclude_name="deepseek")
    assert got is None


def test_same_vendor_backup_slot_cannot_verify_the_primary():
    """deepseek_backup is the SAME account and key as deepseek."""
    chain = _Chain([_Prov("deepseek"), _Prov("deepseek_backup")])
    got = vg.pick_secondary_provider(chain, exclude_name="deepseek")
    assert got is None, (
        "deepseek_backup is built from the same DEEPSEEK_API_KEY "
        "(fallback.py:1534/1545) — it is the same model, not a second opinion"
    )


def test_default_exclusion_is_derived_when_the_caller_forgets():
    """Safe-by-default: the two live call sites passed nothing."""
    chain = _Chain([_Prov("deepseek"), _Prov("deepseek_backup")])
    assert vg.pick_secondary_provider(chain) is None, (
        "with no exclude_name the selector must still refuse the serving "
        "provider — requiring every caller to remember is the shape that failed"
    )


# ── It must still find a genuine second vendor ─────────────────────────────

def test_a_genuine_second_vendor_is_returned():
    chain = _Chain([_Prov("deepseek"), _Prov("anthropic")])
    got = vg.pick_secondary_provider(chain, exclude_name="deepseek")
    assert got is not None and got.name == "anthropic"


def test_a_genuine_second_vendor_is_found_without_an_explicit_exclusion():
    chain = _Chain([_Prov("deepseek"), _Prov("anthropic")])
    got = vg.pick_secondary_provider(chain)
    assert got is not None and got.name == "anthropic", (
        "safe-by-default must not become never-verify — a real second vendor "
        "still has to be selected"
    )


def test_unconfigured_providers_are_skipped():
    chain = _Chain([
        _Prov("deepseek"), _Prov("openai", configured=False), _Prov("anthropic"),
    ])
    got = vg.pick_secondary_provider(chain, exclude_name="deepseek")
    assert got is not None and got.name == "anthropic"


# ── The clock-domain defect ────────────────────────────────────────────────

def test_a_cooling_provider_is_skipped_using_wall_clock():
    """`cooldown_until` is wall-clock; comparing it to monotonic() was wrong."""
    chain = _Chain(
        [_Prov("deepseek"), _Prov("anthropic"), _Prov("gemini")],
        {"anthropic": _cooling(3600)},
    )
    got = vg.pick_secondary_provider(chain, exclude_name="deepseek")
    assert got is not None and got.name == "gemini", (
        "a wall-clock cooldown must exclude anthropic; the old monotonic() "
        "comparison happened to work only because epoch dwarfs process uptime"
    )


def test_an_expired_cooldown_is_available_again():
    chain = _Chain(
        [_Prov("deepseek"), _Prov("anthropic")],
        {"anthropic": {"cooldown_until": time.time() - 10}},
    )
    got = vg.pick_secondary_provider(chain, exclude_name="deepseek")
    assert got is not None and got.name == "anthropic"


# ── Degenerate inputs ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [None, object(), _Chain([])])
def test_degenerate_inputs_return_none(bad):
    assert vg.pick_secondary_provider(bad) is None


def test_vendor_helper_groups_slots_by_account():
    assert vg._vendor_of("deepseek_backup") == "deepseek"
    assert vg._vendor_of("deepseek") == "deepseek"
    assert vg._vendor_of("anthropic") == "anthropic"
    assert vg._vendor_of("") == ""


# ── routed_via must survive the rate-limit wrapper ─────────────────────────

def test_rate_limiter_preserves_the_fallback_routing_tag():
    """double_via_fallback parses `routed_via` for 'fallback:' to exclude the
    primary; the wrapper used to overwrite it unconditionally."""
    import inspect
    from aria_service.llm import rate_limiter

    src = module_source(rate_limiter)
    assert 'startswith("fallback:")' in src, (
        "RateLimitedProvider.complete must NOT clobber an inner "
        "`fallback:<name>` tag — that tag is the only record of which provider "
        "actually served"
    )
