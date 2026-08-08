"""R-F3698 — one env var, two OPPOSITE meanings, and the safe-looking one was armed.

`ARIA_LLM_SHADOW` governs two unrelated decisions in two different consumers:

  model_router.promotion_stage()  — HOLD THE SOVEREIGN BACK from serving grounded
      synthesis. Its own docstring (R-F3636): "The conservative flag wins; that is
      the only direction that is safe to get wrong." Setting it is the CAUTIOUS act.

  create_fallback_chain()         — INSERT the sovereign into the GENERAL fallback
      chain, below the primary (R-F1949). Setting it is the EXPANSIONARY act.

So the one action an operator would take to be careful — `ARIA_LLM_SHADOW=1` —
simultaneously wires a RunPod endpoint into production failover. Per §24 that pod
is force-stopped outside its scheduled windows, i.e. it is offline most of the
week, and docs/aria_llm_fallback_readiness_2026_08_01.md item 1 is explicit:

    "A fallback must be MORE available than what it backs up. Wiring a
     sometimes-on endpoint into the general chain would make outages *worse*:
     every DeepSeek timeout would then wait out a second dead hop before failing.
     It would read as added redundancy and subtract availability."

R-F3636 already fixed HALF of this — it made `ARIA_LLM_SHADOW` an input to
`promotion_stage()` rather than a second switch. It did not reach
`create_fallback_chain`, which still reads the raw env var. Measured live
2026-08-04, the two consumers disagree about the same word at the same instant:

    ARIA_LLM_SHADOW='0'  ARIA_LLM_PROMOTION_STAGE='shadow'
    model_router._shadow()          -> True
    fallback._aria_llm_shadow       -> False
    live chain_order  ['deepseek','anthropic','deepseek_backup']   (no sovereign)

THE FIX IS BEHAVIOUR-PRESERVING BY CONSTRUCTION. Chain placement becomes its own
explicit flag, `ARIA_LLM_IN_FALLBACK_CHAIN`, defaulting OFF. Today the sovereign
is not in the chain (SHADOW='0'); after this it is still not in the chain (new
flag unset). The live chain does not move. What changes is that it can no longer
be moved BY ACCIDENT.

R-F1949's capability is not removed — it is re-addressed. Ask for it explicitly.
"""
from __future__ import annotations

import pytest

from aria_service.llm import fallback
from aria_service.llm import model_router as mr

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source


class _Stub:
    def __init__(self, name):
        self.name = name
        self.is_configured = True


_CLEAR = (
    "ARIA_LLM_URL", "ARIA_LLM_SHADOW", "ARIA_LLM_MODEL", "ARIA_LLM_KEY",
    "ARIA_LLM_PRIMARY_ALL", "ARIA_LLM_IN_FALLBACK_CHAIN",
    "ARIA_LLM_PROMOTION_STAGE",
    "GROQ_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
)


def _chain(monkeypatch, **env) -> list[str]:
    for v in _CLEAR:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_LLM_URL", "http://aria-llm/v1")
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-test")
    monkeypatch.setenv("GROQ_API_KEY", "gkey")   # a 2nd provider so a chain is built
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(
        fallback, "create_llm_provider",
        lambda ptype, key, model="", base_url="": _Stub(model or ptype))
    chain = fallback.create_fallback_chain("deepseek", "dskey", "ds-model", "")
    provs = getattr(chain, "providers", None)
    if provs is None:
        return [getattr(chain, "name", "?")]
    return [getattr(p, "name", "?") for p in provs]


# ── the live baseline must not move ──────────────────────────────────────────


def test_the_live_config_produces_the_live_chain_unchanged(monkeypatch):
    """Pinned from production 2026-08-04: SHADOW='0', STAGE='shadow' → the
    sovereign is NOT in the general chain. This must hold identically before and
    after the fix, or the change is not behaviour-preserving."""
    names = _chain(monkeypatch, ARIA_LLM_SHADOW="0", ARIA_LLM_PROMOTION_STAGE="shadow")
    assert names[0] == "ds-model", names
    assert "aria_llm" not in names, (
        f"live chain must stay DeepSeek-headed with no sovereign hop: {names}"
    )


