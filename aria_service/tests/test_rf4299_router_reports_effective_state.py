"""R-F4299 / C-253 — the router surface let a raw knob imply behaviour it does not have.

CONTEXT, because the obvious fix here is the wrong one. The 2026-08-01 readiness
doc lists as its cheap do-first item: "ARIA_LLM_PROMOTION_STAGE=shadow and
ARIA_LLM_SHADOW=0 describe the same state and DISAGREE. Nothing reconciles them."

**That is stale.** R-F3636 already reconciled them, and correctly: `_shadow()` is
DERIVED (`promotion_stage() == "shadow"`), and the legacy `ARIA_LLM_SHADOW` is a
conservative INPUT to the stage — truthy forces shadow, and can never be a bypass,
because the only safe direction to get that wrong is the one that holds the model
back from users. Measured live 2026-08-24 through the running server:

    promotion_stage = shadow    shadow = True    shadow_env_override = False
    sovereign_pod_serving = False   sovereign_warm = False   samples = 0

One source of truth, honestly reported. Re-deriving it would be inventing work.

WHAT ACTUALLY REMAINS is the thing the doc was reaching for and mis-diagnosed.
The same payload publishes `canary_pct: 50` with nothing saying it is INERT at
stage=shadow. A reader sees 50 and concludes half of chat is served by the
sovereign model. She serves none of it. The doc's own author recorded making
exactly that misreading — "I misread it that way myself before tracing line 312,
which is the point: a config that needs a code trace to interpret is not a
config."

So the defect is not two flags disagreeing. It is a REPORT that states knobs
instead of consequences, and leaves the reader to run the precedence rules in
their head. The fix is to publish what the knob actually does right now.

Also surfaced here: `summary()` never reported `ARIA_LLM_MODEL` at all, so nothing
on any surface said WHICH model version the router would call. Live it reads
`aria-llm-v0.1` while the only models with recorded 500-Q evals are v0.2 and v0.4
— a drift that was invisible precisely because the field did not exist.
"""
from __future__ import annotations

import importlib
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = ("ARIA_LLM_URL", "ARIA_LLM_MODEL", "ARIA_LLM_SHADOW",
        "ARIA_LLM_PROMOTION_STAGE", "ARIA_LLM_CANARY_PCT",
        "ARIA_LLM_PRIMARY_ALL", "ARIA_LLM_ROUTER_DISABLED")


@pytest.fixture
def router(monkeypatch):
    for v in _ENV:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("ARIA_LLM_URL", "http://sovereign.invalid/v1")
    from aria_service.llm import model_router as mr
    return importlib.reload(mr)


def _live(monkeypatch, router):
    """The EXACT aria-intel config, read from printenv 2026-08-24."""
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.1")
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "shadow")
    monkeypatch.setenv("ARIA_LLM_SHADOW", "0")
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "50")
    return router.summary()


# ── the capability test: the number that misleads ──────────────────────────

def test_canary_pct_is_reported_INERT_while_shadowing(monkeypatch, router) -> None:
    """THE MISREADING. `canary_pct: 50` at stage=shadow serves nobody."""
    s = _live(monkeypatch, router)
    assert s["promotion_stage"] == "shadow"
    assert s["canary_pct"] == 50, "the raw knob must still be visible"
    assert s["canary_pct_effective"] == 0, (
        "canary_pct is INERT at stage=shadow and the surface must say so")


def test_one_field_answers_the_docs_question(monkeypatch, router) -> None:
    """'Is she shadowing, or serving 50% of chat?' must be readable, not derived
    by the reader from three fields and a precedence rule."""
    s = _live(monkeypatch, router)
    assert s["serving_users"] is False


def test_canary_pct_becomes_effective_at_canary(monkeypatch, router) -> None:
    """The guard must be able to report the OTHER answer, or it says nothing."""
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "50")
    s = router.summary()
    assert s["canary_pct_effective"] == 50
    assert s["serving_users"] is True


def test_serve_stage_is_serving_users(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    s = router.summary()
    assert s["serving_users"] is True
    assert s["canary_pct_effective"] == 100, "serve routes all grounded synthesis"


def test_off_stage_serves_nobody(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "off")
    s = router.summary()
    assert s["serving_users"] is False
    assert s["canary_pct_effective"] == 0


def test_primary_all_is_serving_even_at_shadow_stage(monkeypatch, router) -> None:
    """R-F93's escape hatch routes every turn. A `serving_users: False` beside it
    would be the same class of lie this fix removes."""
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "shadow")
    monkeypatch.setenv("ARIA_LLM_PRIMARY_ALL", "1")
    s = router.summary()
    assert s["serving_users"] is True


