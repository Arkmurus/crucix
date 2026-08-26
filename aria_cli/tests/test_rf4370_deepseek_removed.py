"""R-F4370 (C-315) — DeepSeek is removed from the coder CLI; ARIA reasons for
herself.

Operator directive 2026-08-26, verbatim: *"remove deepseek from cli, aria must
use her own reasoning now, ensure it is root pricision and surgically"*.

**Changing the default alone would not have been the fix.** DeepSeek could reach
the CLI by FOUR routes, and three of them survive a default change:

    1. the default itself      -- "deepseek" if DEEPSEEK_API_KEY else "aria"
    2. --provider deepseek
    3. LLM_PROVIDER=deepseek   (a generic var the rest of the stack sets)
    4. ANY unrecognised provider -- `_PROVIDER_BASE_URLS.get(provider,
       "https://api.deepseek.com/v1")` silently sent a typo to the vendor

Route 4 is the dangerous one: the output looks identical whichever model
served it, which is the same blindness R-F4303 refuses ("being answered by a
model you did not choose, with nothing in the output saying so"). So the name is
removed from every table, and the vendor default under it is gone.

It FAILS CLOSED. Unconfigured now raises instead of selecting something that
happens to have a key, because a silent substitution is worse than a refusal —
and the refusal NAMES the directive, so the next reader does not "fix" the error
message by restoring the table entry. That revert-by-tidying is the exact
failure CLAUDE.md §17 records happening three times to the Brave/WA capability.
"""
from __future__ import annotations

import pytest

from aria_cli.llm import (
    _PROVIDER_BASE_URLS,
    _PROVIDER_DEFAULT_MODELS,
    _PROVIDER_KEY_ENV,
    _PROVIDER_WINDOWS,
    LLMConfig,
    LLMError,
)

SOVEREIGN = "https://pod.example/v1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("ARIA_CODER_LLM_PROVIDER", "LLM_PROVIDER", "ARIA_CODER_LLM_MODEL",
                "LLM_MODEL", "ARIA_CODER_LLM_API_KEY", "LLM_API_KEY",
                "ARIA_CODER_LLM_BASE_URL", "OPENAI_BASE_URL", "ARIA_LLM_URL",
                "ARIA_LLM_MODEL", "ARIA_LLM_KEY", "ARIA_LLM_MAX_MODEL_LEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-never-be-used")
    yield


def _sovereign(monkeypatch):
    monkeypatch.setenv("ARIA_LLM_URL", SOVEREIGN)
    monkeypatch.setenv("ARIA_LLM_MODEL", "aria-llm-v0.4-dpo")


# ── route 1: the default ────────────────────────────────────────────────────

def test_the_default_is_arias_own_model_even_with_a_deepseek_key_present(monkeypatch):
    """THE DIRECTIVE. A DEEPSEEK_API_KEY in .env used to BE the selection —
    R-F1937 defaulted to the vendor whenever the key existed."""
    _sovereign(monkeypatch)
    cfg = LLMConfig.from_env()

    assert cfg.provider == "aria-llm"
    assert cfg.base_url == SOVEREIGN
    assert cfg.model == "aria-llm-v0.4-dpo"


def test_an_unconfigured_default_refuses_rather_than_picking_a_vendor(monkeypatch):
    """FAIL CLOSED. With no sovereign endpoint the old code would have taken
    DeepSeek on the strength of the key alone."""
    with pytest.raises(LLMError) as exc:
        LLMConfig.from_env()
    assert "ARIA_LLM_URL" in str(exc.value)
    assert not exc.value.transient, "a misconfiguration is not worth retrying"


# ── routes 2 and 3: explicit selection ──────────────────────────────────────

def test_the_provider_flag_cannot_select_deepseek(monkeypatch):
    _sovereign(monkeypatch)
    with pytest.raises(LLMError) as exc:
        LLMConfig.from_env(provider_override="deepseek")
    assert "removed" in str(exc.value).lower()