def test_two_track_default_is_unchanged(monkeypatch):
    names = _chain(monkeypatch)
    assert names[0] == "ds-model"
    assert "aria_llm" not in names


def test_primary_all_escape_hatch_is_unchanged(monkeypatch):
    names = _chain(monkeypatch, ARIA_LLM_PRIMARY_ALL="1")
    assert names[0] == "aria_llm", names


# ── the defect ───────────────────────────────────────────────────────────────


def test_capability_the_conservative_flag_must_not_wire_a_sometimes_on_endpoint(monkeypatch):
    """THE DEFECT. FAILS BEFORE.

    `ARIA_LLM_SHADOW=1` is what `promotion_stage()` documents as the safe,
    conservative setting — it holds the sovereign back from serving. It must not
    ALSO insert a pod that §24 force-stops outside its windows into the general
    failover path.
    """
    names = _chain(monkeypatch, ARIA_LLM_SHADOW="1")
    assert names[0] == "ds-model", names
    assert "aria_llm" not in names, (
        "setting the CONSERVATIVE flag silently added a mostly-offline hop to "
        f"production failover: {names}"
    )


def test_the_conservative_flag_still_does_its_real_job(monkeypatch):
    """...and holding the sovereign back must keep working. The point is to
    separate the two meanings, not to disarm the safe one."""
    for v in _CLEAR:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_LLM_SHADOW", "1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    assert mr.promotion_stage() == "shadow", (
        "R-F3636: the conservative flag still wins over a more permissive stage"
    )


def test_promotion_stage_shadow_alone_never_touches_the_chain(monkeypatch):
    """The two consumers must stop disagreeing about the same word: a promotion
    STAGE is about grounded synthesis, never about general failover."""
    names = _chain(monkeypatch, ARIA_LLM_PROMOTION_STAGE="shadow")
    assert "aria_llm" not in names, names
    names = _chain(monkeypatch, ARIA_LLM_PROMOTION_STAGE="canary")
    assert "aria_llm" not in names, names
    names = _chain(monkeypatch, ARIA_LLM_PROMOTION_STAGE="serve")
    assert "aria_llm" not in names, (
        f"even 'serve' is a GROUNDED-SYNTHESIS decision, not a chain one: {names}"
    )


# ── the capability is re-addressed, not removed ──────────────────────────────


def test_chain_placement_is_available_when_asked_for_explicitly(monkeypatch):
    """R-F1949's behaviour is preserved behind a flag that means ONE thing."""
    names = _chain(monkeypatch, ARIA_LLM_IN_FALLBACK_CHAIN="1")
    assert names[0] == "ds-model", names          # primary unchanged
    assert "aria_llm" in names, names             # sovereign present
    assert names.index("aria_llm") > 0, names     # ...below the primary


def test_explicit_placement_is_independent_of_the_promotion_stage(monkeypatch):
    """Belt and braces: the new flag decides placement on its own, so the two
    questions can never be re-coupled by a stage change."""
    names = _chain(monkeypatch, ARIA_LLM_IN_FALLBACK_CHAIN="1",
                   ARIA_LLM_PROMOTION_STAGE="off")
    assert "aria_llm" in names, names


def test_the_chain_builder_no_longer_reads_the_legacy_flag():
    """Structural pin — the whole point is that this coupling cannot come back.

    Pins the READ, not the word: the variable is still named in the comments
    that explain why it must not be read here, and a test that banned the word
    would force those comments out. That would delete the reasoning and invite
    the coupling straight back — the failure this file exists to prevent.
    """
    import inspect
    import re
    src = function_source(fallback, "create_fallback_chain")
    reads = re.findall(r"getenv\(\s*[\"']ARIA_LLM_SHADOW[\"']", src)
    assert reads == [], (
        "create_fallback_chain must not READ ARIA_LLM_SHADOW: that variable's "
        "other meaning is 'be conservative', and chain insertion is the opposite"
    )
    assert 'getenv("ARIA_LLM_IN_FALLBACK_CHAIN"' in src, (
        "placement must come from its own single-meaning flag"
    )
