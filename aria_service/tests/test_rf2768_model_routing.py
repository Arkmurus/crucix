"""R-F2768 — Phase 2: model-level Claude routing (foundation).

Once DeepSeek is removed, the cheap provider tiers collapse UP to a single
anthropic primary, so without model-level routing every call — including bulk
article extraction — would hit full-price Sonnet. This layer maps each intent to
a Claude model (Haiku cheap / Sonnet standard / Opus premium) and gives providers
a per-call `model=` override.

This foundation test covers the DECISION layer (tier_router.claude_model_for_intent)
and the PROVIDER capability (AnthropicProvider honours a Claude override in its
payload; OpenAICompatProvider ignores a Claude id so it is never sent to a
non-Claude API). The chain-threading + call-site wiring is the next increment.
"""
from __future__ import annotations

import inspect

from aria_service.llm import tier_router as tr
from aria_service.llm.anthropic import AnthropicProvider
from aria_service.llm.openai_compat import OpenAICompatProvider
from aria_service.llm.provider import LLMProvider


# ── routing policy (the Haiku / Sonnet / Opus table) ────────────────────────
def test_routing_policy_maps_intents_to_models():
    # cheap / high-volume → Haiku
    assert tr.claude_model_for_intent("research_extraction") == "claude-haiku-4-5"
    assert tr.claude_model_for_intent("classification") == "claude-haiku-4-5"
    assert tr.claude_model_for_intent("dd_layer_1") == "claude-haiku-4-5"
    assert tr.claude_model_for_intent("deep_research") == "claude-haiku-4-5"
    # customer DD synthesis / chat / structured → Sonnet
    assert tr.claude_model_for_intent("chat") == "claude-sonnet-5"
    assert tr.claude_model_for_intent("structured_output") == "claude-sonnet-5"
    # audit-grade / constitutional → Opus
    assert tr.claude_model_for_intent("audit_grade_dd") == "claude-opus-4-8"
    assert tr.claude_model_for_intent("constitutional_decision") == "claude-opus-4-8"


def test_unknown_intent_is_safe_standard_not_cheap():
    # a mislabelled customer call must never be silently down-modelled to Haiku
    assert tr.claude_model_for_intent("some_intent_that_does_not_exist") == "claude-sonnet-5"


def test_env_overrides_retune_without_deploy(monkeypatch):
    monkeypatch.setenv("ARIA_MODEL_CHEAP", "claude-haiku-4-5")
    monkeypatch.setenv("ARIA_MODEL_STANDARD", "claude-opus-4-8")  # e.g. temporarily lift quality
    assert tr.claude_model_for_intent("chat") == "claude-opus-4-8"
    assert tr.claude_model_for_intent("research_extraction") == "claude-haiku-4-5"


# ── provider per-call override capability ───────────────────────────────────
def test_anthropic_payload_honours_claude_override():
    p = AnthropicProvider(api_key="x", model="claude-sonnet-5")
    # a routed Claude model is used for this call
    assert p._payload("s", "u", 100, model="claude-haiku-4-5")["model"] == "claude-haiku-4-5"
    # a non-Claude id is ignored (never mis-targets Anthropic) → configured model
    assert p._payload("s", "u", 100, model="gpt-4o")["model"] == "claude-sonnet-5"
    # no override → configured model
    assert p._payload("s", "u", 100)["model"] == "claude-sonnet-5"


def test_openai_compat_accepts_model_kwarg_without_error():
    # The fallback chain will call provider.complete(..., model=<claude id>); the
    # OpenAI-compatible provider (DeepSeek / OpenAI) MUST accept the kwarg (else
    # TypeError breaks the chain) and must NOT send a Claude id downstream.
    sig = inspect.signature(OpenAICompatProvider.complete)
    assert "model" in sig.parameters, "OpenAICompat.complete must accept the routing override"


def test_base_provider_contract_has_model_param():
    # complete + the default stream both carry the override param.
    assert "model" in inspect.signature(LLMProvider.complete).parameters
    assert "model" in inspect.signature(LLMProvider.stream).parameters