def test_the_generic_env_var_cannot_select_deepseek_either(monkeypatch):
    """LLM_PROVIDER is set across the wider stack, so gating only the CLI-
    specific var would leave the vendor one export away."""
    _sovereign(monkeypatch)
    monkeypatch.setenv("LLM_PROVIDER", "DeepSeek")   # also case-insensitive
    with pytest.raises(LLMError):
        LLMConfig.from_env()


def test_the_refusal_names_the_directive_and_the_remedy():
    """A refusal the reader cannot act on gets "fixed" by undoing it. This
    message must say WHY, and what to do instead."""
    from aria_cli.llm import _REMOVED_PROVIDERS
    msg = _REMOVED_PROVIDERS["deepseek"]

    assert "R-F4370" in msg
    assert "ARIA_LLM_URL" in msg
    assert "Do not re-add" in msg


# ── route 4: the silent vendor fallback ─────────────────────────────────────

def test_an_unknown_provider_is_refused_not_routed_to_a_vendor(monkeypatch):
    """THE SILENT ROUTE. `.get(provider, "https://api.deepseek.com/v1")` sent
    every unrecognised name — a typo included — to DeepSeek, and nothing in the
    output said so."""
    _sovereign(monkeypatch)
    with pytest.raises(LLMError) as exc:
        LLMConfig.from_env(provider_override="deepsek")   # typo
    assert "another vendor" in str(exc.value)


def test_deepseek_is_absent_from_every_provider_table():
    """Removed from the tables, not merely skipped at one branch: a name that
    is still resolvable is still reachable."""
    for table, label in ((_PROVIDER_BASE_URLS, "base urls"),
                         (_PROVIDER_DEFAULT_MODELS, "default models"),
                         (_PROVIDER_KEY_ENV, "key env vars"),
                         (_PROVIDER_WINDOWS, "context windows")):
        assert "deepseek" not in table, f"deepseek is still in the {label} table"


def test_no_deepseek_endpoint_survives_anywhere_in_the_client():
    """The URL itself must be gone. A commented-out or defaulted
    api.deepseek.com is one edit away from serving again."""
    import pathlib

    import aria_cli.llm as llm_mod
    src = pathlib.Path(llm_mod.__file__).read_text(encoding="utf-8")
    code = [ln for ln in src.splitlines()
            if not ln.lstrip().startswith("#")
            and not ln.lstrip().startswith("#:")]
    assert not any("api.deepseek.com" in ln for ln in code), \
        "a live api.deepseek.com endpoint is still present in llm.py"


# ── what must NOT have been broken ──────────────────────────────────────────

def test_the_other_providers_still_resolve(monkeypatch):
    """Surgical: the directive names DeepSeek, not pluggability. Removing the
    others would be scope the operator did not ask for."""
    _sovereign(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    cfg = LLMConfig.from_env(provider_override="openai")

    assert cfg.provider == "openai"
    assert cfg.base_url == "https://api.openai.com/v1"


def test_ollama_still_resolves_without_a_key(monkeypatch):
    _sovereign(monkeypatch)
    cfg = LLMConfig.from_env(provider_override="ollama")

    assert cfg.base_url.endswith("/v1") and cfg.is_configured


def test_an_explicit_base_url_still_wins(monkeypatch):
    """The escape hatch for a self-hosted OpenAI-compatible endpoint stays —
    it names a specific endpoint, so nothing is being substituted silently."""
    _sovereign(monkeypatch)
    monkeypatch.setenv("ARIA_CODER_LLM_BASE_URL", "http://127.0.0.1:9999/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-x")
    cfg = LLMConfig.from_env(provider_override="openai")

    assert cfg.base_url == "http://127.0.0.1:9999/v1"


def test_the_aria_brain_provider_still_resolves(monkeypatch):
    """R-F2166's tool-less `aria` provider is a different endpoint from
    `aria-llm` and is still explicitly selectable."""
    _sovereign(monkeypatch)
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "tok")
    monkeypatch.setenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev")
    cfg = LLMConfig.from_env(provider_override="aria")

    assert cfg.provider == "aria"
    assert cfg.base_url == "https://aria-intel.fly.dev/api/aria"