# ── which model would actually be called ───────────────────────────────────

def test_the_surface_names_the_model(monkeypatch, router) -> None:
    """Nothing reported ARIA_LLM_MODEL, so no surface could show that live points
    at v0.1 while only v0.2 and v0.4 have recorded 500-Q evals."""
    s = _live(monkeypatch, router)
    assert s["model"] == "aria-llm-v0.1"


# ── the legacy var stays visible as legacy ─────────────────────────────────

def test_the_legacy_var_is_flagged_when_set(monkeypatch, router) -> None:
    s = _live(monkeypatch, router)
    assert s["legacy_shadow_var_present"] is True, (
        "ARIA_LLM_SHADOW is set on the live config; a reader must be told it is "
        "the legacy input, not an independent switch")


def test_it_is_not_flagged_when_absent(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "shadow")
    assert router.summary()["legacy_shadow_var_present"] is False


# ── R-F3636 must NOT be undone by this change ──────────────────────────────

def test_the_conservative_legacy_flag_still_wins(monkeypatch, router) -> None:
    """R-F3636's direction is load-bearing: an operator setting the OLD flag to
    hold the model back must not be overridden into serving by a newer stage."""
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    monkeypatch.setenv("ARIA_LLM_SHADOW", "1")
    s = router.summary()
    assert s["promotion_stage"] == "shadow"
    assert s["serving_users"] is False
    assert s["canary_pct_effective"] == 0


def test_shadow_is_still_derived_not_read(monkeypatch, router) -> None:
    s = _live(monkeypatch, router)
    assert s["shadow"] is True and s["shadow_env_override"] is False, (
        "the derived/raw split R-F3636 introduced must survive")


# ── the three new measures, called DIRECTLY ────────────────────────────────
#
# The R-F1958 pre-commit gate requires a capability test that INVOKES each new
# function, not one that reaches it through a caller. It is right to: a helper
# exercised only via `summary()` is proven as far as summary() happens to use it,
# and the next caller gets no such guarantee.

def test_sovereign_model_names_the_configured_model(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4")
    assert router.sovereign_model() == "aria-llm-v0.4"


def test_sovereign_model_is_empty_when_unset(monkeypatch, router) -> None:
    """Empty, never a guessed default — a surface must not invent a model id."""
    monkeypatch.delenv("ARIA_LLM_MODEL", raising=False)
    assert router.sovereign_model() == ""


def test_sovereign_model_strips_whitespace(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_MODEL", "  aria-llm-v0.4  ")
    assert router.sovereign_model() == "aria-llm-v0.4"


def test_serving_users_directly(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "shadow")
    assert router.serving_users() is False
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    assert router.serving_users() is True


def test_serving_users_is_false_when_the_router_is_disabled(monkeypatch, router) -> None:
    """The kill switch outranks every stage; reporting otherwise would be the
    same false claim of activity this whole fix removes."""
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "serve")
    monkeypatch.setenv("ARIA_LLM_ROUTER_DISABLED", "1")
    assert router.serving_users() is False


def test_canary_pct_effective_directly(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "25")
    assert router.canary_pct_effective() == 25


def test_canary_pct_effective_is_zero_when_disabled(monkeypatch, router) -> None:
    monkeypatch.setenv("ARIA_LLM_PROMOTION_STAGE", "canary")
    monkeypatch.setenv("ARIA_LLM_CANARY_PCT", "25")
    monkeypatch.setenv("ARIA_LLM_ROUTER_DISABLED", "1")
    assert router.canary_pct_effective() == 0


def test_the_model_field_is_what_made_the_v04_dpo_switch_verifiable(monkeypatch, router) -> None:
    """R-F4300 — the operator change this field enabled.

    Live was `aria-llm-v0.1`, a version NOTHING has ever evaluated, and no surface
    could show that because `summary()` did not report the model at all. Once it
    did, the drift was visible and the secret was corrected to `aria-llm-v0.4-dpo`
    — the best-measured id at 0.502 on the 500-Q against the DeepSeek baseline's
    0.336.

    MIND THE NAMING TRAP pinned here: the eval runs labelled v0.5 / v0.6 / v0.7 in
    their FILENAMES all serve under the id `aria-llm-v0.4-dpo`. A filename version
    is not a model id. Read `model` from the report, never the filename — picking
    an id from a filename would have set a model the server does not serve.
    """
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")
    assert router.summary()["model"] == "aria-llm-v0.4-dpo"
    assert router.sovereign_model() == "aria-llm-v0.4-dpo"
